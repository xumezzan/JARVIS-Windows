"""Display-only targets and physical-input polling. No authority to execute tools."""

import ctypes
import json
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, Literal

from pydantic import Field

from jarvis.tools.base import ToolModel
from jarvis.tools.windows import Rectangle


class CursorTarget(ToolModel):
    rectangle: Rectangle
    action: Literal["click", "type", "scroll"] = "click"
    label: str = Field(default="", max_length=500)
    window_handle: int = Field(default=0, ge=0)

    def cursor_move(self) -> dict[str, object]:
        rect = self.rectangle
        return {
            "cursor_state": "moving",
            "target_x": (rect.left + rect.right) / 2,
            "target_y": (rect.top + rect.bottom) / 2,
            "bounding_box": {
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            },
            **self.model_dump(),
        }


@dataclass(frozen=True)
class PhysicalInput:
    x: int
    y: int
    foreground: int
    buttons: bool = False
    typing: bool = False
    mouse_serial: int = 0
    key_serial: int = 0
    move_serial: int | None = None


class _PhysicalPoller:
    """Non-blocking LL hooks distinguish hardware from Jarvis' tagged injected input.

    Only counters/button states survive a callback; no text or key history is kept.
    GetAsyncKeyState still supplies held-button/key state. Counters retain quick
    taps between polls, which polling the high bit alone would otherwise miss.
    """

    def __init__(self) -> None:
        self.user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.ready = Event()
        self.failed = False
        self.thread_id = 0
        self.mouse_serial = self.key_serial = self.move_serial = 0
        self.buttons = {k for k in (1, 2) if self.user32.GetAsyncKeyState(k) & 0x8000}
        self.keys = {k for k in range(8, 255) if self.user32.GetAsyncKeyState(k) & 0x8000}
        self.thread = Thread(target=self._run, name="Jarvis physical input", daemon=True)
        self.thread.start()
        if not self.ready.wait(1) or self.failed:
            self.close()
            raise OSError("input_monitor_unavailable")

    def _run(self) -> None:
        class Mouse(ctypes.Structure):
            _fields_ = [
                ("point", wintypes.POINT),
                ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Key(ctypes.Structure):
            _fields_ = [
                ("key", wintypes.DWORD),
                ("scan", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        hook_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        self.user32.SetWindowsHookExW.restype = wintypes.HANDLE
        self.user32.CallNextHookEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def mouse(code: int, message: int, address: int) -> int:
            if code >= 0:
                event = ctypes.cast(address, ctypes.POINTER(Mouse)).contents
                if not event.flags & 1:  # LLMHF_INJECTED
                    if message == 0x0200:
                        self.move_serial += 1
                    elif message in (0x0201, 0x0204):
                        self.buttons.add(1 if message == 0x0201 else 2)
                        self.mouse_serial += 1
                    elif message in (0x0202, 0x0205):
                        self.buttons.discard(1 if message == 0x0202 else 2)
            return int(self.user32.CallNextHookEx(None, code, message, address))

        def key(code: int, message: int, address: int) -> int:
            if code >= 0:
                event = ctypes.cast(address, ctypes.POINTER(Key)).contents
                if not event.flags & 0x10:  # LLKHF_INJECTED
                    if message in (0x0100, 0x0104):
                        self.keys.add(int(event.key))
                        self.key_serial += 1
                    elif message in (0x0101, 0x0105):
                        self.keys.discard(int(event.key))
            return int(self.user32.CallNextHookEx(None, code, message, address))

        mouse_callback, key_callback = hook_type(mouse), hook_type(key)
        self.thread_id = int(self.kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        module = self.kernel32.GetModuleHandleW(None)
        mouse_hook = self.user32.SetWindowsHookExW(14, mouse_callback, module, 0)
        key_hook = self.user32.SetWindowsHookExW(13, key_callback, module, 0)
        self.failed = not (mouse_hook and key_hook)
        self.ready.set()
        try:
            if not self.failed:
                while self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    self.user32.TranslateMessage(ctypes.byref(message))
                    self.user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if mouse_hook:
                self.user32.UnhookWindowsHookEx(mouse_hook)
            if key_hook:
                self.user32.UnhookWindowsHookEx(key_hook)

    def read(self) -> PhysicalInput:
        point = wintypes.POINT()
        if not self.user32.GetPhysicalCursorPos(ctypes.byref(point)):
            raise OSError("cursor_unavailable")
        return PhysicalInput(
            point.x,
            point.y,
            int(self.user32.GetForegroundWindow() or 0),
            any(self.user32.GetAsyncKeyState(k) & 0x8000 for k in tuple(self.buttons)),
            any(self.user32.GetAsyncKeyState(k) & 0x8000 for k in tuple(self.keys)),
            self.mouse_serial,
            self.key_serial,
            self.move_serial,
        )

    def close(self) -> None:
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        self.thread.join(timeout=1)


class HumanTakeover:
    """A latched intervention, released only after two seconds of actual inactivity.

    Polls high button bits (not GetAsyncKeyState's shared, unreliable low bit). A
    stationary cursor near a target does not continually re-arm the pause.
    """

    def __init__(
        self,
        read: Callable[[], PhysicalInput] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.read = read
        self.clock = clock
        self.target: CursorTarget | None = None
        self.previous: PhysicalInput | None = None
        self.pending = False
        self.last_activity = 0.0
        self.enabled = False
        self.generation = 0
        self._poller: _PhysicalPoller | None = None

    def start(self) -> None:
        if self.read is None and sys.platform == "win32":
            self._poller = _PhysicalPoller()
            self.read = self._poller.read
        self.enabled = self.read is not None
        if self.enabled:
            self.sample()

    def sample(self) -> bool:
        if not self.enabled or self.read is None:
            return False
        current = self.read()
        previous, self.previous = self.previous, current
        if previous is None:
            return False
        if self.target is None and not self.pending:
            return False
        moved = (current.x, current.y) != (previous.x, previous.y) and (
            current.move_serial is None or current.move_serial != previous.move_serial
        )
        target = self.target
        near = bool(
            target is not None
            and current.foreground == target.window_handle
            and moved
            and (current.x - (target.rectangle.left + target.rectangle.right) / 2) ** 2
            + (current.y - (target.rectangle.top + target.rectangle.bottom) / 2) ** 2
            < 80**2
        )
        # Focus changes alone include Jarvis bringing a window forward. They are
        # human intervention only when accompanied by physical input/proximity.
        active = (
            current.buttons
            or current.typing
            or near
            or current.mouse_serial != previous.mouse_serial
            or current.key_serial != previous.key_serial
        )
        if active:
            self.last_activity = self.clock()
            if not self.pending:
                self.pending = True
                self.generation += 1
                return True
        elif self.pending and moved:
            self.last_activity = self.clock()
        return False

    @property
    def quiet(self) -> bool:
        return self.pending and self.clock() - self.last_activity >= 2.0

    def acknowledge(self) -> None:
        self.pending = False

    def stop(self) -> None:
        self.enabled = False
        self.target = None
        if self._poller is not None:
            self._poller.close()
            self._poller = None
            self.read = None


def browser_viewport() -> dict[str, object]:
    """Real Chromium content HWND geometry; runs only in a killable helper.

    No screenX/outerHeight guesses: borders, browser chrome, zoom, negative
    monitor origins and mixed DPI are handled by the actual render widget.
    """
    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    handle = user32.GetForegroundWindow()
    name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, name, 256)
    if not name.value.startswith("Chrome_WidgetWin"):
        raise OSError("browser_window_changed")
    rectangles: list[Rectangle] = []

    def child(hwnd: int, unused: int) -> bool:
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value == "Chrome_RenderWidgetHostHWND" and user32.IsWindowVisible(hwnd):
            rect, point = wintypes.RECT(), wintypes.POINT()
            if (
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                and user32.ClientToScreen(hwnd, ctypes.byref(point))
                and rect.right > 0
                and rect.bottom > 0
            ):
                rectangles.append(
                    Rectangle(
                        left=point.x,
                        top=point.y,
                        right=point.x + rect.right,
                        bottom=point.y + rect.bottom,
                    )
                )
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(child)
    user32.EnumChildWindows.argtypes = [wintypes.HWND, type(callback), wintypes.LPARAM]
    user32.EnumChildWindows(handle, callback, 0)
    if not rectangles:
        raise OSError("browser_viewport_unavailable")
    rect = max(rectangles, key=lambda r: (r.right - r.left) * (r.bottom - r.top))
    return {"window_handle": int(handle), "rectangle": rect.model_dump()}


if __name__ == "__main__":
    if sys.platform != "win32" or sys.argv[1:] != ["--viewport"]:
        raise SystemExit(1)
    print(json.dumps(browser_viewport()))
