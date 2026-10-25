"""Strict browser contracts. Page observations are data, never approval authority."""

from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import Field, create_model, model_validator

from jarvis.permissions.interaction import requires_confirmation
from jarvis.permissions.policies import Risk
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy, origin
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
    has_unsaved_changes: bool = False


class ElementTarget(ToolModel):
    role: Literal["link", "button", "textbox", "searchbox"]
    name: str = Field(min_length=1, max_length=300)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    value: str = Field(default="", max_length=4000)
    request: RequestIntent | None = None


class BrowserArgs(ToolModel):
    service: Literal["isolated-browser", "live-browser"] = "isolated-browser"
    account: Literal["anonymous", "chrome-context"] = "anonymous"


class LiveBrowserArgs(BrowserArgs):
    service: Literal["live-browser"] = "live-browser"
    account: Literal["chrome-context"] = "chrome-context"


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
        if self.element.role not in ("link", "button"):
            raise ValueError("Only observed links and buttons are supported.")
        if self.element.role == "link" and self.element.request is None:
            raise ValueError("Links require an observed destination.")
        return self


class TypeElement(ReadPage):
    element: ElementTarget
    text: str = Field(max_length=4000)

    @model_validator(mode="after")
    def editable(self) -> "TypeElement":
        if self.element.role not in ("textbox", "searchbox") or self.element.request is not None:
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


def browser_schemas(policy: NetworkPolicy) -> dict[Operation, type[BrowserArgs]]:
    if policy.mode is BrowserMode.STRICT:
        return SCHEMAS
    return {
        operation: create_model(
            "Live" + parameters.__name__,
            __base__=(LiveBrowserArgs, parameters),
        )
        for operation, parameters in SCHEMAS.items()
    }


class BrowserBackend(Protocol):
    async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult: ...


def register_browser(
    registry: ToolRegistry, backend: BrowserBackend, policy: NetworkPolicy | None = None
) -> None:
    network_policy = policy or NetworkPolicy()

    def validate_policy(args: BrowserArgs) -> None:
        if (args.service, args.account) != (
            ("isolated-browser", "anonymous")
            if network_policy.mode is BrowserMode.STRICT
            else ("live-browser", "chrome-context")
        ):
            raise ToolError("network_denied")
        if isinstance(args, (OpenPage, NavigatePage, SearchWeb)):
            network_policy.validate_request(args.url, "GET")
        if isinstance(args, ReadPage):
            network_policy.validate(args.target.url)
            if args.target.origin != origin(args.target.url):
                raise ToolError("network_denied")
        if isinstance(args, ClickElement):
            intent = args.element.request
            if intent is not None:
                network_policy.validate_request(intent.url, intent.method, intent.body)
            elif network_policy.mode is BrowserMode.STRICT:
                raise ToolError("network_denied")

    def register(operation: Operation, parameters: type[BrowserArgs]) -> None:
        def classify_risk(args: BrowserArgs) -> Risk:
            if isinstance(args, ClickElement) and (
                args.element.request is None
                or args.element.request.method != "GET"
                or requires_confirmation(args.element.name)
                or (network_policy.mode is BrowserMode.LIVE and args.element.role == "button")
            ):
                return Risk.CONFIRM
            if (
                operation == "close"
                and isinstance(args, ReadPage)
                and args.target.has_unsaved_changes
            ):
                return Risk.CONFIRM
            return Risk.SAFE

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
                network_policy.mode is BrowserMode.STRICT
                and operation in ("open", "navigate", "search", "click")
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
                (
                    "Strict Sandbox: "
                    if network_policy.mode is BrowserMode.STRICT
                    else "Live Browser (Chrome context): "
                )
                + operation,
                Risk.CONFIRM if operation in ("click", "close") else Risk.SAFE,
                parameters,
                BrowserResult,
                check,
                run,
                verify,
                timeout_seconds=45,
                cancellation="Cancel the current Playwright/HTTP operation; no rollback.",
                policy=validate_policy,
                classify_risk=classify_risk,
            )
        )

    for operation, parameters in browser_schemas(network_policy).items():
        register(operation, parameters)
