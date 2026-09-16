"""What the planner is told about the user's world, and how much of it.

Two sources meet here. The label profile in `jarvis.memory` is what the owner chose to
remember and still chooses to send. The knowledge graph is what connectors observed, and
it can hold thousands of entities, so it is never sent whole: only entities the request
actually names, ordered by how well they match, cut to a budget.

The budget is a hard cap in characters, not an estimate. Dropping the weakest match until
the rest fits is deterministic and leaves the strongest evidence in place. Everything
assembled here is untrusted data — it describes what is known, never what to do.
"""

import json
from dataclasses import dataclass, field
from threading import Event

from jarvis.core.context.relevance import rank, terms
from jarvis.knowledge.models import MAX_HINTS, Hint, KnowledgeContext
from jarvis.knowledge.store import KnowledgeFailure, KnowledgeStore
from jarvis.memory.models import MemoryContext


@dataclass(frozen=True)
class Budget:
    entities: int = 6
    characters: int = 2000

    def __post_init__(self) -> None:
        if not 0 <= self.entities <= MAX_HINTS:
            raise ValueError("Entity budget is out of range.")
        if not 200 <= self.characters <= 8000:
            raise ValueError("Character budget is out of range.")


@dataclass(frozen=True)
class Context:
    memory: MemoryContext = field(default_factory=MemoryContext)
    knowledge: KnowledgeContext = field(default_factory=KnowledgeContext)

    @property
    def empty(self) -> bool:
        return self.memory.empty and self.knowledge.empty

    def size(self) -> int:
        return len(serialize(self.knowledge))


def serialize(knowledge: KnowledgeContext) -> str:
    return json.dumps(knowledge.model_dump(mode="json"), ensure_ascii=False)


def assemble(
    command: str,
    store: KnowledgeStore | None = None,
    *,
    memory: MemoryContext | None = None,
    budget: Budget | None = None,
    cancelled: Event | None = None,
) -> Context:
    """Select what is relevant. A store that cannot be read yields no knowledge, not an error.

    Context is an aid, never a precondition: a broken graph must not stop the user's
    request, it must only leave the planner with less to go on.
    """
    chosen = memory or MemoryContext()
    limits = budget or Budget()
    if store is None or limits.entities == 0:
        return Context(chosen)
    spoken = terms(command)
    if not spoken:
        return Context(chosen)
    try:
        known = store.entities(cancelled)
    except KnowledgeFailure:
        return Context(chosen)
    hints = [entity.hint() for _, entity in rank(known, spoken)[: limits.entities]]
    while hints and len(serialize(KnowledgeContext(entities=tuple(hints)))) > limits.characters:
        hints.pop()
    return Context(chosen, KnowledgeContext(entities=tuple(hints)))


def summary(context: Context) -> tuple[Hint, ...]:
    """What the interface can show the owner as 'what Jarvis is going on'."""
    return context.knowledge.entities
