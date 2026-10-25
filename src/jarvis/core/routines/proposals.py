"""The queue of reasons waiting for the owner.

It is deliberately not a notification channel. A routine that found nothing adds nothing,
and a routine that found the same thing twice adds it once: an assistant that speaks every
five minutes is turned off within a day, and then it notices nothing at all.

Everything here expires. A reason is about a moment - a meeting that starts soon, a letter
that just arrived - so a suggestion nobody opened stops being true and leaves. The queue
lives in memory for the same reason, and because a waiting suggestion holds the owner's own
words: the durable journal keeps identities and outcomes, never content.
"""

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from time import monotonic

from jarvis.core.routines.contracts import MAX_SUGGESTIONS, SUGGESTION_TTL, Suggestion


@dataclass(frozen=True)
class _Entry:
    suggestion: Suggestion
    created: float


class SuggestionQueue:
    def __init__(
        self,
        *,
        ttl: float = SUGGESTION_TTL,
        limit: int = MAX_SUGGESTIONS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 0 < ttl <= 86400 or not 0 < limit <= MAX_SUGGESTIONS:
            raise ValueError("Invalid suggestion queue bounds.")
        self._ttl = ttl
        self._limit = limit
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = RLock()

    def add(self, suggestion: Suggestion) -> bool:
        """Queue one reason. The same reason queued again changes nothing and says so."""
        with self._lock:
            self._purge()
            if suggestion.key in self._entries:
                return False
            if len(self._entries) >= self._limit:
                # The oldest reason is the one most likely to have gone stale already.
                self._entries.pop(next(iter(self._entries)))
            self._entries[suggestion.key] = _Entry(suggestion, self._clock())
            return True

    def refresh(self, routine: str, keys: frozenset[str]) -> None:
        """Drop this routine's reasons that its latest look no longer sees.

        The world moved: the meeting was cancelled, the agenda changed, the letter was
        read. A suggestion whose reason is gone must not still be waiting to be acted on.
        """
        with self._lock:
            self._purge()
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if entry.suggestion.routine != routine or key in keys
            }

    def pending(self) -> tuple[Suggestion, ...]:
        with self._lock:
            self._purge()
            return tuple(entry.suggestion for entry in self._entries.values())

    def get(self, key: str) -> Suggestion | None:
        with self._lock:
            self._purge()
            entry = self._entries.get(key)
            return None if entry is None else entry.suggestion

    def dismiss(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _purge(self) -> None:
        now = self._clock()
        self._entries = {
            key: entry for key, entry in self._entries.items() if entry.created + self._ttl > now
        }
