"""How a Fireflies answer reads as entities: recorded meetings, and who spoke in them."""

import json
from typing import Any

from jarvis.knowledge.harvest import Link, Observation, draft, person
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "fireflies"


def meeting(row: dict[str, Any], tool: str, run: str) -> EntityDraft | None:
    return draft(
        type="meeting",
        name=str(row.get("title") or "Встреча без названия")[:120],
        external=(Reference(service=SERVICE, value=str(row["id"]), observed=tool, run=run),),
    )


def attend(row: dict[str, Any], tool: str, run: str) -> Observation:
    entities: list[EntityDraft | None] = [meeting(row, tool, run)]
    links: list[Link] = []
    organizer = str(row.get("organizer") or "")
    if organizer:
        entities.append(person(organizer, SERVICE, tool, run))
        links.append(Link(len(entities) - 1, "owns", 0))
    for address in row.get("participants") or []:
        # Participants arrive as plain addresses; anything else is not an identity.
        if not isinstance(address, str) or "@" not in address or address == organizer:
            continue
        entities.append(person(address, SERVICE, tool, run))
        links.append(Link(len(entities) - 1, "participates_in", 0))
    return Observation(tuple(entities), tuple(links))


def read(tool: str, run: str, result: dict[str, Any]) -> Observation:
    raw = json.loads(result.get("data") or "null")
    if isinstance(raw, dict):
        raw = [raw.get("meeting")]
    rows = raw if isinstance(raw, list) else []
    entities: list[EntityDraft | None] = []
    links: list[Link] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        observation = attend(row, tool, run)
        offset = len(entities)
        entities.extend(observation.entities)
        links.extend(
            Link(link.subject + offset, link.predicate, link.object + offset)
            for link in observation.links
        )
    return Observation(tuple(entities), tuple(links))


MAPPERS = {
    "fireflies.list": read,
    "fireflies.search": read,
    "fireflies.get": read,
    "fireflies.transcript": read,
}
