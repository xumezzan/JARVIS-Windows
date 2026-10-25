"""Teams connector. No Microsoft account, token, network or real chat is used."""

import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest

from jarvis.connectors.http import TransportError
from jarvis.connectors.teams.api import ROUTES, TeamsFailure, transport
from jarvis.connectors.teams.connector import CAPABILITIES, TeamsConnector
from jarvis.connectors.teams.mapping import MAPPERS
from jarvis.connectors.teams.models import ChatsInput, MessagesInput, TeamsResult
from jarvis.connectors.teams.tools import register_teams
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

ACCOUNT = Account(user_id="fixture-user", address="owner@example.test", session="a" * 32)
OTHER = Account(user_id="other-user", address="someone@example.test", session="b" * 32)
GROUP = "19:2da4c29f6d7041eca70b638b43d45437@thread.v2"
ALONE = (
    "19:d74fc2ed-cb0e-4288-a219-b5c71abaf2aa_8c0a1a67-50ce-4114-bb6c-da9c5dbcf6ca@unq.gbl.spaces"
)
RUN = "c" * 32


def raw_chat(identifier: str = GROUP, topic: str | None = "Проект Альфа") -> dict[str, Any]:
    return {
        "id": identifier,
        "topic": topic,
        "chatType": "group" if identifier == GROUP else "oneOnOne",
        "createdDateTime": "2026-09-01T10:00:00Z",
        "lastUpdatedDateTime": "2026-09-18T07:30:00.12Z",
    }


def raw_message(
    identifier: str = "1616964509832",
    content: str = "Смета готова",
    kind: str = "text",
    message_type: str = "message",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "messageType": message_type,
        "createdDateTime": "2026-09-18T07:29:59.83Z",
        "deletedDateTime": None,
        "from": {
            "application": None,
            "device": None,
            "user": {"id": "8ea0", "displayName": "Иван Петров", "userIdentityType": "aadUser"},
        },
        "body": {"contentType": kind, "content": content},
    }


class FakeGraph:
    """Records what the connector asked for and answers with fixture shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.params: list[dict[str, str]] = []
        self.payloads: list[dict[str, Any]] = []
        self.chats: list[dict[str, Any]] = [raw_chat()]
        self.messages: list[dict[str, Any]] = [raw_message()]
        self.stored: dict[str, Any] = {}
        self.deny: str = ""

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert transport().allows(method, path), f"{method} {path} is not an allowed route"
        self.calls.append((method, path))
        if params is not None:
            self.params.append(dict(params))
        if payload is not None:
            self.payloads.append(dict(payload))
        if self.deny:
            raise TransportError(self.deny)  # type: ignore[arg-type]
        if path == "/me/chats":
            return 200, {"value": list(self.chats)}
        if method == "POST":
            body = (payload or {}).get("body")
            content = body.get("content") if isinstance(body, dict) else ""
            self.stored = raw_message("1700000000001", str(content))
            return 201, dict(self.stored)
        if path.endswith("/messages"):
            return 200, {"value": list(self.messages)}
        return 200, dict(self.stored)


class FakeSession:
    """The Microsoft session, reduced to what a connector is allowed to ask of it."""

    def __init__(self) -> None:
        self.account: Account | None = ACCOUNT
        self.surfaces: list[str] = []
        self.refused: set[str] = set()

    def matches(self, account: Account, message: object = None) -> bool:
        return self.account == account

    async def surface_token(self, account: Account, surface: str, context: ExecutionContext) -> str:
        await context.checkpoint()
        self.surfaces.append(surface)
        if self.account != account:
            raise MailFailure("mail_account_changed")
        if surface in self.refused:
            raise MailFailure("mail_credentials")
        return "synthetic-noncredential"


@pytest.fixture
def bench() -> Iterator[tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext]]:
    graph, session = FakeGraph(), FakeSession()
    connector = TeamsConnector(session, graph)  # type: ignore[arg-type]
    yield connector, graph, session, ExecutionContext(Flag())


def test_the_transport_reaches_chats_and_nothing_else() -> None:
    allowed = {(route.method, route.path) for route in ROUTES}
    assert len(allowed) == 4
    # Channels are absent, not guarded: reading them needs tenant admin consent.
    assert not any("channels" in path or "joinedTeams" in path for _, path in allowed)
    # Nothing here deletes a message, edits one, or reads somebody else's mailbox.
    assert not any(method in ("DELETE", "PATCH", "PUT") for method, _ in allowed)
    assert not any("/users/" in path or "/me/messages" in path for _, path in allowed)
    guard = transport()
    assert guard.allows("GET", f"/chats/{GROUP}/messages")
    assert guard.allows("GET", f"/chats/{ALONE}/messages")
    assert not guard.allows("DELETE", f"/chats/{GROUP}/messages/1616964509832")
    assert not guard.allows("GET", "/chats/19:someone@evil.test/messages")
    assert not guard.allows("GET", "/teams/abc/channels")


def test_sending_asks_before_it_happens_and_reading_does_not() -> None:
    registry = ToolRegistry()
    register_teams(registry, TeamsConnector(FakeSession()))  # type: ignore[arg-type]
    assert registry.get("teams.send").risk is Risk.CONFIRM  # type: ignore[union-attr]
    for name in ("teams.chats", "teams.messages", "teams.draft"):
        assert registry.get(name).risk is Risk.SAFE  # type: ignore[union-attr]
    writes = {capability.name for capability in CAPABILITIES if capability.writes}
    assert writes == {"send"}
    assert all(capability.idempotent for capability in CAPABILITIES if not capability.writes)


def test_the_owner_may_tighten_a_capability_but_never_loosen_it() -> None:
    registry = ToolRegistry()
    matrix = PermissionMatrix({"teams": {"messages": Rule(risk=Risk.CONFIRM)}})
    register_teams(registry, TeamsConnector(FakeSession()), matrix)  # type: ignore[arg-type]
    assert registry.get("teams.messages").risk is Risk.CONFIRM  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_chats_come_back_newest_first_and_an_unknown_kind_stays_unknown(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, session, context = bench
    graph.chats = [raw_chat(), raw_chat(ALONE, None), {"id": GROUP, "chatType": "channel"}]
    result = await connector.chats(ChatsInput(account=ACCOUNT, limit=5), context)
    assert graph.calls[0] == ("GET", "/me/chats")
    assert graph.params[0]["$orderby"] == "lastMessagePreview/createdDateTime desc"
    assert graph.params[0]["$top"] == "5"
    rows = json.loads(result.data)
    assert [row["kind"] for row in rows] == ["group", "oneOnOne", "unknown"]
    # A chat without a topic keeps an empty one rather than inventing a name.
    assert rows[1]["topic"] == ""
    # Teams was asked for its own consent, never for the mailbox's.
    assert session.surfaces == ["teams"]


@pytest.mark.asyncio
async def test_a_message_is_read_as_words_and_events_are_left_out(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    picture = "<div><p>Смета &amp; счёт</p><img src='https://tracker.test/x'></div>"
    graph.messages = [
        raw_message(content=picture, kind="html"),
        raw_message("1616964509833", "<systemEventMessage/>", "html", "systemEventMessage"),
        {**raw_message("1616964509834"), "deletedDateTime": "2026-09-18T08:00:00Z"},
    ]
    result = await connector.messages(MessagesInput(account=ACCOUNT, chat=GROUP, limit=10), context)
    assert graph.calls[0] == ("GET", f"/chats/{GROUP}/messages")
    assert graph.params[0]["$orderby"] == "createdDateTime desc"
    rows = json.loads(result.data)
    # One message survives: a system event and a deleted message are not what was said.
    assert len(rows) == 1
    assert rows[0]["text"] == "Смета & счёт"
    assert rows[0]["author"] == "Иван Петров"
    # Nothing inside the message was fetched: one call went out, and it was the listing.
    assert len(graph.calls) == 1


@pytest.mark.asyncio
async def test_more_rows_than_were_asked_for_is_refused_rather_than_trimmed(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    graph.chats = [raw_chat(), raw_chat(ALONE), raw_chat()]
    with pytest.raises(TeamsFailure) as failure:
        await connector.chats(ChatsInput(account=ACCOUNT, limit=1), context)
    assert failure.value.code == "teams_response"


@pytest.mark.asyncio
async def test_a_draft_sends_nothing_and_shows_exactly_what_would_be_sent(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_teams(registry, connector)
    spec = registry.get("teams.draft")
    assert spec is not None
    args = spec.normalize(
        {"account": ACCOUNT.model_dump(), "message": {"chat": GROUP, "text": " Итоги недели  "}}
    )
    result = await spec.run(args, context)
    assert isinstance(result, TeamsResult) and result.state == "draft"
    # No request left the machine, and the preview is the message, trimmed exactly once.
    assert graph.calls == []
    assert json.loads(result.data) == {"chat": GROUP, "text": "Итоги недели"}
    assert await spec.verify(args, result, context) is True


@pytest.mark.asyncio
async def test_a_sent_message_is_read_back_before_it_is_called_done(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_teams(registry, connector)
    spec = registry.get("teams.send")
    assert spec is not None
    args = spec.normalize(
        {
            "account": ACCOUNT.model_dump(),
            "message": {"chat": GROUP, "text": "Отчёт за неделю\nГотов"},
        }
    )
    result = await spec.run(args, context)
    assert isinstance(result, TeamsResult) and result.state == "sent"
    assert graph.calls[0] == ("POST", f"/chats/{GROUP}/messages")
    # Plain text on purpose: nothing approved as words may arrive as markup.
    assert graph.payloads[0] == {
        "body": {"contentType": "text", "content": "Отчёт за неделю\nГотов"}
    }
    assert await spec.verify(args, result, context) is True
    assert graph.calls[1] == ("GET", f"/chats/{GROUP}/messages/1700000000001")


@pytest.mark.asyncio
async def test_a_message_that_came_back_mangled_is_not_reported_as_sent(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_teams(registry, connector)
    spec = registry.get("teams.send")
    assert spec is not None
    args = spec.normalize(
        {"account": ACCOUNT.model_dump(), "message": {"chat": GROUP, "text": "Отчёт за неделю"}}
    )
    result = await spec.run(args, context)
    graph.stored = raw_message("1700000000001", "Отчёт за")
    assert await spec.verify(args, result, context) is False


@pytest.mark.asyncio
async def test_a_tool_refuses_an_account_this_session_is_not_signed_in_as(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, _, context = bench
    registry = ToolRegistry()
    register_teams(registry, connector)
    spec = registry.get("teams.chats")
    assert spec is not None
    args = spec.normalize({"account": OTHER.model_dump()})
    assert await spec.check(args, context) is False


@pytest.mark.asyncio
async def test_a_teams_consent_that_was_never_given_is_not_reported_as_a_changed_account(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, session, context = bench
    session.refused.add("teams")
    with pytest.raises(TeamsFailure) as failure:
        await connector.chats(ChatsInput(account=ACCOUNT), context)
    assert failure.value.code == "teams_credentials"
    session.refused.clear()
    session.account = OTHER
    with pytest.raises(TeamsFailure) as changed:
        await connector.chats(ChatsInput(account=ACCOUNT), context)
    assert changed.value.code == "teams_account_changed"


@pytest.mark.asyncio
async def test_teams_wording_never_crosses_the_boundary(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    for code, expected in (
        ("credentials", "teams_credentials"),
        ("forbidden", "teams_forbidden"),
        ("not_found", "teams_missing"),
        ("rate_limited", "teams_rate_limited"),
        ("server", "teams_network"),
        ("response_unparsable", "teams_response"),
    ):
        graph.deny = code
        with pytest.raises(TeamsFailure) as failure:
            await connector.chats(ChatsInput(account=ACCOUNT), context)
        assert failure.value.code == expected


@pytest.mark.asyncio
async def test_a_cancelled_task_stops_before_the_request_is_issued(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    import asyncio

    connector, graph, _, _ = bench
    flag = Flag()
    flag.set()
    with pytest.raises(asyncio.CancelledError):
        await connector.chats(ChatsInput(account=ACCOUNT), ExecutionContext(flag))
    assert graph.calls == []


@pytest.mark.asyncio
async def test_health_answers_for_the_teams_consent_and_not_only_for_the_sign_in(
    bench: tuple[TeamsConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, session, _ = bench
    assert (await connector.health_check()).state == "ready"
    assert (await connector.authenticate()).account == ACCOUNT.address
    session.refused.add("teams")
    health = await connector.health_check()
    assert (health.state, health.reason) == ("unauthenticated", "credentials")
    # The mailbox is still signed in, which is exactly the point of a separate consent.
    assert (await connector.authenticate()).state == "connected"
    session.account = None
    assert (await connector.health_check()).state == "unauthenticated"
    assert (await connector.authenticate()).state == "disconnected"


def test_a_chat_reads_as_a_conversation_and_an_author_as_a_person_known_to_teams() -> None:
    chats = MAPPERS["teams.chats"](
        "teams.chats",
        RUN,
        {"data": json.dumps([raw_chat(), {"id": ALONE, "topic": None}])},
    )
    names = [entity.name for entity in chats.entities if entity is not None]
    assert names == ["Проект Альфа", "Чат без названия"]
    messages = MAPPERS["teams.messages"](
        "teams.messages",
        RUN,
        {
            "chat": GROUP,
            "data": json.dumps(
                [
                    {"id": "1", "author": "Иван Петров", "text": "Смета"},
                    {"id": "2", "author": "Иван Петров", "text": "И счёт"},
                ]
            ),
        },
    )
    people = [entity for entity in messages.entities[1:] if entity is not None]
    # One person, once, and named rather than addressed: no merge with the email graph.
    assert [person.name for person in people] == ["Иван Петров"]
    assert all(not person.external for person in people)
    assert messages.links[0].predicate == "participates_in"


def test_a_message_the_planner_wrote_cannot_carry_an_invented_chat() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MessagesInput(account=ACCOUNT, chat="19:not-a-thread@evil.test")
    with pytest.raises(ValidationError):
        MessagesInput(account=ACCOUNT, chat=GROUP, limit=500)
