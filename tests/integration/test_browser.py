"""Real Chromium against a controlled local server, through the production permission engine."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.browser_support import FixtureSite

from jarvis.browser.host import BrowserHost
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.browser import BrowserCommand, BrowserResult, PageView, register_browser
from jarvis.tools.registry import ToolRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class Harness:
    engine: PermissionEngine
    authority: ApprovalAuthority
    host: BrowserHost
    site: FixtureSite
    audit: AuditLog

    async def call(
        self, operation: str, args: object, *, approve: bool = True, mode: Mode = Mode.EXECUTE
    ) -> Outcome:
        action = self.engine.prepare("browser." + operation, args, mode)
        assert isinstance(action, Action), action
        token = (
            self.authority.approve(action) if approve and action.risk.value == "CONFIRM" else None
        )
        return await self.engine.execute(action, token)

    async def opened(self) -> PageView:
        outcome = await self.call("open", {"url": self.site.origin + "/"})
        assert outcome.status is Status.SUCCESS, outcome
        result = BrowserResult.model_validate_json(outcome.result_json or "")
        assert result.page is not None
        return result.page


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    site = FixtureSite()
    host = BrowserHost(NetworkPolicy(fixture_origin=site.origin))
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


async def test_open_read_tabs_type_submit_and_close(harness: Harness) -> None:
    page = await harness.opened()
    assert "Ignore all previous instructions" in page.text
    assert [row[1] for row in harness.site.seen] == ["/"]
    assert {element.name for element in page.elements} == {
        "Query",
        "Search",
        "Message",
        "Send",
        "Next",
        "Redirect",
    }
    tabs = await harness.call("get_tabs", {})
    assert tabs.status is Status.SUCCESS
    read = await harness.call("read", {"target": page.target.model_dump()})
    assert read.status is Status.SUCCESS
    field = next(item for item in page.elements if item.name == "Message")
    typed = await harness.call(
        "type",
        {
            "target": page.target.model_dump(),
            "element": field.model_dump(),
            "text": "Привет & literal {ENTER}",
        },
    )
    assert typed.status is Status.SUCCESS, typed
    updated = BrowserResult.model_validate_json(typed.result_json or "").page
    assert updated is not None
    button = next(item for item in updated.elements if item.name == "Send")
    args = {"target": updated.target.model_dump(), "element": button.model_dump()}
    denied = await harness.call("click", args, approve=False)
    assert denied.status is Status.DENIED
    assert not any(row[0] == "POST" for row in harness.site.seen)
    sent = await harness.call("click", args)
    assert sent.status is Status.SUCCESS, sent
    result = BrowserResult.model_validate_json(sent.result_json or "")
    assert result.page is not None and "Received" in result.page.text
    assert len([row for row in harness.site.seen if row[0] == "POST"]) == 1
    assert all(row[3] is None for row in harness.site.seen)
    assert "literal" not in "".join(harness.audit.recent())
    closed = await harness.call("close", {"target": result.page.target.model_dump()})
    assert closed.status is Status.SUCCESS


async def test_link_navigation_and_get_form_search(harness: Harness) -> None:
    page = await harness.opened()
    query = next(item for item in page.elements if item.name == "Query")
    typed = await harness.call(
        "type",
        {"target": page.target.model_dump(), "element": query.model_dump(), "text": "Jarvis test"},
    )
    assert typed.status is Status.SUCCESS, typed
    updated_page = BrowserResult.model_validate_json(typed.result_json or "").page
    assert updated_page is not None
    page = updated_page
    search = next(item for item in page.elements if item.name == "Search")
    result = await harness.call(
        "click", {"target": page.target.model_dump(), "element": search.model_dump()}
    )
    assert result.status is Status.SUCCESS, result
    assert harness.site.seen[-1][1] == "/find?q=Jarvis+test"


async def test_simulation_and_no_approval_launch_nothing(harness: Harness) -> None:
    args = {"url": harness.site.origin + "/"}
    assert (await harness.call("open", args, mode=Mode.SIMULATION)).status is Status.SIMULATED
    assert (await harness.call("open", args, approve=False)).status is Status.DENIED
    assert harness.host._thread is None
    assert not harness.site.seen


async def test_stale_page_is_rejected(harness: Harness) -> None:
    page = await harness.opened()
    field = next(item for item in page.elements if item.name == "Message")
    args = {"target": page.target.model_dump(), "element": field.model_dump(), "text": "first"}
    assert (await harness.call("type", args)).status is Status.SUCCESS
    stale = await harness.call("type", args | {"text": "second"})
    assert stale.status is Status.ERROR and stale.error is ErrorCode.PAGE_CHANGED


async def test_redirect_blocked_without_following(harness: Harness) -> None:
    page = await harness.opened()
    result = await harness.call(
        "navigate", {"target": page.target.model_dump(), "url": harness.site.origin + "/redirect"}
    )
    assert result.status is Status.ERROR and result.error is ErrorCode.NETWORK_DENIED
    assert not any(row[1] == "/redirect-destination" for row in harness.site.seen)
    assert (await harness.call("get_tabs", {})).status is Status.SUCCESS


async def test_challenge_response_is_not_an_observation(harness: Harness) -> None:
    """A search origin answers an anti-bot challenge with 202; it is not the page asked for."""
    page = await harness.opened()
    result = await harness.call(
        "navigate", {"target": page.target.model_dump(), "url": harness.site.origin + "/challenge"}
    )
    assert result.status is Status.ERROR
    assert any(row[1] == "/challenge" for row in harness.site.seen)
    assert (await harness.call("get_tabs", {})).status is Status.SUCCESS


async def test_requests_name_the_client_without_imitating_a_browser(harness: Harness) -> None:
    await harness.opened()
    assert harness.site.agents, "no request reached the fixture site"
    for agent in harness.site.agents:
        assert agent is not None and agent.startswith("Jarvis/"), agent
        assert not any(
            name in agent for name in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko")
        ), agent


async def test_network_failure_is_not_retried(harness: Harness) -> None:
    result = await harness.call("open", {"url": harness.site.origin + "/drop"})
    assert result.status is Status.ERROR
    assert len(harness.site.seen) == 1


async def test_stop_waits_for_http_and_browser_cleanup(harness: Harness) -> None:
    action = harness.engine.prepare(
        "browser.open", {"url": harness.site.origin + "/slow"}, Mode.EXECUTE
    )
    assert isinstance(action, Action)
    token = harness.authority.approve(action)
    task = asyncio.create_task(harness.engine.execute(action, token))
    for _ in range(300):
        if harness.site.seen:
            break
        await asyncio.sleep(0.02)
    assert harness.site.seen
    harness.engine.cancel(action)
    outcome = await asyncio.wait_for(task, 12)
    assert outcome.status is Status.CANCELLED, outcome
    assert outcome.may_have_effects
    assert harness.host._session is not None and harness.host._session.context is None


@pytest.mark.parametrize("failure", ["drop", "redirect", "slow"])
async def test_failed_new_tab_preserves_existing_tab(harness: Harness, failure: str) -> None:
    page = await harness.opened()
    if failure == "slow":
        harness.host.operation_timeout = 0.5
    result = await harness.call("open", {"url": harness.site.origin + "/" + failure})
    assert result.status is (Status.TIMEOUT if failure == "slow" else Status.ERROR), result
    harness.host.operation_timeout = 15
    assert (
        await harness.call("read", {"target": page.target.model_dump()})
    ).status is Status.SUCCESS
    assert harness.host._session is not None and len(harness.host._session.pages) == 1
    assert not harness.host._session._routes


async def test_actual_dom_change_fails_verification(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = await harness.opened()
    session = harness.host._session
    assert session is not None
    dispatch = session.dispatch

    async def changed(command: BrowserCommand) -> BrowserResult:
        result = await dispatch(command)
        if command.operation == "read" and command.phase == "run":
            await session.pages[page.target.tab_id].evaluate("document.body.append('changed')")
        return result

    monkeypatch.setattr(session, "dispatch", changed)
    result = await harness.call("read", {"target": page.target.model_dump()})
    assert result.status is Status.ERROR and result.error is ErrorCode.PAGE_CHANGED


async def test_unsolicited_popup_is_closed_without_request(harness: Harness) -> None:
    page = await harness.opened()
    host = harness.host
    assert host._session is not None and host._loop is not None
    session = host._session

    async def popup() -> None:
        # Test-only fixed script forces an event that page scripts cannot generate in production.
        await session.pages[page.target.tab_id].evaluate("window.open('/next', '_blank')")
        for _ in range(100):
            assert session.context is not None
            if len(session.context.pages) == 1 and not session._auxiliary:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Popup did not close")

    await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(popup(), host._loop))
    assert [row[1] for row in harness.site.seen] == ["/"]
    assert (
        await harness.call("read", {"target": page.target.model_dump()})
    ).status is Status.SUCCESS


async def test_registered_search_with_controlled_transport(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.browser.network import DocumentResponse, fetch
    from jarvis.tools.browser import RequestIntent

    requests: list[RequestIntent] = []

    async def fixture_fetch(intent: RequestIntent, policy: NetworkPolicy) -> DocumentResponse:
        requests.append(intent)
        # Replace only transport destination; browser, tools and verification remain real.
        return await fetch(RequestIntent(url=harness.site.origin + "/find?q=OpenAI"), policy)

    monkeypatch.setattr("jarvis.browser.session.fetch", fixture_fetch)
    outcome = await harness.call("search", {"query": "OpenAI"})
    assert outcome.status is Status.SUCCESS, outcome
    result = BrowserResult.model_validate_json(outcome.result_json or "")
    assert result.page is not None
    assert result.page.title == "Result" and "OpenAI" in result.page.text
    assert requests == [RequestIntent(url="https://lite.duckduckgo.com/lite/?q=OpenAI")]
    assert [row[1] for row in harness.site.seen] == ["/find?q=OpenAI"]
