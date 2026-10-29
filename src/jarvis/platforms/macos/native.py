"""Trusted macOS adapter. Loaded only inside the killable fixed-protocol helper.

Window and AX rectangles use CoreGraphics logical screen points. Captures are mapped
from their actual pixel size to those bounds, so Retina and secondary displays do not
require a guessed global scale. Field values never leave this process, only digests.
"""

import base64
import importlib
import io
import json
import math
import os
import plistlib
import selectors
import subprocess
import tempfile
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jarvis.observability.diagnostics import Reason
from jarvis.platforms.windows.ghost import CursorTarget
from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    MAX_ELEMENT_NAME,
    MAX_SCREENSHOT_JPEG_BYTES,
    MAX_UIA_DEPTH,
    MAX_UIA_ELEMENTS,
    MAX_UIA_OBSERVATION_BYTES,
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
    chrome_url,
    clipboard_digest,
    normalize_newlines,
    text_digest,
    uia_metadata_bytes,
    window_matches,
)

MAX_NODES = 2048
MAX_FIELD_TEXT = 100_000
ROLE_TYPES = {
    "AXButton": "Button",
    "AXTextField": "Edit",
    "AXTextArea": "Document",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXMenuItem": "ListItem",
    "AXTabGroup": "TabItem",
    "AXLink": "Hyperlink",
    "AXPopUpButton": "ComboBox",
    "AXComboBox": "ComboBox",
}
ALIASES = {
    "notepad": "TextEdit",
    "блокнот": "TextEdit",
    "textedit": "TextEdit",
    "notes": "Notes",
    "заметки": "Notes",
    "calculator": "Calculator",
    "calc": "Calculator",
    "калькулятор": "Calculator",
    "chrome": "Google Chrome",
    "браузер": "Google Chrome",
    "хром": "Google Chrome",
    "гугл хром": "Google Chrome",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "вс код": "Visual Studio Code",
    "finder": "Finder",
    "explorer": "Finder",
    "проводник": "Finder",
    "terminal": "Terminal",
    "терминал": "Terminal",
    "cmd": "Terminal",
    "командная строка": "Terminal",
}
# Only this fixed set can generate native keys; text insertion never goes through it.
HOTKEYS: dict[str, tuple[int, bool]] = {
    "Ctrl+C": (8, True),
    "Ctrl+V": (9, True),
    "Ctrl+A": (0, True),
    "Ctrl+Z": (6, True),
    "Enter": (36, False),
    "Tab": (48, False),
    "Alt+F4": (13, True),  # Command-W closes the selected macOS window.
}


def command(
    arguments: list[str], *, data: bytes = b"", limit: int = 1_000_000, timeout: float = 4
) -> bytes:
    """No shell and no unbounded communicate buffer, including clipboard/JXA output."""
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "LC_CTYPE": "en_US.UTF-8"},
    )
    output = bytearray()
    deadline = time.monotonic() + timeout
    assert process.stdin is not None and process.stdout is not None
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            pending = memoryview(data)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError("native_timeout")
                for key, event in selector.select(remaining):
                    if event & selectors.EVENT_WRITE:
                        written = os.write(key.fd, pending[:8192])
                        pending = pending[written:]
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    if event & selectors.EVENT_READ:
                        block = os.read(key.fd, min(32768, limit + 1 - len(output)))
                        if not block:
                            selector.unregister(process.stdout)
                        else:
                            output.extend(block)
                            if len(output) > limit:
                                raise ToolError("control_unsupported")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ToolError("native_failure")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise ToolError("native_timeout") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                stream.close()


def normalize(value: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in value.casefold()).split())


def rectangle(x: float, y: float, width: float, height: float) -> Rectangle:
    values = (x, y, width, height)
    if not all(math.isfinite(float(v)) for v in values) or width <= 0 or height <= 0:
        raise ToolError("control_unsupported")
    return Rectangle(
        left=math.floor(x),
        top=math.floor(y),
        right=math.ceil(x + width),
        bottom=math.ceil(y + height),
    )


@dataclass(frozen=True)
class LaunchEntry:
    bundle: Path
    executable: Path
    names: tuple[str, ...]


@dataclass(frozen=True)
class Node:
    value: Any
    path: tuple[int, ...]
    depth: int


class NativeDesktop:
    before_action: Callable[[CursorTarget], None] | None = None
    before_click: Callable[[Rectangle], None] | None = None

    def __init__(self) -> None:
        self.quartz: Any = None
        self.appkit: Any = None
        self.ax: Any = None
        self.psutil: Any = importlib.import_module("psutil")
        self._loaded = False
        self._settings_opened = False

    def frameworks(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for name, attribute in (
            ("Quartz", "quartz"),
            ("AppKit", "appkit"),
            ("ApplicationServices", "ax"),
        ):
            with suppress(ImportError):
                setattr(self, attribute, importlib.import_module(name))

    def permission_error(self, screen: bool = False) -> None:
        if not self._settings_opened:
            self._settings_opened = True
            pane = "Privacy_ScreenCapture" if screen else "Privacy_Accessibility"
            with suppress(OSError, ToolError):
                command(
                    [
                        "/usr/bin/open",
                        f"x-apple.systempreferences:com.apple.preference.security?{pane}",
                    ],
                    limit=1024,
                )
        reason = (
            Reason.MACOS_SCREEN_RECORDING_REQUIRED
            if screen
            else Reason.MACOS_ACCESSIBILITY_REQUIRED
        )
        raise ToolError("control_unsupported", reason=reason)

    def accessibility(self) -> None:
        self.frameworks()
        if self.ax is None:
            raise ToolError("control_unsupported", reason=Reason.MACOS_NATIVE_DEPENDENCY_MISSING)
        if not self.ax.AXIsProcessTrusted():
            self.permission_error()

    def attr(self, value: Any, name: str, default: Any = None) -> Any:
        error, result = self.ax.AXUIElementCopyAttributeValue(value, name, None)
        if error == -25211:  # kAXErrorAPIDisabled
            self.permission_error()
        if error == 0:
            if name == "AXRole" and (not isinstance(result, str) or not result):
                raise ToolError("control_unsupported")
            return result
        # A leaf need not have AXChildren, and optional metadata can have no value.
        # Messaging failures/invalid elements are never interpreted as an empty tree
        # or as proof that a field is not secure.
        if error in {-25205, -25212} and name != "AXRole":
            return default
        raise ToolError("target_changed", reason=Reason.STALE_ELEMENT)

    def set_attr(self, value: Any, name: str, content: Any) -> None:
        if self.ax.AXUIElementSetAttributeValue(value, name, content) != 0:
            raise ToolError("control_unsupported", reason=Reason.EDITOR_NOT_WRITABLE)

    def bounds(self, value: Any) -> Rectangle:
        point = self.attr(value, "AXPosition")
        size = self.attr(value, "AXSize")
        if point is None or size is None:
            raise ToolError("control_unsupported")
        point_type = getattr(self.ax, "kAXValueCGPointType", 1)
        size_type = getattr(self.ax, "kAXValueCGSizeType", 2)
        ok_point, position = self.ax.AXValueGetValue(point, point_type, None)
        ok_size, dimensions = self.ax.AXValueGetValue(size, size_type, None)
        if not ok_point or not ok_size:
            raise ToolError("control_unsupported")
        return rectangle(position.x, position.y, dimensions.width, dimensions.height)

    def window_rows(self) -> list[dict[str, Any]]:
        self.frameworks()
        if self.quartz is not None:
            rows = self.quartz.CGWindowListCopyWindowInfo(
                self.quartz.kCGWindowListOptionOnScreenOnly
                | self.quartz.kCGWindowListExcludeDesktopElements,
                self.quartz.kCGNullWindowID,
            )
            return [dict(row) for row in rows or ()][:2048]
        # Fixed reviewed JXA code. Application names and model text never become source.
        script = (
            "ObjC.import('CoreGraphics');"
            "JSON.stringify(ObjC.deepUnwrap(ObjC.castRefToObject("
            "$.CGWindowListCopyWindowInfo(17, 0))))"
        )
        raw = command(["/usr/bin/osascript", "-l", "JavaScript", "-e", script])
        result = json.loads(raw)
        if not isinstance(result, list):
            raise ToolError("native_failure")
        return [row for row in result[:2048] if isinstance(row, dict)]

    @staticmethod
    def row_bounds(row: dict[str, Any]) -> Rectangle:
        bounds = row["kCGWindowBounds"]
        return rectangle(
            float(bounds["X"]), float(bounds["Y"]), float(bounds["Width"]), float(bounds["Height"])
        )

    def window_snapshot(self, row: dict[str, Any]) -> WindowTarget:
        pid, handle = int(row["kCGWindowOwnerPID"]), int(row["kCGWindowNumber"])
        process = self.psutil.Process(pid)
        executable = str(Path(process.exe()).resolve())
        app = Path(executable).stem[:200]
        title = str(row.get("kCGWindowName") or row.get("kCGWindowOwnerName") or app)[:1000]
        return WindowTarget(
            app=app,
            handle=handle,
            pid=pid,
            process_started=float(process.create_time()),
            executable=executable,
            runtime_id=[pid, handle],
            title=title,
        )

    def windows(self, app: str | None = None) -> list[WindowTarget]:
        result = []
        for row in self.window_rows():
            try:
                if (
                    int(row.get("kCGWindowLayer", 0)) != 0
                    or float(row.get("kCGWindowAlpha", 1)) <= 0
                ):
                    continue
                self.row_bounds(row)
                target = self.window_snapshot(row)
                if app is None or window_matches(target, app):
                    result.append(target)
                    if len(result) == 100:
                        break
            except (KeyError, TypeError, ValueError, OSError, self.psutil.Error, ToolError):
                continue
        return result

    def resolve_row(self, target: WindowTarget, *, check_title: bool = False) -> dict[str, Any]:
        for row in self.window_rows():
            if int(row.get("kCGWindowNumber", 0)) != target.handle:
                continue
            try:
                current = self.window_snapshot(row)
            except (OSError, self.psutil.Error):
                break
            if (
                current.identity == target.identity
                and (not check_title or current.title == target.title)
                and int(row.get("kCGWindowLayer", 0)) == 0
                and float(row.get("kCGWindowAlpha", 1)) > 0
                and bool(row.get("kCGWindowIsOnscreen", True))
            ):
                return row
            break
        raise ToolError("target_changed", reason=Reason.WINDOW_IDENTITY_CHANGED)

    def resolve(self, target: WindowTarget, *, check_title: bool = False) -> Any:
        self.accessibility()
        row = self.resolve_row(target, check_title=check_title)
        wanted = self.row_bounds(row)
        application = self.ax.AXUIElementCreateApplication(target.pid)
        matches = []
        for window in list(self.attr(application, "AXWindows", ()))[:100]:
            try:
                actual = self.bounds(window)
                if all(
                    abs(a - b) <= 2
                    for a, b in zip(
                        (actual.left, actual.top, actual.right, actual.bottom),
                        (wanted.left, wanted.top, wanted.right, wanted.bottom),
                        strict=True,
                    )
                ):
                    matches.append(window)
            except ToolError:
                continue
        if len(matches) != 1:
            raise ToolError("target_changed", reason=Reason.AMBIGUOUS_WINDOW)
        return matches[0]

    def active(self) -> tuple[Any, WindowTarget]:
        self.accessibility()
        if self.appkit is None:
            raise ToolError("control_unsupported", reason=Reason.MACOS_NATIVE_DEPENDENCY_MISSING)
        app = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            raise ToolError("target_changed")
        pid = int(app.processIdentifier())
        focused = self.attr(self.ax.AXUIElementCreateApplication(pid), "AXFocusedWindow")
        if focused is None:
            raise ToolError("target_changed")
        bounds = self.bounds(focused)
        matches = [
            t
            for t in self.windows()
            if t.pid == pid and self.row_bounds(self.resolve_row(t)) == bounds
        ]
        if len(matches) != 1:
            raise ToolError("target_changed", reason=Reason.AMBIGUOUS_WINDOW)
        return self.resolve(matches[0]), matches[0]

    def require_foreground(self, target: WindowTarget) -> None:
        _, current = self.active()
        if current.identity != target.identity:
            raise ToolError("target_changed", reason=Reason.WINDOW_NOT_FOREGROUND)

    def activate(self, target: WindowTarget) -> Any:
        window = self.resolve(target)
        running = self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
            target.pid
        )
        if running is None or not running.activateWithOptions_(
            self.appkit.NSApplicationActivateIgnoringOtherApps
        ):
            raise ToolError("target_changed", reason=Reason.WINDOW_NOT_FOREGROUND)
        self.ax.AXUIElementPerformAction(window, "AXRaise")
        self.set_attr(window, "AXMain", True)
        deadline = time.monotonic() + 1
        while True:
            try:
                self.require_foreground(target)
                return window
            except ToolError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.03)

    @staticmethod
    def catalog() -> list[LaunchEntry]:
        result: dict[Path, LaunchEntry] = {}
        visited = 0
        for base in (
            Path("/Applications"),
            Path.home() / "Applications",
            Path("/System/Applications"),
            Path("/System/Library/CoreServices"),
        ):
            if not base.is_dir():
                continue
            # Applications may be grouped one folder deep; do not scan arbitrary contents.
            bundles = list(base.glob("*.app")) + list(base.glob("*/*.app"))
            for candidate in bundles:
                visited += 1
                if visited > 5000:
                    return list(result.values())
                try:
                    bundle = candidate.resolve()
                    with (bundle / "Contents/Info.plist").open("rb") as stream:
                        info = plistlib.load(stream)
                    binary = info["CFBundleExecutable"]
                    if not isinstance(binary, str) or "/" in binary or "\\" in binary:
                        continue
                    executable = (bundle / "Contents/MacOS" / binary).resolve()
                    if not executable.is_file() or not executable.is_relative_to(bundle):
                        continue
                    names = tuple(
                        str(n)
                        for n in (
                            bundle.stem,
                            info.get("CFBundleName", ""),
                            info.get("CFBundleDisplayName", ""),
                            binary,
                        )
                        if n
                    )
                    result[bundle] = LaunchEntry(bundle, executable, names)
                except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
                    continue
        return list(result.values())

    def resolve_app(self, name: str) -> LaunchEntry:
        # The public schema is also rechecked in the isolated helper.
        validated = NativeRequest(operation="open", app=name).app
        assert validated is not None
        wanted = normalize(ALIASES.get(validated.casefold(), validated))
        if wanted in {"script editor", "automator", "python", "osascript", "applescript"}:
            raise ToolError("control_unsupported", reason=Reason.APPLICATION_BLOCKED)
        matches = [
            entry for entry in self.catalog() if wanted in {normalize(n) for n in entry.names}
        ]
        if not matches:
            raise ToolError("application_missing")
        if len(matches) != 1:
            raise ToolError("application_ambiguous")
        return matches[0]

    def open_app(self, name: str) -> WindowsResult:
        entry = self.resolve_app(name)
        command(["/usr/bin/open", "-a", str(entry.bundle)], limit=1024)
        deadline = time.monotonic() + 6
        while True:
            matches = [
                target for target in self.windows() if Path(target.executable) == entry.executable
            ]
            if matches:
                return WindowsResult(target=self.with_editor(matches[0]))
            if time.monotonic() >= deadline:
                raise ToolError("native_timeout")
            time.sleep(0.05)

    def with_editor(self, target: WindowTarget) -> WindowTarget:
        # Launch/list must remain useful before Accessibility is granted. Operations
        # requiring fields obtain permission explicitly rather than implying it here.
        self.frameworks()
        if self.ax is None or not self.ax.AXIsProcessTrusted():
            return target
        fields = self.editors(self.resolve(target), target)
        if len(fields) != 1:
            return target
        return target.model_copy(
            update={"editor": fields[0], "empty": fields[0].content_sha256 == text_digest("")}
        )

    def walk(
        self, root: Any, target: WindowTarget, max_depth: int = MAX_UIA_DEPTH
    ) -> tuple[list[Node], bool]:
        queue = deque([Node(root, (target.handle,), 0)])
        nodes: list[Node] = []
        truncated = False
        while queue and len(nodes) < MAX_NODES:
            current = queue.popleft()
            nodes.append(current)
            if self.password(current.value):
                continue
            children = list(self.attr(current.value, "AXChildren", ()))
            if current.depth >= max_depth or len(current.path) >= 32:
                truncated |= bool(children)
                continue
            for index, child in enumerate(children[:MAX_NODES]):
                if len(queue) + len(nodes) >= MAX_NODES:
                    truncated = True
                    break
                queue.append(Node(child, (*current.path, index), current.depth + 1))
        return nodes, truncated or bool(queue)

    def password(self, value: Any) -> bool:
        return (
            "secure" in str(self.attr(value, "AXRole", "")).casefold()
            or "secure" in str(self.attr(value, "AXSubrole", "")).casefold()
            or bool(self.attr(value, "AXProtectedContent", False))
        )

    def private_ancestor(self, value: Any) -> bool:
        """A hit/focused child of a secure field remains a forbidden input target."""
        for _ in range(MAX_UIA_DEPTH):
            if self.password(value):
                return True
            parent = self.attr(value, "AXParent")
            if parent is None:
                return False
            value = parent
        return True  # An ancestry that cannot be bounded is not a safe input target.

    def writable(self, value: Any) -> bool:
        if self.password(value) or not bool(self.attr(value, "AXEnabled", True)):
            return False
        if self.attr(value, "AXRole") not in {"AXTextField", "AXTextArea", "AXComboBox"}:
            return False
        error, settable = self.ax.AXUIElementIsAttributeSettable(value, "AXValue", None)
        return error == 0 and bool(settable)

    def read(self, value: Any) -> str:
        if not self.writable(value):
            raise ToolError("control_unsupported", reason=Reason.EDITOR_NOT_WRITABLE)
        content = self.attr(value, "AXValue")
        if not isinstance(content, str) or len(content) > MAX_FIELD_TEXT:
            raise ToolError("control_unsupported")
        return normalize_newlines(content)

    def selected_tabs(self, nodes: list[Node]) -> list[list[int]]:
        selected = []
        for node in nodes:
            if self.attr(node.value, "AXRole") in {"AXRadioButton", "AXTabGroup"} and (
                self.attr(node.value, "AXSelected", False) or self.attr(node.value, "AXValue") == 1
            ):
                selected.append(list(node.path))
                if len(selected) == 32:
                    break
        return selected

    def editor_target(self, node: Node, tabs: list[list[int]]) -> EditorTarget:
        value = node.value
        role = str(self.attr(value, "AXRole", ""))
        name = self.label(value)
        class_name = role + ":" + str(self.attr(value, "AXSubrole", ""))
        if len(name) > 200 or len(class_name) > 200:
            raise ToolError("control_unsupported")
        return EditorTarget(
            runtime_id=list(node.path),
            control_type="Document" if role == "AXTextArea" else "Edit",
            automation_id=self.identifier(value),
            class_name=class_name,
            handle=0,
            name=name,
            selected_tabs=tabs,
            content_sha256=text_digest(self.read(value)),
        )

    def editors(self, window: Any, target: WindowTarget) -> list[EditorTarget]:
        nodes, _ = self.walk(window, target)
        tabs = self.selected_tabs(nodes)
        result = []
        for node in nodes:
            if self.writable(node.value):
                result.append(self.editor_target(node, tabs))
                if len(result) == 20:
                    break
        return result

    def chosen_editor(self, window: Any, target: WindowTarget, wanted: EditorTarget) -> Any:
        nodes, truncated = self.walk(window, target)
        if truncated:
            raise ToolError("target_changed", reason=Reason.EDITOR_CHANGED)
        tabs = self.selected_tabs(nodes)
        matches = [
            n.value
            for n in nodes
            if self.writable(n.value) and self.editor_target(n, tabs).identity == wanted.identity
        ]
        if len(matches) != 1:
            raise ToolError("target_changed", reason=Reason.EDITOR_CHANGED)
        return matches[0]

    @staticmethod
    def typing(request: NativeRequest) -> TypeText:
        if request.target is None or request.text is None:
            raise ToolError("native_failure")
        return TypeText(
            target=request.target,
            editor=request.editor,
            text=request.text,
            overwrite=request.overwrite,
        )

    def resolve_editor(self, args: TypeText) -> tuple[Any, Any]:
        window = self.resolve(args.target)
        editor = self.chosen_editor(window, args.target, args.field)
        if (
            args.field.content_sha256 is None
            or text_digest(self.read(editor)) != args.field.content_sha256
        ):
            raise ToolError("target_changed", reason=Reason.EDITOR_CHANGED)
        return window, editor

    def label(self, value: Any) -> str:
        # AXValue is deliberately excluded: it can contain user text and passwords.
        return str(self.attr(value, "AXTitle", "") or self.attr(value, "AXDescription", ""))

    def identifier(self, value: Any) -> str:
        result = str(self.attr(value, "AXIdentifier", ""))
        if len(result) > 500:
            raise ToolError("control_unsupported")
        return result

    def element_snapshot(self, node: Node, *, mark_id: int = 1) -> ElementTarget:
        value = node.value
        role = self.attr(value, "AXRole")
        if (
            role not in ROLE_TYPES
            or self.password(value)
            or not self.attr(value, "AXEnabled", True)
        ):
            raise ToolError("control_unsupported")
        label = self.label(value)
        return ElementTarget(
            mark_id=mark_id,
            runtime_id=list(node.path),
            name=label[:MAX_ELEMENT_NAME],
            label_truncated=len(label) > MAX_ELEMENT_NAME,
            name_sha256=clipboard_digest(label) if len(label) > MAX_ELEMENT_NAME else None,
            control_type=cast("Any", ROLE_TYPES[role]),
            automation_id=self.identifier(value),
            rectangle=self.bounds(value),
        )

    def inspection(
        self, window: Any, target: WindowTarget, max_depth: int
    ) -> tuple[WindowsResult, list[Rectangle], bool]:
        bounds = self.row_bounds(self.resolve_row(target))
        nodes, tree_truncated = self.walk(window, target, max_depth)
        truncated = tree_truncated
        candidates, private = [], []
        for node in nodes:
            if self.password(node.value):
                private.append(self.bounds(node.value))
                continue
            try:
                element = self.element_snapshot(node)
            except ToolError:
                continue
            rect = element.rectangle
            if (
                rect.right > bounds.left
                and rect.left < bounds.right
                and rect.bottom > bounds.top
                and rect.top < bounds.bottom
            ):
                candidates.append(element)
        priority = {
            "Edit": 0,
            "ComboBox": 0,
            "Button": 1,
            "RadioButton": 1,
            "CheckBox": 1,
            "Hyperlink": 2,
            "TabItem": 2,
            "Document": 3,
            "ListItem": 4,
        }
        candidates.sort(
            key=lambda e: (
                not bool(e.name.strip()),
                priority[e.control_type],
                e.rectangle.top,
                e.rectangle.left,
            )
        )
        result = WindowsResult(target=target, window_rectangle=bounds, truncated=truncated)
        for element in candidates:
            if len(result.elements) == MAX_UIA_ELEMENTS:
                truncated = True
                break
            marked = element.model_copy(update={"mark_id": len(result.elements) + 1})
            candidate = result.model_copy(update={"elements": [*result.elements, marked]})
            if uia_metadata_bytes(candidate) > MAX_UIA_OBSERVATION_BYTES:
                truncated = True
                break
            result = candidate
        return result.model_copy(update={"truncated": truncated}), private, not tree_truncated

    def inspect(self, depth: int) -> WindowsResult:
        window, target = self.active()
        result, _, _ = self.inspection(window, target, depth)
        self.require_foreground(target)
        if self.row_bounds(self.resolve_row(target)) != result.window_rectangle:
            raise ToolError("target_changed")
        return result

    def clipboard(self, text: str | None = None, *, plain_only: bool = False) -> str:
        if text is not None:
            ClipboardSet(text=text)
            command(["/usr/bin/pbcopy"], data=text.encode("utf-8"), limit=0)
        if plain_only:
            self.frameworks()
            if self.appkit is None:
                raise ToolError(
                    "control_unsupported", reason=Reason.MACOS_NATIVE_DEPENDENCY_MISSING
                )
            types = set(self.appkit.NSPasteboard.generalPasteboard().types() or ())
            if not types <= {
                "public.utf8-plain-text",
                "public.utf16-external-plain-text",
                "public.text",
                "NSStringPboardType",
            }:
                raise ToolError("control_unsupported")
        value = command(["/usr/bin/pbpaste", "-Prefer", "txt"], limit=16000).decode("utf-8")
        ClipboardSet(text=value)
        return value

    @staticmethod
    def mark_image(image: Any, observation: WindowsResult) -> Any:
        bounds = observation.window_rectangle
        if bounds is None:
            return image
        result = image.copy()
        draw = importlib.import_module("PIL.ImageDraw").Draw(result)
        sx, sy = (
            image.width / (bounds.right - bounds.left),
            image.height / (bounds.bottom - bounds.top),
        )
        for element in observation.elements:
            r = element.rectangle
            left = max(0, min(image.width - 1, round((r.left - bounds.left) * sx)))
            top = max(0, min(image.height - 1, round((r.top - bounds.top) * sy)))
            right = max(left, min(image.width - 1, round((r.right - bounds.left) * sx)))
            bottom = max(top, min(image.height - 1, round((r.bottom - bounds.top) * sy)))
            draw.rectangle((left, top, right, bottom), outline="black", width=3)
            draw.rectangle((left, top, right, bottom), outline="#ffe600", width=1)
            text = f"[{element.mark_id}]"
            box = draw.textbbox((0, 0), text)
            width, height = box[2] - box[0] + 6, box[3] - box[1] + 4
            x, y = max(0, min(left, image.width - width)), max(0, min(top, image.height - height))
            draw.rectangle((x, y, x + width, y + height), fill="#ffe600", outline="black")
            draw.text((x + 3 - box[0], y + 2 - box[1]), text, fill="black")
        return result

    @staticmethod
    def redact(image: Any, bounds: Rectangle, private: list[Rectangle]) -> None:
        draw = importlib.import_module("PIL.ImageDraw").Draw(image)
        sx, sy = (
            image.width / (bounds.right - bounds.left),
            image.height / (bounds.bottom - bounds.top),
        )
        for r in private:
            draw.rectangle(
                (
                    math.floor((r.left - bounds.left) * sx),
                    math.floor((r.top - bounds.top) * sy),
                    math.ceil((r.right - bounds.left) * sx),
                    math.ceil((r.bottom - bounds.top) * sy),
                ),
                fill="black",
            )

    def capture(self, scope: str) -> WindowsResult:
        if scope != "active_window":
            # Whole-display privacy cannot be proved for transient and system windows.
            raise ToolError("control_unsupported")
        self.accessibility()  # Required to identify/redact secure controls before pixels leave.
        if self.quartz is None:
            raise ToolError("control_unsupported", reason=Reason.MACOS_NATIVE_DEPENDENCY_MISSING)
        if not self.quartz.CGPreflightScreenCaptureAccess():
            self.permission_error(screen=True)
        window, target = self.active()
        observation, private, complete = self.inspection(window, target, MAX_UIA_DEPTH)
        if not complete:
            raise ToolError("control_unsupported")  # An incomplete tree cannot prove redaction.
        bounds = observation.window_rectangle
        assert bounds is not None
        arguments = ["/usr/sbin/screencapture", "-x", "-o", "-t", "png"]
        arguments += ["-l", str(target.handle)]
        with tempfile.TemporaryDirectory(prefix="jarvis-capture-") as directory:
            destination = Path(directory) / "window.png"
            command([*arguments, str(destination)], timeout=4, limit=1024)
            if not destination.is_file() or destination.stat().st_size > 50_000_000:
                raise ToolError("native_failure")
            module = importlib.import_module("PIL.Image")
            with module.open(destination) as captured:
                if captured.width * captured.height > 50_000_000:
                    raise ToolError("native_failure")
                image = captured.convert("RGB")
        self.require_foreground(target)
        if (
            self.row_bounds(self.resolve_row(target)) != observation.window_rectangle
            and scope == "active_window"
        ):
            raise ToolError("target_changed")
        if scope == "active_window":
            after, after_private, complete = self.inspection(
                self.resolve(target), target, MAX_UIA_DEPTH
            )
            if (
                not complete
                or after_private != private
                or after.window_rectangle != observation.window_rectangle
                or after.elements != observation.elements
            ):
                raise ToolError("target_changed")
        self.redact(image, bounds, private)
        image.thumbnail((1280, 800))
        while True:
            output = io.BytesIO()
            self.mark_image(image, observation).save(output, format="JPEG", quality=65)
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

    def preview(
        self, target: WindowTarget, bounds: Rectangle, *, typing: bool = False, label: str = ""
    ) -> None:
        if self.before_action is not None:
            self.before_action(
                CursorTarget(
                    rectangle=bounds,
                    action="type" if typing else "click",
                    label=label[:500],
                    window_handle=target.handle,
                )
            )
        elif self.before_click is not None and not typing:
            self.before_click(bounds)

    def click(self, request: NativeRequest) -> WindowsResult:
        if request.target is None:
            raise ToolError("native_failure")
        args = ClickElement(
            target=request.target,
            element=request.element,
            x=request.x,
            y=request.y,
            window_rectangle=request.window_rectangle,
        )
        window = self.activate(args.target)
        if args.element is not None:

            def chosen() -> Node:
                nodes, _ = self.walk(self.resolve(args.target), args.target)
                matches = []
                for node in nodes:
                    try:
                        if (
                            self.element_snapshot(node, mark_id=args.element.mark_id)
                            == args.element
                        ):
                            matches.append(node)
                    except ToolError:
                        continue
                if len(matches) != 1:
                    raise ToolError("target_changed", reason=Reason.STALE_ELEMENT)
                return matches[0]

            chosen()
            self.preview(args.target, args.element.rectangle, label=args.element.name)
            node = chosen()
            self.require_foreground(args.target)
            if self.ax.AXUIElementPerformAction(node.value, "AXPress") != 0:
                raise ToolError("control_unsupported")
        else:
            assert args.x is not None and args.y is not None
            bounds = self.bounds(window)
            if (
                bounds != args.window_rectangle
                or not bounds.left <= args.x < bounds.right
                or not bounds.top <= args.y < bounds.bottom
            ):
                raise ToolError("target_changed")
            self.check_point(args.target, args.x, args.y)
            preview = Rectangle(
                left=args.x - 12, top=args.y - 12, right=args.x + 12, bottom=args.y + 12
            )
            self.preview(args.target, preview)
            if self.bounds(self.resolve(args.target)) != bounds:
                raise ToolError("target_changed")
            self.require_foreground(args.target)
            self.check_point(args.target, args.x, args.y)
            self.point_click(args.x, args.y)
        return WindowsResult(target=args.target, performed=True)

    def check_point(self, target: WindowTarget, x: int, y: int) -> None:
        self.require_foreground(target)
        system = self.ax.AXUIElementCreateSystemWide()
        error, element = self.ax.AXUIElementCopyElementAtPosition(system, x, y, None)
        if error != 0 or element is None or self.private_ancestor(element):
            raise ToolError("target_changed")
        error, pid = self.ax.AXUIElementGetPid(element, None)
        if error != 0 or int(pid) != target.pid:
            raise ToolError("target_changed")
        window = self.attr(element, "AXWindow")
        if window is None or self.bounds(window) != self.bounds(self.resolve(target)):
            raise ToolError("target_changed")

    def point_click(self, x: int, y: int) -> None:
        if self.quartz is None:
            raise ToolError("control_unsupported", reason=Reason.MACOS_NATIVE_DEPENDENCY_MISSING)
        q = self.quartz
        previous = q.CGEventGetLocation(q.CGEventCreate(None))
        try:
            for kind in (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp):
                event = q.CGEventCreateMouseEvent(None, kind, (x, y), q.kCGMouseButtonLeft)
                if event is None:
                    raise ToolError("native_failure")
                q.CGEventPost(q.kCGHIDEventTap, event)
        finally:
            current = q.CGEventGetLocation(q.CGEventCreate(None))
            if abs(current.x - x) <= 1 and abs(current.y - y) <= 1:
                q.CGWarpMouseCursorPosition(previous)

    def hotkey(self, request: NativeRequest) -> WindowsResult:
        if request.target is None:
            raise ToolError("native_failure")
        args = SendHotkey(
            target=request.target,
            hotkey=cast("Any", request.hotkey),
            clipboard_sha256=request.clipboard_sha256,
        )
        self.activate(args.target)
        focused = self.attr(
            self.ax.AXUIElementCreateApplication(args.target.pid), "AXFocusedUIElement"
        )
        if focused is None or self.private_ancestor(focused):
            raise ToolError("control_unsupported")
        if (
            args.hotkey == "Ctrl+V"
            and clipboard_digest(self.clipboard(plain_only=True)) != args.clipboard_sha256
        ):
            raise ToolError("target_changed")
        self.require_foreground(args.target)
        if self.quartz is None or args.hotkey not in HOTKEYS:
            raise ToolError("control_unsupported")
        code, command_modifier = HOTKEYS[args.hotkey]
        for down in (True, False):
            event = self.quartz.CGEventCreateKeyboardEvent(None, code, down)
            if event is None:
                raise ToolError("native_failure")
            self.quartz.CGEventSetFlags(
                event, self.quartz.kCGEventFlagMaskCommand if command_modifier else 0
            )
            self.quartz.CGEventPost(self.quartz.kCGHIDEventTap, event)
        return WindowsResult(target=args.target, performed=True)

    def dispatch(self, request: NativeRequest) -> WindowsResult:
        operation = request.operation
        if operation.startswith("calculator_"):
            # Microsoft Calculator contracts carry Windows-only package/control IDs.
            raise ToolError("control_unsupported")
        if operation == "list":
            windows = self.windows(request.app)
            return WindowsResult(windows=windows, truncated=len(windows) == 100)
        if operation in {"check_open", "open"}:
            if request.app is None:
                raise ToolError("native_failure")
            self.resolve_app(request.app)
            return self.open_app(request.app) if operation == "open" else WindowsResult()
        if operation in {"check_chrome", "open_chrome_url"}:
            entry = self.resolve_app("Google Chrome")
            if operation == "check_chrome":
                return WindowsResult()
            if request.url is None:
                raise ToolError("network_denied")
            url = chrome_url(request.url)
            command(["/usr/bin/open", "-a", str(entry.bundle), url], limit=1024)
            return WindowsResult(text=url, performed=True)
        if operation in {"clipboard_get", "clipboard_set"}:
            if operation == "clipboard_set" and request.text is None:
                raise ToolError("native_failure")
            value = self.clipboard(request.text if operation == "clipboard_set" else None)
            return WindowsResult(text=value, clipboard_sha256=clipboard_digest(value))
        if operation == "inspect":
            return self.inspect(request.max_depth)
        if operation == "screenshot":
            return self.capture(request.scope)
        if operation == "hotkey":
            return self.hotkey(request)
        if operation == "click":
            return self.click(request)
        if operation == "focus":
            selector = FocusApp(
                target=request.target,
                handle=request.handle,
                title=request.title,
                process=request.process,
            )
            if selector.target is not None:
                target = selector.target
            else:
                matches = [
                    t
                    for t in self.windows()
                    if selector.handle == t.handle
                    or selector.title is not None
                    and selector.title.casefold() in t.title.casefold()
                    or selector.process is not None
                    and window_matches(t, selector.process)
                ]
                if len(matches) != 1:
                    raise ToolError("target_changed", reason=Reason.AMBIGUOUS_WINDOW)
                target = matches[0]
            self.activate(target)
            return WindowsResult(target=target, focused=True)
        if request.target is None:
            raise ToolError("native_failure")
        if operation == "check_target":
            if request.text is not None:
                self.resolve_editor(self.typing(request))
            else:
                self.resolve_row(request.target)
            return WindowsResult(target=request.target)
        if operation == "fields":
            return WindowsResult(
                target=request.target,
                editors=self.editors(self.resolve(request.target), request.target),
            )
        if operation == "type":
            args = self.typing(request)
            _, editor = self.resolve_editor(args)
            bounds = self.bounds(editor)
            self.preview(args.target, bounds, typing=True, label="Ввод текста...")
            _, editor = self.resolve_editor(args)
            if self.bounds(editor) != bounds:
                raise ToolError("target_changed")
            expected = ("" if args.overwrite else self.read(editor)) + args.text
            self.set_attr(editor, "AXValue", expected)
            chosen = self.chosen_editor(self.resolve(args.target), args.target, args.field)
            digest = text_digest(self.read(chosen))
            if digest != text_digest(expected):
                raise ToolError("target_changed", reason=Reason.EDITOR_CHANGED)
            return WindowsResult(
                target=args.target, text_sha256=digest, inserted_text_sha256=text_digest(args.text)
            )
        if operation == "verify" and request.result is not None:
            if request.result.text_sha256 is not None:
                wanted = request.editor or request.target.editor
                if wanted is None:
                    raise ToolError("native_failure")
                editor = self.chosen_editor(self.resolve(request.target), request.target, wanted)
                return WindowsResult(
                    target=request.target, text_sha256=text_digest(self.read(editor))
                )
            self.resolve_row(request.target)
            try:
                self.require_foreground(request.target)
                focused = True
            except ToolError:
                focused = False
            return WindowsResult(target=request.target, focused=focused)
        # Microsoft's calculator-specific IDs deliberately cannot identify a Mac control.
        raise ToolError("control_unsupported")
