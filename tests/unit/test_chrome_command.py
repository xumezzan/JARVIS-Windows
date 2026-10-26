"""Owner-directed website navigation without sandbox filtering or confirmation."""

import json
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Answer, PlannerInput
from jarvis.desktop.service import DesktopService
from jarvis.desktop.system_tools import ChromeCommandProvider, chrome_command, chrome_destination
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.native import NativeDesktop
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ToolError
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import NativeRequest, WindowsResult, register_windows


@pytest.mark.parametrize(
    "command",
    [
        "открой сайт apple.com",
        "Открой https://apple.com",
        "открой браузер и перейди на сайт apple.com",
        "перейди на apple.com",
        "открыть сайт https://apple.com/",
    ],
)
def test_exact_website_commands(command: str) -> None:
    assert chrome_command(command) == "https://apple.com/"


@pytest.mark.parametrize(
    "command",
    [
        "открой сайт instagram",
        "Открой Instagram",
        "открой сайт инстаграм",
        "открой браузер и перейди на сайт instagram",
        "перейди на instagram.com",
    ],
)
def test_reviewed_site_names_and_domains(command: str) -> None:
    expected = (
        "https://instagram.com/"
        if command.endswith("instagram.com")
        else "https://www.instagram.com/"
    )
    assert chrome_command(command) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("instagram", "https://www.instagram.com/"),
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
async def test_intranet_hosts_are_literal_and_malformed_urls_are_clarified() -> None:
    assert chrome_destination("открой сайт unknown-brand") == "unknown-brand"
    assert chrome_command("открой сайт unknown-brand") == "https://unknown-brand/"
    assert chrome_destination("открой блокнот") is None
    provider = ChromeCommandProvider(None)
    data = PlannerInput("открой сайт https://", (), (), Mode.EXECUTE, "[]")
    proposal = await provider.propose(data)
    assert proposal.kind == "clarify"
    clarified = PlannerInput(
        data.command, (Answer(proposal.question, "example.com"),), (), Mode.EXECUTE, "[]"
    )
    proposal = await provider.propose(clarified)
    assert proposal.tool == "windows.open_chrome_url"
    assert json.loads(proposal.arguments) == {"url": "https://example.com/"}


@pytest.mark.parametrize(
    "command",
    [
        "открой браузер",
        "прочитай сайт apple.com",
        "открой сайт apple.com и напиши привет",
        "открой сайт javascript:alert(1)",
        "открой сайт file:///C:/Windows/test.txt",
        "открой сайт https://user:password@apple.com",
        "открой сайт --remote-debugging-port=9222",
    ],
)
def test_only_web_navigation_commands_are_recipes(command: str) -> None:
    assert chrome_command(command) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://apple.com/",
        "http://example.com/",
        "http://localhost:8080/",
        "http://127.0.0.1:8080/",
        "http://[::1]:8080/",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected_url",
    [
        ("открой сайт apple.com", "https://apple.com/"),
        ("открой сайт instagram", "https://www.instagram.com/"),
        ("открой сайт http://localhost:8080", "http://localhost:8080/"),
    ],
)
async def test_desktop_literal_url_works_without_cloud_or_changing_browser_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, expected_url: str
) -> None:
    calls: list[NativeRequest] = []

    async def native_call(
        self: ProcessBackend, request: NativeRequest, context: object
    ) -> WindowsResult:
        calls.append(request)
        return WindowsResult(performed=True, text=request.url)

    monkeypatch.setattr(ProcessBackend, "call", native_call)
    monkeypatch.setattr(NetworkPolicy, "validate_live_request", AsyncMock())
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    approve = AsyncMock(side_effect=AssertionError("Website navigation must not ask approval"))
    monkeypatch.setattr(service, "_approve", approve)
    try:
        service.settings = service.settings.model_copy(update={"provider": "openai"})
        assert service.start(command, Mode.EXECUTE)
        import asyncio

        worker = service.worker
        assert worker is not None
        await asyncio.wait_for(asyncio.shield(worker), service.limits.total_seconds)
        assert service.task.state == "finished"
        assert "загрузка страницы не проверена" in service.last_speech
        assert "загрузка страницы не проверена" in service.task.summary
        assert [item.operation for item in calls] == ["check_chrome", "open_chrome_url"]
        assert calls[-1].url == expected_url
        assert service.prompt is None and not approve.called
        assert service.config.browser_mode.value == "strict"
        assert not service.start("прочитай apple.com", Mode.EXECUTE)
    finally:
        await service.shutdown()


def test_non_web_address_is_rejected_before_the_native_adapter(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_windows(registry, Mock())
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        outcome = engine.prepare("windows.open_chrome_url", {"url": "file:///C:/test.txt"})
        assert isinstance(outcome, Outcome)
        assert outcome.status is Status.DENIED and outcome.error is ErrorCode.NETWORK_DENIED
        assert "network_denied" in "".join(audit.recent())
