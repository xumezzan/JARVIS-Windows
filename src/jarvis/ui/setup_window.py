"""The screen a new machine opens with: what is connected, and how to connect the rest.

An assistant that can reach nothing looks exactly like an assistant that is broken. On a
fresh laptop Jarvis has no model key, no mailbox and no tokens, and until now it said none
of that: the window came up empty and the setup lived in a tab of another window and, for
most services, in a terminal command nobody had been told about.

This window is the answer to that, and it is deliberately a list rather than a wizard. The
owner connects what they need in whatever order they like, sees at a glance what is still
missing, and can close it at any point - a half-connected Jarvis works, just with fewer
services, and it says which ones.

It opens by itself once, the first time the application runs on a machine. After that it is
a button, because a setup screen that greets you every morning is a setup screen you learn
to dismiss without reading.

The shape around that list is the same one the main screen uses: a rail on the left, the
work in the middle, and one quiet column on the right. Three entries of the rail belong to
this window and scroll it; the other three belong to the main window, so the rail says
where they lead rather than pretending this screen has a page for them.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.ui.components import StatusDot, icon_label, text_label
from jarvis.ui.connections_panel import ConnectionsPanel, divider
from jarvis.ui.dashboard import OrbWidget, line_icon

MARKER = "setup.json"
RAIL_WIDTH = 210
IDENTITY_WIDTH = 286

# Where each entry goes. The three marked False are the main window's, and this window asks
# it to open them rather than growing a second copy of settings it does not own.
NAVIGATION: tuple[tuple[str, str, str, bool], ...] = (
    ("home", "Главная", "home", False),
    ("settings", "Настройки", "settings", True),
    ("models", "Модели", "spark", True),
    ("apps", "Приложения", "apps", True),
    ("commands", "Команды", "command", False),
    ("profile", "Профиль", "user", False),
)

# What Jarvis does, said once, beside the sphere. No state is claimed here: these are the
# capabilities of the product, and what is actually connected is the list on the left.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("mic", "Голосовой ассистент"),
    ("apps", "Работа с приложениями"),
    ("spark", "Умные действия"),
    ("list", "Контекст и память"),
)


def first_run(data_dir: Path) -> bool:
    """Whether this machine has never been shown the setup screen.

    A directory that cannot be read counts as a first run: showing the screen once too
    often costs a click, and never showing it costs the owner the whole application.
    """
    try:
        return not (Path(data_dir) / MARKER).exists()
    except OSError:
        return True


def mark_shown(data_dir: Path) -> None:
    """Remember that it was shown. Failing to remember only means showing it again."""
    try:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).write_text(json.dumps({"shown": True}), encoding="utf-8")
    except OSError:
        return


class SetupWindow(QDialog):
    #: An entry of the rail that belongs to the main window: "home", "commands", "profile".
    navigate = Signal(str)

    def __init__(
        self,
        session: MailSession,
        audit: AuditLog,
        data_dir: Path,
        parent: QWidget | None = None,
        *,
        models: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jarvis — настройки")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1360, 920)
        self.setMinimumSize(960, 620)
        self.data_dir = Path(data_dir)
        self._focused = False
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)
        root.addWidget(self._rail())
        root.addLayout(self._workspace(session, audit, models), 1)
        root.addWidget(self._identity())
        mark_shown(self.data_dir)

    def _rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(RAIL_WIDTH)
        side = QVBoxLayout(rail)
        side.setContentsMargins(12, 16, 12, 14)
        side.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(icon_label("orb", "#7fb2f5", 26))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        wordmark = text_label("Jarvis", "wordmark")
        wordmark.setWordWrap(False)
        text.addWidget(wordmark)
        text.addWidget(text_label("Настройки", "tagline"))
        brand.addLayout(text, 1)
        side.addLayout(brand)
        side.addSpacing(14)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, title, icon, _here in NAVIGATION:
            button = QPushButton(title)
            button.setObjectName("nav")
            button.setIcon(line_icon(icon))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setFixedHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(title)
            button.clicked.connect(lambda _=False, target=key: self.go(target))
            self.navigation.addButton(button)
            self.nav_buttons[key] = button
            side.addWidget(button)
        self.nav_buttons["settings"].setChecked(True)
        side.addStretch(1)
        side.addWidget(divider())
        side.addSpacing(10)
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addWidget(StatusDot("live", "Оболочка запущена"), 0, Qt.AlignmentFlag.AlignTop)
        state = QVBoxLayout()
        state.setContentsMargins(0, 0, 0, 0)
        state.setSpacing(2)
        active = text_label("Jarvis активен", "tileTitle")
        active.setWordWrap(False)
        state.addWidget(active)
        state.addWidget(text_label("Готов к работе", "tileCaption"))
        bottom.addLayout(state, 1)
        side.addLayout(bottom)
        return rail

    def _workspace(
        self, session: MailSession, audit: AuditLog, models: Mapping[str, str] | None
    ) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(4)
        head.setContentsMargins(4, 2, 4, 0)
        head.addWidget(text_label("Настройки", "screenTitle"))
        head.addWidget(
            text_label("Настройте модели, подключения и команды для Jarvis.", "greetingLine")
        )
        column.addLayout(head)
        self.panel = ConnectionsPanel(session=session, audit=audit, models=models)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.panel)
        column.addWidget(self.scroll_area, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 0)
        footer.setSpacing(16)
        footer.addWidget(
            text_label(
                "Каждое подключение применяется сразу: ключ уходит в хранилище Windows, "
                "согласие — в аккаунт Microsoft.",
                "inputHint",
            ),
            1,
        )
        # Named for what it does. Everything here has already been applied by the time it
        # is pressed, so calling it "Сохранить" would be a button taking credit for nothing.
        self.close_button = QPushButton("Готово")
        self.close_button.setObjectName("save")
        self.close_button.setIcon(line_icon("check", "#06101f", 20))
        self.close_button.setIconSize(QSize(16, 16))
        self.close_button.setMinimumHeight(40)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setAccessibleName("Закрыть настройки")
        self.close_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button, 0)
        column.addLayout(footer)
        return column

    def _identity(self) -> QFrame:
        """One quiet column that says whose settings these are."""
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(IDENTITY_WIDTH)
        column = QVBoxLayout(card)
        column.setContentsMargins(20, 24, 20, 20)
        column.setSpacing(14)
        self.orb = OrbWidget()
        self.orb.setFixedSize(150, 150)
        column.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        title = text_label("Jarvis", "identityTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        subtitle = text_label("Ваш ИИ-помощник", "sectionHint")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(subtitle)
        column.addSpacing(6)
        for icon, name in CAPABILITIES:
            row = QFrame()
            row.setObjectName("inset")
            line = QHBoxLayout(row)
            line.setContentsMargins(12, 10, 12, 10)
            line.setSpacing(12)
            line.addWidget(icon_label(icon, "#7fb2f5", 17))
            label = text_label(name, "tileTitle")
            label.setWordWrap(False)
            line.addWidget(label, 1)
            column.addWidget(row)
        column.addStretch(1)
        column.addWidget(divider())
        quote = text_label("«Больше, чем просто ассистент. Это Jarvis.»", "quote")
        quote.setAlignment(Qt.AlignmentFlag.AlignLeft)
        column.addWidget(quote)
        return card

    def go(self, target: str) -> None:
        """Three entries move this screen; three ask the main window to open its own."""
        button = self.nav_buttons.get(target)
        if button is None:
            return
        button.setChecked(True)
        here = {key: own for key, _title, _icon, own in NAVIGATION}.get(target, False)
        if not here:
            # The rail says where it leads; the window that owns the page opens it.
            self.navigate.emit(target)
            self.nav_buttons["settings"].setChecked(True)
            return
        anchors = {
            "settings": None,
            "models": self.panel.states.get("deepseek"),
            "apps": self.panel.surface_buttons.get("teams") or self.panel.states.get("asana"),
        }
        anchor = anchors.get(target)
        if anchor is None:
            self.scroll_area.verticalScrollBar().setValue(0)
            return
        self.scroll_area.ensureWidgetVisible(anchor, 0, 220)

    def showEvent(self, event: QShowEvent) -> None:
        """Put the ring where the tick is: a dialog hands focus to its first control, and
        a ring around "Главная" reads as "you are on Главная", which is what this is not."""
        super().showEvent(event)
        if not self._focused:
            self._focused = True
            QTimer.singleShot(0, self.nav_buttons["settings"].setFocus)

    def shutdown(self) -> None:
        self.panel.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
