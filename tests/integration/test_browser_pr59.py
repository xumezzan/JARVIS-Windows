"""Real Chromium against a controlled local server, through the production permission engine."""

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
from jarvis.tools.browser import BrowserResult, PageView, register_browser
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


async def test_simulation_launches_nothing_and_real_open_needs_no_approval(
    harness: Harness,
) -> None:
    args = {"url": harness.site.origin + "/"}
    assert (await harness.call("open", args, mode=Mode.SIMULATION)).status is Status.SIMULATED
    assert harness.host._thread is None
    assert not harness.site.seen
    assert (await harness.call("open", args, approve=False)).status is Status.SUCCESS
    assert [row[:2] for row in harness.site.seen] == [("GET", "/")]


async def test_forged_safe_label_does_not_match_observed_button(harness: Harness) -> None:
    page = await harness.opened()
    button = next(item for item in page.elements if item.name == "Search")
    # Same DOM fingerprint/request, different label: never click the underlying element.
    forged = button.model_dump() | {"name": "Next"}
    outcome = await harness.call(
        "click", {"target": page.target.model_dump(), "element": forged}, approve=False
    )
    assert outcome.error is ErrorCode.PAGE_CHANGED
    assert [row[1] for row in harness.site.seen] == ["/"]


async def test_close_edited_form_requires_approval_and_rechecks_dirty_state(
    harness: Harness,
) -> None:
    page = await harness.opened()
    field = next(item for item in page.elements if item.name == "Message")
    typed = await harness.call(
        "type",
        {"target": page.target.model_dump(), "element": field.model_dump(), "text": "unsaved"},
        approve=False,
    )
    assert typed.status is Status.SUCCESS
    updated = BrowserResult.model_validate_json(typed.result_json or "").page
    assert updated is not None and updated.target.has_unsaved_changes
    args = {"target": updated.target.model_dump()}
    assert (await harness.call("close", args, approve=False)).status is Status.DENIED
    forged = {"target": updated.target.model_dump() | {"has_unsaved_changes": False}}
    outcome = await harness.call("close", forged, approve=False)
    assert outcome.error is ErrorCode.PAGE_CHANGED
    assert (await harness.call("read", args)).status is Status.SUCCESS
    assert (await harness.call("close", args)).status is Status.SUCCESS
