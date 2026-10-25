"""Registering Notion's declared capabilities as typed tools.

A created page is read back before it is called done: Notion answering 200 says the request
was accepted, and a page that landed under the wrong parent or with the wrong title is read
by people who act on it.

Appending is checked differently, and the difference is worth stating rather than hiding.
Notion answers a PATCH with the blocks it created, and those are compared with what was
asked for - the same words, the same number. That is the service's own report, not a second
look: a new block cannot be addressed again without widening the route table, and reading
the page back returns only its first hundred blocks, so a long page would fail for no
reason. It catches a mangled or dropped paragraph, which is the failure that actually
happens; it would not catch Notion lying about what it stored.
"""

import json
from typing import Any

from jarvis.connectors.base import Capability, tool_name
from jarvis.connectors.notion.connector import CAPABILITIES, NotionConnector
from jarvis.connectors.notion.models import (
    AppendInput,
    CreatePageInput,
    NotionResult,
    PageInput,
    ReadInput,
    SearchInput,
)
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def register_notion(
    registry: ToolRegistry,
    connector: NotionConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        state = await connector.authenticate()
        return state.state == "connected"

    async def verify(args: ToolModel, result: NotionResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if result.state == "created":
            wanted = getattr(args, "page", None)
            if wanted is None or not result.page_id:
                return False
            observed = await connector.page(PageInput(page=result.page_id), context)
            stored = json.loads(observed.data)
            return bool(stored.get("title") == wanted.title)
        if result.state == "appended":
            asked = list(getattr(args, "paragraphs", []))
            written = json.loads(result.data or "[]")
            return isinstance(written, list) and written == asked
        return True

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("search", SearchInput, connector.search),
        ("page", PageInput, connector.page),
        ("read", ReadInput, connector.read),
        ("create_page", CreatePageInput, connector.create_page),
        ("append", AppendInput, connector.append),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                NotionResult,
                check,
                run,
                verify,
                # Two calls fit inside this: the write and the read-back that proves it.
                timeout_seconds=30,
                cancellation="Cancel before the request is issued; a written page is not undone.",
                idempotency=(
                    "Repeatable read."
                    if capability.idempotent
                    else "One execution per prepared request; a repeat would write it twice."
                ),
            )
        )
