"""Physical takeover with fake Quartz; no OS permission or native input is used."""

import importlib
from collections.abc import Iterator
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.platforms.macos import input as mac_input
from jarvis.platforms.macos.input import MacPhysicalPoller
from jarvis.platforms.windows import ghost
from jarvis.platforms.windows.ghost import CursorTarget, HumanTakeover, PhysicalInput
from jarvis.tools.windows import Rectangle


class FakeQuartz:
    kCGEventLeftMouseDown, kCGEventLeftMouseUp = 1, 2
    kCGEventRightMouseDown, kCGEventRightMouseUp = 3, 4
    kCGEventMouseMoved, kCGEventLeftMouseDragged, kCGEventRightMouseDragged = 5, 6, 7
    kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged = 10, 11, 12
    kCGEventScrollWheel = 22
    kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged = 25, 26, 27
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    kCGEventFlagMaskCommand, kCGEventFlagMaskControl = 0x100000, 0x40000
    kCGEventFlagMaskAlternate, kCGEventFlagMaskShift = 0x80000, 0x20000
    kCGEventFlagMaskSecondaryFn = 0x800000
    kCGEventSourceUnixProcessID = 41
    kCGMouseEventButtonNumber, kCGKeyboardEventKeycode = 3, 9
    kCGEventSourceStateHIDSystemState = 1
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly = 1, 0, 1
    kCGWindowListOptionOnScreenOnly, kCGWindowListExcludeDesktopElements = 1, 16
    kCGNullWindowID = 0
    kCFRunLoopDefaultMode = "default"

    def __init__(self) -> None:
        self.tap, self.source, self.loop = object(), object(), object()
        self.CGPreflightListenEventAccess = Mock(return_value=True)
        self.CGRequestListenEventAccess = Mock(side_effect=AssertionError("No permission prompts"))
        self.CGEventSourceButtonState = Mock(return_value=False)
        self.CGEventSourceKeyState = Mock(return_value=False)
        self.CGEventSourceFlagsState = Mock(return_value=0)
        self.CGEventTapCreate = Mock(return_value=self.tap)
        self.CGEventTapIsEnabled = Mock(return_value=True)
        self.CFMachPortCreateRunLoopSource = Mock(return_value=self.source)
        self.CFRunLoopGetCurrent = Mock(return_value=self.loop)
        self.CFRunLoopAddSource = Mock()
        self.CFRunLoopRunInMode = Mock(side_effect=lambda *args: Event().wait(0.01))
        self.CFRunLoopRemoveSource = Mock()
        self.CFMachPortInvalidate = Mock()
        self.CGEventCreate = Mock(return_value=object())
        self.CGEventGetLocation = Mock(return_value=SimpleNamespace(x=-120.4, y=150.6))
        self.CGWindowListCopyWindowInfo = Mock(
            return_value=[
                {"kCGWindowOwnerPID": 42, "kCGWindowNumber": 1, "kCGWindowLayer": 25},
                {"kCGWindowOwnerPID": 99, "kCGWindowNumber": 2, "kCGWindowLayer": 0},
                {"kCGWindowOwnerPID": 42, "kCGWindowNumber": 3, "kCGWindowAlpha": 0},
                {"kCGWindowOwnerPID": 42, "kCGWindowNumber": 100, "kCGWindowLayer": 0},
            ]
        )
        self.CGEventGetIntegerValueField = Mock(
            side_effect=lambda event, field: event.get(field, 0)
        )
        self.CGEventGetFlags = Mock(side_effect=lambda event: event.get("flags", 0))
        self.CGEventKeyboardGetUnicodeString = Mock(side_effect=AssertionError("No text reads"))


@pytest.fixture
def quartz(monkeypatch: pytest.MonkeyPatch) -> FakeQuartz:
    q = FakeQuartz()
    workspace = SimpleNamespace(
        frontmostApplication=lambda: SimpleNamespace(processIdentifier=lambda: 42)
    )
    appkit = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace))
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: q if name == "Quartz" else appkit if name == "AppKit" else real_import(name),
    )
    return q


@pytest.fixture
def poller(quartz: FakeQuartz) -> Iterator[MacPhysicalPoller]:
    monitor = MacPhysicalPoller()
    try:
        yield monitor
    finally:
        monitor.close()
        assert not monitor.thread.is_alive()


def test_passive_tap_is_task_scoped_and_observes_real_logical_geometry(quartz: FakeQuartz) -> None:
    poller = MacPhysicalPoller()
    assert poller.read() == PhysicalInput(-120, 151, 100, move_serial=0)
    args = quartz.CGEventTapCreate.call_args.args
    assert args[:3] == (1, 0, 1)  # Session, head insert, listen-only.
    assert args[3] & (1 << quartz.kCGEventKeyDown)
    assert args[3] & (1 << quartz.kCGEventScrollWheel)
    assert args[5] is None
    quartz.CGRequestListenEventAccess.assert_not_called()
    poller.close()
    poller.close()
    quartz.CFRunLoopRemoveSource.assert_called_once_with(quartz.loop, quartz.source, "default")
    quartz.CFMachPortInvalidate.assert_called_once_with(quartz.tap)
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        poller.read()


@pytest.mark.parametrize("kind", [1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 22, 25, 26, 27])
def test_software_input_cannot_pause_jarvis(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
    kind: int,
) -> None:
    before = poller.read()
    event = {quartz.kCGEventSourceUnixProcessID: 1234, "flags": quartz.kCGEventFlagMaskCommand}
    assert poller._event(None, kind, event, None) is event
    assert poller.read() == before
    quartz.CGEventGetFlags.assert_not_called()
    quartz.CGEventKeyboardGetUnicodeString.assert_not_called()


@pytest.mark.parametrize("kind,button", [(1, 0), (3, 1), (25, 4)])
def test_quick_physical_click_is_retained_between_polls(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
    kind: int,
    button: int,
) -> None:
    event = {quartz.kCGMouseEventButtonNumber: button}
    assert poller._event(None, kind, event, None) is event
    assert poller.read().buttons
    poller._event(None, kind + 1, event, None)
    state = poller.read()
    assert not state.buttons and state.mouse_serial == 1


def test_keys_modifiers_and_scroll_only_keep_aggregate_state(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event = {quartz.kCGKeyboardEventKeycode: 5, "text": "private text must never be read"}
    poller._event(None, quartz.kCGEventKeyDown, event, None)
    assert poller.read().typing
    poller._event(None, quartz.kCGEventKeyUp, event, None)
    assert not poller.read().typing and poller.read().key_serial == 1
    poller._event(
        None, quartz.kCGEventFlagsChanged, {"flags": quartz.kCGEventFlagMaskCommand}, None
    )
    assert poller.read().typing
    poller._event(
        None, quartz.kCGEventFlagsChanged, {"flags": 0x10000}, None
    )  # Caps Lock is not held.
    assert not poller.read().typing and poller.read().key_serial == 3
    poller._event(None, quartz.kCGEventScrollWheel, {}, None)
    assert poller.read().mouse_serial == 1
    assert not poller.keys
    quartz.CGEventKeyboardGetUnicodeString.assert_not_called()
    assert "private" not in repr(poller.read())
    assert capsys.readouterr() == ("", "")


def test_movement_yields_only_for_physical_motion_in_the_target_window(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
) -> None:
    monitor = HumanTakeover(poller.read, lambda: 0)
    monitor.start()
    monitor.target = CursorTarget(
        rectangle=Rectangle(left=300, top=200, right=320, bottom=240),
        window_handle=100,
    )
    quartz.CGEventGetLocation.return_value = SimpleNamespace(x=310, y=220)
    poller._event(None, quartz.kCGEventMouseMoved, {quartz.kCGEventSourceUnixProcessID: 15}, None)
    assert not monitor.sample()
    poller._event(None, quartz.kCGEventMouseMoved, {}, None)
    quartz.CGEventGetLocation.return_value = SimpleNamespace(x=311, y=220)
    assert monitor.sample() and monitor.pending


def test_permission_denial_and_missing_dependencies_fail_without_a_prompt(
    quartz: FakeQuartz,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quartz.CGPreflightListenEventAccess.return_value = False
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        MacPhysicalPoller()
    quartz.CGEventTapCreate.assert_not_called()
    quartz.CGRequestListenEventAccess.assert_not_called()
    monkeypatch.setattr(importlib, "import_module", Mock(side_effect=ImportError))
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        MacPhysicalPoller()


@pytest.mark.parametrize("failure", ["tap", "source", "disabled", "runloop"])
def test_failed_start_releases_every_created_resource(quartz: FakeQuartz, failure: str) -> None:
    if failure == "tap":
        quartz.CGEventTapCreate.return_value = None
    elif failure == "source":
        quartz.CFMachPortCreateRunLoopSource.return_value = None
    elif failure == "disabled":
        quartz.CGEventTapIsEnabled.return_value = False
    else:
        quartz.CFRunLoopAddSource.side_effect = RuntimeError
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        MacPhysicalPoller()
    assert quartz.CFMachPortInvalidate.call_count == (0 if failure == "tap" else 1)


@pytest.mark.parametrize("kind", [0xFFFFFFFE, 0xFFFFFFFF])
def test_disabled_tap_fails_closed_instead_of_silently_reenabling(
    poller: MacPhysicalPoller,
    kind: int,
) -> None:
    poller._event(None, kind, {}, None)
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        poller.read()


def test_callback_errors_and_lost_native_tap_cannot_be_treated_as_inactivity(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
) -> None:
    quartz.CGEventGetIntegerValueField.side_effect = RuntimeError
    event: dict[str, object] = {}
    assert poller._event(None, quartz.kCGEventKeyDown, event, None) is event
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        poller.read()


def test_disabled_tap_between_callbacks_and_missing_cursor_fail_closed(
    poller: MacPhysicalPoller,
    quartz: FakeQuartz,
) -> None:
    quartz.CGEventCreate.return_value = None
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        poller.read()
    quartz.CGEventTapIsEnabled.return_value = False
    assert poller.failed.wait(1)
    with pytest.raises(OSError, match="input_monitor_unavailable"):
        poller.read()


def test_macos_monitor_is_chosen_locally_and_disposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Mock(read=Mock(return_value=PhysicalInput(0, 0, 0)))
    factory = Mock(return_value=backend)
    monkeypatch.setattr(ghost, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(mac_input, "MacPhysicalPoller", factory)
    monitor = HumanTakeover()
    monitor.start()
    assert monitor.enabled
    factory.assert_called_once_with()
    backend.read.assert_called_once_with()
    monitor.stop()
    backend.close.assert_called_once_with()
    assert not bool(monitor.enabled)
    assert monitor.read is None


def test_reader_failure_has_a_finite_monitor_error_instead_of_native_details() -> None:
    reader = Mock(return_value=PhysicalInput(0, 0, 100))
    monitor = HumanTakeover(reader)
    monitor.start()
    reader.side_effect = OSError("private native details")
    with pytest.raises(ghost.InputMonitorUnavailable, match="^input_monitor_unavailable$"):
        monitor.sample()


def test_starting_with_command_and_caps_lock_does_not_leave_typing_stuck(
    quartz: FakeQuartz,
) -> None:
    quartz.CGEventSourceKeyState.side_effect = lambda state, key: key in (5, 55, 57)
    quartz.CGEventSourceFlagsState.return_value = quartz.kCGEventFlagMaskCommand | 0x10000
    poller = MacPhysicalPoller()
    try:
        assert poller.read().typing
        poller._event(None, quartz.kCGEventKeyUp, {quartz.kCGKeyboardEventKeycode: 5}, None)
        poller._event(None, quartz.kCGEventFlagsChanged, {"flags": 0x10000}, None)
        assert not poller.read().typing
    finally:
        poller.close()
