"""New calendar tools share synthetic MSAL/Graph fixtures; never contact Microsoft."""

import json
from collections.abc import AsyncIterator
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from tests.mail_support import FakeCredentials, FakeGraph
from tests.unit.test_planner import Scripted

from jarvis.config import AppConfig
from jarvis.connectors.microsoft.models import Draft, RangeInput
from jarvis.core.planner.contracts import PlanResult, Proposal, Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.core.report import written
from jarvis.mail.calendar import CalendarResult
from jarvis.mail.graph import GraphTransport
from jarvis.mail.oauth_helper import SURFACES
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.base import ExecutionContext
from jarvis.tools.calendar import register_calendar
from jarvis.tools.registry import ToolRegistry

START, END = "2026-10-26T09:00:00Z", "2026-10-26T18:00:00Z"
DRAFT = Draft(
    subject="Fixture meeting",
    start="2026-10-26T10:00:00Z",
    end="2026-10-26T11:00:00Z",
    body="Synthetic agenda",
    location="Room A",
    attendees=("guest@example.test",),
)


class CalendarGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.event: dict[str, Any] = {}
        self.tamper = ""
        self.uncertain = False
        self.next_link = False
        self.params: dict[str, str] = {}
        self.session: MailSession | None = None

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if path == "/me":
            return await super().request(token, method, path, payload, params)
        self.calls.append((method, path))
        if method == "POST":
            assert path == "/me/events" and payload is not None
            self.writes.append(deepcopy(payload))
            self.event = {
                **deepcopy(payload),
                "id": "fixture-event",
                "organizer": {"emailAddress": {"address": self.profile["mail"]}},
                "isCancelled": False,
            }
            if self.uncertain:
                raise RuntimeError("synthetic uncertain effect")
            return 201, deepcopy(self.event)
        if path == "/me/calendarView":
            self.params = dict(params or {})
            result: dict[str, object] = {"value": [deepcopy(self.event)] if self.event else []}
            if self.next_link:
                result["@odata.nextLink"] = "https://untrusted.example/more"
            return 200, result
        assert path == "/me/events/fixture-event"
        stored = deepcopy(self.event)
        if self.tamper == "body":
            stored["body"]["content"] = "changed"
        elif self.tamper == "time":
            stored["start"]["dateTime"] = "2026-10-26T12:00:00"
        elif self.tamper == "attendees":
            stored["attendees"][0]["emailAddress"]["address"] = "other@example.test"
        elif self.tamper == "location":
            stored["location"]["displayName"] = "Other room"
        elif self.tamper == "account":
            assert self.session is not None
            self.session.detach()  # Owner disconnects while read-back is in flight.
        elif self.tamper == "transaction":
            stored["transactionId"] = "different"
        return 200, stored


@pytest_asyncio.fixture
async def bench(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[MailSession, CalendarGraph, PermissionEngine, ApprovalAuthority, AuditLog]
]:
    graph = CalendarGraph()
    session = MailSession(FakeCredentials(), graph)
    graph.session = session
    await session.connect("00000000-0000-0000-0000-000000000001", ExecutionContext(Event()))
    registry = ToolRegistry()
    register_calendar(registry, session)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        yield (
            session,
            graph,
            PermissionEngine(registry, store, audit),
            store.take_authority(audit.approved),
            audit,
        )
    session.detach()


async def draft(engine: PermissionEngine, session: MailSession) -> dict[str, Any]:
    assert session.account is not None
    action = engine.prepare(
        "calendar.create_draft_event",
        {"account": session.account.model_dump(), "event": DRAFT.model_dump()},
        Mode.EXECUTE,
    )
    assert isinstance(action, Action) and action.risk is Risk.SAFE
    result = await engine.execute(action)
    assert result.status is Status.SUCCESS
    stored = CalendarResult.model_validate_json(result.result_json or "")
    return {
        "account": session.account.model_dump(),
        "event": DRAFT.model_dump(),
        "draft_id": stored.draft_id,
    }


def test_calendar_scope_and_graph_routes_are_narrow() -> None:
    assert "Calendars.ReadWrite" in SURFACES["mail"]
    transport = GraphTransport().transport
    assert transport.base_url == "https://graph.microsoft.com/v1.0"
    assert transport.allows("GET", "/me/calendarView")
    assert transport.allows("POST", "/me/events")
    assert transport.allows("GET", "/me/events/fixture-event")
    assert not transport.allows("DELETE", "/me/events/fixture-event")
    assert not transport.allows("POST", "/users/someone/events")


@pytest.mark.asyncio
async def test_ram_draft_then_one_confirmed_creation_and_full_readback(bench: Any) -> None:
    session, graph, engine, authority, _ = bench
    before = list(graph.calls)
    args = await draft(engine, session)
    assert graph.calls == before, "Local draft must not request a token or call Graph."
    denied = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(denied, Action) and denied.risk is Risk.CONFIRM
    assert (await engine.execute(denied)).status is Status.DENIED
    assert not graph.writes
    approved = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(approved, Action)
    result = await engine.execute(approved, authority.approve(approved))
    assert result.status is Status.SUCCESS, result
    observed = CalendarResult.model_validate_json(result.result_json or "")
    assert observed.state == "created" and observed.event_id == "fixture-event"
    assert observed.delivery_verified is False
    assert graph.writes[0]["transactionId"] == args["draft_id"]
    assert ("GET", "/me/events/fixture-event") in graph.calls
    repeated = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(repeated, Action)
    assert (await engine.execute(repeated, authority.approve(repeated))).status is Status.DENIED
    assert len(graph.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["body", "time", "attendees", "location", "account", "transaction"]
)
async def test_readback_must_match_every_approved_field(bench: Any, changed: str) -> None:
    session, graph, engine, authority, _ = bench
    args = await draft(engine, session)
    graph.tamper = changed
    action = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(action, Action)
    result = await engine.execute(action, authority.approve(action))
    assert result.status is Status.ERROR and result.may_have_effects
    assert len(graph.writes) == 1


@pytest.mark.asyncio
async def test_uncertain_post_consumes_draft_and_cannot_be_replayed(bench: Any) -> None:
    session, graph, engine, authority, _ = bench
    args = await draft(engine, session)
    graph.uncertain = True
    first = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(first, Action)
    failed = await engine.execute(first, authority.approve(first))
    assert failed.may_have_effects
    report = PlanResult("error", (Step(first.tool, failed, payload=first.payload),))
    assert "не подтверждено" in " ".join(written(report))
    again = engine.prepare("calendar.create_event", args, Mode.EXECUTE)
    assert isinstance(again, Action)
    assert (await engine.execute(again, authority.approve(again))).status is Status.DENIED
    assert len(graph.writes) == 1


@pytest.mark.asyncio
async def test_draft_change_disconnect_and_simulation_do_not_write(bench: Any) -> None:
    session, graph, engine, authority, _ = bench
    args = await draft(engine, session)
    changed = dict(args, event=DRAFT.model_copy(update={"body": "changed"}).model_dump())
    action = engine.prepare("calendar.create_event", changed, Mode.EXECUTE)
    assert isinstance(action, Action)
    assert (await engine.execute(action, authority.approve(action))).error is ErrorCode.PRECONDITION
    simulated = engine.prepare("calendar.create_event", args, Mode.SIMULATION)
    assert isinstance(simulated, Action)
    assert (
        await engine.execute(simulated, authority.approve(simulated))
    ).status is Status.SIMULATED
    session.detach()
    assert not session._event_drafts
    assert not graph.writes


@pytest.mark.asyncio
async def test_list_uses_fixed_calendar_view_and_does_not_follow_next_link(bench: Any) -> None:
    session, graph, engine, _, _ = bench
    graph.next_link = True
    assert session.account is not None
    args = RangeInput(account=session.account, start=START, end=END)
    action = engine.prepare("calendar.list_events", args.model_dump(), Mode.EXECUTE)
    assert isinstance(action, Action) and action.risk is Risk.SAFE
    result = await engine.execute(action)
    assert result.status is Status.SUCCESS
    observed = CalendarResult.model_validate_json(result.result_json or "")
    assert observed.truncated and observed.events == ()
    assert graph.params["startDateTime"] == START and graph.params["endDateTime"] == END
    assert all(path.startswith("/me") for _, path in graph.calls)
    assert not graph.writes


def test_workspace_configuration_is_outside_agent_filesystem_allowlist(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "data", file_roots=(str(tmp_path),))
    assert not config.file_policy.inside(config.data_dir / "workspace_profile.json")


def test_calendar_aliases_preserve_existing_owner_refusals() -> None:
    registry = ToolRegistry()
    register_calendar(
        registry, MailSession(), PermissionMatrix({"calendar": {"create": Rule(allowed=False)}})
    )
    entry = registry.get("calendar.create_event")
    assert entry is not None and entry.risk is Risk.BLOCKED


@pytest.mark.asyncio
async def test_runner_requires_current_task_draft_and_explicit_attendee_addresses(
    bench: Any,
) -> None:
    session, graph, engine, authority, _ = bench
    registry = engine._registry
    assert session.account is not None
    args = {"account": session.account.model_dump(), "event": DRAFT.model_dump()}
    clarify = AsyncMock(return_value="guest@example.test")
    provider = Scripted(
        [
            call("calendar.create_draft_event", args),
            call("calendar.create_draft_event", args),
            Proposal(kind="finish"),
        ]
    )
    runner = Runner(registry, engine, provider, AsyncMock(), clarify)
    account = {"account": session.account.model_dump(), "state": "account"}
    runner.steps.append(
        Step("outlook.account", Outcome(uuid4(), Status.SUCCESS, result_json=json.dumps(account)))
    )
    result = await runner.run("Подготовь встречу", Mode.EXECUTE)
    assert result.status == "finished"
    clarify.assert_awaited_once()
    assert not graph.writes
    stored = json.loads(result.steps[-1].outcome.result_json or "")
    create_args = {**args, "draft_id": stored["draft_id"]}
    fresh = Runner(
        registry,
        engine,
        Scripted([call("calendar.create_event", create_args)]),
        AsyncMock(side_effect=authority.approve),
        AsyncMock(),
    )
    fresh.steps.append(
        Step("outlook.account", Outcome(uuid4(), Status.SUCCESS, result_json=json.dumps(account)))
    )
    denied = await fresh.run("Создай встречу для guest@example.test", Mode.EXECUTE)
    assert denied.error == "unobserved_target" and not graph.writes
