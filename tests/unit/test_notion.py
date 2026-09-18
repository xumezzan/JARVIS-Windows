"""Notion connector. No integration token, workspace, network or real page is used."""

import asyncio
import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest

from jarvis.connectors.notion.api import ORIGIN, ROUTES, VERSION, NotionFailure, transport
from jarvis.connectors.notion.connector import CAPABILITIES, NotionConnector
from jarvis.connectors.notion.mapping import MAPPERS
from jarvis.connectors.notion.models import (
    AppendInput,
    CreatePageInput,
    NewPage,
    NotionResult,
    PageInput,
    ReadInput,
    SearchInput,
)
from jarvis.connectors.notion.tools import register_notion
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

KEY = "synthetic-noncredential"
RUN = "b" * 32
PAGE = "3c90c3cc-0d44-4b50-8888-8dd25736052a"
PARENT = "b55c9c91-384d-452b-81db-d1ef79372b75"


def raw_page(title: str = "Клиент Альфа", archived: bool = False) -> dict[str, Any]:
    return {
        "object": "page",
        "id": PAGE,
        "url": "https://www.notion.so/alpha",
        "in_trash": archived,
        "properties": {
            "Название": {"type": "title", "title": [{"plain_text": title}]},
            "Статус": {"type": "select", "select": {"name": "Активен"}},
        },
    }


def raw_block(kind: str, words: str) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": [{"plain_text": words}]}}


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, object] | None]] = []
        self.answers: list[dict[str, Any]] = []

    async def request(
        self,
        credential: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert credential == KEY
        assert transport().allows(method, path), f"{method} {path} is not an allowed route"
        self.sent.append((method, path, payload))
        return 200, self.answers.pop(0)


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[NotionConnector, FakeApi]]:
    async def key(service: str) -> str:
        assert service == "notion"
        return KEY

    monkeypatch.setattr("jarvis.connectors.notion.connector.load_api_key", key)
    built = NotionConnector()
    api = FakeApi()
    built.transport = api  # type: ignore[assignment]
    yield built, api


@pytest.mark.asyncio
async def test_a_search_asks_for_pages_and_leaves_the_binned_ones_out(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    api.answers = [{"results": [raw_page(), raw_page("Старое", archived=True)]}]
    result = await built.search(SearchInput(query="Альфа"), ExecutionContext(Flag()))
    method, path, payload = api.sent[0]
    assert (method, path) == ("POST", "/v1/search")
    assert payload == {
        "query": "Альфа",
        "filter": {"property": "object", "value": "page"},
        "page_size": 10,
    }
    assert [row["title"] for row in json.loads(result.data)] == ["Клиент Альфа"]


@pytest.mark.asyncio
async def test_a_title_is_found_by_its_own_declaration_not_by_its_name(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    """The title property is whichever one says type=title; here it is called "Название"."""
    built, api = connector
    api.answers = [raw_page()]
    result = await built.page(PageInput(page=PAGE), ExecutionContext(Flag()))
    assert json.loads(result.data)["title"] == "Клиент Альфа"
    assert result.url == "https://www.notion.so/alpha"


@pytest.mark.asyncio
async def test_reading_a_page_returns_words_and_skips_what_has_none(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    api.answers = [
        {
            "results": [
                raw_block("heading_1", "Итоги"),
                {"object": "block", "type": "image", "image": {"file": {"url": "x"}}},
                raw_block("bulleted_list_item", "Смета до пятницы"),
            ]
        }
    ]
    result = await built.read(ReadInput(page=PAGE), ExecutionContext(Flag()))
    assert json.loads(result.data) == ["Итоги", "Смета до пятницы"]


@pytest.mark.asyncio
async def test_a_created_page_is_read_back_before_it_is_called_done(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_notion(registry, built)
    spec = registry.get("notion.create_page")
    assert spec is not None
    api.answers = [raw_page("Встреча с Альфой"), raw_page("Встреча с Альфой")]
    context = ExecutionContext(Flag())
    args = spec.normalize(
        {"page": {"parent": PARENT, "title": "Встреча с Альфой", "paragraphs": ["Первый пункт"]}}
    )
    result = await spec.run(args, context)
    assert isinstance(result, NotionResult) and result.state == "created"
    method, path, payload = api.sent[0]
    assert (method, path) == ("POST", "/v1/pages")
    assert isinstance(payload, dict)
    # A page, never a data source: the title column of somebody's database is theirs to name.
    assert payload["parent"] == {"page_id": PARENT}
    assert await spec.verify(args, result, context) is True
    assert api.sent[1][:2] == ("GET", f"/v1/pages/{PAGE}")


@pytest.mark.asyncio
async def test_a_page_that_came_back_with_another_title_is_not_reported_as_created(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_notion(registry, built)
    spec = registry.get("notion.create_page")
    assert spec is not None
    api.answers = [raw_page("Встреча с Альфой"), raw_page("Совсем другое")]
    context = ExecutionContext(Flag())
    args = spec.normalize({"page": {"parent": PARENT, "title": "Встреча с Альфой"}})
    result = await spec.run(args, context)
    assert await spec.verify(args, result, context) is False


@pytest.mark.asyncio
async def test_appending_is_checked_against_what_notion_says_it_wrote(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_notion(registry, built)
    spec = registry.get("notion.append")
    assert spec is not None
    context = ExecutionContext(Flag())
    api.answers = [{"results": [raw_block("paragraph", "Смета до пятницы")]}]
    args = spec.normalize({"page": PAGE, "paragraphs": ["Смета до пятницы"]})
    result = await spec.run(args, context)
    assert api.sent[0][:2] == ("PATCH", f"/v1/blocks/{PAGE}/children")
    assert await spec.verify(args, result, context) is True


@pytest.mark.asyncio
async def test_a_paragraph_that_came_back_mangled_fails_the_check(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_notion(registry, built)
    spec = registry.get("notion.append")
    assert spec is not None
    context = ExecutionContext(Flag())
    api.answers = [{"results": [raw_block("paragraph", "Смета до")]}]
    args = spec.normalize({"page": PAGE, "paragraphs": ["Смета до пятницы"]})
    result = await spec.run(args, context)
    assert await spec.verify(args, result, context) is False


def test_writing_asks_before_it_happens_and_reading_does_not() -> None:
    registry = ToolRegistry()
    register_notion(registry, NotionConnector())
    for name in ("notion.create_page", "notion.append"):
        assert registry.get(name).risk is Risk.CONFIRM  # type: ignore[union-attr]
    for name in ("notion.search", "notion.page", "notion.read"):
        assert registry.get(name).risk is Risk.SAFE  # type: ignore[union-attr]


def test_the_owner_may_tighten_a_capability_but_never_loosen_it() -> None:
    registry = ToolRegistry()
    matrix = PermissionMatrix({"notion": {"read": Rule(risk=Risk.CONFIRM)}})
    register_notion(registry, NotionConnector(), matrix)
    assert registry.get("notion.read").risk is Risk.CONFIRM  # type: ignore[union-attr]


def test_the_route_table_is_the_whole_surface_and_the_version_is_pinned() -> None:
    """Notion can archive, delete and rewrite properties. None of it is reachable."""
    built = transport()
    assert ORIGIN == "https://api.notion.com" and len(ROUTES) == 5
    assert built.headers["Notion-Version"] == VERSION
    for method, path in (
        ("DELETE", f"/v1/blocks/{PAGE}"),
        ("PATCH", f"/v1/pages/{PAGE}"),
        ("POST", f"/v1/data_sources/{PAGE}/query"),
        ("GET", "/v1/users"),
        ("POST", "/v1/databases"),
    ):
        assert not built.allows(method, path), f"{method} {path} must not be reachable"
    assert built.allows("GET", f"/v1/pages/{PAGE}") and built.allows("POST", "/v1/search")


def test_an_identifier_is_a_uuid_and_nothing_else() -> None:
    assert PageInput(page=PAGE).page == PAGE
    assert PageInput(page=PAGE.replace("-", "")).page == PAGE.replace("-", "")
    for wrong in ("../users", "page", "3c90c3cc", ""):
        with pytest.raises(ValueError):
            PageInput(page=wrong)


def test_a_capability_that_writes_is_never_safe_and_never_idempotent() -> None:
    writing = [capability for capability in CAPABILITIES if capability.writes]
    assert len(writing) == 2
    assert all(capability.risk is not Risk.SAFE for capability in writing)
    assert all(not capability.idempotent for capability in writing)


def test_the_graph_learns_the_page_and_never_its_contents() -> None:
    observation = MAPPERS["notion.search"](
        "notion.search",
        RUN,
        {"data": json.dumps([{"id": PAGE, "title": "Клиент Альфа", "url": "https://x"}])},
    )
    entities = [entity for entity in observation.entities if entity is not None]
    assert [entity.type for entity in entities] == ["document"]
    assert entities[0].name == "Клиент Альфа"
    assert [reference.service for reference in entities[0].external] == ["notion"]


@pytest.mark.asyncio
async def test_a_missing_token_is_a_finite_failure_and_never_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.core.planner.contracts import ProviderError

    async def refuse(service: str) -> str:
        raise ProviderError("credentials")

    monkeypatch.setattr("jarvis.connectors.notion.connector.load_api_key", refuse)
    built = NotionConnector()
    api = FakeApi()
    built.transport = api  # type: ignore[assignment]
    with pytest.raises(NotionFailure) as failure:
        await built.search(SearchInput(query="Альфа"), ExecutionContext(Flag()))
    assert failure.value.code == "notion_credentials"
    assert api.sent == []


def test_appending_nothing_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(ValueError):
        AppendInput(page=PAGE, paragraphs=[])


@pytest.mark.asyncio
async def test_a_cancelled_task_stops_before_notion_is_asked_anything(
    connector: tuple[NotionConnector, FakeApi],
) -> None:
    """The same rule as everywhere else, proved where it costs something.

    A search given up on costs nothing. A page created after the owner stopped the task is
    a page in their workspace that nothing in this run accounts for, and the paragraphs of
    an append are read by whoever the page is shared with.
    """
    built, api = connector
    stopped = Flag()
    stopped.set()
    with pytest.raises(asyncio.CancelledError):
        await built.search(SearchInput(query="Альфа"), ExecutionContext(stopped))
    with pytest.raises(asyncio.CancelledError):
        await built.create_page(
            CreatePageInput(page=NewPage(parent=PAGE, title="Альфа")),
            ExecutionContext(stopped),
        )
    with pytest.raises(asyncio.CancelledError):
        await built.append(AppendInput(page=PAGE, paragraphs=["строка"]), ExecutionContext(stopped))
    assert api.sent == []
