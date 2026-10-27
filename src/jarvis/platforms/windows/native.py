"""Trusted Windows adapter. Only imported by the disposable native helper.

UIA selects semantic controls. Literal insertion uses a bounded message to that
exact observed editor. Cross-app input is separately bound to an approved window.
"""

import base64
import ctypes
import importlib
import io
import os
import string
import subprocess
import time
from collections import deque
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    MAX_ELEMENT_NAME,
    MAX_SCREENSHOT_JPEG_BYTES,
    MAX_UIA_DEPTH,
    MAX_UIA_ELEMENTS,
    MAX_UIA_OBSERVATION_BYTES,
    AppId,
    ClickElement,
    ClipboardSet,
    EditorTarget,
    ElementTarget,
    FocusApp,
    ListFields,
    NativeRequest,
    Rectangle,
    SendHotkey,
    TypeText,
    WindowsResult,
    WindowTarget,
    chrome_url,
    clipboard_digest,
    normalize_newlines,
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
KEY_CHARACTERS = set(string.ascii_lowercase + string.digits + " ._+-")

# Script hosts and interpreters are not launched by name: starting one would turn a spoken
# word into an arbitrary code execution surface, which no tool in this application exposes.
SCRIPT_HOSTS = {
    "cmd",
    "command prompt",
    "cscript",
    "developer command prompt",
    "developer powershell",
    "mshta",
    "powershell",
    "pwsh",
    "python",
    "pythonw",
    "regedit",
    "regsvr32",
    "rundll32",
    "windows powershell",
    "windows terminal",
    "wscript",
    "wt",
    "командная строка",
    "терминал",
}

# Spoken shorthands that do not appear as Start menu names on every installation.
ALIASES = {
    "калькулятор": "calc",
    "calculator": "calc",
    "блокнот": "notepad",
    "браузер": "chrome",
    "хром": "chrome",
    "гугл хром": "chrome",
    "вс код": "code",
    "vs code": "code",
    "vscode": "code",
    "код": "code",
    "проводник": "explorer",
    "paint": "mspaint",
    "пейнт": "mspaint",
}

# Always-present Windows applications, so a bare installation still answers common requests.
SYSTEM_APPS = ("notepad", "mspaint", "calc", "explorer", "charmap", "magnify")


def normalize(name: str) -> str:
    """Compare spoken words, file stems and window titles on the same footing."""
    folded = "".join(character if character.isalnum() else " " for character in name.casefold())
    return " ".join(folded.split())


def blocked(name: str) -> bool:
    """Match a script host as a whole word, so "Windows PowerShell (x86)" is caught too."""
    words = normalize(name)
    return any(
        words == host or words.startswith(f"{host} ") or f" {host} " in f" {words} "
        for host in SCRIPT_HOSTS
    )


def app_key(executable: str) -> str:
    """The stable identity of an observed window: its own executable stem."""
    stem = PureWindowsPath(executable).stem.lower()
    cleaned = "".join(
        character if character in KEY_CHARACTERS or "\u0400" <= character <= "\u04ff" else "-"
        for character in stem
    ).strip(" ._+-")
    return cleaned[:100] or "unknown"


@dataclass(frozen=True)
class LaunchEntry:
    """One launchable catalog item: a human name, what to start, and the expected key.

    A Store application has no executable a user may run directly; it is started by its
    application user model id through the system launcher instead.
    """

    name: str
    path: Path
    key: str
    aumid: str = ""


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

    def catalog(self) -> list[LaunchEntry]:
        """Applications this user can actually start: the Windows apps folder first, then
        the Start menu, registered App Paths and the always-present system tools."""
        cached = getattr(self, "_catalog", None)
        if cached is not None:
            return cast("list[LaunchEntry]", cached)
        winreg = importlib.import_module("winreg")
        entries: list[LaunchEntry] = []
        seen: set[str] = set()

        def add(name: str, path: Path, key: str, aumid: str = "") -> None:
            token = aumid.casefold() or os.path.normcase(str(path))
            if token in seen or blocked(name) or (not aumid and blocked(path.stem)):
                return
            seen.add(token)
            entries.append(LaunchEntry(name, path, key, aumid))

        for name, identifier in self.installed_apps():
            add(name, self.launcher, "", identifier)

        for variable in ("APPDATA", "PROGRAMDATA"):
            base = os.environ.get(variable)
            if base is None:
                continue
            programs = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if not programs.is_dir():
                continue
            try:
                links = sorted(programs.rglob("*.lnk"))[:2000]
            except OSError:
                continue
            for link in links:
                add(link.stem, link, "")

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(
                    root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
                ) as handle:
                    count = winreg.QueryInfoKey(handle)[0]
                    for index in range(min(count, 500)):
                        name = winreg.EnumKey(handle, index)
                        with winreg.OpenKey(handle, name) as item:
                            value = str(winreg.QueryValueEx(item, "")[0]).strip('"')
                        executable = Path(value)
                        if executable.is_file():
                            add(executable.stem, executable, app_key(str(executable)))
            except OSError:
                continue

        system32 = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32"
        for name in SYSTEM_APPS:
            executable = system32 / f"{name}.exe"
            if executable.is_file():
                add(name, executable, name)
        self._catalog = entries
        return entries

    @property
    def launcher(self) -> Path:
        return Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "explorer.exe"

    def installed_apps(self) -> list[tuple[str, str]]:
        """Names and ids from the Windows apps folder: desktop and Store apps alike.

        Read-only enumeration. The id is produced by Windows, never by a command, model
        or page, and it is the only thing later handed to the system launcher.
        """
        try:
            client = importlib.import_module("comtypes.client")
            # Late binding: pywinauto has already initialised COM without this type library.
            shell = client.CreateObject("Shell.Application", dynamic=True)
            items = shell.NameSpace("shell:AppsFolder").Items()
            total = min(int(items.Count), 1000)
        except Exception:
            return []
        found: list[tuple[str, str]] = []
        for index in range(total):
            try:
                item = items.Item(index)
                name, identifier = str(item.Name), str(item.Path)
            except Exception:
                continue
            # Desktop entries repeat the Start menu shortcut; only ids are new information.
            if name and identifier and "!" in identifier and not Path(identifier).exists():
                found.append((name, identifier))
        return found

    def resolve_app(self, app: AppId) -> LaunchEntry:
        """One spoken name to one launchable item, or an explicit finite failure."""
        entries = self.catalog()
        for wanted in [normalize(app), normalize(ALIASES.get(normalize(app), ""))]:
            if not wanted:
                continue
            if wanted in SCRIPT_HOSTS:
                raise ToolError("application_missing")
            for select in (
                lambda entry, want=wanted: normalize(entry.name) == want or entry.key == want,
                lambda entry, want=wanted: normalize(entry.name).startswith(want),
                lambda entry, want=wanted: want in normalize(entry.name),
            ):
                matches = [entry for entry in entries if select(entry)]
                if not matches:
                    continue
                if len({normalize(entry.name) for entry in matches}) > 1:
                    raise ToolError("application_ambiguous")
                return matches[0]
        raise ToolError("application_missing")

    def candidates(self, app: AppId, entry: LaunchEntry, titles: bool) -> list[WindowTarget]:
        wanted = normalize(app)
        alias = normalize(ALIASES.get(wanted, ""))
        found = self.windows(include_editor=False)
        for select in (
            lambda item: bool(entry.key) and item.app == entry.key,
            lambda item: normalize(item.app) in {wanted, alias} - {""},
            lambda item: titles and wanted in normalize(item.title),
        ):
            matches = [item for item in found if select(item)]
            if matches:
                return matches
        return []

    @staticmethod
    def chrome_executable() -> Path:
        """Resolve Chrome from fixed Windows install metadata, never user arguments."""
        winreg = importlib.import_module("winreg")
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(
                    root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                ) as handle:
                    candidate = Path(str(winreg.QueryValueEx(handle, "")[0]).strip('"'))
                if (
                    candidate.is_absolute()
                    and candidate.name.lower() == "chrome.exe"
                    and candidate.is_file()
                ):
                    return candidate
            except OSError:
                continue
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if candidate.is_absolute() and candidate.is_file():
                    return candidate
        raise ToolError("application_missing")

    def open_chrome_url(self, value: str) -> WindowsResult:
        url = chrome_url(value)
        if url != value:
            raise ToolError("network_denied")
        subprocess.Popen(
            [str(self.chrome_executable()), "--new-tab", url],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return WindowsResult(performed=True, text=url)

    def launch(self, entry: LaunchEntry) -> None:
        """Start the resolved item itself. No user arguments, URLs, PATH lookup or shell."""
        if entry.aumid:
            subprocess.Popen(
                [str(self.launcher), "shell:AppsFolder\\" + entry.aumid],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        if entry.path.suffix.lower() == ".lnk":
            importlib.import_module("os").startfile(str(entry.path))
            return
        subprocess.Popen(
            [str(entry.path)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def runtime(control: Any) -> list[int]:
        return [int(value) for value in control.element_info.runtime_id]

    def walk(self, window: Any, max_depth: int = MAX_UIA_DEPTH) -> tuple[list[Any], bool]:
        # Electron puts actions behind unnamed Pane/Group wrappers. Traverse those
        # without reading their names or rejecting empty container rectangles.
        # element_snapshot still filters invisible, disabled and password controls;
        # the raw-node cap and killable worker bound wide or broken providers.
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

    def writable(self, control: Any) -> bool:
        """A password box or a read-only view is never a typing target, whatever was asked.

        Anything that cannot answer these questions is refused rather than guessed at.
        """
        try:
            if bool(control.element_info.element.CurrentIsPassword):
                return False
            return not bool(control.iface_value.CurrentIsReadOnly)
        except Exception:
            return False

    def controls(self, window: Any) -> list[Any]:
        """Visible, writable text fields of one window; bounded and asked for by type."""
        found: list[Any] = []
        for kind in ("Edit", "Document"):
            try:
                found.extend(window.descendants(control_type=kind))
            except Exception:
                continue
            if len(found) >= 40:
                break
        usable: list[Any] = []
        for control in found[:40]:
            try:
                if control.is_visible() and control.is_enabled() and self.writable(control):
                    usable.append(control)
            except Exception:
                continue
            if len(usable) == 20:
                break
        return usable

    def native_handle(self, window: Any, control: Any) -> int:
        """The control's own window, only when it really belongs to this window's process."""
        handle = int(control.handle or 0)
        if not handle:
            return 0
        owner = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value != window.process_id() or not self.user32.IsChild(window.handle, handle):
            raise ToolError("control_unsupported")
        return handle

    def editor(self, window: Any) -> Any:
        """The one unambiguous field of a window; several fields must be chosen explicitly."""
        candidates = self.controls(window)
        if len(candidates) != 1:
            raise ToolError("control_unsupported")
        return candidates[0]

    def chosen_editor(self, window: Any, wanted: EditorTarget) -> Any:
        """The one field of this window that still matches; ambiguity is refused."""
        matches = []
        for control in self.controls(window):
            try:
                if self.editor_target(window, control).identity == wanted.identity:
                    matches.append(control)
            except Exception:
                continue
        if len(matches) != 1:
            raise ToolError("target_changed")
        return matches[0]

    def read(self, editor: Any) -> str:
        try:
            value = str(editor.iface_text.DocumentRange.GetText(8001))
        except Exception:
            value = str(editor.iface_value.CurrentValue)
        if len(value) > 8000:
            raise ToolError("control_unsupported")
        return normalize_newlines(value)

    def editor_target(self, window: Any, editor: Any) -> EditorTarget:
        return EditorTarget(
            runtime_id=self.runtime(editor),
            control_type=cast("Any", editor.element_info.control_type),
            automation_id=str(editor.element_info.automation_id),
            class_name=str(editor.element_info.class_name),
            handle=int(editor.handle or 0),
            content_sha256=text_digest(self.read(editor)),
            name=str(editor.element_info.name or "")[:200],
            selected_tabs=self.selected_tabs(window),
        )

    def snapshot(self, window: Any, include_editor: bool = True) -> WindowTarget:
        process = self.psutil.Process(window.process_id())
        executable = str(process.exe())
        app = app_key(executable)
        editor_target = None
        empty = False
        if include_editor:
            try:
                editor = self.editor(window)
                editor_target = self.editor_target(window, editor)
                empty = self.read(editor) == ""
            except Exception:
                # A window with no single obvious field still focuses; its fields are listed
                # explicitly instead of guessed at.
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

    def with_editor(self, target: WindowTarget) -> WindowTarget:
        """Inspect only the selected window, after rechecking its observed identity."""
        return self.snapshot(self.resolve(target))

    def windows(
        self, app: AppId | None = None, *, include_editor: bool = True
    ) -> list[WindowTarget]:
        result = []
        for window in self.desktop.windows(visible_only=True):
            try:
                if (
                    not str(window.window_text()).strip()
                    or window.element_info.class_name in SYSTEM_WINDOW_CLASSES
                ):
                    continue
                # Recursive editor inspection can block in an unrelated application.
                # Select by top-level identity first; opening uses these shallow scans.
                snapshot = self.snapshot(window, include_editor=False)
                if app is not None and snapshot.app != app:
                    continue
                result.append(self.with_editor(snapshot) if include_editor else snapshot)
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

    def message(self, handle: int, code: int, first: int, second: ctypes.c_void_p) -> None:
        result = ctypes.c_size_t()
        # SMTO_ABORTIFHUNG, 1s. No Enter/submit key is ever sent.
        # The application may finish an already issued message after helper termination.
        if not self.user32.SendMessageTimeoutW(
            handle, code, first, second, 0x0002, 1000, ctypes.byref(result)
        ):
            raise ToolError("native_failure")

    def write(self, window: Any, editor: Any, value: str, overwrite: bool) -> None:
        """One directed write. Never global keys, clipboard, coordinates or a submit key."""
        handle = self.native_handle(window, editor)
        if handle:
            if overwrite:
                # EM_SETSEL over the whole field, so replacing really replaces. Selecting
                # is a message to this exact control, not a key press anyone else can see.
                self.message(handle, 0x00B1, 0, ctypes.c_void_p(-1))
            buffer = ctypes.create_unicode_buffer(value)
            # EM_REPLACESEL with undo enabled.
            self.message(handle, 0x00C2, 1, ctypes.cast(buffer, ctypes.c_void_p))
            return
        # A field with no window of its own, as in browser and Electron user interfaces:
        # the value pattern is the application's own supported way to set the text.
        try:
            editor.iface_value.SetValue(value)
        except Exception:
            raise ToolError("control_unsupported") from None

    def resolve_editor(self, args: TypeText) -> tuple[Any, Any]:
        """Re-observe the exact window and field, and refuse to destroy unseen content."""
        window = self.resolve(args.target, check_title=False)
        editor = self.chosen_editor(window, args.field)
        if not self.writable(editor):
            raise ToolError("control_unsupported")
        if (
            args.field.content_sha256 is not None
            and text_digest(self.read(editor)) != args.field.content_sha256
        ):
            raise ToolError("target_changed")
        if not args.overwrite and self.read(editor) != "":
            raise ToolError("target_changed")
        return window, editor

    @staticmethod
    def typing(request: NativeRequest) -> TypeText:
        """Re-validate the typing request inside the helper, never trusting the caller."""
        if request.target is None:
            raise ToolError("native_failure")
        try:
            return TypeText(
                target=request.target,
                editor=request.editor,
                text=request.text or "",
                overwrite=request.overwrite,
            )
        except Exception:
            raise ToolError("native_failure") from None

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
        if request.operation.startswith("calculator_"):
            from jarvis.platforms.windows.calculator import dispatch

            return dispatch(self, request)
        match request.operation:
            case "check_chrome":
                self.chrome_executable()
                return WindowsResult()
            case "open_chrome_url":
                if request.url is None:
                    raise ToolError("network_denied")
                return self.open_chrome_url(request.url)
            case "list":
                windows = self.windows(request.app)
                return WindowsResult(windows=windows, truncated=len(windows) == 1000)
            case "check_open":
                if request.app is None:
                    raise ToolError("native_failure")
                self.resolve_app(request.app)
                return WindowsResult()
            case "open":
                if request.app is None:
                    raise ToolError("native_failure")
                entry = self.resolve_app(request.app)
                existing = self.candidates(request.app, entry, titles=False)
                if existing:
                    return WindowsResult(target=self.with_editor(existing[0]))
                known = {item.handle for item in self.windows(include_editor=False)}
                opened = time.time() - 1
                self.launch(entry)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    fresh = [
                        item
                        for item in self.windows(include_editor=False)
                        if item.handle not in known and item.process_started >= opened
                    ]
                    matched = [item for item in fresh if entry.key and item.app == entry.key]
                    if matched or fresh:
                        return WindowsResult(target=self.with_editor((matched or fresh)[0]))
                    time.sleep(0.2)
                # A running instance may simply have taken focus without a new window.
                existing = self.candidates(request.app, entry, titles=True)
                if existing:
                    return WindowsResult(target=self.with_editor(existing[0]))
                raise ToolError("native_timeout")
            case "check_target":
                if request.target is None:
                    raise ToolError("native_failure")
                if request.text is not None:
                    self.resolve_editor(self.typing(request))
                else:
                    self.resolve(request.target)
                return WindowsResult(target=request.target)
            case "fields":
                if request.target is None:
                    raise ToolError("native_failure")
                window = self.resolve(request.target, check_title=False)
                ListFields(target=request.target)
                return WindowsResult(
                    target=request.target,
                    editors=[
                        self.editor_target(window, control) for control in self.controls(window)
                    ],
                )
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
                args = self.typing(request)
                window, editor = self.resolve_editor(args)
                self.write(window, editor, args.text, args.overwrite)
                editor = self.chosen_editor(
                    self.resolve(args.target, check_title=False), args.field
                )
                return WindowsResult(target=args.target, text_sha256=text_digest(self.read(editor)))
            case "verify":
                if request.target is None or request.result is None:
                    raise ToolError("native_failure")
                if request.result.text_sha256 is not None:
                    # A read-back names the same field; it carries no text of its own.
                    wanted = request.editor or request.target.editor
                    if wanted is None:
                        raise ToolError("native_failure")
                    window = self.resolve(request.target, check_title=False)
                    editor = self.chosen_editor(window, wanted)
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
            observation = self._inspection(window, target, MAX_UIA_DEPTH)
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
