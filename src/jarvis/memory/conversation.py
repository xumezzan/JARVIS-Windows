"""Bounded, untrusted task recollections and symbolic routes, never execution authority."""

import json
import re
from collections import deque
from collections.abc import Iterable
from time import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import Field, model_validator

from jarvis.memory.models import PROFILE_TTL, SESSION_TTL
from jarvis.tools.base import ToolModel
from jarvis.tools.scratchpad import MAX_SCRATCHPAD_BYTES, MAX_SCRATCHPAD_KEYS, ScratchpadWrite

MAX_RECENT = 5
MAX_PLAYBOOKS = 32
MAX_RECORD_BYTES = 65536
# Defense in depth, not a universal secret detector. Credential adapters never feed this store.
SENSITIVE = re.compile(
    r"password|passwd|secret|token|cookie|credential|bearer|authorization|api[_ -]?key|"
    r"парол|секрет|токен|куки|approval|signature|capability|private.key",
    re.IGNORECASE,
)
OPAQUE_SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\."
    r"[A-Za-z0-9_-]+|-----BEGIN .*PRIVATE KEY-----",
)
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")


def safe_text(text: str) -> str:
    """Drop a credential-bearing field wholesale; never retain a partially masked secret."""
    if SENSITIVE.search(text) or OPAQUE_SECRET.search(text):
        return "[excluded sensitive field]"
    text = EMAIL.sub("[recipient omitted]", text)
    # Query strings/fragments/userinfo often contain session credentials.
    return re.sub(r"https?://[^\s<>\"']+", lambda m: safe_url(m.group()) or "[URL omitted]", text)


def safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        if SENSITIVE.search(parts.path) or OPAQUE_SECRET.search(parts.path):
            return ""
        return urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, "", ""))
    except ValueError:
        return ""


class RecentTask(ToolModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    created: int = Field(ge=0)
    command: str = Field(max_length=4000, repr=False)
    summary: str = Field(max_length=6000, repr=False)
    scratchpad: tuple[ScratchpadWrite, ...] = Field(default=(), max_length=MAX_SCRATCHPAD_KEYS)
    tools: tuple[str, ...] = Field(default=(), max_length=30)
    window_handles: tuple[int, ...] = Field(default=(), max_length=64)
    urls: tuple[str, ...] = Field(default=(), max_length=32)
    paths: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def bounded(self) -> "RecentTask":
        notes = {note.key: note.value for note in self.scratchpad}
        if len(notes) != len(self.scratchpad):
            raise ValueError("Duplicate note.")
        if len(json.dumps(notes, ensure_ascii=False).encode()) > MAX_SCRATCHPAD_BYTES:
            raise ValueError("Notes limit.")
        if len(self.model_dump_json().encode()) > MAX_RECORD_BYTES:
            raise ValueError("Task recollection limit.")
        if any(handle <= 0 for handle in self.window_handles):
            raise ValueError("Invalid handle.")
        return self


class Playbook(ToolModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    created: int = Field(ge=0)
    # Search terms and tool names only: no arguments, selectors, content or identities.
    terms: tuple[str, ...] = Field(min_length=1, max_length=64)
    route: tuple[str, ...] = Field(min_length=2, max_length=30)

    @model_validator(mode="after")
    def bounded(self) -> "Playbook":
        if any(not re.fullmatch(r"[\w-]{1,40}", term) for term in self.terms):
            raise ValueError("Invalid search term.")
        if any(not re.fullmatch(r"[a-z][a-z0-9_.]{0,99}", tool) for tool in self.route):
            raise ValueError("Invalid tool name.")
        return self


class TaskMemoryContext(ToolModel):
    recent: tuple[RecentTask, ...] = Field(default=(), max_length=MAX_RECENT, repr=False)
    playbooks: tuple[Playbook, ...] = Field(default=(), max_length=3, repr=False)


def command_terms(command: str) -> tuple[str, ...]:
    # Deterministic local matching; no embedding service or additional cloud disclosure.
    if command == "[excluded sensitive field]":
        return ()
    command = re.sub(r"https?://\S+|[\"«].*?[\"»]", " ", safe_text(command))
    stop = {"теперь", "пожалуйста", "этот", "этого", "этой", "сделай", "please", "the"}
    return tuple(
        sorted(
            {
                word[:40]
                for word in re.findall(r"[^\W\d_]{3,}", command.casefold())
                if word not in stop
            }
        )
    )[:64]


def make_playbook(task: RecentTask) -> Playbook | None:
    route: list[str] = []
    for tool in task.tools:
        if tool == "memory.scratchpad_write":
            continue
        if (
            route
            and tool == route[-1]
            and tool
            in {
                "windows.screenshot",
                "windows.inspect_active_window",
                "browser.read",
            }
        ):
            continue
        route.append(tool)
    terms = command_terms(task.command)
    if len(route) < 3 or len(set(route)) < 2 or not terms:
        return None
    return Playbook(created=task.created, terms=terms, route=tuple(route))


class ConversationRingBuffer:
    """Newest first; TTL applies to RAM as well as persisted snapshots."""

    def __init__(self) -> None:
        self._items: deque[RecentTask] = deque(maxlen=MAX_RECENT)

    def replace(self, items: Iterable[RecentTask]) -> None:
        self._items = deque(items, maxlen=MAX_RECENT)

    def append(self, task: RecentTask) -> None:
        self._items.appendleft(task)

    def read(self, stamp: int | None = None) -> tuple[RecentTask, ...]:
        stamp = int(time()) if stamp is None else stamp
        self._items = deque(
            (item for item in self._items if 0 <= stamp - item.created < SESSION_TTL),
            maxlen=MAX_RECENT,
        )
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()


def match_playbooks(
    command: str, books: Iterable[Playbook], stamp: int | None = None
) -> tuple[Playbook, ...]:
    stamp = int(time()) if stamp is None else stamp
    terms = {term[:5] for term in command_terms(command)}
    scored: list[tuple[float, Playbook]] = []
    for book in books:
        if not 0 <= stamp - book.created < PROFILE_TTL:
            continue
        known = {term[:5] for term in book.terms}
        shared = terms & known
        score = len(shared) / max(1, len(terms | known))
        if terms == known or (len(shared) >= 2 and score >= 0.45):
            scored.append((score, book))
    return tuple(
        book
        for _, book in sorted(scored, key=lambda row: (row[0], row[1].created), reverse=True)[:3]
    )
