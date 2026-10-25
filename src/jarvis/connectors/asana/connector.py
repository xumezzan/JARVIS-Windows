"""Asana as a connector: four reads and one guarded write.

The reads answer the questions the owner's own scenarios ask - what is open on this
project, what is assigned to this person, what does this task actually say. The write
creates a task and nothing else: no editing, no completing, no deleting, no moving work
between projects. Those are absent rather than forbidden, so there is nothing to bypass.

Creating a task is CONFIRM, and deliberately. It is not reversible work on the owner's own
machine: it appears in a shared workspace, other people see it, and some of them will act
on it. The owner may lower it in their matrix; the default is the one that asks.
"""

import json
from typing import Any

from jarvis.connectors.asana.api import AsanaFailure, translate, transport
from jarvis.connectors.asana.models import (
    MAX_DATA,
    AsanaResult,
    CreateTaskInput,
    Project,
    ProjectsInput,
    Task,
    TaskInput,
    TasksInput,
    Workspace,
    WorkspacesInput,
)
from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import TransportError
from jarvis.core.planner.contracts import ProviderError
from jarvis.permissions.policies import Risk
from jarvis.security.credentials import load_api_key
from jarvis.tools.base import ExecutionContext

# Asana returns compact records by default, so every field the assistant reports has to be
# asked for by name. Asking for exactly these keeps the answer small and the surface known.
TASK_FIELDS = "gid,name,completed,due_on,notes,permalink_url,assignee.name"
PROJECT_FIELDS = "gid,name,archived"

CAPABILITIES = (
    Capability("workspaces", "Показать рабочие пространства.", Risk.SAFE, True, reads=("account",)),
    Capability("projects", "Показать проекты пространства.", Risk.SAFE, True, reads=("project",)),
    Capability(
        "tasks", "Показать задачи проекта или исполнителя.", Risk.SAFE, True, reads=("task",)
    ),
    Capability("task", "Прочитать одну задачу целиком.", Risk.SAFE, True, reads=("task",)),
    Capability("create_task", "Создать задачу в Asana.", Risk.CONFIRM, False, writes=("task",)),
)


def text(value: object, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def records(body: dict[str, object]) -> list[dict[str, Any]]:
    data = body.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def record(body: dict[str, object]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def task_of(item: dict[str, Any]) -> Task:
    assignee = item.get("assignee")
    return Task(
        gid=text(item.get("gid"), 32) or "0",
        name=text(item.get("name"), 200),
        completed=bool(item.get("completed")),
        assignee=text(assignee.get("name"), 200) if isinstance(assignee, dict) else "",
        due_on=text(item.get("due_on"), 10),
        notes=text(item.get("notes"), 4000),
        permalink=text(item.get("permalink_url"), 300),
    )


def packed(payload: object) -> tuple[str, bool]:
    """One bounded JSON string. Asana's answers are people's writing, so they are cut."""
    encoded = json.dumps(payload, ensure_ascii=False)
    return (encoded, False) if len(encoded) <= MAX_DATA else (encoded[:MAX_DATA], True)


class AsanaConnector:
    service = "asana"

    def __init__(self) -> None:
        self.transport = transport()

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

    async def _call(
        self,
        method: str,
        path: str,
        context: ExecutionContext,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        await context.checkpoint()
        try:
            key = await load_api_key(self.service)
        except ProviderError:
            raise AsanaFailure("asana_credentials") from None
        try:
            _, body = await self.transport.request(key, method, path, payload, params)
        except TransportError as error:
            raise translate(error) from None
        finally:
            key = ""
        return body

    async def workspaces(self, args: WorkspacesInput, context: ExecutionContext) -> AsanaResult:
        body = await self._call(
            "GET",
            "/workspaces",
            context,
            params={"limit": str(args.limit), "opt_fields": "gid,name"},
        )
        found = [
            Workspace(gid=text(i.get("gid"), 32) or "0", name=text(i.get("name")))
            for i in records(body)
        ]
        data, cut = packed([item.model_dump(mode="json") for item in found[: args.limit]])
        return AsanaResult(state="workspaces", data=data, truncated=cut)

    async def projects(self, args: ProjectsInput, context: ExecutionContext) -> AsanaResult:
        body = await self._call(
            "GET",
            "/projects",
            context,
            params={
                "workspace": args.workspace,
                "limit": str(args.limit),
                "opt_fields": PROJECT_FIELDS,
            },
        )
        found = [
            Project(
                gid=text(item.get("gid"), 32) or "0",
                name=text(item.get("name")),
                archived=bool(item.get("archived")),
            )
            for item in records(body)
        ]
        data, cut = packed([item.model_dump(mode="json") for item in found[: args.limit]])
        return AsanaResult(state="projects", data=data, truncated=cut)

    async def tasks(self, args: TasksInput, context: ExecutionContext) -> AsanaResult:
        params = {"limit": str(args.limit), "opt_fields": TASK_FIELDS}
        if args.project:
            params["project"] = args.project
        else:
            # The model validator has already refused anything but these two shapes.
            params["workspace"] = args.workspace or ""
            params["assignee"] = args.assignee
        body = await self._call("GET", "/tasks", context, params=params)
        found = [task_of(item) for item in records(body)]
        if not args.completed:
            found = [item for item in found if not item.completed]
        data, cut = packed([item.model_dump(mode="json") for item in found[: args.limit]])
        return AsanaResult(state="tasks", data=data, truncated=cut)

    async def task(self, args: TaskInput, context: ExecutionContext) -> AsanaResult:
        body = await self._call(
            "GET", f"/tasks/{args.task}", context, params={"opt_fields": TASK_FIELDS}
        )
        found = task_of(record(body))
        data, cut = packed(found.model_dump(mode="json"))
        return AsanaResult(state="task", task_gid=found.gid, data=data, truncated=cut)

    async def create_task(self, args: CreateTaskInput, context: ExecutionContext) -> AsanaResult:
        wanted = args.task
        payload: dict[str, object] = {"name": wanted.name}
        if wanted.projects:
            payload["projects"] = list(wanted.projects)
        if wanted.workspace:
            payload["workspace"] = wanted.workspace
        if wanted.notes:
            payload["notes"] = wanted.notes
        if wanted.due_on:
            payload["due_on"] = wanted.due_on
        if wanted.assignee:
            payload["assignee"] = wanted.assignee
        body = await self._call(
            "POST",
            "/tasks",
            context,
            params={"opt_fields": TASK_FIELDS},
            payload={"data": payload},
        )
        created = task_of(record(body))
        data, cut = packed(created.model_dump(mode="json"))
        return AsanaResult(state="created", task_gid=created.gid, data=data, truncated=cut)
