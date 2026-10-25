"""Reusable native dashboard components with semantic roles and keyboard access."""

import calendar
from datetime import date

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui.dashboard import line_icon

MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def text_label(text: str, role: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(role)
    return widget


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    """An outline icon as a label, for the places where the icon is not itself a control."""
    widget = QLabel()
    widget.setPixmap(line_icon(name, color, size).pixmap(size, size))
    widget.setFixedSize(size, size)
    return widget


class StatusDot(QLabel):
    """One coloured dot with a spoken name, because colour alone says nothing out loud."""

    def __init__(self, tone: str = "off", meaning: str = "") -> None:
        super().__init__()
        self.setObjectName("dot")
        self.setFixedSize(8, 8)
        self.set_tone(tone, meaning)

    def set_tone(self, tone: str, meaning: str = "") -> None:
        self.setProperty("tone", tone)
        if meaning:
            self.setAccessibleName(meaning)
            self.setToolTip(meaning)
        self.style().unpolish(self)
        self.style().polish(self)


class Panel(QFrame):
    """One heading baseline and one spacing scale for dashboard panels.

    The heading can carry an icon and one action on the right - "весь журнал", "все
    события" - because a panel that shows part of something needs a way to the whole of it.
    """

    def __init__(self, title: str, caption: str = "", icon: str = "", action: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(14)
        # Kept reachable: a panel that counts something puts the count in its own heading.
        self.heading = QHBoxLayout()
        heading = self.heading
        heading.setSpacing(8)
        if icon:
            heading.addWidget(icon_label(icon, "#7fb2f5", 17))
        title_label = text_label(title, "cardTitle")
        title_label.setWordWrap(False)
        heading.addWidget(title_label)
        heading.addStretch(1)
        self.caption_label: QLabel | None = None
        if caption:
            self.caption_label = text_label(caption, "sectionHint")
            self.caption_label.setWordWrap(False)
            heading.addWidget(self.caption_label)
        self.action_button: QPushButton | None = None
        if action:
            self.action_button = QPushButton(action)
            self.action_button.setObjectName("link")
            self.action_button.setIcon(line_icon("chevron", "#5b9dff", 14))
            self.action_button.setIconSize(QSize(12, 12))
            self.action_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.setAccessibleName(f"{action} — {title}")
            heading.addWidget(self.action_button)
        self.body.addLayout(heading)


class IconButton(QPushButton):
    def __init__(self, icon_name: str, accessible_name: str, role: str = "round") -> None:
        super().__init__()
        self.setObjectName(role)
        self.setIcon(line_icon(icon_name, "#b8d6fa", 28))
        self.setIconSize(QSize(18, 18))
        self.setFixedSize(36, 36)
        self.setAccessibleName(accessible_name)
        self.setToolTip(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class TileButton(QPushButton):
    """A card that is a button: icon, what it does, and what it will do it to."""

    def __init__(self, icon_name: str, title: str, caption: str, color: str = "#7fb2f5") -> None:
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(60)
        self.setAccessibleName(f"{title} — {caption}")
        self.setToolTip(caption)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        row.addWidget(icon_label(icon_name, color, 20))
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.title_label = text_label(title, "tileTitle")
        self.title_label.setWordWrap(False)
        self.caption_label = text_label(caption, "tileCaption")
        self.caption_label.setWordWrap(False)
        column.addWidget(self.title_label)
        column.addWidget(self.caption_label)
        row.addLayout(column, 1)
        row.addWidget(icon_label("chevron", "#54637a", 14))


class AppTile(QPushButton):
    """One connected - or plainly unconnected - service, small enough to scan a grid of."""

    def __init__(self, icon_name: str, name: str, color: str = "#7fb2f5") -> None:
        super().__init__()
        self.setObjectName("appTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(62)
        self.name = name
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 8, 4, 8)
        column.setSpacing(5)
        self.badge = icon_label(icon_name, color, 20)
        column.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        self.name_label = text_label(name, "appName")
        self.name_label.setWordWrap(False)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.name_label)
        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        """The tile says which it is. A service that is not connected never looks connected."""
        self.setProperty("connected", "true" if connected else "false")
        state = "подключено" if connected else "не подключено"
        self.setAccessibleName(f"{self.name} — {state}")
        self.setToolTip(
            f"{self.name} — подключено"
            if connected
            else f"{self.name} — не подключено. Открыть настройку подключений."
        )
        self.style().unpolish(self)
        self.style().polish(self)


class EventRow(QWidget):
    """One appointment: when, what, and where it is - nothing invented around it."""

    def __init__(self, time_text: str, title: str, place: str, tone: str = "busy") -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        dot = StatusDot(tone)
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        time_label = text_label(time_text, "eventTime")
        time_label.setWordWrap(False)
        time_label.setFixedWidth(42)
        row.addWidget(time_label, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(text_label(title, "eventTitle"))
        if place:
            column.addWidget(text_label(place, "eventPlace"))
        row.addLayout(column, 1)
        self.setAccessibleName(f"{time_text} {title} {place}".strip())


class MonthCalendar(QFrame):
    """An actual local month, with no invented appointments or connected account."""

    def __init__(self, today: date | None = None) -> None:
        super().__init__()
        self.setObjectName("monthCalendar")
        today = today or date.today()
        self.setAccessibleName(f"Календарь: {MONTHS[today.month - 1]} {today.year}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
        for column, name in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")):
            weekday = text_label(name, "calendarWeekday")
            weekday.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(weekday, 0, column)
            grid.setColumnStretch(column, 1)
        for row, week in enumerate(calendar.monthcalendar(today.year, today.month), 1):
            for column, day in enumerate(week):
                role = "calendarToday" if day == today.day else "calendarDay"
                cell = text_label(str(day) if day else "", role)
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setFixedHeight(24)
                if day:
                    cell.setAccessibleName(f"{day:02d}.{today.month:02d}.{today.year}")
                grid.addWidget(cell, row, column)
        layout.addLayout(grid)
