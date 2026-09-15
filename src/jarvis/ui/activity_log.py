"""Bounded in-memory activity view; raw commands are not persisted."""

from datetime import datetime

from PySide6.QtWidgets import QListWidget, QWidget

from jarvis.observability.events import EVENT_TEXT, ShellEvent


class ActivityLog(QListWidget):
    def __init__(self, limit: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._limit = limit
        self.setAccessibleName("История действий")
        self.setWordWrap(True)

    def append_event(self, event: ShellEvent) -> None:
        self.insertItem(0, f"{datetime.now():%H:%M:%S}   {EVENT_TEXT[event]}")
        while self.count() > self._limit:
            self.takeItem(self.count() - 1)
