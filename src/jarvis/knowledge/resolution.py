"""Deciding whether two observations describe the same entity, and when to ask the owner.

Only an external identifier resolves an entity on its own: it is the service's own claim
that two records are the same. A matching name is returned as a candidate and nothing more,
because people share names and a wrong merge silently redirects later work — the wrong
John's task, the wrong client's invoice. The owner confirms a name match once; from then on
the identifiers carry it.
"""

from dataclasses import dataclass, field
from threading import Event
from typing import Literal

from jarvis.knowledge.models import Entity, EntityDraft
from jarvis.knowledge.store import KnowledgeStore

Match = Literal["identifier", "name", "new"]


@dataclass(frozen=True)
class Resolution:
    match: Match
    entity: Entity | None = None
    candidates: tuple[Entity, ...] = field(default=())

    @property
    def needs_confirmation(self) -> bool:
        """A name match is a question for the owner, never a decision taken here."""
        return self.match == "name"


def resolve(
    store: KnowledgeStore, draft: EntityDraft, cancelled: Event | None = None
) -> Resolution:
    for reference in draft.external:
        found = store.by_reference(reference.service, reference.value, cancelled)
        if found is not None and found.type == draft.type:
            return Resolution("identifier", found)
    labels = {draft.name.casefold(), *(alias.casefold() for alias in draft.aliases)}
    candidates = tuple(
        entity
        for entity in store.search(draft.name, draft.type, cancelled=cancelled)
        # An exact label, not a substring: "John" must not claim "Johnson".
        if labels & {entity.name.casefold(), *(alias.casefold() for alias in entity.aliases)}
    )
    if candidates:
        return Resolution("name", None, candidates)
    return Resolution("new")


def confirm(
    store: KnowledgeStore, entity: Entity, draft: EntityDraft, cancelled: Event | None = None
) -> Entity:
    """Record the owner's decision that a candidate and an observation are the same entity."""
    return store.attach(entity.id, draft, cancelled)
