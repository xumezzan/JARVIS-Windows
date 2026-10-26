"""Real Chromium SPA/profile fixtures, without user accounts or public network."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from tests.browser_support import FixtureSite
from tests.unit.test_browser_workspace import profile

from jarvis.browser.inspection import INSPECT
from jarvis.browser.session import BrowserSession
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ExecutionContext, canonical
from jarvis.tools.browser import (
    BrowserCommand,
    BrowserResult,
    OpenWorkspaceApp,
    PageTarget,
    PageView,
    register_browser,
)
from jarvis.tools.registry import ToolRegistry


@dataclass
class WorkspaceHarness:
    session: BrowserSession
    engine: PermissionEngine
    authority: ApprovalAuthority
    site: FixtureSite

    async def invoke(
        self, operation: str, args: dict[str, Any], *, approve: bool = True
    ) -> Outcome:
        action = self.engine.prepare("browser." + operation, args, Mode.EXECUTE)
        assert isinstance(action, Action), action
        token = (
            self.authority.approve(action) if approve and action.risk.value == "CONFIRM" else None
        )
        return await self.engine.execute(action, token)

    async def opened(self) -> PageView:
        outcome = await self.invoke("open", {"url": self.site.origin + "/workspace-spa"})
        assert outcome.status is Status.SUCCESS, outcome
        page = BrowserResult.model_validate_json(outcome.result_json or "").page
        assert page is not None
        return page


@pytest_asyncio.fixture
async def workspace(tmp_path: Path) -> AsyncIterator[WorkspaceHarness]:
    site = FixtureSite()
    session = BrowserSession(
        NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.WORKSPACE),
        profile_dir=tmp_path / "browser_profile",
        headless=True,
        workspace=profile(),
    )

    class Backend:
        async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
            await context.checkpoint()
            return await session.dispatch(command)

    registry = ToolRegistry()
    register_browser(registry, Backend(), session.policy, workspace=profile())
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    engine = PermissionEngine(registry, store, audit)
    try:
        yield WorkspaceHarness(session, engine, store.take_authority(audit.approved), site)
    finally:
        engine.cancel_all()
        await session.close()
        site.close()
        audit.close()


@pytest.mark.asyncio
async def test_spa_roles_contenteditable_input_and_private_field_redaction(
    workspace: WorkspaceHarness,
) -> None:
    page = await workspace.opened()
    assert {"tab", "menuitem", "combobox", "option", "textbox", "button"} <= {
        element.role for element in page.elements
    }
    assert [element.mark_id for element in page.elements] == list(range(1, len(page.elements) + 1))
    browser_page = workspace.session.pages[page.target.tab_id]
    raw = await browser_page.evaluate(INSPECT, True)
    assert "fixture-private-" not in json.dumps(raw)
    assert "fixture-private-" not in page.model_dump_json()
    field = next(element for element in page.elements if element.name == "Draft editor")
    outcome = await workspace.invoke(
        "type",
        {
            "target": page.target.model_dump(),
            "element": field.model_dump(),
            "text": "Привет, команда!",
        },
    )
    assert outcome.status is Status.SUCCESS, outcome
    updated = BrowserResult.model_validate_json(outcome.result_json or "").page
    assert updated is not None
    assert "input:insertText:true" in updated.text
    assert next(e.value for e in updated.elements if e.name == "Draft editor") == "Привет, команда!"
    assert updated.target.observation_id != page.target.observation_id
    stale = await workspace.invoke(
        "type",
        {
            "target": page.target.model_dump(),
            "element": field.model_dump(),
            "text": "Stale",
        },
    )
    assert stale.status is Status.ERROR


@pytest.mark.asyncio
async def test_same_named_buttons_are_bound_to_exact_observed_nodes(
    workspace: WorkspaceHarness,
) -> None:
    page = await workspace.opened()
    buttons = [element for element in page.elements if element.name == "Open"]
    assert len(buttons) == 2 and buttons[0].mark_id != buttons[1].mark_id
    denied = await workspace.invoke(
        "click",
        {
            "target": page.target.model_dump(),
            "element": buttons[1].model_dump(),
        },
        approve=False,
    )
    assert denied.status is Status.DENIED
    outcome = await workspace.invoke(
        "click",
        {
            "target": page.target.model_dump(),
            "element": buttons[1].model_dump(),
        },
    )
    assert outcome.status is Status.SUCCESS, outcome
    updated = BrowserResult.model_validate_json(outcome.result_json or "").page
    assert updated is not None and "\nSecond\n" in updated.text


@pytest.mark.asyncio
async def test_scroll_and_tab_return_fresh_observations(workspace: WorkspaceHarness) -> None:
    page = await workspace.opened()
    scrolled = await workspace.invoke(
        "scroll",
        {
            "target": page.target.model_dump(),
            "direction": "down",
            "amount": 600,
        },
    )
    assert scrolled.status is Status.SUCCESS, scrolled
    updated = BrowserResult.model_validate_json(scrolled.result_json or "").page
    assert updated is not None
    actual = workspace.session.pages[page.target.tab_id]
    assert await actual.evaluate("window.scrollY") > 0
    assert updated.target.observation_id != page.target.observation_id
    keyed = await workspace.invoke(
        "press_key",
        {
            "target": updated.target.model_dump(),
            "key": "Tab",
        },
    )
    assert keyed.status is Status.SUCCESS, keyed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,text",
    [
        ("Notes editor", "Первая строка\nВторая строка"),
        ("Comment draft", "Комментарий"),
        ("Assignee", "Alex"),
    ],
)
async def test_plaintext_editors_and_combobox_keep_exact_text(
    workspace: WorkspaceHarness,
    name: str,
    text: str,
) -> None:
    page = await workspace.opened()
    field = next(element for element in page.elements if element.name == name)
    result = await workspace.invoke(
        "type",
        {
            "target": page.target.model_dump(),
            "element": field.model_dump(),
            "text": text,
        },
    )
    assert result.status is Status.SUCCESS, result
    updated = BrowserResult.model_validate_json(result.result_json or "").page
    assert updated is not None
    assert next(element.value for element in updated.elements if element.name == name) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("via_redirect", [False, True])
async def test_workspace_blocks_initial_private_popup_request(
    workspace: WorkspaceHarness, via_redirect: bool
) -> None:
    other = FixtureSite()
    try:
        observed = await workspace.opened()
        page = workspace.session.pages[observed.target.tab_id]
        context = page.context
        destination = other.origin + "/must-not-reach"
        initial = (
            workspace.site.origin + "/live-private?port=" + str(other.server.server_port)
            if via_redirect
            else destination
        )
        async with context.expect_event(
            "requestfailed",
            predicate=lambda request: (
                request.url in (initial, destination, other.origin + "/private")
            ),
        ):
            await page.evaluate("url => { window.open(url); }", initial)
        assert not other.seen
    finally:
        other.close()


@pytest.mark.asyncio
async def test_autofill_is_a_confirmed_shortcut_receipt_without_post_fill_inspection(
    workspace: WorkspaceHarness,
) -> None:
    page = await workspace.opened()
    denied = await workspace.invoke(
        "trigger_1password_autofill",
        {
            "target": page.target.model_dump(),
        },
        approve=False,
    )
    assert denied.status is Status.DENIED
    result = await workspace.invoke(
        "trigger_1password_autofill",
        {
            "target": page.target.model_dump(),
        },
    )
    assert result.status is Status.SUCCESS, result
    receipt = BrowserResult.model_validate_json(result.result_json or "")
    assert receipt.autofill_requested and receipt.page is None
    assert receipt.affected_tab == page.target.tab_id
    assert page.target.tab_id not in workspace.session.nodes
    tabs = await workspace.invoke("get_tabs", {})
    assert tabs.status is Status.SUCCESS
    fresh = BrowserResult.model_validate_json(tabs.result_json or "").tabs[0]
    assert fresh.observation_id != page.target.observation_id
    observed = await workspace.invoke("read", {"target": fresh.model_dump()})
    assert observed.status is Status.SUCCESS
    assert "fixture-private-" not in (observed.result_json or "")
    assert "Autofill shortcut requested" in (observed.result_json or "")


@pytest.mark.asyncio
async def test_workspace_redirect_and_resources_cannot_reach_other_loopback_port(
    workspace: WorkspaceHarness,
) -> None:
    other = FixtureSite()
    try:
        result = await workspace.invoke(
            "open",
            {
                "url": workspace.site.origin
                + "/live-private?port="
                + str(other.server.server_port),
            },
        )
        assert result.status is Status.ERROR
        assert not other.seen
        resources = await workspace.invoke(
            "open",
            {
                "url": workspace.site.origin
                + "/live-resources?port="
                + str(other.server.server_port),
            },
        )
        assert resources.status is Status.SUCCESS, resources
        assert not other.seen
    finally:
        other.close()


@pytest.mark.asyncio
async def test_persistent_profile_keeps_only_synthetic_cookie_across_restart(
    tmp_path: Path,
) -> None:
    site = FixtureSite()
    policy = NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.WORKSPACE)
    directory = tmp_path / "browser_profile"
    first = BrowserSession(policy, headless=True, profile_dir=directory)
    second = BrowserSession(policy, headless=True, profile_dir=directory)
    try:
        await first.start()
        assert first.context is not None
        await first.context.add_cookies(
            [
                {
                    "name": "fixture-only",
                    "value": "synthetic-session",
                    "url": site.origin,
                    "expires": time() + 3600,
                }
            ]
        )
        await first.close()
        await second.start()
        assert second.context is not None
        cookies = await second.context.cookies([site.origin])
        assert any(
            item["name"] == "fixture-only" and item["value"] == "synthetic-session"
            for item in cookies
        )
    finally:
        await first.close()
        await second.close()
        site.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [True, False])
async def test_open_workspace_app_reuses_tab_or_opens_exact_launch_url(
    tmp_path: Path,
    existing: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The tab-selection algorithm is isolated from public services. SPA behavior and
    # persistence use real local Chromium in the tests above.
    policy = NetworkPolicy.from_workspace(profile())
    session = BrowserSession(policy, profile_dir=tmp_path / "browser_profile", workspace=profile())
    fake_page = Mock(url="https://erp.example.com/dashboard")
    fake_page.is_closed.return_value = False
    fake_page.bring_to_front = AsyncMock()
    fake_context = Mock(new_page=AsyncMock(return_value=fake_page))
    session.context = fake_context
    tab = "a" * 32
    if existing:
        session.pages[tab] = fake_page
    view = PageView(
        target=PageTarget(
            tab_id=tab,
            document_id="b" * 32,
            url=fake_page.url,
            origin="https://erp.example.com",
            fingerprint="c" * 64,
        ),
        title="ERP",
        text="Synthetic",
    )
    monkeypatch.setattr(session, "start", AsyncMock())
    monkeypatch.setattr(session, "refresh_tabs", AsyncMock())
    monkeypatch.setattr(session, "adopt", AsyncMock(return_value=tab))
    navigate = AsyncMock(return_value=200)
    monkeypatch.setattr(session, "navigate", navigate)
    monkeypatch.setattr(session, "observe", AsyncMock(return_value=view))
    monkeypatch.setattr(session, "resolve", AsyncMock(return_value=view))
    args = OpenWorkspaceApp(service="live-browser", account="chrome-context", app_id="sales_erp")
    result = await session.dispatch(
        BrowserCommand(
            operation="open_workspace_app",
            phase="run",
            parameters=canonical(args),
        )
    )
    assert result.page == view
    fake_page.bring_to_front.assert_awaited_once()
    if existing:
        fake_context.new_page.assert_not_awaited()
        navigate.assert_not_awaited()
    else:
        fake_context.new_page.assert_awaited_once()
        assert navigate.call_args.args[1].url == "https://erp.example.com/dashboard"


@pytest.mark.asyncio
async def test_app_switch_does_not_discard_an_existing_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserSession(
        NetworkPolicy.from_workspace(profile()),
        profile_dir=tmp_path / "browser_profile",
        workspace=profile(),
    )
    old = Mock(url="https://erp.example.com/other-editor")
    new = Mock(url="https://erp.example.com/dashboard")
    old.is_closed.return_value = new.is_closed.return_value = False
    old.bring_to_front = AsyncMock()
    new.bring_to_front = AsyncMock()
    session.pages["a" * 32] = old
    session.context = Mock(new_page=AsyncMock(return_value=new))
    target = PageTarget(
        tab_id="a" * 32,
        document_id="b" * 32,
        url=old.url,
        origin="https://erp.example.com",
        fingerprint="c" * 64,
        has_unsaved_changes=True,
    )
    draft = PageView(target=target, title="Draft", text="Unsaved")
    opened = draft.model_copy(
        update={
            "target": target.model_copy(
                update={
                    "tab_id": "d" * 32,
                    "url": new.url,
                    "has_unsaved_changes": False,
                }
            )
        }
    )
    monkeypatch.setattr(session, "start", AsyncMock())
    monkeypatch.setattr(session, "refresh_tabs", AsyncMock())
    monkeypatch.setattr(session, "adopt", AsyncMock(return_value="d" * 32))
    monkeypatch.setattr(session, "observe", AsyncMock(side_effect=[draft, opened]))
    navigate = AsyncMock(return_value=200)
    monkeypatch.setattr(session, "navigate", navigate)
    args = OpenWorkspaceApp(service="live-browser", account="chrome-context", app_id="sales_erp")
    result = await session.dispatch(
        BrowserCommand(
            operation="open_workspace_app",
            phase="run",
            parameters=canonical(args),
        )
    )
    assert result.page == opened and session.pages["a" * 32] is old
    assert navigate.call_args.args[0] is new
    old.bring_to_front.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_mail_calendar_origin_prefers_the_matching_route(tmp_path: Path) -> None:
    session = BrowserSession(
        NetworkPolicy.from_workspace(profile()),
        profile_dir=tmp_path / "browser_profile",
        workspace=profile(),
    )
    calendar = Mock(url="https://outlook.office.com/calendar/view/month")
    mail = Mock(url="https://outlook.office.com/mail/inbox")
    calendar.is_closed.return_value = mail.is_closed.return_value = False
    session.pages = {"a" * 32: calendar, "b" * 32: mail}
    app = OpenWorkspaceApp(app_id="ms_mail")
    assert await session._app_tab(app) == ("b" * 32, mail)
