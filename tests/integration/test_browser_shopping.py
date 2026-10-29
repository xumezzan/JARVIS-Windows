"""Dynamic shop controls and observation recovery in real local Chromium, without purchases."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from tests.browser_support import FixtureSite
from tests.integration.test_browser_workspace import WorkspaceHarness

from jarvis.browser.session import BrowserSession
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ExecutionContext
from jarvis.tools.browser import BrowserCommand, BrowserResult, PageView, register_browser
from jarvis.tools.registry import ToolRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(params=[BrowserMode.LIVE, BrowserMode.WORKSPACE])
async def shop(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[WorkspaceHarness]:
    site = FixtureSite()
    session = BrowserSession(
        NetworkPolicy(fixture_origin=site.origin, mode=request.param),
        profile_dir=tmp_path / "profile" if request.param is BrowserMode.WORKSPACE else None,
        headless=True,
    )

    class Backend:
        async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
            await context.checkpoint()
            return await session.dispatch(command)

    registry = ToolRegistry()
    register_browser(registry, Backend(), session.policy)
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


async def opened(shop: WorkspaceHarness) -> PageView:
    outcome = await shop.invoke("open", {"url": shop.site.origin + "/shop"})
    assert outcome.status is Status.SUCCESS, outcome
    view = BrowserResult.model_validate_json(outcome.result_json or "{}").page
    assert view is not None
    return view


async def test_select_variants_and_wait_for_cart_confirmation(shop: WorkspaceHarness) -> None:
    view = await opened(shop)
    for name, role in (
        ("Burgundy", "radio"),
        ("256 GB", "radio"),
        ("Include case", "checkbox"),
        ("Delivery", "option"),
        ("Add to cart", "button"),
    ):
        element = next(el for el in view.elements if el.name == name and el.role == role)
        args = {"target": view.target.model_dump(), "element": element.model_dump()}
        denied = await shop.invoke("click", args, approve=False)
        assert denied.status is Status.DENIED
        outcome = await shop.invoke("click", args)
        assert outcome.status is Status.SUCCESS, outcome
        fresh = BrowserResult.model_validate_json(outcome.result_json or "{}").page
        assert fresh is not None
        view = fresh
    assert "Cart: Test phone burgundy 256 GB" in view.text
    actual = shop.session.pages[view.target.tab_id]
    assert await actual.evaluate("window.cartClicks") == 1
    assert await actual.locator("select").input_value() == "Delivery"
    assert await actual.get_by_role("checkbox").is_checked()


async def test_animation_attributes_do_not_invalidate_a_button(shop: WorkspaceHarness) -> None:
    view = await opened(shop)
    page = shop.session.pages[view.target.tab_id]
    await page.locator("button").evaluate(
        "el => {el.className = 'animated hover'; el.style.transform = 'translateX(1px)';}"
    )
    element = next(el for el in view.elements if el.name == "Add to cart")
    outcome = await shop.invoke(
        "click", {"target": view.target.model_dump(), "element": element.model_dump()}
    )
    assert outcome.status is Status.SUCCESS, outcome
    assert await page.evaluate("window.cartClicks") == 1


async def test_read_refreshes_changed_content_but_click_cannot_use_old_marks(
    shop: WorkspaceHarness,
) -> None:
    view = await opened(shop)
    page = shop.session.pages[view.target.tab_id]
    button = next(el for el in view.elements if el.name == "Add to cart")
    await page.locator("button").evaluate("el => el.textContent = 'Buy now'")
    outcome = await shop.invoke(
        "click", {"target": view.target.model_dump(), "element": button.model_dump()}
    )
    assert outcome.error is ErrorCode.PAGE_CHANGED and not outcome.may_have_effects
    refreshed = await shop.invoke("read", {"target": view.target.model_dump()})
    assert refreshed.status is Status.SUCCESS, refreshed
    updated = BrowserResult.model_validate_json(refreshed.result_json or "{}").page
    assert updated is not None and updated.target != view.target
    assert any(el.name == "Buy now" for el in updated.elements)
    assert await page.evaluate("window.cartClicks || 0") == 0
    await page.goto(shop.site.origin + "/live-ui")
    stale = await shop.invoke("read", {"target": view.target.model_dump()})
    assert stale.error is ErrorCode.PAGE_CHANGED


async def test_runner_refreshes_stale_shop_before_proposing_the_next_action(
    shop: WorkspaceHarness,
) -> None:
    class ShoppingProvider:
        calls = 0

        async def propose(self, data: PlannerInput) -> Proposal:
            self.calls += 1
            if self.calls == 1:
                return call("browser.open", {"url": shop.site.origin + "/shop"})
            if self.calls in (2, 3):
                view = BrowserResult.model_validate_json(
                    data.steps[-1].outcome.result_json or "{}"
                ).page
                assert view is not None
                if self.calls == 2:
                    page = shop.session.pages[view.target.tab_id]
                    await page.locator("h1").evaluate("el => el.textContent = 'Updated stock'")
                else:
                    assert [step.tool for step in data.steps[-3:]] == [
                        "browser.click",
                        "browser.get_tabs",
                        "browser.read",
                    ]
                    assert "Updated stock" in view.text
                button = next(el for el in view.elements if el.name == "Add to cart")
                return call(
                    "browser.click",
                    {"target": view.target.model_dump(), "element": button.model_dump()},
                )
            assert "Cart: Test phone blue 128 GB" in (data.steps[-1].outcome.result_json or "")
            return Proposal(kind="finish")

    async def approve(action: Action) -> ApprovalToken:
        return shop.authority.approve(action)

    async def clarify(question: str) -> None:
        pytest.fail("No clarification is needed for the fixture")

    result = await Runner(
        shop.engine._registry, shop.engine, ShoppingProvider(), approve, clarify
    ).run("Добавь тестовый телефон в корзину", Mode.EXECUTE)
    assert result.status == "finished", result
    assert len(result.steps) == 5
    page = next(iter(shop.session.pages.values()))
    assert await page.evaluate("window.cartClicks") == 1
