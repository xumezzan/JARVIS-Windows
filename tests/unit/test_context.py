"""Selecting relevant context under a budget. No network, account or model is used."""

import json
from pathlib import Path

import pytest

from jarvis.core.context.assembly import Budget, Context, assemble
from jarvis.core.context.relevance import matches, rank, score, terms
from jarvis.knowledge.models import EntityDraft
from jarvis.knowledge.store import KnowledgeStore, reference
from jarvis.memory.models import Hint, MemoryContext

RUN = "a" * 32


def store(tmp_path: Path, now: float = 1_700_000_000) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge.sqlite3", clock=lambda: now)


def person(name: str, *services: str, aliases: tuple[str, ...] = ()) -> EntityDraft:
    # Each entity needs identifiers of its own: a shared one would correctly merge them.
    slug = "".join(char for char in name.casefold() if char.isalnum())
    return EntityDraft(
        type="person",
        name=name,
        aliases=aliases,
        external=tuple(
            reference(service, f"id_{service}_{slug}", f"{service}.search_items", RUN)
            for service in services
        ),
    )


def test_only_words_worth_matching_become_terms() -> None:
    assert terms("Найди счёт Джона в QuickBooks") == ("найди", "счёт", "джона", "quickbooks")
    # Very short words carry no signal and would match everything.
    assert terms("я и он") == ()
    assert terms("Acme acme ACME") == ("acme",)
    assert len(terms(" ".join(f"слово{index}" for index in range(40)))) == 16


def test_a_shorter_word_matches_only_as_a_real_prefix() -> None:
    assert matches("john", "john")
    # Russian inflection: the stored name is the stem of the spoken form.
    assert matches("джон", "джона")
    assert matches("проект", "проекта")
    # Too short to be evidence: "joe" must not claim "joey" by three letters.
    assert not matches("joe", "joey")
    assert not matches("john", "johann")
    assert not matches("smith", "smyth")


def test_naming_a_whole_label_beats_naming_part_of_it() -> None:
    entity = person("John Smith")
    both = score(entity, terms("письмо john smith"))
    one = score(entity, terms("письмо john"))
    assert both > one > 0
    assert score(entity, terms("покажи календарь")) == 0


def test_an_alias_is_enough_to_be_relevant() -> None:
    entity = person("John Smith", aliases=("Джон",))
    assert score(entity, terms("что обсуждали с Джоном")) > 0


def test_ranking_prefers_the_better_match_then_the_more_recent(tmp_path: Path) -> None:
    graph = store(tmp_path)
    older = graph.upsert(person("Acme Industries"))
    graph.clock = lambda: 1_700_000_100
    newer = graph.upsert(person("Acme Holdings"))
    # Each names one of its two words, so the scores tie and recency decides.
    ranked = rank(graph.entities(), terms("счёт acme"))
    assert [entity.id for _, entity in ranked] == [newer.id, older.id]
    # Naming both words of one label outweighs being the more recent entity.
    exact = rank(graph.entities(), terms("счёт acme industries"))
    assert exact[0][1].id == older.id and exact[0][0] > exact[1][0]


def test_only_what_the_command_names_reaches_the_context(tmp_path: Path) -> None:
    graph = store(tmp_path)
    graph.upsert(person("John Smith", "asana", "outlook"))
    graph.upsert(person("Mary Jones", "asana"))
    graph.upsert(EntityDraft(type="company", name="Acme"))
    context = assemble("создай задачу для John Smith", graph)
    assert [hint.name for hint in context.knowledge.entities] == ["John Smith"]
    assert context.knowledge.entities[0].services == ("asana", "outlook")


def test_a_command_naming_nothing_known_carries_no_knowledge(tmp_path: Path) -> None:
    graph = store(tmp_path)
    graph.upsert(person("John Smith", "asana"))
    assert assemble("покажи календарь на завтра", graph).knowledge.empty
    # A command of only tiny words has nothing to match on.
    assert assemble("я и он", graph).knowledge.empty


def test_the_chosen_labels_pass_through_untouched(tmp_path: Path) -> None:
    memory = MemoryContext(profile=(Hint(kind="profession", label="Роль", value="Инженер"),))
    context = assemble("любая команда", store(tmp_path), memory=memory)
    assert context.memory == memory
    assert context.knowledge.empty and not context.empty


def test_the_entity_budget_is_a_hard_cap(tmp_path: Path) -> None:
    graph = store(tmp_path)
    for index in range(8):
        graph.upsert(person(f"Acme Number{index}"))
    context = assemble("счёт acme", graph, budget=Budget(entities=3))
    assert len(context.knowledge.entities) == 3
    assert assemble("счёт acme", graph, budget=Budget(entities=0)).knowledge.empty


def test_the_character_budget_drops_the_weakest_match(tmp_path: Path) -> None:
    graph = store(tmp_path)
    for index in range(6):
        graph.upsert(person(f"Acme Number{index}", "asana", "outlook", "notion"))
    full = assemble("счёт acme", graph)
    tight = assemble("счёт acme", graph, budget=Budget(characters=400))
    assert 0 < len(tight.knowledge.entities) < len(full.knowledge.entities)
    assert tight.size() <= 400
    # What survives is the head of the ranking, not an arbitrary subset.
    kept = [hint.name for hint in tight.knowledge.entities]
    assert kept == [hint.name for hint in full.knowledge.entities][: len(kept)]


def test_a_budget_outside_its_range_is_refused() -> None:
    for entities, characters in ((-1, 2000), (13, 2000), (6, 199), (6, 8001)):
        with pytest.raises(ValueError):
            Budget(entities=entities, characters=characters)


def test_context_is_an_aid_not_a_precondition(tmp_path: Path) -> None:
    memory = MemoryContext(profile=(Hint(kind="profession", label="Роль", value="Инженер"),))
    # The path is a directory: the graph cannot be read, and the request still proceeds.
    unreadable = assemble("задача для John Smith", KnowledgeStore(tmp_path), memory=memory)
    assert unreadable.knowledge.empty and unreadable.memory == memory
    assert assemble("задача для John Smith", None, memory=memory).memory == memory
    assert Context().empty


def test_reading_a_graph_nobody_wrote_creates_nothing(tmp_path: Path) -> None:
    graph = store(tmp_path)
    assert assemble("задача для John Smith", graph).knowledge.empty
    assert graph.entities() == () and graph.context().empty
    assert graph.get("b" * 32) is None and graph.search("john") == ()
    assert not graph.path.exists()


def test_what_reaches_the_prompt_never_carries_an_identifier(tmp_path: Path) -> None:
    graph = store(tmp_path)
    graph.upsert(
        EntityDraft(
            type="person",
            name="John Smith",
            external=(reference("asana", "user_456", "asana.search_tasks", RUN),),
        )
    )
    context = assemble("задача для John Smith", graph)
    serialized = json.dumps(context.knowledge.model_dump(mode="json"), ensure_ascii=False)
    assert "John Smith" in serialized and "asana" in serialized
    assert "user_456" not in serialized and RUN not in serialized
