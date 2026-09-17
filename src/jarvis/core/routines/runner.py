"""One cycle of one routine: look, then either say something or stay quiet.

A cycle is an ordinary run. It takes a run id, writes to the same journal, prepares through
the same engine and is audited like anything the owner started by hand - because a step
carried out while nobody is watching needs that record more, not less.

What separates it from a run the owner started is what it may do. Observation is SAFE and
nothing else. Reversible work is carried out only when the owner allowed it for this exact
routine. Everything that needs confirmation is queued and waits, and the runner has no
approval authority at all: it is not given one, so a background run cannot confirm itself
even if a future change forgot the rule.
"""

import json
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from threading import Event
from time import time
from typing import Literal

from jarvis.core.routines.contracts import MAX_OBSERVATIONS, Call, Routine, Suggestion
from jarvis.core.routines.proposals import SuggestionQueue
from jarvis.core.routines.state import RoutineState
from jarvis.core.workflow.journal import Journal, RunJournal
from jarvis.core.workflow.models import step_key
from jarvis.core.workflow.store import WorkflowFailure, WorkflowStore
from jarvis.permissions.approvals import Action
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.registry import ToolRegistry

RECENT_RUNS = 200
UNATTENDED = (Risk.SAFE, Risk.ROUTINE)

Status_ = Literal["off", "not_due", "budget", "unavailable", "quiet", "suggested", "acted"]
# What became of one suggested effect: carried out, already in the world, or not done.
Done = Literal["done", "duplicate", "failed"]


@dataclass(frozen=True)
class Cycle:
    routine: str
    status: Status_
    suggested: int = 0
    acted: int = 0


CYCLE_TEXT: dict[str, str] = {
    "off": "выключена",
    "not_due": "ещё не время",
    "budget": "дневной предел запусков исчерпан",
    "unavailable": "нечего посмотреть: сервис недоступен или не подключён",
    "quiet": "повода нет",
    "suggested": "есть предложение",
    "acted": "выполнено самостоятельно",
}


def already_issued(store: WorkflowStore, key: str) -> bool:
    """Whether this exact effect was already put into the world, in this run or an older one.

    A journal that cannot be read answers yes. Refusing to act on an unreadable journal is
    the safe direction: the opposite answer risks doing the same thing twice.
    """
    try:
        runs = store.recent(RECENT_RUNS)
    except WorkflowFailure:
        return True
    return any(run.issued(key) is not None for run in runs)


class RoutineRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        workflows: WorkflowStore,
        state: RoutineState,
        queue: SuggestionQueue,
        routines: Sequence[Routine] = (),
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.workflows = workflows
        self.state = state
        self.queue = queue
        self.routines = tuple(routines)
        self.clock = clock
        self.cancelled = Event()
        self.active: Action | None = None

    def cancel(self) -> None:
        """The kill switch and shutdown both land here; an issued effect is not recalled."""
        self.cancelled.set()
        if self.active is not None:
            self.engine.cancel(self.active)

    def resume(self) -> None:
        """Switched back on. Nothing is replayed; the next cycle simply looks again."""
        self.cancelled.clear()

    async def tick(self) -> tuple[Cycle, ...]:
        cycles = []
        for routine in self.routines:
            if self.cancelled.is_set():
                break
            cycles.append(await self.run(routine))
        return tuple(cycles)

    async def run(self, routine: Routine) -> Cycle:
        name = routine.name
        settings = self.state.settings(name)
        if not self.state.active or not settings.enabled or self.cancelled.is_set():
            return Cycle(name, "off")
        if not self.state.due(name, routine.every_seconds):
            return Cycle(name, "not_due")
        if not self.state.spend():
            # The count is already durable, so the stop survives a restart of the process.
            return Cycle(name, "budget")
        self.state.mark(name)
        try:
            record = self.workflows.start("routine " + name, Mode.EXECUTE)
        except WorkflowFailure:
            return Cycle(name, "unavailable")
        journal = RunJournal(self.workflows, record.id, cancelled=self.cancelled)
        try:
            seen = await self._look(routine)
            suggestions = () if seen is None else routine.notice(seen, self.clock())
        except Exception:
            # A routine is trusted code, but a broken one must not take the session with it.
            self._finish(record.id, "failed")
            return Cycle(name, "unavailable")
        if seen is None:
            self._finish(record.id, "failed")
            return Cycle(name, "unavailable")
        # What the latest look no longer sees is no longer a reason to do anything.
        self.queue.refresh(name, frozenset(item.key for item in suggestions))
        acted = queued = 0
        for index, suggestion in enumerate(suggestions):
            unattended = (
                suggestion.actionable and settings.acts and self._unattended(suggestion.tool)
            )
            done = await self._act(suggestion, journal, index) if unattended else "failed"
            if done == "done":
                acted += 1
            elif done == "duplicate":
                # It is already in the world. Offering to do it again is not a suggestion.
                continue
            elif self.queue.add(suggestion):
                queued += 1
        self._finish(record.id, "done")
        status: Status_ = "acted" if acted else "suggested" if queued else "quiet"
        return Cycle(name, status, queued, acted)

    async def _look(self, routine: Routine) -> dict[str, str] | None:
        """Everything the routine asked to see, or nothing when one read did not work out."""
        seen: dict[str, str] = {}
        while len(seen) < MAX_OBSERVATIONS:
            if self.cancelled.is_set():
                return None
            call = routine.observe(seen, self.clock())
            if call is None:
                return seen
            if call.tool in seen:
                # An observation asked for twice would never end; the routine is at fault.
                return seen
            observed = await self._observe(call)
            if observed is None:
                return None
            seen[call.tool] = observed
        return seen

    async def _observe(self, call: Call) -> str | None:
        action = self.engine.prepare(call.tool, call.arguments, Mode.EXECUTE)
        if isinstance(action, Outcome):
            return None
        if action.risk is not Risk.SAFE:
            # Looking is reading. Anything that changes something is not an observation.
            self.engine.cancel(action)
            return None
        self.active = action
        try:
            outcome = await self.engine.execute(action)
        finally:
            self.active = None
        return outcome.result_json if outcome.status is Status.SUCCESS else None

    def _unattended(self, tool: str) -> bool:
        """What this routine may carry out by itself, once the owner allowed acting."""
        spec = self.registry.get(tool)
        return spec is not None and spec.risk in UNATTENDED

    async def _act(self, suggestion: Suggestion, journal: Journal, index: int) -> Done:
        try:
            arguments = json.loads(suggestion.arguments)
        except ValueError:
            return "failed"
        action = self.engine.prepare(suggestion.tool, arguments, Mode.EXECUTE)
        if isinstance(action, Outcome):
            return "failed"
        if action.risk not in UNATTENDED:
            # The level is read again from the prepared snapshot, not from the suggestion.
            self.engine.cancel(action)
            return "failed"
        key = step_key(action.tool, action.payload)
        if already_issued(self.workflows, key):
            self.engine.cancel(action)
            return "duplicate"
        try:
            # Written before the call, so a crash cannot make a done thing look undone.
            journal.issue(index, action.tool, key)
        except WorkflowFailure:
            self.engine.cancel(action)
            return "failed"
        self.active = action
        try:
            # No token is passed, and none exists: a routine is never given the authority.
            outcome = await self.engine.execute(action)
        finally:
            self.active = None
        with suppress(WorkflowFailure):
            journal.complete(key, outcome)
        return "done" if outcome.status is Status.SUCCESS else "failed"

    def _finish(self, run_id: str, phase: Literal["done", "failed"]) -> None:
        with suppress(WorkflowFailure):
            self.workflows.phase(run_id, phase)
