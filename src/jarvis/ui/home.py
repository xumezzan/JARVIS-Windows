"""The panels the main screen is made of, apart from the command itself.

They are here rather than in `main_window.py` for one reason: that file owns the run - the
planner session, the permission prompts, cancellation, the states - and a screen full of
cards was quietly turning it into a layout file as well. Each panel below knows how to draw
one thing and says, through a signal, what the owner asked for; the window decides what that
means.

Every one of them is built for the honest case first. A service with no key shows as a
service with no key, a day with no connected calendar says exactly that and offers the way
to fix it, and the summary counts what actually happened in this session. Nothing on this
screen is a placeholder that looks like data.
"""

from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.connectors.instants import parse
from jarvis.core.report import MONTHS
from jarvis.ui.components import (
    AppTile,
    EventRow,
    MonthCalendar,
    Panel,
    StatusDot,
    TileButton,
    icon_label,
    text_label,
)
from jarvis.ui.dashboard import line_icon

MAX_TITLE = 70

# The short starters: four ways a command usually begins, put into the line rather than
# executed, because the owner still has to say what they actually want done.
STARTERS: tuple[tuple[str, str, str, str], ...] = (
    ("command", "Приложение", "Открыть приложение", "Открой "),
    ("search", "Найти в сети", "Найти информацию", "Найди в интернете "),
    ("tasks", "Задача", "Создать задачу", "Создай задачу в Asana: "),
    ("mail", "Письмо", "Написать письмо", "Напиши письмо "),
)

# Six whole commands. Each one is a sentence this assistant can actually carry out, and
# clicking puts it in the command line so it can be read before it runs.
ACTIONS: tuple[tuple[str, str, str, str, str], ...] = (
    ("calendar", "Календарь", "Встречи на сегодня", "Покажи мои встречи на сегодня", "#7fb2f5"),
    ("tasks", "Asana", "Мои задачи", "Покажи мои открытые задачи в Asana", "#f2857f"),
    ("mail", "Почта", "Проверить почту", "Проверь почту и скажи, что важное", "#7fb2f5"),
    ("document", "Notion", "Найти страницу", "Найди в Notion страницу о клиенте", "#c3ccd8"),
    ("globe", "Интернет", "Найти информацию", "Найди в интернете ", "#7fd6c0"),
    (
        "command",
        "Приложения",
        "Открыть приложение",
        "Открой блокнот и напиши список дел на завтра",
        "#b9a8f0",
    ),
)

# What the grid of services is a grid of. The Microsoft account carries four surfaces at
# once, so it is one tile rather than four: connecting it connects all of them.
APPS: tuple[tuple[str, str, str, str], ...] = (
    ("microsoft", "mail", "Microsoft", "#7fb2f5"),
    ("deepseek", "spark", "DeepSeek", "#7fd6c0"),
    ("openai", "spark", "OpenAI", "#b9a8f0"),
    ("notion", "document", "Notion", "#c3ccd8"),
    ("asana", "tasks", "Asana", "#f2857f"),
    ("fireflies", "chat", "Fireflies", "#e5c07b"),
    ("voice", "mic", "Голос", "#7fb2f5"),
    ("mcp", "apps", "MCP", "#8fa3bb"),
)


def day_title(moment: datetime) -> str:
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def clipped(text: str, limit: int = MAX_TITLE) -> str:
    """A subject from a service is data, and long data must not push the layout around."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class QuickActions(Panel):
    """Six commands worth having on the screen, in the owner's words."""

    chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__("Быстрые действия", icon="spark")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.tiles: dict[str, TileButton] = {}
        for index, (icon, title, caption, command, color) in enumerate(ACTIONS):
            tile = TileButton(icon, title, caption, color)
            tile.setToolTip(f"Подставить в строку команды: «{command}»")
            tile.clicked.connect(lambda _=False, text=command: self.chosen.emit(text))
            grid.addWidget(tile, index // 2, index % 2)
            self.tiles[title] = tile
        for column in (0, 1):
            grid.setColumnStretch(column, 1)
        self.body.addLayout(grid)


class ConnectedApps(Panel):
    """Which services this machine can actually reach, and the way to add one."""

    connect_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Подключения", icon="link")
        self.counter = text_label("проверяю", "counter")
        self.counter.setWordWrap(False)
        self.heading.addWidget(self.counter)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.tiles: dict[str, AppTile] = {}
        for index, (key, icon, name, color) in enumerate(APPS):
            tile = AppTile(icon, name, color)
            tile.clicked.connect(self.connect_requested)
            grid.addWidget(tile, index // 4, index % 4)
            self.tiles[key] = tile
        for column in range(4):
            grid.setColumnStretch(column, 1)
        self.body.addLayout(grid)
        self.add_button = QPushButton("Подключить ещё")
        self.add_button.setObjectName("link")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.setAccessibleName("Открыть настройку подключений")
        self.add_button.clicked.connect(self.connect_requested)
        self.body.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)

    def apply(self, states: dict[str, bool]) -> None:
        """Show what the probe found. A service it could not ask about is not connected."""
        for key, tile in self.tiles.items():
            tile.set_connected(bool(states.get(key)))
        connected = sum(1 for key in self.tiles if states.get(key))
        self.counter.setText(f"{connected} из {len(self.tiles)}")
        self.counter.setAccessibleName(f"Подключено {connected} из {len(self.tiles)} приложений")

    @property
    def missing(self) -> int:
        return sum(1 for tile in self.tiles.values() if tile.property("connected") == "false")


class TodayPanel(Panel):
    """The day: the month, what is left of it, and the one thing it may be missing."""

    connect_requested = Signal()
    open_requested = Signal()

    def __init__(self, today: datetime | None = None) -> None:
        moment = today or datetime.now()
        super().__init__("Сегодня", day_title(moment), icon="calendar")
        self.body.addWidget(MonthCalendar(moment.date()))
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        self.body.addWidget(divider)
        self.events_box = QWidget()
        self.events_box.setObjectName("zone")
        self.events = QVBoxLayout(self.events_box)
        self.events.setContentsMargins(0, 0, 0, 0)
        self.events.setSpacing(12)
        self.body.addWidget(self.events_box)
        self.hint = text_label("", "sectionHint")
        self.hint.setAccessibleName("Состояние календаря")
        self.body.addWidget(self.hint)
        self.connect_button = QPushButton("Подключить календарь")
        self.connect_button.clicked.connect(self.connect_requested)
        self.body.addWidget(self.connect_button)
        self.all_button = QPushButton("Все события")
        self.all_button.setObjectName("link")
        self.all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.all_button.setAccessibleName("Открыть календарь в планировщике")
        self.all_button.clicked.connect(self.open_requested)
        self.body.addWidget(self.all_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.count = 0
        self.next_event = ""
        self.show_account(None)

    def _clear(self) -> None:
        while self.events.count():
            item = self.events.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def show_account(self, address: str | None) -> None:
        """No account is not an error, and it is not a day with nothing in it either."""
        self._clear()
        self.count = 0
        self.next_event = ""
        self.connect_button.setVisible(address is None)
        self.all_button.setVisible(address is not None)
        self.hint.setText(
            "Читаю календарь…" if address else "Календарь не подключён — встречи не видны."
        )

    def show_events(self, events: tuple[dict[str, object], ...], read: bool) -> None:
        """Real appointments, or a line saying why there are none on the screen."""
        self._clear()
        self.count = len(events)
        self.next_event = ""
        if not read:
            self.hint.setText("Календарь не прочитан. Проверьте подключение и попробуйте снова.")
            return
        for index, event in enumerate(events):
            start = str(event.get("start") or "")
            try:
                when = f"{parse(start).astimezone():%H:%M}"
            except ValueError:
                when = "--:--"
            title = clipped(str(event.get("subject") or "")) or "Без названия"
            place = clipped(str(event.get("location") or ""), 40)
            if not self.next_event:
                self.next_event = f"{when} {title}"
            self.events.addWidget(EventRow(when, title, place, "busy" if index == 0 else "off"))
        self.hint.setText(
            f"Событий до конца дня: {self.count}." if self.count else "До конца дня встреч нет."
        )


class SummaryCard(Panel):
    """Three facts this window already knows, in one sentence rather than three cards."""

    def __init__(self) -> None:
        super().__init__("Сводка", icon="list")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(icon_label("spark", "#7fb2f5", 18), 0, Qt.AlignmentFlag.AlignTop)
        self.text = text_label("", "summaryText")
        self.text.setAccessibleName("Краткая сводка")
        row.addWidget(self.text, 1)
        self.body.addLayout(row)

    def summarise(self, events: int, next_event: str, completed: int, missing: int) -> None:
        """Only what is already known here: no model call, and nothing filled in for effect."""
        parts = []
        if next_event:
            parts.append(f"Ближайшая встреча — {next_event}")
        elif events:
            parts.append(f"Встреч до конца дня: {events}")
        else:
            parts.append("Встреч на сегодня не видно")
        parts.append(f"выполнено команд за сеанс: {completed}")
        if missing:
            parts.append(f"не подключено сервисов: {missing}")
        self.text.setText(" · ".join(parts) + ".")


class AgentCard(QFrame):
    """What is running, where it is running, and the way into it."""

    open_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("agentCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        row.addWidget(icon_label("orb", "#7fb2f5", 22))
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        title = text_label("Jarvis Agent", "tileTitle")
        title.setWordWrap(False)
        column.addWidget(title)
        self.caption = text_label("Работает на вашем устройстве", "tileCaption")
        column.addWidget(self.caption)
        row.addLayout(column, 1)
        self.dot = StatusDot("live", "Агент работает на этом устройстве")
        row.addWidget(self.dot)
        self.open_button = QPushButton()
        self.open_button.setObjectName("link")
        self.open_button.setIcon(line_icon("chevron", "#54637a", 14))
        self.open_button.setIconSize(QSize(14, 14))
        self.open_button.setFixedWidth(24)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setAccessibleName("Открыть планировщик и память")
        self.open_button.setToolTip("Открыть планировщик и память")
        self.open_button.clicked.connect(self.open_requested)
        row.addWidget(self.open_button)

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)
