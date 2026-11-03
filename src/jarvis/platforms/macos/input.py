"""Task-scoped, passive physical input monitor. No text or input history is kept."""

import importlib
from threading import Event, Lock, Thread
from typing import Any

from jarvis.platforms.windows.ghost import PhysicalInput

# HIToolbox Events.h kVK_RightCommand through kVK_Function. Modifiers are tracked
# by flagsChanged; Caps Lock is latched, rather than a permanently held key.
MODIFIER_KEYCODES = frozenset(range(0x36, 0x40))


class MacPhysicalPoller:
    """Listen-only session tap, ignoring events posted by software processes.

    HID event counters also count CGEventPost, so they cannot distinguish Jarvis'
    own hotkeys. The tap checks source PID before reading only event metadata.
    No key characters, window titles, event objects or callbacks leave this class.
    Existing OS input-monitoring consent is required; this class never requests it.
    """

    def __init__(self) -> None:
        try:
            self.q: Any = importlib.import_module("Quartz")
            appkit: Any = importlib.import_module("AppKit")
            if not self.q.CGPreflightListenEventAccess():
                raise OSError("input_monitor_unavailable")
            self.workspace = appkit.NSWorkspace.sharedWorkspace()
            self.modifier_mask = (
                self.q.kCGEventFlagMaskCommand
                | self.q.kCGEventFlagMaskControl
                | self.q.kCGEventFlagMaskAlternate
                | self.q.kCGEventFlagMaskShift
                | self.q.kCGEventFlagMaskSecondaryFn
            )
            state = self.q.kCGEventSourceStateHIDSystemState
            self.buttons = {k for k in range(32) if self.q.CGEventSourceButtonState(state, k)}
            self.keys = {
                k
                for k in range(128)
                if k not in MODIFIER_KEYCODES and self.q.CGEventSourceKeyState(state, k)
            }
            self.modifiers = int(self.q.CGEventSourceFlagsState(state)) & self.modifier_mask
        except Exception:
            raise OSError("input_monitor_unavailable") from None
        self.lock = Lock()
        self.ready, self.closed, self.failed = Event(), Event(), Event()
        self.mouse_serial = self.key_serial = self.move_serial = 0
        self.thread = Thread(target=self._run, name="Jarvis Mac physical input", daemon=True)
        self.thread.start()
        if not self.ready.wait(1) or self.failed.is_set():
            self.close()
            raise OSError("input_monitor_unavailable")

    def _event(self, proxy: Any, kind: int, event: Any, context: Any) -> Any:
        q = self.q
        try:
            if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                self.failed.set()
                return event
            if (
                self.closed.is_set()
                or q.CGEventGetIntegerValueField(event, q.kCGEventSourceUnixProcessID) != 0
            ):
                return event
            with self.lock:
                if kind in (
                    q.kCGEventLeftMouseDown,
                    q.kCGEventRightMouseDown,
                    q.kCGEventOtherMouseDown,
                ):
                    self.buttons.add(
                        int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventButtonNumber))
                    )
                    self.mouse_serial += 1
                elif kind in (
                    q.kCGEventLeftMouseUp,
                    q.kCGEventRightMouseUp,
                    q.kCGEventOtherMouseUp,
                ):
                    self.buttons.discard(
                        int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventButtonNumber))
                    )
                elif kind == q.kCGEventScrollWheel:
                    self.mouse_serial += 1
                elif kind in (
                    q.kCGEventMouseMoved,
                    q.kCGEventLeftMouseDragged,
                    q.kCGEventRightMouseDragged,
                    q.kCGEventOtherMouseDragged,
                ):
                    self.move_serial += 1
                elif kind in (q.kCGEventKeyDown, q.kCGEventKeyUp, q.kCGEventFlagsChanged):
                    self.modifiers = int(q.CGEventGetFlags(event)) & self.modifier_mask
                    if kind == q.kCGEventKeyDown:
                        self.keys.add(
                            int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
                        )
                        self.key_serial += 1
                    elif kind == q.kCGEventKeyUp:
                        self.keys.discard(
                            int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
                        )
                    else:
                        self.key_serial += 1
        except Exception:
            # A native callback must neither throw into Quartz nor silently stop
            # protecting the task. The next read fails and the runner cancels.
            self.failed.set()
        return event  # Listen-only: input is never changed or suppressed.

    def _run(self) -> None:
        q = self.q
        tap = source = loop = None
        try:
            kinds = (
                q.kCGEventLeftMouseDown,
                q.kCGEventLeftMouseUp,
                q.kCGEventRightMouseDown,
                q.kCGEventRightMouseUp,
                q.kCGEventOtherMouseDown,
                q.kCGEventOtherMouseUp,
                q.kCGEventMouseMoved,
                q.kCGEventLeftMouseDragged,
                q.kCGEventRightMouseDragged,
                q.kCGEventOtherMouseDragged,
                q.kCGEventScrollWheel,
                q.kCGEventKeyDown,
                q.kCGEventKeyUp,
                q.kCGEventFlagsChanged,
            )
            tap = q.CGEventTapCreate(
                q.kCGSessionEventTap,
                q.kCGHeadInsertEventTap,
                q.kCGEventTapOptionListenOnly,
                sum(1 << kind for kind in kinds),
                self._event,
                None,
            )
            if tap is None or not q.CGEventTapIsEnabled(tap):
                raise OSError("input_monitor_unavailable")
            source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
            if source is None:
                raise OSError("input_monitor_unavailable")
            loop = q.CFRunLoopGetCurrent()
            q.CFRunLoopAddSource(loop, source, q.kCFRunLoopDefaultMode)
            self.ready.set()
            while not self.closed.is_set() and not self.failed.is_set():
                q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.1, False)
                if not q.CGEventTapIsEnabled(tap):
                    self.failed.set()
        except Exception:
            self.failed.set()
        finally:
            self.ready.set()
            if source is not None and loop is not None:
                q.CFRunLoopRemoveSource(loop, source, q.kCFRunLoopDefaultMode)
            if tap is not None:
                q.CFMachPortInvalidate(tap)

    def read(self) -> PhysicalInput:
        if self.closed.is_set() or self.failed.is_set() or not self.thread.is_alive():
            raise OSError("input_monitor_unavailable")
        try:
            q = self.q
            event = q.CGEventCreate(None)
            if event is None:
                raise OSError("cursor_unavailable")
            point = q.CGEventGetLocation(event)
            front = self.workspace.frontmostApplication()
            pid = int(front.processIdentifier()) if front is not None else 0
            rows = q.CGWindowListCopyWindowInfo(
                q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                q.kCGNullWindowID,
            )
            foreground = next(
                (
                    int(row["kCGWindowNumber"])
                    for row in (rows or ())[:2048]
                    if int(row.get("kCGWindowOwnerPID", 0)) == pid
                    and int(row.get("kCGWindowLayer", 0)) == 0
                    and float(row.get("kCGWindowAlpha", 1)) > 0
                ),
                0,
            )
            with self.lock:
                return PhysicalInput(
                    round(point.x),
                    round(point.y),
                    foreground,
                    bool(self.buttons),
                    bool(self.keys or self.modifiers),
                    self.mouse_serial,
                    self.key_serial,
                    self.move_serial,
                )
        except Exception:
            raise OSError("input_monitor_unavailable") from None

    def close(self) -> None:
        self.closed.set()
        self.thread.join(timeout=1)
        with self.lock:
            self.keys.clear()
            self.buttons.clear()
            self.modifiers = 0
