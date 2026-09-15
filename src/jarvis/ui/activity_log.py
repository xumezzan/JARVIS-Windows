"""Bounded in-memory activity view; raw commands are not persisted."""

from datetime import datetime

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.ui.dashboard import line_icon


class ActivityLog(QListWidget):
    def __init__(self, limit: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._limit = limit
        self.setAccessibleName("История действий")
        self.setWordWrap(True)
        self.setIconSize(QSize(30, 30))
        self.setSpacing(2)

    def append_event(self, event: ShellEvent) -> None:
        icon_name, color = {
            ShellEvent.STARTED: ("home", "#84b9ff"),
            ShellEvent.SUBMITTED: ("chat", "#a597ed"),
            ShellEvent.EXECUTING: ("clock", "#84b9ff"),
            ShellEvent.SUCCEEDED: ("check", "#70e2c7"),
        }.get(event, ("tasks", "#b6b9cf"))
        item = QListWidgetItem(
            line_icon(icon_name, color, 30),
            f"{datetime.now():%H:%M}\n{EVENT_TEXT[event]}",
        )
        item.setSizeHint(QSize(0, 104))
        self.insertItem(0, item)
        while self.count() > self._limit:
            self.takeItem(self.count() - 1)
