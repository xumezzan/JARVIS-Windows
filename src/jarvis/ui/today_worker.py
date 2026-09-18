"""Today's appointments, read once, off the interface thread.

The right-hand column says what the day holds, and there is only one honest way to fill it:
ask the calendar. That is a network call through the permission engine, and doing it on the
thread that draws the window would freeze the window for as long as the service takes.

Nothing here decides anything and no approval authority crosses this boundary. The read is
refused unless the prepared action is SAFE - the same rule the briefing walk applies - so a
capability the owner tightened in their matrix is refused here exactly as it is everywhere
else. A failure is an empty day on the screen with a line saying the calendar was not read,
never an invented meeting.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from PySide6.QtCore import QThread

from jarvis.connectors.instants import render
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.registry import ToolRegistry

TOOL = "calendar.list"
MAX_EVENTS = 6


def rows(payload: str | None) -> list[dict[str, Any]]:
    """The list a tool result carries, or nothing at all. A malformed answer is nothing."""
    try:
        outer = json.loads(payload or "null")
        inner = json.loads(outer.get("data") or "null") if isinstance(outer, dict) else None
    except ValueError:
        return []
    if isinstance(inner, dict):
        inner = [inner]
    return [row for row in inner if isinstance(row, dict)] if isinstance(inner, list) else []


class TodayWorker(QThread):
    """One bounded read of the rest of the day."""

    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        account: dict[str, object],
        now: datetime | None = None,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.engine = engine
        self.account = account
        self.now = now or datetime.now(UTC)
        self.events: tuple[dict[str, Any], ...] = ()
        self.read = False

    def run(self) -> None:
        try:
            self.events = tuple(asyncio.run(self._read())[:MAX_EVENTS])
            self.read = True
        except Exception:
            # Trusted code, but a calendar that breaks must not take the window with it.
            self.read = False

    async def _read(self) -> list[dict[str, Any]]:
        if self.registry.get(TOOL) is None:
            return []
        # Until the end of the local day: what is left of today, not the next twenty-four
        # hours, because a card headed "Сегодня" that shows tomorrow morning is lying.
        local = self.now.astimezone()
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        action = self.engine.prepare(
            TOOL,
            {
                "account": self.account,
                "start": render(self.now),
                "end": render(midnight),
                "limit": MAX_EVENTS,
            },
            Mode.EXECUTE,
        )
        if isinstance(action, Outcome):
            return []
        if action.risk is not Risk.SAFE:
            # Showing the day is looking at it. Anything that changes something is not that.
            self.engine.cancel(action)
            return []
        outcome = await self.engine.execute(action)
        if outcome.status is not Status.SUCCESS:
            return []
        return [row for row in rows(outcome.result_json) if not row.get("cancelled")]
