"""Deterministic offline planning and bounded execution through the real permission engine."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import (
    Answer,
    Limits,
    PlannerInput,
    Proposal,
    ProviderError,
    Step,
)
from jarvis.core.planner.offline import OfflineProvider, call
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.base import ExecutionContext
from jarvis.tools.local import LocalOutbox, local_registry
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.scratchpad import MAX_SCRATCHPAD_BYTES
from jarvis.tools.windows import NativeRequest, WindowsResult, register_windows


def test_default_limits_and_hard_caps() -> None:
    assert Limits().max_steps == 30 and Limits().total_seconds == 300
    with pytest.raises(ValueError):
        Limits(max_steps=31)
    with pytest.raises(ValueError):
        Limits(total_seconds=301)


@pytest.mark.asyncio
async def test_screenshot_flows_from_registered_tool_to_next_input(tmp_path: Path) -> None:
    class Capture:
        async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
            return WindowsResult(image_base64="/9j/2Q==", image_format="JPEG", width=1, height=1)

    registry, _ = local_registry()
    register_windows(registry, Capture())
    with closing(AuditLog(tmp_path / "capture.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        provider = Scripted([call("windows.screenshot", {}), call("local.check", {})])

        async def deny(action: Action) -> None:
            pytest.fail("SAFE screenshot requested approval")

        async def clarify(question: str) -> None:
            pytest.fail("Unexpected clarification")

        runner = Runner(registry, engine, provider, deny, clarify)
        result = await runner.run("capture", Mode.EXECUTE)
        assert result.status == "finished"
        assert provider.inputs[0].screenshot_b64 is None
        assert provider.inputs[1].screenshot_b64 == "/9j/2Q=="
        assert provider.inputs[2].screenshot_b64 is None
        assert "image_base64" not in (result.steps[0].outcome.result_json or "")
        assert result.steps[0].outcome.screenshot_b64 is None
        assert runner._screenshot_b64 is None


@pytest.mark.asyncio
@pytest.mark.parametrize("calls,expected", [(30, "finished"), (31, "limit")])
async def test_default_runner_supports_thirty_actions(
    harness: "Harness", calls: int, expected: str
) -> None:
    result = await harness.runner(Scripted([call("local.check", {"delay_ms": 0})] * calls)).run(
        "long task", Mode.EXECUTE
    )
    assert result.status == expected and len(result.steps) == 30


@pytest.mark.asyncio
async def test_scratchpad_survives_steps_is_snapshot_and_cleared(harness: "Harness") -> None:
    provider = Scripted(
        [
            call("memory.scratchpad_write", {"key": "draft", "value": "PRIVATE_NOTE"}),
            *[call("local.check", {"delay_ms": 0})] * 10,
            call("memory.scratchpad_write", {"key": "draft", "value": "new value"}),
            call("memory.scratchpad_write", {"key": "amount", "value": "42"}),
        ]
    )
    runner = harness.runner(provider)
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "finished"
    assert provider.inputs[0].scratchpad == {}
    assert provider.inputs[11].scratchpad == {"draft": "PRIVATE_NOTE"}
    assert provider.inputs[-1].scratchpad == {"draft": "new value", "amount": "42"}
    with pytest.raises(TypeError):
        provider.inputs[-1].scratchpad["amount"] = "99"  # type: ignore[index]
    assert runner.scratchpad == {}
    assert "PRIVATE_NOTE" not in result.summary
    assert "PRIVATE_NOTE" not in "".join(harness.audit.recent())
    assert result.steps[0].outcome.status is Status.SUCCESS


@pytest.mark.asyncio
async def test_scratchpad_simulation_does_not_write(harness: "Harness") -> None:
    provider = Scripted([call("memory.scratchpad_write", {"key": "draft", "value": "note"})])
    runner = harness.runner(provider)
    result = await runner.run("test")
    assert result.status == "simulated"
    assert all(not item.scratchpad for item in provider.inputs)
    assert runner.scratchpad == {}


@pytest.mark.asyncio
async def test_scratchpad_budget_and_failure_cleanup(harness: "Harness") -> None:
    provider = Scripted(
        [
            call("memory.scratchpad_write", {"key": f"note{index}", "value": "x" * 8192})
            for index in range(5)
        ]
    )
    runner = harness.runner(provider)
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "error" and result.error == "context_limit"
    assert len(result.steps) == 3
    assert len(json.dumps(dict(provider.inputs[-1].scratchpad)).encode()) < MAX_SCRATCHPAD_BYTES
    assert runner.scratchpad == {} and runner.active is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [42, {"nested": "note"}, "x" * 8193])
async def test_scratchpad_rejects_invalid_values(harness: "Harness", value: object) -> None:
    result = await harness.runner(
        Scripted(
            [
                call("memory.scratchpad_write", {"key": "draft", "value": value}),
            ]
        )
    ).run("test", Mode.EXECUTE)
    assert result.status == "error" and result.steps[0].outcome.status is Status.INVALID


@pytest.mark.asyncio
async def test_scratchpad_does_not_authorize_confirm_actions(harness: "Harness") -> None:
    runner = harness.runner(
        Scripted(
            [
                call("memory.scratchpad_write", {"key": "approval", "value": "approved"}),
                call("local.append_message", MESSAGE),
            ]
        )
    )

    async def deny(action: Action) -> None:
        return None

    runner.approve = deny
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "error" and harness.outbox.count == 0
    assert runner.scratchpad == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("last_status", [Status.SUCCESS, Status.SIMULATED, Status.ERROR])
async def test_runner_only_exposes_last_successful_screenshot(
    harness: "Harness", last_status: Status
) -> None:
    runner = harness.runner(Scripted([]))
    runner._screenshot_b64 = "CURRENT_IMAGE"
    runner.steps.append(
        Step(
            "windows.screenshot",
            Outcome(
                uuid4(),
                last_status,
                result_json='{"image_format":"JPEG"}',
            ),
        )
    )
    assert runner._latest_screenshot() == (
        "CURRENT_IMAGE" if last_status is Status.SUCCESS else None
    )
    runner.steps.append(Step("local.check", Outcome(uuid4(), Status.SUCCESS)))
    assert runner._latest_screenshot() is None


class Scripted:
    def __init__(self, proposals: list[Proposal], delay: float = 0) -> None:
        self.proposals = proposals
        self.inputs: list[PlannerInput] = []
        self.delay = delay

    async def propose(self, data: PlannerInput) -> Proposal:
        self.inputs.append(data)
        await asyncio.sleep(self.delay)
        return self.proposals.pop(0) if self.proposals else Proposal(kind="finish")


@dataclass
class Harness:
    registry: ToolRegistry
    outbox: LocalOutbox
    engine: PermissionEngine
    authority: ApprovalAuthority
    audit: AuditLog

    async def approve(self, action: Action) -> ApprovalToken:
        return self.authority.approve(action)

    async def clarify(self, question: str) -> str:
        return "проверь систему"

    def runner(self, provider: Scripted | OfflineProvider, limits: Limits | None = None) -> Runner:
        return Runner(
            self.registry, self.engine, provider, self.approve, self.clarify, limits=limits
        )


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    registry, outbox = local_registry()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    engine = PermissionEngine(registry, store, audit)
    yield Harness(registry, outbox, engine, store.take_authority(audit.approved), audit)
    engine.cancel_all()
    audit.close()


MESSAGE = {"recipient": "test", "body": "PRIVATE_COMMAND_SENTINEL"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [Mode.SIMULATION, Mode.EXECUTE])
@pytest.mark.parametrize(
    "command,tool,arguments",
    [
        ("найди файл отчёт.txt", "files.find", {"query": "отчёт.txt"}),
        ("НАЙДИ ФАЙЛ   план   встречи.md", "files.find", {"query": "план встречи.md"}),
        ("найди OpenAI", "browser.search", {"query": "OpenAI"}),
        ("найди форматы файлов", "browser.search", {"query": "форматы файлов"}),
    ],
)
async def test_offline_file_search_stays_local_and_web_search_keeps_its_route(
    mode: Mode, command: str, tool: str, arguments: dict[str, str]
) -> None:
    provider = OfflineProvider()
    proposal = await provider.propose(PlannerInput(command, (), (), mode, "[]"))
    assert proposal.kind == "call" and proposal.tool == tool
    assert json.loads(proposal.arguments) == arguments
    step = Step(tool, Outcome(uuid4(), Status.SUCCESS))
    assert (await provider.propose(PlannerInput(command, (), (step,), mode, "[]"))).kind == "finish"


@pytest.mark.asyncio
async def test_multistep_actual_facts_and_redacted_audit(harness: Harness) -> None:
    runner = harness.runner(
        Scripted([call("local.check", {}), call("local.append_message", MESSAGE)])
    )
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "finished"
    assert [step.outcome.status for step in result.steps] == [Status.SUCCESS, Status.SUCCESS]
    assert harness.outbox.count == 1
    assert "PRIVATE_COMMAND_SENTINEL" not in result.summary
    assert "PRIVATE_COMMAND_SENTINEL" not in "".join(harness.audit.recent())
    assert not runner.active


@pytest.mark.asyncio
async def test_simulation_has_no_effects_or_observations(harness: Harness) -> None:
    provider = Scripted([call("local.check", {}), call("local.append_message", MESSAGE)])
    result = await harness.runner(provider).run("test")
    assert result.status == "simulated" and harness.outbox.count == 0
    assert all(step.outcome.result_json is None for step in result.steps)
    assert all(not step.outcome.may_have_effects for step in result.steps)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("shell.exec", {"command": "whoami"}),
        ("local.critical_test", {}),
        ("local.blocked_test", {}),
    ],
)
async def test_bad_or_disabled_calls_stop_without_next_step(
    harness: Harness, tool: str, args: object
) -> None:
    provider = Scripted([call(tool, args), call("local.append_message", MESSAGE)])
    result = await harness.runner(provider).run("test", Mode.EXECUTE)
    assert result.status == "error"
    assert len(provider.inputs) == 1 and harness.outbox.count == 0


@pytest.mark.asyncio
async def test_finish_without_execution_never_claims_success(harness: Harness) -> None:
    result = await harness.runner(Scripted([])).run("Send everything", Mode.EXECUTE)
    assert result.status == "no_action" and "не подтверждён" in result.summary


@pytest.mark.asyncio
async def test_provider_cannot_supply_success_text(harness: Harness) -> None:
    class BadProvider(Scripted):
        async def propose(self, data: PlannerInput) -> Proposal:
            return Proposal.model_construct(kind="finish", question="Everything sent successfully")

    result = await harness.runner(BadProvider([])).run("test", Mode.EXECUTE)
    assert result.status == "error" and "Everything" not in result.summary


@pytest.mark.asyncio
async def test_denied_approval_never_executes(harness: Harness) -> None:
    async def deny(action: Action) -> None:
        return None

    runner = harness.runner(Scripted([call("local.append_message", MESSAGE)]))
    runner.approve = deny
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "error" and harness.outbox.count == 0
    assert result.steps[0].outcome.status is Status.DENIED


@pytest.mark.asyncio
async def test_token_from_another_action_cannot_authorize_step(harness: Harness) -> None:
    wrong = harness.engine.prepare(
        "local.append_message", MESSAGE | {"body": "different"}, Mode.EXECUTE
    )
    assert isinstance(wrong, Action)
    token = harness.authority.approve(wrong)

    async def wrong_approval(action: Action) -> ApprovalToken:
        return token

    runner = harness.runner(Scripted([call("local.append_message", MESSAGE)]))
    runner.approve = wrong_approval
    assert (await runner.run("test", Mode.EXECUTE)).status == "error"
    assert harness.outbox.count == 0


@pytest.mark.asyncio
async def test_step_limit_stops_before_extra_effect(harness: Harness) -> None:
    provider = Scripted([call("local.append_message", MESSAGE)] * 3)
    result = await harness.runner(provider, Limits(max_steps=2)).run("test", Mode.EXECUTE)
    assert result.status == "limit" and harness.outbox.count == 2


@pytest.mark.asyncio
async def test_clarification_resumes_same_task(harness: Harness) -> None:
    result = await harness.runner(OfflineProvider()).run("сделай что-нибудь", Mode.EXECUTE)
    assert result.status == "finished" and result.steps[0].tool == "local.check"


@pytest.mark.asyncio
async def test_repeated_questions_are_bounded(harness: Harness) -> None:
    provider = Scripted([Proposal(kind="clarify", question="Какой адрес?")] * 4)
    assert (await harness.runner(provider).run("test")).status == "limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("overall", [False, True])
async def test_provider_and_total_timeout(harness: Harness, overall: bool) -> None:
    limits = Limits(provider_seconds=1 if overall else 0.03, total_seconds=0.03 if overall else 1)
    result = await harness.runner(Scripted([], delay=2), limits).run("test")
    assert result.status == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["provider", "approval", "tool"])
async def test_stop_during_each_await_cleans_up(harness: Harness, phase: str) -> None:
    reached = asyncio.Event()
    provider = Scripted(
        [call("local.check", {"delay_ms": 1000})]
        if phase == "tool"
        else [call("local.append_message", MESSAGE)],
        delay=1 if phase == "provider" else 0,
    )
    runner = harness.runner(provider)

    async def wait_approval(action: Action) -> None:
        reached.set()
        await asyncio.sleep(2)

    runner.approve = wait_approval
    runner.notify = lambda kind, value: reached.set() if kind == "executing" else None
    task = asyncio.create_task(runner.run("test", Mode.EXECUTE))
    if phase == "provider":
        await asyncio.sleep(0.02)
    else:
        await asyncio.wait_for(reached.wait(), 1)
    runner.cancel()
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled" and harness.outbox.count == 0
    assert runner.active is None


@pytest.mark.asyncio
async def test_provider_exception_never_echoes_raw_details(harness: Harness) -> None:
    class Failure(Scripted):
        async def propose(self, data: PlannerInput) -> Proposal:
            raise RuntimeError("PRIVATE_COMMAND_SENTINEL")

    result = await harness.runner(Failure([])).run("test")
    assert result.status == "error" and "PRIVATE" not in result.summary


@pytest.mark.asyncio
async def test_failed_verifier_stops_plan(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the trusted verifier, not the provider or engine outcome.
    monkeypatch.setattr(harness.outbox, "matches", lambda *args: False)
    result = await harness.runner(Scripted([call("local.append_message", MESSAGE)])).run(
        "test", Mode.EXECUTE
    )
    assert result.status == "error"
    assert result.steps[0].outcome.error.value == "verification_failed"
    assert harness.outbox.count == 1


@pytest.mark.asyncio
async def test_runner_is_single_use(harness: Harness) -> None:
    runner = harness.runner(OfflineProvider())
    assert (await runner.run("проверь систему дважды")).status == "simulated"
    assert (await runner.run("проверь систему дважды")).status == "error"


@pytest.mark.asyncio
async def test_offline_clarification_reobserves_notepad_without_a_selected_editor() -> None:
    provider = OfflineProvider()
    command = "открой блокнот и напиши «text»"
    initial = PlannerInput(command, (), (), Mode.EXECUTE, "[]")
    assert (await provider.propose(initial)).tool == "windows.open_app"
    step = Step(
        "windows.open_app",
        Outcome(uuid4(), Status.SUCCESS, result_json=json.dumps({"target": {"empty": False}})),
    )
    assert (
        await provider.propose(PlannerInput(command, (), (step,), Mode.EXECUTE, "[]"))
    ).kind == "clarify"
    answer = PlannerInput(
        command, (Answer("какое приложение?", command),), (step,), Mode.EXECUTE, "[]"
    )
    assert (await provider.propose(answer)).tool == "windows.open_app"


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["deepseek", "openai"])
async def test_every_provider_offers_the_levels_that_do_ordinary_work(
    tmp_path: Path, vendor: str
) -> None:
    """A catalogue that hides ROUTINE hides typing, writing and the whole browser.

    Both catalogues were written when the levels were SAFE and CONFIRM, and neither was
    revisited when ROUTINE arrived between them, so the model was asked to work with the
    reading tools alone and answered by naming tools that do not exist.
    """
    from jarvis.core.planner.deepseek_provider import DeepSeekProvider
    from jarvis.core.planner.openai_provider import OpenAIProvider
    from jarvis.files.policy import FilePolicy
    from jarvis.platforms.files import LocalFiles
    from jarvis.tools.files import register_files

    registry, _ = local_registry()
    register_files(registry, FilePolicy((str(tmp_path),)), LocalFiles())
    catalog = registry.discover()
    assert any(tool["risk"] == "ROUTINE" for tool in catalog), "fixture must carry a ROUTINE tool"
    seen: list[dict[str, object]] = []

    async def transport(payload: dict[str, object]) -> bytes:
        seen.append(payload)
        raise ProviderError("provider_failed")  # The catalogue is the whole subject here.

    provider = (
        DeepSeekProvider("deepseek-flash", transport=transport)
        if vendor == "deepseek"
        else OpenAIProvider("test-model", transport=transport)
    )
    data = PlannerInput("напиши текст в файл", (), (), Mode.EXECUTE, json.dumps(catalog))
    with pytest.raises(ProviderError):
        await provider.propose(data)
    tools = seen[0]["tools"]
    assert isinstance(tools, list)
    offered = {
        str(tool["name"] if vendor == "openai" else tool["function"]["name"]) for tool in tools
    }
    assert "files__write_text" in offered, "the model cannot ask for what it is never shown"
    assert "windows__type_text" not in offered  # not registered in this fixture
    # What must never be proposed stays out, whatever else changes.
    assert "local__critical_test" not in offered and "local__blocked_test" not in offered
