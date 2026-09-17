"""What the assistant worked out from the owner's own finished runs.

Two kinds of memory already exist. `jarvis.memory.models` holds what the owner typed, and
`jarvis.knowledge` holds what connectors observed in services. Neither of them remembers
the one thing that repeats every single day: that when this owner says "почту" they mean
this tool, that "блокнот" opens that application, and that the John they confirmed last
week is still the same John. Without it every run starts by asking again.

What is kept is a mapping, never content. One word the owner used, and what it turned out
to mean - a registered tool name, an application name, or the entity the owner themselves
confirmed. No command text, no arguments, no results, no addresses: a single word cannot
reassemble the sentence it came from, and the label rules that guard the profile refuse it
if it looks like a credential or an opaque identifier.

Three limits make it safe to be wrong. Nothing is written unless the owner switched
learning on. Nothing is written except from a run that finished, because a run that failed
teaches the wrong lesson. And everything written is visible in the memory panel, deleted
one row at a time, and expires on its own.

The confirmed-entity rows never leave this store: they carry an entity id, and an id that
the planner never saw is exactly what it must not be handed. They exist so that
`jarvis.knowledge.resolution` stops asking a question the owner already answered.
"""

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import time
from typing import Literal

from pydantic import Field, model_validator

from jarvis.memory.models import validate_label
from jarvis.memory.store import MemoryFailure
from jarvis.tools.base import ToolModel

Learned = Literal["tool", "application", "entity"]
LEARNED: dict[str, str] = {
    "tool": "Инструмент",
    "application": "Приложение",
    "entity": "Подтверждённое имя",
}

DERIVED_TTL = 30 * 86400
MAX_DERIVED = 64
MAX_CONFIRMED = 16
MAX_ROW = 1024
MAX_DB_BYTES = 1048576

TOOL = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
# Mirrors `AppId` in `jarvis.tools.windows`: a spoken application name, never a path.
APPLICATION = re.compile(r"[0-9A-Za-zЀ-ӿ][0-9A-Za-zЀ-ӿ ._+\-]{0,99}")
ENTITY = re.compile(r"[a-f0-9]{32}")
WORD = re.compile(r"[^\W_]+", re.UNICODE)
MIN_PHRASE = 3


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class Derived(ToolModel):
    """One mapping the assistant learned, with how many finished runs agreed on it."""

    kind: Learned
    phrase: str = Field(repr=False)
    target: str = Field(repr=False)
    runs: int = Field(ge=1, le=9999)
    updated: int = Field(ge=0)
    expires: int = Field(ge=0)

    @model_validator(mode="after")
    def constraints(self) -> "Derived":
        if not 0 < self.expires - self.updated <= DERIVED_TTL:
            raise ValueError("Invalid retention.")
        # The label rules of the profile apply here too: no credentials, no opaque ids.
        if normalize(validate_label(self.phrase)) != self.phrase:
            raise ValueError("A phrase is normalised and ordinary.")
        if len(self.phrase) < MIN_PHRASE:
            raise ValueError("A phrase is too short to mean anything.")
        if self.kind != "entity" and WORD.fullmatch(self.phrase) is None:
            raise ValueError("A learned phrase is a single word.")
        pattern = {"tool": TOOL, "application": APPLICATION, "entity": ENTITY}[self.kind]
        if pattern.fullmatch(self.target) is None:
            raise ValueError("The meaning does not match the kind.")
        return self

    @property
    def key(self) -> str:
        """A phrase means one thing per kind, so the pair is the row's identity."""
        return f"{self.kind}:{self.phrase}"


class DerivedHint(ToolModel):
    """The planner's view: a word and what it meant last time. Never an identifier."""

    kind: Literal["tool", "application"]
    phrase: str
    means: str


class DerivedContext(ToolModel):
    phrases: tuple[DerivedHint, ...] = Field(default=(), max_length=MAX_DERIVED, repr=False)

    @property
    def empty(self) -> bool:
        return not self.phrases


def hint(record: Derived) -> DerivedHint | None:
    """Confirmed names stay home: they carry an id the planner has not observed."""
    if record.kind == "entity":
        return None
    return DerivedHint(kind=record.kind, phrase=record.phrase, means=record.target)


class DerivedStore:
    """Bounded, expiring and cancellable, with the same failure categories as the profile."""

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
                raise MemoryFailure("storage")
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.set_progress_handler(lambda: int(cancelled.is_set()), 100)
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS learned (key TEXT PRIMARY KEY, payload TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
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

    def _absent(self) -> bool:
        """Reading a store nobody has written to must not create one."""
        return not self.path.exists()

    def _rows(self, db: sqlite3.Connection) -> tuple[Derived, ...]:
        now = int(self.clock())
        rows = db.execute("SELECT key, payload FROM learned LIMIT ?", (MAX_DERIVED + 1,)).fetchall()
        if len(rows) > MAX_DERIVED:
            raise MemoryFailure("limit")
        records = []
        for key, payload in rows:
            try:
                if not isinstance(payload, str) or len(payload) > MAX_ROW:
                    raise ValueError
                record = Derived.model_validate_json(payload)
                if record.key != key or record.updated > now:
                    raise ValueError
            except Exception:
                raise MemoryFailure("invalid") from None
            if record.expires <= now:
                db.execute("DELETE FROM learned WHERE key=?", (key,))
            else:
                records.append(record)
        # Best supported first: what many runs agreed on outranks a single coincidence.
        records.sort(key=lambda item: (-item.runs, -item.updated, item.phrase))
        return tuple(records)

    def read(self, cancelled: Event | None = None) -> tuple[Derived, ...]:
        if self._absent():
            return ()
        with self._db(cancelled or Event()) as db:
            return self._rows(db)

    def learning(self, cancelled: Event | None = None) -> bool:
        """Learning is off until the owner turns it on, and stays off if unreadable."""
        if self._absent():
            return False
        try:
            with self._db(cancelled or Event()) as db:
                row = db.execute("SELECT value FROM settings WHERE key='learning'").fetchone()
        except MemoryFailure:
            return False
        return bool(row) and row[0] == "on"

    def set_learning(self, value: bool, cancelled: Event | None = None) -> bool:
        with self._db(cancelled or Event()) as db:
            state = "on" if value else "off"
            db.execute("INSERT OR REPLACE INTO settings VALUES ('learning', ?)", (state,))
            return value

    def record(
        self, kind: Learned, phrase: str, target: str, cancelled: Event | None = None
    ) -> Derived:
        """Write one mapping, or strengthen the one already there."""
        now = int(self.clock())
        try:
            candidate = Derived(
                kind=kind,
                phrase=normalize(phrase),
                target=target,
                runs=1,
                updated=now,
                expires=now + DERIVED_TTL,
            )
        except ValueError:
            raise MemoryFailure("invalid") from None
        with self._db(cancelled or Event()) as db:
            records = self._rows(db)
            existing = next((item for item in records if item.key == candidate.key), None)
            # One word, one meaning: a run that meant something else replaces the row and
            # starts counting again, rather than strengthening a mapping nobody confirmed.
            agreed = existing if existing is not None and existing.target == target else None
            if existing is None:
                if len(records) >= MAX_DERIVED:
                    self._evict(db, records)
                if kind == "entity" and sum(1 for i in records if i.kind == "entity") >= (
                    MAX_CONFIRMED
                ):
                    raise MemoryFailure("limit")
            written = (
                candidate
                if agreed is None
                else Derived(
                    kind=agreed.kind,
                    phrase=agreed.phrase,
                    target=agreed.target,
                    runs=min(agreed.runs + 1, 9999),
                    updated=now,
                    expires=now + DERIVED_TTL,
                )
            )
            db.execute(
                "INSERT OR REPLACE INTO learned VALUES (?, ?)",
                (written.key, written.model_dump_json()),
            )
            return written

    @staticmethod
    def _evict(db: sqlite3.Connection, records: tuple[Derived, ...]) -> None:
        """Make room by dropping the assistant's own weakest guess, never the owner's answer."""
        weakest = [item for item in records if item.kind != "entity"]
        if not weakest:
            raise MemoryFailure("limit")
        loser = min(weakest, key=lambda item: (item.runs, item.updated, item.phrase))
        db.execute("DELETE FROM learned WHERE key=?", (loser.key,))

    def forget(self, previous: Derived, cancelled: Event | None = None) -> tuple[Derived, ...]:
        with self._db(cancelled or Event()) as db:
            records = self._rows(db)
            if previous not in records:
                raise MemoryFailure("conflict")
            db.execute("DELETE FROM learned WHERE key=?", (previous.key,))
            return self._rows(db)

    def clear(self, cancelled: Event | None = None) -> tuple[Derived, ...]:
        with self._db(cancelled or Event()) as db:
            db.execute("DELETE FROM learned")
            return ()

    def entity_for(self, alias: str, cancelled: Event | None = None) -> str | None:
        """The entity the owner already confirmed for this name, if they ever did."""
        try:
            wanted = f"entity:{normalize(alias)}"
            for record in self.read(cancelled):
                if record.key == wanted:
                    return record.target
        except MemoryFailure:
            # An unreadable store asks the owner again; it never guesses on their behalf.
            return None
        return None


def context(records: tuple[Derived, ...]) -> DerivedContext:
    hints = tuple(found for record in records if (found := hint(record)) is not None)
    return DerivedContext(phrases=hints)


def serialize(shown: DerivedContext) -> str:
    return json.dumps(shown.model_dump(mode="json"), ensure_ascii=False)
