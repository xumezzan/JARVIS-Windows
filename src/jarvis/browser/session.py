"""Serialized Strict Sandbox or live browser context, optionally attached over CDP."""

import asyncio
import hashlib
import json
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    ElementHandle,
    JSHandle,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from jarvis.browser.inspection import INSPECT, LIVE_SELECTOR, SAFE_ELEMENT, STRICT_SELECTOR
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
    OpenWorkspaceApp,
    PageTarget,
    PageView,
    PressKey,
    ReadPage,
    RequestIntent,
    ScrollPage,
    SearchWeb,
    TriggerAutofill,
    TypeElement,
    browser_schemas,
)
from jarvis.workspace.models import WorkspaceProfile


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
        self,
        policy: NetworkPolicy,
        *,
        headless: bool = True,
        cdp_endpoint: str = "",
        profile_dir: Path | None = None,
        workspace: WorkspaceProfile | None = None,
    ) -> None:
        self.policy = policy
        self.headless = headless
        self.cdp_endpoint = validate_cdp_endpoint(cdp_endpoint) if cdp_endpoint else ""
        if self.cdp_endpoint and policy.mode is BrowserMode.STRICT:
            raise ValueError("CDP requires Live Browser mode.")
        if profile_dir is not None and (
            policy.mode is not BrowserMode.WORKSPACE or self.cdp_endpoint
        ):
            raise ValueError("A persistent profile requires owned Workspace Browser mode.")
        self.profile_dir = profile_dir
        self.workspace = WorkspaceProfile.model_validate(workspace or WorkspaceProfile())
        self.live = policy.mode is not BrowserMode.STRICT
        self.schemas = browser_schemas(policy)
        self.network_guard = LiveNetworkGuard(policy) if self.live else None
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.pages: dict[str, Page] = {}
        self.documents: dict[str, str] = {}
        self.observations: dict[str, PageView] = {}
        self._materials: dict[str, str] = {}
        self.nodes: dict[str, JSHandle] = {}
        self.typed: dict[str, ElementHandle] = {}
        self.grant: Grant | None = None
        self._new_page = False
        self._auxiliary: set[asyncio.Task[None]] = set()
        self._routes: set[asyncio.Task[Any]] = set()
        self._loading_page: Page | None = None
        self._popup_failure = False
        self._workspace_route_installed = False
        self._workspace_handler = self.workspace_route
        self._workspace_slots = asyncio.Semaphore(16)

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
                self.context.set_default_timeout(5000)
                self.context.set_default_navigation_timeout(10000)
                if self.policy.mode is BrowserMode.WORKSPACE:
                    await self.context.route("**/*", self._workspace_handler)
                    self._workspace_route_installed = True
                    self.context.on("page", self._page_opened)
                await self.refresh_tabs()
                return
            if self.policy.mode is BrowserMode.WORKSPACE:
                if (
                    self.profile_dir is None
                    or self.profile_dir.is_symlink()
                    or str(self.profile_dir).startswith(("//", "\\\\"))
                ):
                    raise ToolError("browser_unavailable")
                self.profile_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(self.profile_dir.resolve()),
                    headless=self.headless,
                    java_script_enabled=True,
                    service_workers="block",
                    accept_downloads=False,
                    permissions=[],
                    ignore_https_errors=False,
                    ignore_default_args=[]
                    if self.policy.fixture_origin
                    else ["--password-store=basic", "--use-mock-keychain"],
                    args=[
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--no-pings",
                    ],
                )
                self.browser = self.context.browser
                self.context.set_default_timeout(5000)
                self.context.set_default_navigation_timeout(10000)
                await self.context.route("**/*", self._workspace_handler)
                self._workspace_route_installed = True
                self.context.on("page", self._page_opened)
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
        existing = next((key for key, value in self.pages.items() if value is page), None)
        if existing is not None:
            return existing
        if self.network_guard is not None:
            await self.network_guard.attach(page)
        existing = next((key for key, value in self.pages.items() if value is page), None)
        if existing is not None:
            return existing
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
                await self._clear_nodes(key)
                if self.network_guard is not None:
                    await self.network_guard.detach(page)
        if (self.cdp_endpoint or self.profile_dir is not None) and self.context is not None:
            for page in self.context.pages:
                if (
                    page in self.pages.values()
                    or page.is_closed()
                    or len(self.pages) >= self.tab_limit
                ):
                    continue
                try:
                    self.policy.validate(page.url)
                except ToolError:
                    continue  # Chrome internals/blank/private tabs are never tool targets.
                await self.adopt(page)

    @property
    def tab_limit(self) -> int:
        return 32 if self.policy.mode is BrowserMode.WORKSPACE else 8

    async def _clear_nodes(self, tab_id: str) -> None:
        self.observations.pop(tab_id, None)
        self._materials.pop(tab_id, None)
        handle = self.nodes.pop(tab_id, None)
        if handle is not None:
            await handle.dispose()
        typed = self.typed.pop(tab_id, None)
        if typed is not None:
            await typed.dispose()

    def _page_opened(self, page: Page) -> None:
        if self._new_page:
            return

        async def close_popup() -> None:
            try:
                async with asyncio.timeout(3):
                    if self.policy.mode is BrowserMode.WORKSPACE:
                        assert self.network_guard is not None
                        await self.network_guard.attach(page)
                        try:
                            self.policy.validate(page.url)
                        except ToolError:
                            return
                        if len(self.pages) < self.tab_limit:
                            await self.adopt(page)
                        return
                    await page.close()
            except Exception:
                self._popup_failure = True

        task = asyncio.create_task(close_popup())
        self._auxiliary.add(task)
        task.add_done_callback(self._auxiliary.discard)

    async def workspace_route(self, route: Route) -> None:
        """Guard initial popup/subresource requests too; CDP Fetch checks redirect hops.

        In external Chrome, unrelated owner tabs retain their traffic. Only adopted
        pages and their popups are controlled; the browser process is not a firewall.
        """
        try:
            # Chromium may expose a popup's first request before its frame. Refuse
            # that request: without a page we cannot guard subsequent redirects.
            page = route.request.frame.page
            if (
                self.cdp_endpoint
                and page not in self.pages.values()
                and await page.opener() not in self.pages.values()
            ):
                await route.continue_()
                return
            async with self._workspace_slots:
                await self.policy.validate_live_request(route.request.url, route.request.method)
            assert self.network_guard is not None
            await self.network_guard.attach(page)
            await route.continue_()
        except Exception:
            await route.abort()

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

    async def observe(self, tab_id: str, *, publish: bool = True, fresh: bool = False) -> PageView:
        page = self.pages.get(tab_id)
        if page is None or page.is_closed():
            raise ToolError("page_changed")
        if self.network_guard is not None and page in self.network_guard.failures:
            raise ToolError("browser_failure")
        url = self.policy.validate(page.url)
        raw = cast(dict[str, Any], await page.evaluate(INSPECT, self.live))
        if not raw.get("material"):
            raise ToolError("browser_failure")
        elements: list[ElementTarget] = []
        indexes = []
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
                    self.live
                    and item["role"]
                    in ("button", "tab", "menuitem", "combobox", "option", "radio", "checkbox")
                ):
                    continue  # Ambiguous button, popup, or unsupported form.
                element = ElementTarget(
                    role=item["role"],
                    name=item["name"],
                    fingerprint=digest(item["html"]),
                    value=item["value"],
                    request=intent,
                    mark_id=len(elements) + 1,
                )
                size += len(element.model_dump_json().encode())
                if size > 40000:
                    break
                elements.append(element)
                indexes.append(item["index"])
            except (ToolError, ValueError):
                continue
        previous = self.observations.get(tab_id)
        fingerprint = digest(raw["material"])
        changed = previous is None or (
            previous.target.fingerprint != fingerprint
            or previous.target.document_id != self.documents[tab_id]
            or previous.target.url != url
        )
        observation_id = previous.target.observation_id if previous is not None else None
        if publish and (changed or fresh or self.policy.mode is BrowserMode.WORKSPACE):
            observation_id = uuid4().hex
        view = PageView(
            target=PageTarget(
                tab_id=tab_id,
                document_id=self.documents[tab_id],
                url=url,
                origin=origin(url),
                fingerprint=fingerprint,
                has_unsaved_changes=raw["has_unsaved_changes"],
                observation_id=observation_id,
            ),
            title=raw["title"],
            text=raw["text"],
            elements=elements,
        )
        if publish:
            selector = LIVE_SELECTOR if self.live else STRICT_SELECTOR
            handles = await page.evaluate_handle(
                "data => { const all = document.querySelectorAll(data.selector); "
                "return data.indexes.map(i => all[i]); }",
                {"selector": selector, "indexes": indexes},
            )
            old = self.nodes.pop(tab_id, None)
            if old is not None:
                await old.dispose()
            self.nodes[tab_id] = handles
            self.observations[tab_id] = view
            if self.live:
                self._materials[tab_id] = json.dumps(
                    json.loads(raw["material"])[:-1], separators=(",", ":"), ensure_ascii=False
                )
        return view

    async def resolve(self, target: PageTarget) -> PageView:
        current = await self.observe(target.tab_id, publish=False)
        if current.target != target:
            raise ToolError("page_changed")
        return current

    def readable_page(self, target: PageTarget) -> Page:
        """Refresh a known document even when its contents changed; never follow navigation."""
        page = self.pages.get(target.tab_id)
        if (
            page is None
            or page.is_closed()
            or self.documents.get(target.tab_id) != target.document_id
            or self.policy.validate(page.url) != target.url
            or origin(target.url) != target.origin
        ):
            raise ToolError("page_changed")
        return page

    async def wait_for_change(self, page: Page, target: PageTarget) -> None:
        """Wait for a real DOM/form change after JS input, with a finite no-op bound."""
        # Focus changing to the clicked control is not the requested JS result.
        with suppress(PlaywrightTimeoutError):
            await page.wait_for_function(
                "previous => JSON.stringify(JSON.parse(("
                + INSPECT
                + ")(true).material).slice(0, -1)) !== previous",
                arg=self._materials.get(target.tab_id, ""),
                timeout=1500,
            )

    async def check_element(self, args: ClickElement | TypeElement) -> None:
        current = await self.resolve(args.target)
        if args.element not in current.elements:
            raise ToolError("page_changed")
        handle = await self._element(args)
        await handle.dispose()

    async def _element(self, args: ClickElement | TypeElement) -> ElementHandle:
        assert args.element is not None
        nodes = self.nodes.get(args.target.tab_id)
        if nodes is None or args.element.mark_id is None:
            raise ToolError("page_changed")
        handle = await nodes.get_property(str(args.element.mark_id - 1))
        element = handle.as_element()
        if element is None or not await element.evaluate(SAFE_ELEMENT):
            await handle.dispose()
            raise ToolError("page_changed")
        return element

    async def highlight_click(self, args: ClickElement) -> None:
        """Use viewport coordinates inside the browser, avoiding guessed browser chrome/DPI."""
        if self.headless:
            return
        page = self.pages[args.target.tab_id]
        await page.bring_to_front()
        locator = await self._visual_element(args)
        try:
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
                    background: 'rgba(63,123,255,.08)',
                    boxShadow: 'inset 0 0 10px rgba(63,123,255,.5)'
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
        finally:
            await locator.dispose()

    async def _visual_element(self, args: ClickElement | TypeElement) -> ElementHandle:
        element = await self._element(args)
        if not await element.evaluate("el => el.tagName === 'OPTION'"):
            return element
        parent = await element.evaluate_handle("el => el.closest('select')")
        await element.dispose()
        select = parent.as_element()
        if select is None or not await select.evaluate(SAFE_ELEMENT):
            await parent.dispose()
            raise ToolError("page_changed")
        return select

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
                    assert args.element is not None and args.element.request is not None
                    if result.page.target.url != self.policy.validate(args.element.request.url):
                        raise ToolError("browser_failure")
                if self.live:
                    # A PageView is a point-in-time observation, not a promise that JS
                    # stops afterwards. Actions still recheck the exact snapshot.
                    self.readable_page(result.page.target)
                else:
                    current = await self.resolve(result.page.target)
                    if current != result.page:
                        raise ToolError("page_changed")
            if command.operation == "close":
                assert isinstance(args, ReadPage)
                if result.closed_tab != args.target.tab_id or args.target.tab_id in self.pages:
                    raise ToolError("browser_failure")
            if command.operation == "type":
                assert isinstance(args, TypeElement)
                handle = self.typed.pop(args.target.tab_id, None)
                if handle is None:
                    raise ToolError("browser_failure")
                try:
                    if not await handle.evaluate(SAFE_ELEMENT):
                        raise ToolError("page_changed")
                    value = (
                        await handle.inner_text()
                        if await handle.evaluate("el => el.isContentEditable")
                        else await handle.input_value()
                    )
                    if value != args.text:
                        raise ToolError("browser_failure")
                finally:
                    await handle.dispose()
            if command.operation == "trigger_1password_autofill":
                assert isinstance(args, TriggerAutofill)
                if not result.autofill_requested or result.affected_tab != args.target.tab_id:
                    raise ToolError("browser_failure")
            if command.operation == "get_tabs":
                if self.live:
                    for target in result.tabs:
                        self.readable_page(target)
                elif [(await self.observe(key, publish=False)).target for key in self.pages] != (
                    result.tabs
                ):
                    raise ToolError("page_changed")
            return result
        if isinstance(args, (OpenPage, NavigatePage, SearchWeb)):
            self.policy.validate(args.url)
        if command.operation == "read" and isinstance(args, ReadPage) and self.live:
            self.readable_page(args.target)
        elif isinstance(args, ReadPage):
            await self.resolve(args.target)
        if isinstance(args, (ClickElement, TypeElement)):
            await self.check_element(args)
        if command.phase == "check":
            return BrowserResult()
        match command.operation:
            case "get_tabs":
                if self.cdp_endpoint or self.profile_dir is not None:
                    await self.start()
                    await self.refresh_tabs()
                return BrowserResult(tabs=[(await self.observe(key)).target for key in self.pages])
            case "open" | "search" | "open_workspace_app":
                assert isinstance(args, (OpenPage, SearchWeb, OpenWorkspaceApp))
                url = args.url if isinstance(args, (OpenPage, SearchWeb)) else self._app_url(args)
                await self.start()
                await self.refresh_tabs()
                if isinstance(args, OpenWorkspaceApp):
                    existing = await self._app_tab(args)
                    if existing is not None:
                        key, page = existing
                        # Mail and Calendar share one origin, but have distinct launch paths.
                        path = urlsplit(url).path.rstrip("/")
                        current_path = urlsplit(page.url).path.rstrip("/")
                        needs_navigation = bool(path) and not (
                            current_path == path or current_path.startswith(path + "/")
                        )
                        unsaved = (
                            needs_navigation
                            and (await self.observe(key, publish=False)).target.has_unsaved_changes
                        )
                        if not unsaved:
                            await page.bring_to_front()
                            status = (
                                await self.navigate(page, RequestIntent(url=url))
                                if needs_navigation
                                else None
                            )
                            return BrowserResult(
                                page=await self.observe(key), response_status=status
                            )
                        # SAFE app switching must not discard a draft on another route.
                        # Keep that tab and open the requested application in a new one.
                if len(self.pages) >= self.tab_limit:
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
                    status = await self.navigate(page, RequestIntent(url=url))
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
                    handle = await self._element(args)
                    try:
                        if await handle.evaluate("el => el.tagName === 'OPTION'"):
                            parent = await handle.evaluate_handle("el => el.closest('select')")
                            try:
                                select = parent.as_element()
                                if select is None or not await select.evaluate(SAFE_ELEMENT):
                                    raise ToolError("page_changed")
                                await select.select_option(element=handle, timeout=5000)
                            finally:
                                await parent.dispose()
                        else:
                            await handle.click(timeout=5000)
                    finally:
                        await handle.dispose()
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await self.wait_for_change(page, args.target)
                    return BrowserResult(page=await self.observe(args.target.tab_id))
                assert args.element is not None and args.element.request is not None
                status = await self.navigate(
                    self.pages[args.target.tab_id], args.element.request, click=args.element
                )
                return BrowserResult(
                    page=await self.observe(args.target.tab_id), response_status=status
                )
            case "type":
                assert isinstance(args, TypeElement)
                handle = await self._element(args)
                try:
                    if not await handle.is_editable():
                        raise ToolError("page_changed")
                    # Playwright uses native text insertion and emits input events for
                    # contenteditable, including React/Lexical/ProseMirror hosts.
                    await handle.fill(args.text)
                except BaseException:
                    await handle.dispose()
                    raise
                old = self.typed.pop(args.target.tab_id, None)
                if old is not None:
                    await old.dispose()
                self.typed[args.target.tab_id] = handle
                return BrowserResult(page=await self.observe(args.target.tab_id))
            case "scroll":
                assert isinstance(args, ScrollPage)
                page = self.pages[args.target.tab_id]
                await page.bring_to_front()
                x = (
                    args.amount * (1 if args.direction == "right" else -1)
                    if args.direction in ("left", "right")
                    else 0
                )
                y = (
                    args.amount * (1 if args.direction == "down" else -1)
                    if args.direction in ("up", "down")
                    else 0
                )
                await page.mouse.wheel(x, y)
                await page.evaluate(
                    "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
                return BrowserResult(page=await self.observe(args.target.tab_id))
            case "press_key":
                assert isinstance(args, PressKey)
                page = self.pages[args.target.tab_id]
                if args.key == "Enter" and not await page.evaluate(
                    "() => document.activeElement && document.activeElement.tagName !== 'IFRAME'"
                    " && (" + SAFE_ELEMENT + ")(document.activeElement)"
                ):
                    raise ToolError("page_changed")
                await page.bring_to_front()
                await page.keyboard.press(args.key)
                return BrowserResult(page=await self.observe(args.target.tab_id))
            case "trigger_1password_autofill":
                assert isinstance(args, TriggerAutofill)
                page = self.pages[args.target.tab_id]
                if not await page.evaluate("() => document.activeElement?.tagName !== 'IFRAME'"):
                    raise ToolError("page_changed")
                await page.bring_to_front()
                await page.keyboard.press(
                    "Meta+Backslash" if sys.platform == "darwin" else "Control+Backslash"
                )
                # A request receipt, never a claim of successful fill/sign-in. No DOM
                # observation after the shortcut; get_tabs/read supplies a fresh snapshot.
                await self._clear_nodes(args.target.tab_id)
                return BrowserResult(autofill_requested=True, affected_tab=args.target.tab_id)
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
                await self._clear_nodes(args.target.tab_id)
                return BrowserResult(closed_tab=args.target.tab_id)
        raise ToolError("browser_failure")

    def _app_url(self, args: OpenWorkspaceApp) -> str:
        app = next(
            (app for app in self.workspace.apps if app.enabled and app.id == args.app_id), None
        )
        if app is None or app.kind == "native" or app.launch_url is None:
            raise ToolError("network_denied")
        return self.policy.validate(app.launch_url)

    async def _app_tab(self, args: OpenWorkspaceApp) -> tuple[str, Page] | None:
        app = next(app for app in self.workspace.apps if app.id == args.app_id)
        assert app.launch_url is not None
        candidates = []
        for key, page in self.pages.items():
            if page.is_closed():
                continue
            try:
                current = origin(self.policy.validate(page.url))
            except ToolError:
                continue
            if current in app.allowed_origins:
                candidates.append((key, page))
        path = urlsplit(app.launch_url).path.rstrip("/")
        return next(
            (
                item
                for item in candidates
                if urlsplit(item[1].url).path.rstrip("/") == path
                or path
                and urlsplit(item[1].url).path.startswith(path + "/")
            ),
            candidates[0] if candidates else None,
        )

    def _navigated(self, tab_id: str, page: Page, frame: Any) -> None:
        if frame == page.main_frame and tab_id in self.pages:
            self.documents[tab_id] = uuid4().hex

    async def cancel_pending(self) -> None:
        self.grant = None
        for tab_id in list(self.typed):
            await self._clear_nodes(tab_id)
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
        if self._workspace_route_installed and self.context is not None:
            self.context.remove_listener("page", self._page_opened)
            try:
                await self.context.unroute("**/*", self._workspace_handler)
            except Exception:
                failed = True
            self._workspace_route_installed = False
        # Stop pending popup adoption before disposing its CDP guards. Otherwise
        # an in-flight attach could install a handler after guard.close().
        auxiliary = list(self._auxiliary)
        for task in auxiliary:
            task.cancel()
        await asyncio.gather(*auxiliary, return_exceptions=True)
        self._auxiliary.clear()
        for tab_id in set(self.nodes) | set(self.typed):
            try:
                await self._clear_nodes(tab_id)
            except Exception:
                failed = True
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
        self.pages.clear()
        self.documents.clear()
        self.context = None
        self.browser = None
        self.playwright = None
        self._popup_failure = False
        if failed:
            raise ToolError("browser_cleanup")
