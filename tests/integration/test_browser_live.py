"""Real Chromium JS/cookies/redirects and CDP lifecycle, using only local fixtures."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from tests.browser_support import FixtureSite
from tests.integration.test_browser import Harness

from jarvis.browser.host import BrowserHost
from jarvis.browser.session import BrowserSession
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ExecutionContext
from jarvis.tools.browser import BrowserCommand, BrowserResult, register_browser
from jarvis.tools.registry import ToolRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Harness]:
    site = FixtureSite()
    host = BrowserHost(
        NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.LIVE), headless=True
    )
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    registry = ToolRegistry()
    register_browser(registry, host, host.policy)
    engine = PermissionEngine(registry, store, audit)
    yield Harness(engine, store.take_authority(audit.approved), host, site, audit)
    engine.cancel_all()
    host.shutdown()
    site.close()
    audit.close()


async def test_live_executes_scripts_loads_resources_and_keeps_cookies(live: Harness) -> None:
    page = await live.opened()
    # The same fixture's JS and image are blocked by Strict Sandbox's existing tests.
    for _ in range(100):
        if {"/background", "/script-send"} <= {row[1] for row in live.site.seen}:
            break
        await asyncio.sleep(0.02)
    assert {"/background", "/script-send"} <= {row[1] for row in live.site.seen}
    result = await live.call(
        "navigate",
        {
            "target": page.target.model_dump(),
            "url": live.site.origin + "/redirect",
        },
    )
    assert result.status is Status.SUCCESS, result
    redirected = BrowserResult.model_validate_json(result.result_json or "").page
    assert redirected is not None
    assert redirected.target.url == live.site.origin + "/redirect-destination"
    assert any(row[1] == "/redirect-destination" and row[3] == "demo=yes" for row in live.site.seen)
    assert (
        await live.call("read", {"target": redirected.target.model_dump()})
    ).status is Status.SUCCESS
    spa = await live.call(
        "navigate",
        {"target": redirected.target.model_dump(), "url": redirected.target.url + "#/route"},
    )
    assert spa.status is Status.SUCCESS, spa
    assert BrowserResult.model_validate_json(spa.result_json or "").response_status is None
    assert len([row for row in live.site.seen if row[1] == "/redirect-destination"]) == 1


async def test_live_redirect_and_subresource_to_other_local_port_are_blocked(live: Harness) -> None:
    # HTTP fixture redirects to a second constructor-untrusted loopback server.
    other = FixtureSite()
    try:
        resources = await live.call(
            "open",
            {"url": live.site.origin + "/live-resources?port=" + str(other.server.server_port)},
        )
        assert resources.status is Status.SUCCESS, resources
        result = await live.call(
            "open",
            {"url": live.site.origin + "/live-private?port=" + str(other.server.server_port)},
        )
        assert result.status is Status.ERROR
        assert not other.seen
    finally:
        other.close()


async def test_js_button_requires_approval_and_search_field_is_observed(live: Harness) -> None:
    outcome = await live.call("open", {"url": live.site.origin + "/live-ui"})
    assert outcome.status is Status.SUCCESS, outcome
    page = BrowserResult.model_validate_json(outcome.result_json or "").page
    assert page is not None
    field = next(element for element in page.elements if element.name == "Live query")
    assert field.role == "searchbox"
    typed = await live.call(
        "type",
        {
            "target": page.target.model_dump(),
            "element": field.model_dump(),
            "text": "hello",
        },
    )
    assert typed.status is Status.SUCCESS, typed
    updated = BrowserResult.model_validate_json(typed.result_json or "").page
    assert updated is not None
    button = next(element for element in updated.elements if element.name == "Show result")
    assert button.request is None
    args = {"target": updated.target.model_dump(), "element": button.model_dump()}
    assert (await live.call("click", args, approve=False)).status is Status.DENIED
    clicked = await live.call("click", args)
    assert clicked.status is Status.SUCCESS, clicked
    result = BrowserResult.model_validate_json(clicked.result_json or "").page
    assert result is not None and "Result: hello" in result.text


async def test_cdp_attaches_existing_tabs_uses_cookies_and_leaves_browser_running(
    tmp_path: Path,
) -> None:
    site = FixtureSite()
    process: asyncio.subprocess.Process | None = None
    session: BrowserSession | None = None
    try:
        async with async_playwright() as driver:
            profile = tmp_path / "chrome-profile"
            process = await asyncio.create_subprocess_exec(
                driver.chromium.executable_path,
                "--headless=new",
                "--no-sandbox",
                "--no-first-run",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--user-data-dir=" + str(profile),
                "about:blank",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            port_file = profile / "DevToolsActivePort"
            async with asyncio.timeout(10):
                while not port_file.exists():
                    await asyncio.sleep(0.05)
            port = port_file.read_text().splitlines()[0]
            endpoint = "http://127.0.0.1:" + port
            observer = await driver.chromium.connect_over_cdp(endpoint)
            context = observer.contexts[0]
            page = context.pages[0]
            await page.goto(site.origin + "/live-ui")
            session = BrowserSession(
                NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.LIVE),
                cdp_endpoint=endpoint,
            )
            await session.start()
            assert list(session.pages.values())[0].url == page.url
            registry = ToolRegistry()

            class Backend:
                async def call(
                    self, command: BrowserCommand, context: ExecutionContext
                ) -> BrowserResult:
                    return await session.dispatch(command)  # type: ignore[union-attr]

            register_browser(registry, Backend(), session.policy)
            with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
                store = ApprovalStore()
                engine = PermissionEngine(registry, store, audit)
                action = engine.prepare("browser.get_tabs", {}, Mode.EXECUTE)
                assert isinstance(action, Action)
                tabs = await engine.execute(action)
                assert tabs.status is Status.SUCCESS
                target = json.loads(tabs.result_json or "{}")["tabs"][0]
                navigate = engine.prepare(
                    "browser.navigate",
                    {
                        "target": target,
                        "url": site.origin + "/redirect",
                    },
                    Mode.EXECUTE,
                )
                assert isinstance(navigate, Action)
                result = await engine.execute(navigate)
                assert result.status is Status.SUCCESS, result
                assert any(
                    row[1] == "/redirect-destination" and row[3] == "demo=yes" for row in site.seen
                )
            await session.close()
            session = None
            assert process.returncode is None
            assert not page.is_closed() and page.url.endswith("/redirect-destination")
            assert (await context.cookies())[0]["name"] == "demo"
            await observer.close()
    finally:
        if session is not None:
            await session.close()
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        site.close()
