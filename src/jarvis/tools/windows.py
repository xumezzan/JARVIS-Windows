"""Portable desktop schemas with legacy windows.* names. No native imports here."""

import ipaddress
import json
import socket
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated, Literal, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, StringConstraints, field_validator, model_validator

from jarvis.permissions.interaction import requires_confirmation
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

MAX_REPLY_BYTES = 2_500_000
MAX_SCREENSHOT_JPEG_BYTES = 48 * 1024
MAX_SCREENSHOT_BASE64 = 4 * ((MAX_SCREENSHOT_JPEG_BYTES + 2) // 3)
MAX_UIA_ELEMENTS = 96
MAX_UIA_DEPTH = 32
MAX_ELEMENT_NAME = 120
MAX_UIA_OBSERVATION_BYTES = 67000
# Names and absolute application paths are resolved by the OS catalog, never a shell.
AppQuery = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]


def normalize_newlines(text: str) -> str:
    """Equate Windows CRLF and RichEdit CR paragraphs without dropping any line."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_digest(text: str) -> str:
    return sha256(normalize_newlines(text).encode("utf-8")).hexdigest()


def clipboard_digest(text: str) -> str:
    """Bind the exact clipboard text, including line endings."""
    return sha256(text.encode("utf-8")).hexdigest()


class EditorTarget(ToolModel):
    """Identity of one text field inside an already exactly identified window."""

    runtime_id: list[int] = Field(min_length=1, max_length=32)
    control_type: Literal["Edit", "Document"]
    automation_id: str = Field(max_length=500)
    class_name: str = Field(max_length=200)
    handle: int = Field(ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
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
    app: str = Field(min_length=1, max_length=200)
    handle: int = Field(gt=0)
    pid: int = Field(gt=0)
    process_started: float = Field(gt=0, allow_inf_nan=False)
    executable: str = Field(min_length=1, max_length=1000)
    runtime_id: list[int] = Field(min_length=1, max_length=32)
    title: str = Field(max_length=1000)
    editor: EditorTarget | None = None
    empty: bool = False

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.app,
            self.handle,
            self.pid,
            self.process_started,
            self.executable,
        )


def window_matches(target: WindowTarget, query: str) -> bool:
    """Human search only; subsequent actions still bind an exact observed identity."""
    query = query.casefold().strip()
    if query.endswith(".exe"):
        query = query[:-4]
    return bool(query) and (query in target.app.casefold() or query in target.title.casefold())


class ListWindows(ToolModel):
    app: AppQuery | None = Field(
        default=None,
        description=(
            "Any partial process name or window title. "
            "Use null to list all open windows. Results contain exact identities."
        ),
    )


class OpenApp(ToolModel):
    app: AppQuery = Field(
        validation_alias=AliasChoices("app", "query"),
        description=(
            "Any installed application's name, shortcut name or absolute application path. "
            "The host OS resolves the name and returns an observed window target. "
            "Do not supply command-line arguments or shell code."
        ),
    )


class OpenChromeUrl(ToolModel):
    service: Literal["external-chrome"] = "external-chrome"
    account: Literal["current-profile"] = "current-profile"
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def canonical_destination(cls, value: str) -> str:
        # Canonicalize before the immutable audited action snapshot is created.
        return normalize_chrome_url(value)


class ChromeHandoff(ToolModel):
    url: str = Field(min_length=1, max_length=2000)
    submitted: bool
    navigation_verified: Literal[False] = False


def chrome_url(value: str) -> str:
    """Web URL syntax only. The owner delegates site and network handling to Chrome."""
    try:
        if (
            not value
            or len(value) > 2000
            or "\\" in value
            or any(
                ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
                for char in value
            )
        ):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or any(char.isspace() for char in parts.netloc)
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if "%" in host:
            raise ValueError  # Encoded host characters have browser-specific normalization.
        host = host.rstrip(".")
        if host.rstrip(".") == "localhost" or host.rstrip(".").endswith(".localhost"):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Browsers also accept abbreviated/octal/hex IPv4, e.g. 127.1.
            # inet_aton parses literals locally and never resolves a hostname.
            try:
                address = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                address = None
        if address is not None and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
        ):
            raise ValueError
        port = parts.port
        if port is not None and port <= 0:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            authority += f":{port}"
        safe = "/:@!$&'()*+,;=-._~%"
        canonical = urlunsplit(
            (
                parts.scheme,
                authority,
                quote(parts.path or "/", safe=safe),
                quote(parts.query, safe=safe + "?"),
                quote(parts.fragment, safe=safe + "?"),
            )
        )
        if len(canonical) > 2000:
            raise ValueError
        return canonical
    except (ValueError, UnicodeError):
        raise ToolError("network_denied") from None


def normalize_chrome_url(value: str) -> str:
    """Normalize a literal web address; semantic destinations belong to the planner."""
    value = value.strip()
    candidate = value if "://" in value else "https://" + value
    return chrome_url(candidate)


class FocusApp(ToolModel):
    target: WindowTarget | None = None
    handle: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    process: AppQuery | None = None

    @model_validator(mode="after")
    def one_selector(self) -> "FocusApp":
        if (
            sum(value is not None for value in (self.target, self.handle, self.title, self.process))
            != 1
        ):
            raise ValueError("Supply exactly one window selector.")
        if self.title is not None and not self.title.strip():
            raise ValueError("Empty title selector.")
        return self


class ListFields(ToolModel):
    target: WindowTarget


class TypeText(ToolModel):
    service: Literal["windows-desktop"] = "windows-desktop"
    action_type: Literal["fill_text_field", "set_editor_text"] = "fill_text_field"
    target: WindowTarget | None = None
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
    def field(self) -> EditorTarget | None:
        return self.editor or (self.target.editor if self.target is not None else None)


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
    control_type: Literal[
        "Button",
        "Edit",
        "Document",
        "TabItem",
        "ListItem",
        "Hyperlink",
        "RadioButton",
        "CheckBox",
        "ComboBox",
    ]
    automation_id: str = Field(max_length=500)
    rectangle: Rectangle


class InspectActiveWindow(ToolModel):
    max_depth: int = Field(default=MAX_UIA_DEPTH, ge=1, le=MAX_UIA_DEPTH)


class ClipboardGet(ToolModel):
    pass


class ClipboardSet(ToolModel):
    text: str = Field(max_length=4000)

    @field_validator("text")
    @classmethod
    def literal_text(cls, value: str) -> str:
        return TypeText.literal_text(value)


class SendHotkey(ToolModel):
    target: WindowTarget | None = None
    hotkey: str = Field(min_length=1, max_length=80)
    clipboard_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("hotkey")
    @classmethod
    def single_chord(cls, value: str) -> str:
        parts = value.casefold().split("+")
        modifiers = {
            "ctrl",
            "control",
            "cmd",
            "command",
            "meta",
            "super",
            "win",
            "alt",
            "option",
            "shift",
        }
        named = {
            "enter",
            "return",
            "tab",
            "escape",
            "esc",
            "backspace",
            "delete",
            "insert",
            "home",
            "end",
            "pageup",
            "pagedown",
            "up",
            "down",
            "left",
            "right",
            "arrowup",
            "arrowdown",
            "arrowleft",
            "arrowright",
            "space",
        }
        if not 1 <= len(parts) <= 5 or any(part not in modifiers for part in parts[:-1]):
            raise ValueError("Supply modifiers and one key, never executable key syntax.")
        key = parts[-1]
        if not (
            (len(key) == 1 and key.isascii() and key.isalnum())
            or key in named
            or (key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24)
        ):
            raise ValueError("Unsupported key.")
        return value


class ClickElement(ToolModel):
    target: WindowTarget | None = None
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
        elif not coordinates:
            raise ValueError("Supply an element, mark or both coordinates.")
        return self


class Screenshot(ToolModel):
    scope: Literal["active_window", "screen"] = "active_window"


class WindowsResult(ToolModel):
    windows: list[WindowTarget] = Field(default_factory=list, max_length=100)
    editors: list[EditorTarget] = Field(default_factory=list, max_length=20)
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
    inserted_text_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )
    text_verified: bool = False


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
    "check_chrome",
    "open_chrome_url",
    "check_target",
    "verify",
    "inspect",
    "clipboard_get",
    "clipboard_set",
    "hotkey",
    "click",
    "screenshot",
    "fields",
]


class NativeRequest(ToolModel):
    operation: Operation
    app: AppQuery | None = None
    url: str | None = Field(default=None, max_length=2000)
    target: WindowTarget | None = None
    editor: EditorTarget | None = None
    text: str | None = Field(default=None, max_length=4000)
    overwrite: bool = False
    result: WindowsResult | None = None
    handle: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    process: AppQuery | None = None
    max_depth: int = Field(default=MAX_UIA_DEPTH, ge=1, le=MAX_UIA_DEPTH)
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
    def chrome_policy(args: OpenChromeUrl) -> None:
        if chrome_url(args.url) != args.url:
            raise ToolError("network_denied")

    async def chrome_check(args: OpenChromeUrl, context: ExecutionContext) -> bool:
        await backend.call(NativeRequest(operation="check_chrome"), context)
        return True

    async def chrome_run(args: OpenChromeUrl, context: ExecutionContext) -> ChromeHandoff:
        await context.checkpoint()
        receipt = await backend.call(
            NativeRequest(operation="open_chrome_url", url=args.url), context
        )
        return ChromeHandoff(url=receipt.text or "", submitted=receipt.performed)

    async def chrome_verify(
        args: OpenChromeUrl, result: ChromeHandoff, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        # This verifies the handoff receipt, not page load, redirects or page contents.
        return result.submitted and result.url == args.url and not result.navigation_verified

    registry.register(
        ToolSpec(
            "windows.open_chrome_url",
            "Открыть любой HTTP/HTTPS-сайт в обычном Chrome, в новой вкладке текущего профиля. "
            "Без подтверждения, списка разрешённых сайтов и проверок сети Jarvis. "
            "Локальные/private IP и localhost запрещены; использует текущий профиль. "
            "Не читает вкладки и не проверяет загрузку страницы. "
            "После передачи сетью и перенаправлениями управляет Chrome, не Strict Sandbox.",
            # Owner's explicit policy on 2026-10-26: opening a website is automatic.
            Risk.SAFE,
            OpenChromeUrl,
            ChromeHandoff,
            chrome_check,
            chrome_run,
            chrome_verify,
            timeout_seconds=30,
            policy=chrome_policy,
        )
    )

    async def list_check(args: ListWindows, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def list_run(args: ListWindows, context: ExecutionContext) -> WindowsResult:
        return await backend.call(NativeRequest(operation="list", app=args.app), context)

    async def list_verify(
        args: ListWindows, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return all(args.app is None or window_matches(item, args.app) for item in result.windows)

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
        if args.target is not None and result.target.identity != args.target.identity:
            return False
        if args.handle is not None and result.target.handle != args.handle:
            return False
        if args.title is not None and args.title.casefold() not in result.target.title.casefold():
            return False
        if args.process is not None and not window_matches(result.target, args.process):
            return False
        checked = await backend.call(
            NativeRequest(operation="verify", target=result.target, result=result), context
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
        if result.target is None or (
            args.target is not None and result.target.identity != args.target.identity
        ):
            return False
        if result.text_sha256 is None:
            # A physical paste has an execution receipt, but cannot prove document text.
            return result.performed and result.inserted_text_sha256 == text_digest(args.text)
        if (
            args.overwrite
            or (args.target is not None and args.target.empty)
            or (args.field is not None and args.field.content_sha256 == text_digest(""))
        ):
            if result.text_sha256 != text_digest(args.text):
                return False
        elif result.inserted_text_sha256 != text_digest(args.text):
            return False
        checked = await backend.call(
            NativeRequest(
                operation="verify", target=result.target, editor=args.editor, result=result
            ),
            context,
        )
        return checked.text_sha256 == result.text_sha256

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
            "Ввести текст в поле или активное окно; UIA/AX, затем вставка через буфер. "
            "overwrite=true заменяет содержимое. Проверяйте результат новым наблюдением.",
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
                return (
                    result.performed
                    and result.target is not None
                    and (args.target is None or result.target.identity == args.target.identity)
                )
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

    def click_risk(args: ToolModel) -> Risk:
        assert isinstance(args, ClickElement)
        if args.element is None:
            return Risk.SAFE  # Owner authorizes ordinary coordinate and custom-control input.
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
        Risk.SAFE,
        "Отправить комбинацию клавиш в указанное или текущее активное окно. "
        "Комбинация содержит модификаторы и одну клавишу; текст вводится type_text.",
    )
    register_operation(
        "windows.click_element",
        ClickElement,
        "click",
        Risk.SAFE,
        "Активировать указанное или текущее окно и нажать UIA/AX-элемент "
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
