"""Cross-service entities, links and resolution. No network, account or real service id."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest

from jarvis.knowledge.models import Entity, EntityDraft, Reference
from jarvis.knowledge.resolution import confirm, resolve
from jarvis.knowledge.store import KnowledgeFailure, KnowledgeStore, reference

RUN = "a" * 32
OTHER_RUN = "b" * 32


def store(tmp_path: Path, now: float = 1_700_000_000) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge.sqlite3", clock=lambda: now)


def person(name: str, *refs: Reference, aliases: tuple[str, ...] = ()) -> EntityDraft:
    return EntityDraft(type="person", name=name, aliases=aliases, external=refs)


def test_a_reference_must_carry_the_observation_that_produced_it() -> None:
    assert reference("asana", "12345", "asana.search_tasks", RUN).key == "asana:12345"
    for service, value, observed, run in (
        ("Asana", "12345", "asana.search_tasks", RUN),
        ("asana", "1234 5", "asana.search_tasks", RUN),
        ("asana", "12345", "search_tasks", RUN),
        ("asana", "12345", "asana.search_tasks", "not-a-run"),
        ("asana", "bearer_abc", "asana.search_tasks", RUN),
    ):
        with pytest.raises(ValueError):
            reference(service, value, observed, run)


def test_a_credential_is_never_a_name() -> None:
    for name in ("api key 123", "Пароль Джона", "", "x" * 121, "John\nSmith"):
        with pytest.raises(ValueError):
            person(name)


def test_the_same_identifier_merges_instead_of_creating_a_twin(tmp_path: Path) -> None:
    graph = store(tmp_path)
    asana = reference("asana", "user_456", "asana.search_tasks", RUN)
    first = graph.upsert(person("John Smith", asana))
    outlook = reference("outlook", "john@acme.test", "outlook.list", OTHER_RUN)
    second = graph.upsert(person("J. Smith", asana, outlook, aliases=("Джон",)))
    assert second.id == first.id
    assert second.name == "John Smith"
    # The established name stays; a newly observed spelling becomes an alias.
    assert set(second.aliases) == {"J. Smith", "Джон"}
    assert second.services == ("asana", "outlook")
    assert len(graph.search("smith")) == 1


def test_one_identifier_belongs_to_exactly_one_entity(tmp_path: Path) -> None:
    graph = store(tmp_path)
    shared = reference("asana", "user_456", "asana.search_tasks", RUN)
    graph.upsert(person("John Smith", shared))
    stranger = graph.upsert(person("Mary Jones"))
    with pytest.raises(KnowledgeFailure) as error:
        graph.attach(stranger.id, person("Mary Jones", shared))
    assert error.value.code == "conflict"


def test_a_matching_name_is_a_question_not_a_merge(tmp_path: Path) -> None:
    graph = store(tmp_path)
    known = graph.upsert(
        person("John Smith", reference("asana", "user_456", "asana.search_tasks", RUN))
    )
    observation = person(
        "John Smith", reference("fireflies", "p_xyz", "fireflies.get_meeting", OTHER_RUN)
    )
    decision = resolve(graph, observation)
    assert decision.match == "name" and decision.needs_confirmation
    assert [candidate.id for candidate in decision.candidates] == [known.id]
    # Nothing was written: resolving is a read.
    assert graph.by_reference("fireflies", "p_xyz") is None
    linked = confirm(graph, decision.candidates[0], observation)
    assert linked.id == known.id and linked.services == ("asana", "fireflies")
    assert resolve(graph, observation).match == "identifier"


def test_a_partial_name_never_claims_another_entity(tmp_path: Path) -> None:
    graph = store(tmp_path)
    graph.upsert(person("Johnson"))
    # "John" is a substring of "Johnson"; an exact label is required, so this is new.
    assert resolve(graph, person("John")).match == "new"


def test_an_unknown_observation_is_new(tmp_path: Path) -> None:
    graph = store(tmp_path)
    assert resolve(graph, person("Nobody Here")).match == "new"


def test_context_names_the_services_but_never_the_identifiers(tmp_path: Path) -> None:
    graph = store(tmp_path)
    graph.upsert(
        person(
            "John Smith",
            reference("asana", "user_456", "asana.search_tasks", RUN),
            reference("outlook", "john@acme.test", "outlook.list", RUN),
        )
    )
    context = graph.context()
    assert not context.empty
    hint = context.entities[0]
    assert hint.name == "John Smith" and hint.services == ("asana", "outlook")
    # The whole projection is what reaches a planner prompt; an identifier in it would let
    # a model address a target it never observed.
    serialized = json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
    assert "user_456" not in serialized
    assert "john@acme.test" not in serialized
    assert RUN not in serialized
    assert "asana" in serialized


def test_links_need_two_known_entities_and_read_back(tmp_path: Path) -> None:
    graph = store(tmp_path)
    john = graph.upsert(person("John Smith"))
    acme = graph.upsert(EntityDraft(type="company", name="Acme"))
    with pytest.raises(KnowledgeFailure) as error:
        graph.relate(john.id, "works_at", "c" * 32, "outlook.list", RUN)
    assert error.value.code == "unknown"
    with pytest.raises(KnowledgeFailure):
        graph.relate(john.id, "works_at", john.id, "outlook.list", RUN)
    link = graph.relate(john.id, "works_at", acme.id, "outlook.list", RUN)
    assert link.observed == "outlook.list"
    assert graph.links(john.id) == (link,)
    assert graph.links(acme.id) == (link,)
    # Repeating the same observation updates the link rather than duplicating it.
    graph.relate(john.id, "works_at", acme.id, "outlook.list", OTHER_RUN)
    assert len(graph.links(john.id)) == 1


def test_forgetting_an_entity_takes_its_identifiers_and_links(tmp_path: Path) -> None:
    graph = store(tmp_path)
    asana = reference("asana", "user_456", "asana.search_tasks", RUN)
    john = graph.upsert(person("John Smith", asana))
    acme = graph.upsert(EntityDraft(type="company", name="Acme"))
    graph.relate(john.id, "works_at", acme.id, "outlook.list", RUN)
    graph.forget(john.id)
    assert graph.get(john.id) is None
    assert graph.by_reference("asana", "user_456") is None
    assert graph.links(acme.id) == ()
    with pytest.raises(KnowledgeFailure):
        graph.forget(john.id)
    graph.clear()
    assert graph.context().empty


def test_a_type_change_is_a_conflict_not_a_silent_rewrite(tmp_path: Path) -> None:
    graph = store(tmp_path)
    asana = reference("asana", "user_456", "asana.search_tasks", RUN)
    graph.upsert(person("John Smith", asana))
    with pytest.raises(KnowledgeFailure) as error:
        graph.upsert(EntityDraft(type="company", name="John Smith", external=(asana,)))
    assert error.value.code == "conflict"


def test_a_tampered_row_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    graph = store(tmp_path)
    john = graph.upsert(person("John Smith"))
    with closing(sqlite3.connect(graph.path)) as db:
        db.execute("UPDATE entities SET payload=? WHERE id=?", ("{}", john.id))
        db.commit()
    with pytest.raises(KnowledgeFailure) as error:
        graph.get(john.id)
    assert error.value.code == "invalid"


def test_cancellation_stops_before_writing(tmp_path: Path) -> None:
    graph = store(tmp_path)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(KnowledgeFailure) as error:
        graph.upsert(person("John Smith"), cancelled)
    assert error.value.code == "cancelled"
    assert graph.context(cancelled=Event()).empty


def test_entities_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.knowledge.store.MAX_ENTITIES", 2)
    graph = store(tmp_path)
    graph.upsert(person("One Person"))
    graph.upsert(person("Two Person"))
    with pytest.raises(KnowledgeFailure) as error:
        graph.upsert(person("Three Person"))
    assert error.value.code == "limit"


def test_an_entity_survives_a_reopen(tmp_path: Path) -> None:
    asana = reference("asana", "user_456", "asana.search_tasks", RUN)
    store(tmp_path).upsert(person("John Smith", asana))
    reopened = store(tmp_path).by_reference("asana", "user_456")
    assert isinstance(reopened, Entity) and reopened.name == "John Smith"
