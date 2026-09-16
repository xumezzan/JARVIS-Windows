"""Portable Windows schemas and registered operations. No native imports here."""

from hashlib import sha256
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, field_validator, model_validator

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

# Any installed application: a spoken or typed name for a request, and the executable's
# own stem for an observed window. Letters, digits, spaces and a few separators only —
# never a path, argument, URL or shell fragment.
AppId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[0-9A-Za-z\u0400-\u04FF][0-9A-Za-z\u0400-\u04FF ._+\-]*$",
    ),
]


def text_digest(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


class EditorTarget(ToolModel):
    """Identity of one text field inside an already exactly identified window."""

    runtime_id: list[int] = Field(min_length=1, max_length=32)
    control_type: Literal["Edit", "Document"]
    automation_id: str = Field(max_length=500)
    class_name: str = Field(max_length=200)
    handle: int = Field(ge=0)
    name: str = Field(default="", max_length=200)
    selected_tabs: list[list[int]] = Field(default_factory=list, max_length=32)

    @property
    def identity(self) -> tuple[object, ...]:
        """What must still match when the field is found again.

        An application may rebuild a control between two calls, which gives it a new window
        handle and runtime id while it stays the same field of the same document. Role,
        name, automation id and the selected tab do not change that way, so they are the
        binding; the surrounding window is identified exactly and separately.
        """
        return (
            self.control_type,
            self.class_name,
            self.automation_id,
            self.name,
            tuple(tuple(tab) for tab in self.selected_tabs),
        )


class WindowTarget(ToolModel):
    app: AppId
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
    app: AppId


class FocusApp(ToolModel):
    target: WindowTarget


class ListFields(ToolModel):
    target: WindowTarget


class TypeText(ToolModel):
    service: Literal["windows-desktop"] = "windows-desktop"
    action_type: Literal["fill_text_field"] = "fill_text_field"
    target: WindowTarget
    # The exact field, as observed in this task. Omitted only when the window has one.
    editor: EditorTarget | None = None
    text: str = Field(min_length=1, max_length=4000)
    # Existing content is kept unless the caller asks for it to be replaced.
    overwrite: bool = False

    @field_validator("text")
    @classmethod
    def literal_text(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Unsupported control character.")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Invalid Unicode scalar.")
        return value

    @property
    def field(self) -> EditorTarget:
        chosen = self.editor or self.target.editor
        if chosen is None:
            raise ValueError("Select an observed text field.")
        return chosen

    @model_validator(mode="after")
    def observed_field(self) -> "TypeText":
        if self.editor is None and self.target.editor is None:
            raise ValueError("Select an observed text field.")
        return self


class WindowsResult(ToolModel):
    windows: list[WindowTarget] = Field(default_factory=list, max_length=100)
    editors: list[EditorTarget] = Field(default_factory=list, max_length=20)
    target: WindowTarget | None = None
    focused: bool = False
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


Operation = Literal[
    "list", "open", "focus", "type", "fields", "check_open", "check_target", "verify"
]


class NativeRequest(ToolModel):
    operation: Operation
    app: AppId | None = None
    target: WindowTarget | None = None
    editor: EditorTarget | None = None
    text: str | None = Field(default=None, max_length=4000)
    overwrite: bool = False
    result: WindowsResult | None = None


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
        # The requested name is a human word; the observed window carries the real
        # executable identity, which check_target re-observes before this returns true.
        if result.target is None:
            return False
        await backend.call(NativeRequest(operation="check_target", target=result.target), context)
        return True

    async def fields_check(args: ListFields, context: ExecutionContext) -> bool:
        await backend.call(NativeRequest(operation="check_target", target=args.target), context)
        return True

    async def fields_run(args: ListFields, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="fields", target=args.target), context)

    async def fields_verify(
        args: ListFields, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return result.target == args.target

    async def focus_check(args: FocusApp, context: ExecutionContext) -> bool:
        await backend.call(NativeRequest(operation="check_target", target=args.target), context)
        return True

    async def focus_run(args: FocusApp, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="focus", target=args.target), context)

    async def focus_verify(
        args: FocusApp, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        if result.target != args.target or not result.focused:
            return False
        checked = await backend.call(
            NativeRequest(operation="verify", target=args.target, result=result), context
        )
        return checked.focused

    async def type_check(args: TypeText, context: ExecutionContext) -> bool:
        await backend.call(
            NativeRequest(
                operation="check_target",
                target=args.target,
                editor=args.editor,
                text=args.text,
                overwrite=args.overwrite,
            ),
            context,
        )
        return True

    async def type_run(args: TypeText, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(
                operation="type",
                target=args.target,
                editor=args.editor,
                text=args.text,
                overwrite=args.overwrite,
            ),
            context,
        )

    async def type_verify(args: TypeText, result: WindowsResult, context: ExecutionContext) -> bool:
        if result.target != args.target or result.text_sha256 != text_digest(args.text):
            return False
        checked = await backend.call(
            NativeRequest(
                operation="verify", target=args.target, editor=args.editor, result=result
            ),
            context,
        )
        return checked.text_sha256 == text_digest(args.text)

    cancellation = "Helper is killed and reaped on stop/timeout; issued OS effects may remain."
    registry.register(
        ToolSpec(
            "windows.get_open_windows",
            "Найти окна разрешённых приложений.",
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
            "Открыть установленное приложение по названию.",
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
            "windows.get_text_fields",
            "Найти текстовые поля точно выбранного окна.",
            Risk.SAFE,
            ListFields,
            WindowsResult,
            fields_check,
            fields_run,
            fields_verify,
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
            "Ввести буквальный текст в наблюдаемое текстовое поле.",
            Risk.CONFIRM,
            TypeText,
            WindowsResult,
            type_check,
            type_run,
            type_verify,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
