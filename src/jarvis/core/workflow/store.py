"""Bounded SQLite run journal with atomic appends and finite failure categories.

A run survives the process that started it. That is the point: after a restart the owner
can see which requests were left unfinished and which effects already reached a service.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import time
from typing import Literal
from uuid import uuid4

from jarvis.core.workflow.models import MAX_STEPS, Phase, RunRecord, StepRecord
from jarvis.observability.audit import ErrorCode
from jarvis.permissions.policies import Mode, Status

MAX_RUNS = 500
MAX_DB_BYTES = 8 * 1024 * 1024
MAX_ROW = 16384


class WorkflowFailure(Exception):
    def __init__(
        self, code: Literal["storage", "invalid", "limit", "cancelled", "unknown", "finished"]
    ) -> None:
        self.code = code
        super().__init__(code)


class WorkflowStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time) -> None:
        self.path = path
        self.clock = clock

    @contextmanager
    def _db(self, cancelled: Event) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            self._check(cancelled)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink() or (
                self.path.exists() and self.path.stat().st_size > MAX_DB_BYTES
            ):
                raise WorkflowFailure("storage")
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.set_progress_handler(lambda: int(cancelled.is_set()), 100)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(id TEXT PRIMARY KEY, created INTEGER NOT NULL, payload TEXT NOT NULL)"
            )
            yield db
            self._check(cancelled)
            db.commit()
        except WorkflowFailure:
            raise
        except Exception:
            raise WorkflowFailure("cancelled" if cancelled.is_set() else "storage") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _check(cancelled: Event) -> None:
        if cancelled.is_set():
            raise WorkflowFailure("cancelled")

    @staticmethod
    def _record(key: str, payload: object) -> RunRecord:
        try:
            if not isinstance(payload, str) or len(payload) > MAX_ROW:
                raise ValueError
            record = RunRecord.model_validate_json(payload)
            if record.id != key:
                raise ValueError
        except Exception:
            raise WorkflowFailure("invalid") from None
        return record

    def _read(self, db: sqlite3.Connection, run_id: str) -> RunRecord | None:
        row = db.execute("SELECT id, payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return None if row is None else self._record(row[0], row[1])

    def _write(self, db: sqlite3.Connection, record: RunRecord) -> RunRecord:
        payload = record.model_dump_json()
        if len(payload) > MAX_ROW:
            raise WorkflowFailure("limit")
        db.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?)", (record.id, record.created, payload)
        )
        return record

    def start(self, request: str, mode: Mode, cancelled: Event | None = None) -> RunRecord:
        now = int(self.clock())
        try:
            record = RunRecord(
                id=uuid4().hex,
                request=request,
                mode=Mode(mode).value,
                phase="running",
                created=now,
                updated=now,
            )
        except Exception:
            raise WorkflowFailure("invalid") from None
        with self._db(cancelled or Event()) as db:
            count = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            if count >= MAX_RUNS:
                # Oldest first: a journal that refuses to grow must still accept today's work.
                db.execute(
                    "DELETE FROM runs WHERE id IN "
                    "(SELECT id FROM runs ORDER BY created ASC LIMIT ?)",
                    (count - MAX_RUNS + 1,),
                )
            return self._write(db, record)

    def get(self, run_id: str, cancelled: Event | None = None) -> RunRecord | None:
        with self._db(cancelled or Event()) as db:
            return self._read(db, run_id)

    def append(self, run_id: str, step: StepRecord, cancelled: Event | None = None) -> RunRecord:
        try:
            step = StepRecord.model_validate(step, strict=True)
        except Exception:
            raise WorkflowFailure("invalid") from None
        now = int(self.clock())
        with self._db(cancelled or Event()) as db:
            record = self._read(db, run_id)
            if record is None:
                raise WorkflowFailure("unknown")
            if record.finished:
                raise WorkflowFailure("finished")
            if len(record.steps) >= MAX_STEPS:
                raise WorkflowFailure("limit")
            return self._write(
                db,
                RunRecord(
                    id=record.id,
                    request=record.request,
                    mode=record.mode,
                    phase=record.phase,
                    steps=(*record.steps, step),
                    created=record.created,
                    updated=now,
                ),
            )

    def complete(
        self,
        run_id: str,
        key: str,
        status: Status,
        error: ErrorCode,
        may_have_effects: bool,
        cancelled: Event | None = None,
    ) -> RunRecord:
        """Resolve the most recent issued step under this key with the outcome it got."""
        now = int(self.clock())
        with self._db(cancelled or Event()) as db:
            record = self._read(db, run_id)
            if record is None:
                raise WorkflowFailure("unknown")
            if record.finished:
                raise WorkflowFailure("finished")
            steps = list(record.steps)
            position = next(
                (
                    index
                    for index in reversed(range(len(steps)))
                    if steps[index].key == key and steps[index].unresolved
                ),
                None,
            )
            if position is None:
                raise WorkflowFailure("unknown")
            try:
                steps[position] = StepRecord(
                    index=steps[position].index,
                    tool=steps[position].tool,
                    key=key,
                    state="finished",
                    status=Status(status).value,
                    error=ErrorCode(error).value,
                    may_have_effects=may_have_effects,
                    updated=now,
                )
            except Exception:
                raise WorkflowFailure("invalid") from None
            return self._write(
                db,
                RunRecord(
                    id=record.id,
                    request=record.request,
                    mode=record.mode,
                    phase=record.phase,
                    steps=tuple(steps),
                    created=record.created,
                    updated=now,
                ),
            )

    def phase(self, run_id: str, phase: Phase, cancelled: Event | None = None) -> RunRecord:
        now = int(self.clock())
        with self._db(cancelled or Event()) as db:
            record = self._read(db, run_id)
            if record is None:
                raise WorkflowFailure("unknown")
            if record.finished:
                # A finished run is history; reopening it would make its journal a lie.
                raise WorkflowFailure("finished")
            return self._write(
                db,
                RunRecord(
                    id=record.id,
                    request=record.request,
                    mode=record.mode,
                    phase=phase,
                    steps=record.steps,
                    created=record.created,
                    updated=now,
                ),
            )

    def recent(self, limit: int = 20, cancelled: Event | None = None) -> tuple[RunRecord, ...]:
        with self._db(cancelled or Event()) as db:
            rows = db.execute(
                "SELECT id, payload FROM runs ORDER BY created DESC LIMIT ?",
                (max(1, min(limit, MAX_RUNS)),),
            ).fetchall()
        return tuple(self._record(key, payload) for key, payload in rows)

    def unfinished(self, cancelled: Event | None = None) -> tuple[RunRecord, ...]:
        """What was in flight when the process stopped, newest first."""
        return tuple(record for record in self.recent(MAX_RUNS, cancelled) if not record.finished)

    def forget(self, run_id: str, cancelled: Event | None = None) -> None:
        with self._db(cancelled or Event()) as db:
            if self._read(db, run_id) is None:
                raise WorkflowFailure("unknown")
            db.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def clear(self, cancelled: Event | None = None) -> None:
        with self._db(cancelled or Event()) as db:
            db.execute("DELETE FROM runs")
