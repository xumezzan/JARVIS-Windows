"""Turning what a tool observed into entities, without ever steering the task.

Each connector says how to read its own results; this layer only applies those readings
and writes them down with provenance. Three rules hold it together.

A person is merged across services by their address, under the neutral `email` namespace,
because an address is the one identity Outlook, the calendar, Fireflies and later Asana all
agree on. Alongside it the connector records where the person was seen — `calendar:<address>`,
`fireflies:<address>` — so the graph can answer both "is this the same John" and "where does
he appear". Merging still follows identifiers alone: two strangers who share a name stay
two entities, because a name has never been proof.

Harvesting is an aid, never a precondition. A store that cannot be written leaves the graph
poorer and the user's task untouched; a request must not fail because a note could not be
filed. And it is bounded: one listing of twenty-five meetings must not write a thousand rows.

Everything harvested came from a service's answer, so it is untrusted content. It becomes
names and identifiers only, never instructions, and the validators refuse anything that
looks like a credential.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Protocol

from jarvis.knowledge.models import EntityDraft, Predicate, Reference
from jarvis.knowledge.store import KnowledgeFailure, KnowledgeStore

MAX_ENTITIES = 25
MAX_LINKS = 60
EMAIL = "email"


@dataclass(frozen=True)
class Link:
    """A relationship between two drafts of the same observation, held by position."""

    subject: int
    predicate: Predicate
    object: int


@dataclass(frozen=True)
class Observation:
    """A refused entity stays as a gap rather than shifting the ones after it."""

    entities: tuple[EntityDraft | None, ...] = ()
    links: tuple[Link, ...] = field(default=())


Mapper = Callable[[str, str, dict[str, Any]], Observation]


def draft(**fields: Any) -> EntityDraft | None:
    """Build an entity, or refuse it. One unusable label must not discard the rest.

    A meeting whose title reads like a credential is dropped; the people who attended it
    are still worth knowing, and the observation as a whole still lands.
    """
    try:
        return EntityDraft(**fields)
    except ValueError:
        return None


def person(address: str, service: str, tool: str, run: str, name: str = "") -> EntityDraft | None:
    """One person, addressable everywhere by the address and placed by the service."""
    label = (name or address).strip() or address
    try:
        references = (
            Reference(service=EMAIL, value=address, observed=tool, run=run),
            Reference(service=service, value=address, observed=tool, run=run),
        )
    except ValueError:
        return None
    return draft(
        type="person",
        name=label,
        aliases=(address,) if label != address else (),
        external=references,
    )


class Harvester(Protocol):
    def record(self, tool: str, run: str, result_json: str | None) -> None: ...


class GraphHarvester:
    def __init__(self, store: KnowledgeStore, mappers: Mapping[str, Mapper]) -> None:
        self.store = store
        self.mappers = dict(mappers)
        self.written = 0

    def record(self, tool: str, run: str, result_json: str | None) -> None:
        mapper = self.mappers.get(tool)
        if mapper is None or not result_json:
            return
        try:
            result = json.loads(result_json)
            if not isinstance(result, dict):
                return
            observation = mapper(tool, run, result)
        except Exception:
            # A connector's reading of its own answer must not break the user's task.
            return
        self._write(observation, tool, run)

    def _write(self, observation: Observation, tool: str, run: str) -> None:
        cancelled = Event()
        stored: list[str | None] = []
        for entity in observation.entities[:MAX_ENTITIES]:
            if entity is None:
                stored.append(None)
                continue
            try:
                stored.append(self.store.upsert(entity, cancelled).id)
                self.written += 1
            except KnowledgeFailure:
                # A conflicting identifier or a full store: skip this one, keep the rest.
                stored.append(None)
        for link in observation.links[:MAX_LINKS]:
            if not (0 <= link.subject < len(stored) and 0 <= link.object < len(stored)):
                continue
            subject, target = stored[link.subject], stored[link.object]
            if subject is None or target is None or subject == target:
                continue
            try:
                # The link carries the same provenance as the entities it joins.
                self.store.relate(subject, link.predicate, target, tool, run)
            except KnowledgeFailure:
                continue
