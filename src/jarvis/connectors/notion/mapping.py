"""How a Notion answer reads as entities: pages, and nothing else.

A page is a document with an identity Notion owns. Its text is not harvested: the graph
keeps who and what, never content, and a page of notes is content by definition.
"""

import json
from typing import Any

from jarvis.knowledge.harvest import Link, Observation, draft
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "notion"


def reference(value: str, tool: str, run: str) -> tuple[Reference, ...]:
    try:
        return (Reference(service=SERVICE, value=value, observed=tool, run=run),)
    except ValueError:
        return ()


def rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = json.loads(result.get("data") or "null")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict) and row.get("id") and row.get("title")]


def read(tool: str, run: str, result: dict[str, Any]) -> Observation:
    entities: list[EntityDraft | None] = [
        draft(
            type="document",
            name=str(row["title"])[:120],
            external=reference(str(row["id"]), tool, run),
        )
        for row in rows(result)
    ]
    links: list[Link] = []
    return Observation(tuple(entities), tuple(links))


MAPPERS = {
    "notion.search": read,
    "notion.page": read,
    "notion.create_page": read,
}
