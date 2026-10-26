"""Three calendar capabilities sharing mail authentication and exact UI approval."""

from jarvis.connectors.base import Capability
from jarvis.connectors.microsoft.models import AccountInput, RangeInput
from jarvis.mail.calendar import CalendarResult, CalendarSession, CreateEventInput, DraftEventInput
from jarvis.mail.session import MailSession
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def register_calendar(
    registry: ToolRegistry, session: MailSession, matrix: PermissionMatrix | None = None
) -> CalendarSession:
    calendar = CalendarSession(session)
    policy = matrix or PermissionMatrix()

    def effective(name: str, risk: Risk, legacy: str = "") -> Risk:
        capability = Capability(legacy or name, "Workspace calendar capability.", risk, False)
        inherited = policy.effective("calendar", capability)
        return policy.effective(
            "calendar", Capability(name, capability.description, inherited, False)
        )

    async def check(args: AccountInput, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return session.matches(args.account) and (
            not isinstance(args, CreateEventInput)
            or session.event_draft_matches(args.account, args.draft_id, args.event)
        )

    async def verify(args: ToolModel, result: CalendarResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if not session.matches(result.account):
            return False
        if isinstance(args, CreateEventInput):
            return (
                result.state == "created" and result.event == args.event and bool(result.event_id)
            )
        if isinstance(args, DraftEventInput):
            return session.event_draft_matches(result.account, result.draft_id, args.event)
        return result.state == "listed"

    registry.register(
        ToolSpec(
            "calendar.list_events",
            "Read a bounded UTC calendar interval for the connected Outlook account.",
            effective("list_events", Risk.SAFE, "list"),
            RangeInput,
            CalendarResult,
            check,
            calendar.list_events,
            verify,
            timeout_seconds=45,
        )
    )
    registry.register(
        ToolSpec(
            "calendar.create_draft_event",
            "Preview an event in session RAM; no Graph write or invitation.",
            effective("create_draft_event", Risk.SAFE),
            DraftEventInput,
            CalendarResult,
            check,
            calendar.create_draft_event,
            verify,
        )
    )
    registry.register(
        ToolSpec(
            "calendar.create_event",
            "Create the exact RAM draft and invite its explicit attendees; read back to verify.",
            effective("create_event", Risk.CONFIRM, "create"),
            CreateEventInput,
            CalendarResult,
            check,
            calendar.create_event,
            verify,
            timeout_seconds=60,
            idempotency="A draft is consumed before one POST; never retry an uncertain creation.",
        )
    )
    return calendar
