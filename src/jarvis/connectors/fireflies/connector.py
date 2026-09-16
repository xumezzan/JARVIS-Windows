"""Fireflies as a connector: four reads over one API key, and no way to write.

Every call spends from a daily allowance — fifty on the free plan — so the surface is
split by cost rather than by convenience. Listing a day is cheap, a summary is one call,
and the full transcript is asked for only when someone actually wants the words. Fetching
the sentences alongside every summary would burn the allowance on text nobody read.
"""

import json
from datetime import UTC, datetime
from typing import Any

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.fireflies.graphql import (
    MEETING,
    MEETINGS,
    TRANSCRIPT,
    FirefliesFailure,
    translate,
    transport,
)
from jarvis.connectors.fireflies.models import (
    MAX_LINES,
    MAX_PARTICIPANTS,
    FirefliesResult,
    Line,
    Meeting,
    MeetingInput,
    RangeInput,
    SearchInput,
    Summary,
    TranscriptInput,
)
from jarvis.connectors.http import ServiceTransport, TransportError
from jarvis.connectors.instants import render
from jarvis.core.planner.contracts import ProviderError
from jarvis.permissions.policies import Risk
from jarvis.security.credentials import load_api_key
from jarvis.tools.base import ExecutionContext

MAX_DATA = 48000

CAPABILITIES = (
    Capability("list", "Показать встречи за интервал.", Risk.SAFE, True, reads=("meeting",)),
    Capability(
        "search", "Найти встречи по слову или участнику.", Risk.SAFE, True, reads=("meeting",)
    ),
    Capability(
        "get", "Прочитать итоги встречи и пункты действий.", Risk.SAFE, True, reads=("meeting",)
    ),
    Capability("transcript", "Прочитать расшифровку встречи.", Risk.SAFE, True, reads=("meeting",)),
)


def moment(value: object) -> str:
    """Fireflies dates arrive as epoch milliseconds or as text; one exact shape leaves."""
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return render(datetime.fromtimestamp(float(value) / 1000, UTC))
        if isinstance(value, str) and value:
            return render(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError, OSError):
        raise FirefliesFailure("fireflies_response") from None
    raise FirefliesFailure("fireflies_response")


def labels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FirefliesFailure("fireflies_response")
    found = []
    for item in value[:MAX_PARTICIPANTS]:
        if isinstance(item, str):
            found.append(item[:200])
        elif isinstance(item, dict):
            # Some accounts answer with objects; keep the address and nothing else.
            address = item.get("email") or item.get("name") or ""
            found.append(str(address)[:200])
    return tuple(label for label in found if label)


def texts(value: object, limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FirefliesFailure("fireflies_response")
    return tuple(str(item)[:1000] for item in value[:limit] if isinstance(item, str | int | float))


def meeting_of(row: object) -> Meeting:
    if not isinstance(row, dict):
        raise FirefliesFailure("fireflies_response")
    duration = row.get("duration")
    try:
        return Meeting(
            id=str(row["id"]),
            title=str(row.get("title") or "")[:200],
            start=moment(row.get("date")),
            minutes=min(1440, max(0, int(float(duration) / 60))) if duration is not None else 0,
            organizer=str(row.get("organizer_email") or "")[:200],
            participants=labels(row.get("participants")),
        )
    except FirefliesFailure:
        raise
    except Exception:
        raise FirefliesFailure("fireflies_response") from None


def summary_of(row: dict[str, Any]) -> Summary:
    raw = row.get("summary")
    if raw is None:
        return Summary()
    if not isinstance(raw, dict):
        raise FirefliesFailure("fireflies_response")
    return Summary(
        overview=str(raw.get("overview") or "")[:8000],
        bullets=texts(raw.get("bullet_gist"), 40),
        keywords=labels(raw.get("keywords")),
        action_items=texts(raw.get("action_items"), 40),
    )


def bounded(payload: object) -> tuple[str, bool]:
    """Serialise for the tool result, and say plainly when something was left out."""
    text = json.dumps(payload, ensure_ascii=False)
    return (text, False) if len(text) <= MAX_DATA else (text[:MAX_DATA], True)


class FirefliesConnector:
    service = "fireflies"

    def __init__(self, graph: ServiceTransport | None = None) -> None:
        self.graph = graph or transport()

    def capabilities(self) -> tuple[Capability, ...]:
        return CAPABILITIES

    async def authenticate(self) -> AuthState:
        try:
            await load_api_key(self.service)
        except ProviderError:
            return AuthState("disconnected")
        return AuthState("connected")

    async def health_check(self) -> Health:
        state = await self.authenticate()
        return (
            Health("ready")
            if state.state == "connected"
            else Health("unauthenticated", "credentials")
        )

    async def _query(
        self, document: str, variables: dict[str, object], context: ExecutionContext
    ) -> dict[str, Any]:
        try:
            key = await load_api_key(self.service)
        except ProviderError:
            raise FirefliesFailure("fireflies_credentials") from None
        await context.checkpoint()
        try:
            _, body = await self.graph.request(
                key, "POST", "/graphql", {"query": document, "variables": variables}
            )
        except TransportError as error:
            raise translate(error) from None
        finally:
            key = ""
        await context.checkpoint()
        if body.get("errors"):
            # GraphQL reports failure inside a 200, so status alone proves nothing.
            raise FirefliesFailure("fireflies_response")
        data = body.get("data")
        if not isinstance(data, dict):
            raise FirefliesFailure("fireflies_response")
        return data

    async def _meetings(
        self, variables: dict[str, object], limit: int, context: ExecutionContext
    ) -> FirefliesResult:
        data = await self._query(MEETINGS, variables, context)
        rows = data.get("transcripts")
        if rows is None:
            rows = []
        if not isinstance(rows, list) or len(rows) > limit:
            raise FirefliesFailure("fireflies_response")
        meetings = [meeting_of(row).model_dump(mode="json") for row in rows]
        text, truncated = bounded(meetings)
        return FirefliesResult(state="listed", data=text, truncated=truncated)

    async def list(self, args: RangeInput, context: ExecutionContext) -> FirefliesResult:
        return await self._meetings(
            {
                "limit": args.limit,
                "fromDate": args.start,
                "toDate": args.end,
                "keyword": None,
                "participants": None,
            },
            args.limit,
            context,
        )

    async def search(self, args: SearchInput, context: ExecutionContext) -> FirefliesResult:
        return await self._meetings(
            {
                "limit": args.limit,
                "fromDate": args.start,
                "toDate": args.end,
                "keyword": args.keyword.strip() or None,
                "participants": list(args.participants) or None,
            },
            args.limit,
            context,
        )

    async def get(self, args: MeetingInput, context: ExecutionContext) -> FirefliesResult:
        data = await self._query(MEETING, {"id": args.meeting_id}, context)
        row = data.get("transcript")
        if not isinstance(row, dict):
            raise FirefliesFailure("fireflies_missing")
        meeting = meeting_of(row)
        text, truncated = bounded(
            {
                "meeting": meeting.model_dump(mode="json"),
                # Untrusted: what people said, as the service heard it.
                "untrusted_summary": summary_of(row).model_dump(mode="json"),
            }
        )
        return FirefliesResult(state="read", meeting_id=meeting.id, data=text, truncated=truncated)

    async def transcript(self, args: TranscriptInput, context: ExecutionContext) -> FirefliesResult:
        data = await self._query(TRANSCRIPT, {"id": args.meeting_id}, context)
        row = data.get("transcript")
        if not isinstance(row, dict):
            raise FirefliesFailure("fireflies_missing")
        meeting = meeting_of(row)
        sentences = row.get("sentences") or []
        if not isinstance(sentences, list):
            raise FirefliesFailure("fireflies_response")
        lines = [
            Line(
                speaker=str(item.get("speaker_name") or "")[:200],
                text=str(item.get("text") or "")[:2000],
            ).model_dump(mode="json")
            for item in sentences[: min(args.lines, MAX_LINES)]
            if isinstance(item, dict)
        ]
        text, truncated = bounded(
            {"meeting": meeting.model_dump(mode="json"), "untrusted_lines": lines}
        )
        return FirefliesResult(
            state="transcript",
            meeting_id=meeting.id,
            data=text,
            truncated=truncated or len(sentences) > len(lines),
        )
