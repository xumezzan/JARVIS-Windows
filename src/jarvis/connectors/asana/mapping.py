"""How an Asana answer reads as entities: projects, tasks, and who they are on.

Assignees arrive as display names, not addresses, so they are not merged with the people
the calendar and Fireflies know. Two services agreeing that somebody is called "Иван
Петров" is not evidence that it is the same Иван Петров, and the graph merges on
identifiers rather than on strings - so an assignee is recorded as a person known to
Asana, and stays a separate record until an address ties them together.
"""

import json
from typing import Any

from jarvis.knowledge.harvest import Link, Observation, draft
from jarvis.knowledge.models import EntityDraft, Reference

SERVICE = "asana"


def reference(value: str, tool: str, run: str) -> tuple[Reference, ...]:
    try:
        return (Reference(service=SERVICE, value=value, observed=tool, run=run),)
    except ValueError:
        return ()


def task(row: dict[str, Any], tool: str, run: str) -> Observation:
    entities: list[EntityDraft | None] = [
        draft(
            type="task",
            name=str(row.get("name") or "Задача без названия")[:120],
            external=reference(str(row["gid"]), tool, run),
        )
    ]
    links: list[Link] = []
    assignee = str(row.get("assignee") or "").strip()
    if assignee:
        # Named, not addressed: this record answers "who in Asana", not "which person".
        entities.append(draft(type="person", name=assignee[:120]))
        links.append(Link(len(entities) - 1, "owns", 0))
    return Observation(tuple(entities), tuple(links))


def project(row: dict[str, Any], tool: str, run: str) -> Observation:
    return Observation(
        (
            draft(
                type="project",
                name=str(row.get("name") or "Проект без названия")[:120],
                external=reference(str(row["gid"]), tool, run),
            ),
        ),
        (),
    )


def rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = json.loads(result.get("data") or "null")
    if isinstance(raw, dict):
        raw = [raw]
    return (
        [row for row in raw if isinstance(row, dict) and row.get("gid")]
        if isinstance(raw, list)
        else []
    )


def collect(reader: Any, tool: str, run: str, result: dict[str, Any]) -> Observation:
    entities: list[EntityDraft | None] = []
    links: list[Link] = []
    for row in rows(result):
        observation = reader(row, tool, run)
        offset = len(entities)
        entities.extend(observation.entities)
        links.extend(
            Link(link.subject + offset, link.predicate, link.object + offset)
            for link in observation.links
        )
    return Observation(tuple(entities), tuple(links))


def read_tasks(tool: str, run: str, result: dict[str, Any]) -> Observation:
    return collect(task, tool, run, result)


def read_projects(tool: str, run: str, result: dict[str, Any]) -> Observation:
    return collect(project, tool, run, result)


MAPPERS = {
    "asana.tasks": read_tasks,
    "asana.task": read_tasks,
    "asana.create_task": read_tasks,
    "asana.projects": read_projects,
}
