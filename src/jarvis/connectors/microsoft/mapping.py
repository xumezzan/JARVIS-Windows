"""How a calendar answer reads as entities: meetings, and the people in them."""

import json
from typing import Any

from jarvis.knowledge.harvest import Link, Observation, draft, person
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "calendar"


def meeting(row: dict[str, Any], tool: str, run: str) -> EntityDraft | None:
    return draft(
        type="meeting",
        name=str(row.get("subject") or "Встреча без названия")[:120],
        external=(Reference(service=SERVICE, value=str(row["id"]), observed=tool, run=run),),
    )


def read(tool: str, run: str, result: dict[str, Any]) -> Observation:
    """A listing holds an array of events; a read or a write holds one."""
    raw = json.loads(result.get("data") or "null")
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    entities: list[EntityDraft | None] = []
    links: list[Link] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        entities.append(meeting(row, tool, run))
        index = len(entities) - 1
        organizer = str(row.get("organizer") or "")
        if organizer:
            entities.append(person(organizer, SERVICE, tool, run))
            links.append(Link(len(entities) - 1, "owns", index))
        for attendee in row.get("attendees") or []:
            if not isinstance(attendee, dict) or not attendee.get("address"):
                continue
            entities.append(
                person(
                    str(attendee["address"]),
                    SERVICE,
                    tool,
                    run,
                    name=str(attendee.get("name") or ""),
                )
            )
            links.append(Link(len(entities) - 1, "participates_in", index))
    return Observation(tuple(entities), tuple(links))


MAPPERS = {
    "calendar.list": read,
    "calendar.search": read,
    "calendar.get": read,
    "calendar.create": read,
    "calendar.update": read,
}
