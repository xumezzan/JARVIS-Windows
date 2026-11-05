"""Owner-directed website navigation without sandbox filtering or confirmation."""

import json
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.native import NativeDesktop
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ToolError
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import NativeRequest, WindowsResult, register_windows


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.test", "https://example.test/"),
        ("instagram.com", "https://instagram.com/"),
        ("https://www.instagram.com", "https://www.instagram.com/"),
        ("HTTPS://WWW.INSTAGRAM.COM", "https://www.instagram.com/"),
        ("https://www.instagram.com/?next=explore", "https://www.instagram.com/?next=explore"),
    ],
)
def test_tool_normalizes_before_audited_snapshot(tmp_path: Path, raw: str, expected: str) -> None:
    backend = Mock()
    registry = ToolRegistry()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare("windows.open_chrome_url", {"url": raw}, Mode.EXECUTE)
        assert isinstance(action, Action) and action.risk is Risk.SAFE
        assert json.loads(action.payload)["url"] == expected
        assert not backend.call.called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://apple.com/",
        "http://example.com/",
        "https://host.internal/",
        "https://1password.com/",
        "http://intranet/",
        "https://site-does-not-exist.invalid/",
    ],
)
async def test_any_web_host_opens_without_approval_or_sandbox_network_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    backend = Mock()
    backend.call = AsyncMock(return_value=WindowsResult(performed=True, text=url))
    dns = AsyncMock(side_effect=AssertionError("Jarvis must delegate DNS to Chrome"))
    monkeypatch.setattr(NetworkPolicy, "validate_live_request", dns)
    policy = Mock(side_effect=AssertionError("No sandbox policy for external Chrome"))
    monkeypatch.setattr(NetworkPolicy, "validate", policy)
    registry, store = ToolRegistry(), ApprovalStore()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("windows.open_chrome_url", {"url": url}, Mode.EXECUTE)
        assert isinstance(action, Action) and action.risk is Risk.SAFE
        assert not backend.call.called and not dns.called
        with pytest.raises(ValueError):
            store.take_authority(audit.approved).approve(action)
        outcome = await engine.execute(action)
        assert outcome.status is Status.SUCCESS
        assert not dns.called and not policy.called
        assert backend.call.call_count == 2
        assert json.loads(outcome.result_json or "{}")["url"] == url
        assert (await engine.execute(action)).status is Status.INVALID
        assert url not in "".join(audit.recent())


@pytest.mark.asyncio
async def test_chrome_simulation_has_no_dns_or_process_effects(tmp_path: Path) -> None:
    backend = Mock(call=AsyncMock())
    registry, store = ToolRegistry(), ApprovalStore()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("windows.open_chrome_url", {"url": "https://apple.com/"})
        assert isinstance(action, Action)
        assert (await engine.execute(action)).status is Status.SIMULATED
        assert not backend.call.called


def test_native_launch_uses_fixed_executable_and_new_tab_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    desktop = NativeDesktop.__new__(NativeDesktop)
    executable = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    monkeypatch.setattr(desktop, "chrome_executable", lambda: executable)
    process = Mock()
    monkeypatch.setattr(subprocess, "Popen", process)
    receipt = desktop.dispatch(NativeRequest(operation="open_chrome_url", url="https://apple.com/"))
    assert receipt.performed and receipt.text == "https://apple.com/"
    assert receipt.target is None  # No fabricated page/window verification.
    assert process.call_args.args == ([str(executable), "--new-tab", "https://apple.com/"],)
    assert process.call_args.kwargs["shell"] is False
    with pytest.raises(ToolError):
        desktop.dispatch(NativeRequest(operation="open_chrome_url", url="file:///C:/test.txt"))
    assert process.call_count == 1


def test_non_web_address_is_rejected_before_the_native_adapter(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_windows(registry, Mock())
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        outcome = engine.prepare("windows.open_chrome_url", {"url": "file:///C:/test.txt"})
        assert isinstance(outcome, Outcome)
        assert outcome.status is Status.DENIED and outcome.error is ErrorCode.NETWORK_DENIED
        assert "network_denied" in "".join(audit.recent())


@pytest.mark.parametrize("url", ["http://localhost:8080/", "http://127.0.0.1/", "http://[::1]/"])
def test_loopback_is_denied_before_native_browser_launch(tmp_path: Path, url: str) -> None:
    backend = Mock(call=AsyncMock())
    registry = ToolRegistry()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        action = PermissionEngine(registry, ApprovalStore(), audit).prepare(
            "windows.open_chrome_url", {"url": url}, Mode.EXECUTE
        )
        assert isinstance(action, Outcome) and action.error is ErrorCode.NETWORK_DENIED
        backend.call.assert_not_called()
