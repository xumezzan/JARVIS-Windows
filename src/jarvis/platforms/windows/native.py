"""Trusted Windows adapter. Only imported by the disposable native helper.

UIA selects semantic controls. Literal insertion uses a bounded message to that
exact native edit HWND, never global keys, clipboard, coordinates or shell text.
"""

import ctypes
import importlib
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    AppId,
    EditorTarget,
    NativeRequest,
    TypeText,
    WindowsResult,
    WindowTarget,
    text_digest,
)

EDIT_CLASSES = {"edit", "richedit20w", "richedit50w", "richeditd2dpt"}
APP_NAMES: tuple[AppId, ...] = ("notepad", "chrome", "vscode")


class NativeDesktop:
    def __init__(self) -> None:
        # These modules never load on macOS/Linux or in Simulation Mode.
        self.psutil = importlib.import_module("psutil")
        pywinauto = importlib.import_module("pywinauto")
        self.desktop = pywinauto.Desktop(backend="uia", allow_magic_lookup=False)
        self.user32 = importlib.import_module("ctypes").WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

    def paths(self, app: AppId) -> list[Path]:
        if app == "notepad":
            return [Path(os.environ["SYSTEMROOT"]) / "System32" / "notepad.exe"]
        roots = [
            Path(os.environ[name])
            for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if name in os.environ
        ]
        if app == "chrome":
            return [root / "Google/Chrome/Application/chrome.exe" for root in roots]
        return [root / "Microsoft VS Code/Code.exe" for root in roots] + [
            Path(os.environ["LOCALAPPDATA"]) / "Programs/Microsoft VS Code/Code.exe"
        ]

    def executable(self, app: AppId) -> Path:
        for path in self.paths(app):
            if path.is_file():
                return path
        raise ToolError("application_missing")

    def app_for(self, executable: str) -> AppId | None:
        path = Path(executable)
        for app in APP_NAMES:
            if any(
                os.path.normcase(str(candidate)) == os.path.normcase(executable)
                for candidate in self.paths(app)
            ):
                return app
        # Windows 11's packaged Notepad is launched through the System32 redirector.
        package_root = Path(os.environ["PROGRAMFILES"]) / "WindowsApps"
        try:
            relative = path.relative_to(package_root)
        except ValueError:
            return None
        if (
            len(relative.parts) == 3
            and relative.parts[0].startswith("Microsoft.WindowsNotepad_")
            and relative.parts[0].endswith("__8wekyb3d8bbwe")
            and [part.lower() for part in relative.parts[1:]] == ["notepad", "notepad.exe"]
        ):
            return "notepad"
        return None

    @staticmethod
    def runtime(control: Any) -> list[int]:
        return [int(value) for value in control.element_info.runtime_id]

    def selected_tabs(self, window: Any) -> list[list[int]]:
        return sorted(
            self.runtime(tab)
            for tab in window.descendants(control_type="TabItem")
            if tab.iface_selection_item.CurrentIsSelected
        )

    def editor(self, window: Any) -> Any:
        # A Find/Save dialog in the same process is not a document editor.
        if str(window.element_info.class_name).lower() != "notepad":
            raise ToolError("control_unsupported")
        candidates = [
            control
            for control in window.descendants()
            if control.element_info.control_type in ("Edit", "Document")
            and control.element_info.class_name.lower() in EDIT_CLASSES
            and control.is_visible()
            and control.is_enabled()
        ]
        if len(candidates) != 1:
            raise ToolError("control_unsupported")
        editor = candidates[0]
        # Require a real native edit control for directed insertion, including modern RichEdit.
        handle = int(editor.handle or 0)
        owner = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if (
            not handle
            or owner.value != window.process_id()
            or not self.user32.IsChild(window.handle, handle)
        ):
            raise ToolError("control_unsupported")
        return editor

    def read(self, editor: Any) -> str:
        try:
            value = str(editor.iface_text.DocumentRange.GetText(8001))
        except Exception:
            value = str(editor.iface_value.CurrentValue)
        return value.replace("\r\n", "\n")

    def editor_target(self, window: Any, editor: Any) -> EditorTarget:
        return EditorTarget(
            runtime_id=self.runtime(editor),
            control_type=cast("Any", editor.element_info.control_type),
            automation_id=str(editor.element_info.automation_id),
            class_name=str(editor.element_info.class_name),
            handle=int(editor.handle),
            selected_tabs=self.selected_tabs(window),
        )

    def snapshot(self, window: Any, include_editor: bool = True) -> WindowTarget:
        process = self.psutil.Process(window.process_id())
        executable = str(process.exe())
        app = self.app_for(executable)
        if app is None:
            raise ToolError("target_changed")
        editor_target = None
        empty = False
        if include_editor and app == "notepad":
            try:
                editor = self.editor(window)
                editor_target = self.editor_target(window, editor)
                empty = self.read(editor) == ""
            except Exception:
                # Unsupported editors remain focusable; they can never be typing targets.
                editor_target = None
        return WindowTarget(
            app=app,
            handle=int(window.handle),
            pid=int(process.pid),
            process_started=float(process.create_time()),
            executable=executable,
            runtime_id=self.runtime(window),
            title=str(window.window_text()),
            editor=editor_target,
            empty=empty,
        )

    def windows(self, app: AppId | None = None) -> list[WindowTarget]:
        result = []
        for window in self.desktop.windows(visible_only=True):
            try:
                snapshot = self.snapshot(window)
                if app is None or snapshot.app == app:
                    result.append(snapshot)
            except Exception:
                continue  # Unrelated, inaccessible or closing windows are not targets.
            if len(result) == 100:
                break
        return result

    def resolve(self, target: WindowTarget, check_title: bool = True) -> Any:
        try:
            window = self.desktop.window(handle=target.handle).wrapper_object()
            current = self.snapshot(window, include_editor=False)
            fields = ("app", "handle", "pid", "process_started", "executable", "runtime_id")
            if any(getattr(current, name) != getattr(target, name) for name in fields):
                raise ToolError("target_changed")
            if check_title and current.title != target.title:
                raise ToolError("target_changed")
            return window
        except Exception:
            raise ToolError("target_changed") from None

    def resolve_editor(self, target: WindowTarget, *, empty: bool) -> Any:
        window = self.resolve(target, check_title=empty)
        editor = self.editor(window)
        if self.editor_target(window, editor) != target.editor:
            raise ToolError("target_changed")
        if empty and self.read(editor) != "":
            raise ToolError("target_changed")
        return editor

    def dispatch(self, request: NativeRequest) -> WindowsResult:
        match request.operation:
            case "list":
                return WindowsResult(windows=self.windows(request.app))
            case "check_open":
                if request.app is None:
                    raise ToolError("native_failure")
                self.executable(request.app)
                return WindowsResult()
            case "open":
                if request.app is None:
                    raise ToolError("native_failure")
                existing = self.windows(request.app)
                if existing:
                    return WindowsResult(target=existing[0])
                # Fixed executable path only: no user args, URLs, PATH lookup or shell.
                subprocess.Popen(
                    [str(self.executable(request.app))],
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    windows = self.windows(request.app)
                    if windows:
                        return WindowsResult(target=windows[0])
                    time.sleep(0.1)
                raise ToolError("native_timeout")
            case "check_target":
                if request.target is None:
                    raise ToolError("native_failure")
                if request.text is not None:
                    TypeText(target=request.target, text=request.text)
                    self.resolve_editor(request.target, empty=True)
                else:
                    self.resolve(request.target)
                return WindowsResult(target=request.target)
            case "focus":
                if request.target is None:
                    raise ToolError("native_failure")
                window = self.resolve(request.target)
                self.user32.ShowWindowAsync(window.handle, 9)  # SW_RESTORE
                self.user32.SetForegroundWindow(window.handle)
                self.resolve(request.target)
                return WindowsResult(
                    target=request.target,
                    focused=self.user32.GetForegroundWindow() == request.target.handle,
                )
            case "type":
                if request.target is None or request.text is None:
                    raise ToolError("native_failure")
                args = TypeText(target=request.target, text=request.text)
                editor = self.resolve_editor(args.target, empty=True)
                buffer = ctypes.create_unicode_buffer(args.text)
                result = ctypes.c_size_t()
                # EM_REPLACESEL, undo enabled, SMTO_ABORTIFHUNG, 1s. No Enter/submit.
                # The application may finish an already issued message after helper termination.
                if not self.user32.SendMessageTimeoutW(
                    editor.handle,
                    0x00C2,
                    1,
                    ctypes.cast(buffer, ctypes.c_void_p),
                    0x0002,
                    1000,
                    ctypes.byref(result),
                ):
                    raise ToolError("native_failure")
                editor = self.resolve_editor(args.target, empty=False)
                return WindowsResult(target=args.target, text_sha256=text_digest(self.read(editor)))
            case "verify":
                if request.target is None or request.result is None:
                    raise ToolError("native_failure")
                if request.result.text_sha256 is not None:
                    editor = self.resolve_editor(request.target, empty=False)
                    return WindowsResult(
                        target=request.target, text_sha256=text_digest(self.read(editor))
                    )
                self.resolve(request.target)
                return WindowsResult(
                    target=request.target,
                    focused=self.user32.GetForegroundWindow() == request.target.handle,
                )
        raise ToolError("native_failure")
