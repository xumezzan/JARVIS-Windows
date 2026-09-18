"""Asana connector. No API key, account, network or real workspace is used."""

import asyncio
import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest

from jarvis.connectors.asana.api import ORIGIN, ROUTES, AsanaFailure, transport
from jarvis.connectors.asana.connector import CAPABILITIES, AsanaConnector
from jarvis.connectors.asana.mapping import MAPPERS
from jarvis.connectors.asana.models import (
    AsanaResult,
    CreateTaskInput,
    NewTask,
    ProjectsInput,
    TaskInput,
    TasksInput,
    WorkspacesInput,
)
from jarvis.connectors.asana.tools import register_asana
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

KEY = "synthetic-noncredential"
RUN = "a" * 32


def raw_task(
    gid: str = "1201", name: str = "Прислать смету", completed: bool = False
) -> dict[str, Any]:
    return {
        "gid": gid,
        "name": name,
        "completed": completed,
        "due_on": "2026-09-30",
        "notes": "Черновик у Ивана",
        "permalink_url": "https://app.asana.com/0/1/1201",
        "assignee": {"gid": "77", "name": "Иван Петров"},
    }


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, str], dict[str, object] | None]] = []
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
        # The real transport refuses an unlisted route; the fake holds itself to the same table.
        assert transport().allows(method, path), f"{method} {path} is not an allowed route"
        self.sent.append((method, path, dict(params or {}), payload))
        return 200, self.answers.pop(0)


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[AsanaConnector, FakeApi]]:
    async def key(service: str) -> str:
        assert service == "asana"
        return KEY

    monkeypatch.setattr("jarvis.connectors.asana.connector.load_api_key", key)
    built = AsanaConnector()
    api = FakeApi()
    built.transport = api  # type: ignore[assignment]
    yield built, api


@pytest.mark.asyncio
async def test_open_tasks_of_a_project_are_read_and_the_finished_ones_left_out(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    built, api = connector
    api.answers = [{"data": [raw_task(), raw_task("1202", "Уже сделано", completed=True)]}]
    result = await built.tasks(TasksInput(project="900"), ExecutionContext(Flag()))
    method, path, params, _ = api.sent[0]
    assert (method, path) == ("GET", "/tasks")
    assert params["project"] == "900" and "assignee" not in params
    rows = json.loads(result.data)
    assert [row["name"] for row in rows] == ["Прислать смету"]
    assert rows[0]["assignee"] == "Иван Петров"


@pytest.mark.asyncio
async def test_a_listing_without_a_project_needs_both_a_workspace_and_a_person() -> None:
    """Asana refuses a bare listing; this refuses it before anything is sent."""
    with pytest.raises(ValueError):
        TasksInput()
    with pytest.raises(ValueError):
        TasksInput(workspace="5")
    assert TasksInput(workspace="5", assignee="me").assignee == "me"


@pytest.mark.asyncio
async def test_a_created_task_is_read_back_before_it_is_called_done(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_asana(registry, built)
    spec = registry.get("asana.create_task")
    assert spec is not None
    api.answers = [
        {"data": raw_task(name="Собрать отчёт")},
        {"data": raw_task(name="Собрать отчёт")},
    ]
    context = ExecutionContext(Flag())
    args = spec.normalize({"task": {"name": "Собрать отчёт", "projects": ["900"]}})
    result = await spec.run(args, context)
    assert isinstance(result, AsanaResult) and result.state == "created"
    method, path, _, payload = api.sent[0]
    assert (method, path) == ("POST", "/tasks")
    assert payload == {"data": {"name": "Собрать отчёт", "projects": ["900"]}}
    assert await spec.verify(args, result, context) is True
    # The read-back is a second call, against the identifier the service returned.
    assert api.sent[1][:2] == ("GET", "/tasks/1201")


@pytest.mark.asyncio
async def test_a_task_that_came_back_different_is_not_reported_as_created(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    built, api = connector
    registry = ToolRegistry()
    register_asana(registry, built)
    spec = registry.get("asana.create_task")
    assert spec is not None
    api.answers = [
        {"data": raw_task(name="Собрать отчёт")},
        {"data": raw_task(name="Совсем другое")},
    ]
    context = ExecutionContext(Flag())
    args = spec.normalize({"task": {"name": "Собрать отчёт", "projects": ["900"]}})
    result = await spec.run(args, context)
    assert await spec.verify(args, result, context) is False


def test_creating_a_task_asks_before_it_happens_and_reading_does_not() -> None:
    """A task appears in a shared workspace and other people act on it."""
    registry = ToolRegistry()
    register_asana(registry, AsanaConnector())
    assert registry.get("asana.create_task").risk is Risk.CONFIRM  # type: ignore[union-attr]
    for name in ("asana.tasks", "asana.task", "asana.projects", "asana.workspaces"):
        assert registry.get(name).risk is Risk.SAFE  # type: ignore[union-attr]


def test_the_owner_may_tighten_a_capability_but_never_loosen_it() -> None:
    registry = ToolRegistry()
    matrix = PermissionMatrix({"asana": {"tasks": Rule(risk=Risk.CONFIRM)}})
    register_asana(registry, AsanaConnector(), matrix)
    assert registry.get("asana.tasks").risk is Risk.CONFIRM  # type: ignore[union-attr]


def test_the_route_table_is_the_whole_surface() -> None:
    """Asana can delete, complete and move work. None of it is reachable from here."""
    built = transport()
    assert ORIGIN == "https://app.asana.com/api/1.0"
    assert len(ROUTES) == 6
    for method, path in (
        ("DELETE", "/tasks/1201"),
        ("PUT", "/tasks/1201"),
        ("POST", "/tasks/1201/addProject"),
        ("GET", "/tasks/1201/subtasks"),
        ("POST", "/workspaces/5/tasks/search"),
    ):
        assert not built.allows(method, path), f"{method} {path} must not be reachable"
    assert built.allows("GET", "/tasks/1201") and built.allows("POST", "/tasks")


def test_a_capability_that_writes_is_never_safe() -> None:
    writing = [capability for capability in CAPABILITIES if capability.writes]
    assert writing and all(capability.risk is not Risk.SAFE for capability in writing)
    assert all(not capability.idempotent for capability in writing)


def test_an_assignee_is_a_name_in_asana_and_not_a_person_anywhere_else() -> None:
    """Two services agreeing on a display name is not evidence of the same human."""
    observation = MAPPERS["asana.tasks"](
        "asana.tasks",
        RUN,
        {"data": json.dumps([{"gid": "1201", "name": "Смета", "assignee": "Иван Петров"}])},
    )
    entities = [entity for entity in observation.entities if entity is not None]
    assert [entity.type for entity in entities] == ["task", "person"]
    task, person = entities
    assert [reference.service for reference in task.external] == ["asana"]
    # The person carries no external identity at all, so nothing merges them by name.
    assert person.external == ()


@pytest.mark.asyncio
async def test_a_missing_key_is_a_finite_failure_and_never_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.core.planner.contracts import ProviderError

    async def refuse(service: str) -> str:
        raise ProviderError("credentials")

    monkeypatch.setattr("jarvis.connectors.asana.connector.load_api_key", refuse)
    built = AsanaConnector()
    api = FakeApi()
    built.transport = api  # type: ignore[assignment]
    with pytest.raises(AsanaFailure) as failure:
        await built.projects(ProjectsInput(workspace="5"), ExecutionContext(Flag()))
    assert failure.value.code == "asana_credentials"
    assert api.sent == []


@pytest.mark.asyncio
async def test_a_workspace_listing_asks_only_for_what_it_reports(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    built, api = connector
    api.answers = [{"data": [{"gid": "5", "name": "Acme"}]}]
    result = await built.workspaces(WorkspacesInput(), ExecutionContext(Flag()))
    assert json.loads(result.data) == [{"gid": "5", "name": "Acme"}]
    assert api.sent[0][2]["opt_fields"] == "gid,name"


@pytest.mark.asyncio
async def test_one_task_is_addressed_by_the_identifier_and_nothing_else(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    built, api = connector
    api.answers = [{"data": raw_task()}]
    result = await built.task(TaskInput(task="1201"), ExecutionContext(Flag()))
    assert api.sent[0][:2] == ("GET", "/tasks/1201")
    assert result.task_gid == "1201"
    # An identifier is digits: a path, a name or a sentence cannot be one.
    with pytest.raises(ValueError):
        TaskInput(task="../projects")


@pytest.mark.asyncio
async def test_a_cancelled_task_stops_before_asana_is_asked_anything(
    connector: tuple[AsanaConnector, FakeApi],
) -> None:
    """Cancellation is answered before the wire, and that matters most for the write.

    A read given up on costs nothing. A create given up on after the request went out is a
    task in somebody's project that this run does not know it made, so the checkpoint has
    to come first - and on the write the test proves it by the request that never happened.
    """
    built, api = connector
    stopped = Flag()
    stopped.set()
    with pytest.raises(asyncio.CancelledError):
        await built.tasks(TasksInput(project="900"), ExecutionContext(stopped))
    with pytest.raises(asyncio.CancelledError):
        await built.create_task(
            CreateTaskInput(task=NewTask(name="Прислать смету", workspace="5")),
            ExecutionContext(stopped),
        )
    assert api.sent == []
