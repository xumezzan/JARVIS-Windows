"""The briefing before a meeting: same five places, every line named, one outage survivable."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from jarvis.core.meeting import Briefer, Briefing, Client, company, plural
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
ACCOUNT: dict[str, object] = {
    "service": "outlook",
    "user_id": "fixture-user",
    "address": "owner@example.test",
    "session": "a" * 32,
}
CLIENT = "john@acme.test"
WALK = (
    "calendar.list",
    "fireflies.search",
    "asana.workspaces",
    "asana.projects",
    "asana.tasks",
    "outlook.list",
    "notion.search",
)


class Ask(ToolModel):
    """Everything the briefing may ask any of the five services, and nothing else."""

    account: dict[str, object] | None = None
    start: str | None = None
    end: str | None = None
    limit: int | None = None
    participants: list[str] | None = None
    workspace: str | None = None
    project: str | None = None
    completed: bool | None = None
    folder: str | None = None
    query: str | None = None


class Answer(ToolModel):
    data: str = ""


def event(
    subject: str = "Ревью Альфы", attendees: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "id": "evt1",
        "subject": subject,
        "start": "2026-09-18T14:00:00Z",
        "end": "2026-09-18T15:00:00Z",
        "organizer": "owner@example.test",
        "attendees": attendees
        if attendees is not None
        else [
            {"address": "owner@example.test", "name": "Владелец", "response": "accepted"},
            {"address": CLIENT, "name": "Джон Смит", "response": "accepted"},
        ],
        "location": "",
        "cancelled": False,
    }


ANSWERS: dict[str, list[dict[str, Any]]] = {
    "calendar.list": [event()],
    "fireflies.search": [
        {
            "id": "ff1",
            "title": "Звонок с Альфой",
            "start": "2026-09-12T10:00:00Z",
            "minutes": 30,
            "organizer": "owner@example.test",
            "participants": [CLIENT],
        }
    ],
    "asana.workspaces": [{"gid": "1", "name": "Компания"}],
    "asana.projects": [
        {"gid": "11", "name": "Внутреннее", "archived": False},
        {"gid": "10", "name": "Acme — внедрение", "archived": False},
    ],
    "asana.tasks": [
        {
            "gid": "100",
            "name": "Прислать смету",
            "completed": False,
            "due_on": "2026-09-19",
            "assignee": "Владелец",
            "notes": "",
            "permalink": "",
        }
    ],
    "outlook.list": [
        {
            "id": "m1",
            "subject": "Смета",
            "from": {"emailAddress": {"address": CLIENT}},
            "receivedDateTime": "2026-09-17T09:00:00Z",
            "isDraft": False,
            "isRead": True,
        },
        {
            "id": "m2",
            "subject": "Внутреннее письмо",
            "from": {"emailAddress": {"address": "boss@example.test"}},
            "receivedDateTime": "2026-09-17T10:00:00Z",
            "isDraft": False,
            "isRead": False,
        },
    ],
    "notion.search": [{"id": "page1", "title": "Клиент Acme", "url": "", "archived": False}],
}


@dataclass
class Services:
    """The five services, as fixtures: what each answers, and which one is having a bad day."""

    answers: dict[str, list[dict[str, Any]] | str] = field(
        default_factory=lambda: dict(ANSWERS.items())
    )
    broken: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    risks: dict[str, Risk] = field(default_factory=dict)

    async def check(self, args: Ask, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def verify(self, args: Ask, result: Answer, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    def run_of(self, name: str) -> Any:
        async def run(args: Ask, context: ExecutionContext) -> Answer:
            await context.checkpoint()
            self.calls.append(name)
            if name in self.broken:
                raise ToolError("network_denied")
            found = self.answers.get(name, [])
            return Answer(data=found if isinstance(found, str) else json.dumps(found))

        return run

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for name in WALK:
            registry.register(
                ToolSpec(
                    name,
                    "Фикстура сервиса для сводки.",
                    self.risks.get(name, Risk.SAFE),
                    Ask,
                    Answer,
                    self.check,
                    self.run_of(name),
                    self.verify,
                    timeout_seconds=2,
                )
            )
        return registry


@dataclass
class Bench:
    briefer: Briefer
    services: Services
    audit: AuditLog

    async def prepare(self, cancelled: Event | None = None) -> Briefing:
        return await self.briefer.prepare(ACCOUNT, cancelled or Event())


def bench_for(tmp_path: Path, services: Services) -> Bench:
    audit = AuditLog(tmp_path / "audit.sqlite3")
    registry = services.registry()
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    return Bench(Briefer(registry, engine, clock=lambda: NOW), services, audit)


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    built = bench_for(tmp_path, Services())
    yield built
    built.audit.close()


@pytest.mark.asyncio
async def test_the_same_five_places_are_read_in_the_same_order(bench: Bench) -> None:
    briefing = await bench.prepare()
    assert briefing.state == "ready"
    # Not a plan: the walk does not depend on what a model felt like choosing today.
    assert tuple(bench.services.calls) == WALK
    assert [section.part for section in briefing.sections] == [
        "meeting",
        "calls",
        "tasks",
        "mail",
        "page",
    ]


@pytest.mark.asyncio
async def test_every_line_says_which_tool_and_which_record_it_came_from(bench: Bench) -> None:
    briefing = await bench.prepare()
    lines = [line for section in briefing.sections for line in section.lines]
    assert lines and all(line.source and line.entity and line.text for line in lines)
    written = briefing.written()
    for source, entity in (
        ("calendar.list", "evt1"),
        ("fireflies.search", "ff1"),
        ("asana.tasks", "100"),
        ("outlook.list", "m1"),
        ("notion.search", "page1"),
    ):
        assert f"{source} · {entity}" in written


@pytest.mark.asyncio
async def test_the_meeting_decides_who_the_client_is(bench: Bench) -> None:
    briefing = await bench.prepare()
    # The owner is in their own invitation and is not the client; the guest from another
    # domain is. Nothing here reads a name out of a command.
    assert briefing.clients == (Client(CLIENT, "Джон Смит"),)
    asked = json.loads(json.dumps(ANSWERS["fireflies.search"]))  # the fixture is untouched
    assert asked[0]["participants"] == [CLIENT]
    mail = next(section for section in briefing.sections if section.part == "mail")
    # The internal letter is somebody else's business, not this meeting's.
    assert [line.entity for line in mail.lines] == ["m1"]
    tasks = next(section for section in briefing.sections if section.part == "tasks")
    assert [line.entity for line in tasks.lines] == ["100"]


@pytest.mark.asyncio
async def test_a_meeting_with_only_colleagues_still_gets_a_briefing(tmp_path: Path) -> None:
    services = Services()
    services.answers["calendar.list"] = [
        event(attendees=[{"address": "boss@example.test", "name": "Руководитель"}])
    ]
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        assert briefing.state == "ready"
        assert briefing.clients == (Client("boss@example.test", "Руководитель"),)
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_one_service_down_costs_its_own_section_and_nothing_else(tmp_path: Path) -> None:
    services = Services()
    services.broken = {"fireflies.search", "notion.search"}
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        assert briefing.state == "ready"
        states = {section.part: section.state for section in briefing.sections}
        assert states["calls"] == "unavailable" and states["page"] == "unavailable"
        # The owner still walks in knowing what is in the mailbox and on the board.
        assert states["tasks"] == "found" and states["mail"] == "found"
        assert "сервис недоступен" in briefing.written()
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_without_a_calendar_there_is_nothing_to_brief_about(tmp_path: Path) -> None:
    services = Services()
    services.broken = {"calendar.list"}
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        assert briefing.state == "unavailable"
        assert briefing.sections == ()
        assert "Календарь недоступен" in briefing.written()
        assert built.services.calls == ["calendar.list"]
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_no_meeting_means_nothing_to_gather(tmp_path: Path) -> None:
    services = Services()
    services.answers["calendar.list"] = []
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        assert briefing.state == "no_meeting"
        # Nothing else is asked: there is no meeting to ask about.
        assert built.services.calls == ["calendar.list"]
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_a_capability_the_owner_tightened_is_refused_and_never_runs(tmp_path: Path) -> None:
    services = Services()
    # Looking is reading. A capability raised above SAFE is not an observation any more,
    # and the briefing must not quietly carry it out.
    services.risks = {"notion.search": Risk.CONFIRM}
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        page = next(section for section in briefing.sections if section.part == "page")
        assert page.state == "unavailable"
        assert "notion.search" not in built.services.calls
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_a_cancelled_briefing_stops_before_it_asks_anything(bench: Bench) -> None:
    cancelled = Event()
    cancelled.set()
    briefing = await bench.prepare(cancelled)
    assert briefing.state == "unavailable"
    assert bench.services.calls == []


@pytest.mark.asyncio
async def test_an_answer_that_makes_no_sense_leaves_a_section_empty(tmp_path: Path) -> None:
    services = Services()
    services.answers["fireflies.search"] = "не json"
    services.answers["asana.projects"] = [{"gid": "10"}]
    services.answers["notion.search"] = [{"title": "без идентификатора"}]
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        states = {section.part: section.state for section in briefing.sections}
        # Nothing invented, nothing raised: a service that answers nonsense answered nothing.
        assert states["calls"] == "empty"
        assert states["tasks"] == "empty"
        assert states["page"] == "empty"
    finally:
        built.audit.close()


@pytest.mark.asyncio
async def test_what_is_said_out_loud_carries_counts_and_no_identifiers(bench: Bench) -> None:
    briefing = await bench.prepare()
    spoken = briefing.spoken()
    assert "Ревью Альфы" in spoken
    assert "1 прошлый разговор" in spoken and "1 открытая задача" in spoken
    assert "1 письмо" in spoken and "страница клиента найдена" in spoken
    # A voice cannot pronounce an address or a record id, and should not try.
    assert "@" not in spoken and "evt1" not in spoken and "page1" not in spoken


@pytest.mark.asyncio
async def test_unread_letters_come_first_and_say_that_they_are_unread(tmp_path: Path) -> None:
    services = Services()
    services.answers["outlook.list"] = [
        {
            "id": "read",
            "subject": "Старое",
            "from": {"emailAddress": {"address": CLIENT}},
            "receivedDateTime": "2026-09-16T09:00:00Z",
            "isRead": True,
        },
        {
            "id": "unread",
            "subject": "Смета",
            "from": {"emailAddress": {"address": CLIENT}},
            "receivedDateTime": "2026-09-17T09:00:00Z",
            "isRead": False,
        },
        {
            # No flag at all: counted as read, because claiming "unread" without evidence
            # sends the owner into the meeting looking for a letter that is not waiting.
            "id": "unknown",
            "subject": "Без признака",
            "from": {"emailAddress": {"address": CLIENT}},
            "receivedDateTime": "2026-09-15T09:00:00Z",
        },
    ]
    built = bench_for(tmp_path, services)
    try:
        briefing = await built.prepare()
        mail = next(section for section in briefing.sections if section.part == "mail")
        assert [line.entity for line in mail.lines] == ["unread", "read", "unknown"]
        assert "не прочитано" in mail.lines[0].text
        assert all("не прочитано" not in line.text for line in mail.lines[1:])
    finally:
        built.audit.close()


def test_russian_counting_is_not_a_table_read_aloud() -> None:
    forms = ("письмо", "письма", "писем")
    assert [plural(count, forms) for count in (0, 1, 2, 5, 11, 21, 104)] == [
        "писем",
        "письмо",
        "письма",
        "писем",
        "писем",
        "письмо",
        "письма",
    ]


def test_the_word_a_client_is_filed_under_comes_from_their_own_address() -> None:
    assert company("john@acme.test") == "acme"
    assert company("john@a.io") == ""
    assert company("not-an-address") == ""
