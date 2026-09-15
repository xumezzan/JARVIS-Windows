"""Reusable native dashboard components with semantic roles and keyboard access."""

import calendar
from datetime import date

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from jarvis.ui.dashboard import line_icon


def text_label(text: str, role: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(role)
    return widget


class Panel(QFrame):
    """One heading baseline and one spacing scale for dashboard panels."""

    def __init__(self, title: str, caption: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 20, 24, 20)
        self.body.setSpacing(16)
        heading = QHBoxLayout()
        title_label = text_label(title, "cardTitle")
        title_label.setWordWrap(False)
        heading.addWidget(title_label, 1)
        if caption:
            caption_label = text_label(caption, "muted")
            caption_label.setWordWrap(False)
            heading.addWidget(caption_label)
        self.body.addLayout(heading)


class IconButton(QPushButton):
    def __init__(self, icon_name: str, accessible_name: str, role: str = "round") -> None:
        super().__init__()
        self.setObjectName(role)
        self.setIcon(line_icon(icon_name, "#b8d6fa", 28))
        self.setIconSize(QSize(24, 24))
        self.setFixedSize(48, 48)
        self.setAccessibleName(accessible_name)
        self.setToolTip(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class MonthCalendar(QFrame):
    """An actual local month, with no invented appointments or connected account."""

    def __init__(self, today: date | None = None) -> None:
        super().__init__()
        self.setObjectName("monthCalendar")
        today = today or date.today()
        months = (
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
        self.setAccessibleName(f"Календарь: {months[today.month - 1]} {today.year}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(text_label(f"{months[today.month - 1]} {today.year}", "monthTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)
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
                cell.setFixedHeight(28)
                if day:
                    cell.setAccessibleName(f"{day:02d}.{today.month:02d}.{today.year}")
                grid.addWidget(cell, row, column)
        layout.addLayout(grid)
