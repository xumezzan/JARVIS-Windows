"""Installer exercises use real local boundaries without accounts or external effects."""

import json
import socket
import tempfile
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event
from typing import Any, NoReturn

import aiohttp
import keyring
import pytest
from keyring.backend import KeyringBackend

from jarvis.core.planner import openai_provider
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.core.planner.offline import OfflineProvider
from jarvis.installation import offline_checks, probe
from jarvis.mail.credentials import ProcessCredentials
from jarvis.mail.graph import GraphTransport
from jarvis.mail.models import Account, MailResult, Message
from jarvis.mail.session import MailSession
from jarvis.memory.store import MemoryStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms import credentials
from jarvis.security import credentials as provider_credentials
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ExecutionContext


def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
    pytest.fail("Offline diagnostic attempted an account, approval authority or network operation")


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ApprovalStore, "take_authority", forbidden)
    monkeypatch.setattr(openai_provider.OpenAIProvider, "__init__", forbidden)
    monkeypatch.setattr(openai_provider, "request", forbidden)
    monkeypatch.setattr(provider_credentials, "load_api_key", forbidden)
    monkeypatch.setattr(credentials, "native_store", forbidden)
    monkeypatch.setattr(ProcessCredentials, "call", forbidden)
    monkeypatch.setattr(GraphTransport, "request", forbidden)
    monkeypatch.setattr(aiohttp, "ClientSession", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(NetworkPolicy, "resolve", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    for name in (
        "get_keyring",
        "get_password",
        "get_credential",
        "set_password",
        "delete_password",
    ):
        monkeypatch.setattr(keyring, name, forbidden)


def test_real_local_probes_use_only_disposable_storage_and_print_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_data = tmp_path / "user-data"
    user_data.mkdir()
    sentinel = user_data / "memory.sqlite3"
    sentinel.write_bytes(b"Private profile remains untouched")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(user_data))
    directories: list[Path] = []
    storage: list[Path] = []
    temporary_directory = tempfile.TemporaryDirectory
    audit_init = AuditLog.__init__
    memory_init = MemoryStore.__init__

    def temporary(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
        directory = temporary_directory(*args, **kwargs)
        directories.append(Path(directory.name))
        return directory

    def audit(self: AuditLog, path: Path) -> None:
        storage.append(path)
        audit_init(self, path)

    def memory(self: MemoryStore, path: Path, **kwargs: Any) -> None:
        storage.append(path)
        memory_init(self, path, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary)
    monkeypatch.setattr(AuditLog, "__init__", audit)
    monkeypatch.setattr(MemoryStore, "__init__", memory)
    offline_checks.check_permissions()
    offline_checks.check_planner()
    offline_checks.check_memory()
    offline_checks.check_browser_policy()
    offline_checks.check_outlook_local()
    assert len(directories) == 5 and storage
    assert all(any(path.is_relative_to(directory) for directory in directories) for path in storage)
    assert all(not directory.exists() for directory in directories)
    assert sentinel.read_bytes() == b"Private profile remains untouched"
    assert capsys.readouterr() == ("", "")


def test_permission_probe_executes_and_verifies_safe_tool_without_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, Mode, Outcome]] = []
    execute = PermissionEngine.execute

    async def track(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        assert token is None
        result = await execute(self, action, token)
        observed.append((action.tool, action.mode, result))
        return result

    monkeypatch.setattr(PermissionEngine, "execute", track)
    offline_checks.check_permissions()
    safe = next(result for name, _, result in observed if name == "local.check")
    assert safe.status is Status.SUCCESS
    assert json.loads(safe.result_json or "null") == {"completed": True}
    assert (
        next(result for _, mode, result in observed if mode is Mode.SIMULATION).status
        is Status.SIMULATED
    )
    assert (
        next(result for name, _, result in observed if name == "local.append_message").status
        is Status.DENIED
    )
    assert observed[-1][2].status is Status.CANCELLED


def test_permission_probe_catches_simulation_invoking_an_adapter_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = PermissionEngine.execute

    async def unsafe_simulation(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        if action.mode is Mode.SIMULATION:
            tool = self._registry.get(action.tool)
            assert tool is not None
            await tool.check(action.payload, ExecutionContext(Event()))
        return await execute(self, action, token)

    monkeypatch.setattr(PermissionEngine, "execute", unsafe_simulation)
    with pytest.raises(RuntimeError, match="^offline_permissions_failed$"):
        offline_checks.check_permissions()


def test_permission_probe_catches_missing_durable_audit_after_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 0
    original = AuditLog

    class LostAudit(AuditLog):
        def recent(self, limit: int = 200) -> list[str]:
            return []

    def open_audit(path: Path) -> AuditLog:
        nonlocal count
        count += 1
        return original(path) if count == 1 else LostAudit(path)

    monkeypatch.setattr(offline_checks, "AuditLog", open_audit)
    with pytest.raises(RuntimeError, match="^offline_permissions_failed$"):
        offline_checks.check_permissions()


def test_planner_probe_runs_exactly_two_safe_local_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, Mode, Outcome]] = []
    execute = PermissionEngine.execute

    async def track(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        assert token is None
        result = await execute(self, action, token)
        observed.append((action.tool, action.mode, result))
        return result

    monkeypatch.setattr(PermissionEngine, "execute", track)
    offline_checks.check_planner()
    assert len(observed) == 2
    assert all(
        name == "local.check" and mode is Mode.EXECUTE and result.status is Status.SUCCESS
        for name, mode, result in observed
    )


def test_planner_probe_rejects_an_incomplete_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    propose = OfflineProvider.propose

    async def stop_early(self: OfflineProvider, data: PlannerInput) -> Proposal:
        return Proposal(kind="finish") if data.steps else await propose(self, data)

    monkeypatch.setattr(OfflineProvider, "propose", stop_early)
    with pytest.raises(RuntimeError, match="^offline_planner_failed$"):
        offline_checks.check_planner()


def test_memory_probe_detects_missing_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MemoryStore, "read", lambda *args, **kwargs: ())
    with pytest.raises(RuntimeError, match="^offline_memory_failed$"):
        offline_checks.check_memory()


def test_browser_probe_simulates_navigation_and_requires_confirmation_for_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, Risk, Mode, Outcome]] = []
    execute = PermissionEngine.execute

    async def track(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        assert token is None
        result = await execute(self, action, token)
        observed.append((action.tool, action.risk, action.mode, result))
        return result

    monkeypatch.setattr(PermissionEngine, "execute", track)
    offline_checks.check_browser_policy()
    assert [(name, risk, mode, outcome.status) for name, risk, mode, outcome in observed] == [
        ("browser.get_tabs", Risk.SAFE, Mode.SIMULATION, Status.SIMULATED),
        ("browser.read", Risk.SAFE, Mode.SIMULATION, Status.SIMULATED),
        ("browser.open", Risk.SAFE, Mode.SIMULATION, Status.SIMULATED),
        ("browser.click", Risk.CONFIRM, Mode.SIMULATION, Status.DENIED),
    ]
    assert all(not outcome.may_have_effects for _, _, _, outcome in observed)


def test_browser_probe_detects_adapter_hook_even_when_its_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = PermissionEngine.execute

    async def unsafe_simulation(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        if action.tool == "browser.get_tabs":
            tool = self._registry.get(action.tool)
            assert tool is not None
            with suppress(RuntimeError):
                await tool.check(action.payload, ExecutionContext(Event()))
        return await execute(self, action, token)

    monkeypatch.setattr(PermissionEngine, "execute", unsafe_simulation)
    with pytest.raises(RuntimeError, match="^offline_browser_policy_failed$"):
        offline_checks.check_browser_policy()


@pytest.mark.parametrize("defect", ["url", "post"])
def test_browser_probe_detects_disabled_url_or_post_policy(
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    validate_request = NetworkPolicy.validate_request

    def bypass(self: NetworkPolicy, url: str, method: str, body: str = "") -> str:
        if defect == "url" or method == "POST":
            return url
        return validate_request(self, url, method, body)

    monkeypatch.setattr(NetworkPolicy, "validate_request", bypass)
    with pytest.raises(RuntimeError, match="^offline_browser_policy_failed$"):
        offline_checks.check_browser_policy()


def test_outlook_probe_executes_ram_draft_simulates_save_and_denies_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, Risk, Outcome]] = []
    execute = PermissionEngine.execute

    async def track(
        self: PermissionEngine, action: Action, token: ApprovalToken | None = None
    ) -> Outcome:
        assert token is None
        assert action.mode is (
            Mode.SIMULATION if action.tool == "outlook.save_draft" else Mode.EXECUTE
        )
        result = await execute(self, action, token)
        observed.append((action.tool, action.risk, result))
        return result

    monkeypatch.setattr(PermissionEngine, "execute", track)
    offline_checks.check_outlook_local()
    assert [(name, risk, outcome.status) for name, risk, outcome in observed] == [
        ("outlook.local_draft", Risk.SAFE, Status.SUCCESS),
        ("outlook.local_draft", Risk.SAFE, Status.DENIED),
        ("outlook.save_draft", Risk.SAFE, Status.SIMULATED),
        ("outlook.send", Risk.CONFIRM, Status.DENIED),
    ]


def test_outlook_probe_detects_draft_content_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    local_draft = MailSession.local_draft

    def corrupt(self: MailSession, account: Account, message: Message) -> MailResult:
        original = local_draft(self, account, message)
        return MailResult.model_validate(original.model_dump() | {"data": "corrupted"})

    monkeypatch.setattr(MailSession, "local_draft", corrupt)
    with pytest.raises(RuntimeError, match="^offline_outlook_local_failed$"):
        offline_checks.check_outlook_local()


def test_outlook_probe_detects_drafts_not_cleared_on_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MailSession, "detach", lambda self: None)
    with pytest.raises(RuntimeError, match="^offline_outlook_local_failed$"):
        offline_checks.check_outlook_local()


@pytest.mark.parametrize("name", ["browser_policy", "outlook_local"])
def test_extra_offline_probes_are_in_installer_dispatch(name: str) -> None:
    assert name in probe.PROBE_CHECKS
    assert probe.run_check(name) == {"status": "passed", "code": "none"}


class ConstructOnlyBackend(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        pass

    def get_password(self, service: str, username: str) -> str | None:
        forbidden()

    def set_password(self, service: str, username: str, password: str) -> None:
        forbidden()

    def delete_password(self, service: str, username: str) -> None:
        forbidden()


def test_credentials_probe_constructs_backend_without_any_credential_operation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[str] = []

    def construct() -> KeyringBackend:
        called.append("construct")
        return ConstructOnlyBackend()

    monkeypatch.setattr(credentials, "native_store", construct)
    offline_checks.check_credentials_backend()
    assert called == ["construct"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("probe", "error_code"),
    [
        (offline_checks.check_permissions, "offline_permissions_failed"),
        (offline_checks.check_planner, "offline_planner_failed"),
        (offline_checks.check_memory, "offline_memory_failed"),
        (offline_checks.check_browser_policy, "offline_browser_policy_failed"),
        (offline_checks.check_outlook_local, "offline_outlook_local_failed"),
        (offline_checks.check_credentials_backend, "credentials_backend_unavailable"),
    ],
)
def test_failures_are_fixed_categories_without_underlying_user_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    probe: Callable[[], None],
    error_code: str,
) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("PRIVATE_PATH_OR_CREDENTIAL_SENTINEL")

    monkeypatch.setattr(offline_checks, "local_registry", unavailable)
    monkeypatch.setattr(offline_checks, "MemoryStore", unavailable)
    monkeypatch.setattr(offline_checks, "register_browser", unavailable)
    monkeypatch.setattr(offline_checks, "register_outlook", unavailable)
    monkeypatch.setattr(credentials, "native_store", unavailable)
    with pytest.raises(RuntimeError) as error:
        probe()
    assert str(error.value) == error_code
    assert error.value.__suppress_context__
    assert capsys.readouterr() == ("", "")
