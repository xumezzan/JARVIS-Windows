"""Browser contracts, exact permissions, and DNS transport boundaries."""

import asyncio
import json
import socket
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from jarvis.browser.network import PolicyResolver
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import (
    DEFAULT_READ_ONLY,
    PASSWORD_MANAGERS,
    NetworkPolicy,
    normalized_url,
)
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.browser import (
    BrowserCommand,
    BrowserResult,
    PageTarget,
    SearchWeb,
    register_browser,
)
from jarvis.tools.registry import ToolRegistry

TARGET = PageTarget(
    tab_id="a" * 32,
    document_id="b" * 32,
    url="https://example.com/",
    origin="https://example.com",
    fingerprint="c" * 64,
)
FIELD = {"role": "textbox", "name": "Message", "fingerprint": "d" * 64}
BUTTON = {
    "role": "button",
    "name": "Send",
    "fingerprint": "e" * 64,
    "request": {"url": "https://example.com/submit", "method": "GET", "body": ""},
}
CASES = [
    ("open", {"url": TARGET.url}),
    ("navigate", {"target": TARGET.model_dump(), "url": "https://example.com/next"}),
    ("search", {"query": "OpenAI"}),
    ("click", {"target": TARGET.model_dump(), "element": BUTTON}),
    ("type", {"target": TARGET.model_dump(), "element": FIELD, "text": "test"}),
    ("read", {"target": TARGET.model_dump()}),
    ("get_tabs", {}),
    ("close", {"target": TARGET.model_dump()}),
]


class Probe:
    def __init__(self) -> None:
        self.calls: list[BrowserCommand] = []

    async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
        self.calls.append(command)
        return BrowserResult()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", CASES)
async def test_all_simulation_hooks_are_skipped(name: str, args: object, tmp_path: Path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("browser." + name, args, Mode.SIMULATION)
        assert isinstance(action, Action)
        assert (await engine.execute(action)).status is Status.SIMULATED
        assert not probe.calls


@pytest.mark.asyncio
async def test_approval_binds_every_target_and_form_field(tmp_path: Path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        for key, value in [
            ("tab_id", "f" * 32),
            ("frame", "other"),
            ("origin", "https://other.org"),
            ("fingerprint", "a" * 64),
        ]:
            action = engine.prepare(
                "browser.click", {"target": TARGET.model_dump(), "element": BUTTON}
            )
            assert isinstance(action, Action)
            args = json.loads(action.payload)
            args["target"][key] = value
            assert (await engine.execute(replace(action, payload=json.dumps(args)))).status in (
                Status.DENIED,
                Status.INVALID,
            )
        action = engine.prepare("browser.click", {"target": TARGET.model_dump(), "element": BUTTON})
        assert isinstance(action, Action)
        args = json.loads(action.payload)
        args["element"]["request"]["body"] = "body=changed"
        assert (
            await engine.execute(replace(action, payload=json.dumps(args)))
        ).status is Status.DENIED
        assert not probe.calls


@pytest.mark.asyncio
async def test_empty_adapter_result_is_not_success(tmp_path: Path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("browser.open", {"url": TARGET.url}, Mode.EXECUTE)
        assert isinstance(action, Action)
        result = await engine.execute(action)
        assert result.status is Status.ERROR and result.error is ErrorCode.VERIFICATION


@pytest.mark.parametrize(
    "name,args",
    [
        ("open", {"url": TARGET.url, "fixture_origin": "http://127.0.0.1:8000"}),
        ("open", {"url": TARGET.url, "javascript": "alert(1)"}),
        ("type", {"target": TARGET.model_dump(), "element": FIELD, "text": "bad\x00"}),
        ("type", {"target": TARGET.model_dump(), "element": BUTTON, "text": "test"}),
        ("click", {"target": TARGET.model_dump(), "element": FIELD}),
        ("read", {"target": TARGET.model_dump() | {"frame": "iframe"}}),
        ("search", {"query": "OpenAI", "url": "https://example.com/"}),
    ],
)
def test_invalid_schema_never_prepares(name: str, args: object, tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_browser(registry, Probe())
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        result = PermissionEngine(registry, ApprovalStore(), audit).prepare("browser." + name, args)
        assert isinstance(result, Outcome) and result.status is Status.INVALID


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,test",
        "https://example.com@localhost/",
        "https://user:pass@example.com/",
        "https://example.com/#fragment",
        "https://example.com:0/",
        "https://example.com\\@localhost/",
        "https://example.com\n/",
        "https://%65xample.com/",
    ],
)
def test_malformed_urls_fail_closed(url: str) -> None:
    with pytest.raises(ToolError, match="network_denied"):
        normalized_url(url)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "224.0.0.1",
        "localhost",
        "test.local",
    ],
)
def test_allowlist_cannot_enable_private_network(host: str) -> None:
    url = "https://" + host
    with pytest.raises(ToolError, match="network_denied"):
        NetworkPolicy((url,)).validate(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], []])
async def test_dns_private_or_mixed_answers_rejected(
    addresses: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    with pytest.raises(ToolError, match="network_denied"):
        await PolicyResolver(NetworkPolicy()).resolve("example.com", 443)


@pytest.mark.asyncio
async def test_connector_receives_only_the_checked_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    answers = await PolicyResolver(NetworkPolicy()).resolve("example.com", 443)
    assert [answer["host"] for answer in answers] == ["93.184.216.34"]
    assert answers[0]["flags"] == socket.AI_NUMERICHOST
    lookup.assert_awaited_once()


def test_exact_origins_and_fixture_post_boundary() -> None:
    policy = NetworkPolicy()
    assert policy.validate("https://EXAMPLE.com:443/") == "https://example.com/"
    for url in ("https://sub.example.com/", "https://example.com.evil.org/", "http://example.com/"):
        with pytest.raises(ToolError):
            policy.validate(url)
    with pytest.raises(ToolError):
        policy.validate_request("https://example.com/submit", "POST", "body=test")
    fixture = NetworkPolicy(fixture_origin="http://127.0.0.1:8000")
    assert fixture.validate_request("http://127.0.0.1:8000/submit", "POST", "body=test")
    for url in ("http://127.0.0.1:8001/submit", "http://127.0.0.1:8000/other"):
        with pytest.raises(ToolError):
            fixture.validate_request(url, "POST", "body=test")


def test_search_destination_contains_exact_query() -> None:
    assert (
        SearchWeb(query="OpenAI & test").url
        == "https://lite.duckduckgo.com/lite/?q=OpenAI+%26+test"
    )
    with pytest.raises(ValidationError):
        SearchWeb(query="OpenAI", url="https://example.com/")


@pytest.mark.parametrize("mode", [Mode.EXECUTE, Mode.SIMULATION])
@pytest.mark.parametrize(
    "name,args",
    [
        ("open", {"url": "http://127.0.0.1:8000/"}),
        ("open", {"url": "https://unknown.example/"}),
        (
            "click",
            {
                "target": TARGET.model_dump(),
                "element": BUTTON
                | {"request": {"url": TARGET.url, "method": "POST", "body": "message=test"}},
            },
        ),
    ],
)
def test_network_policy_also_runs_in_simulation(
    name: str, args: object, mode: Mode, tmp_path: Path
) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        outcome = PermissionEngine(registry, ApprovalStore(), audit).prepare(
            "browser." + name, args, mode
        )
        assert isinstance(outcome, Outcome) and outcome.status is Status.INVALID
        assert not probe.calls


def test_tls_keeps_verification_and_never_logs_session_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ssl

    from jarvis.browser.network import tls_context

    keys = tmp_path / "tls-keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keys))
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.get_ca_certs()
    assert not context.keylog_filename
    assert not keys.exists()


def test_tls_fallback_preserves_existing_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    import certifi

    from jarvis.browser.network import tls_context

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    monkeypatch.setattr("jarvis.browser.network.ssl.SSLContext", lambda protocol: context)

    def unexpected_bundle() -> str:
        raise AssertionError("Existing trusted roots must be preserved")

    monkeypatch.setattr("jarvis.browser.network.certifi.where", unexpected_bundle)
    assert tls_context() is context


def test_a_password_manager_is_refused_before_anything_else_is_considered() -> None:
    """The one site an assistant holding the owner's live sessions must never reach."""
    policy = NetworkPolicy(allowed_origins=("https://my.1password.com", "https://example.com"))
    # Allowed by the owner's own allowlist and still refused: the denial is checked first,
    # so no later capability can reopen it.
    for url in (
        "https://my.1password.com/",
        "https://my.1password.com/vaults",
        "https://start.1password.com/signin",
        "https://anything.bitwarden.com/",
    ):
        with pytest.raises(ToolError):
            policy.validate(url)
        assert policy.tier(url) == "blocked"
    # A name that merely ends with the same letters is a different site.
    assert policy.tier("https://not1password.com/") == "ordinary"
    assert policy.tier("https://1password.com.evil.org/") == "ordinary"


def test_money_and_admin_consoles_may_be_read_and_not_changed() -> None:
    """`CRITICAL` is disabled until decision Р5 exists, so these are looked at only."""
    sites = ("https://qbo.intuit.com", "https://us-east-1.console.aws.amazon.com")
    policy = NetworkPolicy(allowed_origins=sites)
    for url in (site + "/" for site in sites):
        # Reading is navigation, and navigation stays available.
        assert policy.validate(url) == url
        assert policy.tier(url) == "read_only" and not policy.writable(url)
        # Sending anything is not reading, whatever the form says it does.
        with pytest.raises(ToolError):
            policy.validate_request(url, "POST", "amount=1000")
    ordinary = "https://example.com/"
    assert NetworkPolicy().tier(ordinary) == "ordinary"


def test_a_tier_covers_subdomains_and_the_allowlist_still_does_not() -> None:
    """The asymmetry is deliberate: a loose denial is safe, a loose permission is not."""
    policy = NetworkPolicy(allowed_origins=("https://example.com",))
    # Denials reach the subdomains of what they name.
    assert policy.tier("https://console.aws.amazon.com/billing") == "read_only"
    assert policy.tier("https://eu-west-2.console.aws.amazon.com/") == "read_only"
    # Permissions do not.
    with pytest.raises(ToolError):
        policy.validate("https://sub.example.com/")


def test_a_tier_list_is_bare_lowercase_domains() -> None:
    for bad in ("https://1password.com", "1Password.com", "example.com/path", "example.com:443"):
        with pytest.raises(ValueError):
            NetworkPolicy(blocked_domains=(bad,))
    assert all(name == name.lower() for name in (*PASSWORD_MANAGERS, *DEFAULT_READ_ONLY))


@pytest.mark.parametrize("mode", [Mode.SIMULATION, Mode.EXECUTE])
def test_typing_into_a_read_only_site_never_prepares(mode: Mode, tmp_path: Path) -> None:
    """A site the owner may only look at accepts no text, in either mode.

    Typing is how a page is changed even when the change leaves by some other route, so the
    refusal lands at normalization - before the adapter exists and identically in simulation.
    """
    console = PageTarget(
        tab_id="a" * 32,
        document_id="b" * 32,
        url="https://qbo.intuit.com/app/invoice",
        origin="https://qbo.intuit.com",
        fingerprint="c" * 64,
    )
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe, NetworkPolicy(allowed_origins=("https://qbo.intuit.com",)))
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        typing = engine.prepare(
            "browser.type",
            {"target": console.model_dump(), "element": FIELD, "text": "1000"},
            mode,
        )
        assert isinstance(typing, Outcome) and typing.status is Status.INVALID
        # Reading the same page is still prepared: looking is not changing.
        reading = engine.prepare("browser.read", {"target": console.model_dump()}, mode)
        assert not isinstance(reading, Outcome)
        engine.cancel(reading)
        assert not probe.calls
