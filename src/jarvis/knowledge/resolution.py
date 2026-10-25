"""Deciding whether two observations describe the same entity, and when to ask the owner.

Only an external identifier resolves an entity on its own: it is the service's own claim
that two records are the same. A matching name is returned as a candidate and nothing more,
because people share names and a wrong merge silently redirects later work — the wrong
John's task, the wrong client's invoice. The owner confirms a name match once; from then on
the identifiers carry it.

"Once" used to mean once per session, because nothing remembered the answer. Learned memory
does: a confirmation is written down as this exact name meaning this exact entity, and the
same question is not asked again until the owner deletes it or it expires. That decides who
is meant, never what may be done — a write still goes only to a target observed in the
current task.
"""

from dataclasses import dataclass, field
from threading import Event
from typing import Literal

from jarvis.knowledge.models import Entity, EntityDraft
from jarvis.knowledge.store import KnowledgeStore
from jarvis.memory.derived import DerivedStore
from jarvis.memory.store import MemoryFailure

Match = Literal["identifier", "confirmed", "name", "new"]


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
    store: KnowledgeStore,
    draft: EntityDraft,
    cancelled: Event | None = None,
    *,
    confirmed: DerivedStore | None = None,
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
    if not candidates:
        return Resolution("new")
    if confirmed is not None:
        for label in labels:
            chosen = confirmed.entity_for(label, cancelled)
            # Only among today's candidates: a remembered answer selects, it never conjures.
            picked = next((entity for entity in candidates if entity.id == chosen), None)
            if picked is not None:
                return Resolution("confirmed", picked, candidates)
    return Resolution("name", None, candidates)


def confirm(
    store: KnowledgeStore,
    entity: Entity,
    draft: EntityDraft,
    cancelled: Event | None = None,
    *,
    confirmed: DerivedStore | None = None,
) -> Entity:
    """Record the owner's decision that a candidate and an observation are the same entity.

    The decision outlives the session when there is somewhere to keep it; if there is not,
    the graph still merges and only the question comes back.
    """
    merged = store.attach(entity.id, draft, cancelled)
    if confirmed is not None:
        try:
            confirmed.record("entity", draft.name, merged.id, cancelled)
        except MemoryFailure:
            return merged
    return merged
