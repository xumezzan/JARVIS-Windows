"""Fireflies connector. No API key, account, network or real meeting is used."""

import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest

from jarvis.connectors.fireflies.connector import CAPABILITIES, FirefliesConnector, moment
from jarvis.connectors.fireflies.graphql import MEETING, MEETINGS, TRANSCRIPT, FirefliesFailure
from jarvis.connectors.fireflies.models import (
    FirefliesResult,
    MeetingInput,
    RangeInput,
    SearchInput,
    TranscriptInput,
)
from jarvis.connectors.fireflies.tools import register_fireflies
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

START = "2026-09-16T00:00:00Z"
END = "2026-09-17T00:00:00Z"
KEY = "synthetic-noncredential"


def raw_meeting(identifier: str = "trn_1") -> dict[str, Any]:
    return {
        "id": identifier,
        "title": "Синк по Альфе",
        "date": 1789560000000,
        "duration": 1800,
        "organizer_email": "owner@example.test",
        "participants": ["owner@example.test", "john@acme.test"],
    }


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {"data": {"transcripts": [raw_meeting()]}}

    async def request(
        self,
        credential: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert (method, path) == ("POST", "/graphql")
        assert credential == KEY
        self.sent.append(dict(payload or {}))
        return 200, dict(self.answer)


@pytest.fixture
def bench(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FirefliesConnector, FakeGraph, ExecutionContext]]:
    async def key(provider: str = "openai") -> str:
        assert provider == "fireflies"
        return KEY

    monkeypatch.setattr("jarvis.connectors.fireflies.connector.load_api_key", key)
    graph = FakeGraph()
    yield FirefliesConnector(graph), graph, ExecutionContext(Flag())  # type: ignore[arg-type]


def test_the_connector_declares_only_reads() -> None:
    assert {capability.name for capability in CAPABILITIES} == {
        "list",
        "search",
        "get",
        "transcript",
    }
    # Fireflies can delete a transcript; that capability is absent, not merely guarded.
    assert all(
        capability.risk is Risk.SAFE and capability.idempotent and not capability.writes
        for capability in CAPABILITIES
    )


def test_the_query_documents_are_constants_not_assembled_text() -> None:
    for document in (MEETINGS, MEETING, TRANSCRIPT):
        assert "$" in document and "{" in document
        # Nothing interpolates into them, so no caller can widen what is asked for.
        assert "%s" not in document and "{}" not in document and "format" not in document
    assert "sentences" in TRANSCRIPT and "sentences" not in MEETING
    # The summary is one call and the words are another: an allowance is spent on purpose.
    assert "summary" in MEETING and "summary" not in MEETINGS


@pytest.mark.asyncio
async def test_a_search_word_travels_as_a_variable(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    args = SearchInput(start=START, end=END, keyword='") { id } deleteTranscript(id: "trn_1')
    await connector.search(args, context)
    body = graph.sent[0]
    assert body["query"] == MEETINGS
    assert body["variables"]["keyword"] == '") { id } deleteTranscript(id: "trn_1'
    # The document is untouched: the injection attempt stays data.
    assert "deleteTranscript" not in str(body["query"])


@pytest.mark.asyncio
async def test_a_search_needs_something_to_search_for() -> None:
    with pytest.raises(ValueError):
        SearchInput(start=START, end=END)
    assert SearchInput(start=START, end=END, keyword="альфа").keyword == "альфа"
    assert SearchInput(start=START, end=END, participants=("john@acme.test",)).keyword == ""


def test_free_form_dates_are_refused_before_anything_is_asked() -> None:
    for start, end in (("вчера", END), ("2026-09-16T00:00:00+03:00", END), (END, START)):
        with pytest.raises(ValueError):
            RangeInput(start=start, end=end)


@pytest.mark.asyncio
async def test_listing_normalises_epoch_and_seconds(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    result = await connector.list(RangeInput(start=START, end=END), context)
    rows = json.loads(result.data)
    assert result.state == "listed" and len(rows) == 1
    assert rows[0]["start"] == "2026-09-16T12:00:00Z"
    # Duration arrives in seconds and leaves in whole minutes.
    assert rows[0]["minutes"] == 30
    assert rows[0]["participants"] == ["owner@example.test", "john@acme.test"]


def test_a_date_in_no_known_shape_is_refused() -> None:
    assert moment(1789560000000) == "2026-09-16T12:00:00Z"
    assert moment("2026-09-16T12:00:00Z") == "2026-09-16T12:00:00Z"
    unusable: tuple[object, ...] = (None, "позавчера", True, [], 10**20)
    for value in unusable:
        with pytest.raises(FirefliesFailure):
            moment(value)


@pytest.mark.asyncio
async def test_a_summary_is_carried_as_untrusted_observation(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    graph.answer = {
        "data": {
            "transcript": raw_meeting()
            | {
                "summary": {
                    "overview": "Обсудили сроки.",
                    "action_items": ["Джон готовит предложение к пятнице"],
                    "keywords": ["альфа"],
                    "bullet_gist": ["Сроки сдвинуты"],
                }
            }
        }
    }
    result = await connector.get(MeetingInput(meeting_id="trn_1"), context)
    payload = json.loads(result.data)
    assert result.state == "read" and result.meeting_id == "trn_1"
    # What people said is labelled as observation, never as instruction.
    assert payload["untrusted_summary"]["action_items"] == ["Джон готовит предложение к пятнице"]
    assert payload["meeting"]["title"] == "Синк по Альфе"


@pytest.mark.asyncio
async def test_a_missing_meeting_is_one_finite_category(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    graph.answer = {"data": {"transcript": None}}
    with pytest.raises(FirefliesFailure) as error:
        await connector.get(MeetingInput(meeting_id="trn_absent"), context)
    assert error.value.code == "fireflies_missing"


@pytest.mark.asyncio
async def test_errors_inside_a_successful_status_are_still_errors(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    graph.answer = {"errors": [{"message": "unauthorised"}], "data": None}
    with pytest.raises(FirefliesFailure) as error:
        await connector.list(RangeInput(start=START, end=END), context)
    assert error.value.code == "fireflies_response"


@pytest.mark.asyncio
async def test_a_long_transcript_is_cut_and_says_so(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, graph, context = bench
    graph.answer = {
        "data": {
            "transcript": raw_meeting()
            | {
                "sentences": [
                    {"speaker_name": "John", "text": f"Строка {index}"} for index in range(500)
                ]
            }
        }
    }
    result = await connector.transcript(TranscriptInput(meeting_id="trn_1", lines=50), context)
    payload = json.loads(result.data)
    assert len(payload["untrusted_lines"]) == 50
    # Five hundred lines were there; the result admits it kept fifty.
    assert result.truncated is True


@pytest.mark.asyncio
async def test_a_missing_key_never_reaches_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.core.planner.contracts import ProviderError

    async def absent(provider: str = "openai") -> str:
        raise ProviderError("credentials")

    monkeypatch.setattr("jarvis.connectors.fireflies.connector.load_api_key", absent)
    graph = FakeGraph()
    connector = FirefliesConnector(graph)  # type: ignore[arg-type]
    with pytest.raises(FirefliesFailure) as error:
        await connector.list(RangeInput(start=START, end=END), ExecutionContext(Flag()))
    assert error.value.code == "fireflies_credentials"
    assert graph.sent == []
    assert (await connector.authenticate()).state == "disconnected"
    assert (await connector.health_check()).reason == "credentials"


@pytest.mark.asyncio
async def test_the_tools_register_with_owner_policy_applied(
    bench: tuple[FirefliesConnector, FakeGraph, ExecutionContext],
) -> None:
    connector, _, context = bench
    registry = ToolRegistry()
    register_fireflies(
        registry, connector, PermissionMatrix({"fireflies": {"transcript": Rule(allowed=False)}})
    )
    listed = registry.get("fireflies.list")
    transcript = registry.get("fireflies.transcript")
    assert listed is not None and transcript is not None
    assert listed.risk is Risk.SAFE and transcript.risk is Risk.BLOCKED
    payload = listed.normalize({"start": START, "end": END, "limit": 5})
    result = await listed.run(payload, context)
    assert await listed.verify(payload, result, context) is True
    assert FirefliesResult.model_validate(result).state == "listed"
