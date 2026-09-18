"""Everything the next meeting needs, gathered from the services that hold it.

This is the owner's own sentence - "prepare everything I need for today's client meeting" -
and it is deliberately not a plan. A model asked to do this would choose five tools, and
the answer would be as good as its choice that day; the point of this screen is that the
same five places are always looked at, in the same order, and that every line on it says
where it came from.

Three rules hold it together.

The meeting decides who the client is. The calendar is read first, and the people in the
invitation - the ones outside the owner's own mail domain - are what the other services are
then asked about. Nothing here guesses a client from a word in a command.

Every line carries its source: the tool that observed it and the identifier the service
itself gave the thing. A summary whose lines cannot be traced is a summary nobody can check,
and this one is meant to be checked.

One service being down costs its own section and nothing else. Fireflies unreachable means
the past calls are missing from the briefing, not that the briefing failed - the owner still
walks into the meeting knowing what is in the mailbox and on the board.

Everything a service answers is data. Titles, subjects and task names are shown to the owner
as words on a screen; they never become a tool name, an argument or an instruction.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, Literal

from jarvis.connectors.instants import parse, render
from jarvis.core.report import MONTHS
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.registry import ToolRegistry

# How far ahead a "today's meeting" may be, and how far back the history is worth reading.
AHEAD_HOURS = 12
HISTORY_DAYS = 90
MAX_LINES = 5
MAX_EVENTS = 10
MAX_PROJECTS = 25
MAX_TEXT = 120

Part = Literal["meeting", "calls", "tasks", "mail", "page"]
State = Literal["found", "empty", "unavailable"]

TITLE: dict[Part, str] = {
    "meeting": "Встреча",
    "calls": "Прошлые разговоры",
    "tasks": "Открытые задачи",
    "mail": "Письма от этого человека",
    "page": "Страница клиента",
}
EMPTY: dict[Part, str] = {
    "meeting": "встречи в ближайшие часы нет",
    "calls": "прошлых разговоров не нашлось",
    "tasks": "открытых задач не нашлось",
    "mail": "писем от него не нашлось",
    "page": "страницы о нём не нашлось",
}
# Said out loud, where a count is what a person can actually hold in their head.
SPOKEN: dict[Part, tuple[str, str, str]] = {
    "calls": ("прошлый разговор", "прошлых разговора", "прошлых разговоров"),
    "tasks": ("открытая задача", "открытые задачи", "открытых задач"),
    "mail": ("письмо", "письма", "писем"),
}


def plural(count: int, forms: tuple[str, str, str]) -> str:
    """Russian counting, because "2 письмо" is the sound of a machine reading a table."""
    tens, ones = count % 100, count % 10
    if 11 <= tens <= 14 or ones == 0 or ones >= 5:
        return forms[2]
    return forms[0] if ones == 1 else forms[1]


def short(value: object, limit: int = MAX_TEXT) -> str:
    """One bounded line of somebody else's words: no control characters, no wall of text."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def day_text(value: str) -> str:
    try:
        moment = parse(value)
    except ValueError:
        return ""
    return f"{moment.day} {MONTHS[moment.month - 1]}, {moment:%H:%M} UTC"


def domain(address: str) -> str:
    """The part after the at sign, and nothing when there is no at sign to speak of.

    Returning the whole string for a malformed address would make it look like a domain,
    and then a word out of somebody's typo would be searched for in Asana and Notion.
    """
    _, sign, host = address.rpartition("@")
    return host.casefold() if sign else ""


def company(address: str) -> str:
    """The name a client is filed under elsewhere, taken from their own address."""
    label = domain(address).split(".")[0]
    return label if len(label) > 2 else ""


@dataclass(frozen=True)
class Line:
    """One fact and where it came from. Nothing reaches the screen without both."""

    source: str
    entity: str
    text: str

    def written(self) -> str:
        return f"    — {self.text}  ·  {self.source} · {self.entity}"


@dataclass(frozen=True)
class Section:
    part: Part
    state: State
    lines: tuple[Line, ...] = ()

    def written(self) -> tuple[str, ...]:
        if self.state == "unavailable":
            return (f"{TITLE[self.part]}: сервис недоступен, не подключён или запрещён.",)
        if self.state == "empty" or not self.lines:
            return (f"{TITLE[self.part]}: {EMPTY[self.part]}.",)
        head = f"{TITLE[self.part]} — {len(self.lines)}:"
        return (head, *(line.written() for line in self.lines))


@dataclass(frozen=True)
class Client:
    """The person the meeting is with, as the invitation named them."""

    address: str
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.address


@dataclass(frozen=True)
class Briefing:
    state: Literal["ready", "no_meeting", "unavailable"]
    subject: str = ""
    start: str = ""
    clients: tuple[Client, ...] = ()
    sections: tuple[Section, ...] = field(default=())

    def written(self) -> str:
        """What the owner reads. Service words are quoted here, never obeyed."""
        if self.state == "unavailable":
            return "Календарь недоступен, не подключён или запрещён — собрать сводку не из чего."
        if self.state == "no_meeting":
            return f"Встречи в ближайшие {AHEAD_HOURS} часов нет. Собирать нечего."
        lines: list[str] = []
        for section in self.sections:
            lines.extend(section.written())
            lines.append("")
        return "\n".join(lines).strip()

    def spoken(self) -> str:
        """The same thing out loud: counts and names, no addresses and no identifiers."""
        if self.state == "unavailable":
            return "Календарь недоступен, сводку собрать не из чего."
        if self.state == "no_meeting":
            return "Встречи в ближайшие часы нет."
        found = {section.part: section for section in self.sections}
        when = day_text(self.start).partition(", ")[2] or day_text(self.start)
        parts = [f"К встрече «{self.subject}» в {when}" if self.subject else "К встрече"]
        for part in ("calls", "tasks", "mail"):
            section = found.get(part)
            if section is None:
                continue
            if section.state == "unavailable":
                parts.append(f"{TITLE[part].lower()} — сервис недоступен")
                continue
            count = len(section.lines)
            parts.append(f"{count} {plural(count, SPOKEN[part])}")
        page = found.get("page")
        if page is not None:
            parts.append(
                "страница клиента найдена"
                if page.state == "found"
                else "страницы клиента нет"
                if page.state == "empty"
                else "страница клиента недоступна"
            )
        return parts[0] + ": " + ", ".join(parts[1:]) + "."


def rows(payload: str | None) -> list[dict[str, Any]]:
    """The list a tool result carries, or nothing at all. A malformed answer is nothing."""
    try:
        outer = json.loads(payload or "null")
        inner = json.loads(outer.get("data") or "null") if isinstance(outer, dict) else None
    except ValueError:
        return []
    if isinstance(inner, dict):
        inner = [inner]
    return [row for row in inner if isinstance(row, dict)] if isinstance(inner, list) else []


class Briefer:
    """The walk itself: read, read, read, and say where each answer came from."""

    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.clock = clock
        self.cancelled = Event()

    async def prepare(self, account: dict[str, object], cancelled: Event) -> Briefing:
        self.cancelled = cancelled
        now = self.clock()
        events = await self._observe(
            "calendar.list",
            {
                "account": account,
                "start": render(now),
                "end": render(now + timedelta(hours=AHEAD_HOURS)),
                "limit": MAX_EVENTS,
            },
        )
        if events is None:
            return Briefing("unavailable")
        chosen = self._meeting(rows(events), str(account.get("address") or ""))
        if chosen is None:
            return Briefing("no_meeting")
        event, clients = chosen
        sections = [
            Section(
                "meeting",
                "found",
                (
                    Line(
                        "calendar.list",
                        short(event.get("id"), 60),
                        f"«{short(event.get('subject')) or 'без названия'}», "
                        f"{day_text(short(event.get('start'), 20))}, "
                        f"с {', '.join(client.label for client in clients)}",
                    ),
                ),
            ),
            await self._calls(clients, now),
            await self._tasks(clients),
            await self._mail(account, clients),
            await self._page(clients),
        ]
        return Briefing(
            "ready",
            short(event.get("subject")),
            short(event.get("start"), 20),
            clients,
            tuple(sections),
        )

    def _meeting(
        self, events: list[dict[str, Any]], owner: str
    ) -> tuple[dict[str, Any], tuple[Client, ...]] | None:
        """The next meeting that has somebody from outside in it, and who that is.

        A meeting with nobody external is still a meeting, so it is taken when no other
        exists - its attendees are simply who the rest of the briefing is about.
        """
        home = domain(owner)
        live = [event for event in events if not event.get("cancelled")]
        for event in live:
            guests = self._guests(event, home, outside=True)
            if guests:
                return event, guests
        for event in live:
            guests = self._guests(event, home, outside=False)
            if guests:
                return event, guests
        # A meeting with nobody listed is still the meeting the owner is walking into.
        return (live[0], ()) if live else None

    def _guests(self, event: dict[str, Any], home: str, *, outside: bool) -> tuple[Client, ...]:
        attendees = event.get("attendees")
        found: list[Client] = []
        for attendee in attendees if isinstance(attendees, list) else []:
            if not isinstance(attendee, dict):
                continue
            address = short(attendee.get("address"), 254)
            # Somebody the other services can be asked about is somebody with an address.
            if not domain(address) or (outside and domain(address) == home):
                continue
            found.append(Client(address, short(attendee.get("name"), 60)))
        return tuple(found[:MAX_LINES])

    async def _calls(self, clients: tuple[Client, ...], now: datetime) -> Section:
        found = await self._observe(
            "fireflies.search",
            {
                "start": render(now - timedelta(days=HISTORY_DAYS)),
                "end": render(now),
                "participants": [client.address for client in clients],
                "limit": MAX_LINES,
            },
        )
        if found is None:
            return Section("calls", "unavailable")
        lines = tuple(
            Line(
                "fireflies.search",
                short(row.get("id"), 120),
                f"«{short(row.get('title')) or 'без названия'}», "
                + day_text(short(row.get("start"), 20)),
            )
            for row in rows(found)[:MAX_LINES]
            if row.get("id")
        )
        return Section("calls", "found" if lines else "empty", lines)

    async def _tasks(self, clients: tuple[Client, ...]) -> Section:
        """Asana has no free-text search here, so the client is matched against projects.

        The word matched is the client's own domain - "acme" from acme.test - or the name
        the invitation gave them. It is a fact from the meeting, not a guess about a person.
        """
        spaces = await self._observe("asana.workspaces", {"limit": 5})
        if spaces is None:
            return Section("tasks", "unavailable")
        workspaces = [short(row.get("gid"), 32) for row in rows(spaces) if row.get("gid")]
        if not workspaces:
            return Section("tasks", "empty")
        projects = await self._observe(
            "asana.projects", {"workspace": workspaces[0], "limit": MAX_PROJECTS}
        )
        if projects is None:
            return Section("tasks", "unavailable")
        terms = {term.casefold() for client in clients for term in self._terms(client) if term}
        match = next(
            (
                row
                for row in rows(projects)
                if not row.get("archived")
                and any(term in short(row.get("name")).casefold() for term in terms)
            ),
            None,
        )
        if match is None:
            return Section("tasks", "empty")
        tasks = await self._observe(
            "asana.tasks",
            {"project": short(match.get("gid"), 32), "completed": False, "limit": MAX_LINES},
        )
        if tasks is None:
            return Section("tasks", "unavailable")
        lines = tuple(
            Line(
                "asana.tasks",
                short(row.get("gid"), 32),
                short(row.get("name"))
                + (f", срок {short(row.get('due_on'), 10)}" if row.get("due_on") else ""),
            )
            for row in rows(tasks)[:MAX_LINES]
            if row.get("gid") and not row.get("completed")
        )
        return Section("tasks", "found" if lines else "empty", lines)

    async def _mail(self, account: dict[str, object], clients: tuple[Client, ...]) -> Section:
        """What this person wrote, with anything still unread first and marked as such.

        Read letters are not dropped: the last exchange is often exactly what is needed
        before a meeting, whether or not it was opened. A letter whose read flag is
        missing counts as read - claiming "unread" without evidence would be the one
        mistake here that sends the owner into the meeting looking for a letter that
        does not exist.
        """
        letters = await self._observe(
            "outlook.list", {"account": account, "folder": "inbox", "limit": MAX_EVENTS}
        )
        if letters is None:
            return Section("mail", "unavailable")
        addresses = {client.address.casefold() for client in clients}
        waiting: list[Line] = []
        opened: list[Line] = []
        for row in rows(letters):
            sender = row.get("from")
            mailbox = sender.get("emailAddress") if isinstance(sender, dict) else None
            address = short(mailbox.get("address"), 254) if isinstance(mailbox, dict) else ""
            if address.casefold() not in addresses or not row.get("id"):
                continue
            unread = row.get("isRead") is False
            line = Line(
                "outlook.list",
                short(row.get("id"), 120),
                f"«{short(row.get('subject')) or 'без темы'}», "
                f"{day_text(short(row.get('receivedDateTime'), 20)) or 'без даты'}"
                + (" · не прочитано" if unread else ""),
            )
            (waiting if unread else opened).append(line)
        lines = (*waiting, *opened)[:MAX_LINES]
        return Section("mail", "found" if lines else "empty", lines)

    async def _page(self, clients: tuple[Client, ...]) -> Section:
        terms = [term for client in clients for term in self._terms(client) if term]
        if not terms:
            return Section("page", "empty")
        pages = await self._observe("notion.search", {"query": terms[0], "limit": MAX_LINES})
        if pages is None:
            return Section("page", "unavailable")
        lines = tuple(
            Line("notion.search", short(row.get("id"), 36), f"«{short(row.get('title'))}»")
            for row in rows(pages)[:MAX_LINES]
            if row.get("id") and row.get("title")
        )
        return Section("page", "found" if lines else "empty", lines)

    def _terms(self, client: Client) -> Sequence[str]:
        return (company(client.address), client.name)

    async def _observe(self, tool: str, arguments: dict[str, object]) -> str | None:
        """One bounded read through the engine. Anything but a plain success is nothing.

        The level is read from the prepared snapshot rather than from this file, so a
        capability the owner tightened in their matrix is refused here like anywhere else.
        """
        if self.cancelled.is_set() or self.registry.get(tool) is None:
            return None
        action = self.engine.prepare(tool, arguments, Mode.EXECUTE)
        if isinstance(action, Outcome):
            return None
        if action.risk is not Risk.SAFE:
            # Preparing a briefing is looking. Anything that changes something is not it.
            self.engine.cancel(action)
            return None
        outcome = await self.engine.execute(action)
        return outcome.result_json if outcome.status is Status.SUCCESS else None
