"""The routines that ship with the assistant: what each one looks at, and what it says.

Every one of them follows the same shape. It asks the account it is connected to, makes one
bounded read over a fixed window, and turns the fields it got back into a sentence. A
service answer is data here, exactly as it is everywhere else: it never becomes a tool name,
an argument, a schedule or an instruction.

The windows are deliberately written in hours from now rather than in days. Turning "today"
into a range means guessing a timezone and where the working day ends, and a routine that
guesses wrong is worse than one that says nothing.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from jarvis.connectors.instants import parse, render
from jarvis.core.routines.contracts import Call, Suggestion, reason_key

SOON_MINUTES = 15
SOON_HOURS = 2
AGENDA_HOURS = 12
AGENDA_FILE = "Встречи.txt"
RECENT_MINUTES = 30
MAX_ROWS = 25


def _account(seen: dict[str, str]) -> dict[str, object] | None:
    """The account the connector itself reported, never one from settings or a plan."""
    try:
        data = json.loads(seen.get("outlook.account", "") or "{}")
    except ValueError:
        return None
    account = data.get("account") if isinstance(data, dict) else None
    return account if isinstance(account, dict) else None


def _rows(seen: dict[str, str], tool: str) -> list[dict[str, object]]:
    try:
        data = json.loads(seen.get(tool, "") or "{}")
        rows = json.loads(data.get("data", "[]")) if isinstance(data, dict) else []
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)][:MAX_ROWS]


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        return parse(value)
    except ValueError:
        pass
    try:  # A service may add fractional seconds; the instant itself is still exact.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _clock(moment: datetime) -> str:
    """Shown in the owner's own time: the operating system knows it, so nothing is guessed."""
    return moment.astimezone().strftime("%H:%M")


def _window(now: float, hours: int) -> tuple[str, str]:
    start = datetime.fromtimestamp(now, UTC)
    return render(start), render(datetime.fromtimestamp(now + hours * 3600, UTC))


def _text(value: object, limit: int = 60) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) and value else ""


class MeetingsSoon:
    """Says that a meeting is about to start. It suggests nothing and does nothing."""

    name = "meetings.soon"
    title = "Встреча вот-вот начнётся"
    description = (
        "Каждые пять минут смотрит ближайшие встречи и предупреждает за четверть часа "
        "до начала. Ничего не выполняет."
    )
    every_seconds = 300

    def observe(self, seen: dict[str, str], now: float) -> Call | None:
        if "outlook.account" not in seen:
            return Call("outlook.account")
        account = _account(seen)
        if account is None or "calendar.list" in seen:
            return None
        start, end = _window(now, SOON_HOURS)
        return Call("calendar.list", {"account": account, "start": start, "end": end, "limit": 10})

    def notice(self, seen: dict[str, str], now: float) -> tuple[Suggestion, ...]:
        found = []
        for row in _rows(seen, "calendar.list"):
            moment = _moment(row.get("start"))
            if moment is None or row.get("cancelled") is True:
                continue
            minutes = (moment.timestamp() - now) / 60
            if not 0 <= minutes <= SOON_MINUTES:
                continue
            subject = _text(row.get("subject")) or "Без темы"
            where = _text(row.get("location"), 80)
            found.append(
                Suggestion(
                    routine=self.name,
                    key=reason_key(self.name, _text(row.get("id"), 200), render(moment)),
                    title=f"«{subject}» начинается через {int(minutes)} мин",
                    detail=f"Начало в {_clock(moment)}." + (f" Место: {where}." if where else ""),
                )
            )
        return tuple(found)


class MeetingsAgenda:
    """Writes the next half day of meetings into one file in the owner's own folder.

    This is the routine that can act by itself, and only when the owner allowed it for this
    routine: the file is inside the folders already allowed, it is one known name, and
    writing it again with the same content is refused by the journal rather than repeated.
    """

    name = "meetings.agenda"
    title = "Повестка ближайших часов в файле"
    description = (
        "Раз в час собирает встречи ближайших двенадцати часов в файл «Встречи.txt» в вашей "
        "папке. Без отдельного разрешения только предлагает."
    )
    every_seconds = 3600

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def observe(self, seen: dict[str, str], now: float) -> Call | None:
        if "outlook.account" not in seen:
            return Call("outlook.account")
        account = _account(seen)
        if account is None or "calendar.list" in seen:
            return None
        start, end = _window(now, AGENDA_HOURS)
        return Call("calendar.list", {"account": account, "start": start, "end": end, "limit": 25})

    def notice(self, seen: dict[str, str], now: float) -> tuple[Suggestion, ...]:
        lines = []
        for row in _rows(seen, "calendar.list"):
            moment = _moment(row.get("start"))
            if moment is None or row.get("cancelled") is True:
                continue
            subject = _text(row.get("subject")) or "Без темы"
            where = _text(row.get("location"), 80)
            lines.append(f"{_clock(moment)} — {subject}" + (f" ({where})" if where else ""))
        if not lines:
            return ()
        text = "Встречи ближайших 12 часов\n\n" + "\n".join(lines) + "\n"
        arguments = json.dumps(
            {"path": str(self.folder / AGENDA_FILE), "text": text, "overwrite": True},
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            Suggestion(
                routine=self.name,
                # The content is the reason: an unchanged agenda is not a new one.
                key=reason_key(self.name, text),
                title=f"Повестка на ближайшие 12 часов: встреч {len(lines)}",
                detail=f"Записать в {AGENDA_FILE}. Первая: {lines[0]}"[:400],
                tool="files.write_text",
                arguments=arguments,
            ),
        )


class MailArrived:
    """Says that something new is in the inbox. It opens nothing and answers nothing."""

    name = "mail.recent"
    title = "Новое письмо во «Входящих»"
    description = (
        "Каждые пять минут смотрит начало входящих и отмечает письма, пришедшие за "
        "последние полчаса. Ничего не выполняет."
    )
    every_seconds = 300

    def observe(self, seen: dict[str, str], now: float) -> Call | None:
        if "outlook.account" not in seen:
            return Call("outlook.account")
        account = _account(seen)
        if account is None or "outlook.list" in seen:
            return None
        return Call("outlook.list", {"account": account, "folder": "inbox", "limit": 5})

    def notice(self, seen: dict[str, str], now: float) -> tuple[Suggestion, ...]:
        found = []
        for row in _rows(seen, "outlook.list"):
            moment = _moment(row.get("receivedDateTime"))
            if moment is None or row.get("isDraft") is True:
                continue
            minutes = (now - moment.timestamp()) / 60
            if not 0 <= minutes <= RECENT_MINUTES:
                continue
            mailbox = row.get("from")
            address = mailbox.get("emailAddress") if isinstance(mailbox, dict) else None
            sender = ""
            if isinstance(address, dict):
                sender = _text(address.get("name")) or _text(address.get("address"))
            subject = _text(row.get("subject")) or "Без темы"
            found.append(
                Suggestion(
                    routine=self.name,
                    key=reason_key(self.name, _text(row.get("id"), 200)),
                    title=f"Письмо: «{subject}»",
                    detail=(f"От {sender}. " if sender else "") + f"Пришло в {_clock(moment)}.",
                )
            )
        return tuple(found)


def builtin(folder: Path | None = None) -> tuple[MeetingsSoon | MeetingsAgenda | MailArrived, ...]:
    """The routines this installation offers. Each still has to be switched on by hand."""
    if folder is None:
        return (MeetingsSoon(), MailArrived())
    return (MeetingsSoon(), MeetingsAgenda(folder), MailArrived())
