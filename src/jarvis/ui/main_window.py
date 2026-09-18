"""Desktop command shell: the command bar runs the real planner through PermissionEngine."""

from uuid import UUID, uuid4

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Step
from jarvis.core.planner.identifiers import valid_model
from jarvis.core.report import written
from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.observability.logging import ShellLog
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.security.credentials import setup_command
from jarvis.ui.activity_log import ActivityLog
from jarvis.ui.checklist import Checklist
from jarvis.ui.components import IconButton, Panel, StatusDot
from jarvis.ui.connection_probe import ConnectionProbe
from jarvis.ui.dashboard import OrbWidget, line_icon
from jarvis.ui.home import STARTERS, AgentCard, ConnectedApps, QuickActions, SummaryCard, TodayPanel
from jarvis.ui.permission_workbench import PermissionWorkbench
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.preferences import MAX_NAME, HomePreferences
from jarvis.ui.setup_window import SetupWindow
from jarvis.ui.states import STATE_LABELS, UiState
from jarvis.ui.theme import PALETTES, build_stylesheet, load_fonts
from jarvis.ui.today_worker import TodayWorker
from jarvis.ui.voice_panel import HoldButton, VoicePanel
from jarvis.ui.workers import start as start_worker

# Finite planner error categories rendered as advice; never arbitrary text from a tool.
ERROR_ADVICE: dict[str, str] = {
    "credentials": (
        "Ключ модели не найден. Откройте планировщик → вкладка «Подключения» и введите ключ там."
    ),
    "provider_failed": "Модель не ответила. Проверьте ключ, идентификатор модели и сеть.",
    "provider_output": "Ответ модели не соответствует схеме планировщика.",
    "context_limit": "Запрос к модели превысил допустимый размер.",
    "unsupported_platform": "Этот инструмент доступен только в Windows.",
    "application_missing": "Приложение не найдено на этом компьютере.",
    "unobserved_target": "Цель не наблюдалась в этой задаче; начните команду заново.",
    "approval": "Подтверждение не выдано или уже использовано.",
    "precondition": "Предусловие инструмента не выполнено; проверьте состояние окна.",
    "policy": "Действие запрещено политикой разрешений.",
}

PLAN_STATES: dict[str, tuple[UiState, ShellEvent]] = {
    "finished": (UiState.SUCCESS, ShellEvent.SUCCEEDED),
    "simulated": (UiState.SUCCESS, ShellEvent.SUCCEEDED),
    "no_action": (UiState.ERROR, ShellEvent.FAILED),
    "error": (UiState.ERROR, ShellEvent.FAILED),
    "limit": (UiState.ERROR, ShellEvent.FAILED),
    "cancelled": (UiState.CANCELLED, ShellEvent.CANCELLED),
    "timeout": (UiState.ERROR, ShellEvent.TIMED_OUT),
}


# The three fixed widths this screen is laid out around. The rail is a rail - it never
# takes space from the work - and the day column stays narrow enough to be read at a glance.
RAIL_WIDTH = 216
RAIL_COMPACT = 64
JOURNAL_WIDTH = 288
DAY_WIDTH = 320
ORB_SIZE = 212
ORB_COMPACT = 132
# Wide enough for four zones side by side, and for three of them without the journal.
WIDE = 1400
COMPACT = 1180

# The colour of the dot beside the state. The words stay the ones in `states.py`: two
# vocabularies for one thing is how a screen starts contradicting itself.
STATUS_TONES: dict[UiState, str] = {
    UiState.IDLE: "live",
    UiState.LISTENING: "busy",
    UiState.THINKING: "busy",
    UiState.AWAITING_APPROVAL: "warn",
    UiState.EXECUTING: "busy",
    UiState.SUCCESS: "live",
    UiState.ERROR: "error",
    UiState.CANCELLED: "warn",
}


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(name)
    return widget


class MainWindow(QMainWindow):
    state_changed = Signal(str)
    task_finished = Signal(str)

    def __init__(self, config: AppConfig, log: ShellLog) -> None:
        super().__init__()
        self.config = config
        self.log = log
        self.state = UiState.IDLE
        self.running = False
        self.permission_workbench: PermissionWorkbench | None = None
        self.planner_window: PlannerWindow | None = None
        self.setup_window: SetupWindow | None = None
        self.voice: VoicePanel | None = None
        self._request_id: UUID | None = None
        self._cancel_requested = False
        self._in_close = False
        self._waits = 0
        self._closing = False
        self._closed = False
        self.today_worker: TodayWorker | None = None
        self.probe: ConnectionProbe | None = None
        # Two things this window remembers about its owner: what to call them, and which
        # palette they chose. Both are local, and neither is worth asking for twice.
        self.preferences = HomePreferences(config.data_dir / "home.json", tuple(PALETTES))
        self.setWindowTitle("JARVIS • Desktop Preview")
        self.resize(1480, 940)
        self.setMinimumSize(860, 640)
        load_fonts()
        self.setStyleSheet(build_stylesheet(self.preferences.theme))
        self._build_ui()
        # One planner session for the whole application, so the microphone, the command bar
        # and the planner window share one registry, permission engine and audit log.
        self._ensure_planner()
        self._set_state(UiState.IDLE)
        self._record(ShellEvent.STARTED)
        # What is connected is a fact about this machine, and the screen says it out loud
        # rather than showing eight tiles that all look the same.
        self._probe_connections()
        self.command_input.setFocus()
        self._submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._submit_shortcut.activated.connect(self.submit)
        self._stop_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._stop_shortcut.activated.connect(self.stop)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("dashboard")
        central.setMinimumSize(0, 840)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(central)
        self.setCentralWidget(self.scroll_area)
        root = QHBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)
        root.addWidget(self._build_rail())
        self.dashboard_grid = QGridLayout()
        self.dashboard_grid.setSpacing(16)
        root.addLayout(self.dashboard_grid, 1)
        self.left_column = self._build_journal()
        self.center_column = self._build_center()
        self.right_column = self._build_day()
        self._show_mode_line()
        self._show_greeting()
        self._compact_layout: bool | None = None
        self._layout_key = ""
        self._arrange_dashboard()

    def _build_rail(self) -> QFrame:
        """Who this is, where to go, and whether it is running - in that order."""
        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(RAIL_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 14)
        side.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = label("", "brandMark")
        mark.setPixmap(line_icon("orb", "#7fb2f5", 26).pixmap(26, 26))
        mark.setFixedSize(26, 26)
        brand.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_text.setContentsMargins(0, 0, 0, 0)
        self.wordmark = label("Jarvis", "wordmark")
        self.wordmark.setWordWrap(False)
        self.brand_tagline = label("Твой AI-ассистент. Всегда рядом.", "tagline")
        brand_text.addWidget(self.wordmark)
        brand_text.addWidget(self.brand_tagline)
        brand.addLayout(brand_text, 1)
        side.addLayout(brand)
        side.addSpacing(14)

        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        # Five entries, each of which opens something real. A menu item that scrolls the
        # page to a card is not navigation, and the owner said so about the last one.
        for key, title, icon_name in (
            ("home", "Главная", "home"),
            ("journal", "Журнал", "clock"),
            ("plans", "Планы", "calendar"),
            ("connections", "Подключения", "link"),
            ("settings", "Настройки", "settings"),
        ):
            button = QPushButton(title)
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.setIcon(line_icon(icon_name))
            button.setIconSize(QSize(18, 18))
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setFixedHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, target=key: self._navigate(target))
            self.navigation.addButton(button)
            self.nav_buttons[key] = button
            side.addWidget(button)
        self.nav_buttons["home"].setChecked(True)
        side.addStretch(1)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        side.addWidget(divider)
        side.addSpacing(10)
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.avatar = label("J", "avatar")
        self.avatar.setFixedSize(34, 34)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom.addWidget(self.avatar)
        agent = QVBoxLayout()
        agent.setSpacing(2)
        agent.setContentsMargins(0, 0, 0, 0)
        self.agent_name = label("Jarvis", "tileTitle")
        self.agent_name.setWordWrap(False)
        agent.addWidget(self.agent_name)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.agent_dot = StatusDot("live", "Оболочка запущена")
        state_row.addWidget(self.agent_dot)
        self.agent_state = label("Онлайн", "tileCaption")
        self.agent_state.setWordWrap(False)
        state_row.addWidget(self.agent_state, 1)
        agent.addLayout(state_row)
        bottom.addLayout(agent, 1)
        side.addLayout(bottom)
        return sidebar

    def _build_journal(self) -> QWidget:
        """What has happened. Quiet on purpose - it is a record, not a workplace."""
        column = QWidget()
        column.setObjectName("column")
        left = QVBoxLayout(column)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)
        self.journal_card = Panel("Журнал", icon="clock", action="Весь журнал")
        if self.journal_card.action_button is not None:
            # The whole journal is the audit of every tool call, which already has a window.
            self.journal_card.action_button.clicked.connect(self.open_permissions)
        self.activity = ActivityLog(self.config.activity_limit)
        self.activity.setObjectName("activity")
        self.journal_card.body.addWidget(self.activity, 1)
        self._completed = 0
        self.summary_label = label("Завершено команд: 0", "sectionHint")
        self.journal_card.body.addWidget(self.summary_label)
        left.addWidget(self.journal_card, 1)
        return column

    def _build_center(self) -> QWidget:
        """The one thing this window is for, with everything around it kept out of its way."""
        column = QWidget()
        column.setObjectName("column")
        center = QVBoxLayout(column)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(16)
        greeting = QVBoxLayout()
        greeting.setSpacing(4)
        greeting.setContentsMargins(0, 0, 0, 0)
        self.greeting_title = label("Привет", "greetingTitle")
        self.greeting_title.setWordWrap(False)
        greeting.addWidget(self.greeting_title)
        greeting.addWidget(label("Я Jarvis. Чем могу помочь?", "greetingLine"))
        head.addLayout(greeting, 1)
        head.addWidget(self._build_status_pill(), 0, Qt.AlignmentFlag.AlignTop)
        center.addLayout(head)

        # The sphere is the one picture on the screen, and it says one thing: whether
        # anything is happening. It sits above the command rather than in place of it.
        self.orb = OrbWidget()
        self.orb.setFixedSize(ORB_SIZE, ORB_SIZE)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.orb)
        orb_row.addStretch(1)
        center.addLayout(orb_row)

        command_frame = QFrame()
        self.command_frame = command_frame
        command_frame.setObjectName("commandBar")
        command_layout = QHBoxLayout(command_frame)
        command_layout.setContentsMargins(8, 6, 8, 6)
        command_layout.setSpacing(10)
        # The microphone lives in the command line: speaking and typing are the same act,
        # so they belong in one place rather than in two corners of the screen.
        self.microphone_button = HoldButton("")
        self.microphone_button.setObjectName("microphone")
        self.microphone_button.setIcon(line_icon("mic", "#81bfff", 26))
        self.microphone_button.setIconSize(QSize(20, 20))
        self.microphone_button.setFixedSize(42, 42)
        self.microphone_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.microphone_button.setAccessibleName("Удерживайте для записи команды")
        self.microphone_button.setToolTip(
            "Удерживайте кнопку или пробел на ней и говорите. "
            "Распознанная команда запускается сразу."
        )
        command_layout.addWidget(self.microphone_button)
        self.command_input = QPlainTextEdit()
        self.command_input.setObjectName("commandInput")
        self.command_input.installEventFilter(self)
        self.command_input.setAccessibleName("Текстовая команда")
        self.command_input.setPlaceholderText("Скажите, что сделать")
        self.command_input.setFixedHeight(42)
        self.command_input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        command_layout.addWidget(self.command_input, 1)
        self.submit_button = QPushButton()
        self.submit_button.setObjectName("send")
        self.submit_button.setIcon(line_icon("arrow", "#06101f", 28))
        self.submit_button.setIconSize(QSize(20, 20))
        self.submit_button.setFixedSize(42, 42)
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_button.setAccessibleName("Выполнить команду")
        self.submit_button.setToolTip("Выполнить команду · Ctrl+Enter")
        self.submit_button.clicked.connect(self.submit)
        command_layout.addWidget(self.submit_button)
        command_frame.setFixedHeight(58)
        center.addWidget(command_frame)

        hints = QHBoxLayout()
        hints.setSpacing(10)
        self.validation_label = label("Ctrl+Enter — выполнить", "inputHint")
        hints.addWidget(self.validation_label)
        hints.addStretch()
        self.voice_hint = label("Микрофон выключен. Запись по удержанию.", "inputHint")
        self.voice_hint.setAccessibleName("Состояние микрофона")
        hints.addWidget(self.voice_hint)
        center.addLayout(hints)

        # Four ways a command usually starts. They fill the line rather than run: the owner
        # finishes the sentence, and nothing leaves this window until they send it.
        starters = QHBoxLayout()
        starters.setSpacing(10)
        self.starter_buttons: dict[str, QPushButton] = {}
        for icon_name, title, spoken, prefix in STARTERS:
            chip = QPushButton(title)
            chip.setObjectName("chip")
            chip.setIcon(line_icon(icon_name, "#7fb2f5", 20))
            chip.setIconSize(QSize(15, 15))
            chip.setFixedHeight(36)
            # A row of chips must never be what decides the width of the window; Qt
            # shortens the label itself when the four of them stop fitting.
            chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setAccessibleName(spoken)
            chip.setToolTip(f"{spoken} — подставить в строку команды: «{prefix}…»")
            chip.clicked.connect(lambda _=False, text=prefix: self._use_example(text))
            starters.addWidget(chip, 1)
            self.starter_buttons[title] = chip
        center.addLayout(starters)

        # The empty state teaches by offering whole commands; once there is a task, the
        # card that shows the work takes its place.
        self.quick_actions = QuickActions()
        self.quick_actions.chosen.connect(self._use_example)
        self.examples_box = self.quick_actions
        center.addWidget(self.quick_actions)

        self.task_card, task_body = self._card("Сейчас делаю")
        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Здесь появится команда, которую я принял")
        self.transcript.setFixedHeight(46)
        task_body.addWidget(self.transcript)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        task_body.addWidget(self.progress)
        # The same steps the planner window shows, in the window the owner is looking at.
        self.checklist = Checklist()
        task_body.addWidget(self.checklist, 1)
        self.action_label = label("Пока ничего не выполнялось.", "muted")
        self.action_label.setAccessibleName("Результат задачи")
        task_body.addWidget(self.action_label)
        self.task_card.setVisible(False)
        center.addWidget(self.task_card, 1)

        center.addStretch(1)
        center.addWidget(self._build_settings())
        return column

    def _build_status_pill(self) -> QFrame:
        """One line that says whether the assistant is idle, busy or stopped, and a way in."""
        pill = QFrame()
        pill.setObjectName("statusPill")
        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(10)
        self.status_dot = StatusDot("live", "Система активна")
        row.addWidget(self.status_dot)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        self.state_label = label("", "state")
        self.state_label.setWordWrap(False)
        self.state_label.setAccessibleName("Состояние задачи")
        column.addWidget(self.state_label)
        # The line under it is about the shell rather than the task: it is running, and it
        # keeps saying so while the state above changes.
        self.system_label = label("Система активна", "pillCaption")
        self.system_label.setWordWrap(False)
        column.addWidget(self.system_label)
        row.addLayout(column, 1)
        self.settings_button = IconButton("settings", "Настройки ассистента")
        self.settings_button.clicked.connect(lambda: self._navigate("settings"))
        row.addWidget(self.settings_button)
        self.stop_button = IconButton("close", "Остановить выполнение (Esc)")
        self.stop_button.setEnabled(False)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.stop_button)
        return pill

    def _build_settings(self) -> QWidget:
        """Everything configurable in this window, in one place the owner opens on purpose.

        One line instead of four controls: the state stays in words on the screen, because
        the owner asked for autonomy to be visible, not for four decisions before speaking.
        """
        holder = QWidget()
        holder.setObjectName("zone")
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.mode_label = label("", "sectionHint")
        self.mode_label.setWordWrap(False)
        self.mode_label.setAccessibleName("Как работает ассистент")
        mode_row.addWidget(self.mode_label)
        self.mode_button = QPushButton("изменить")
        self.mode_button.setObjectName("link")
        self.mode_button.setCheckable(True)
        self.mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_button.toggled.connect(self._show_modes)
        mode_row.addWidget(self.mode_button)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        self.mode_box = Panel("Настройки", icon="settings")
        modes = self.mode_box.body
        pickers = QHBoxLayout()
        pickers.setSpacing(10)
        self.run_mode = QComboBox()
        self.run_mode.addItems(["Выполнять действия", "Только симуляция"])
        self.run_mode.setAccessibleName("Режим выполнения")
        self.run_mode.currentIndexChanged.connect(self._show_mode_line)
        pickers.addWidget(self.run_mode, 1)
        self.provider_mode = QComboBox()
        self.provider_mode.addItems(
            ["Офлайн: учебные команды", "Облако: DeepSeek, сложные задачи — OpenAI"]
        )
        self.provider_mode.setAccessibleName("Планировщик команд")
        self.provider_mode.currentIndexChanged.connect(self._show_mode_line)
        pickers.addWidget(self.provider_mode, 1)
        modes.addLayout(pickers)
        self.autonomy = QCheckBox("Делать самому, не спрашивая на каждом шаге")
        self.autonomy.setChecked(True)
        self.autonomy.setAccessibleName("Автономный режим")
        self.autonomy.toggled.connect(self._show_mode_line)
        modes.addWidget(self.autonomy)
        self.hands_free = QCheckBox("Слушать по слову «Джарвис», без удержания кнопки")
        self.hands_free.setAccessibleName("Постоянное прослушивание микрофона")
        self.hands_free.setToolTip(
            "Микрофон слушает без удержания кнопки, пока переключатель включён. "
            "Выполняется только фраза, начинающаяся со слова «Джарвис»."
        )
        self.hands_free.toggled.connect(self._hands_free)
        self.hands_free.toggled.connect(self._show_mode_line)
        modes.addWidget(self.hands_free)

        appearance = QHBoxLayout()
        appearance.setSpacing(10)
        appearance.addWidget(label("Как к вам обращаться", "sectionHint"))
        self.name_input = QLineEdit(self.preferences.name)
        self.name_input.setMaxLength(MAX_NAME)
        self.name_input.setPlaceholderText("имя для приветствия")
        self.name_input.setAccessibleName("Имя владельца для приветствия")
        self.name_input.setToolTip("Хранится только на этом компьютере, рядом с настройками.")
        self.name_input.editingFinished.connect(self._remember_preferences)
        appearance.addWidget(self.name_input, 1)
        self.theme_picker = QComboBox()
        self.theme_picker.setObjectName("themePicker")
        self.theme_picker.setAccessibleName("Цветовая тема")
        self.theme_picker.addItems(list(PALETTES))
        self.theme_picker.setCurrentText(self.preferences.theme)
        self.theme_picker.currentTextChanged.connect(self._apply_theme)
        appearance.addWidget(self.theme_picker)
        self.motion_button = QPushButton("Анимация")
        self.motion_button.setObjectName("motion")
        self.motion_button.setCheckable(True)
        self.motion_button.setChecked(True)
        self.motion_button.setAccessibleName("Анимация сферы")
        self.motion_button.toggled.connect(self.orb.set_motion_enabled)
        appearance.addWidget(self.motion_button)
        modes.addLayout(appearance)

        self.permissions_button = QPushButton("Инструменты и разрешения")
        self.permissions_button.setIcon(line_icon("shield", "#b8d6fa"))
        self.permissions_button.clicked.connect(self.open_permissions)
        modes.addWidget(self.permissions_button)
        self.mode_box.setVisible(False)
        outer.addWidget(self.mode_box)
        return holder

    def _build_day(self) -> QWidget:
        """What is around the task: the day, what is connected, and what is running."""
        column = QWidget()
        column.setObjectName("column")
        right = QVBoxLayout(column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)

        self.today = TodayPanel()
        self.today.connect_requested.connect(self.open_setup)
        self.today.open_requested.connect(self.open_calendar)
        # Kept under their old names: the day still asks for the one thing it needs.
        self.calendar_card = self.today
        self.calendar_hint = self.today.hint
        self.calendar_button = self.today.connect_button
        right.addWidget(self.today)

        self.connected_apps = ConnectedApps()
        self.connected_apps.connect_requested.connect(self.open_setup)
        self.setup_button = self.connected_apps.add_button
        right.addWidget(self.connected_apps)

        self.summary_card = SummaryCard()
        right.addWidget(self.summary_card)

        self.agent_card = AgentCard()
        self.agent_card.open_requested.connect(self.open_planner)
        self.planner_button = self.agent_card.open_button
        right.addWidget(self.agent_card)
        right.addStretch(1)
        self._show_summary()
        return column

    @staticmethod
    def _card(title: str, caption: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = Panel(title, caption)
        return card, card.body

    @Slot(str)
    def _apply_theme(self, theme: str) -> None:
        stylesheet = build_stylesheet(theme)
        self.setStyleSheet(stylesheet)
        for dialog in (self.permission_workbench, self.planner_window, self.setup_window):
            if dialog is not None:
                dialog.setStyleSheet(stylesheet)
        self._remember_preferences()

    def _arrange_dashboard(self) -> None:
        """Three widths, one rule: the command keeps the middle and the rail keeps its width.

        Below the wide breakpoint the journal - the one column that is a record rather than
        a workplace - moves under the work instead of squeezing it, and below the compact
        one the rail drops to icons and the day joins the journal on the second row.
        """
        width = self.width()
        key = "compact" if width < COMPACT else "medium" if width < WIDE else "wide"
        if key == self._layout_key:
            return
        self._layout_key = key
        compact = key == "compact"
        self._compact_layout = compact
        for column in (self.left_column, self.center_column, self.right_column):
            self.dashboard_grid.removeWidget(column)
        for index in range(3):
            self.dashboard_grid.setColumnStretch(index, 0)
        self.sidebar.setFixedWidth(RAIL_COMPACT if compact else RAIL_WIDTH)
        for button in self.nav_buttons.values():
            button.setText("" if compact else button.accessibleName())
        for widget in (self.wordmark, self.brand_tagline, self.agent_name, self.agent_state):
            widget.setVisible(not compact)
        self.orb.setFixedSize(*((ORB_COMPACT, ORB_COMPACT) if compact else (ORB_SIZE, ORB_SIZE)))
        if compact:
            # Nothing is pinned to a width here: at this size the two support columns share
            # whatever is left, and the content has to fit rather than scroll sideways.
            for column in (self.left_column, self.right_column):
                column.setMinimumWidth(0)
                column.setMaximumWidth(WIDE)
            self.left_column.setVisible(True)
            self.center_column.setMinimumHeight(560)
            self.dashboard_grid.addWidget(self.center_column, 0, 0, 1, 2)
            self.dashboard_grid.addWidget(self.left_column, 1, 0)
            self.dashboard_grid.addWidget(self.right_column, 1, 1)
            self.dashboard_grid.setColumnStretch(0, 1)
            self.dashboard_grid.setColumnStretch(1, 1)
        else:
            self.center_column.setMinimumHeight(0)
            # The support columns are fixed and the work takes everything else, so widening
            # the window widens the command rather than the record beside it.
            self.left_column.setFixedWidth(JOURNAL_WIDTH)
            self.right_column.setFixedWidth(DAY_WIDTH)
            for index, column in enumerate(
                (self.left_column, self.center_column, self.right_column)
            ):
                self.dashboard_grid.addWidget(column, 0, index)
            self.dashboard_grid.setColumnStretch(1, 1)
            # Three zones instead of four: below the wide breakpoint the record gives its
            # space to the work rather than squeezing both.
            self.left_column.setVisible(key == "wide")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_compact_layout"):
            self._arrange_dashboard()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.command_input and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            self.command_frame.setProperty("active", event.type() == QEvent.Type.FocusIn)
            self.command_frame.style().unpolish(self.command_frame)
            self.command_frame.style().polish(self.command_frame)
        return super().eventFilter(watched, event)

    def _navigate(self, target: str) -> None:
        """Every entry opens something real; none of them is a name for scrolling."""
        self.nav_buttons[target].setChecked(True)
        if target == "connections":
            self.open_setup()
            return
        if target == "plans":
            # Plans are the routines the assistant runs by itself; they live in the planner
            # window, and the entry opens them there rather than pretending otherwise.
            self._open_planner_tab("Рутины")
            return
        if target == "settings":
            self.mode_button.setChecked(True)
            self.scroll_area.ensureWidgetVisible(self.mode_box)
            self.run_mode.setFocus()
            return
        widget = self.command_input if target == "home" else self.journal_card
        self.scroll_area.ensureWidgetVisible(widget)
        if target == "home":
            self.command_input.setFocus()
        else:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.setFocus()

    def _open_planner_tab(self, title: str) -> None:
        planner = self._ensure_planner()
        if planner is None:
            return
        for index in range(planner.tabs.count()):
            if planner.tabs.tabText(index) == title:
                planner.tabs.setCurrentIndex(index)
                break
        self.open_planner()

    @Slot()
    def open_calendar(self) -> None:
        """The whole day rather than what is left of it: Outlook keeps the calendar."""
        self._open_planner_tab("Outlook")

    def _record(self, event: ShellEvent) -> None:
        self.log.record(event, self._request_id)
        self.activity.append_event(event)
        if event == ShellEvent.SUCCEEDED:
            self._completed += 1
            self.summary_label.setText(f"Завершено команд: {self._completed}")
            self._show_summary()

    def _set_state(self, state: UiState) -> None:
        self.state = state
        self.state_label.setText(STATE_LABELS[state])
        self.status_dot.set_tone(STATUS_TONES[state], STATE_LABELS[state])
        self.orb.set_state(state)
        self.state_label.setProperty("status", state.value)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.state_changed.emit(state.value)

    def _show_greeting(self) -> None:
        """Greet the owner by the name they gave, or by no name at all - never by a guess."""
        name = self.preferences.name
        if not name:
            planner = self.planner_window
            account = planner.mail_session.account if planner is not None else None
            # The address is the only name this application has been told; its local part
            # is a reasonable greeting and a poor invention, so it is used only as it is.
            local = account.address.split("@")[0] if account is not None else ""
            name = local[:MAX_NAME].capitalize() if local.replace(".", "").isalpha() else ""
        self.greeting_title.setText(f"Привет, {name}" if name else "Привет")
        self.avatar.setText((name[:1] or "J").upper())

    def _show_calendar(self) -> None:
        """The day, or the one thing that has to happen before there is a day to show."""
        planner = self.planner_window
        account = planner.mail_session.account if planner is not None else None
        self.today.show_account(account.address if account is not None else None)
        if account is not None:
            self.calendar_hint.setText(f"Календарь Microsoft · {account.address}")
            self._read_today(account.model_dump(mode="json"))
        self._show_greeting()
        self._show_summary()

    def _read_today(self, account: dict[str, object]) -> None:
        """One bounded calendar read, off this thread, never while another one is running."""
        planner = self.planner_window
        if planner is None or self.today_worker is not None or self._closing:
            return
        worker = TodayWorker(planner.registry, planner.engine, account)
        self.today_worker = worker
        worker.finished.connect(self._today_read)
        start_worker(worker)

    @Slot()
    def _today_read(self) -> None:
        worker, self.today_worker = self.today_worker, None
        if worker is None or self._closing:
            return
        self.today.show_events(worker.events, worker.read)
        self._show_summary()

    def _probe_connections(self) -> None:
        """Ask the credential store what is actually connected, once, in a worker."""
        if self.probe is not None or self._closing:
            return
        probe = ConnectionProbe(self.config.data_dir)
        self.probe = probe
        probe.finished.connect(self._connections_probed)
        start_worker(probe)

    @Slot()
    def _connections_probed(self) -> None:
        probe, self.probe = self.probe, None
        if probe is None or self._closing:
            return
        planner = self.planner_window
        account = planner.mail_session.account if planner is not None else None
        self.connected_apps.apply({**probe.states, "microsoft": account is not None})
        self._show_summary()

    def _show_summary(self) -> None:
        """Only what this window already knows, in one sentence."""
        self.summary_card.summarise(
            self.today.count,
            self.today.next_event,
            self._completed,
            self.connected_apps.missing,
        )

    def _set_busy(self, busy: bool) -> None:
        self.command_input.setReadOnly(busy)
        self.submit_button.setEnabled(not busy)
        self.run_mode.setEnabled(not busy)
        self.provider_mode.setEnabled(not busy)
        self.autonomy.setEnabled(not busy)
        self.planner_button.setEnabled(not busy)
        self.permissions_button.setEnabled(not busy)
        # A stop button on an idle screen is a control that does nothing; it appears when
        # there is something to stop and goes away again when there is not.
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(0)

    @Slot()
    def submit(self) -> None:
        if self.running or self._closing:
            return
        command = self.command_input.toPlainText().strip()
        if not command or len(command) > 4000:
            self.validation_label.setText("Введите команду длиной от 1 до 4 000 символов.")
            return
        planner = self._ensure_planner()
        if planner is None:
            return
        if planner.worker is not None:
            self.validation_label.setText("Планировщик уже выполняет задачу.")
            return
        cloud = self.provider_mode.currentIndex() == 1
        if cloud and not self._cloud_ready(planner):
            return
        # A hidden planner still owns the session; this window shows its prompts and results.
        planner.prompt_parent = self
        self.transcript.setPlainText(command)
        self.validation_label.setText(
            "Команда выполняется по-настоящему."
            if self.run_mode.currentIndex() == 0
            else "Симуляция: проверки разрешений без реальных действий."
        )
        self._begin()
        self._waits = 0
        self.action_label.setText("Планирование первого шага…")
        self._launch(command, cloud)

    def _launch(self, command: str, cloud: bool) -> None:
        """Start the run, waiting out the memory panel's own start-up read if it is busy."""
        planner = self.planner_window
        if planner is None or not self.running or self._cancel_requested:
            return
        if planner.memory.worker is not None and self._waits < 40:
            # A command that lands while the memory store is being read waits for it.
            self._waits += 1
            self.action_label.setText("Память загружается; команда запустится сама…")
            QTimer.singleShot(50, lambda: self._launch(command, cloud))
            return
        started = planner.run_command(
            command,
            execute=self.run_mode.currentIndex() == 0,
            autonomous=self.autonomy.isChecked(),
            cloud=cloud,
        )
        if not started:
            self._reset()
            self._set_state(UiState.ERROR)
            self.action_label.setText(planner.status.text())
            self._record(ShellEvent.FAILED)
            self._request_id = None

    def _ensure_planner(self) -> PlannerWindow | None:
        """One planner session owns the registry, engine, audit and approval authority."""
        if self.planner_window is None:
            try:
                planner = PlannerWindow(self.config, self)
            except Exception:
                self.action_label.setText(
                    "Не удалось открыть планировщик. Проверьте журнал и зависимости."
                )
                self._set_state(UiState.ERROR)
                return None
            planner.command.setPlainText(self.command_input.toPlainText())
            planner.finished.connect(self._planner_closed)
            planner.progress_event.connect(self._planner_progress)
            planner.task_finished.connect(self._planner_finished)
            self.planner_window = planner
            self.voice = planner.voice
            # The assistant answers out loud from this window; the planner keeps the switch.
            self.voice.speech_enabled.setChecked(True)
            self.voice.attach(self.microphone_button)
            self.voice.message.connect(self.voice_hint.setText)
            self.voice.transcript_ready.connect(self._spoken)
        self.planner_window.setStyleSheet(self.styleSheet())
        self._show_calendar()
        return self.planner_window

    @Slot()
    def _show_mode_line(self) -> None:
        """One line that says how the assistant will work, in words rather than in controls."""
        parts = [
            "делаю сам" if self.autonomy.isChecked() else "спрашиваю на каждом шаге",
            "офлайн-модель" if self.provider_mode.currentIndex() == 0 else "облачная модель",
        ]
        if self.run_mode.currentIndex() == 1:
            parts.append("только симуляция")
        if self.hands_free.isChecked():
            parts.append("слушаю по слову «Джарвис»")
        self.mode_label.setText(" · ".join(parts))
        # The same sentence under the agent card: how it works belongs where it is said
        # to be working, not only next to the switch that changes it.
        self.agent_card.set_caption("На этом устройстве · " + " · ".join(parts))

    @Slot()
    def _remember_preferences(self) -> None:
        """Keep the name and the palette for the next launch; an empty name is no name."""
        self.preferences.remember(self.name_input.text(), self.theme_picker.currentText())
        self.name_input.setText(self.preferences.name)
        self._show_greeting()

    @Slot(bool)
    def _show_modes(self, shown: bool) -> None:
        self.mode_box.setVisible(shown)
        self.mode_button.setText("свернуть" if shown else "изменить")

    def _use_example(self, text: str) -> None:
        self.command_input.setPlainText(text)
        self.command_input.setFocus()

    @Slot(bool)
    def _hands_free(self, enabled: bool) -> None:
        """Standing capture: explicit, visible, never on by default and off on shutdown."""
        if self.voice is not None:
            self.voice.set_hands_free(enabled and not self._closing)
            if enabled and not self.voice.hands_free:
                self.hands_free.setChecked(False)

    @Slot(str)
    def _spoken(self, command: str) -> None:
        """A finished transcript runs at once: speaking the command is the submission."""
        if not command.strip() or self.running or self._closing:
            return
        self.command_input.setPlainText(command)
        self.submit()

    def _cloud_ready(self, planner: PlannerWindow) -> bool:
        """Cloud use needs a model identifier and an explicit transmission consent."""
        if planner.cloud_consent.isChecked() and planner.model.text().strip():
            return True
        dialog = QDialog(self)
        dialog.setWindowTitle("Отправка команды в DeepSeek и OpenAI")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        # Wide enough that the consent line is never cut off mid-sentence.
        dialog.setMinimumWidth(760)
        body = QVBoxLayout(dialog)
        body.addWidget(
            label(
                "Облачный планировщик отправляет текст команды, ваши уточнения и "
                "результаты инструментов — даже в режиме симуляции. Запросы тарифицируются "
                "отдельно. store=false не означает отсутствие хранения у провайдера."
            )
        )
        body.addWidget(
            label(
                "Обычные задачи ведёт DeepSeek — его модель берётся из настроек. "
                "Ниже укажите модель OpenAI: на неё планировщик переходит, когда задача "
                "оказывается многослойной, поэтому идентификатор нужен заранее."
            )
        )
        model = QPlainTextEdit(planner.model.text() or self.config.planner_model)
        model.setPlaceholderText("например, gpt-5.4-mini")
        model.setFixedHeight(46)
        body.addWidget(model)
        body.addWidget(
            label(
                "Ключи вводите сами в терминале, по одному на поставщика: "
                + setup_command("deepseek")
                + "  и  "
                + setup_command("openai")
                + ". Они хранятся в Windows Credential Locker и не попадают "
                "в команду или журнал."
            )
        )
        # Short enough to be read whole at this width; who takes over when is said above.
        consent = QCheckBox(
            "Разрешаю отправлять команду, уточнения и результаты в DeepSeek и OpenAI"
        )
        body.addWidget(consent)
        hint = label("")
        body.addWidget(hint)
        buttons = QDialogButtonBox()
        # Built by hand so the confirming button can be held shut until the answer is complete.
        confirm = QPushButton("OK")
        confirm.setDefault(True)
        buttons.addButton(confirm, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        body.addWidget(buttons)

        def gate() -> None:
            """Say what is still missing here, rather than refusing after the window closes."""
            typed = model.toPlainText().strip()
            ready = valid_model(typed) and consent.isChecked()
            confirm.setEnabled(ready)
            if not typed:
                hint.setText("Укажите модель OpenAI — например, gpt-5.4-mini.")
            elif not valid_model(typed):
                hint.setText("Идентификатор пишется без пробелов: gpt-5.4-mini, не gpt 5.4 mini.")
            elif not consent.isChecked():
                hint.setText("Отметьте согласие выше — без него команда в облако не уйдёт.")
            else:
                hint.setText("Спрошу только один раз: снять разрешение можно в планировщике.")

        consent.toggled.connect(gate)
        model.textChanged.connect(gate)
        gate()
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        identifier = model.toPlainText().strip()
        if valid_model(identifier):
            # Kept even when consent was withheld, so the next dialog opens already filled in.
            planner.model.setText(identifier)
            planner.remember_cloud()
        if not accepted:
            self.validation_label.setText("Облачный режим не подтверждён.")
            return False
        planner.cloud_consent.setChecked(True)
        return True

    def _begin(self) -> None:
        self._request_id = uuid4()
        self._cancel_requested = False
        self.running = True
        self.checklist.reset()
        # The examples were the empty state; once there is a task they are in the way, and
        # the card that shows the work takes their place.
        self.examples_box.setVisible(False)
        self.task_card.setVisible(True)
        self._set_busy(True)
        self._set_state(UiState.THINKING)
        self._record(ShellEvent.SUBMITTED)

    def _reset(self) -> None:
        """Release the UI. The request id outlives this so the closing event stays correlated."""
        self.running = False
        self._set_busy(False)

    @Slot(str, object)
    def _planner_progress(self, kind: str, value: object) -> None:
        if self._cancel_requested:
            return
        if not self.running:
            # A run started from the planner window itself; mirror it here too.
            self._begin()
        self.checklist.record(kind, value)
        if kind == "budget" and isinstance(value, tuple):
            spent, ceiling = value
            self.action_label.setText(f"Обращений к модели: {spent} из {ceiling}.")
        elif kind == "thinking":
            self._set_state(UiState.THINKING)
            self.action_label.setText(f"Планирование шага {value}…")
        elif kind == "executing":
            self._set_state(UiState.EXECUTING)
            self.action_label.setText(f"Выполняется: {value}")
            self._record(ShellEvent.EXECUTING)
        elif kind == "tool" and isinstance(value, Step):
            self.action_label.setText(
                f"{value.tool}: {value.outcome.status.value} ({value.outcome.error.value})"
            )

    @Slot()
    def stop(self) -> None:
        if self.planner_window is not None:
            self.planner_window.stop()
        if self.permission_workbench is not None:
            self.permission_workbench.stop()
        if self.running and not self._cancel_requested:
            self._cancel_requested = True
            self.stop_button.setEnabled(False)
            self.action_label.setText("Остановка; уже выданные действия не отзываются…")
            self._record(ShellEvent.CANCEL_REQUESTED)
            if self.planner_window is None or self.planner_window.worker is None:
                # Stopped before the planner started: nothing else will report this task.
                self._planner_finished("cancelled")

    @Slot(str)
    def _planner_finished(self, status: str) -> None:
        if not self.running:
            return
        state, event = PLAN_STATES.get(status, (UiState.ERROR, ShellEvent.FAILED))
        self.checklist.settle()
        self._reset()
        self._set_state(state)
        result = self.planner_window.last_result if self.planner_window is not None else None
        # The report first: this window is where the owner reads what happened, not a log.
        rows = [*written(result)] if result is not None else [EVENT_TEXT[event]]
        if result is not None and result.error in ERROR_ADVICE:
            rows.append(ERROR_ADVICE[result.error])
        if result is not None:
            rows.extend(
                f"{index}. {step.tool}: {step.outcome.status.value}"
                for index, step in enumerate(result.steps, 1)
            )
        self.action_label.setText("\n".join(rows))
        self._record(event)
        self._request_id = None
        self.command_input.setFocus()
        self.task_finished.emit(status)
        if self._closing and not self._in_close:
            self.close()

    def shutdown(self) -> None:
        """Fallback for application-level quit; planner shutdown joins its worker thread."""
        self._closing = True
        if self.voice is not None:
            self.voice.set_hands_free(False)
        if self.running:
            self.stop()
        if self.setup_window is not None:
            self.setup_window.shutdown()
        if self.planner_window is not None:
            self.planner_window.shutdown()
        if self.permission_workbench is not None:
            self.permission_workbench.shutdown()
        self._reset()
        self._request_id = None
        if not self._closed:
            self._record(ShellEvent.CLOSED)
            self.log.close()
            self._closed = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self.running:
            self._in_close = True
            try:
                self.stop()
            finally:
                self._in_close = False
        if self.running:
            # A planner thread is still draining; its completion closes this window.
            event.ignore()
            return
        self.shutdown()
        event.accept()

    @Slot()
    def open_permissions(self) -> None:
        if self.running or self._closing:
            return
        if self.permission_workbench is None:
            try:
                self.permission_workbench = PermissionWorkbench(
                    self.config.data_dir,
                    self,
                    browser_host=BrowserHost(NetworkPolicy(self.config.browser_origins)),
                )
            except Exception:
                self.action_label.setText(
                    "Не удалось открыть журнал инструментов. Проверьте каталог данных."
                )
                self._set_state(UiState.ERROR)
                return
            self.permission_workbench.finished.connect(self._permissions_closed)
        self.permission_workbench.setStyleSheet(self.styleSheet())
        self.permission_workbench.show()

    @Slot(int)
    def _permissions_closed(self, result: int) -> None:
        if self.permission_workbench is not None:
            self.permission_workbench.shutdown()
            self.permission_workbench.deleteLater()
            self.permission_workbench = None

    @Slot()
    def open_setup(self) -> None:
        """The one screen that says what is connected and connects the rest.

        It borrows the planner's session and audit log rather than making its own: signing
        in twice in one application would leave two accounts and one of them unaudited.
        """
        if self._closing:
            return
        planner = self._ensure_planner()
        if planner is None:
            return
        if self.setup_window is None:
            self.setup_window = SetupWindow(
                planner.mail_session,
                planner.audit,
                self.config.data_dir,
                self,
                # Shown beside the key, not editable there: the planner owns the identifier.
                models={
                    "deepseek": self.config.fast_model,
                    "openai": planner.model.text().strip() or self.config.planner_model,
                },
            )
            self.setup_window.finished.connect(self._setup_closed)
            self.setup_window.navigate.connect(self._from_setup)
        self.setup_window.setStyleSheet(self.styleSheet())
        self.setup_window.show()
        self.setup_window.raise_()
        self.setup_window.activateWindow()

    @Slot(str)
    def _from_setup(self, target: str) -> None:
        """An entry of the setup rail that belongs here: open it, rather than duplicating it."""
        if self._closing:
            return
        self.raise_()
        self.activateWindow()
        if target == "commands":
            # Commands are configured where they are given, next to the command line.
            self._navigate("settings")
            self.run_mode.setFocus()
            return
        if target == "profile":
            self._navigate("settings")
            self.name_input.setFocus()
            self.name_input.selectAll()
            return
        self._navigate("home")

    def _setup_closed(self) -> None:
        # Something may have been connected while it was open; the day and the grid of
        # services both say so at once rather than at the next launch.
        self._show_calendar()
        self._probe_connections()
        window, self.setup_window = self.setup_window, None
        if window is not None:
            window.shutdown()
            window.deleteLater()

    def open_planner(self) -> None:
        if self.running or self._closing:
            return
        planner = self._ensure_planner()
        if planner is None:
            return
        # A visible planner owns its own prompts again.
        planner.prompt_parent = planner
        typed = self.command_input.toPlainText()
        if typed.strip():
            planner.command.setPlainText(typed)
        planner.show()

    @Slot(int)
    def _planner_closed(self, result: int) -> None:
        if self.planner_window is not None:
            self.planner_window.shutdown()
            self.planner_window.deleteLater()
            self.planner_window = None
            self.voice = None
