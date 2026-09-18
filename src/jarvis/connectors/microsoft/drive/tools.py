"""Registering the drive's declared capabilities as typed tools.

A written range is read back before it is called done. Graph answering 200 says the request
was accepted, and a report that landed one column to the left is read by people who act on
it. The read-back is a real second look, not the service's own report of itself: a range
has an address, so it can be asked for again - which is exactly what a freshly created
Notion block or Teams message cannot do.

The comparison is exact, with one allowance: a number is compared as a number, because
Excel stores 42 and "42" the same way once a cell holds a value. Everything else must come
back character for character. That means a string Excel decides to reinterpret - a date
like "01.09.2026", which it stores as a serial number - fails verification rather than
passing quietly, and the failure is the honest answer: what was asked for is not what the
workbook now holds.
"""

import json
from typing import Any

from jarvis.connectors.base import Capability, tool_name
from jarvis.connectors.microsoft.drive.connector import CAPABILITIES, DriveConnector
from jarvis.connectors.microsoft.drive.models import (
    Cell,
    DriveResult,
    ReadInput,
    SearchInput,
    SheetsInput,
    WriteInput,
)
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def same(asked: Cell, stored: object) -> bool:
    if isinstance(asked, int | float) and isinstance(stored, int | float):
        return float(asked) == float(stored)
    return isinstance(stored, str | int | float) and str(asked) == str(stored)


def matches(asked: list[list[Cell]], stored: object) -> bool:
    if not isinstance(stored, list) or len(stored) != len(asked):
        return False
    for wanted_row, stored_row in zip(asked, stored, strict=True):
        if not isinstance(stored_row, list) or len(stored_row) != len(wanted_row):
            return False
        if not all(same(cell, held) for cell, held in zip(wanted_row, stored_row, strict=True)):
            return False
    return True


def register_drive(
    registry: ToolRegistry,
    connector: DriveConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        account = getattr(args, "account", None)
        return account is not None and connector.session.matches(account)

    async def verify(args: ToolModel, result: DriveResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if not connector.session.matches(result.account):
            return False
        if result.state == "written":
            wanted = getattr(args, "range", None)
            if wanted is None:
                return False
            observed = await connector.stored(wanted, result.account, context)
            return matches(wanted.values, json.loads(observed.data or "null"))
        return True

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("files", SearchInput, connector.files),
        ("sheets", SheetsInput, connector.sheets),
        ("read", ReadInput, connector.read),
        ("write", WriteInput, connector.write),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                DriveResult,
                check,
                run,
                verify,
                # Two calls fit inside this: the write and the read-back that proves it.
                timeout_seconds=40,
                cancellation=(
                    "Cancel before the request is issued; overwritten cells are not restored."
                ),
                idempotency=(
                    "Repeatable read."
                    if capability.idempotent
                    else "One execution per prepared request; the same cells are written again."
                ),
            )
        )
