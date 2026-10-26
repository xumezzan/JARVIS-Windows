"""Calendar actions over the existing verified Microsoft account and Graph transport."""

from typing import Literal
from urllib.parse import quote

from pydantic import Field

from jarvis.connectors.microsoft.calendar import SELECT, event_of, instant, payload_of
from jarvis.connectors.microsoft.models import AccountInput, Draft, Event, EventInput, RangeInput
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.mail.session import MailSession
from jarvis.tools.base import ExecutionContext, ToolModel


class DraftEventInput(AccountInput):
    event: Draft


class CreateEventInput(DraftEventInput):
    draft_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class CalendarResult(ToolModel):
    state: Literal["listed", "local_draft", "created"]
    account: Account
    draft_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")
    event_id: str = Field(default="", max_length=512)
    event: Draft | None = None
    events: tuple[Event, ...] = Field(default=(), max_length=25)
    truncated: bool = False
    delivery_verified: Literal[False] = False


class CalendarSession:
    def __init__(self, session: MailSession) -> None:
        self.session = session

    async def list_events(self, args: RangeInput, context: ExecutionContext) -> CalendarResult:
        token = await self.session.token(args.account, context)
        status, data = await self.session.graph.request(
            token,
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
        await context.checkpoint()
        if not self.session.matches(args.account):
            raise MailFailure("mail_account_changed")
        rows = data.get("value")
        if status != 200 or not isinstance(rows, list) or len(rows) > args.limit:
            raise MailFailure("mail_response")
        result = CalendarResult(
            state="listed",
            account=args.account,
            events=tuple(event_of(row) for row in rows),
            truncated=bool(data.get("@odata.nextLink"))
            or any(
                isinstance(row, dict)
                and isinstance(row.get("attendees"), list)
                and len(row["attendees"]) > 20
                for row in rows
            ),
        )
        if len(result.model_dump_json().encode()) > 48000:
            raise MailFailure("mail_response_limit")
        return result

    async def create_draft_event(
        self, args: DraftEventInput, context: ExecutionContext
    ) -> CalendarResult:
        await context.checkpoint()
        key = self.session.local_event_draft(args.account, args.event)
        return CalendarResult(
            state="local_draft", account=args.account, draft_id=key, event=args.event
        )

    async def create_event(
        self, args: CreateEventInput, context: ExecutionContext
    ) -> CalendarResult:
        token = await self.session.token(args.account, context)
        payload = payload_of(args.event) | {"transactionId": args.draft_id}
        await context.checkpoint()
        self.session.claim_event_draft(args.account, args.draft_id, args.event)
        # Exactly one effect, after the engine's durable STARTED audit record.
        status, data = await self.session.graph.request(token, "POST", "/me/events", payload)
        key = data.get("id")
        if status != 201 or not isinstance(key, str) or key in (".", ".."):
            raise MailFailure("mail_response")
        created_id = EventInput(account=args.account, event_id=key).event_id
        if not self.session.matches(args.account):
            raise MailFailure("mail_account_changed")
        await context.checkpoint()
        # Re-verify the account before read-back too; never report an old account's event.
        token = await self.session.token(args.account, context)
        read_status, stored = await self.session.graph.request(
            token,
            "GET",
            "/me/events/" + quote(created_id, safe=""),
            params={"$select": SELECT + ",body,transactionId"},
        )
        await context.checkpoint()
        observed = event_of(stored)
        body, location, attendees = (
            stored.get("body"),
            stored.get("location"),
            stored.get("attendees"),
        )
        addresses = (
            [
                row.get("emailAddress", {}).get("address")
                for row in attendees
                if isinstance(row, dict) and isinstance(row.get("emailAddress"), dict)
            ]
            if isinstance(attendees, list)
            else None
        )
        if (
            read_status != 200
            or not self.session.matches(args.account)
            or observed.id != created_id
            or observed.subject != args.event.subject
            or instant(stored.get("start")) != args.event.start
            or instant(stored.get("end")) != args.event.end
            or observed.cancelled
            or observed.organizer.casefold() != args.account.address.casefold()
            or not isinstance(body, dict)
            or str(body.get("contentType", "")).lower() != "text"
            or body.get("content") != args.event.body
            or not isinstance(location, dict)
            or location.get("displayName") != args.event.location
            or addresses is None
            or not isinstance(attendees, list)
            or len(attendees) != len(args.event.attendees)
            or len(addresses) != len(args.event.attendees)
            or any(not isinstance(value, str) for value in addresses)
            or sorted(str(value).casefold() for value in addresses)
            != sorted(value.casefold() for value in args.event.attendees)
            or stored.get("transactionId") != args.draft_id
        ):
            raise MailFailure("mail_verification")
        return CalendarResult(
            state="created",
            account=args.account,
            draft_id=args.draft_id,
            event_id=observed.id,
            event=args.event,
            events=(observed,),
        )
