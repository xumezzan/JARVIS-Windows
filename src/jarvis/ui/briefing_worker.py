"""One briefing, gathered off the interface thread.

The walk itself is five reads through the permission engine, and every one of them is a
network call. Doing that on the thread that draws the window would freeze it for as long as
the slowest service takes, which is exactly when the owner most wants to see progress.

No approval authority crosses this boundary, and nothing here decides anything: the worker
carries a briefer, an account and a cancellation flag, and hands back what was found.
"""

import asyncio
from threading import Event

from PySide6.QtCore import QThread

from jarvis.core.meeting import Briefer, Briefing


class BriefingWorker(QThread):
    def __init__(self, briefer: Briefer, account: dict[str, object]) -> None:
        super().__init__()
        self.briefer = briefer
        self.account = account
        self.cancelled = Event()
        self.briefing: Briefing | None = None
        self.error = ""

    def cancel(self) -> None:
        """Stop between reads. A read already issued is a read that already happened."""
        self.cancelled.set()

    def run(self) -> None:
        try:
            self.briefing = asyncio.run(self.briefer.prepare(self.account, self.cancelled))
        except Exception:
            # Trusted code, but a briefing that breaks must not take the session with it.
            self.error = "briefing_unavailable"
