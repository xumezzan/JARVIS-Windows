"""Serialized Strict Sandbox or live browser context, optionally attached over CDP."""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright

from jarvis.browser.inspection import INSPECT
from jarvis.browser.live import LiveNetworkGuard
from jarvis.browser.network import fetch
from jarvis.security.browser_policy import (
    BrowserMode,
    NetworkPolicy,
    normalized_url,
    origin,
    validate_cdp_endpoint,
)
from jarvis.tools.base import ToolError
from jarvis.tools.browser import (
    BrowserCommand,
    BrowserResult,
    ClickElement,
    ElementTarget,
    NavigatePage,
    OpenPage,
    PageTarget,
    PageView,
    ReadPage,
    RequestIntent,
    SearchWeb,
    TypeElement,
    browser_schemas,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Grant:
    page: Page
    intent: RequestIntent
    used: bool = False
    response_status: int | None = None
    error: ToolError | None = None


class BrowserSession:
    def __init__(
        self, policy: NetworkPolicy, *, headless: bool = True, cdp_endpoint: str = ""
    ) -> None:
        self.policy = policy
        self.headless = headless
        self.cdp_endpoint = validate_cdp_endpoint(cdp_endpoint) if cdp_endpoint else ""
        if self.cdp_endpoint and policy.mode is not BrowserMode.LIVE:
            raise ValueError("CDP requires Live Browser mode.")
        self.live = policy.mode is BrowserMode.LIVE
        self.schemas = browser_schemas(policy)
        self.network_guard = LiveNetworkGuard(policy) if self.live else None
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.pages: dict[str, Page] = {}
        self.documents: dict[str, str] = {}
        self.grant: Grant | None = None
        self._new_page = False
        self._auxiliary: set[asyncio.Task[None]] = set()
        self._routes: set[asyncio.Task[Any]] = set()
        self._loading_page: Page | None = None
        self._popup_failure = False

    async def start(self) -> None:
        if self.context is not None:
            return
        try:
            self.playwright = await async_playwright().start()
            if self.cdp_endpoint:
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    self.cdp_endpoint, timeout=10000
                )
                if not self.browser.contexts:
                    raise ToolError("browser_unavailable")
                self.context = self.browser.contexts[0]
                await self.refresh_tabs()
                return
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-domain-reliability",
                    "--no-pings",
                ],
            )
            self.context = await self.browser.new_context(
                java_script_enabled=self.live,
                service_workers="block",
                accept_downloads=False,
                permissions=[],
                ignore_https_errors=False,
                offline=not self.live,
            )
            self.context.set_default_timeout(5000)
            self.context.set_default_navigation_timeout(10000)
            if not self.live:
                await self.context.route("**/*", self.route)
                self.context.on("page", self._page_opened)
        except Exception:
            await self.close()
            raise ToolError("browser_unavailable") from None

    async def adopt(self, page: Page) -> str:
        if self.network_guard is not None:
            await self.network_guard.attach(page)
        tab_id = uuid4().hex
        self.pages[tab_id] = page
        self.documents[tab_id] = uuid4().hex
        page.on("framenavigated", lambda frame: self._navigated(tab_id, page, frame))
        return tab_id

    async def refresh_tabs(self) -> None:
        for key, page in list(self.pages.items()):
            if page.is_closed():
                self.pages.pop(key)
                self.documents.pop(key)
                if self.network_guard is not None:
                    await self.network_guard.detach(page)
        if self.cdp_endpoint and self.context is not None:
            for page in self.context.pages:
                if page in self.pages.values() or page.is_closed() or len(self.pages) >= 8:
                    continue
                try:
                    self.policy.validate(page.url)
                except ToolError:
                    continue  # Chrome internals/blank/private tabs are never tool targets.
                await self.adopt(page)

    def _page_opened(self, page: Page) -> None:
        if self._new_page:
            return

        async def close_popup() -> None:
            try:
                async with asyncio.timeout(3):
                    await page.close()
            except Exception:
                self._popup_failure = True

        task = asyncio.create_task(close_popup())
        self._auxiliary.add(task)
        task.add_done_callback(self._auxiliary.discard)

    async def route(self, route: Route) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._routes.add(task)
        try:
            await self._route(route)
        finally:
            if task is not None:
                self._routes.discard(task)

    async def _route(self, route: Route) -> None:
        request = route.request
        grant = self.grant
        try:
            if (
                grant is None
                or grant.used
                or request.frame != grant.page.main_frame
                or not request.is_navigation_request()
            ):
                await route.abort()
                return
            observed = RequestIntent.model_validate(
                {
                    "url": normalized_url(request.url),
                    "method": request.method,
                    "body": request.post_data or "",
                }
            )
            if (observed.url, observed.method, observed.body) != (
                grant.intent.url,
                grant.intent.method,
                grant.intent.body,
            ):
                raise ToolError("network_denied")
            grant.used = True  # Before any network byte; never retry a consumed grant.
            response = await fetch(grant.intent, self.policy)
            grant.response_status = response.status
            await route.fulfill(
                status=response.status,
                body=response.body,
                headers={
                    "content-type": response.content_type,
                    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'none'; frame-src 'none'; base-uri 'none'; object-src 'none'",
                    "referrer-policy": "no-referrer",
                    "cache-control": "no-store",
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if grant is not None:
                grant.error = (
                    error
                    if isinstance(error, ToolError)
                    else ToolError(
                        "browser_timeout" if isinstance(error, TimeoutError) else "browser_failure"
                    )
                )
            if grant is not None and grant.used:
                # Keep a stable, explicitly local error document after an issued request
                # fails. Chromium's aborted navigation can otherwise strand its renderer,
                # making even get_tabs/close impossible. The tool still returns the error.
                await route.fulfill(
                    status=502,
                    content_type="text/html; charset=utf-8",
                    headers={"content-security-policy": "default-src 'none'"},
                    body="<html><head><title>Jarvis: страница недоступна</title></head>"
                    "<body><h1>Локальное сообщение Jarvis</h1>"
                    "<p>Загрузка завершилась ошибкой. Это не содержимое сайта. "
                    "Выполните новый переход или закройте вкладку.</p></body></html>",
                )
            else:
                await route.abort()

    async def observe(self, tab_id: str) -> PageView:
        page = self.pages.get(tab_id)
        if page is None or page.is_closed():
            raise ToolError("page_changed")
        if self.network_guard is not None and page in self.network_guard.failures:
            raise ToolError("browser_failure")
        url = self.policy.validate(page.url)
        raw = cast(dict[str, Any], await page.evaluate(INSPECT, self.live))
        if not raw.get("material"):
            raise ToolError("browser_failure")
        elements = []
        size = 0
        for item in raw["elements"]:
            try:
                intent = None
                if item["request"] is not None:
                    intent = RequestIntent.model_validate(item["request"])
                    intent = intent.model_copy(
                        update={
                            "url": self.policy.validate_request(
                                intent.url, intent.method, intent.body
                            )
                        }
                    )
                elif item["role"] not in ("textbox", "searchbox") and not (
                    self.live and item["role"] == "button"
                ):
                    continue  # Ambiguous button, popup, or unsupported form.
                element = ElementTarget(
                    role=item["role"],
                    name=item["name"],
                    fingerprint=digest(item["html"]),
                    value=item["value"],
                    request=intent,
                )
                locator = page.get_by_role(element.role, name=element.name, exact=True)
                if await locator.count() != 1:
                    continue
                size += len(element.model_dump_json())
                if size > 40000:
                    break
                elements.append(element)
            except (ToolError, ValueError):
                continue
        return PageView(
            target=PageTarget(
                tab_id=tab_id,
                document_id=self.documents[tab_id],
                url=url,
                origin=origin(url),
                fingerprint=digest(raw["material"]),
                has_unsaved_changes=raw["has_unsaved_changes"],
            ),
            title=raw["title"],
            text=raw["text"],
            elements=elements,
        )

    async def resolve(self, target: PageTarget) -> PageView:
        current = await self.observe(target.tab_id)
        if current.target != target:
            raise ToolError("page_changed")
        return current

    async def check_element(self, args: ClickElement | TypeElement) -> None:
        current = await self.resolve(args.target)
        if args.element not in current.elements:
            raise ToolError("page_changed")

    async def highlight_click(self, args: ClickElement) -> None:
        """Use viewport coordinates inside the browser, avoiding guessed browser chrome/DPI."""
        if self.headless:
            return
        page = self.pages[args.target.tab_id]
        await page.bring_to_front()
        locator = page.get_by_role(args.element.role, name=args.element.name, exact=True)
        await locator.scroll_into_view_if_needed(timeout=5000)
        await locator.click(trial=True, timeout=5000)
        await self.check_element(args)
        bounds = await locator.bounding_box()
        if bounds is None:
            raise ToolError("page_changed")
        marker = await locator.evaluate_handle("""element => {
            const r = element.getBoundingClientRect();
            const marker = document.createElement('div');
            marker.setAttribute('aria-hidden', 'true');
            Object.assign(marker.style, {
                position: 'fixed', left: `${r.x}px`, top: `${r.y}px`,
                width: `${r.width}px`, height: `${r.height}px`,
                pointerEvents: 'none', zIndex: '2147483647', boxSizing: 'border-box',
                border: '2px solid rgba(101,180,255,.9)', borderRadius: '5px',
                background: 'rgba(63,123,255,.08)', boxShadow: 'inset 0 0 10px rgba(63,123,255,.5)'
            });
            document.documentElement.append(marker);
            return marker;
        }""")
        try:
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
            await asyncio.sleep(0.3)
        finally:
            try:
                await marker.evaluate("element => element.remove()")
            finally:
                await marker.dispose()
        await self.check_element(args)
        if await locator.bounding_box() != bounds:
            raise ToolError("page_changed")

    async def navigate(
        self, page: Page, intent: RequestIntent, *, click: ElementTarget | None = None
    ) -> int | None:
        intent = intent.model_copy(update={"url": self.policy.validate(intent.url)})
        if self.live:
            self._loading_page = page
            previous = self.policy.validate(page.url) if page.url != "about:blank" else ""
            response = await page.goto(intent.url, wait_until="domcontentloaded", timeout=10000)
            self._loading_page = None
            current = self.policy.validate(page.url)
            if response is None:
                if current == intent.url and previous.split("#")[0] == current.split("#")[0]:
                    return None  # Same-document SPA navigation, no fabricated HTTP status.
                raise ToolError("browser_failure")
            if not 200 <= response.status < 300:
                raise ToolError("browser_failure")
            return response.status
        grant = Grant(page, intent)
        self._loading_page = page
        self.grant = grant
        try:
            if click is None:
                await page.goto(intent.url, wait_until="domcontentloaded")
            else:
                async with page.expect_navigation(wait_until="domcontentloaded"):
                    await page.get_by_role(click.role, name=click.name, exact=True).click()
            if grant.error is not None:
                raise grant.error
            if (
                not grant.used
                or grant.response_status is None
                or normalized_url(page.url) != intent.url
            ):
                raise ToolError("browser_failure")
            self._loading_page = None
            return grant.response_status
        except Exception:
            if grant.error is not None:
                # The routed request has already been aborted and goto has settled.
                # Its renderer may be swapping to an error document; there is no load to stop.
                self._loading_page = None
                raise grant.error from None
            raise
        finally:
            self.grant = None

    async def dispatch(self, command: BrowserCommand) -> BrowserResult:
        if self._popup_failure:
            raise ToolError("browser_cleanup")
        args = self.schemas[command.operation].model_validate_json(command.parameters)
        if command.phase == "verify":
            result = command.result
            if result is None:
                raise ToolError("browser_failure")
            if result.page is not None:
                if (
                    not self.live
                    and isinstance(args, (OpenPage, NavigatePage, SearchWeb))
                    and result.page.target.url != self.policy.validate(args.url)
                ):
                    raise ToolError("browser_failure")
                if not self.live and isinstance(args, ClickElement):
                    assert args.element.request is not None
                    if result.page.target.url != self.policy.validate(args.element.request.url):
                        raise ToolError("browser_failure")
                current = await self.resolve(result.page.target)
                if current != result.page:
                    raise ToolError("page_changed")
            if command.operation == "close":
                assert isinstance(args, ReadPage)
                if result.closed_tab != args.target.tab_id or args.target.tab_id in self.pages:
                    raise ToolError("browser_failure")
            if command.operation == "type":
                assert isinstance(args, TypeElement)
                page = self.pages[args.target.tab_id]
                locator = page.get_by_role(args.element.role, name=args.element.name, exact=True)
                value = (
                    await locator.inner_text()
                    if await locator.get_attribute("contenteditable") == "true"
                    else await locator.input_value()
                )
                if value != args.text:
                    raise ToolError("browser_failure")
            if command.operation == "get_tabs":
                tabs = [(await self.observe(key)).target for key in self.pages]
                if tabs != result.tabs:
                    raise ToolError("page_changed")
            return result
        if isinstance(args, (OpenPage, NavigatePage, SearchWeb)):
            self.policy.validate(args.url)
        if isinstance(args, ReadPage):
            await self.resolve(args.target)
        if isinstance(args, (ClickElement, TypeElement)):
            await self.check_element(args)
        if command.phase == "check":
            return BrowserResult()
        match command.operation:
            case "get_tabs":
                if self.cdp_endpoint:
                    await self.start()
                    await self.refresh_tabs()
                return BrowserResult(tabs=[(await self.observe(key)).target for key in self.pages])
            case "open" | "search":
                assert isinstance(args, (OpenPage, SearchWeb))
                await self.start()
                if len(self.pages) >= 8:
                    raise ToolError("browser_failure")
                assert self.context is not None
                self._new_page = True
                try:
                    page = await self.context.new_page()
                finally:
                    self._new_page = False
                tab_id = ""
                try:
                    tab_id = await self.adopt(page)
                    if self.live:
                        await page.bring_to_front()
                    status = await self.navigate(page, RequestIntent(url=args.url))
                    return BrowserResult(page=await self.observe(tab_id), response_status=status)
                except BaseException:
                    await self.cancel_pending()
                    await page.close()
                    if self.network_guard is not None:
                        await self.network_guard.detach(page)
                    self.pages.pop(tab_id, None)
                    self.documents.pop(tab_id, None)
                    raise
            case "navigate":
                assert isinstance(args, NavigatePage)
                status = await self.navigate(
                    self.pages[args.target.tab_id], RequestIntent(url=args.url)
                )
                return BrowserResult(
                    page=await self.observe(args.target.tab_id), response_status=status
                )
            case "click":
                assert isinstance(args, ClickElement)
                await self.highlight_click(args)
                if self.live:
                    page = self.pages[args.target.tab_id]
                    await page.get_by_role(
                        args.element.role, name=args.element.name, exact=True
                    ).click(timeout=5000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    return BrowserResult(page=await self.observe(args.target.tab_id))
                assert args.element.request is not None
                status = await self.navigate(
                    self.pages[args.target.tab_id], args.element.request, click=args.element
                )
                return BrowserResult(
                    page=await self.observe(args.target.tab_id), response_status=status
                )
            case "type":
                assert isinstance(args, TypeElement)
                page = self.pages[args.target.tab_id]
                await page.get_by_role(args.element.role, name=args.element.name, exact=True).fill(
                    args.text
                )
                return BrowserResult(page=await self.observe(args.target.tab_id))
            case "read":
                assert isinstance(args, ReadPage)
                return BrowserResult(page=await self.observe(args.target.tab_id))
            case "close":
                assert isinstance(args, ReadPage)
                await self.pages[args.target.tab_id].close(run_before_unload=False)
                if self.network_guard is not None:
                    await self.network_guard.detach(self.pages[args.target.tab_id])
                self.pages.pop(args.target.tab_id)
                self.documents.pop(args.target.tab_id)
                return BrowserResult(closed_tab=args.target.tab_id)
        raise ToolError("browser_failure")

    def _navigated(self, tab_id: str, page: Page, frame: Any) -> None:
        if frame == page.main_frame and tab_id in self.pages:
            self.documents[tab_id] = uuid4().hex

    async def cancel_pending(self) -> None:
        self.grant = None
        routes = list(self._routes)
        for task in routes:
            task.cancel()
        await asyncio.gather(*routes, return_exceptions=True)
        page = self._loading_page
        self._loading_page = None
        if page is not None and not page.is_closed():
            try:
                async with asyncio.timeout(2):
                    connection = await page.context.new_cdp_session(page)
                    try:
                        await connection.send("Page.stopLoading")
                    finally:
                        await connection.detach()
            except Exception:
                raise ToolError("browser_cleanup") from None

    async def close(self) -> None:
        failed = False
        try:
            await self.cancel_pending()
        except Exception:
            failed = True
        if self.network_guard is not None:
            try:
                await self.network_guard.close()
            except Exception:
                failed = True
        for resource in () if self.cdp_endpoint else (self.context, self.browser):
            if resource is not None:
                try:
                    async with asyncio.timeout(3):
                        await resource.close()
                except Exception:
                    failed = True
        if self.playwright is not None:
            try:
                async with asyncio.timeout(3):
                    await self.playwright.stop()
            except Exception:
                failed = True
        for task in self._auxiliary:
            task.cancel()
        await asyncio.gather(*self._auxiliary, return_exceptions=True)
        self.pages.clear()
        self.documents.clear()
        self.context = None
        self.browser = None
        self.playwright = None
        self._popup_failure = False
        if failed:
            raise ToolError("browser_cleanup")
