"""What the planner is told about the user's world, and how much of it.

Three sources meet here. The label profile in `jarvis.memory` is what the owner chose to
remember and still chooses to send. The knowledge graph is what connectors observed, and
it can hold thousands of entities, so it is never sent whole: only entities the request
actually names, ordered by how well they match, cut to a budget. Learned memory is what
the owner's own finished runs turned out to mean - the words they use for a tool or an
application - selected by the same word matching as everything else.

The budget is a hard cap in characters, not an estimate, and it covers all of it: learned
phrases take at most a quarter of it, the graph gets what is left, and each drops its
weakest match until it fits. Deterministic, and the strongest evidence stays. Everything
assembled here is untrusted data — it describes what is known, never what to do.
"""

import json
from dataclasses import dataclass, field
from threading import Event

from jarvis.core.context.relevance import matches, rank, terms
from jarvis.knowledge.models import MAX_HINTS, Hint, KnowledgeContext
from jarvis.knowledge.store import KnowledgeFailure, KnowledgeStore
from jarvis.memory.derived import (
    MAX_DERIVED,
    Derived,
    DerivedContext,
    DerivedStore,
)
from jarvis.memory.derived import context as learned
from jarvis.memory.derived import serialize as serialize_learned
from jarvis.memory.models import MemoryContext
from jarvis.memory.store import MemoryFailure

LEARNED_SHARE = 4


@dataclass(frozen=True)
class Budget:
    entities: int = 6
    phrases: int = 6
    characters: int = 2000

    def __post_init__(self) -> None:
        if not 0 <= self.entities <= MAX_HINTS:
            raise ValueError("Entity budget is out of range.")
        if not 0 <= self.phrases <= MAX_DERIVED:
            raise ValueError("Phrase budget is out of range.")
        if not 200 <= self.characters <= 8000:
            raise ValueError("Character budget is out of range.")


@dataclass(frozen=True)
class Context:
    memory: MemoryContext = field(default_factory=MemoryContext)
    knowledge: KnowledgeContext = field(default_factory=KnowledgeContext)
    derived: DerivedContext = field(default_factory=DerivedContext)

    @property
    def empty(self) -> bool:
        return self.memory.empty and self.knowledge.empty and self.derived.empty

    def size(self) -> int:
        return len(serialize(self.knowledge)) + len(serialize_learned(self.derived))


def serialize(knowledge: KnowledgeContext) -> str:
    return json.dumps(knowledge.model_dump(mode="json"), ensure_ascii=False)


def recall(
    store: DerivedStore | None,
    spoken: tuple[str, ...],
    limit: int,
    characters: int,
    cancelled: Event | None = None,
) -> DerivedContext:
    """Learned phrases the request actually uses. Confirmed names never come along."""
    if store is None or limit == 0:
        return DerivedContext()
    try:
        records = store.read(cancelled)
    except MemoryFailure:
        return DerivedContext()
    named: list[Derived] = [
        record
        for record in records
        if record.kind != "entity" and any(matches(record.phrase, word) for word in spoken)
        # The store is already ordered by how many finished runs agreed.
    ][:limit]
    while named and len(serialize_learned(learned(tuple(named)))) > characters:
        named.pop()
    return learned(tuple(named))


def assemble(
    command: str,
    store: KnowledgeStore | None = None,
    *,
    memory: MemoryContext | None = None,
    derived: DerivedStore | None = None,
    budget: Budget | None = None,
    cancelled: Event | None = None,
) -> Context:
    """Select what is relevant. A store that cannot be read yields no knowledge, not an error.

    Context is an aid, never a precondition: a broken graph or an unreadable learned store
    must not stop the user's request, it must only leave the planner with less to go on.
    """
    chosen = memory or MemoryContext()
    limits = budget or Budget()
    spoken = terms(command)
    if not spoken:
        return Context(chosen)
    phrases = recall(derived, spoken, limits.phrases, limits.characters // LEARNED_SHARE, cancelled)
    # Whatever the learned phrases did not use stays available to the graph.
    left = limits.characters - len(serialize_learned(phrases))
    if store is None or limits.entities == 0:
        return Context(chosen, KnowledgeContext(), phrases)
    try:
        known = store.entities(cancelled)
    except KnowledgeFailure:
        return Context(chosen, KnowledgeContext(), phrases)
    hints = [entity.hint() for _, entity in rank(known, spoken)[: limits.entities]]
    while hints and len(serialize(KnowledgeContext(entities=tuple(hints)))) > left:
        hints.pop()
    return Context(chosen, KnowledgeContext(entities=tuple(hints)), phrases)


def summary(context: Context) -> tuple[Hint, ...]:
    """What the interface can show the owner as 'what Jarvis is going on'."""
    return context.knowledge.entities
