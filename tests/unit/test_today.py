"""What is left of today: read through the engine, bounded to the day, never invented."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry
from jarvis.ui.today_worker import TOOL, TodayWorker, rows

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
ACCOUNT: dict[str, object] = {
    "service": "outlook",
    "user_id": "fixture-user",
    "address": "owner@example.test",
    "session": "a" * 32,
}


class Ask(ToolModel):
    account: dict[str, object] | None = None
    start: str | None = None
    end: str | None = None
    limit: int | None = None


class Answer(ToolModel):
    data: str = ""


def event(subject: str = "Встреча с клиентом", cancelled: bool = False) -> dict[str, Any]:
    return {
        "id": "evt1",
        "subject": subject,
        "start": "2026-09-18T14:00:00Z",
        "end": "2026-09-18T15:00:00Z",
        "organizer": "owner@example.test",
        "attendees": [],
        "location": "Ташкент",
        "cancelled": cancelled,
    }


@dataclass
class Calendar:
    """One service, as a fixture: what it answers, how risky it is, and what it was asked."""

    events: list[dict[str, Any]] = field(default_factory=lambda: [event()])
    risk: Risk = Risk.SAFE
    broken: bool = False
    asked: list[Ask] = field(default_factory=list)

    async def check(self, args: Ask, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def verify(self, args: Ask, result: Answer, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def run(self, args: Ask, context: ExecutionContext) -> Answer:
        await context.checkpoint()
        self.asked.append(args)
        if self.broken:
            raise ToolError("network_denied")
        return Answer(data=json.dumps(self.events))

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                TOOL,
                "Фикстура календаря для главного экрана.",
                self.risk,
                Ask,
                Answer,
                self.check,
                self.run,
                self.verify,
                timeout_seconds=2,
            )
        )
        return registry


@dataclass
class Bench:
    calendar: Calendar
    worker: TodayWorker
    audit: AuditLog


def bench_for(tmp_path: Path, calendar: Calendar, registry: ToolRegistry | None = None) -> Bench:
    audit = AuditLog(tmp_path / "audit.sqlite3")
    tools = registry if registry is not None else calendar.registry()
    engine = PermissionEngine(tools, ApprovalStore(), audit)
    return Bench(calendar, TodayWorker(tools, engine, ACCOUNT, NOW), audit)


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    built = bench_for(tmp_path, Calendar())
    yield built
    built.audit.close()


def test_the_day_shown_is_the_day_the_service_holds(bench: Bench) -> None:
    bench.worker.run()
    assert bench.worker.read
    assert [row["subject"] for row in bench.worker.events] == ["Встреча с клиентом"]


def test_the_range_ends_with_the_local_day(bench: Bench) -> None:
    bench.worker.run()
    asked = bench.calendar.asked[0]
    assert asked.start == "2026-09-18T09:00:00Z"
    # Whatever the machine's timezone is, the end is its own midnight rather than "+24h".
    assert asked.end is not None and asked.end.endswith("Z")
    ends = datetime.strptime(asked.end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    local_midnight = ends.astimezone()
    assert (local_midnight.hour, local_midnight.minute) == (0, 0)
    assert 0 < (ends - NOW).total_seconds() <= 48 * 3600


def test_a_cancelled_meeting_is_not_a_meeting(tmp_path: Path) -> None:
    bench = bench_for(tmp_path, Calendar(events=[event(cancelled=True), event("Тренировка")]))
    bench.worker.run()
    assert [row["subject"] for row in bench.worker.events] == ["Тренировка"]
    bench.audit.close()


def test_anything_but_a_plain_read_shows_an_empty_day(tmp_path: Path) -> None:
    # Showing the day is looking at it; a capability the owner raised is refused here too.
    bench = bench_for(tmp_path, Calendar(risk=Risk.CONFIRM))
    bench.worker.run()
    assert bench.worker.events == () and not bench.calendar.asked
    bench.audit.close()


def test_a_service_that_fails_leaves_the_card_saying_so(tmp_path: Path) -> None:
    bench = bench_for(tmp_path, Calendar(broken=True))
    bench.worker.run()
    # Read stays true - the worker finished - and the empty list is what gets shown.
    assert bench.worker.events == ()
    bench.audit.close()


def test_a_calendar_this_session_does_not_have_is_simply_not_read(tmp_path: Path) -> None:
    bench = bench_for(tmp_path, Calendar(), registry=ToolRegistry())
    bench.worker.run()
    assert bench.worker.events == () and not bench.calendar.asked
    bench.audit.close()


def test_a_malformed_answer_is_no_answer() -> None:
    assert rows(None) == [] and rows("") == [] and rows("{") == []
    assert rows(json.dumps({"data": "null"})) == []
    assert rows(json.dumps({"data": json.dumps({"id": "one"})})) == [{"id": "one"}]
