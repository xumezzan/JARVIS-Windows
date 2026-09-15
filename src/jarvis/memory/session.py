"""Explicit per-window context. No automatic ingestion of commands or tool output."""

from collections.abc import Callable
from time import monotonic

from jarvis.memory.models import SESSION_TTL, Hint
from jarvis.memory.store import MemoryFailure


class SessionContext:
    def __init__(self, *, clock: Callable[[], float] = monotonic):
        self.clock = clock
        self._items: list[tuple[float, Hint]] = []

    def read(self) -> tuple[Hint, ...]:
        now = self.clock()
        self._items = [(end, hint) for end, hint in self._items if end > now]
        return tuple(hint for _, hint in self._items)

    def add(self, hint: Hint) -> None:
        hint = Hint.model_validate(hint)
        if len(self.read()) >= 6:
            raise MemoryFailure("limit")
        self._items.append((self.clock() + SESSION_TTL, hint))

    def delete(self, hint: Hint) -> None:
        self.read()
        self._items = [(end, item) for end, item in self._items if item is not hint]

    def clear(self) -> None:
        self._items.clear()
