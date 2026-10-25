"""The runner's view of a durable run: what may already exist, and what just happened.

Kept as a narrow protocol so the planner depends on three verbs rather than on a database.
"""

from threading import Event
from typing import Protocol

from jarvis.core.workflow.models import Phase, StepRecord, step_key
from jarvis.core.workflow.store import WorkflowFailure, WorkflowStore
from jarvis.permissions.engine import Outcome


class Journal(Protocol):
    run_id: str

    def issued(self, key: str) -> bool: ...

    def issue(self, index: int, tool: str, key: str) -> None: ...

    def complete(self, key: str, outcome: Outcome) -> None: ...

    def phase(self, phase: Phase) -> None: ...


class RunJournal:
    """Binds one run record to its store.

    `issued` is deliberately conservative: if the journal cannot be read, it answers that
    the effect may already have happened. Refusing to act on an unreadable journal is the
    safe direction — the opposite answer risks issuing the same effect twice.
    """

    def __init__(
        self, store: WorkflowStore, run_id: str, *, cancelled: Event | None = None
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.cancelled = cancelled

    def issued(self, key: str) -> bool:
        try:
            record = self.store.get(self.run_id, self.cancelled)
        except WorkflowFailure:
            return True
        return record is not None and record.issued(key) is not None

    def issue(self, index: int, tool: str, key: str) -> None:
        self.store.append(
            self.run_id,
            StepRecord(
                index=index, tool=tool, key=key, state="issued", updated=int(self.store.clock())
            ),
            self.cancelled,
        )

    def complete(self, key: str, outcome: Outcome) -> None:
        self.store.complete(
            self.run_id,
            key,
            outcome.status,
            outcome.error,
            outcome.may_have_effects,
            self.cancelled,
        )

    def phase(self, phase: Phase) -> None:
        """Where the run stands now, so a restart finds it in the state it stopped in.

        A cancelled store makes this fail like any other write. The caller treats that as
        losing the note, not as losing the task: an unfinished run is the safe appearance,
        because it is offered for resumption instead of being believed to be done.
        """
        self.store.phase(self.run_id, phase, self.cancelled)


__all__ = ["Journal", "RunJournal", "step_key"]
