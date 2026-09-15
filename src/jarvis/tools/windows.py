"""Portable Windows schemas and registered operations. No native imports here."""

from hashlib import sha256
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

AppId = Literal["notepad", "chrome", "vscode"]


def text_digest(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


class EditorTarget(ToolModel):
    runtime_id: list[int] = Field(min_length=1, max_length=32)
    control_type: Literal["Edit", "Document"]
    automation_id: str = Field(max_length=500)
    class_name: str = Field(max_length=200)
    handle: int = Field(ge=0)
    selected_tabs: list[list[int]] = Field(default_factory=list, max_length=32)


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


class TypeText(ToolModel):
    service: Literal["windows-desktop"] = "windows-desktop"
    action_type: Literal["fill_empty_notepad_editor"] = "fill_empty_notepad_editor"
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
    def only_empty_notepad(self) -> "TypeText":
        if self.target.app != "notepad" or not self.target.empty or self.target.editor is None:
            raise ValueError("Select an observed empty Notepad editor.")
        return self


class WindowsResult(ToolModel):
    windows: list[WindowTarget] = Field(default_factory=list, max_length=100)
    target: WindowTarget | None = None
    focused: bool = False
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


Operation = Literal["list", "open", "focus", "type", "check_open", "check_target", "verify"]


class NativeRequest(ToolModel):
    operation: Operation
    app: AppId | None = None
    target: WindowTarget | None = None
    text: str | None = Field(default=None, max_length=4000)
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
        if result.target is None or result.target.app != args.app:
            return False
        await backend.call(NativeRequest(operation="check_target", target=result.target), context)
        return True

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
            "Ввести буквальный текст в пустой Блокнот.",
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
