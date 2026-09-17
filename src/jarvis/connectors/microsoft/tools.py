"""Registering the calendar's declared capabilities as typed tools.

Every write is read back before it is reported as done. Graph answering 201 says the
request was accepted, not that the calendar now holds what was asked for, and a meeting
that silently landed at the wrong hour is worse than one that failed loudly. Cancellation
verifies the opposite way: for the organiser the event leaves the calendar entirely, so
its absence is the proof.
"""

import json
from typing import Any

from jarvis.connectors.base import Capability, tool_name
from jarvis.connectors.microsoft.calendar import CAPABILITIES, CalendarConnector
from jarvis.connectors.microsoft.graph import CalendarFailure
from jarvis.connectors.microsoft.models import (
    AvailabilityInput,
    CalendarResult,
    CancelInput,
    CreateInput,
    EventInput,
    RangeInput,
    SearchInput,
    UpdateInput,
)
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

# Each operation takes its own input model, so the table is typed at the ToolSpec.
WRITES = ("created", "updated")


def register_calendar(
    registry: ToolRegistry,
    connector: CalendarConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        account = getattr(args, "account", None)
        return account is not None and connector.session.matches(account)

    async def verify(args: ToolModel, result: CalendarResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if not connector.session.matches(result.account):
            return False
        if result.state in WRITES:
            written = getattr(args, "event", None)
            observed = await connector.get(
                EventInput(account=result.account, event_id=result.event_id), context
            )
            stored = json.loads(observed.data)
            return written is None or (
                stored.get("subject") == written.subject
                and stored.get("start") == written.start
                and stored.get("end") == written.end
            )
        if result.state == "cancelled":
            try:
                observed = await connector.get(
                    EventInput(account=result.account, event_id=result.event_id), context
                )
            except CalendarFailure as error:
                # The organiser's copy is removed outright, so absence is the confirmation.
                return error.code == "calendar_missing"
            return bool(json.loads(observed.data).get("cancelled"))
        return True

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("list", RangeInput, connector.list),
        ("search", SearchInput, connector.search),
        ("get", EventInput, connector.get),
        ("availability", AvailabilityInput, connector.availability),
        ("create", CreateInput, connector.create),
        ("update", UpdateInput, connector.update),
        ("cancel", CancelInput, connector.cancel),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                CalendarResult,
                check,
                run,
                verify,
                # Two calls fit inside this: the action and the read-back that proves it.
                timeout_seconds=30,
                cancellation="Cancel before the request is issued; an issued write is not undone.",
                idempotency=(
                    "Repeatable read."
                    if capability.idempotent
                    else "One execution per prepared request; a repeat would invite people twice."
                ),
            )
        )
