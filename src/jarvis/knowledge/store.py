"""Bounded SQLite knowledge graph with atomic edits and finite failure categories.

An external identifier belongs to exactly one entity. That is what makes the store a graph
rather than a pile of copies: the second time a connector observes the same Asana user, it
merges into the entity that already holds that identifier instead of creating a twin.
Merging is driven by identifiers only. A matching name is a suggestion for
`jarvis.knowledge.resolution`, never an automatic merge — two people share a name.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import time
from typing import Literal
from uuid import uuid4

from jarvis.knowledge.models import (
    MAX_ALIASES,
    MAX_HINTS,
    MAX_REFERENCES,
    Entity,
    EntityDraft,
    EntityType,
    KnowledgeContext,
    Predicate,
    Reference,
    Relationship,
)

MAX_ENTITIES = 2000
MAX_LINKS = 8000
MAX_DB_BYTES = 16 * 1024 * 1024
MAX_ROW = 8192


class KnowledgeFailure(Exception):
    def __init__(
        self, code: Literal["storage", "invalid", "limit", "cancelled", "conflict", "unknown"]
    ) -> None:
        self.code = code
        super().__init__(code)


class KnowledgeStore:
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
                raise KnowledgeFailure("storage")
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.set_progress_handler(lambda: int(cancelled.is_set()), 100)
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, payload TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS external (key TEXT PRIMARY KEY, entity TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS links (key TEXT PRIMARY KEY, payload TEXT)")
            yield db
            self._check(cancelled)
            db.commit()
        except KnowledgeFailure:
            raise
        except Exception:
            raise KnowledgeFailure("cancelled" if cancelled.is_set() else "storage") from None
        finally:
            if db is not None:
                db.close()

    def _absent(self) -> bool:
        """A graph nobody has written to yet. Reading one must not create a database."""
        return not self.path.exists()

    @staticmethod
    def _check(cancelled: Event) -> None:
        if cancelled.is_set():
            raise KnowledgeFailure("cancelled")

    @staticmethod
    def _entity(key: str, payload: object) -> Entity:
        try:
            if not isinstance(payload, str) or len(payload) > MAX_ROW:
                raise ValueError
            entity = Entity.model_validate_json(payload)
            if entity.id != key:
                raise ValueError
        except Exception:
            raise KnowledgeFailure("invalid") from None
        return entity

    def _read(self, db: sqlite3.Connection, entity_id: str) -> Entity | None:
        row = db.execute("SELECT id, payload FROM entities WHERE id=?", (entity_id,)).fetchone()
        return None if row is None else self._entity(row[0], row[1])

    def _all(self, db: sqlite3.Connection) -> tuple[Entity, ...]:
        rows = db.execute(
            "SELECT id, payload FROM entities LIMIT ?", (MAX_ENTITIES + 1,)
        ).fetchall()
        if len(rows) > MAX_ENTITIES:
            raise KnowledgeFailure("limit")
        return tuple(self._entity(key, payload) for key, payload in rows)

    def _store(self, db: sqlite3.Connection, entity: Entity) -> Entity:
        payload = entity.model_dump_json()
        if len(payload) > MAX_ROW:
            raise KnowledgeFailure("limit")
        db.execute("INSERT OR REPLACE INTO entities VALUES (?, ?)", (entity.id, payload))
        for reference in entity.external:
            owner = db.execute(
                "SELECT entity FROM external WHERE key=?", (reference.key,)
            ).fetchone()
            if owner is not None and owner[0] != entity.id:
                # One identifier, one entity: a second owner would silently merge strangers.
                raise KnowledgeFailure("conflict")
            db.execute("INSERT OR REPLACE INTO external VALUES (?, ?)", (reference.key, entity.id))
        return entity

    def upsert(self, draft: EntityDraft, cancelled: Event | None = None) -> Entity:
        """Merge by external identifier; otherwise create. Names never merge on their own."""
        try:
            draft = EntityDraft.model_validate(draft, strict=True)
        except Exception:
            raise KnowledgeFailure("invalid") from None
        now = int(self.clock())
        with self._db(cancelled or Event()) as db:
            owners = {
                row[0]
                for reference in draft.external
                for row in db.execute(
                    "SELECT entity FROM external WHERE key=?", (reference.key,)
                ).fetchall()
            }
            if len(owners) > 1:
                raise KnowledgeFailure("conflict")
            current = self._read(db, owners.pop()) if owners else None
            if current is None:
                count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                if count >= MAX_ENTITIES:
                    raise KnowledgeFailure("limit")
                return self._store(
                    db,
                    Entity(
                        id=uuid4().hex,
                        type=draft.type,
                        name=draft.name,
                        aliases=draft.aliases,
                        external=draft.external,
                        updated=now,
                    ),
                )
            if current.type != draft.type:
                raise KnowledgeFailure("conflict")
            return self._store(db, self._merge(current, draft, now))

    @staticmethod
    def _merge(current: Entity, draft: EntityDraft, now: int) -> Entity:
        """The established name wins; a newly observed one is kept as an alias."""
        known = {current.name.casefold(), *(alias.casefold() for alias in current.aliases)}
        aliases = list(current.aliases)
        for candidate in (draft.name, *draft.aliases):
            if candidate.casefold() not in known and len(aliases) < MAX_ALIASES:
                aliases.append(candidate)
                known.add(candidate.casefold())
        references = list(current.external)
        seen = {reference.key for reference in references}
        for reference in draft.external:
            if reference.key not in seen and len(references) < MAX_REFERENCES:
                references.append(reference)
                seen.add(reference.key)
        return Entity(
            id=current.id,
            type=current.type,
            name=current.name,
            aliases=tuple(aliases),
            external=tuple(references),
            updated=now,
        )

    def attach(self, entity_id: str, draft: EntityDraft, cancelled: Event | None = None) -> Entity:
        """Merge an observation into an entity the owner confirmed as the same one.

        This is the only path where a name match becomes a link, and it exists because
        the decision belongs to the owner rather than to a similarity score.
        """
        try:
            draft = EntityDraft.model_validate(draft, strict=True)
        except Exception:
            raise KnowledgeFailure("invalid") from None
        now = int(self.clock())
        with self._db(cancelled or Event()) as db:
            current = self._read(db, entity_id)
            if current is None:
                raise KnowledgeFailure("unknown")
            if current.type != draft.type:
                raise KnowledgeFailure("conflict")
            return self._store(db, self._merge(current, draft, now))

    def get(self, entity_id: str, cancelled: Event | None = None) -> Entity | None:
        if self._absent():
            return None
        with self._db(cancelled or Event()) as db:
            return self._read(db, entity_id)

    def by_reference(
        self, service: str, value: str, cancelled: Event | None = None
    ) -> Entity | None:
        if self._absent():
            return None
        with self._db(cancelled or Event()) as db:
            row = db.execute(
                "SELECT entity FROM external WHERE key=?", (f"{service}:{value}",)
            ).fetchone()
            return None if row is None else self._read(db, row[0])

    def entities(self, cancelled: Event | None = None) -> tuple[Entity, ...]:
        """Everything known, bounded by MAX_ENTITIES.

        Context assembly scores candidates itself, so the matching rule lives in one place
        rather than half here and half there. If the cap ever rises far above a few
        thousand, this is where a term index belongs.
        """
        if self._absent():
            return ()
        with self._db(cancelled or Event()) as db:
            return self._all(db)

    def search(
        self,
        text: str,
        kind: EntityType | None = None,
        limit: int = 10,
        cancelled: Event | None = None,
    ) -> tuple[Entity, ...]:
        needle = text.strip().casefold()
        if not needle or self._absent():
            return ()
        with self._db(cancelled or Event()) as db:
            found = [
                entity
                for entity in self._all(db)
                if (kind is None or entity.type == kind)
                and any(needle in label.casefold() for label in (entity.name, *entity.aliases))
            ]
        found.sort(key=lambda entity: (-entity.updated, entity.name))
        return tuple(found[: max(1, min(limit, 50))])

    def relate(
        self,
        subject: str,
        predicate: Predicate,
        object_id: str,
        observed: str,
        run: str,
        cancelled: Event | None = None,
    ) -> Relationship:
        now = int(self.clock())
        try:
            link = Relationship(
                subject=subject,
                predicate=predicate,
                object=object_id,
                observed=observed,
                run=run,
                updated=now,
            )
        except Exception:
            raise KnowledgeFailure("invalid") from None
        if link.subject == link.object:
            raise KnowledgeFailure("invalid")
        with self._db(cancelled or Event()) as db:
            if self._read(db, link.subject) is None or self._read(db, link.object) is None:
                raise KnowledgeFailure("unknown")
            count = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
            existing = db.execute("SELECT key FROM links WHERE key=?", (link.key,)).fetchone()
            if existing is None and count >= MAX_LINKS:
                raise KnowledgeFailure("limit")
            db.execute(
                "INSERT OR REPLACE INTO links VALUES (?, ?)", (link.key, link.model_dump_json())
            )
        return link

    def links(
        self, entity_id: str, limit: int = 50, cancelled: Event | None = None
    ) -> tuple[Relationship, ...]:
        if self._absent():
            return ()
        with self._db(cancelled or Event()) as db:
            rows = db.execute(
                "SELECT payload FROM links WHERE key LIKE ? OR key LIKE ? LIMIT ?",
                (f"{entity_id}:%", f"%:{entity_id}", max(1, min(limit, MAX_LINKS))),
            ).fetchall()
        found = []
        for (payload,) in rows:
            try:
                if not isinstance(payload, str) or len(payload) > MAX_ROW:
                    raise ValueError
                found.append(Relationship.model_validate_json(payload))
            except Exception:
                raise KnowledgeFailure("invalid") from None
        return tuple(found)

    def context(self, limit: int = 8, cancelled: Event | None = None) -> KnowledgeContext:
        """The planner's view of what is known. Identifiers stay behind in the store."""
        if self._absent():
            return KnowledgeContext()
        with self._db(cancelled or Event()) as db:
            entities = sorted(self._all(db), key=lambda entity: -entity.updated)
        return KnowledgeContext(
            entities=tuple(entity.hint() for entity in entities[: max(0, min(limit, MAX_HINTS))])
        )

    def forget(self, entity_id: str, cancelled: Event | None = None) -> None:
        with self._db(cancelled or Event()) as db:
            if self._read(db, entity_id) is None:
                raise KnowledgeFailure("unknown")
            db.execute("DELETE FROM entities WHERE id=?", (entity_id,))
            db.execute("DELETE FROM external WHERE entity=?", (entity_id,))
            db.execute(
                "DELETE FROM links WHERE key LIKE ? OR key LIKE ?",
                (f"{entity_id}:%", f"%:{entity_id}"),
            )

    def clear(self, cancelled: Event | None = None) -> None:
        with self._db(cancelled or Event()) as db:
            for table in ("entities", "external", "links"):
                db.execute(f"DELETE FROM {table}")


def reference(service: str, value: str, observed: str, run: str) -> Reference:
    """Build a provenance-carrying identifier; raises rather than storing an unsourced id."""
    return Reference(service=service, value=value, observed=observed, run=run)
