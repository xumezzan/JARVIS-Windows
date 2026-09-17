"""Microsoft Calendar connector. No account, token, network or real event is used."""

import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest
from tests.mail_support import FakeCredentials

from jarvis.connectors.microsoft.calendar import CAPABILITIES, CalendarConnector, free_slots
from jarvis.connectors.microsoft.graph import ROUTES, CalendarFailure
from jarvis.connectors.microsoft.models import (
    AvailabilityInput,
    CalendarResult,
    CreateInput,
    Draft,
    EventInput,
    RangeInput,
    SearchInput,
)
from jarvis.connectors.microsoft.tools import register_calendar
from jarvis.mail.models import Account
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

ACCOUNT = Account(user_id="fixture-user", address="owner@example.test", session="a" * 32)
START = "2026-09-16T09:00:00Z"
END = "2026-09-16T18:00:00Z"


def raw_event(
    identifier: str = "evt_1", subject: str = "Ревью", cancelled: bool = False
) -> dict[str, Any]:
    return {
        "id": identifier,
        "subject": subject,
        "start": {"dateTime": "2026-09-16T14:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-16T15:00:00.0000000", "timeZone": "UTC"},
        "organizer": {"emailAddress": {"address": "owner@example.test", "name": "Owner"}},
        "attendees": [
            {
                "emailAddress": {"address": "john@acme.test", "name": "John Smith"},
                "status": {"response": "accepted"},
            }
        ],
        "location": {"displayName": "Переговорная"},
        "isCancelled": cancelled,
    }


class FakeGraph:
    """Records what the connector asked for and answers with fixture shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict[str, Any]] = []
        self.params: list[dict[str, str]] = []
        self.events: dict[str, dict[str, Any]] = {"evt_1": raw_event()}
        self.view = "000011110000"
        self.missing = False

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((method, path))
        if payload is not None:
            self.payloads.append(dict(payload))
        if params is not None:
            self.params.append(dict(params))
        if path == "/me/calendarView":
            return 200, {"value": list(self.events.values())}
        if path == "/me/calendar/getSchedule":
            schedules = payload["schedules"] if payload else []
            assert isinstance(schedules, list)
            return 200, {"value": [{"availabilityView": self.view} for _ in schedules]}
        if method == "POST" and path == "/me/events":
            created = raw_event("evt_new", str((payload or {}).get("subject") or ""))
            self.events["evt_new"] = created
            return 201, created
        if path.endswith("/cancel"):
            key = path.split("/")[3]
            self.events.pop(key, None)
            self.missing = True
            return 202, {}
        key = path.split("/")[3]
        if method == "PATCH":
            self.events[key] = raw_event(key, str((payload or {}).get("subject") or ""))
            return 200, self.events[key]
        if key not in self.events:
            from jarvis.connectors.http import TransportError

            raise TransportError("not_found")
        return 200, self.events[key]


class FakeSession:
    def __init__(self) -> None:
        self.account: Account | None = ACCOUNT
        self.credentials = FakeCredentials()

    def matches(self, account: Account, message: object = None) -> bool:
        return self.account == account

    async def token(self, account: Account, context: ExecutionContext) -> str:
        await context.checkpoint()
        if self.account != account:
            from jarvis.mail.credentials import MailFailure

            raise MailFailure("mail_account_changed")
        return "synthetic-noncredential"


@pytest.fixture
def bench() -> Iterator[tuple[CalendarConnector, FakeGraph, ExecutionContext]]:
    graph = FakeGraph()
    connector = CalendarConnector(FakeSession(), graph)  # type: ignore[arg-type]
    yield connector, graph, ExecutionContext(Flag())


def test_the_transport_reaches_only_calendar_paths() -> None:
    allowed = {(route.method, route.path) for route in ROUTES}
    assert ("GET", r"/me/calendarView") in allowed
    # The mailbox is not reachable from here, and neither is anyone else's calendar.
    assert not any("mailFolders" in path or "/users/" in path for _, path in allowed)
    assert not any(method == "DELETE" for method, _ in allowed)


def test_a_write_is_never_declared_safe() -> None:
    writes = {capability.name for capability in CAPABILITIES if capability.writes}
    assert writes == {"create", "update", "cancel"}
    assert all(
        capability.risk is Risk.CONFIRM and not capability.idempotent
        for capability in CAPABILITIES
        if capability.writes
    )
    assert all(
        capability.risk is Risk.SAFE and capability.idempotent
        for capability in CAPABILITIES
        if not capability.writes
    )


@pytest.mark.asyncio
async def test_free_form_dates_are_refused_before_anything_is_asked() -> None:
    for start, end in (
        ("завтра", END),
        ("2026-09-16T14:00:00+02:00", END),
        ("2026-09-16T14:00:00", END),
        ("2026-02-30T14:00:00Z", END),
        (END, START),
        ("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z"),
    ):
        with pytest.raises(ValueError):
            RangeInput(account=ACCOUNT, start=start, end=end)


@pytest.mark.asyncio
async def test_listing_normalises_what_graph_returns(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    result = await connector.list(RangeInput(account=ACCOUNT, start=START, end=END), context)
    events = json.loads(result.data)
    assert result.state == "listed" and len(events) == 1
    # Graph answers with fractional seconds and a separate zone; one exact shape leaves.
    assert events[0]["start"] == "2026-09-16T14:00:00Z"
    assert events[0]["organizer"] == "owner@example.test"
    assert events[0]["attendees"][0]["response"] == "accepted"
    assert graph.params[0]["startDateTime"] == START


@pytest.mark.asyncio
async def test_a_search_word_never_becomes_query_syntax(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    args = SearchInput(account=ACCOUNT, start=START, end=END, query="' or startswith(subject,'")
    result = await connector.search(args, context)
    assert json.loads(result.data) == []
    # The request shape is fixed; the word is matched here, not sent as a filter.
    assert graph.calls == [("GET", "/me/calendarView")]
    assert "$filter" not in graph.params[0] and "$search" not in graph.params[0]


@pytest.mark.asyncio
async def test_a_search_matches_subject_location_and_people(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    for query in ("ревью", "переговорная", "john@acme.test", "John Smith"):
        args = SearchInput(account=ACCOUNT, start=START, end=END, query=query)
        assert len(json.loads((await connector.search(args, context)).data)) == 1


@pytest.mark.asyncio
async def test_availability_reports_only_time_everyone_is_free(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    args = AvailabilityInput(
        account=ACCOUNT,
        start=START,
        end=END,
        addresses=("owner@example.test", "john@acme.test"),
        minutes=30,
    )
    slots = json.loads((await connector.availability(args, context)).data)
    assert slots == [
        {"start": "2026-09-16T09:00:00Z", "end": "2026-09-16T11:00:00Z"},
        {"start": "2026-09-16T13:00:00Z", "end": "2026-09-16T15:00:00Z"},
    ]


def test_one_busy_person_removes_the_slot() -> None:
    assert free_slots(["0000", "0110"], START, 30) == tuple(free_slots(["0000", "0110"], START, 30))
    slots = free_slots(["0000", "0110"], START, 30)
    assert [(slot.start, slot.end) for slot in slots] == [
        ("2026-09-16T09:00:00Z", "2026-09-16T09:30:00Z"),
        ("2026-09-16T10:30:00Z", "2026-09-16T11:00:00Z"),
    ]
    assert free_slots([], START, 30) == ()


@pytest.mark.asyncio
async def test_creating_sends_utc_without_a_trailing_marker(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    draft = Draft(
        subject="Созвон",
        start="2026-09-16T14:00:00Z",
        end="2026-09-16T15:00:00Z",
        attendees=("john@acme.test",),
    )
    result = await connector.create(CreateInput(account=ACCOUNT, event=draft), context)
    assert result.state == "created" and result.event_id == "evt_new"
    sent = graph.payloads[0]
    assert sent["start"] == {"dateTime": "2026-09-16T14:00:00", "timeZone": "UTC"}
    assert sent["attendees"] == [
        {"emailAddress": {"address": "john@acme.test"}, "type": "required"}
    ]
    # An accepted write is never reported as delivered to the invitees.
    assert result.delivery_verified is False


@pytest.mark.asyncio
async def test_a_display_name_never_identifies_an_invitee() -> None:
    for attendees in (("John Smith",), ("john@acme.test", "JOHN@acme.test"), ("john@",)):
        with pytest.raises(ValueError):
            Draft(
                subject="Созвон",
                start="2026-09-16T14:00:00Z",
                end="2026-09-16T15:00:00Z",
                attendees=attendees,
            )


@pytest.mark.asyncio
async def test_a_write_is_reported_done_only_after_reading_it_back(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    registry = ToolRegistry()
    register_calendar(registry, connector)
    tool = registry.get("calendar.create")
    assert tool is not None
    draft = Draft(subject="Созвон", start="2026-09-16T14:00:00Z", end="2026-09-16T15:00:00Z")
    payload = tool.normalize({"account": ACCOUNT, "event": draft})
    result = await tool.run(payload, context)
    graph.calls.clear()
    assert await tool.verify(payload, result, context) is True
    assert graph.calls == [("GET", "/me/events/evt_new")]


@pytest.mark.asyncio
async def test_a_write_that_landed_differently_fails_verification(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    registry = ToolRegistry()
    register_calendar(registry, connector)
    tool = registry.get("calendar.create")
    assert tool is not None
    draft = Draft(subject="Созвон", start="2026-09-16T14:00:00Z", end="2026-09-16T15:00:00Z")
    payload = tool.normalize({"account": ACCOUNT, "event": draft})
    result = await tool.run(payload, context)
    # The service kept something else under that identifier.
    graph.events["evt_new"] = raw_event("evt_new", "Совсем другая встреча")
    assert await tool.verify(payload, result, context) is False


@pytest.mark.asyncio
async def test_cancellation_is_proved_by_the_event_being_gone(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    registry = ToolRegistry()
    register_calendar(registry, connector)
    tool = registry.get("calendar.cancel")
    assert tool is not None
    payload = tool.normalize({"account": ACCOUNT, "event_id": "evt_1", "comment": "Переносим"})
    result = await tool.run(payload, context)
    assert CalendarResult.model_validate(result).state == "cancelled"
    assert await tool.verify(payload, result, context) is True


@pytest.mark.asyncio
async def test_a_missing_event_is_one_finite_category(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    with pytest.raises(CalendarFailure) as error:
        await connector.get(EventInput(account=ACCOUNT, event_id="evt_absent"), context)
    assert error.value.code == "calendar_missing"


@pytest.mark.asyncio
async def test_a_changed_account_stops_the_call_before_the_wire(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    connector.session.account = None
    with pytest.raises(CalendarFailure) as error:
        await connector.list(RangeInput(account=ACCOUNT, start=START, end=END), context)
    assert error.value.code == "calendar_account_changed"
    assert graph.calls == []


@pytest.mark.asyncio
async def test_a_malformed_answer_is_refused_rather_than_guessed(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    graph.events["evt_1"] = {"id": "evt_1", "start": {"dateTime": "не время", "timeZone": "UTC"}}
    with pytest.raises(CalendarFailure) as error:
        await connector.list(RangeInput(account=ACCOUNT, start=START, end=END), context)
    assert error.value.code == "calendar_response"


@pytest.mark.asyncio
async def test_owner_policy_can_close_the_calendar(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, _ = bench
    registry = ToolRegistry()
    register_calendar(
        registry, connector, PermissionMatrix({"calendar": {"cancel": Rule(allowed=False)}})
    )
    listed = registry.get("calendar.list")
    cancel = registry.get("calendar.cancel")
    assert listed is not None and cancel is not None
    assert listed.risk is Risk.SAFE and cancel.risk is Risk.BLOCKED


@pytest.mark.asyncio
async def test_connection_is_reported_from_the_account_it_already_has(
    bench: tuple[CalendarConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, _ = bench
    assert (await connector.authenticate()).state == "connected"
    assert (await connector.health_check()).state == "ready"
    connector.session.account = None
    assert (await connector.authenticate()).state == "disconnected"
    assert (await connector.health_check()).state == "unauthenticated"


def observed(tool: str, payload: str) -> bool:
    """The runner's own rule, exercised on fabricated observations."""
    from uuid import uuid4

    from jarvis.core.planner.contracts import Step
    from jarvis.core.planner.runner import Runner
    from jarvis.observability.audit import ErrorCode
    from jarvis.permissions.approvals import Action
    from jarvis.permissions.engine import Outcome, PermissionEngine
    from jarvis.permissions.policies import Mode, Status
    from jarvis.tools.local import local_registry

    registry, _ = local_registry()

    async def approve(action: Action) -> None:
        return None

    async def clarify(question: str) -> None:
        return None

    engine = PermissionEngine.__new__(PermissionEngine)
    runner = Runner.__new__(Runner)
    runner.engine = engine
    runner.steps = [
        Step(
            "outlook.account",
            Outcome(
                uuid4(),
                Status.SUCCESS,
                ErrorCode.NONE,
                result_json=json.dumps(
                    {"state": "account", "account": ACCOUNT.model_dump(mode="json")}
                ),
            ),
        ),
        Step(
            "calendar.list",
            Outcome(
                uuid4(),
                Status.SUCCESS,
                ErrorCode.NONE,
                result_json=json.dumps(
                    {
                        "state": "listed",
                        "account": ACCOUNT.model_dump(mode="json"),
                        "data": json.dumps([{"id": "evt_1"}]),
                    }
                ),
            ),
        ),
    ]
    action = Action(uuid4(), tool, payload, Risk.CONFIRM, Mode.EXECUTE)
    return bool(runner._observed_target(action))


def test_a_meeting_must_be_seen_in_this_task_before_it_can_be_changed() -> None:
    account = ACCOUNT.model_dump(mode="json")
    seen = json.dumps({"account": account, "event_id": "evt_1", "comment": ""})
    invented = json.dumps({"account": account, "event_id": "evt_invented", "comment": ""})
    assert observed("calendar.cancel", seen) is True
    # An identifier the model produced on its own is refused even with the right account.
    assert observed("calendar.cancel", invented) is False
    assert observed("calendar.get", invented) is False
    # A range read needs the account but names no meeting.
    assert observed("calendar.list", json.dumps({"account": account})) is True


def test_a_calendar_call_for_another_account_is_refused() -> None:
    stranger = ACCOUNT.model_dump(mode="json") | {"address": "someone@elsewhere.test"}
    assert observed("calendar.list", json.dumps({"account": stranger})) is False
