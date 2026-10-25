"""Live mode selection, network preflights and permission invariants without live accounts."""

import asyncio
import json
import socket
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from tests.unit.test_browser import TARGET, Probe

from jarvis.browser.host import BrowserHost
from jarvis.browser.session import BrowserSession
from jarvis.config import load_config
from jarvis.core.planner.contracts import PlannerInput
from jarvis.core.planner.offline import OfflineProvider
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy, validate_cdp_endpoint
from jarvis.tools.base import ToolError
from jarvis.tools.browser import register_browser
from jarvis.tools.registry import ToolRegistry


def test_live_is_explicit_and_visible() -> None:
    assert load_config({}).browser_mode is BrowserMode.STRICT
    config = load_config(
        {
            "JARVIS_BROWSER_MODE": "live",
            "JARVIS_BROWSER_CDP_ENDPOINT": "http://localhost:9222",
        }
    )
    host = BrowserHost(config.browser_policy, cdp_endpoint=config.browser_cdp_endpoint)
    assert not host.headless and host.cdp_endpoint == "http://localhost:9222"
    assert BrowserHost().headless
    with pytest.raises(ValueError):
        load_config({"JARVIS_BROWSER_CDP_ENDPOINT": "http://localhost:9222"})
    with pytest.raises(ValueError):
        BrowserHost(cdp_endpoint="http://localhost:9222")
    with pytest.raises(ValueError) as error:
        load_config({"JARVIS_BROWSER_MODE": "SECRET"})
    assert "SECRET" not in str(error.value)


@pytest.mark.asyncio
async def test_unavailable_cdp_never_launches_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    playwright = Mock()
    playwright.chromium.connect_over_cdp = AsyncMock(side_effect=RuntimeError("PRIVATE"))
    playwright.chromium.launch = AsyncMock()
    playwright.stop = AsyncMock()
    monkeypatch.setattr(
        "jarvis.browser.session.async_playwright",
        lambda: Mock(start=AsyncMock(return_value=playwright)),
    )
    session = BrowserSession(
        NetworkPolicy(mode=BrowserMode.LIVE), cdp_endpoint="http://localhost:9222"
    )
    with pytest.raises(ToolError, match="browser_unavailable"):
        await session.start()
    playwright.chromium.launch.assert_not_called()
    playwright.stop.assert_awaited_once()
    assert session.browser is None and session.context is None


@pytest.mark.parametrize(
    "value",
    [
        "https://localhost:9222",
        "http://example.com:9222",
        "http://127.0.0.2:9222",
        "http://localhost",
        "http://localhost:9222/json",
        "http://user:SECRET@localhost:9222",
        "http://localhost:9222/?token=SECRET",
        "http://localhost:9222/#SECRET",
        "http://localhost:9222\\foo",
        "http://localhost:0",
        "http://localhost:99999",
    ],
)
def test_cdp_endpoint_is_local_configuration_only(value: str) -> None:
    with pytest.raises(ValueError) as error:
        validate_cdp_endpoint(value)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://127.0.0.1/",
        "https://192.168.1.5/",
        "https://10.0.0.1/",
        "https://172.16.0.1/",
        "https://169.254.169.254/",
        "https://[::1]/",
        "https://[fe80::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://localhost/",
        "https://machine.local/",
        "https://user:pass@example.com/",
    ],
)
def test_live_private_network_and_unsafe_urls_remain_blocked(url: str) -> None:
    with pytest.raises(ToolError, match="network_denied"):
        NetworkPolicy(mode=BrowserMode.LIVE).validate(url)


def test_live_public_https_and_spa_fragments() -> None:
    policy = NetworkPolicy(mode=BrowserMode.LIVE)
    assert (
        policy.validate("https://arbitrary.example/#/account")
        == "https://arbitrary.example/#/account"
    )
    assert policy.validate_request("https://arbitrary.example/form", "POST", "q=hello")
    with pytest.raises(ToolError):
        NetworkPolicy().validate("https://arbitrary.example/")


@pytest.mark.asyncio
async def test_live_dns_preflight_blocks_mixed_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = AsyncMock(
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ("93.184.216.34", "192.168.1.1")
        ]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    with pytest.raises(ToolError, match="network_denied"):
        await NetworkPolicy(mode=BrowserMode.LIVE).validate_live_request("https://example.com/")


@pytest.mark.asyncio
async def test_live_simulation_never_connects_and_js_buttons_need_approval(tmp_path: Path) -> None:
    registry = ToolRegistry()
    probe = Probe()
    register_browser(registry, probe, NetworkPolicy(mode=BrowserMode.LIVE))
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        button = {"role": "button", "name": "Continue", "fingerprint": "d" * 64}
        args = {"target": TARGET.model_dump(), "element": button}
        action = engine.prepare("browser.click", args, Mode.SIMULATION)
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM
        assert json.loads(action.payload)["account"] == "chrome-context"
        assert (await engine.execute(action)).status is Status.DENIED
        approved = engine.prepare("browser.click", args, Mode.SIMULATION)
        assert isinstance(approved, Action)
        token = store.take_authority(audit.approved).approve(approved)
        assert (await engine.execute(approved, token)).status is Status.SIMULATED
        public = engine.prepare(
            "browser.open", {"url": "https://arbitrary.example/"}, Mode.SIMULATION
        )
        assert isinstance(public, Action)
        assert (await engine.execute(public)).status is Status.SIMULATED
        injected = engine.prepare(
            "browser.open",
            {
                "url": "https://example.com/",
                "cdp_endpoint": "http://localhost:9222",
            },
        )
        assert isinstance(injected, Outcome) and injected.status is Status.INVALID
        assert not probe.calls


@pytest.mark.asyncio
async def test_search_recipe_uses_one_configured_browser() -> None:
    proposal = await OfflineProvider().propose(
        PlannerInput(
            command="открой chrome и найди OpenAI",
            answers=(),
            steps=(),
            mode=Mode.EXECUTE,
            catalog_json="[]",
        )
    )
    assert proposal.tool == "browser.search"
    assert json.loads(proposal.arguments) == {"query": "OpenAI"}
