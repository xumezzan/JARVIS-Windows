"""How a Teams answer reads as entities: conversations, and who spoke in them.

Authors arrive as display names, not addresses, so they are not merged with the people the
calendar, Outlook and Fireflies know. Two services agreeing that somebody is called "Иван
Петров" is not evidence that it is the same Иван Петров, and the graph merges on
identifiers rather than on strings - so an author is recorded as a person known to Teams
and stays a separate record until an address ties them together.

What was said is not harvested. The graph keeps who and what, never content, and a chat is
content by definition.
"""

import json
from typing import Any

from jarvis.knowledge.harvest import Link, Observation, draft
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "teams"
UNNAMED = "Чат без названия"


def reference(value: str, tool: str, run: str) -> tuple[Reference, ...]:
    try:
        return (Reference(service=SERVICE, value=value, observed=tool, run=run),)
    except ValueError:
        return ()


def rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = json.loads(result.get("data") or "null")
    if isinstance(raw, dict):
        raw = [raw]
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def read_chats(tool: str, run: str, result: dict[str, Any]) -> Observation:
    entities: list[EntityDraft | None] = [
        draft(
            type="conversation",
            name=str(row.get("topic") or UNNAMED)[:120],
            external=reference(str(row["id"]), tool, run),
        )
        for row in rows(result)
        if row.get("id")
    ]
    return Observation(tuple(entities), ())


def read_messages(tool: str, run: str, result: dict[str, Any]) -> Observation:
    """One conversation, and the people observed writing in it."""
    chat = str(result.get("chat") or "")
    if not chat:
        return Observation((), ())
    entities: list[EntityDraft | None] = [
        draft(type="conversation", name=UNNAMED, external=reference(chat, tool, run))
    ]
    links: list[Link] = []
    seen: set[str] = set()
    for row in rows(result):
        author = str(row.get("author") or "").strip()
        if not author or author.casefold() in seen:
            continue
        seen.add(author.casefold())
        # Named, not addressed: this record answers "who in Teams", not "which person".
        entities.append(draft(type="person", name=author[:120]))
        links.append(Link(len(entities) - 1, "participates_in", 0))
    return Observation(tuple(entities), tuple(links))


MAPPERS = {
    "teams.chats": read_chats,
    "teams.messages": read_messages,
}
