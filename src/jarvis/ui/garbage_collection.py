"""Collect cyclic Qt wrappers on the GUI thread, never from planner allocations."""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Slot


class GuiGarbageCollector(QObject):
    """Close only after all GUI-owned workers have drained."""

    def __init__(self, application: QCoreApplication) -> None:
        if QThread.currentThread() != application.thread():
            raise RuntimeError("GUI garbage collector must be created on the GUI thread.")
        super().__init__(application)
        self.previous = gc.isenabled()
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.collect)
        gc.disable()
        self.timer.start()

    @Slot()
    def collect(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Qt cycle collection must run on the GUI thread.")
        if not self.closed:
            gc.collect()

    def close(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Qt cycle collector must close on the GUI thread.")
        if self.closed:
            return
        self.timer.stop()
        try:
            self.collect()
        finally:
            self.closed = True
            if self.previous:
                gc.enable()
