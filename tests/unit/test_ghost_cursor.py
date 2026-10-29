"""Ghost geometry, physical takeover, stale grounding and cursor-preserving input."""

import asyncio
import ctypes
import importlib
from contextlib import closing
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, PropertyMock
from uuid import uuid4

import pytest
from tests.unit.test_windows_cross_app import ELEMENT, RECT, adapter, control
from tests.windows_support import target

from jarvis.browser.session import screen_rectangle
from jarvis.core.planner.contracts import Limits, Step
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.platforms.windows.ghost import CursorTarget, HumanTakeover, PhysicalInput
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ToolError, canonical
from jarvis.tools.browser import BrowserResult, PageTarget, PageView, ReadPage, register_browser
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import Rectangle, WindowsResult, register_windows


def test_cursor_move_center_bounds_and_scaled_negative_monitor() -> None:
    rect = screen_rectangle(
        {"x": 100.5, "y": 20.0, "width": 80.0, "height": 40.0},
        Rectangle(left=-1920, top=100, right=0, bottom=1180),
        1280,
        720,
    )
    move = CursorTarget(rectangle=rect, action="type").cursor_move()
    assert move["target_x"] == -1709
    assert move["target_y"] == 160
    assert move["bounding_box"] == {"left": -1769, "top": 130, "width": 120, "height": 60}
    assert move["cursor_state"] == "moving" and move["action"] == "type"


@pytest.mark.parametrize("event", ["left", "right", "typing", "near", "fast_tap"])
def test_yield_waits_for_two_seconds_of_quiet_and_restarts_on_activity(event: str) -> None:
    now = [0.0]
    value = [PhysicalInput(0, 0, 100)]
    monitor = HumanTakeover(lambda: value[0], lambda: now[0])
    monitor.start()
    monitor.target = CursorTarget(rectangle=RECT, window_handle=100)
    value[0] = PhysicalInput(
        310 if event == "near" else 0,
        220 if event == "near" else 0,
        100,
        buttons=event in {"left", "right"},
        typing=event == "typing",
        mouse_serial=1 if event == "fast_tap" else 0,
    )
    assert monitor.sample() and monitor.pending
    value[0] = PhysicalInput(value[0].x, value[0].y, 100, mouse_serial=value[0].mouse_serial)
    now[0] = 1.99
    monitor.sample()
    assert not bool(monitor.quiet)
    now[0] = 2.0
    assert bool(monitor.quiet)
    value[0] = PhysicalInput(310, 220, 100, typing=True)
    monitor.sample()
    assert not bool(monitor.quiet)
    value[0] = PhysicalInput(310, 220, 100)
    now[0] = 3.9
    monitor.sample()
    assert not bool(monitor.quiet)
    now[0] = 4.0
    assert bool(monitor.quiet)
    monitor.acknowledge()
    assert not bool(monitor.pending)
    assert not monitor.sample()  # A stationary cursor near the goal cannot trap the run.


def test_other_window_proximity_and_injected_motion_do_not_yield() -> None:
    value = [PhysicalInput(0, 0, 100, move_serial=1)]
    monitor = HumanTakeover(lambda: value[0])
    monitor.start()
    monitor.target = CursorTarget(rectangle=RECT, window_handle=100)
    value[0] = PhysicalInput(310, 220, 999, move_serial=2)
    assert not monitor.sample()
    value[0] = PhysicalInput(311, 220, 100, move_serial=2)
    assert not monitor.sample()  # Injected movement has no new physical hook counter.
    value[0] = PhysicalInput(312, 220, 100, move_serial=3)
    assert monitor.sample()


def test_trusted_prompt_click_and_key_release_cannot_create_an_approval_loop() -> None:
    value = [PhysicalInput(0, 0, 100)]
    monitor = HumanTakeover(lambda: value[0])
    monitor.start()
    monitor.target = CursorTarget(rectangle=RECT, window_handle=100)
    monitor.begin_prompt()
    value[0] = PhysicalInput(0, 0, 999, buttons=True, typing=True, mouse_serial=1, key_serial=1)
    assert not monitor.sample()
    monitor.end_prompt()
    assert not monitor.sample()  # The approval mouse button may still be held down.
    value[0] = PhysicalInput(0, 0, 999, mouse_serial=1, key_serial=1)
    assert not monitor.sample()
    value[0] = PhysicalInput(0, 0, 100, buttons=True, mouse_serial=2, key_serial=1)
    assert monitor.sample()  # A new human intervention is never suppressed.


@pytest.mark.asyncio
async def test_yield_cancels_old_action_and_refreshes_grounding_through_engine(
    tmp_path: Path,
) -> None:
    fresh = ELEMENT.model_copy(update={"name": "New control", "runtime_id": [999]})
    observation = WindowsResult(target=target(), elements=[fresh], window_rectangle=RECT)
    backend = Mock(call=AsyncMock(return_value=observation))
    registry = ToolRegistry()
    register_windows(registry, backend)
    events: list[tuple[str, object]] = []
    monitor = HumanTakeover(lambda: PhysicalInput(0, 0, 100), lambda: 3.0)
    monitor.start()
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        runner = Runner(
            registry,
            engine,
            Mock(),
            AsyncMock(),
            AsyncMock(),
            takeover=monitor,
            notify=lambda k, v: events.append((k, v)),
        )
        old = WindowsResult(target=target(), elements=[ELEMENT], window_rectangle=RECT)
        runner.steps = [
            Step(
                "windows.inspect_active_window",
                Outcome(
                    uuid4(),
                    Status.SUCCESS,
                    result_json=canonical(old),
                ),
            )
        ]
        args = {"target": target().model_dump(), "element": ELEMENT.model_dump()}
        action = engine.prepare("windows.click_element", args, Mode.EXECUTE)
        assert isinstance(action, Action) and runner._observed_target(action)
        runner.active = action
        monitor.pending = True
        assert await runner._yield_boundary(Mode.EXECUTE)
        assert not runner._observed_target(action)
        assert (await engine.execute(action)).status is not Status.SUCCESS
        new_action = engine.prepare(
            "windows.click_element", args | {"element": fresh.model_dump()}, Mode.EXECUTE
        )
        assert isinstance(new_action, Action) and runner._observed_target(new_action)
        assert runner._planner_steps()[0].outcome.result_json is None
        assert any(k == "observations_refreshed" for k, _ in events)
        assert events[0] == ("cursor_state", "yielding")
        assert all(call.args[0].operation == "inspect" for call in backend.call.call_args_list)


@pytest.mark.asyncio
async def test_cancel_during_yield_cannot_resume_or_refresh(tmp_path: Path) -> None:
    registry = ToolRegistry()
    backend = Mock(call=AsyncMock())
    register_windows(registry, backend)
    monitor = HumanTakeover(lambda: PhysicalInput(0, 0, 100), lambda: 0.0)
    monitor.start()
    monitor.pending = True
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        runner = Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            Mock(),
            AsyncMock(),
            AsyncMock(),
            takeover=monitor,
        )
        task = asyncio.create_task(runner._yield_boundary(Mode.EXECUTE))
        await asyncio.sleep(0)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        backend.call.assert_not_called()


@pytest.mark.asyncio
async def test_yield_expires_dom_snapshot_and_reads_current_document(tmp_path: Path) -> None:
    old_target = PageTarget(
        tab_id="a" * 32,
        document_id="b" * 32,
        url="https://example.com/",
        origin="https://example.com",
        fingerprint="c" * 64,
    )
    fresh_target = old_target.model_copy(update={"document_id": "d" * 32})
    old = PageView(target=old_target, title="Old", text="Old document")
    fresh = PageView(target=fresh_target, title="Fresh", text="Human navigation")

    async def call(command: Any, context: Any) -> BrowserResult:
        return (
            BrowserResult(tabs=[fresh_target])
            if command.operation == "get_tabs"
            else BrowserResult(page=fresh)
        )

    backend = Mock(call=AsyncMock(side_effect=call))
    registry = ToolRegistry()
    register_browser(registry, backend, NetworkPolicy())
    monitor = HumanTakeover(lambda: PhysicalInput(0, 0, 100), lambda: 3.0)
    monitor.start()
    monitor.pending = True
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        runner = Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            Mock(),
            AsyncMock(),
            AsyncMock(),
            takeover=monitor,
        )
        runner.steps = [
            Step(
                "browser.read",
                Outcome(uuid4(), Status.SUCCESS, result_json=canonical(BrowserResult(page=old))),
                canonical(ReadPage(target=old_target)),
            )
        ]
        assert runner._browser_page(old_target) == old
        assert await runner._yield_boundary(Mode.EXECUTE)
        assert runner._browser_page(old_target) is None
        assert runner._browser_page(fresh_target) == fresh
        operations = [c.args[0].operation for c in backend.call.call_args_list]
        assert "get_tabs" in operations and "read" in operations
        assert runner._planner_steps()[0].outcome.result_json is None


@pytest.mark.asyncio
async def test_simulation_does_not_start_physical_monitor(tmp_path: Path) -> None:
    monitor = Mock(spec=HumanTakeover)
    registry = ToolRegistry()
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        runner = Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            Mock(propose=AsyncMock(return_value={"kind": "finish"})),
            AsyncMock(),
            AsyncMock(),
            takeover=monitor,
            limits=Limits(),
        )
        assert (await runner.run("fixture", Mode.SIMULATION)).status == "no_action"
        monitor.start.assert_not_called()
        monitor.sample.assert_not_called()


@pytest.mark.parametrize(
    "pattern,method",
    [
        ("iface_invoke", "Invoke"),
        ("iface_toggle", "Toggle"),
        ("iface_selection_item", "Select"),
    ],
)
def test_native_patterns_never_move_system_cursor(
    monkeypatch: pytest.MonkeyPatch,
    pattern: str,
    method: str,
) -> None:
    class MissingPattern(Exception):
        pass

    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(NoPatternInterfaceError=MissingPattern)
            if name == "pywinauto.uia_defines"
            else real_import(name)
        ),
    )

    desktop, button = adapter(), control()
    order = ["iface_invoke", "iface_toggle", "iface_selection_item"]
    for missing in order[: order.index(pattern)]:
        monkeypatch.setattr(
            type(button), missing, PropertyMock(side_effect=MissingPattern), raising=False
        )
    expected = getattr(getattr(button, pattern), method)
    desktop.phantom_click(Mock(), button)
    expected.assert_called_once()
    desktop.user32.SetCursorPos.assert_not_called()
    button.click_input.assert_not_called()


@pytest.mark.parametrize("sent", [0, 2, 4])
def test_fallback_batches_restore_and_restores_after_partial_failure(sent: int) -> None:
    desktop = adapter()

    def cursor(pointer: Any) -> int:
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT))
        point.contents.x, point.contents.y = 50, 70
        return 1

    desktop.user32.GetCursorPos.side_effect = cursor
    desktop.user32.GetAsyncKeyState.return_value = 0
    desktop.user32.GetSystemMetrics.side_effect = lambda k: {76: 0, 77: 0, 78: 1920, 79: 1080}[k]
    desktop.user32.SendInput.return_value = sent
    if sent == 4:
        desktop.save_click_restore(300, 200)
        desktop.user32.SetCursorPos.assert_not_called()
    else:
        with pytest.raises(ToolError, match="native_failure"):
            desktop.save_click_restore(300, 200)
        desktop.user32.SetCursorPos.assert_called_once_with(50, 70)
    count, inputs, _ = desktop.user32.SendInput.call_args.args
    assert count == 4
    assert inputs[1].value.mouse.flags == 2 and inputs[2].value.mouse.flags == 4
    assert inputs[3].value.mouse.dx == round(50 * 65535 / 1919)
    assert inputs[3].value.mouse.dy == round(70 * 65535 / 1079)
