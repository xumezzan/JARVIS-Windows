"""Trusted Windows adapter. Only imported by the disposable native helper.

UIA selects semantic controls. Literal insertion uses a bounded message to that
exact observed editor. Cross-app input is separately bound to an approved window.
"""

import base64
import ctypes
import importlib
import io
import os
import subprocess
import time
from collections import deque
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    MAX_ELEMENT_NAME,
    MAX_SCREENSHOT_JPEG_BYTES,
    MAX_UIA_ELEMENTS,
    MAX_UIA_OBSERVATION_BYTES,
    AppId,
    ClickElement,
    ClipboardSet,
    EditorTarget,
    ElementTarget,
    FocusApp,
    NativeRequest,
    Rectangle,
    SendHotkey,
    TypeText,
    WindowsResult,
    WindowTarget,
    clipboard_digest,
    text_digest,
    uia_metadata_bytes,
)

EDIT_CLASSES = {"edit", "richedit20w", "richedit50w", "richeditd2dpt"}
APP_NAMES: tuple[AppId, ...] = ("notepad", "chrome", "vscode")
SYSTEM_WINDOW_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "tooltips_class32",
}
HOTKEYS = {
    "Ctrl+C": "^c",
    "Ctrl+V": "^v",
    "Ctrl+A": "^a",
    "Enter": "{ENTER}",
    "Tab": "{TAB}",
    "Alt+F4": "%{F4}",
}


class NativeDesktop:
    before_click: Callable[[Rectangle], None] | None = None

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
        if app not in APP_NAMES:
            raise ToolError("application_missing")
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

    def walk(self, window: Any, max_depth: int = 5) -> tuple[list[Any], bool]:
        pending = deque([(window, 0)])
        controls: list[Any] = []
        truncated = False
        while pending and len(controls) < 2048:
            parent, depth = pending.popleft()
            if depth >= max_depth:
                continue
            try:
                children = parent.children()
            except Exception:
                continue
            for child in children:
                if len(controls) >= 2048:
                    truncated = True
                    break
                controls.append(child)
                pending.append((child, depth + 1))
        return controls, truncated or bool(pending)

    def selected_tabs(self, window: Any) -> list[list[int]]:
        controls, truncated = self.walk(window)
        tabs = sorted(
            self.runtime(tab)
            for tab in controls
            if tab.element_info.control_type == "TabItem"
            and tab.iface_selection_item.CurrentIsSelected
        )
        if truncated or len(tabs) > 32:
            raise ToolError("control_unsupported")
        return tabs

    @staticmethod
    def password(control: Any) -> bool:
        # Fail closed if a provider cannot supply the password flag.
        try:
            return bool(control.element_info.element.CurrentIsPassword)
        except Exception:
            return True

    def editor(self, window: Any) -> Any:
        controls, truncated = self.walk(window)
        candidates = []
        for control in controls:
            if (
                control.element_info.control_type not in ("Edit", "Document")
                or not control.is_visible()
                or not control.is_enabled()
                or self.password(control)
            ):
                continue
            if control.element_info.class_name.lower() not in EDIT_CLASSES:
                try:
                    if control.iface_value.CurrentIsReadOnly:
                        continue
                except Exception:
                    continue
            candidates.append(control)
        if truncated or len(candidates) != 1:
            raise ToolError("control_unsupported")
        editor = candidates[0]
        handle = int(editor.handle or 0)
        if not handle and editor.element_info.class_name.lower() in EDIT_CLASSES:
            raise ToolError("control_unsupported")
        if handle:
            owner = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value != window.process_id() or not self.user32.IsChild(window.handle, handle):
                raise ToolError("control_unsupported")
        return editor

    def read(self, editor: Any) -> str:
        try:
            value = str(editor.iface_text.DocumentRange.GetText(8001))
        except Exception:
            value = str(editor.iface_value.CurrentValue)
        if len(value) > 8000:
            raise ToolError("control_unsupported")
        return value.replace("\r\n", "\n")

    def editor_target(self, window: Any, editor: Any) -> EditorTarget:
        return EditorTarget(
            runtime_id=self.runtime(editor),
            control_type=cast("Any", editor.element_info.control_type),
            automation_id=str(editor.element_info.automation_id),
            class_name=str(editor.element_info.class_name),
            handle=int(editor.handle or 0),
            content_sha256=text_digest(self.read(editor)),
            selected_tabs=self.selected_tabs(window),
        )

    def snapshot(self, window: Any, include_editor: bool = True) -> WindowTarget:
        process = self.psutil.Process(window.process_id())
        executable = str(process.exe())
        app = self.app_for(executable)
        if app is None:
            app = Path(executable).stem.lower()
        editor_target = None
        empty = False
        if include_editor:
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
                if (
                    not str(window.window_text()).strip()
                    or window.element_info.class_name in SYSTEM_WINDOW_CLASSES
                ):
                    continue
                # Enumerate arbitrary apps without scanning every editor tree.
                snapshot = self.snapshot(window, include_editor=False)
                if snapshot.app == "notepad":
                    snapshot = self.snapshot(window)
                if app is None or snapshot.app == app:
                    result.append(snapshot)
            except Exception:
                continue  # Unrelated, inaccessible or closing windows are not targets.
            if len(result) == 1000:
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
        current = self.editor_target(window, editor)
        expected = target.editor
        if expected is None:
            raise ToolError("target_changed")
        # Older empty-Notepad snapshots did not contain a content digest.
        if expected.content_sha256 is None:
            current = current.model_copy(update={"content_sha256": None})
            if not target.empty:
                raise ToolError("target_changed")
        elif not empty:
            current = current.model_copy(update={"content_sha256": expected.content_sha256})
        if current != expected:
            raise ToolError("target_changed")
        if empty and target.empty and self.read(editor) != "":
            raise ToolError("target_changed")
        return editor

    def dispatch(self, request: NativeRequest) -> WindowsResult:
        if request.operation in {
            "inspect",
            "clipboard_get",
            "clipboard_set",
            "hotkey",
            "click",
            "screenshot",
        }:
            return self.cross_app(request)
        match request.operation:
            case "list":
                windows = self.windows(request.app)
                return WindowsResult(windows=windows, truncated=len(windows) == 1000)
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
                selector = FocusApp(
                    target=request.target, handle=request.handle, title=request.title
                )
                target = selector.target or self.select_window(selector)
                self.activate(target)
                return WindowsResult(target=target, focused=True)
            case "type":
                if request.target is None or request.text is None:
                    raise ToolError("native_failure")
                args = TypeText(target=request.target, text=request.text)
                editor = self.resolve_editor(args.target, empty=True)
                if editor.element_info.class_name.lower() not in EDIT_CLASSES:
                    editor.iface_value.SetValue(args.text)
                    editor = self.resolve_editor(args.target, empty=False)
                    return WindowsResult(
                        target=args.target, text_sha256=text_digest(self.read(editor))
                    )
                buffer = ctypes.create_unicode_buffer(args.text)
                result = ctypes.c_size_t()
                # Empty native editor: EM_REPLACESEL with undo. Nonempty: WM_SETTEXT.
                # SMTO_ABORTIFHUNG, 1s. No Enter/submit.
                # The application may finish an already issued message after helper termination.
                if not self.user32.SendMessageTimeoutW(
                    editor.handle,
                    0x00C2 if args.target.empty else 0x000C,
                    1 if args.target.empty else 0,
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

    def select_window(self, selector: FocusApp) -> WindowTarget:
        if selector.handle is not None:
            window = self.desktop.window(handle=selector.handle).wrapper_object()
            if (
                not window.is_visible()
                or not str(window.window_text()).strip()
                or window.element_info.class_name in SYSTEM_WINDOW_CLASSES
            ):
                raise ToolError("target_changed")
            return self.snapshot(window)
        matches = [
            target
            for target in self.windows()
            if selector.title is not None and selector.title.casefold() in target.title.casefold()
        ]
        if len(matches) != 1:
            raise ToolError("target_changed")
        return matches[0]

    def active(self) -> tuple[Any, WindowTarget]:
        handle = int(self.user32.GetForegroundWindow() or 0)
        if not handle:
            raise ToolError("target_changed")
        window = self.desktop.window(handle=handle).wrapper_object()
        return window, self.snapshot(window)

    def activate(self, target: WindowTarget) -> Any:
        window = self.resolve(target)
        if int(self.user32.GetForegroundWindow() or 0) != target.handle:
            self.user32.ShowWindowAsync(window.handle, 9)
            self.user32.SetForegroundWindow(window.handle)
        self.require_foreground(target)
        return window

    def require_foreground(self, target: WindowTarget) -> None:
        self.resolve(target)
        if int(self.user32.GetForegroundWindow() or 0) != target.handle:
            raise ToolError("target_changed")

    @staticmethod
    def rectangle(control: Any) -> Rectangle:
        rect = control.rectangle()
        return Rectangle(
            left=int(rect.left), top=int(rect.top), right=int(rect.right), bottom=int(rect.bottom)
        )

    def element_snapshot(self, control: Any, *, mark_id: int = 1) -> ElementTarget:
        if not control.is_visible() or not control.is_enabled() or self.password(control):
            raise ToolError("control_unsupported")
        name = str(control.window_text())
        return ElementTarget(
            mark_id=mark_id,
            runtime_id=self.runtime(control),
            name=name[:MAX_ELEMENT_NAME],
            label_truncated=len(name) > MAX_ELEMENT_NAME,
            name_sha256=clipboard_digest(name) if len(name) > MAX_ELEMENT_NAME else None,
            control_type=control.element_info.control_type,
            automation_id=str(control.element_info.automation_id),
            rectangle=self.rectangle(control),
        )

    def inspect(self, max_depth: int) -> WindowsResult:
        window, target = self.active()
        return self._inspection(window, target, max_depth)

    def _inspection(self, window: Any, target: WindowTarget, max_depth: int) -> WindowsResult:
        candidates: list[ElementTarget] = []
        bounds = self.rectangle(window)
        controls, truncated = self.walk(window, max_depth)
        for control in controls:
            if control.element_info.control_type not in {
                "Button",
                "Edit",
                "Document",
                "TabItem",
                "ListItem",
            }:
                continue
            try:
                element = self.element_snapshot(control)
            except Exception:
                continue
            rect = element.rectangle
            if (
                rect.right > bounds.left
                and rect.left < bounds.right
                and rect.bottom > bounds.top
                and rect.top < bounds.bottom
            ):
                candidates.append(element)
        # Prefer editors/actions/tabs to repeated document/list contents, then reading order.
        priority = {"Edit": 0, "Button": 1, "TabItem": 2, "Document": 3, "ListItem": 4}
        candidates.sort(
            key=lambda e: (
                not bool(e.name.strip()),
                priority[e.control_type],
                e.rectangle.top,
                e.rectangle.left,
            )
        )
        result = WindowsResult(target=target, window_rectangle=bounds, truncated=truncated)
        if uia_metadata_bytes(result) > MAX_UIA_OBSERVATION_BYTES:
            raise ToolError("control_unsupported")
        elements: list[ElementTarget] = []
        for element in candidates:
            if len(elements) == MAX_UIA_ELEMENTS:
                truncated = True
                break
            marked = element.model_copy(update={"mark_id": len(elements) + 1})
            candidate = result.model_copy(update={"elements": [*elements, marked]})
            if uia_metadata_bytes(candidate) > MAX_UIA_OBSERVATION_BYTES:
                truncated = True
                continue
            elements.append(marked)
        self.require_foreground(target)
        if self.rectangle(window) != bounds:
            raise ToolError("target_changed")
        return result.model_copy(update={"elements": elements, "truncated": truncated})

    def clipboard(self, text: str | None = None, *, plain_only: bool = False) -> str:
        clipboard = importlib.import_module("win32clipboard")
        gui = importlib.import_module("win32gui")
        # A real helper-owned HWND is required for EmptyClipboard/SetClipboardData ownership.
        owner = gui.CreateWindowEx(0, "Static", "", 0, 0, 0, 0, 0, -3, 0, 0, None)
        opened = False
        try:
            clipboard.OpenClipboard(owner)
            opened = True
            if text is not None:
                ClipboardSet(text=text)
                clipboard.EmptyClipboard()
                clipboard.SetClipboardText(text, 13)  # CF_UNICODETEXT, immediate OS-owned data
            if plain_only:
                current_format = clipboard.EnumClipboardFormats(0)
                while current_format:
                    if current_format not in {1, 7, 13, 16}:  # Windows text/locale formats only
                        raise ToolError("control_unsupported")
                    current_format = clipboard.EnumClipboardFormats(current_format)
            if not clipboard.IsClipboardFormatAvailable(13):
                raise ToolError("control_unsupported")
            data_handle = clipboard.GetClipboardDataHandle(13)
            kernel32 = importlib.import_module("ctypes").WinDLL("kernel32", use_last_error=True)
            kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
            kernel32.GlobalSize.restype = ctypes.c_size_t
            size = kernel32.GlobalSize(data_handle)
            if not 0 < size <= 16002:  # 4000 Unicode scalars plus a UTF-16 terminator
                raise ToolError("control_unsupported")
            value = clipboard.GetClipboardData(13)
            if not isinstance(value, str) or len(value) > 4000:
                raise ToolError("control_unsupported")
            ClipboardSet(text=value)
            return value
        finally:
            if opened:
                clipboard.CloseClipboard()
            gui.DestroyWindow(owner)

    def capture(self, scope: str) -> WindowsResult:
        grab = importlib.import_module("PIL.ImageGrab")
        observation = WindowsResult()
        if scope == "active_window":
            window, target = self.active()
            observation = self._inspection(window, target, 5)
            rect = observation.window_rectangle
            assert rect is not None
            image = grab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
            self.require_foreground(target)
            if self.rectangle(window) != rect:
                raise ToolError("target_changed")
        else:
            image = grab.grab(all_screens=True)
        image = image.convert("RGB")
        image.thumbnail((1280, 800))
        # Quality alone cannot bound noisy/high-detail screens. Encode the same capture
        # at smaller dimensions until its JPEG fits; never retry the OS capture.
        while True:
            output = io.BytesIO()
            marked = self._mark_image(image, observation)
            marked.save(output, format="JPEG", quality=65)
            if output.tell() <= MAX_SCREENSHOT_JPEG_BYTES:
                break
            if image.width == 1 and image.height == 1:
                raise ToolError("native_failure")
            image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)))
        return observation.model_copy(
            update={
                "image_base64": base64.b64encode(output.getvalue()).decode(),
                "image_format": "JPEG",
                "width": image.width,
                "height": image.height,
            }
        )

    @staticmethod
    def _mark_image(image: Any, observation: WindowsResult) -> Any:
        bounds = observation.window_rectangle
        if bounds is None or not observation.elements:
            return image
        marked = image.copy()
        draw = importlib.import_module("PIL.ImageDraw").Draw(marked)
        sx = image.width / (bounds.right - bounds.left)
        sy = image.height / (bounds.bottom - bounds.top)
        for element in observation.elements:
            rect = element.rectangle
            left = max(0, min(image.width - 1, round((rect.left - bounds.left) * sx)))
            top = max(0, min(image.height - 1, round((rect.top - bounds.top) * sy)))
            right = max(left, min(image.width - 1, round((rect.right - bounds.left) * sx)))
            bottom = max(top, min(image.height - 1, round((rect.bottom - bounds.top) * sy)))
            draw.rectangle((left, top, right, bottom), outline="black", width=3)
            draw.rectangle((left, top, right, bottom), outline="#ffe600", width=1)
            text = f"[{element.mark_id}]"
            box = draw.textbbox((0, 0), text)
            width, height = box[2] - box[0] + 6, box[3] - box[1] + 4
            x, y = max(0, min(left, image.width - width)), max(0, min(top, image.height - height))
            draw.rectangle((x, y, x + width, y + height), fill="#ffe600", outline="black")
            draw.text((x + 3 - box[0], y + 2 - box[1]), text, fill="black")
        return marked

    def cross_app(self, request: NativeRequest) -> WindowsResult:
        if request.operation == "inspect":
            return self.inspect(request.max_depth)
        if request.operation in {"clipboard_get", "clipboard_set"}:
            if request.operation == "clipboard_set" and request.text is None:
                raise ToolError("native_failure")
            text = self.clipboard(request.text if request.operation == "clipboard_set" else None)
            return WindowsResult(text=text, clipboard_sha256=clipboard_digest(text))
        if request.operation == "screenshot":
            return self.capture(request.scope)
        if request.target is None:
            raise ToolError("native_failure")
        if request.operation == "hotkey":
            args = SendHotkey(
                target=request.target,
                hotkey=cast("Any", request.hotkey),
                clipboard_sha256=request.clipboard_sha256,
            )
            self.activate(args.target)
            if (
                args.hotkey == "Ctrl+V"
                and clipboard_digest(self.clipboard(plain_only=True)) != args.clipboard_sha256
            ):
                raise ToolError("target_changed")
            self.require_foreground(args.target)
            importlib.import_module("pywinauto.keyboard").send_keys(HOTKEYS[args.hotkey], pause=0)
            # Dispatch is observable; arbitrary application effects need a fresh observation.
            return WindowsResult(target=args.target, performed=True)
        click_args = ClickElement(
            target=request.target,
            element=request.element,
            x=request.x,
            y=request.y,
            window_rectangle=request.window_rectangle,
        )
        window = self.activate(click_args.target)
        if click_args.element is not None:
            controls, _ = self.walk(window)
            matches = []
            for control in controls:
                try:
                    if (
                        self.element_snapshot(control, mark_id=click_args.element.mark_id)
                        == click_args.element
                    ):
                        matches.append(control)
                except Exception:
                    continue
            if len(matches) != 1:
                raise ToolError("target_changed")
            control = matches[0]
            self.require_foreground(click_args.target)
            if self.before_click is not None:
                self.before_click(click_args.element.rectangle)
                self.require_foreground(click_args.target)
                if (
                    self.element_snapshot(control, mark_id=click_args.element.mark_id)
                    != click_args.element
                ):
                    raise ToolError("target_changed")
            # Only fallback for absence of InvokePattern, never after a possibly issued invocation.
            try:
                invoke = control.iface_invoke
            except importlib.import_module("pywinauto.uia_defines").NoPatternInterfaceError:
                rect = self.rectangle(control)
                self.check_point(
                    click_args.target, (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
                )
                self.require_foreground(click_args.target)
                control.click_input()
            else:
                invoke.Invoke()
        else:
            rect = self.rectangle(window)
            if (
                rect != click_args.window_rectangle
                or click_args.x is None
                or click_args.y is None
                or not rect.left <= click_args.x < rect.right
                or not rect.top <= click_args.y < rect.bottom
            ):
                raise ToolError("target_changed")
            self.check_point(click_args.target, click_args.x, click_args.y)
            if self.before_click is not None:
                self.before_click(
                    Rectangle(
                        left=click_args.x - 12,
                        top=click_args.y - 12,
                        right=click_args.x + 12,
                        bottom=click_args.y + 12,
                    )
                )
                if self.rectangle(window) != rect:
                    raise ToolError("target_changed")
                self.check_point(click_args.target, click_args.x, click_args.y)
            self.require_foreground(click_args.target)
            importlib.import_module("pywinauto.mouse").click(coords=(click_args.x, click_args.y))
        return WindowsResult(target=click_args.target, performed=True)

    def check_point(self, target: WindowTarget, x: int, y: int) -> None:
        self.user32.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user32.WindowFromPoint.restype = wintypes.HWND
        hit = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hit or (hit != target.handle and not self.user32.IsChild(target.handle, hit)):
            raise ToolError("target_changed")
