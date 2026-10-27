"""Replanning uses the real engine; uncertain effects, policy and cancellation stay bounded."""

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from tests.unit.test_planner import MESSAGE, Harness, Scripted
from tests.unit.test_planner import harness as harness
from tests.windows_support import WindowsProbe, target

from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.openai_provider import OpenAIProvider, user_content
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.observability.diagnostics import Reason, Stage
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    ElementTarget,
    NativeRequest,
    Rectangle,
    WindowsResult,
    register_windows,
)
from jarvis.workspace.models import AppEntry, WorkspaceProfile


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"delay_ms": "1"}, {"extra": "PRIVATE_SENTINEL"}])
async def test_validation_failure_is_an_observation_and_can_be_corrected(
    harness: Harness, arguments: object
) -> None:
    provider = Scripted([call("local.check", arguments), call("local.check", {})])
    events = []
    runner = harness.runner(provider)
    runner.notify = lambda kind, value: events.append((kind, value))
    result = await runner.run("check", Mode.EXECUTE)
    assert result.status == "finished" and len(result.steps) == 2
    failure = provider.inputs[1].steps[0]
    assert failure.tool == "local.check" and failure.outcome.stage is Stage.PREPARE
    assert failure.outcome.reason is Reason.INVALID_ARGUMENTS
    diagnostic = json.loads(user_content(provider.inputs[1]))["untrusted_observations"][0]
    assert diagnostic["failure"]["stage"] == "prepare"
    assert "Аргументы" in diagnostic["failure"]["message"]
    assert len([event for event in events if event[0] == "tool"]) == 2
    assert "PRIVATE_SENTINEL" not in "".join(harness.audit.recent())
    record = next(
        json.loads(row) for row in harness.audit.recent() if json.loads(row)["status"] == "INVALID"
    )
    assert record["tool"] == "local.check" and record["stage"] == "prepare"


@pytest.mark.asyncio
async def test_three_consecutive_failures_stop_without_spending_the_whole_budget(
    harness: Harness,
) -> None:
    provider = Scripted([call("local.check", {"extra": True})] * 10)
    result = await harness.runner(provider).run("check", Mode.EXECUTE)
    assert result.status == "error" and len(result.steps) == len(provider.inputs) == 3


@pytest.mark.asyncio
async def test_precondition_failure_returns_to_provider_before_execution(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = harness.registry.get("local.check")
    assert tool is not None
    calls = 0

    async def check(payload: str, context: ExecutionContext) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setitem(harness.registry._tools, tool.name, replace(tool, check=check))
    provider = Scripted([call(tool.name, {}), call(tool.name, {})])
    result = await harness.runner(provider).run("check", Mode.EXECUTE)
    assert result.status == "finished"
    failed = provider.inputs[1].steps[0].outcome
    assert failed.error is ErrorCode.PRECONDITION and failed.stage is Stage.PRECONDITIONS
    assert not failed.may_have_effects


@pytest.mark.asyncio
async def test_failed_verifier_allows_inspection_but_never_reissues_effect(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.outbox, "matches", lambda *args: False)
    provider = Scripted(
        [
            call("local.append_message", MESSAGE),
            call("local.check", {}),
            call("local.append_message", MESSAGE),
        ]
    )
    result = await harness.runner(provider).run("check", Mode.EXECUTE)
    assert result.status == "error" and result.error == "duplicate_effect"
    assert harness.outbox.count == 1 and len(result.steps) == 3
    assert result.steps[0].outcome.stage is Stage.VERIFY
    assert provider.inputs[1].steps[0].outcome.diagnostic is not None
    assert result.steps[-1].outcome.reason is Reason.EFFECT_ALREADY_ISSUED


@pytest.mark.asyncio
async def test_failed_verifier_then_read_and_finish_remains_unconfirmed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.outbox, "matches", lambda *args: False)
    result = await harness.runner(
        Scripted([call("local.append_message", MESSAGE), call("local.check", {})])
    ).run("check", Mode.EXECUTE)
    assert result.status == "error" and result.error == "verification_failed"
    assert harness.outbox.count == 1


@pytest.mark.asyncio
async def test_missing_window_can_be_observed_and_selected_by_partial_process(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    probe = WindowsProbe()
    register_windows(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)

        async def deny(value: object) -> None:
            pytest.fail("No approval or clarification expected")

        provider = Scripted(
            [
                call("windows.focus_app", {"target": target().model_dump()}),
                call("windows.get_open_windows", {}),
                call("windows.focus_app", {"process": "note"}),
            ]
        )
        events = []
        result = await Runner(
            registry,
            engine,
            provider,
            deny,
            deny,
            notify=lambda kind, value: events.append((kind, value)),
        ).run("focus", Mode.EXECUTE)
        assert result.status == "finished" and len(result.steps) == 3
        assert result.steps[0].outcome.error is ErrorCode.UNOBSERVED_TARGET
        assert result.steps[0].outcome.stage is Stage.OBSERVED_TARGET
        assert not result.steps[0].outcome.may_have_effects
        assert any(json.loads(row)["error"] == "unobserved_target" for row in audit.recent())
        focus = next(request for request in probe.calls if request.operation == "focus")
        assert focus.target == target() and focus.process is None
        assert len([event for event in events if event[0] == "tool"]) == 3


@pytest.mark.asyncio
async def test_workspace_native_hint_is_local_and_uses_the_installed_catalog(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    probe = WindowsProbe()
    register_windows(registry, probe)
    profile = WorkspaceProfile(
        apps=(
            AppEntry(
                id="editor",
                name="Мой редактор",
                category="productivity",
                kind="native",
                native_exe_hint="notepad",
            ),
        )
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:

        async def deny(value: object) -> None:
            pytest.fail("Unexpected interaction")

        provider = Scripted([call("windows.open_app", {"app": "Мой редактор"})])
        result = await Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            provider,
            deny,
            deny,
            workspace=profile,
        ).run("open", Mode.EXECUTE)
        assert result.status == "finished" and probe.calls[0].app == "notepad"
        assert "native_exe_hint" not in provider.inputs[0].workspace.model_dump_json()


@pytest.mark.asyncio
async def test_openai_transport_receives_failure_details(harness: Harness) -> None:
    failed = harness.engine.prepare("local.check", {"extra": True}, Mode.EXECUTE)
    assert not isinstance(failed, Action)

    async def transport(payload: dict[str, Any]) -> bytes:
        content = payload["input"][0]["content"]
        observation = json.loads(content)["untrusted_observations"][0]
        assert observation["failure"]["reason"] == "invalid_arguments"
        assert observation["failure"]["message"]
        return json.dumps(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"kind":"finish"}'}],
                    }
                ],
            }
        ).encode()

    data = PlannerInput(
        "check",
        (),
        (Step("local.check", failed),),
        Mode.EXECUTE,
        json.dumps(harness.registry.discover()),
    )
    assert (await OpenAIProvider("fixture", transport=transport).propose(data)).kind == "finish"


@pytest.mark.asyncio
async def test_stale_mark_is_audited_then_refreshed_before_the_click(tmp_path: Path) -> None:
    rectangle = Rectangle(left=0, top=0, right=100, bottom=100)
    element = ElementTarget(
        mark_id=1,
        runtime_id=[3],
        name="Next",
        control_type="Button",
        automation_id="next",
        rectangle=rectangle,
    )

    class Backend:
        clicks = 0

        async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
            if request.operation == "inspect":
                return WindowsResult(
                    target=target(), window_rectangle=rectangle, elements=[element]
                )
            assert request.operation == "click" and request.element == element
            self.clicks += 1
            return WindowsResult(target=target(), performed=True)

    backend = Backend()
    registry = ToolRegistry()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:

        async def deny(value: object) -> None:
            pytest.fail("Unexpected interaction")

        provider = Scripted(
            [
                call("windows.inspect_active_window", {}),
                call("windows.click_element", {"target": target().model_dump(), "mark_id": 96}),
                call("windows.inspect_active_window", {}),
                call("windows.click_element", {"target": target().model_dump(), "mark_id": 1}),
            ]
        )
        result = await Runner(
            registry, PermissionEngine(registry, ApprovalStore(), audit), provider, deny, deny
        ).run("next", Mode.EXECUTE)
        assert result.status == "finished" and backend.clicks == 1
        assert provider.inputs[2].steps[1].outcome.reason is Reason.STALE_MARK
        assert any(json.loads(row)["reason"] == "stale_mark" for row in audit.recent())


@pytest.mark.asyncio
async def test_offline_notepad_recipe_accepts_an_observed_nonempty_editor() -> None:
    from jarvis.core.planner.offline import OfflineProvider

    observed = target().model_copy(update={"empty": False})
    step = Step(
        "windows.open_app",
        Outcome(
            uuid4(), Status.SUCCESS, result_json=WindowsResult(target=observed).model_dump_json()
        ),
    )
    data = PlannerInput("открой блокнот и напиши «more»", (), (step,), Mode.EXECUTE, "[]")
    proposal = await OfflineProvider().propose(data)
    assert proposal.tool == "windows.type_text" and json.loads(proposal.arguments)["text"] == "more"
