"""Atomic SQLite task snapshots and bounded playbooks alongside the unchanged profile."""

import json
import sqlite3
from threading import Event
from time import time
from typing import TYPE_CHECKING

from jarvis.memory.conversation import (
    MAX_PLAYBOOKS,
    MAX_RECENT,
    MAX_RECORD_BYTES,
    SENSITIVE,
    Playbook,
    RecentTask,
    make_playbook,
    safe_text,
    safe_url,
)
from jarvis.memory.models import PROFILE_TTL, SESSION_TTL
from jarvis.memory.store import MemoryFailure, MemoryStore
from jarvis.permissions.policies import Status
from jarvis.tools.scratchpad import ScratchpadWrite

if TYPE_CHECKING:
    from jarvis.core.planner.contracts import PlanResult


def task_snapshot(command: str, result: "PlanResult") -> RecentTask | None:
    """Copy successful final notes and allowlisted references, never raw adapter payloads."""
    if (
        result.status != "finished"
        or not result.steps
        or any(step.outcome.status is not Status.SUCCESS for step in result.steps)
    ):
        return None
    handles: set[int] = set()
    urls: set[str] = set()
    paths: set[str] = set()
    for step in result.steps:
        value = json.loads(step.outcome.result_json or "{}")
        if not isinstance(value, dict):
            continue
        if step.tool.startswith("windows."):
            for target in [value.get("target"), *value.get("windows", [])]:
                if isinstance(target, dict) and type(target.get("handle")) is int:
                    handles.add(target["handle"])
        elif step.tool.startswith("browser."):
            page = value.get("page") or {}
            for target in [page.get("target"), *value.get("tabs", [])]:
                if isinstance(target, dict) and isinstance(target.get("url"), str):
                    url = safe_url(target["url"])
                    if url:
                        urls.add(url)
        elif step.tool.startswith("fs."):
            for file in [value, *value.get("files", [])]:
                if isinstance(file, dict) and isinstance(file.get("path"), str):
                    path = file["path"]
                    if not SENSITIVE.search(path):
                        paths.add(path)
    return RecentTask(
        created=int(time()),
        command=safe_text(command),
        summary=result.summary,
        scratchpad=tuple(
            ScratchpadWrite(key=note.key, value=safe_text(note.value))
            for note in result.scratchpad
            if not SENSITIVE.search(note.key)
        ),
        tools=tuple(step.tool for step in result.steps),
        window_handles=tuple(sorted(handles))[:64],
        urls=tuple(sorted(urls))[:32],
        paths=tuple(sorted(paths))[:32],
    )


class ExperienceStore(MemoryStore):
    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        db.execute(
            "CREATE TABLE IF NOT EXISTS recent_tasks "
            "(id TEXT PRIMARY KEY, created INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS playbooks "
            "(id TEXT PRIMARY KEY, created INTEGER NOT NULL, payload TEXT NOT NULL)"
        )

    def _load(self, db: sqlite3.Connection) -> tuple[tuple[RecentTask, ...], tuple[Playbook, ...]]:
        self._schema(db)
        stamp = int(self.clock())
        db.execute("DELETE FROM recent_tasks WHERE created <= ?", (stamp - SESSION_TTL,))
        db.execute("DELETE FROM playbooks WHERE created <= ?", (stamp - PROFILE_TTL,))
        tasks: list[RecentTask] = []
        books: list[Playbook] = []
        for table, limit, model, dest in (
            ("recent_tasks", MAX_RECENT, RecentTask, tasks),
            ("playbooks", MAX_PLAYBOOKS, Playbook, books),
        ):
            # Table names are trusted constants, never request/model inputs.
            rows = db.execute(
                f"SELECT id, created, payload FROM {table} ORDER BY rowid DESC LIMIT ?",
                (limit + 1,),
            ).fetchall()
            if len(rows) > limit:
                raise MemoryFailure("limit")
            for key, created, payload in rows:
                try:
                    if not isinstance(payload, str) or len(payload.encode()) > MAX_RECORD_BYTES:
                        raise ValueError
                    value = model.model_validate_json(payload)
                    if value.id != key or value.created != created or created > stamp:
                        raise ValueError
                except ValueError:
                    raise MemoryFailure("invalid") from None
                dest.append(value)  # type: ignore[arg-type]
        return tuple(tasks), tuple(books)

    def load(
        self, cancelled: Event | None = None
    ) -> tuple[tuple[RecentTask, ...], tuple[Playbook, ...]]:
        with self._db(cancelled or Event()) as db:
            return self._load(db)

    def remember(self, task: RecentTask, cancelled: Event | None = None) -> None:
        task = RecentTask.model_validate(task)
        if not 0 <= int(self.clock()) - task.created < SESSION_TTL:
            raise MemoryFailure("invalid")
        with self._db(cancelled or Event()) as db:
            _, books = self._load(db)
            # Idempotence for a task ID; never duplicate its learned route.
            if db.execute("SELECT 1 FROM recent_tasks WHERE id=?", (task.id,)).fetchone():
                return
            db.execute(
                "INSERT INTO recent_tasks VALUES (?, ?, ?)",
                (task.id, task.created, task.model_dump_json()),
            )
            db.execute(
                "DELETE FROM recent_tasks WHERE id NOT IN "
                "(SELECT id FROM recent_tasks ORDER BY rowid DESC LIMIT ?)",
                (MAX_RECENT,),
            )
            book = make_playbook(task)
            if book is not None:
                for previous in books:
                    if previous.terms == book.terms:
                        db.execute("DELETE FROM playbooks WHERE id=?", (previous.id,))
                db.execute(
                    "INSERT INTO playbooks VALUES (?, ?, ?)",
                    (book.id, book.created, book.model_dump_json()),
                )
                db.execute(
                    "DELETE FROM playbooks WHERE id NOT IN "
                    "(SELECT id FROM playbooks ORDER BY rowid DESC LIMIT ?)",
                    (MAX_PLAYBOOKS,),
                )

    def forget(self, key: str = "", cancelled: Event | None = None) -> None:
        with self._db(cancelled or Event()) as db:
            self._schema(db)
            if key:
                db.execute("DELETE FROM recent_tasks WHERE id=?", (key,))
                db.execute("DELETE FROM playbooks WHERE id=?", (key,))
            else:
                db.execute("DELETE FROM recent_tasks")
                db.execute("DELETE FROM playbooks")
