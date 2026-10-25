"""Connector contract and shared transport. No account, secret or public network is used."""

from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest

from jarvis.connectors.base import (
    AuthState,
    Capability,
    ConnectorRegistry,
    EntityType,
    Health,
    tool_name,
)
from jarvis.connectors.http import Route, ServiceTransport, TransportError
from jarvis.permissions.policies import Risk

ROUTES = (Route("GET", r"/items/[a-z0-9]+"), Route("POST", r"/items"))


def transport(**overrides: Any) -> ServiceTransport:
    settings: dict[str, Any] = {"base_url": "https://api.example.test/v1", "routes": ROUTES}
    settings.update(overrides)
    base = settings.pop("base_url")
    routes = settings.pop("routes")
    return ServiceTransport(base, routes, **settings)


def install_session(
    monkeypatch: pytest.MonkeyPatch,
    status: int = 200,
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace the wire with a recorder; the transport's own settings stay under test."""
    sessions: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []

    class Content:
        async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
            yield body

    class Response:
        def __init__(self) -> None:
            self.status = status
            self.content = Content()
            self.headers = headers or {}

        async def __aenter__(self) -> "Response":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            sessions.append(kwargs)
            self.connector = kwargs["connector"]

        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: Any) -> None:
            await self.connector.close()

        def request(self, method: str, url: str, **kwargs: Any) -> Response:
            sent.append({"method": method, "url": url, **kwargs})
            return Response()

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    return sessions, sent


class FakeConnector:
    service = "example"

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            Capability("search_items", "Найти записи.", Risk.SAFE, True, reads=("task",)),
            Capability("create_item", "Создать запись.", Risk.CONFIRM, False, writes=("task",)),
        )

    async def authenticate(self) -> AuthState:
        return AuthState("connected", "owner@example.test")

    async def health_check(self) -> Health:
        return Health("ready")


def test_capability_metadata_must_be_trusted_and_a_write_is_never_safe() -> None:
    empty: tuple[EntityType, ...] = ()
    task: tuple[EntityType, ...] = ("task",)
    for name, description, risk, writes in (
        ("Search", "Ок.", Risk.SAFE, empty),
        ("search.items", "Ок.", Risk.SAFE, empty),
        ("search_items", "", Risk.SAFE, empty),
        ("search_items", "x" * 201, Risk.SAFE, empty),
        ("create_item", "Ок.", Risk.SAFE, task),
    ):
        with pytest.raises(ValueError):
            Capability(name, description, risk, True, writes=writes)
    with pytest.raises(ValueError):
        Capability("check", "Ок.", "SAFE", True)  # type: ignore[arg-type]


def test_a_capability_cannot_invent_a_tool_namespace() -> None:
    assert tool_name("asana", "create_task") == "asana.create_task"
    for service, capability in (
        ("Asana", "create_task"),
        ("asana", "create.task"),
        ("asana", "../local"),
        ("", "create_task"),
    ):
        with pytest.raises(ValueError):
            tool_name(service, capability)


def test_account_label_never_carries_control_characters() -> None:
    assert AuthState("disconnected").account == ""
    with pytest.raises(ValueError):
        AuthState("connected", "owner@example.test\nBearer x")


def test_registry_rejects_duplicates_and_describes_every_capability() -> None:
    registry = ConnectorRegistry()
    registry.add(FakeConnector())
    with pytest.raises(ValueError):
        registry.add(FakeConnector())
    assert registry.services() == ("example",)
    assert registry.get("missing") is None
    described = registry.describe()[0]
    capabilities = described["capabilities"]
    assert isinstance(capabilities, list)
    assert [row["tool"] for row in capabilities] == ["example.search_items", "example.create_item"]


def test_transport_construction_refuses_a_widened_surface() -> None:
    for overrides in (
        {"base_url": "http://api.example.test"},
        {"base_url": "https://user:pass@api.example.test"},
        {"base_url": "https://api.example.test/v1/"},
        {"base_url": "https://api.example.test/v1?query=1"},
        {"routes": ()},
        {"headers": {"Authorization": "Bearer x"}},
        {"headers": {"cookie": "a=b"}},
        {"headers": {"Prefer": "line\nbreak"}},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"max_response_bytes": 10},
        {"success_statuses": (200, 302)},
        {"authorization": "basic"},
    ):
        with pytest.raises(ValueError):
            transport(**overrides)
    for method, path in (("TRACE", "/items"), ("GET", "items"), ("GET", "/" + "x" * 300)):
        with pytest.raises(ValueError):
            Route(method, path)  # type: ignore[arg-type]


def test_routes_match_in_full_and_never_by_prefix() -> None:
    client = transport()
    assert client.allows("GET", "/items/abc") and client.allows("POST", "/items")
    for method, path in (
        ("GET", "/items/abc/secrets"),
        ("GET", "/items/abc?x=1"),
        ("POST", "/items/abc"),
        ("DELETE", "/items"),
        ("GET", "/Items/abc"),
    ):
        assert not client.allows(method, path)


@pytest.mark.asyncio
async def test_a_denied_route_or_argument_never_reaches_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, _ = install_session(monkeypatch)
    client = transport()
    for method, path, credential, params in (
        ("DELETE", "/items", "token", None),
        ("GET", "/items/abc/../../me", "token", None),
        ("GET", "/items/abc", "", None),
        ("GET", "/items/abc", "token\nHost: evil", None),
        ("GET", "/items/abc", "token", {"$select": "id\nCookie: a=b"}),
    ):
        with pytest.raises(TransportError):
            await client.request(credential, method, path, params=params)
    assert sessions == []


@pytest.mark.asyncio
async def test_wire_settings_stay_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions, sent = install_session(monkeypatch, body=b'{"id":"abc"}')
    status, data = await transport(headers={"Accept": "application/json"}).request(
        "synthetic-noncredential", "GET", "/items/abc", params={"$select": "id"}
    )
    assert (status, data) == (200, {"id": "abc"})
    settings = sessions[0]
    assert settings["trust_env"] is False
    assert isinstance(settings["cookie_jar"], aiohttp.DummyCookieJar)
    assert settings["auto_decompress"] is False
    request = sent[0]
    assert request["url"] == "https://api.example.test/v1/items/abc"
    assert request["allow_redirects"] is False
    assert request["headers"]["Accept-Encoding"] == "identity"
    assert request["headers"]["Authorization"] == "Bearer synthetic-noncredential"
    assert request["params"] == {"$select": "id"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,headers,code",
    [
        (302, "{}", None, "redirect"),
        (401, "{}", None, "credentials"),
        (403, "{}", None, "forbidden"),
        (404, "{}", None, "not_found"),
        (409, "{}", None, "conflict"),
        (429, "{}", None, "rate_limited"),
        (418, "{}", None, "rejected"),
        (503, "{}", None, "server"),
        (200, "oversize", None, "response_limit"),
        (200, "[1,2]", None, "response_invalid"),
        (200, "{oops", None, "response_unparsable"),
        (200, "{}", {"Content-Encoding": "gzip"}, "response_invalid"),
    ],
)
async def test_every_failure_has_one_finite_category(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: str,
    headers: dict[str, str] | None,
    code: str,
) -> None:
    # The oversize case is named, not inlined: a parametrised id of that size cannot be
    # written to the environment variable pytest exports for the running test.
    raw = b"x" * 262145 if body == "oversize" else body.encode()
    install_session(monkeypatch, status=status, body=raw, headers=headers)
    with pytest.raises(TransportError) as error:
        await transport().request("synthetic-noncredential", "GET", "/items/abc")
    assert error.value.code == code


@pytest.mark.asyncio
async def test_an_empty_success_is_an_empty_object(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, status=204, body=b"")
    assert await transport().request("synthetic-noncredential", "GET", "/items/abc") == (204, {})


@pytest.mark.asyncio
async def test_graph_keeps_its_own_error_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.mail.credentials import MailFailure
    from jarvis.mail.graph import GraphTransport

    for status, expected in (
        (401, "mail_credentials"),
        (302, "mail_network"),
        (500, "mail_network"),
    ):
        install_session(monkeypatch, status=status)
        with pytest.raises(MailFailure) as error:
            await GraphTransport().request("synthetic-noncredential", "GET", "/me")
        assert error.value.code == expected
