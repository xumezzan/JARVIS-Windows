"""Registering Asana's declared capabilities as typed tools.

The creation is read back before it is reported as done. Asana answering 201 says the
request was accepted, not that the workspace now holds the task that was asked for, and a
task that landed with the wrong name is worse than one that failed loudly - somebody will
read it and act on it.
"""

import json
from typing import Any

from jarvis.connectors.asana.connector import CAPABILITIES, AsanaConnector
from jarvis.connectors.asana.models import (
    AsanaResult,
    CreateTaskInput,
    ProjectsInput,
    TaskInput,
    TasksInput,
    WorkspacesInput,
)
from jarvis.connectors.base import Capability, tool_name
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry


def register_asana(
    registry: ToolRegistry,
    connector: AsanaConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in CAPABILITIES}

    async def check(args: ToolModel, context: ExecutionContext) -> bool:
        await context.checkpoint()
        state = await connector.authenticate()
        return state.state == "connected"

    async def verify(args: ToolModel, result: AsanaResult, context: ExecutionContext) -> bool:
        await context.checkpoint()
        if result.state != "created":
            return True
        wanted = getattr(args, "task", None)
        if wanted is None or not result.task_gid:
            return False
        # Read it back: an accepted request is not a stored task.
        observed = await connector.task(TaskInput(task=result.task_gid), context)
        stored = json.loads(observed.data)
        return bool(stored.get("name") == wanted.name)

    operations: tuple[tuple[str, type[ToolModel], Any], ...] = (
        ("workspaces", WorkspacesInput, connector.workspaces),
        ("projects", ProjectsInput, connector.projects),
        ("tasks", TasksInput, connector.tasks),
        ("task", TaskInput, connector.task),
        ("create_task", CreateTaskInput, connector.create_task),
    )
    for name, parameters, run in operations:
        capability: Capability = capabilities[name]
        registry.register(
            ToolSpec(
                tool_name(connector.service, name),
                capability.description,
                policy.effective(connector.service, capability),
                parameters,
                AsanaResult,
                check,
                run,
                verify,
                # Two calls fit inside this: the creation and the read-back that proves it.
                timeout_seconds=30,
                cancellation="Cancel before the request is issued; a created task is not undone.",
                idempotency=(
                    "Repeatable read."
                    if capability.idempotent
                    else "One execution per prepared request; a repeat would create the task twice."
                ),
            )
        )
