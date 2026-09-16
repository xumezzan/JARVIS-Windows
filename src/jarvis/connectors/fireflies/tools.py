"""Registering the Fireflies reads as typed tools.

Nothing here writes, so verification is the honest minimum: a read is proved by having
returned a well-formed answer, and claiming more would be theatre.
"""

from typing import Any

from jarvis.connectors.base import Capability, tool_name
from jarvis.connectors.fireflies.connector import CAPABILITIES, FirefliesConnector
from jarvis.connectors.fireflies.models import (
    FirefliesResult,
    MeetingInput,
    RangeInput,
    SearchInput,
    TranscriptInput,
)
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def register_fireflies(
    registry: ToolRegistry,
    connector: FirefliesConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def verify(args: ToolModel, result: FirefliesResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return isinstance(result, FirefliesResult)

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("list", RangeInput, connector.list),
        ("search", SearchInput, connector.search),
        ("get", MeetingInput, connector.get),
        ("transcript", TranscriptInput, connector.transcript),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                FirefliesResult,
                check,
                run,
                verify,
                timeout_seconds=30,
                cancellation="Cancel the pending query; nothing is written to the service.",
                idempotency="Repeatable read, but each attempt spends from the daily allowance.",
            )
        )
