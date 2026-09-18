"""Registering Teams' declared capabilities as typed tools.

A sent message is read back before it is called done. Graph answering 201 says the request
was accepted, and a message that arrived truncated or in the wrong conversation is read by
people who act on it - so the stored message is fetched by its own identifier and its words
are compared with the words the owner approved.

The comparison is made on flattened text, and that is worth stating rather than hiding.
Teams returns a message as a body whose content this connector reduces to a single line, so
a draft written in paragraphs comes back as one; comparing the flattened forms catches a
mangled, cut or substituted message, which is the failure that actually happens. It would
not catch Teams keeping different line breaks than the owner typed.
"""

import json
from typing import Any

from jarvis.connectors.base import Capability, tool_name
from jarvis.connectors.teams.connector import CAPABILITIES, TeamsConnector
from jarvis.connectors.teams.models import (
    ChatsInput,
    DraftInput,
    MessagesInput,
    SendInput,
    TeamsResult,
)
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def flat(value: str) -> str:
    return " ".join(value.split())


def register_teams(
    registry: ToolRegistry,
    connector: TeamsConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        account = getattr(args, "account", None)
        return account is not None and connector.session.matches(account)

    async def verify(args: ToolModel, result: TeamsResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if not connector.session.matches(result.account):
            return False
        if result.state == "draft":
            # Nothing was sent, so the only claim to check is that the preview is the note.
            asked = getattr(args, "message", None)
            shown = json.loads(result.data or "{}")
            return asked is not None and shown == asked.model_dump(mode="json")
        if result.state == "sent":
            asked = getattr(args, "message", None)
            if asked is None or not result.message_id:
                return False
            written = await connector.stored(
                result.account, result.chat, result.message_id, context
            )
            return flat(written.text) == flat(asked.text)
        return True

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("chats", ChatsInput, connector.chats),
        ("messages", MessagesInput, connector.messages),
        ("draft", DraftInput, connector.draft),
        ("send", SendInput, connector.send),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                TeamsResult,
                check,
                run,
                verify,
                # Two calls fit inside this: the send and the read-back that proves it.
                timeout_seconds=30,
                cancellation="Cancel before the request is issued; a sent message is not unsent.",
                idempotency=(
                    "Repeatable read."
                    if capability.idempotent
                    else "One execution per prepared request; a repeat would send it twice."
                ),
            )
        )
