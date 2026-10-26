"""Exact local navigation, approval binding and truthful external-browser receipts."""

import json
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from jarvis.config import AppConfig
from jarvis.desktop.service import DesktopService
from jarvis.desktop.system_tools import chrome_command
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
        "открой браузер",
        "прочитай сайт apple.com",
        "открой сайт apple.com и напиши привет",
        "открой сайт javascript:alert(1)",
        "открой сайт file:///C:/Windows/test.txt",
        "открой сайт https://user:password@apple.com",
        "открой сайт https://127.0.0.1",
        "открой сайт https://localhost",
        "открой сайт https://host.internal",
        "открой сайт https://1password.com",
        "открой сайт --remote-debugging-port=9222",
    ],
)
def test_no_implicit_commands_or_private_destinations(command: str) -> None:
    assert chrome_command(command) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_exact_approval_precedes_dns_and_native_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approved: bool
) -> None:
    backend = Mock()
    backend.call = AsyncMock(
        return_value=WindowsResult(performed=True, opened_url="https://apple.com/")
    )
    dns = AsyncMock(return_value="https://apple.com/")
    monkeypatch.setattr(NetworkPolicy, "validate_live_request", dns)
    registry, store = ToolRegistry(), ApprovalStore()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare(
            "windows.open_chrome_url", {"url": "https://apple.com/"}, Mode.EXECUTE
        )
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM
        assert not backend.call.called and not dns.called
        token = store.take_authority(audit.approved).approve(action) if approved else None
        outcome = await engine.execute(action, token)
        assert outcome.status is (Status.SUCCESS if approved else Status.DENIED)
        assert dns.call_count == int(approved)
        assert backend.call.call_count == 2 * int(approved)
        assert (await engine.execute(action, token)).status is Status.INVALID
        assert "apple.com" not in "".join(audit.recent())


@pytest.mark.asyncio
async def test_chrome_simulation_has_no_dns_or_process_effects(tmp_path: Path) -> None:
    backend = Mock(call=AsyncMock())
    registry, store = ToolRegistry(), ApprovalStore()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("windows.open_chrome_url", {"url": "https://apple.com/"})
        assert isinstance(action, Action)
        token = store.take_authority(audit.approved).approve(action)
        assert (await engine.execute(action, token)).status is Status.SIMULATED
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
    assert receipt.performed and receipt.opened_url == "https://apple.com/"
    assert receipt.target is None  # No fabricated page/window verification.
    assert process.call_args.args == ([str(executable), "--new-tab", "https://apple.com/"],)
    assert process.call_args.kwargs["shell"] is False
    with pytest.raises(ToolError):
        desktop.dispatch(NativeRequest(operation="open_chrome_url", url="file:///C:/test.txt"))
    assert process.call_count == 1


@pytest.mark.asyncio
async def test_desktop_literal_url_works_without_cloud_or_changing_browser_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[NativeRequest] = []

    async def native_call(
        self: ProcessBackend, request: NativeRequest, context: object
    ) -> WindowsResult:
        calls.append(request)
        return WindowsResult(performed=True, opened_url=request.url)

    monkeypatch.setattr(ProcessBackend, "call", native_call)
    monkeypatch.setattr(NetworkPolicy, "validate_live_request", AsyncMock())
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        service.settings = service.settings.model_copy(update={"provider": "openai"})
        assert service.start("открой сайт apple.com", Mode.EXECUTE)
        import asyncio

        async with asyncio.timeout(service.limits.total_seconds):
            while service.prompt is None:
                await asyncio.sleep(0.005)
        prompt = service.prompt
        assert prompt is not None and isinstance(prompt.value, Action)
        assert json.loads(prompt.value.payload)["url"] == "https://apple.com/"
        assert not calls
        wire = prompt.wire()
        from jarvis.desktop.protocol import Request

        await service.handle(
            Request.model_validate(
                {
                    "id": uuid4(),
                    "operation": "respond",
                    "reply": {
                        "prompt_id": prompt.id,
                        "signature": wire["signature"],
                        "approved": True,
                    },
                }
            )
        )
        worker = service.worker
        assert worker is not None
        await asyncio.wait_for(asyncio.shield(worker), service.limits.total_seconds)
        assert service.task.state == "finished"
        assert "загрузка страницы не проверена" in service.last_speech
        assert "загрузка страницы не проверена" in service.task.summary
        assert [item.operation for item in calls] == ["check_chrome", "open_chrome_url"]
        assert service.config.browser_mode.value == "strict"
        assert not service.start("прочитай apple.com", Mode.EXECUTE)
    finally:
        await service.shutdown()


def test_network_policy_denial_keeps_finite_reason_not_invalid_request(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_windows(registry, Mock())
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        outcome = engine.prepare("windows.open_chrome_url", {"url": "https://127.0.0.1/"})
        assert isinstance(outcome, Outcome)
        assert outcome.status is Status.DENIED and outcome.error is ErrorCode.NETWORK_DENIED
        assert "network_denied" in "".join(audit.recent())
