"""Child process for the worker-lifetime test: a panel deleted while its job still runs.

The failure it guards against kills the interpreter outright, so it cannot be observed
from inside a test process - only a parent watching this one exit can tell.
"""

import gc
import sys
import time
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from jarvis.memory.models import Entry
from jarvis.memory.store import MemoryStore
from jarvis.ui import workers
from jarvis.ui.memory_panel import MemoryPanel

SLOW_READ_SECONDS = 1.5


class SlowStore(MemoryStore):
    """Holds the profile read open long enough for the widget to be deleted under it."""

    def read(self, cancelled: Event | None = None) -> tuple[Entry, ...]:
        time.sleep(SLOW_READ_SECONDS)
        return super().read(cancelled)


def main() -> int:
    application = QApplication(["worker-lifetime"])
    panel = MemoryPanel(SlowStore(Path(mkdtemp()) / "memory.json"))
    # Opening the panel reads the profile; the read is still in flight below.
    application.processEvents()
    if panel.worker is None or not panel.worker.isRunning():
        print("the panel did not start its read")
        return 2
    # What a torn-down window does and what the panel has no way to refuse: closing is
    # declined while a worker runs, deletion is not.
    panel.close()
    panel.deleteLater()
    del panel
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    print("deleted")
    if workers.pending() != 1:
        print("the run was released while it was still running")
        return 3
    if not workers.drain():
        print("the read did not finish")
        return 4
    print("drained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
