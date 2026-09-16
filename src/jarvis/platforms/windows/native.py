"""Trusted Windows adapter. Only imported by the disposable native helper.

UIA selects semantic controls. Literal insertion uses a bounded message to that
exact native edit HWND, never global keys, clipboard, coordinates or shell text.
"""

import ctypes
import importlib
import os
import string
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    AppId,
    EditorTarget,
    ListFields,
    NativeRequest,
    TypeText,
    WindowsResult,
    WindowTarget,
    text_digest,
)

EDIT_CLASSES = {"edit", "richedit20w", "richedit50w", "richeditd2dpt"}
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
    stem = Path(executable).stem.lower()
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
        found = self.windows()
        for select in (
            lambda item: bool(entry.key) and item.app == entry.key,
            lambda item: normalize(item.app) in {wanted, alias} - {""},
            lambda item: titles and wanted in normalize(item.title),
        ):
            matches = [item for item in found if select(item)]
            if matches:
                return matches
        return []

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
            os.startfile(str(entry.path))  # noqa: S606 - resolved shortcut, no parsed command line
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

    def selected_tabs(self, window: Any) -> list[list[int]]:
        return sorted(
            self.runtime(tab)
            for tab in window.descendants(control_type="TabItem")
            if tab.iface_selection_item.CurrentIsSelected
        )

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
        return value.replace("\r\n", "\n")

    def editor_target(self, window: Any, editor: Any) -> EditorTarget:
        return EditorTarget(
            runtime_id=self.runtime(editor),
            control_type=cast("Any", editor.element_info.control_type),
            automation_id=str(editor.element_info.automation_id),
            class_name=str(editor.element_info.class_name),
            handle=int(editor.handle or 0),
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
        match request.operation:
            case "list":
                return WindowsResult(windows=self.windows(request.app))
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
                    return WindowsResult(target=existing[0])
                known = {item.handle for item in self.windows()}
                opened = time.time() - 1
                self.launch(entry)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    fresh = [
                        item
                        for item in self.windows()
                        if item.handle not in known and item.process_started >= opened
                    ]
                    matched = [item for item in fresh if entry.key and item.app == entry.key]
                    if matched or fresh:
                        return WindowsResult(target=(matched or fresh)[0])
                    time.sleep(0.2)
                # A running instance may simply have taken focus without a new window.
                existing = self.candidates(request.app, entry, titles=True)
                if existing:
                    return WindowsResult(target=existing[0])
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
