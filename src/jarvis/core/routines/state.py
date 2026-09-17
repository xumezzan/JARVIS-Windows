"""Which routines the owner switched on, and how much they may run today.

Three switches, in order of authority. Nothing runs unless the owner turned routines on at
all; then each routine is enabled one at a time; and only then may a routine be allowed to
carry out the reversible work it suggests. Every one of them is off in a fresh installation,
so an update never starts doing something on its own.

The day's budget is here rather than in the journal because the journal records what
happened in the world, and a refused cycle did nothing. Keeping the count next to the
switches also means a restart cannot hand a routine a fresh allowance.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import time

DAY_SECONDS = 86400
DEFAULT_BUDGET = 48
MAX_BYTES = 65536
MAX_ROUTINES = 32


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    acts: bool = False


@dataclass
class _Routine:
    enabled: bool = False
    acts: bool = False
    last: float = 0.0


class RoutineState:
    def __init__(
        self,
        path: Path,
        *,
        budget: int = DEFAULT_BUDGET,
        clock: Callable[[], float] = time,
    ) -> None:
        if not 0 < budget <= 1000:
            raise ValueError("Invalid daily routine budget.")
        self.path = path
        self.budget = budget
        self.clock = clock
        self.unsaved = False
        self._lock = RLock()
        self._active = False
        self._routines: dict[str, _Routine] = {}
        self._day = self._today()
        self._runs = 0
        self._load()

    def _today(self) -> int:
        return int(self.clock() // DAY_SECONDS)

    def _load(self) -> None:
        """An unreadable file leaves everything off: nothing runs on a guess."""
        try:
            if not self.path.exists() or self.path.is_symlink():
                return
            if self.path.stat().st_size > MAX_BYTES:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self._active = data.get("active") is True
            self._day = int(data.get("day", self._day))
            self._runs = max(0, min(int(data.get("runs", 0)), self.budget))
            routines = data.get("routines")
            if isinstance(routines, dict):
                for name, value in list(routines.items())[:MAX_ROUTINES]:
                    if isinstance(name, str) and isinstance(value, dict):
                        self._routines[name] = _Routine(
                            enabled=value.get("enabled") is True,
                            acts=value.get("acts") is True,
                            last=float(value.get("last", 0.0)),
                        )
        except (OSError, ValueError, TypeError):
            self._active = False
            self._routines = {}

    def save(self) -> None:
        """A failure to store the switches never stops the session; the panel says so."""
        with self._lock:
            payload = {
                "active": self._active,
                "day": self._day,
                "runs": self._runs,
                "routines": {
                    name: {"enabled": item.enabled, "acts": item.acts, "last": item.last}
                    for name, item in self._routines.items()
                },
            }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.unsaved = False
        except OSError:
            self.unsaved = True

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def set_active(self, value: bool) -> None:
        """The one switch that stops everything, including a routine mid-schedule."""
        with self._lock:
            self._active = bool(value)
        self.save()

    def settings(self, name: str) -> Settings:
        with self._lock:
            item = self._routines.get(name, _Routine())
            return Settings(enabled=item.enabled, acts=item.acts)

    def enable(self, name: str, value: bool) -> None:
        with self._lock:
            item = self._routines.setdefault(name, _Routine())
            item.enabled = bool(value)
        self.save()

    def allow_acting(self, name: str, value: bool) -> None:
        """Permission to carry out reversible work unattended, for this routine only."""
        with self._lock:
            item = self._routines.setdefault(name, _Routine())
            item.acts = bool(value)
        self.save()

    def due(self, name: str, every_seconds: int) -> bool:
        with self._lock:
            item = self._routines.get(name, _Routine())
            return self.clock() - item.last >= max(1, every_seconds)

    def run_soon(self, name: str) -> None:
        """The owner asked for a look now, so the next cycle stops waiting for the schedule."""
        with self._lock:
            self._routines.setdefault(name, _Routine()).last = 0.0
        self.save()

    def mark(self, name: str) -> None:
        with self._lock:
            self._routines.setdefault(name, _Routine()).last = self.clock()
        self.save()

    def spend(self) -> bool:
        """Count one cycle against the day. A refusal is durable: it survives a restart."""
        with self._lock:
            today = self._today()
            if today != self._day:
                self._day, self._runs = today, 0
            if self._runs >= self.budget:
                return False
            self._runs += 1
        self.save()
        return True

    @property
    def spent(self) -> int:
        with self._lock:
            return 0 if self._today() != self._day else self._runs

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.budget
