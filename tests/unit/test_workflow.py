"""Durable runs, idempotency and recovery. No network, account or real service is used."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import Proposal
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.core.workflow.journal import RunJournal
from jarvis.core.workflow.models import RunRecord, StepRecord, step_key
from jarvis.core.workflow.recovery import decide
from jarvis.core.workflow.store import WorkflowFailure, WorkflowStore
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.local import local_registry

KEY = step_key("local.append_message", '{"body":"x"}')
MESSAGE = {"recipient": "test", "body": "PRIVATE_COMMAND_SENTINEL"}


def store(tmp_path: Path, now: float = 1_700_000_000) -> WorkflowStore:
    return WorkflowStore(tmp_path / "workflows.sqlite3", clock=lambda: now)


def issued(index: int = 0, key: str = KEY, tool: str = "local.append_message") -> StepRecord:
    return StepRecord(index=index, tool=tool, key=key, state="issued", updated=1)


def finished(
    status: Status = Status.SUCCESS,
    error: ErrorCode = ErrorCode.NONE,
    effects: bool = True,
    key: str = KEY,
) -> StepRecord:
    return StepRecord(
        index=0,
        tool="local.append_message",
        key=key,
        state="finished",
        status=status.value,
        error=error.value,
        may_have_effects=effects,
        updated=2,
    )


def record(*steps: StepRecord) -> RunRecord:
    return RunRecord(
        id="a" * 32,
        request="test",
        mode=Mode.EXECUTE.value,
        phase="running",
        steps=steps,
        created=1,
        updated=2,
    )


def test_a_key_identifies_the_effect_not_the_plan_around_it() -> None:
    assert step_key("asana.create_task", '{"name":"a"}') == step_key(
        "asana.create_task", '{"name":"a"}'
    )
    assert step_key("asana.create_task", '{"name":"a"}') != step_key(
        "asana.create_task", '{"name":"b"}'
    )
    assert step_key("asana.create_task", "{}") != step_key("notion.create_page", "{}")


def test_a_step_carries_an_outcome_only_once_it_has_one() -> None:
    assert issued().unresolved and not issued().succeeded
    with pytest.raises(ValueError):
        StepRecord(
            index=0, tool="t.t", key=KEY, state="issued", status=Status.SUCCESS.value, updated=1
        )
    with pytest.raises(ValueError):
        StepRecord(index=0, tool="t.t", key=KEY, state="finished", updated=1)
    with pytest.raises(ValueError):
        StepRecord(
            index=0, tool="t.t", key=KEY, state="finished", status="MAYBE", error="none", updated=1
        )


def test_only_a_step_that_may_have_left_something_behind_blocks_a_retry() -> None:
    # Still in flight: a timeout is not proof that nothing happened.
    assert record(issued()).issued(KEY) is not None
    assert record(finished()).issued(KEY) is not None
    assert record(finished(Status.ERROR, ErrorCode.EXECUTION, effects=True)).issued(KEY) is not None
    # Refused before anything was attempted: nothing to protect.
    assert record(finished(Status.DENIED, ErrorCode.POLICY, effects=False)).issued(KEY) is None
    assert (
        record(finished(Status.INVALID, ErrorCode.INVALID_REQUEST, effects=False)).issued(KEY)
        is None
    )
    assert record().issued(KEY) is None


def test_a_run_is_written_read_and_resolved(tmp_path: Path) -> None:
    journal = store(tmp_path)
    run = journal.start("разбери встречи", Mode.EXECUTE)
    assert run.phase == "running" and not run.finished
    journal.append(run.id, issued())
    assert journal.get(run.id) is not None
    updated = journal.complete(run.id, KEY, Status.SUCCESS, ErrorCode.NONE, True)
    assert updated.steps[0].succeeded and not updated.steps[0].unresolved
    assert updated.issued(KEY) is not None
    done = journal.phase(run.id, "done")
    assert done.finished


def test_a_finished_run_is_history(tmp_path: Path) -> None:
    journal = store(tmp_path)
    run = journal.start("test", Mode.EXECUTE)
    journal.phase(run.id, "done")
    actions: tuple[Callable[[], object], ...] = (
        lambda: journal.append(run.id, issued()),
        lambda: journal.phase(run.id, "running"),
        lambda: journal.complete(run.id, KEY, Status.SUCCESS, ErrorCode.NONE, True),
    )
    for action in actions:
        with pytest.raises(WorkflowFailure) as error:
            action()
        assert error.value.code == "finished"


def test_resolving_needs_a_step_that_was_actually_issued(tmp_path: Path) -> None:
    journal = store(tmp_path)
    run = journal.start("test", Mode.EXECUTE)
    with pytest.raises(WorkflowFailure) as error:
        journal.complete(run.id, KEY, Status.SUCCESS, ErrorCode.NONE, True)
    assert error.value.code == "unknown"
    journal.append(run.id, issued())
    journal.complete(run.id, KEY, Status.SUCCESS, ErrorCode.NONE, True)
    # The same key resolves once; a second call has nothing left in flight.
    with pytest.raises(WorkflowFailure):
        journal.complete(run.id, KEY, Status.SUCCESS, ErrorCode.NONE, True)


def test_unfinished_runs_survive_a_reopen(tmp_path: Path) -> None:
    first = store(tmp_path).start("одна", Mode.EXECUTE)
    second = store(tmp_path).start("вторая", Mode.SIMULATION)
    store(tmp_path).phase(second.id, "done")
    reopened = store(tmp_path)
    assert [run.id for run in reopened.unfinished()] == [first.id]
    assert len(reopened.recent()) == 2
    reopened.forget(first.id)
    assert reopened.unfinished() == ()
    reopened.clear()
    assert reopened.recent() == ()


def test_the_journal_makes_room_rather_than_refusing_todays_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.core.workflow.store.MAX_RUNS", 2)
    journal = store(tmp_path)
    oldest = journal.start("одна", Mode.EXECUTE)
    journal.start("вторая", Mode.EXECUTE)
    journal.start("третья", Mode.EXECUTE)
    assert journal.get(oldest.id) is None
    assert len(journal.recent()) == 2


def test_a_tampered_row_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    journal = store(tmp_path)
    run = journal.start("test", Mode.EXECUTE)
    with closing(sqlite3.connect(journal.path)) as db:
        db.execute("UPDATE runs SET payload=? WHERE id=?", ("{}", run.id))
        db.commit()
    with pytest.raises(WorkflowFailure) as error:
        journal.get(run.id)
    assert error.value.code == "invalid"


def test_cancellation_stops_before_writing(tmp_path: Path) -> None:
    journal = store(tmp_path)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(WorkflowFailure) as error:
        journal.start("test", Mode.EXECUTE, cancelled)
    assert error.value.code == "cancelled"


def test_an_unreadable_journal_answers_that_the_effect_may_exist(tmp_path: Path) -> None:
    # The path is a directory: nothing can be read, and guessing "not done" would risk a double.
    unusable = RunJournal(WorkflowStore(tmp_path), "b" * 32)
    assert unusable.issued(KEY) is True
    missing = RunJournal(store(tmp_path), "b" * 32)
    assert missing.issued(KEY) is False


@pytest.mark.parametrize(
    "error,idempotent,effects,attempts,expected",
    [
        (ErrorCode.TIMEOUT, True, False, 1, "retry"),
        (ErrorCode.BROWSER_TIMEOUT, True, False, 1, "retry"),
        (ErrorCode.TIMEOUT, True, False, 2, "stop"),
        # A timeout on something that may have landed is never repeated on its own.
        (ErrorCode.TIMEOUT, True, True, 1, "stop"),
        (ErrorCode.TIMEOUT, False, False, 1, "stop"),
        (ErrorCode.APPROVAL, False, True, 1, "ask"),
        (ErrorCode.APPLICATION_MISSING, True, False, 1, "ask"),
        (ErrorCode.PATH_DENIED, True, False, 1, "ask"),
        (ErrorCode.POLICY, True, False, 1, "stop"),
        (ErrorCode.VERIFICATION, True, False, 1, "stop"),
        (ErrorCode.TARGET_CHANGED, True, False, 1, "stop"),
    ],
)
def test_recovery_never_repeats_what_may_already_have_happened(
    error: ErrorCode, idempotent: bool, effects: bool, attempts: int, expected: str
) -> None:
    assert (
        decide(error, idempotent=idempotent, may_have_effects=effects, attempts=attempts)
        == expected
    )


def test_a_successful_step_is_not_a_recovery_decision() -> None:
    with pytest.raises(ValueError):
        decide(ErrorCode.NONE, idempotent=True, may_have_effects=False, attempts=1)


class Scripted:
    def __init__(self, proposals: list[Proposal]) -> None:
        self.proposals = list(proposals)

    async def propose(self, data: object) -> Proposal:
        return self.proposals.pop(0) if self.proposals else Proposal(kind="finish")


class Bench:
    def __init__(self, tmp_path: Path) -> None:
        self.registry, self.outbox = local_registry()
        self.audit = AuditLog(tmp_path / "audit.sqlite3")
        approvals = ApprovalStore()
        self.engine = PermissionEngine(self.registry, approvals, self.audit)
        self.authority = approvals.take_authority(self.audit.approved)

    async def approve(self, action: Action) -> ApprovalToken:
        return self.authority.approve(action)

    async def clarify(self, question: str) -> str:
        return "проверь систему"

    def runner(self, proposals: list[Proposal], journal: RunJournal) -> Runner:
        return Runner(
            self.registry,
            self.engine,
            Scripted(proposals),
            self.approve,
            self.clarify,
            journal=journal,
        )


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    workbench = Bench(tmp_path)
    yield workbench
    workbench.engine.cancel_all()
    workbench.audit.close()


@pytest.mark.asyncio
async def test_an_effect_is_journalled_before_it_is_attempted(bench: Bench, tmp_path: Path) -> None:
    runs = store(tmp_path)
    run = runs.start("test", Mode.EXECUTE)
    proposals = [call("local.check", {}), call("local.append_message", MESSAGE)]
    result = await bench.runner(proposals, RunJournal(runs, run.id)).run("test", Mode.EXECUTE)
    assert result.status == "finished"
    steps = runs.get(run.id).steps  # type: ignore[union-attr]
    # The read left nothing behind, so only the effect is in the journal.
    assert [step.tool for step in steps] == ["local.append_message"]
    assert steps[0].succeeded and not steps[0].unresolved


@pytest.mark.asyncio
async def test_a_second_attempt_refuses_to_repeat_the_same_effect(
    bench: Bench, tmp_path: Path
) -> None:
    runs = store(tmp_path)
    run = runs.start("test", Mode.EXECUTE)
    first = await bench.runner(
        [call("local.append_message", MESSAGE)], RunJournal(runs, run.id)
    ).run("test", Mode.EXECUTE)
    assert first.status == "finished" and bench.outbox.count == 1
    second = await bench.runner(
        [call("local.append_message", MESSAGE)], RunJournal(runs, run.id)
    ).run("test", Mode.EXECUTE)
    assert second.status == "error" and second.error == "duplicate_effect"
    # The point of the whole mechanism: one request, one message.
    assert bench.outbox.count == 1


@pytest.mark.asyncio
async def test_an_unresolved_effect_still_blocks_the_next_attempt(
    bench: Bench, tmp_path: Path
) -> None:
    runs = store(tmp_path)
    run = runs.start("test", Mode.EXECUTE)
    payload = bench.registry.get("local.append_message").normalize(MESSAGE)  # type: ignore[union-attr]
    runs.append(run.id, issued(key=step_key("local.append_message", payload)))
    result = await bench.runner(
        [call("local.append_message", MESSAGE)], RunJournal(runs, run.id)
    ).run("test", Mode.EXECUTE)
    assert result.status == "error" and result.error == "duplicate_effect"
    assert bench.outbox.count == 0


@pytest.mark.asyncio
async def test_an_unusable_journal_stops_the_run_instead_of_acting_unrecorded(
    bench: Bench, tmp_path: Path
) -> None:
    runs = store(tmp_path)
    missing = RunJournal(runs, "c" * 32)
    result = await bench.runner([call("local.append_message", MESSAGE)], missing).run(
        "test", Mode.EXECUTE
    )
    assert result.status == "error" and result.error == "journal_unavailable"
    assert bench.outbox.count == 0


@pytest.mark.asyncio
async def test_a_run_without_a_journal_keeps_its_previous_behaviour(bench: Bench) -> None:
    runner = Runner(
        bench.registry,
        bench.engine,
        Scripted([call("local.append_message", MESSAGE)]),
        bench.approve,
        bench.clarify,
    )
    result = await runner.run("test", Mode.EXECUTE)
    assert result.status == "finished" and bench.outbox.count == 1


def test_outcome_values_reach_the_journal_unchanged(tmp_path: Path) -> None:
    runs = store(tmp_path)
    run = runs.start("test", Mode.EXECUTE)
    runs.append(run.id, issued())
    journal = RunJournal(runs, run.id)
    journal.complete(
        KEY,
        Outcome(
            request_id=uuid4(),
            status=Status.TIMEOUT,
            error=ErrorCode.TIMEOUT,
            may_have_effects=True,
        ),
    )
    step = runs.get(run.id).steps[0]  # type: ignore[union-attr]
    assert step.status == Status.TIMEOUT.value and step.may_have_effects
