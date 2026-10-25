"""Runner provenance regressions; mock adapters do not verify native Windows input."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from tests.unit.test_planner import Scripted
from tests.windows_support import target

from jarvis.core.planner.contracts import Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.base import canonical
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    ElementTarget,
    Rectangle,
    WindowsResult,
    clipboard_digest,
    register_windows,
)

RECT = Rectangle(left=10, top=20, right=610, bottom=420)
MARK = ElementTarget(
    mark_id=1,
    runtime_id=[10, 20],
    name="Search",
    control_type="Button",
    automation_id="search",
    rectangle=RECT,
)


def observe(runner: Runner, name: str, result: WindowsResult) -> None:
    runner.steps.append(Step(name, Outcome(uuid4(), Status.SUCCESS, result_json=canonical(result))))


@pytest.fixture
def windows_runner(tmp_path: Path) -> Iterator[tuple[Runner, AsyncMock, AsyncMock]]:
    backend = AsyncMock()
    registry = ToolRegistry()
    register_windows(registry, Mock(call=backend))
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        store = ApprovalStore()
        authority = store.take_authority(audit.approved)
        approve = AsyncMock(side_effect=authority.approve)
        runner = Runner(
            registry, PermissionEngine(registry, store, audit), Scripted([]), approve, AsyncMock()
        )
        yield runner, backend, approve


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [Mode.EXECUTE, Mode.SIMULATION])
@pytest.mark.parametrize(
    "name,args,reply",
    [
        (
            "clipboard_get",
            {},
            WindowsResult(text="text", clipboard_sha256=clipboard_digest("text")),
        ),
        (
            "clipboard_set",
            {"text": "text"},
            WindowsResult(text="text", clipboard_sha256=clipboard_digest("text")),
        ),
        *[
            (
                "screenshot",
                {"scope": scope},
                WindowsResult(image_base64="/9j/2Q==", image_format="JPEG", width=1, height=1),
            )
            for scope in ("active_window", "screen")
        ],
    ],
)
async def test_global_tools_run_as_first_step_without_window_history(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
    mode: Mode,
    name: str,
    args: dict[str, str],
    reply: WindowsResult,
) -> None:
    runner, backend, approve = windows_runner
    backend.return_value = reply
    runner.provider = Scripted([call("windows." + name, args)])
    result = await runner.run("test", mode)
    assert result.status == ("finished" if mode is Mode.EXECUTE else "simulated")
    assert len(result.steps) == 1
    approve.assert_not_awaited()
    if mode is Mode.EXECUTE:
        backend.assert_awaited_once()
        assert backend.call_args.args[0].operation == name
    else:
        backend.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["Ctrl+C", "Ctrl+V", "Ctrl+A", "Enter", "Tab", "Alt+F4", "click"]
)
async def test_targeted_input_reaches_adapter_after_inspection(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock], operation: str
) -> None:
    runner, backend, approve = windows_runner
    inspection = WindowsResult(target=target(), window_rectangle=RECT)
    proposals = [call("windows.inspect_active_window", {})]
    replies = [inspection]
    args: dict[str, object] = {"target": target().model_dump()}
    if operation == "Ctrl+V":
        digest = clipboard_digest("text")
        proposals.append(call("windows.clipboard_set", {"text": "text"}))
        replies.append(WindowsResult(text="text", clipboard_sha256=digest))
        args["clipboard_sha256"] = digest
    if operation == "click":
        name = "click_element"
        args.update(x=100, y=100, window_rectangle=RECT.model_dump())
    else:
        name = "send_hotkey"
        args["hotkey"] = operation
    proposals.append(call("windows." + name, args))
    replies.append(WindowsResult(target=target(), performed=True))
    runner.provider = Scripted(proposals)
    backend.side_effect = replies
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "finished"
    assert all(step.outcome.status is Status.SUCCESS for step in result.steps)
    assert backend.await_count == len(replies)
    assert backend.call_args.args[0].operation == ("click" if operation == "click" else "hotkey")
    assert approve.await_count == int(operation in ("Enter", "Alt+F4", "click"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,history",
    [
        (operation, history)
        for operation in ("hotkey", "click")
        for history in ("missing", "simulated", "failed", "changed_window", "changed_bounds")
        if operation == "click" or history != "changed_bounds"
    ],
)
async def test_unobserved_input_stops_before_approval_and_adapter(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock], operation: str, history: str
) -> None:
    runner, backend, approve = windows_runner
    if history != "missing":
        status = {"simulated": Status.SIMULATED, "failed": Status.ERROR}.get(
            history, Status.SUCCESS
        )
        observed = target()
        if history == "changed_window":
            observed = observed.model_copy(update={"pid": 999})
        bounds = RECT.model_copy(update={"right": 700}) if history == "changed_bounds" else RECT
        runner.steps.append(
            Step(
                "windows.inspect_active_window",
                Outcome(
                    uuid4(),
                    status,
                    result_json=canonical(WindowsResult(target=observed, window_rectangle=bounds)),
                ),
            )
        )
    args: dict[str, object] = {"target": target().model_dump()}
    if operation == "click":
        name = "click_element"
        args.update(x=100, y=100, window_rectangle=RECT.model_dump())
    else:
        name = "send_hotkey"
        args["hotkey"] = "Enter"
    runner.provider = Scripted([call("windows." + name, args)])
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "error" and result.error == "unobserved_target"
    assert runner.active is None
    backend.assert_not_awaited()
    approve.assert_not_awaited()


def test_inspect_screenshot_click_keeps_uia_provenance(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
) -> None:
    runner, _, _ = windows_runner
    observe(
        runner,
        "windows.inspect_active_window",
        WindowsResult(
            target=target(),
            elements=[MARK],
            window_rectangle=RECT,
        ),
    )
    observe(
        runner,
        "windows.screenshot",
        WindowsResult(
            target=target(),
            image_format="JPEG",
            width=600,
            height=400,
        ),
    )
    action = runner.engine.prepare(
        "windows.click_element",
        {
            "target": target().model_dump(),
            "element": MARK.model_dump(),
        },
        Mode.EXECUTE,
    )
    from jarvis.permissions.approvals import Action

    assert isinstance(action, Action)
    assert runner._observed_target(action) is True
    runner.engine.cancel(action)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_mark", [False, True])
async def test_inspect_screenshot_click_resolves_before_engine_and_adapter(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
    use_mark: bool,
) -> None:
    runner, backend, approve = windows_runner
    runner.provider = Scripted(
        [
            call("windows.inspect_active_window", {}),
            call("windows.screenshot", {"scope": "active_window"}),
            call(
                "windows.click_element",
                {
                    "target": target().model_dump(),
                    **({"mark_id": 1} if use_mark else {"element": MARK.model_dump()}),
                },
            ),
        ]
    )
    backend.side_effect = [
        WindowsResult(target=target(), elements=[MARK], window_rectangle=RECT),
        WindowsResult(
            target=target(), image_base64="/9j/2Q==", image_format="JPEG", width=1, height=1
        ),
        WindowsResult(target=target(), performed=True),
    ]
    result = await runner.run("click Search", Mode.EXECUTE)
    assert result.status == "finished" and len(result.steps) == 3
    assert backend.call_args.args[0].element == MARK
    approve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["open_app", "focus_app", "click_element", "type_text", "send_hotkey"]
)
@pytest.mark.parametrize("use_mark", [False, True])
async def test_mutations_expire_old_elements_and_marks(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
    mutation: str,
    use_mark: bool,
) -> None:
    runner, backend, approve = windows_runner
    observe(
        runner,
        "windows.inspect_active_window",
        WindowsResult(
            target=target(),
            elements=[MARK],
            window_rectangle=RECT,
        ),
    )
    observe(runner, "windows." + mutation, WindowsResult(target=target(), performed=True))
    observe(runner, "windows.screenshot", WindowsResult(target=target(), image_format="JPEG"))
    runner.provider = Scripted(
        [
            call(
                "windows.click_element",
                {
                    "target": target().model_dump(),
                    **({"mark_id": 1} if use_mark else {"element": MARK.model_dump()}),
                },
            )
        ]
    )
    result = await runner.run("test", Mode.EXECUTE)
    assert result.error == "unobserved_target"
    backend.assert_not_awaited()
    approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_latest_mark_mapping_is_usable(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
) -> None:
    runner, backend, approve = windows_runner
    observe(
        runner,
        "windows.inspect_active_window",
        WindowsResult(target=target(), elements=[MARK], window_rectangle=RECT),
    )
    replacement = MARK.model_copy(update={"runtime_id": [99], "name": "Delete"})
    observe(
        runner,
        "windows.screenshot",
        WindowsResult(target=target(), elements=[replacement], window_rectangle=RECT),
    )
    runner.provider = Scripted(
        [call("windows.click_element", {"target": target().model_dump(), "mark_id": 1})]
    )
    backend.return_value = WindowsResult(target=target(), performed=True)
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "finished"
    assert backend.call_args.args[0].element == replacement
    assert approve.await_count == 1
    assert '"Delete"' in approve.call_args.args[0].payload


@pytest.mark.asyncio
async def test_unknown_mark_does_not_reach_adapter(
    windows_runner: tuple[Runner, AsyncMock, AsyncMock],
) -> None:
    runner, backend, approve = windows_runner
    observe(
        runner,
        "windows.inspect_active_window",
        WindowsResult(target=target(), elements=[MARK], window_rectangle=RECT),
    )
    runner.provider = Scripted(
        [call("windows.click_element", {"target": target().model_dump(), "mark_id": 96})]
    )
    result = await runner.run("test", Mode.EXECUTE)
    assert result.error == "unobserved_target"
    backend.assert_not_awaited()
    approve.assert_not_awaited()
