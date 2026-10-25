"""Memory learned from finished runs: what it keeps, what it refuses, and what it sends."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from tests.unit.test_planner import Scripted

from jarvis.core.context.assembly import Budget, assemble
from jarvis.core.context.learning import MAX_MEANINGS, MAX_PHRASES, MemoryLearner, meanings
from jarvis.core.planner.contracts import Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.knowledge.models import EntityDraft
from jarvis.knowledge.resolution import confirm, resolve
from jarvis.knowledge.store import KnowledgeStore, reference
from jarvis.memory.derived import (
    DERIVED_TTL,
    MAX_CONFIRMED,
    MAX_DERIVED,
    Derived,
    DerivedStore,
)
from jarvis.memory.derived import context as learned
from jarvis.memory.models import Hint, MemoryContext
from jarvis.memory.store import MemoryFailure
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.local import LocalOutbox, local_registry

NOW = 1_700_000_000.0
RUN = "b" * 32


def store(tmp_path: Path, now: float = NOW) -> DerivedStore:
    return DerivedStore(tmp_path / "derived.sqlite3", clock=lambda: now)


def done(tool: str, payload: str = "{}", status: Status = Status.SUCCESS) -> Step:
    return Step(tool, Outcome(uuid4(), status, ErrorCode.NONE), payload)


@dataclass
class Harness:
    registry: object
    outbox: LocalOutbox
    engine: PermissionEngine
    authority: object
    audit: AuditLog

    async def approve(self, action: Action) -> ApprovalToken:
        authority = self.authority
        assert hasattr(authority, "approve")
        token: ApprovalToken = authority.approve(action)
        return token

    async def clarify(self, question: str) -> str:
        return "проверь систему"


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    registry, outbox = local_registry()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    approvals = ApprovalStore()
    engine = PermissionEngine(registry, approvals, audit)
    yield Harness(registry, outbox, engine, approvals.take_authority(audit.approved), audit)
    engine.cancel_all()
    audit.close()


def test_a_mapping_is_strengthened_by_agreement_and_replaced_by_disagreement(
    tmp_path: Path,
) -> None:
    learnt = store(tmp_path)
    assert learnt.record("tool", "почту", "outlook.list").runs == 1
    assert learnt.record("tool", "Почту", "outlook.list").runs == 2
    # One word means one thing: a run that meant something else starts the count again.
    changed = learnt.record("tool", "почту", "calendar.list")
    assert changed.runs == 1 and changed.target == "calendar.list"
    assert len(learnt.read()) == 1


def test_a_phrase_is_an_ordinary_word_and_a_meaning_matches_its_kind(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    for phrase in ("пароль от почты", "token", "почта письмо", "ab"):
        with pytest.raises(MemoryFailure) as refusal:
            learnt.record("tool", phrase, "outlook.list")
        assert refusal.value.code == "invalid"
    for kind, target in (("tool", "not a tool"), ("entity", "john"), ("application", "C:\\x.exe")):
        with pytest.raises(MemoryFailure):
            learnt.record(kind, "джон", target)  # type: ignore[arg-type]
    # A confirmed name is a name, so it may hold a space; a learned word may not.
    assert learnt.record("entity", "Иван Петров", "a" * 32).phrase == "иван петров"


def test_learned_memory_expires_on_its_own(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.record("tool", "почту", "outlook.list")
    assert len(learnt.read()) == 1
    later = DerivedStore(tmp_path / "derived.sqlite3", clock=lambda: NOW + DERIVED_TTL + 1)
    assert later.read() == ()


def test_the_weakest_guess_makes_room_but_a_confirmed_name_never_does(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.record("entity", "джон", "a" * 32)
    learnt.record("tool", "слабое", "local.check")
    for index in range(MAX_DERIVED - 2):
        learnt.record("tool", f"слово{index}", "local.check")
        learnt.record("tool", f"слово{index}", "local.check")
    learnt.record("tool", "новое", "outlook.list")
    records = learnt.read()
    assert len(records) == MAX_DERIVED
    assert "слабое" not in {record.phrase for record in records}
    assert any(record.kind == "entity" for record in records)


def test_confirmed_names_have_a_limit_of_their_own(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    for index in range(MAX_CONFIRMED):
        learnt.record("entity", f"имя{index}", f"{index:032x}")
    with pytest.raises(MemoryFailure) as refusal:
        learnt.record("entity", "ещё одно", "f" * 32)
    assert refusal.value.code == "limit"


def test_reading_a_store_nobody_wrote_creates_nothing(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    assert learnt.read() == () and not learnt.learning()
    assert not (tmp_path / "derived.sqlite3").exists()


def test_the_owner_deletes_one_row_or_all_of_them(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    first = learnt.record("tool", "почту", "outlook.list")
    learnt.record("tool", "встречи", "calendar.list")
    assert {record.phrase for record in learnt.forget(first)} == {"встречи"}
    with pytest.raises(MemoryFailure) as refusal:
        learnt.forget(first)
    assert refusal.value.code == "conflict"
    assert learnt.clear() == () and learnt.read() == ()


def test_nothing_is_learned_until_the_owner_switches_it_on(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learner = MemoryLearner(learnt)
    learner.learn("проверь систему", (done("local.check"),))
    assert learnt.read() == () and learner.written == 0
    assert learnt.set_learning(True) and learnt.learning()
    learner.learn("проверь систему", (done("local.check"),))
    assert {record.phrase for record in learnt.read()} == {"проверь", "систему"}
    learnt.set_learning(False)
    learner.learn("открой блокнот", (done("local.check"),))
    assert "открой" not in {record.phrase for record in learnt.read()}


def test_only_steps_that_succeeded_teach_anything() -> None:
    steps = (
        done("local.check", status=Status.ERROR),
        done("windows.open_app", json.dumps({"app": "notepad"})),
        done("windows.type_text", json.dumps({"text": "secret"})),
    )
    found = meanings(steps)
    assert ("tool", "local.check") not in found
    assert ("tool", "windows.open_app") in found and ("application", "notepad") in found
    assert all("secret" not in target for _, target in found)
    assert len(found) <= MAX_MEANINGS


def test_what_one_run_may_write_is_bounded(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.set_learning(True)
    learner = MemoryLearner(learnt)
    steps = tuple(done("local.check") for _ in range(8))
    learner.learn(" ".join(f"слово{index}" for index in range(20)), steps)
    assert len(learnt.read()) <= MAX_PHRASES * MAX_MEANINGS


@pytest.mark.asyncio
async def test_a_run_teaches_only_when_it_finished(harness: Harness, tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.set_learning(True)
    learner = MemoryLearner(learnt)

    def runner(provider: Scripted) -> Runner:
        return Runner(
            harness.registry,  # type: ignore[arg-type]
            harness.engine,
            provider,
            harness.approve,
            harness.clarify,
            learner=learner,
        )

    simulated = await runner(Scripted([call("local.check", {})])).run("проверь систему")
    assert simulated.status == "simulated" and learnt.read() == ()
    failed = await runner(Scripted([call("local.fail", {})])).run("сломай систему", Mode.EXECUTE)
    assert failed.status == "error" and learnt.read() == ()
    finished = await runner(Scripted([call("local.check", {})])).run(
        "проверь систему", Mode.EXECUTE
    )
    assert finished.status == "finished"
    assert {(record.phrase, record.target) for record in learnt.read()} == {
        ("проверь", "local.check"),
        ("систему", "local.check"),
    }


def test_a_confirmed_name_never_travels_to_the_planner(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.record("entity", "джон", "a" * 32)
    learnt.record("tool", "почту", "outlook.list")
    context = learned(learnt.read())
    assert [hint.phrase for hint in context.phrases] == ["почту"]
    assert "a" * 32 not in json.dumps(context.model_dump(mode="json"), ensure_ascii=False)


def test_learned_words_reach_the_command_that_uses_them(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.record("tool", "почту", "outlook.list")
    learnt.record("tool", "встречи", "calendar.list")
    context = assemble("покажи почту", derived=learnt)
    assert [hint.means for hint in context.derived.phrases] == ["outlook.list"]
    assert assemble("сделай отчёт", derived=learnt).derived.empty
    # Inflection is matched exactly as it is for known names: by prefix, not by guesswork.
    learnt.record("tool", "проект", "asana.list")
    assert not assemble("покажи проекта", derived=learnt).derived.empty


def test_deleting_a_learned_row_changes_the_very_next_selection(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    record = learnt.record("tool", "почту", "outlook.list")
    assert not assemble("покажи почту", derived=learnt).derived.empty
    learnt.forget(record)
    assert assemble("покажи почту", derived=learnt).derived.empty


def test_all_three_sources_together_stay_inside_one_budget(tmp_path: Path) -> None:
    knowledge = KnowledgeStore(tmp_path / "knowledge.sqlite3", clock=lambda: NOW)
    learnt = store(tmp_path)
    for index in range(12):
        knowledge.upsert(
            EntityDraft(
                type="person",
                name=f"Джон Смит {index}",
                external=(reference("email", f"john{index}@example.test", "outlook.list", RUN),),
            )
        )
        learnt.record("tool", f"джон{index}", "outlook.list")
    memory = MemoryContext(
        profile=tuple(
            Hint(kind="project", label=f"Проект {index}", value="Альфа") for index in range(8)
        )
    )
    command = "джон " + " ".join(f"джон{index}" for index in range(12))
    budget = Budget(characters=400)
    context = assemble(command, knowledge, memory=memory, derived=learnt, budget=budget)
    assert context.size() <= budget.characters
    assert not context.derived.empty and not context.knowledge.empty


def test_an_unreadable_learned_store_leaves_the_task_alone(tmp_path: Path) -> None:
    broken = tmp_path / "derived.sqlite3"
    broken.write_text("not a database", encoding="utf-8")
    learnt = DerivedStore(broken, clock=lambda: NOW)
    assert assemble("покажи почту", derived=learnt).derived.empty
    assert not learnt.learning()
    MemoryLearner(learnt).learn("покажи почту", (done("local.check"),))


def test_the_same_john_is_confirmed_once_and_remembered(tmp_path: Path) -> None:
    knowledge = KnowledgeStore(tmp_path / "knowledge.sqlite3", clock=lambda: NOW)
    learnt = store(tmp_path)
    known = knowledge.upsert(
        EntityDraft(
            type="person",
            name="Джон Смит",
            external=(reference("asana", "user_1", "asana.search_items", RUN),),
        )
    )
    seen = EntityDraft(
        type="person",
        name="Джон Смит",
        external=(reference("notion", "user_9", "notion.search_items", RUN),),
    )
    asked = resolve(knowledge, seen, confirmed=learnt)
    assert asked.match == "name" and asked.needs_confirmation
    merged = confirm(knowledge, known, seen, confirmed=learnt)
    assert merged.id == known.id
    again = resolve(knowledge, seen, confirmed=learnt)
    # The identifier now carries it, but the answer itself is what stops the question.
    assert again.match in ("identifier", "confirmed") and not again.needs_confirmation
    stranger = EntityDraft(
        type="person",
        name="Джон Смит",
        external=(reference("notion", "user_77", "notion.search_items", RUN),),
    )
    assert resolve(knowledge, stranger, confirmed=learnt).match == "confirmed"
    forgotten = next(record for record in learnt.read() if record.kind == "entity")
    learnt.forget(forgotten)
    assert resolve(knowledge, stranger, confirmed=learnt).needs_confirmation


def test_a_remembered_answer_selects_only_among_today_s_candidates(tmp_path: Path) -> None:
    knowledge = KnowledgeStore(tmp_path / "knowledge.sqlite3", clock=lambda: NOW)
    learnt = store(tmp_path)
    learnt.record("entity", "джон смит", "c" * 32)
    seen = EntityDraft(type="person", name="Джон Смит")
    # Nothing in the graph answers to that name, so a stored answer conjures nobody.
    assert resolve(knowledge, seen, confirmed=learnt).match == "new"


def test_a_learned_row_survives_a_restart_with_its_count(tmp_path: Path) -> None:
    first = store(tmp_path)
    first.record("tool", "почту", "outlook.list")
    first.record("tool", "почту", "outlook.list")
    reopened = store(tmp_path)
    kept = reopened.read()[0]
    assert isinstance(kept, Derived) and kept.runs == 2
    assert reopened.entity_for("нет такого") is None


def test_a_cancelled_read_is_reported_as_cancelled(tmp_path: Path) -> None:
    learnt = store(tmp_path)
    learnt.record("tool", "почту", "outlook.list")
    cancelled = Event()
    cancelled.set()
    with pytest.raises(MemoryFailure) as refusal:
        learnt.read(cancelled)
    assert refusal.value.code == "cancelled"
