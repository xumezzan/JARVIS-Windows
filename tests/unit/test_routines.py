"""A routine observes, suggests and stays quiet; it never confirms anything by itself."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis.connectors.instants import render
from jarvis.core.routines.contracts import Call, Suggestion, reason_key
from jarvis.core.routines.proposals import SuggestionQueue
from jarvis.core.routines.runner import RoutineRunner
from jarvis.core.routines.state import RoutineState
from jarvis.core.routines.triggers import MailArrived, MeetingsAgenda, MeetingsSoon, builtin
from jarvis.core.workflow.store import WorkflowStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

NOW = 1_800_000_000.0


class Inputs(ToolModel):
    value: str = "data"


class Result(ToolModel):
    count: int


@dataclass
class Tools:
    calls: list[str] = field(default_factory=list)

    async def check(self, args: Inputs, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    def run_of(self, name: str):  # type: ignore[no-untyped-def]
        async def run(args: Inputs, context: ExecutionContext) -> Result:
            await context.checkpoint()
            self.calls.append(name)
            return Result(count=len(self.calls))

        return run

    async def verify(self, args: Inputs, result: Result, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for name, risk in (
            ("test.read", Risk.SAFE),
            ("test.write", Risk.ROUTINE),
            ("test.send", Risk.CONFIRM),
        ):
            registry.register(
                ToolSpec(
                    name,
                    "Локальная проверка рутин.",
                    risk,
                    Inputs,
                    Result,
                    self.check,
                    self.run_of(name),
                    self.verify,
                    timeout_seconds=2,
                )
            )
        return registry


class Fake:
    """A routine written by the test: fixed observations, fixed conclusions."""

    name = "test.routine"
    title = "Тестовая рутина"
    description = "Смотрит один SAFE-инструмент."
    every_seconds = 60

    def __init__(
        self,
        observations: tuple[str, ...] = ("test.read",),
        suggestions: tuple[Suggestion, ...] = (),
    ) -> None:
        self.observations = observations
        self.suggestions = suggestions
        self.seen: dict[str, str] = {}

    def observe(self, seen: dict[str, str], now: float) -> Call | None:
        for tool in self.observations:
            if tool not in seen:
                return Call(tool, {})
        return None

    def notice(self, seen: dict[str, str], now: float) -> tuple[Suggestion, ...]:
        self.seen = dict(seen)
        return self.suggestions


def suggestion(tool: str = "", key: str = "reason") -> Suggestion:
    return Suggestion(
        routine=Fake.name,
        key=reason_key(Fake.name, key),
        title="Есть повод",
        tool=tool,
        arguments=json.dumps({"value": "data"}) if tool else "{}",
    )


@dataclass
class Harness:
    runner: RoutineRunner
    tools: Tools
    state: RoutineState
    queue: SuggestionQueue
    workflows: WorkflowStore
    audit: AuditLog

    def switch_on(self, acts: bool = False) -> None:
        self.state.set_active(True)
        self.state.enable(Fake.name, True)
        self.state.allow_acting(Fake.name, acts)


def harness_for(tmp_path: Path, routine: object, *, budget: int = 48) -> Harness:
    tools = Tools()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    registry = tools.registry()
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    workflows = WorkflowStore(tmp_path / "workflows.sqlite3")
    state = RoutineState(tmp_path / "routines.json", budget=budget, clock=lambda: NOW)
    queue = SuggestionQueue(clock=lambda: NOW)
    runner = RoutineRunner(
        registry,
        engine,
        workflows,
        state,
        queue,
        [routine],  # type: ignore[list-item]
        clock=lambda: NOW,
    )
    return Harness(runner, tools, state, queue, workflows, audit)


@pytest.fixture
def quiet(tmp_path: Path) -> Iterator[Harness]:
    harness = harness_for(tmp_path, Fake())
    yield harness
    harness.audit.close()


def approvals(harness: Harness) -> list[str]:
    records = [json.loads(record) for record in harness.audit.recent(1000)]
    return [record["actor"] for record in records if record["event"] == "approved"]


@pytest.mark.asyncio
async def test_nothing_runs_until_the_owner_turns_it_on(quiet: Harness) -> None:
    assert [cycle.status for cycle in await quiet.runner.tick()] == ["off"]
    quiet.state.enable(Fake.name, True)  # One switch is not enough: the master is still off.
    assert [cycle.status for cycle in await quiet.runner.tick()] == ["off"]
    assert not quiet.tools.calls and not quiet.workflows.recent()


@pytest.mark.asyncio
async def test_a_look_that_finds_nothing_says_nothing(quiet: Harness) -> None:
    quiet.switch_on()
    (cycle,) = await quiet.runner.tick()
    assert cycle.status == "quiet" and cycle.suggested == 0
    assert quiet.tools.calls == ["test.read"] and not quiet.queue.pending()
    # It is still an ordinary run: journalled, finished, and visible afterwards.
    (record,) = quiet.workflows.recent()
    assert record.request == "routine test.routine" and record.phase == "done"


@pytest.mark.asyncio
async def test_the_schedule_holds_between_cycles(quiet: Harness) -> None:
    quiet.switch_on()
    assert (await quiet.runner.tick())[0].status == "quiet"
    assert (await quiet.runner.tick())[0].status == "not_due"
    assert quiet.tools.calls == ["test.read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("acts", [True, False])
async def test_a_routine_never_carries_out_what_needs_confirming(
    tmp_path: Path, acts: bool
) -> None:
    harness = harness_for(tmp_path, Fake(suggestions=(suggestion("test.send"),)))
    try:
        harness.switch_on(acts=acts)
        (cycle,) = await harness.runner.tick()
        assert cycle.status == "suggested" and cycle.acted == 0
        assert [item.tool for item in harness.queue.pending()] == ["test.send"]
        # Nothing was sent, and no approval was issued for it under any setting.
        assert harness.tools.calls == ["test.read"] and not approvals(harness)
    finally:
        harness.audit.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("acts", [True, False])
async def test_reversible_work_waits_for_its_own_permission(tmp_path: Path, acts: bool) -> None:
    harness = harness_for(tmp_path, Fake(suggestions=(suggestion("test.write"),)))
    try:
        harness.switch_on(acts=acts)
        (cycle,) = await harness.runner.tick()
        if acts:
            assert cycle.status == "acted" and cycle.acted == 1
            assert harness.tools.calls == ["test.read", "test.write"]
            assert not harness.queue.pending()
        else:
            assert cycle.status == "suggested" and cycle.suggested == 1
            assert harness.tools.calls == ["test.read"]
            assert [item.tool for item in harness.queue.pending()] == ["test.write"]
    finally:
        harness.audit.close()


@pytest.mark.asyncio
async def test_an_effect_already_issued_is_not_repeated_after_a_restart(tmp_path: Path) -> None:
    first = harness_for(tmp_path, Fake(suggestions=(suggestion("test.write"),)))
    try:
        first.switch_on(acts=True)
        assert (await first.runner.tick())[0].acted == 1
    finally:
        first.audit.close()
    # A new process: new state, new queue, new engine — the same durable journal.
    second = harness_for(tmp_path, Fake(suggestions=(suggestion("test.write"),)))
    try:
        second.switch_on(acts=True)
        second.state.run_soon(Fake.name)
        (cycle,) = await second.runner.tick()
        assert cycle.status == "quiet" and cycle.acted == 0
        assert second.tools.calls == ["test.read"] and not second.queue.pending()
    finally:
        second.audit.close()


@pytest.mark.asyncio
async def test_the_day_has_a_budget_and_it_survives_a_restart(tmp_path: Path) -> None:
    harness = harness_for(tmp_path, Fake(), budget=1)
    try:
        harness.switch_on()
        assert (await harness.runner.tick())[0].status == "quiet"
        # The schedule is not what stops the next one: the day's count is.
        harness.state.run_soon(Fake.name)
        (cycle,) = await harness.runner.tick()
        assert cycle.status == "budget" and harness.tools.calls == ["test.read"]
        restarted = RoutineState(tmp_path / "routines.json", budget=1, clock=lambda: NOW)
        assert restarted.exhausted and restarted.spent == 1
    finally:
        harness.audit.close()


@pytest.mark.asyncio
async def test_a_reason_that_is_gone_stops_waiting(tmp_path: Path) -> None:
    routine = Fake(suggestions=(suggestion(),))
    harness = harness_for(tmp_path, routine)
    try:
        harness.switch_on()
        assert (await harness.runner.tick())[0].suggested == 1
        routine.suggestions = ()
        harness.state.run_soon(Fake.name)
        (cycle,) = await harness.runner.tick()
        assert cycle.status == "quiet" and not harness.queue.pending()
    finally:
        harness.audit.close()


@pytest.mark.asyncio
async def test_an_observation_that_changes_something_is_refused(tmp_path: Path) -> None:
    harness = harness_for(tmp_path, Fake(observations=("test.write",)))
    try:
        harness.switch_on(acts=True)
        (cycle,) = await harness.runner.tick()
        # Looking is reading: a routine cannot smuggle a write in as an observation.
        assert cycle.status == "unavailable" and not harness.tools.calls
        (record,) = harness.workflows.recent()
        assert record.phase == "failed"
    finally:
        harness.audit.close()


@pytest.mark.asyncio
async def test_stopping_everything_stops_the_next_cycle(quiet: Harness) -> None:
    quiet.switch_on()
    quiet.runner.cancel()
    assert [cycle.status for cycle in await quiet.runner.tick()] == []
    quiet.state.set_active(False)
    assert not quiet.tools.calls


def test_the_queue_forgets_what_nobody_opened() -> None:
    clock = [0.0]
    queue = SuggestionQueue(ttl=100, clock=lambda: clock[0])
    assert queue.add(suggestion()) and not queue.add(suggestion())
    assert len(queue.pending()) == 1
    clock[0] = 101
    assert not queue.pending() and queue.get(suggestion().key) is None


def test_the_queue_is_bounded_and_dismissable() -> None:
    queue = SuggestionQueue(limit=2, clock=lambda: 0.0)
    for index in range(3):
        queue.add(suggestion(key=f"reason-{index}"))
    assert len(queue.pending()) == 2
    queue.dismiss(queue.pending()[0].key)
    assert len(queue.pending()) == 1
    queue.clear()
    assert not queue.pending()


def test_switches_are_off_by_default_and_survive_a_restart(tmp_path: Path) -> None:
    state = RoutineState(tmp_path / "routines.json", clock=lambda: NOW)
    assert not state.active and not state.settings("meetings.soon").enabled
    assert not state.settings("meetings.soon").acts
    state.set_active(True)
    state.enable("meetings.soon", True)
    state.allow_acting("meetings.soon", True)
    restored = RoutineState(tmp_path / "routines.json", clock=lambda: NOW)
    assert restored.active and restored.settings("meetings.soon") == state.settings("meetings.soon")


def test_an_unreadable_file_leaves_everything_off(tmp_path: Path) -> None:
    path = tmp_path / "routines.json"
    path.write_text("{ not json", encoding="utf-8")
    state = RoutineState(path, clock=lambda: NOW)
    assert not state.active and not state.settings("meetings.soon").enabled


def event(subject: str, minutes: float, **extra: object) -> dict[str, object]:
    moment = datetime.fromtimestamp(NOW + minutes * 60, UTC)
    return {
        "id": "event-" + subject,
        "subject": subject,
        "start": render(moment),
        "end": render(moment),
        "organizer": "chief@example.com",
        "attendees": [],
        "location": "",
        "cancelled": False,
        **extra,
    }


def observed(tool: str, rows: list[dict[str, object]]) -> dict[str, str]:
    account = {
        "service": "outlook",
        "user_id": "u1",
        "address": "o@example.com",
        "session": "a" * 32,
    }
    return {
        "outlook.account": json.dumps({"state": "account", "account": account}),
        tool: json.dumps(
            {"state": "listed", "account": account, "data": json.dumps(rows, ensure_ascii=False)}
        ),
    }


def test_a_routine_asks_the_account_first_and_then_uses_exactly_it() -> None:
    routine = MeetingsSoon()
    first = routine.observe({}, NOW)
    assert first is not None and first.tool == "outlook.account" and not first.arguments
    seen = observed("calendar.list", [])
    second = routine.observe({"outlook.account": seen["outlook.account"]}, NOW)
    assert second is not None and second.tool == "calendar.list"
    assert second.arguments["account"] == json.loads(seen["outlook.account"])["account"]
    assert routine.observe(seen, NOW) is None


def test_a_meeting_is_worth_saying_only_while_it_is_still_ahead() -> None:
    seen = observed(
        "calendar.list",
        [
            event("Планёрка", 10),
            event("Ретро", 40),
            event("Отменённая", 5, cancelled=True),
            event("Прошедшая", -5),
        ],
    )
    titles = [item.title for item in MeetingsSoon().notice(seen, NOW)]
    assert titles == ["«Планёрка» начинается через 10 мин"]


def test_an_unchanged_agenda_is_not_a_new_reason(tmp_path: Path) -> None:
    routine = MeetingsAgenda(tmp_path)
    seen = observed("calendar.list", [event("Планёрка", 30), event("Ретро", 120)])
    (first,) = routine.notice(seen, NOW)
    (again,) = routine.notice(seen, NOW + 60)
    assert first.key == again.key and first.tool == "files.write_text"
    arguments = json.loads(first.arguments)
    assert arguments["path"] == str(tmp_path / "Встречи.txt") and arguments["overwrite"] is True
    assert "Планёрка" in arguments["text"] and "Ретро" in arguments["text"]
    # A changed day is a different reason, so it is offered again.
    (changed,) = routine.notice(observed("calendar.list", [event("Планёрка", 30)]), NOW)
    assert changed.key != first.key


def test_an_empty_calendar_writes_nothing(tmp_path: Path) -> None:
    assert MeetingsAgenda(tmp_path).notice(observed("calendar.list", []), NOW) == ()


def test_only_mail_that_just_arrived_is_mentioned() -> None:
    def letter(name: str, minutes: float, **extra: object) -> dict[str, object]:
        moment = datetime.fromtimestamp(NOW - minutes * 60, UTC)
        return {
            "id": "mail-" + name,
            "subject": name,
            "from": {"emailAddress": {"name": "Иван", "address": "ivan@example.com"}},
            "receivedDateTime": render(moment),
            "isDraft": False,
            **extra,
        }

    seen = observed(
        "outlook.list",
        [letter("Смета", 5), letter("Старое", 120), letter("Черновик", 2, isDraft=True)],
    )
    found = MailArrived().notice(seen, NOW)
    assert [item.title for item in found] == ["Письмо: «Смета»"]
    assert "Иван" in found[0].detail and not found[0].actionable


def test_the_installation_offers_what_it_can_actually_do(tmp_path: Path) -> None:
    assert [routine.name for routine in builtin(None)] == ["meetings.soon", "mail.recent"]
    assert [routine.name for routine in builtin(tmp_path)] == [
        "meetings.soon",
        "meetings.agenda",
        "mail.recent",
    ]


def test_a_suggestion_holds_a_sentence_and_exact_arguments() -> None:
    with pytest.raises(ValueError):
        Suggestion(routine="r", key="k", title="x" * 200)
    with pytest.raises(ValueError):
        Suggestion(routine="r", key="k", title="Повод", arguments="{oops}")
    with pytest.raises(ValueError):
        # Arguments without a tool would be an action nobody can name.
        Suggestion(routine="r", key="k", title="Повод", arguments='{"path": "x"}')
