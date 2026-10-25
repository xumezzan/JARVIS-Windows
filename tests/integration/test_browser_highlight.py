"""Real Chromium viewport preview against a local page, without desktop/hardware claims."""

import asyncio
from time import monotonic

import pytest
from tests.browser_support import FixtureSite

from jarvis.browser.session import BrowserSession
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ToolError
from jarvis.tools.browser import ClickElement


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "move", "cancel"])
async def test_preview_is_visible_for_300ms_and_removed_before_revalidation(change: str) -> None:
    site = FixtureSite()
    session = BrowserSession(NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.LIVE))
    try:
        await session.start()
        assert session.context is not None
        page = await session.context.new_page()
        await page.goto(site.origin + "/live-ui")
        tab = await session.adopt(page)
        observed = await session.observe(tab)
        button = next(item for item in observed.elements if item.name == "Show result")
        args = ClickElement(target=observed.target, element=button)
        session.headless = False  # Exercise viewport preview in real offscreen Chromium.
        task = asyncio.create_task(session.highlight_click(args))
        async with asyncio.timeout(3):
            while not await page.locator('html > div[aria-hidden="true"]').count():
                await asyncio.sleep(0.005)
        started = monotonic()
        marker = page.locator('html > div[aria-hidden="true"]')
        assert await marker.evaluate("el => getComputedStyle(el).pointerEvents") == "none"
        if change == "move":
            await page.get_by_role("button").evaluate("el => el.style.marginLeft = '100px'")
            with pytest.raises(ToolError, match="page_changed"):
                await task
        elif change == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
            assert monotonic() - started >= 0.28
            await session.check_element(args)
        assert await marker.count() == 0
        assert await page.locator("p").inner_text() == "Ready"
    finally:
        await session.close()
        site.close()
