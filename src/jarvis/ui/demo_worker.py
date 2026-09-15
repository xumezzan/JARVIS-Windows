"""A bounded, cancellable demonstration thread with no command execution capability."""

from enum import StrEnum
from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, QThread, Signal


class DemoOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class DemoWorker(QThread):
    """Only waits and emits a progress signal. User text is never passed to this worker."""

    executing = Signal()

    def __init__(
        self, duration_ms: int, timeout_ms: int, fail: bool, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._duration = duration_ms / 1000
        self._timeout = timeout_ms / 1000
        self._fail = fail
        self._cancel = Event()
        self.outcome = DemoOutcome.ERROR

    def cancel(self) -> None:
        self._cancel.set()

    def _pause(self, seconds: float, deadline: float) -> bool:
        if self._cancel.wait(max(0, min(seconds, deadline - monotonic()))):
            self.outcome = DemoOutcome.CANCELLED
            return False
        if monotonic() >= deadline:
            self.outcome = DemoOutcome.TIMEOUT
            return False
        return True

    def run(self) -> None:
        try:
            deadline = monotonic() + self._timeout
            if not self._pause(self._duration / 4, deadline):
                return
            self.executing.emit()
            if not self._pause(self._duration * 3 / 4, deadline):
                return
            self.outcome = DemoOutcome.ERROR if self._fail else DemoOutcome.SUCCESS
        except Exception:
            # No exception content crosses into logs, UI, or a model context.
            self.outcome = DemoOutcome.ERROR
