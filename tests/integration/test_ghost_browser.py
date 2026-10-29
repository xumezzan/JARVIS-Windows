"""Real local Chromium actions with ghost geometry, without moving the Windows mouse."""

from unittest.mock import AsyncMock

import pytest
from tests.browser_support import FixtureSite

from jarvis.browser.session import BrowserSession
from jarvis.platforms.windows.ghost import CursorTarget
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import canonical
from jarvis.tools.browser import BrowserCommand
from jarvis.tools.windows import Rectangle


@pytest.mark.asyncio
async def test_click_type_and_scroll_publish_exact_screen_targets() -> None:
    site = FixtureSite()
    session = BrowserSession(NetworkPolicy(fixture_origin=site.origin, mode=BrowserMode.LIVE))
    seen: list[CursorTarget] = []

    async def preview(target: CursorTarget) -> None:
        seen.append(target)

    try:
        await session.start()
        assert session.context is not None
        page = await session.context.new_page()
        await page.goto(site.origin + "/live-ui")
        tab = await session.adopt(page)
        observed = await session.observe(tab)
        viewport = Rectangle(left=-1920, top=120, right=-640, bottom=840)
        session.viewport_target = AsyncMock(return_value=(viewport, 100, 1280, 720))  # type: ignore[method-assign]
        session.before_action = preview
        session.headless = False  # Test the headed preview path in the local offscreen browser.
        button = next(el for el in observed.elements if el.name == "Show result")
        bounds = await page.get_by_role("button", name="Show result").bounding_box()
        assert bounds is not None
        result = await session.dispatch(
            BrowserCommand(
                operation="click",
                phase="run",
                parameters=canonical(
                    session.schemas["click"].model_validate(
                        {"target": observed.target, "element": button}
                    )
                ),
            )
        )
        assert seen[0].rectangle.left == round(-1920 + bounds["x"])
        assert seen[0].rectangle.top == round(120 + bounds["y"])
        assert await page.locator("p").inner_text() == "Result:"
        assert result.page is not None
        field = next(el for el in result.page.elements if el.role == "searchbox")
        result = await session.dispatch(
            BrowserCommand(
                operation="type",
                phase="run",
                parameters=canonical(
                    session.schemas["type"].model_validate(
                        {"target": result.page.target, "element": field, "text": "fixture"}
                    )
                ),
            )
        )
        assert await page.get_by_role("searchbox").input_value() == "fixture"
        assert result.page is not None
        await session.dispatch(
            BrowserCommand(
                operation="scroll",
                phase="run",
                parameters=canonical(
                    session.schemas["scroll"].model_validate(
                        {"target": result.page.target, "direction": "down", "amount": 200}
                    )
                ),
            )
        )
        assert [target.action for target in seen] == ["click", "type", "scroll"]
        assert all(target.window_handle == 100 for target in seen)
        assert seen[-1].rectangle == viewport
    finally:
        await session.close()
        site.close()
