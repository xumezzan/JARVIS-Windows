"""Strict browser contracts. Page observations are data, never approval authority."""

from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import Field, model_validator

from jarvis.permissions.policies import Risk
from jarvis.security.browser_policy import NetworkPolicy, origin
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec, canonical
from jarvis.tools.registry import ToolRegistry

Operation = Literal["open", "navigate", "search", "click", "type", "read", "get_tabs", "close"]


class FormValue(ToolModel):
    name: str = Field(min_length=1, max_length=300)
    value: str = Field(max_length=4000)


class RequestIntent(ToolModel):
    url: str = Field(min_length=1, max_length=6000)
    method: Literal["GET", "POST"] = "GET"
    body: str = Field(default="", max_length=12000)
    fields: list[FormValue] = Field(default_factory=list, max_length=21)


class PageTarget(ToolModel):
    tab_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    document_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    url: str = Field(min_length=1, max_length=6000)
    origin: str = Field(min_length=1, max_length=1000)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame: Literal["main"] = "main"


class ElementTarget(ToolModel):
    role: Literal["link", "button", "textbox"]
    name: str = Field(min_length=1, max_length=300)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    value: str = Field(default="", max_length=4000)
    request: RequestIntent | None = None


class BrowserArgs(ToolModel):
    service: Literal["isolated-browser"] = "isolated-browser"
    account: Literal["anonymous"] = "anonymous"


class OpenPage(BrowserArgs):
    url: str = Field(min_length=1, max_length=6000)


class ReadPage(BrowserArgs):
    target: PageTarget


class NavigatePage(ReadPage):
    url: str = Field(min_length=1, max_length=6000)


class SearchWeb(BrowserArgs):
    query: str = Field(min_length=1, max_length=500)
    url: str = ""

    @model_validator(mode="after")
    def exact_destination(self) -> "SearchWeb":
        url = "https://html.duckduckgo.com/html/?" + urlencode({"q": self.query})
        if self.url and self.url != url:
            raise ValueError("Search destination changed.")
        object.__setattr__(self, "url", url)
        return self


class ClickElement(ReadPage):
    element: ElementTarget

    @model_validator(mode="after")
    def actionable(self) -> "ClickElement":
        if self.element.role not in ("link", "button") or self.element.request is None:
            raise ValueError("Only observed links and explicit form submissions are supported.")
        return self


class TypeElement(ReadPage):
    element: ElementTarget
    text: str = Field(max_length=4000)

    @model_validator(mode="after")
    def editable(self) -> "TypeElement":
        if self.element.role != "textbox" or self.element.request is not None:
            raise ValueError("Only observed plain text fields are supported.")
        if "\x00" in self.text or any(0xD800 <= ord(char) <= 0xDFFF for char in self.text):
            raise ValueError("Invalid text.")
        return self


class PageView(ToolModel):
    target: PageTarget
    title: str = Field(max_length=500)
    text: str = Field(max_length=12000)
    elements: list[ElementTarget] = Field(default_factory=list, max_length=50)


class BrowserResult(ToolModel):
    page: PageView | None = None
    tabs: list[PageTarget] = Field(default_factory=list, max_length=8)
    closed_tab: str | None = None
    response_status: int | None = Field(default=None, ge=200, le=299)
    observed_only: Literal[True] = True


class BrowserCommand(ToolModel):
    operation: Operation
    phase: Literal["check", "run", "verify"]
    parameters: str = Field(max_length=65536)
    result: BrowserResult | None = None


SCHEMAS: dict[Operation, type[BrowserArgs]] = {
    "open": OpenPage,
    "navigate": NavigatePage,
    "search": SearchWeb,
    "click": ClickElement,
    "type": TypeElement,
    "read": ReadPage,
    "get_tabs": BrowserArgs,
    "close": ReadPage,
}


class BrowserBackend(Protocol):
    async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult: ...


def register_browser(
    registry: ToolRegistry, backend: BrowserBackend, policy: NetworkPolicy | None = None
) -> None:
    network_policy = policy or NetworkPolicy()

    def validate_policy(args: BrowserArgs) -> None:
        if isinstance(args, (OpenPage, NavigatePage, SearchWeb)):
            network_policy.validate_request(args.url, "GET")
        if isinstance(args, ReadPage):
            network_policy.validate(args.target.url)
            if args.target.origin != origin(args.target.url):
                raise ToolError("network_denied")
        if isinstance(args, ClickElement):
            assert args.element.request is not None
            intent = args.element.request
            network_policy.validate_request(intent.url, intent.method, intent.body)

    def register(operation: Operation, parameters: type[BrowserArgs]) -> None:
        async def check(args: BrowserArgs, context: ExecutionContext) -> bool:
            await backend.call(
                BrowserCommand(operation=operation, phase="check", parameters=canonical(args)),
                context,
            )
            return True

        async def run(args: BrowserArgs, context: ExecutionContext) -> BrowserResult:
            return await backend.call(
                BrowserCommand(operation=operation, phase="run", parameters=canonical(args)),
                context,
            )

        async def verify(
            args: BrowserArgs, result: BrowserResult, context: ExecutionContext
        ) -> bool:
            if operation not in ("get_tabs", "close") and result.page is None:
                return False
            if (
                operation in ("open", "navigate", "search", "click")
                and result.response_status is None
            ):
                return False
            if (
                isinstance(args, ReadPage)
                and operation != "close"
                and (result.page is None or result.page.target.tab_id != args.target.tab_id)
            ):
                return False
            if (
                operation == "close"
                and isinstance(args, ReadPage)
                and result.closed_tab != args.target.tab_id
            ):
                return False
            await backend.call(
                BrowserCommand(
                    operation=operation, phase="verify", parameters=canonical(args), result=result
                ),
                context,
            )
            return True

        registry.register(
            ToolSpec(
                "browser." + operation,
                "Изолированный браузер: " + operation,
                Risk.SAFE if operation in ("read", "get_tabs") else Risk.CONFIRM,
                parameters,
                BrowserResult,
                check,
                run,
                verify,
                timeout_seconds=45,
                cancellation="Cancel the current Playwright/HTTP operation; no rollback.",
                policy=validate_policy,
            )
        )

    for operation, parameters in SCHEMAS.items():
        register(operation, parameters)
