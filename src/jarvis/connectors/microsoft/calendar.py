"""Microsoft Calendar as a connector: declared capabilities over one verified account.

The account and its token come from the existing Microsoft session, so connecting Outlook
connects the calendar too — one consent, one identity, re-verified on every call.

Searching is done over a fetched range and filtered here rather than through an OData
filter built from the user's words. A query string that becomes query syntax is a class of
bug worth designing out: the range is bounded, the filter is a plain substring, and nothing
the planner writes can change the shape of the request.
"""

import json
from datetime import UTC, datetime, timedelta

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import ServiceTransport, TransportError
from jarvis.connectors.microsoft.graph import CalendarFailure, translate, transport
from jarvis.connectors.microsoft.models import (
    MAX_ATTENDEES,
    Attendee,
    AvailabilityInput,
    CalendarResult,
    CancelInput,
    CreateInput,
    Draft,
    Event,
    EventInput,
    RangeInput,
    Response,
    SearchInput,
    Slot,
    UpdateInput,
)
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.mail.session import MailSession
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext

SELECT = "id,subject,start,end,organizer,attendees,location,isCancelled"

CAPABILITIES = (
    Capability("list", "Показать встречи за интервал.", Risk.SAFE, True, reads=("meeting",)),
    Capability("get", "Прочитать одну встречу.", Risk.SAFE, True, reads=("meeting",)),
    Capability(
        "search", "Найти встречи по слову в интервале.", Risk.SAFE, True, reads=("meeting",)
    ),
    Capability("availability", "Найти свободное общее время.", Risk.SAFE, True, reads=("person",)),
    Capability(
        "create",
        "Создать встречу и пригласить участников.",
        Risk.CONFIRM,
        False,
        writes=("meeting",),
    ),
    Capability("update", "Изменить встречу.", Risk.CONFIRM, False, writes=("meeting",)),
    Capability(
        "cancel",
        "Отменить встречу и уведомить участников.",
        Risk.CONFIRM,
        False,
        writes=("meeting",),
    ),
)


def instant(value: object) -> str:
    """Graph returns fractional seconds and a separate zone; one exact UTC shape leaves here."""
    if not isinstance(value, dict):
        raise CalendarFailure("calendar_response")
    raw = value.get("dateTime")
    zone = value.get("timeZone")
    if not isinstance(raw, str) or zone not in ("UTC", "Etc/GMT", None):
        raise CalendarFailure("calendar_response")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise CalendarFailure("calendar_response") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def response_of(item: dict[str, object]) -> Response:
    """An unknown or missing reply means nothing is known, never an assumed acceptance."""
    status = item.get("status")
    raw = status.get("response") if isinstance(status, dict) else None
    if raw == "accepted":
        return "accepted"
    if raw == "declined":
        return "declined"
    if raw == "tentative":
        return "tentative"
    return "none"


def address_of(value: object) -> str:
    if not isinstance(value, dict):
        raise CalendarFailure("calendar_response")
    mail = value.get("emailAddress")
    if not isinstance(mail, dict) or not isinstance(mail.get("address"), str):
        raise CalendarFailure("calendar_response")
    return str(mail["address"])


def event_of(row: object) -> Event:
    if not isinstance(row, dict):
        raise CalendarFailure("calendar_response")
    attendees = row.get("attendees") or []
    if not isinstance(attendees, list):
        raise CalendarFailure("calendar_response")
    location = row.get("location")
    try:
        return Event(
            id=str(row["id"]),
            subject=str(row.get("subject") or ""),
            start=instant(row.get("start")),
            end=instant(row.get("end")),
            organizer=address_of(row.get("organizer")),
            attendees=tuple(
                Attendee(
                    address=address_of(item),
                    name=str((item.get("emailAddress") or {}).get("name") or "")[:120],
                    response=response_of(item),
                )
                for item in attendees[:MAX_ATTENDEES]
                if isinstance(item, dict)
            ),
            location=str((location or {}).get("displayName") or "")[:300]
            if isinstance(location, dict)
            else "",
            cancelled=bool(row.get("isCancelled")),
        )
    except CalendarFailure:
        raise
    except Exception:
        raise CalendarFailure("calendar_response") from None


def payload_of(draft: Draft) -> dict[str, object]:
    return {
        "subject": draft.subject,
        "body": {"contentType": "Text", "content": draft.body},
        "start": {"dateTime": draft.start[:-1], "timeZone": "UTC"},
        "end": {"dateTime": draft.end[:-1], "timeZone": "UTC"},
        "location": {"displayName": draft.location},
        "attendees": [
            {"emailAddress": {"address": address}, "type": "required"}
            for address in draft.attendees
        ],
    }


def free_slots(views: list[str], start: str, minutes: int) -> tuple[Slot, ...]:
    """Intervals where every requested person is free, merged into contiguous blocks."""
    if not views:
        return ()
    length = min(len(view) for view in views)
    begin = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    slots: list[Slot] = []
    index = 0
    while index < length:
        if any(view[index] != "0" for view in views):
            index += 1
            continue
        run = index
        while run < length and all(view[run] == "0" for view in views):
            run += 1
        slots.append(
            Slot(
                start=(begin + timedelta(minutes=minutes * index)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end=(begin + timedelta(minutes=minutes * run)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        index = run
    return tuple(slots[:MAX_ATTENDEES])


class CalendarConnector:
    service = "calendar"

    def __init__(self, session: MailSession, graph: ServiceTransport | None = None) -> None:
        self.session = session
        self.graph = graph or transport()

    def capabilities(self) -> tuple[Capability, ...]:
        return CAPABILITIES

    async def authenticate(self) -> AuthState:
        """The Outlook panel owns connection; the calendar reports what it already has."""
        account = self.session.account
        return (
            AuthState("connected", account.address)
            if account is not None
            else AuthState("disconnected")
        )

    async def health_check(self) -> Health:
        return Health("ready") if self.session.account is not None else Health("unauthenticated")

    async def _call(
        self,
        account: Account,
        context: ExecutionContext,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            token = await self.session.token(account, context)
        except MailFailure:
            raise CalendarFailure("calendar_account_changed") from None
        await context.checkpoint()
        try:
            _, data = await self.graph.request(token, method, path, payload, params)
        except TransportError as error:
            raise translate(error) from None
        await context.checkpoint()
        return data

    def _events(self, data: dict[str, object], limit: int) -> list[Event]:
        values = data.get("value")
        if not isinstance(values, list) or len(values) > limit:
            raise CalendarFailure("calendar_response")
        return [event_of(row) for row in values]

    async def list(self, args: RangeInput, context: ExecutionContext) -> CalendarResult:
        data = await self._call(
            args.account,
            context,
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": args.start,
                "endDateTime": args.end,
                "$top": str(args.limit),
                "$select": SELECT,
                "$orderby": "start/dateTime",
            },
        )
        events = self._events(data, args.limit)
        return CalendarResult(
            state="listed",
            account=args.account,
            data=json.dumps(
                [event.model_dump(mode="json") for event in events], ensure_ascii=False
            ),
        )

    async def search(self, args: SearchInput, context: ExecutionContext) -> CalendarResult:
        data = await self._call(
            args.account,
            context,
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": args.start,
                "endDateTime": args.end,
                "$top": str(MAX_ATTENDEES * 2),
                "$select": SELECT,
                "$orderby": "start/dateTime",
            },
        )
        needle = args.query.casefold()
        found = [
            event
            for event in self._events(data, MAX_ATTENDEES * 2)
            if needle in event.subject.casefold()
            or needle in event.location.casefold()
            or any(
                needle in attendee.address.casefold() or needle in attendee.name.casefold()
                for attendee in event.attendees
            )
        ][: args.limit]
        return CalendarResult(
            state="listed",
            account=args.account,
            data=json.dumps([event.model_dump(mode="json") for event in found], ensure_ascii=False),
        )

    async def get(self, args: EventInput, context: ExecutionContext) -> CalendarResult:
        data = await self._call(
            args.account, context, "GET", "/me/events/" + args.event_id, params={"$select": SELECT}
        )
        event = event_of(data)
        return CalendarResult(
            state="read",
            account=args.account,
            event_id=event.id,
            data=json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
        )

    async def availability(
        self, args: AvailabilityInput, context: ExecutionContext
    ) -> CalendarResult:
        data = await self._call(
            args.account,
            context,
            "POST",
            "/me/calendar/getSchedule",
            {
                "schedules": list(args.addresses),
                "startTime": {"dateTime": args.start[:-1], "timeZone": "UTC"},
                "endTime": {"dateTime": args.end[:-1], "timeZone": "UTC"},
                "availabilityViewInterval": args.minutes,
            },
        )
        values = data.get("value")
        if not isinstance(values, list) or len(values) != len(args.addresses):
            raise CalendarFailure("calendar_response")
        views = []
        for row in values:
            if not isinstance(row, dict) or not isinstance(row.get("availabilityView"), str):
                raise CalendarFailure("calendar_response")
            views.append(str(row["availabilityView"]))
        slots = free_slots(views, args.start, args.minutes)
        return CalendarResult(
            state="availability",
            account=args.account,
            data=json.dumps([slot.model_dump(mode="json") for slot in slots], ensure_ascii=False),
        )

    async def create(self, args: CreateInput, context: ExecutionContext) -> CalendarResult:
        data = await self._call(args.account, context, "POST", "/me/events", payload_of(args.event))
        event = event_of(data)
        return CalendarResult(
            state="created",
            account=args.account,
            event_id=event.id,
            data=json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
        )

    async def update(self, args: UpdateInput, context: ExecutionContext) -> CalendarResult:
        data = await self._call(
            args.account,
            context,
            "PATCH",
            "/me/events/" + args.event_id,
            payload_of(args.event),
        )
        event = event_of(data)
        return CalendarResult(
            state="updated",
            account=args.account,
            event_id=event.id,
            data=json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
        )

    async def cancel(self, args: CancelInput, context: ExecutionContext) -> CalendarResult:
        await self._call(
            args.account,
            context,
            "POST",
            "/me/events/" + args.event_id + "/cancel",
            {"Comment": args.comment},
        )
        return CalendarResult(state="cancelled", account=args.account, event_id=args.event_id)
