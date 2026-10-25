"""Installer exercises use real local boundaries without accounts or external effects."""

import socket
import urllib.request
from contextlib import suppress
from threading import Event
from typing import Any, NoReturn

import aiohttp
import keyring
import pytest
from keyring.backend import KeyringBackend

from jarvis.core.planner import openai_provider
from jarvis.installation import offline_checks, probe
from jarvis.mail.credentials import ProcessCredentials
from jarvis.mail.graph import GraphTransport
from jarvis.mail.models import Account, MailResult, Message
from jarvis.mail.session import MailSession
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
