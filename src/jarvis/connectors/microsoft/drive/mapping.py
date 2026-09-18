"""How a drive answer reads as entities: files, and nothing inside them.

A file is a document with an identity OneDrive owns. Its contents are not harvested: the
graph keeps who and what, never content, and the cells of a report are content by
definition - the numbers in them are exactly the kind of thing that must not leak into a
prompt because a file once appeared in a search.
"""

import json
from typing import Any

from jarvis.knowledge.harvest import Observation, draft
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "onedrive"


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
    return [row for row in raw if isinstance(row, dict) and row.get("id") and row.get("name")]


def read_files(tool: str, run: str, result: dict[str, Any]) -> Observation:
    entities: list[EntityDraft | None] = [
        draft(
            type="document",
            name=str(row["name"])[:120],
            external=reference(str(row["id"]), tool, run),
        )
        for row in rows(result)
    ]
    return Observation(tuple(entities), ())


MAPPERS = {"onedrive.files": read_files}
