"""A task that outlives one window: long bounds, resumption, waiting states and budget.

Nothing here touches a network, an account or a real service. The effects are messages in
the process-local outbox, which is exactly what makes duplication visible: one request must
leave one message behind, however many times the task is interrupted and started again.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis.core.planner.contracts import Budget, Limits, Proposal, Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.resume import restore
from jarvis.core.planner.runner import Runner
from jarvis.core.workflow.journal import RunJournal
from jarvis.core.workflow.models import RunRecord, step_key
from jarvis.core.workflow.store import WorkflowStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.local import local_registry

LOOK = call("local.check", {"delay_ms": 0})


def message(index: int) -> Proposal:
    """Each effect is its own: the same body twice would be the same key twice."""
    return call("local.append_message", {"recipient": "test", "body": f"шаг {index}"})


class Scripted:
    def __init__(self, proposals: list[Proposal]) -> None:
        self.proposals = list(proposals)
        self.asked = 0

    async def propose(self, data: object) -> Proposal:
        self.asked += 1
        return self.proposals.pop(0) if self.proposals else Proposal(kind="finish")


class Bench:
    def __init__(self, tmp_path: Path) -> None:
        self.registry, self.outbox = local_registry()
        self.audit = AuditLog(tmp_path / "audit.sqlite3")
        approvals = ApprovalStore()
        self.engine = PermissionEngine(self.registry, approvals, self.audit)
        self.authority = approvals.take_authority(self.audit.approved)
        self.store = WorkflowStore(tmp_path / "workflows.sqlite3")
        self.seen: list[str] = []

    async def approve(self, action: Action) -> ApprovalToken:
        # What the record says while the owner is being asked is the point of the phase.
        self.seen.append(self.phase())
        return self.authority.approve(action)

    async def clarify(self, question: str) -> str:
        self.seen.append(self.phase())
        return "уточнение владельца"

    def start(self, request: str = "длинная задача") -> RunJournal:
        self.run = self.store.start(request, Mode.EXECUTE)
        return RunJournal(self.store, self.run.id)

    def key(self, proposal: Proposal) -> str:
        """The key the engine itself would derive for this call, not a guess at one."""
        action = self.engine.prepare(proposal.tool, json.loads(proposal.arguments))
        assert isinstance(action, Action)
        self.engine.cancel(action)
        return step_key(action.tool, action.payload)

    async def crash(self, journal: RunJournal, proposal: Proposal, index: int = 0) -> None:
        """Leave behind what a killed process leaves: the effect done, the run still open.

        The steps are the runner's own — journal before the call, execute, journal the
        outcome — stopping short of the closing note the runner would have written.
        """
        action = self.engine.prepare(proposal.tool, json.loads(proposal.arguments), Mode.EXECUTE)
        assert isinstance(action, Action)
        key = step_key(action.tool, action.payload)
        journal.issue(index, action.tool, key)
        journal.complete(key, await self.engine.execute(action, self.authority.approve(action)))

    def record(self) -> RunRecord:
        found = self.store.get(self.run.id)
        assert found is not None
        return found

    def phase(self) -> str:
        return self.record().phase

    def runner(
        self,
        proposals: list[Proposal],
        journal: RunJournal | None,
        *,
        limits: Limits | None = None,
        resumed: tuple[Step, ...] = (),
    ) -> Runner:
        return Runner(
            self.registry,
            self.engine,
            Scripted(proposals),
            self.approve,
            self.clarify,
            limits=limits or Limits.long(),
            journal=journal,
            resumed=resumed,
        )


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    workbench = Bench(tmp_path)
    yield workbench
    workbench.engine.cancel_all()
    workbench.audit.close()


@pytest.mark.asyncio
async def test_long_bounds_are_refused_without_the_journal_that_carries_them(
    bench: Bench,
) -> None:
    result = await bench.runner([message(1)], None).run("test", Mode.EXECUTE)
    # The refusal comes before anything is proposed, so the world is untouched.
    assert result.status == "error" and result.error == "journal_required"
    assert bench.outbox.count == 0


def test_the_short_bounds_stay_available_without_a_journal() -> None:
    assert not Limits().durable and Limits().max_steps == 8
    with pytest.raises(ValueError):
        Limits(max_steps=64)
    with pytest.raises(ValueError):
        Limits(total_seconds=3600)
    assert Limits.long().max_steps == 64 and Limits.long().total_seconds == 3600


@pytest.mark.asyncio
async def test_a_thirty_step_task_reaches_its_end(bench: Bench) -> None:
    journal = bench.start()
    proposals: list[Proposal] = []
    for index in range(15):
        proposals.extend((LOOK, message(index)))
    result = await bench.runner(proposals, journal).run("длинная задача", Mode.EXECUTE)
    assert result.status == "finished"
    assert len(result.steps) == 30
    # Reads leave nothing behind, so the journal holds the fifteen effects and no more.
    assert len(bench.record().steps) == 15
    assert bench.outbox.count == 15
    assert bench.phase() == "done"


@pytest.mark.asyncio
async def test_a_restart_repeats_nothing_and_continues_where_it_stopped(bench: Bench) -> None:
    journal = bench.start()
    await bench.crash(journal, message(1))
    # The run is still open, which is exactly how an interrupted task is recognised later.
    assert bench.phase() == "running" and bench.record() in bench.store.unfinished()
    assert bench.outbox.count == 1

    resumed = restore(bench.record())
    assert len(resumed) == 1 and resumed[0].outcome.result_json is None
    result = await bench.runner(
        [message(2)], RunJournal(bench.store, bench.run.id), resumed=resumed
    ).run("длинная задача", Mode.EXECUTE)
    assert result.status == "finished"
    # The earlier step is reported with the new one rather than silently dropped, and only
    # the new one reached the world.
    assert [step.tool for step in result.steps] == ["local.append_message"] * 2
    assert bench.outbox.count == 2
    assert bench.phase() == "done"


@pytest.mark.asyncio
async def test_a_run_the_owner_already_closed_is_not_reopened(bench: Bench) -> None:
    journal = bench.start()
    first = await bench.runner([message(1)], journal).run("длинная задача", Mode.EXECUTE)
    assert first.status == "finished" and bench.phase() == "done"
    resumed = restore(bench.record())
    again = await bench.runner(
        [message(2)], RunJournal(bench.store, bench.run.id), resumed=resumed
    ).run("длинная задача", Mode.EXECUTE)
    # A finished run is history. Refusing to write into it is what keeps its journal true,
    # and the refusal lands before anything is executed.
    assert again.status == "error" and again.error == "journal_unavailable"
    assert bench.outbox.count == 1


@pytest.mark.asyncio
async def test_a_step_whose_outcome_never_arrived_is_not_tried_again(bench: Bench) -> None:
    journal = bench.start()
    proposal = message(7)
    journal.issue(0, "local.append_message", bench.key(proposal))
    record = bench.record()
    assert record.steps[0].unresolved
    resumed = restore(record)
    # Possibly done is not done, and it is certainly not nothing: the resumed task sees a
    # timeout that may have had effects, and the guard refuses the same key outright.
    assert resumed[0].outcome.status is Status.TIMEOUT
    assert resumed[0].outcome.may_have_effects
    result = await bench.runner([proposal], journal, resumed=resumed).run("тест", Mode.EXECUTE)
    assert result.status == "error" and result.error == "duplicate_effect"
    assert bench.outbox.count == 0


@pytest.mark.asyncio
async def test_the_budget_stops_the_task_and_reports_it_as_itself(bench: Bench) -> None:
    journal = bench.start()
    spent: list[object] = []
    runner = bench.runner(
        [LOOK, LOOK, message(1)],
        journal,
        limits=Limits.long(Budget(max_calls=2)),
    )
    runner.notify = lambda kind, value: spent.append(value) if kind == "budget" else None
    result = await runner.run("длинная задача", Mode.EXECUTE)
    assert result.status == "budget"
    # Two answers were paid for and used; the third was never asked for.
    assert len(result.steps) == 2 and runner.calls == 2
    assert spent == [(1, 2), (2, 2)]
    assert bench.outbox.count == 0
    # An exhausted budget did not finish the work, and the record says so.
    assert bench.phase() == "failed"
    assert "бюджет" in result.summary.casefold()


@pytest.mark.asyncio
async def test_a_waiting_run_says_which_of_the_two_waits_it_is_in(bench: Bench) -> None:
    journal = bench.start()
    result = await bench.runner(
        [Proposal(kind="clarify", question="какой адрес?"), message(1)], journal
    ).run("длинная задача", Mode.EXECUTE)
    assert result.status == "finished"
    assert bench.seen == ["waiting_input", "waiting_approval"]
    assert bench.phase() == "done"


@pytest.mark.asyncio
async def test_a_cancelled_run_is_closed_as_cancelled(bench: Bench) -> None:
    journal = bench.start()
    runner = bench.runner([LOOK, message(1)], journal)

    def stop(kind: str, value: object) -> None:
        if kind == "tool":
            runner.cancel()

    runner.notify = stop
    result = await runner.run("длинная задача", Mode.EXECUTE)
    assert result.status == "cancelled"
    # Cancellation stops the next step; it does not pretend the first one did not happen.
    assert len(result.steps) == 1
    assert bench.outbox.count == 0
    assert bench.phase() == "cancelled"
