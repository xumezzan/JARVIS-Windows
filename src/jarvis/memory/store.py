"""Bounded SQLite profile with atomic edits, expiry and finite failure categories."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import time
from typing import Literal

from jarvis.memory.models import MAX_ENTRIES, Entry


class MemoryFailure(Exception):
    def __init__(self, code: Literal["storage", "invalid", "limit", "cancelled", "conflict"]):
        self.code = code
        super().__init__(code)


class MemoryStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time):
        self.path = path
        self.clock = clock

    @contextmanager
    def _db(self, cancelled: Event) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            self._check(cancelled)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink() or (
                self.path.exists() and self.path.stat().st_size > 1048576
            ):
                raise MemoryFailure("storage")
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.set_progress_handler(lambda: int(cancelled.is_set()), 100)
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS profile (id TEXT PRIMARY KEY, payload TEXT)")
            yield db
            self._check(cancelled)
            db.commit()
        except MemoryFailure:
            raise
        except Exception:
            raise MemoryFailure("cancelled" if cancelled.is_set() else "storage") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _check(cancelled: Event) -> None:
        if cancelled.is_set():
            raise MemoryFailure("cancelled")

    def _read(self, db: sqlite3.Connection) -> tuple[Entry, ...]:
        now = int(self.clock())
        rows = db.execute("SELECT id, payload FROM profile LIMIT 33").fetchall()
        if len(rows) > MAX_ENTRIES:
            raise MemoryFailure("limit")
        entries = []
        for key, payload in rows:
            try:
                if not isinstance(payload, str) or len(payload) > 2048:
                    raise ValueError
                entry = Entry.model_validate_json(payload)
                if entry.id != key or entry.updated > now:
                    raise ValueError
            except Exception:
                raise MemoryFailure("invalid") from None
            if entry.expires <= now:
                db.execute("DELETE FROM profile WHERE id=?", (key,))
            else:
                entries.append(entry)
        return tuple(entries)

    def read(self, cancelled: Event | None = None) -> tuple[Entry, ...]:
        with self._db(cancelled or Event()) as db:
            return self._read(db)

    def save(
        self, entry: Entry, previous: Entry | None = None, cancelled: Event | None = None
    ) -> tuple[Entry, ...]:
        try:
            entry = Entry.model_validate(entry)
        except Exception:
            raise MemoryFailure("invalid") from None
        now = int(self.clock())
        if not now - 60 <= entry.updated <= now or entry.expires <= now:
            raise MemoryFailure("invalid")
        with self._db(cancelled or Event()) as db:
            entries = self._read(db)
            current = next((item for item in entries if item.id == entry.id), None)
            if current != previous:
                raise MemoryFailure("conflict")
            if current is None and len(entries) >= MAX_ENTRIES:
                raise MemoryFailure("limit")
            db.execute(
                "INSERT OR REPLACE INTO profile VALUES (?, ?)", (entry.id, entry.model_dump_json())
            )
            return self._read(db)

    def delete(self, previous: Entry, cancelled: Event | None = None) -> tuple[Entry, ...]:
        with self._db(cancelled or Event()) as db:
            entries = self._read(db)
            if previous not in entries:
                raise MemoryFailure("conflict")
            db.execute("DELETE FROM profile WHERE id=?", (previous.id,))
            return self._read(db)

    def clear(self, cancelled: Event | None = None) -> tuple[Entry, ...]:
        with self._db(cancelled or Event()) as db:
            db.execute("DELETE FROM profile")
            return ()
