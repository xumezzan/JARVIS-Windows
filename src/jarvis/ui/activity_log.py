"""Bounded in-memory activity view; raw commands are not persisted."""

from datetime import datetime

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.ui.dashboard import line_icon

# What each event looks like in the record: an icon and a colour that carry the same
# meaning as the words beside them, so the column can be skimmed rather than read.
MARKS: dict[ShellEvent, tuple[str, str]] = {
    ShellEvent.STARTED: ("home", "#7fb2f5"),
    ShellEvent.SUBMITTED: ("chat", "#b9a8f0"),
    ShellEvent.EXECUTING: ("clock", "#7fb2f5"),
    ShellEvent.SUCCEEDED: ("check", "#61d6b4"),
    ShellEvent.FAILED: ("close", "#ff9db0"),
    ShellEvent.TIMED_OUT: ("clock", "#e5c07b"),
    ShellEvent.CANCEL_REQUESTED: ("close", "#e5c07b"),
    ShellEvent.CANCELLED: ("close", "#e5c07b"),
    ShellEvent.CLOSED: ("tasks", "#8496ab"),
}


def split(text: str) -> tuple[str, str]:
    """The first sentence as the heading, whatever follows as the detail under it.

    The texts are a closed set in `observability/events.py` - arbitrary user input never
    reaches a log line - so splitting them on the sentence is a formatting decision rather
    than parsing something that could surprise us.
    """
    head, _, rest = text.partition(". ")
    return (head, rest.strip()) if rest else (text.rstrip("."), "")


class ActivityLog(QListWidget):
    def __init__(self, limit: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._limit = limit
        self.setAccessibleName("История действий")
        self.setWordWrap(True)
        self.setIconSize(QSize(22, 22))
        self.setSpacing(2)

    def append_event(self, event: ShellEvent) -> None:
        icon_name, color = MARKS.get(event, ("tasks", "#8496ab"))
        heading, detail = split(EVENT_TEXT[event])
        item = QListWidgetItem(
            line_icon(icon_name, color, 22),
            f"{datetime.now():%H:%M}  ·  {heading}" + (f"\n{detail}" if detail else ""),
        )
        item.setToolTip(EVENT_TEXT[event])
        item.setSizeHint(QSize(0, 72 if detail else 44))
        self.insertItem(0, item)
        while self.count() > self._limit:
            self.takeItem(self.count() - 1)
