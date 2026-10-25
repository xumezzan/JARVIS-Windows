"""Portable Windows schemas and registered operations. No native imports here."""

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from jarvis.permissions.interaction import requires_confirmation
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

AppId = str
LaunchAppId = Literal["notepad", "chrome", "vscode"]
MAX_REPLY_BYTES = 2_500_000
MAX_SCREENSHOT_JPEG_BYTES = 48 * 1024
MAX_SCREENSHOT_BASE64 = 4 * ((MAX_SCREENSHOT_JPEG_BYTES + 2) // 3)
MAX_UIA_ELEMENTS = 96
MAX_ELEMENT_NAME = 120
MAX_UIA_OBSERVATION_BYTES = 67000


def text_digest(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def clipboard_digest(text: str) -> str:
    """Bind the exact clipboard text, including line endings."""
    return sha256(text.encode("utf-8")).hexdigest()


class EditorTarget(ToolModel):
    runtime_id: list[int] = Field(min_length=1, max_length=32)
    control_type: Literal["Edit", "Document"]
    automation_id: str = Field(max_length=500)
    class_name: str = Field(max_length=200)
    handle: int = Field(ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    selected_tabs: list[list[int]] = Field(default_factory=list, max_length=32)


class WindowTarget(ToolModel):
    app: str = Field(min_length=1, max_length=200)
    handle: int = Field(gt=0)
    pid: int = Field(gt=0)
    process_started: float = Field(gt=0, allow_inf_nan=False)
    executable: str = Field(min_length=1, max_length=1000)
    runtime_id: list[int] = Field(min_length=1, max_length=32)
    title: str = Field(max_length=1000)
    editor: EditorTarget | None = None
    empty: bool = False


class ListWindows(ToolModel):
    app: AppId | None = None


class OpenApp(ToolModel):
    app: LaunchAppId


class FocusApp(ToolModel):
    target: WindowTarget | None = None
    handle: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def one_selector(self) -> "FocusApp":
        if sum(value is not None for value in (self.target, self.handle, self.title)) != 1:
            raise ValueError("Supply exactly one window selector.")
        if self.title is not None and not self.title.strip():
            raise ValueError("Empty title selector.")
        return self


class TypeText(ToolModel):
    service: Literal["windows-desktop"] = "windows-desktop"
    action_type: Literal["fill_empty_notepad_editor", "set_editor_text"] = "set_editor_text"
    target: WindowTarget
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def literal_text(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Unsupported control character.")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Invalid Unicode scalar.")
        return value

    @model_validator(mode="after")
    def observed_editor(self) -> "TypeText":
        if self.target.editor is None:
            raise ValueError("Select an observed editor.")
        if not self.target.empty and self.target.editor.content_sha256 is None:
            raise ValueError("A nonempty editor requires an observed content digest.")
        return self


class Rectangle(ToolModel):
    left: int
    top: int
    right: int
    bottom: int

    @model_validator(mode="after")
    def positive_area(self) -> "Rectangle":
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Empty rectangle.")
        return self


class ElementTarget(ToolModel):
    mark_id: int = Field(default=1, ge=1, le=MAX_UIA_ELEMENTS)
    runtime_id: list[int] = Field(min_length=1, max_length=32)
    name: str = Field(max_length=MAX_ELEMENT_NAME)
    label_truncated: bool = False
    name_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    control_type: Literal["Button", "Edit", "Document", "TabItem", "ListItem"]
    automation_id: str = Field(max_length=500)
    rectangle: Rectangle


class InspectActiveWindow(ToolModel):
    max_depth: int = Field(default=5, ge=1, le=5)


class ClipboardGet(ToolModel):
    pass


class ClipboardSet(ToolModel):
    text: str = Field(max_length=4000)

    @field_validator("text")
    @classmethod
    def literal_text(cls, value: str) -> str:
        return TypeText.literal_text(value)


class SendHotkey(ToolModel):
    target: WindowTarget
    hotkey: Literal["Ctrl+C", "Ctrl+V", "Ctrl+A", "Enter", "Tab", "Alt+F4"]
    clipboard_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bind_paste(self) -> "SendHotkey":
        if self.hotkey == "Ctrl+V" and self.clipboard_sha256 is None:
            raise ValueError("Paste requires the observed clipboard digest.")
        return self


class ClickElement(ToolModel):
    target: WindowTarget
    element: ElementTarget | None = None
    mark_id: int | None = Field(default=None, ge=1, le=MAX_UIA_ELEMENTS)
    x: int | None = None
    y: int | None = None
    window_rectangle: Rectangle | None = None

    @model_validator(mode="after")
    def one_destination(self) -> "ClickElement":
        coordinates = self.x is not None and self.y is not None
        if self.element is not None or self.mark_id is not None:
            if self.element is not None and self.mark_id is not None:
                raise ValueError("Supply an element or a mark, not both.")
            if self.x is not None or self.y is not None or self.window_rectangle is not None:
                raise ValueError("Supply an element, a mark or coordinates.")
        elif not coordinates or self.window_rectangle is None:
            raise ValueError("Coordinates require an observed window rectangle.")
        return self


class Screenshot(ToolModel):
    scope: Literal["active_window", "screen"] = "active_window"


class WindowsResult(ToolModel):
    windows: list[WindowTarget] = Field(default_factory=list, max_length=1000)
    target: WindowTarget | None = None
    focused: bool = False
    elements: list[ElementTarget] = Field(default_factory=list, max_length=MAX_UIA_ELEMENTS)
    window_rectangle: Rectangle | None = None
    truncated: bool = False
    text: str | None = Field(default=None, max_length=4000, repr=False)
    clipboard_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    image_base64: str | None = Field(default=None, max_length=2_000_000, repr=False)
    image_format: Literal["JPEG"] | None = None
    width: int | None = Field(default=None, gt=0, le=1280)
    height: int | None = Field(default=None, gt=0, le=800)
    performed: bool = False
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def uia_metadata_bytes(result: WindowsResult) -> int:
    """Bound metadata including escaping when embedded in the provider's JSON text."""
    metadata = result.model_dump(mode="json", exclude={"image_base64"})
    for element in metadata["elements"]:
        if element.get("name_sha256") is None:
            element.pop("name_sha256", None)
    text = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    return len(json.dumps(text, ensure_ascii=False).encode())


Operation = Literal[
    "list",
    "open",
    "focus",
    "type",
    "check_open",
    "check_target",
    "verify",
    "inspect",
    "clipboard_get",
    "clipboard_set",
    "hotkey",
    "click",
    "screenshot",
]


class NativeRequest(ToolModel):
    operation: Operation
    app: AppId | None = None
    target: WindowTarget | None = None
    text: str | None = Field(default=None, max_length=4000)
    result: WindowsResult | None = None
    handle: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    max_depth: int = Field(default=5, ge=1, le=5)
    hotkey: str | None = None
    clipboard_sha256: str | None = None
    element: ElementTarget | None = None
    x: int | None = None
    y: int | None = None
    window_rectangle: Rectangle | None = None
    scope: Literal["active_window", "screen"] = "active_window"


class WindowsBackend(Protocol):
    async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult: ...


def register_windows(registry: ToolRegistry, backend: WindowsBackend) -> None:
    async def list_check(args: ListWindows, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def list_run(args: ListWindows, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="list", app=args.app), context)

    async def list_verify(
        args: ListWindows, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return all(args.app is None or item.app == args.app for item in result.windows)

    async def open_check(args: OpenApp, context: ExecutionContext) -> bool:
        await backend.call(NativeRequest(operation="check_open", app=args.app), context)
        return True

    async def open_run(args: OpenApp, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="open", app=args.app), context)

    async def open_verify(args: OpenApp, result: WindowsResult, context: ExecutionContext) -> bool:
        if result.target is None or result.target.app != args.app:
            return False
        await backend.call(NativeRequest(operation="check_target", target=result.target), context)
        return True

    async def focus_check(args: FocusApp, context: ExecutionContext) -> bool:
        if args.target is not None:
            await backend.call(NativeRequest(operation="check_target", target=args.target), context)
        return True

    async def focus_run(args: FocusApp, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="focus", **args.model_dump()), context)

    async def focus_verify(
        args: FocusApp, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        if result.target is None or not result.focused:
            return False
        if args.target is not None and result.target != args.target:
            return False
        if args.handle is not None and result.target.handle != args.handle:
            return False
        if args.title is not None and args.title.casefold() not in result.target.title.casefold():
            return False
        checked = await backend.call(
            NativeRequest(operation="verify", target=result.target, result=result), context
        )
        return checked.focused

    async def type_check(args: TypeText, context: ExecutionContext) -> bool:
        await backend.call(
            NativeRequest(operation="check_target", target=args.target, text=args.text), context
        )
        return True

    async def type_run(args: TypeText, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(operation="type", target=args.target, text=args.text), context
        )

    async def type_verify(args: TypeText, result: WindowsResult, context: ExecutionContext) -> bool:
        if result.target != args.target or result.text_sha256 != text_digest(args.text):
            return False
        checked = await backend.call(
            NativeRequest(operation="verify", target=args.target, result=result), context
        )
        return checked.text_sha256 == text_digest(args.text)

    cancellation = "Helper is killed and reaped on stop/timeout; issued OS effects may remain."
    registry.register(
        ToolSpec(
            "windows.get_open_windows",
            "Найти видимые окна рабочего стола с непустым заголовком.",
            Risk.SAFE,
            ListWindows,
            WindowsResult,
            list_check,
            list_run,
            list_verify,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
    registry.register(
        ToolSpec(
            "windows.open_app",
            "Открыть Notepad, Chrome или VS Code.",
            Risk.SAFE,
            OpenApp,
            WindowsResult,
            open_check,
            open_run,
            open_verify,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
    registry.register(
        ToolSpec(
            "windows.focus_app",
            "Фокус на точно выбранное окно.",
            Risk.SAFE,
            FocusApp,
            WindowsResult,
            focus_check,
            focus_run,
            focus_verify,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
    registry.register(
        ToolSpec(
            "windows.type_text",
            "Заменить текст точно наблюдавшегося редактора буквальным текстом.",
            Risk.SAFE,
            TypeText,
            WindowsResult,
            type_check,
            type_run,
            type_verify,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )

    def register_operation(
        name: str,
        parameters: type[ToolModel],
        operation: Operation,
        risk: Risk,
        description: str,
        classify_risk: Callable[[ToolModel], Risk] | None = None,
    ) -> None:
        async def check(args: ToolModel, context: ExecutionContext) -> bool:
            await context.checkpoint()
            return True

        async def run(args: ToolModel, context: ExecutionContext) -> WindowsResult:
            fields = args.model_dump()
            if isinstance(args, ClickElement):
                if args.mark_id is not None:
                    raise ToolError("target_changed")  # Only Runner can resolve observed marks.
                fields.pop("mark_id")
            return await backend.call(
                NativeRequest.model_validate({"operation": operation, **fields}), context
            )

        async def verify(args: ToolModel, result: WindowsResult, context: ExecutionContext) -> bool:
            await context.checkpoint()
            if isinstance(args, InspectActiveWindow):
                return (
                    result.target is not None
                    and result.window_rectangle is not None
                    and uia_metadata_bytes(result) <= MAX_UIA_OBSERVATION_BYTES
                )
            if isinstance(args, (ClipboardGet, ClipboardSet)):
                return (
                    result.text is not None
                    and result.clipboard_sha256 == clipboard_digest(result.text)
                    and (not isinstance(args, ClipboardSet) or result.text == args.text)
                )
            if isinstance(args, Screenshot):
                return (
                    result.image_base64 is not None
                    and len(result.image_base64) <= MAX_SCREENSHOT_BASE64
                    and result.image_format == "JPEG"
                    and result.width is not None
                    and result.height is not None
                    and uia_metadata_bytes(result) <= MAX_UIA_OBSERVATION_BYTES
                )
            if isinstance(args, (SendHotkey, ClickElement)):
                return result.performed and result.target == args.target
            return False

        registry.register(
            ToolSpec(
                name,
                description,
                risk,
                parameters,
                WindowsResult,
                check,
                run,
                verify,
                timeout_seconds=30,
                cancellation=cancellation,
                classify_risk=classify_risk,
            )
        )

    register_operation(
        "windows.inspect_active_window",
        InspectActiveWindow,
        "inspect",
        Risk.SAFE,
        "Наблюдать активное окно и его интерактивные UIA-элементы.",
    )
    register_operation(
        "windows.clipboard_get",
        ClipboardGet,
        "clipboard_get",
        Risk.SAFE,
        "Прочитать текст системного буфера обмена и его SHA-256.",
    )
    register_operation(
        "windows.clipboard_set",
        ClipboardSet,
        "clipboard_set",
        Risk.SAFE,
        "Заменить текст системного буфера обмена.",
    )

    def hotkey_risk(args: ToolModel) -> Risk:
        assert isinstance(args, SendHotkey)
        # Enter may submit; Alt+F4 may discard unsaved work. No focused-control snapshot.
        return Risk.CONFIRM if args.hotkey in ("Enter", "Alt+F4") else Risk.SAFE

    def click_risk(args: ToolModel) -> Risk:
        assert isinstance(args, ClickElement)
        if args.element is None or not args.element.name.strip() or args.element.label_truncated:
            return Risk.CONFIRM  # Coordinates/unnamed elements carry no action semantics.
        return (
            Risk.CONFIRM
            if any(
                requires_confirmation(label)
                for label in (args.element.name, args.element.automation_id)
            )
            else Risk.SAFE
        )

    register_operation(
        "windows.send_hotkey",
        SendHotkey,
        "hotkey",
        Risk.CONFIRM,
        "Активировать точное окно и отправить одну комбинацию клавиш. "
        "Enter может отправить данные, Alt+F4 может закрыть несохранённое окно.",
        classify_risk=hotkey_risk,
    )
    register_operation(
        "windows.click_element",
        ClickElement,
        "click",
        Risk.CONFIRM,
        "Активировать точное окно и нажать наблюдавшийся UIA-элемент "
        "по полному ElementTarget или mark_id последнего наблюдения; "
        "либо точку в наблюдавшихся границах окна; возможны внешние эффекты.",
        classify_risk=click_risk,
    )
    register_operation(
        "windows.screenshot",
        Screenshot,
        "screenshot",
        Risk.SAFE,
        "Захватить активное окно или экран: base64 JPEG до 1280×800.",
    )
