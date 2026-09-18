"""Desktop command shell: the command bar runs the real planner through PermissionEngine."""

from datetime import datetime
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
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
from jarvis.ui.components import IconButton, MonthCalendar, Panel
from jarvis.ui.dashboard import OrbWidget, line_icon
from jarvis.ui.permission_workbench import PermissionWorkbench
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.setup_window import SetupWindow
from jarvis.ui.states import STATE_LABELS, UiState
from jarvis.ui.theme import PALETTES, STYLESHEET, build_stylesheet, load_fonts
from jarvis.ui.voice_panel import HoldButton, VoicePanel

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


# What an empty screen should teach: three things worth saying, in the owner's own words
# rather than in the vocabulary of the tools underneath.
EXAMPLES = (
    "Подготовь всё к встрече с клиентом",
    "Открой блокнот и напиши список дел на завтра",
    "Проверь систему дважды",
)


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
        self.setWindowTitle("JARVIS • Desktop Preview")
        self.resize(1480, 940)
        self.setMinimumSize(860, 640)
        load_fonts()
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        # One planner session for the whole application, so the microphone, the command bar
        # and the planner window share one registry, permission engine and audit log.
        self._ensure_planner()
        self._set_state(UiState.IDLE)
        self._record(ShellEvent.STARTED)
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
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(24)
        header = QHBoxLayout()
        mark = label("", "brandMark")
        mark.setPixmap(line_icon("orb", "#8dc2ff", 24).pixmap(24, 24))
        header.addWidget(mark)
        header.addWidget(label("Jarvis", "wordmark"))
        header.addStretch()
        self.motion_button = QPushButton("Анимация")
        self.motion_button.setObjectName("motion")
        self.motion_button.setCheckable(True)
        self.motion_button.setChecked(True)
        self.motion_button.setAccessibleName("Анимация сферы")
        header.addWidget(self.motion_button)
        self.theme_picker = QComboBox()
        self.theme_picker.setObjectName("themePicker")
        self.theme_picker.setAccessibleName("Цветовая тема")
        self.theme_picker.addItems(list(PALETTES))
        self.theme_picker.currentTextChanged.connect(self._apply_theme)
        header.addWidget(self.theme_picker)
        root.addLayout(header)
        columns = QHBoxLayout()
        columns.setSpacing(20)
        root.addLayout(columns, 1)

        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(160)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 12, 8, 12)
        side.setSpacing(8)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        # Four entries, each of which opens something. "Чат", "Задачи" and "Календарь"
        # were three names for scrolling to a card, which is not navigation.
        for key, title, icon_name in (
            ("home", "Главная", "home"),
            ("journal", "Журнал", "tasks"),
            ("memory", "Память", "apps"),
            ("connections", "Подключения", "settings"),
        ):
            button = QPushButton(title)
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.setIcon(line_icon(icon_name))
            button.setIconSize(QSize(21, 21))
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setFixedHeight(52)
            button.clicked.connect(lambda checked=False, target=key: self._navigate(target))
            self.navigation.addButton(button)
            self.nav_buttons[key] = button
            side.addWidget(button)
        self.nav_buttons["home"].setChecked(True)
        side.addStretch()
        avatar = label("J", "avatar")
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(avatar)
        self.sidebar_greeting = label("Ваше пространство", "greeting")
        self.sidebar_hint = label("На этом устройстве", "sectionHint")
        side.addWidget(self.sidebar_greeting)
        side.addWidget(self.sidebar_hint)
        columns.addWidget(sidebar)

        self.dashboard_grid = QGridLayout()
        self.dashboard_grid.setSpacing(24)
        columns.addLayout(self.dashboard_grid, 1)

        # Left: what has happened. Quiet on purpose - it is a record, not a workplace.
        self.left_column = QWidget()
        self.left_column.setObjectName("column")
        left = QVBoxLayout(self.left_column)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(18)
        self.journal_card, journal_body = self._card("Журнал", "этот сеанс")
        self.activity = ActivityLog(self.config.activity_limit)
        self.activity.setObjectName("activity")
        journal_body.addWidget(self.activity, 1)
        self._completed = 0
        self.summary_label = label("Завершено команд: 0", "sectionHint")
        journal_body.addWidget(self.summary_label)
        left.addWidget(self.journal_card, 1)

        # Centre: the one thing this window is for. The command is the largest element on
        # the screen, and what the assistant is doing right now sits directly under it.
        self.center_column = QWidget()
        self.center_column.setObjectName("column")
        center = QVBoxLayout(self.center_column)
        center.setContentsMargins(0, 6, 0, 0)
        center.setSpacing(12)
        state_row = QHBoxLayout()
        state_row.setSpacing(12)
        self.orb = OrbWidget()
        self.orb.setFixedSize(84, 84)
        self.motion_button.toggled.connect(self.orb.set_motion_enabled)
        state_row.addWidget(self.orb)
        self.state_label = label("", "state")
        self.state_label.setAccessibleName("Состояние задачи")
        state_row.addWidget(self.state_label, 1)
        self.stop_button = IconButton("close", "Остановить выполнение (Esc)")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        state_row.addWidget(self.stop_button)
        center.addLayout(state_row)

        command_frame = QFrame()
        self.command_frame = command_frame
        command_frame.setObjectName("commandBar")
        command_layout = QHBoxLayout(command_frame)
        command_layout.setContentsMargins(12, 8, 10, 8)
        command_layout.setSpacing(10)
        # The microphone lives in the command line: speaking and typing are the same act,
        # so they belong in one place rather than in two corners of the screen.
        self.microphone_button = HoldButton("")
        self.microphone_button.setObjectName("microphone")
        self.microphone_button.setIcon(line_icon("mic", "#81bfff", 26))
        self.microphone_button.setIconSize(QSize(24, 24))
        self.microphone_button.setFixedSize(46, 46)
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
        self.command_input.setFixedHeight(64)
        command_layout.addWidget(self.command_input, 1)
        self.submit_button = QPushButton()
        self.submit_button.setObjectName("send")
        self.submit_button.setIcon(line_icon("arrow", "#e4eeff", 28))
        self.submit_button.setIconSize(QSize(26, 26))
        self.submit_button.setFixedSize(46, 46)
        self.submit_button.setAccessibleName("Выполнить команду")
        self.submit_button.setToolTip("Выполнить команду · Ctrl+Enter")
        self.submit_button.clicked.connect(self.submit)
        command_layout.addWidget(self.submit_button)
        command_frame.setFixedHeight(84)
        center.addWidget(command_frame)

        hints = QHBoxLayout()
        hints.setSpacing(10)
        self.validation_label = label("Ctrl+Enter — выполнить", "inputHint")
        hints.addWidget(self.validation_label)
        hints.addStretch()
        self.voice_hint = label("Микрофон выключен. Запись только по удержанию.", "inputHint")
        self.voice_hint.setAccessibleName("Состояние микрофона")
        hints.addWidget(self.voice_hint)
        center.addLayout(hints)

        # One line instead of four controls. The state stays in words on the screen: the
        # owner asked for autonomy to be visible, not for four decisions before speaking.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.mode_label = label("", "sectionHint")
        self.mode_label.setWordWrap(False)
        self.mode_label.setAccessibleName("Как работает ассистент")
        mode_row.addWidget(self.mode_label)
        self.mode_button = QPushButton("изменить")
        self.mode_button.setObjectName("link")
        self.mode_button.setCheckable(True)
        self.mode_button.toggled.connect(self._show_modes)
        mode_row.addWidget(self.mode_button)
        mode_row.addStretch(1)
        center.addLayout(mode_row)

        self.mode_box = QWidget()
        modes = QVBoxLayout(self.mode_box)
        modes.setContentsMargins(0, 0, 0, 0)
        modes.setSpacing(8)
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
        self.mode_box.setVisible(False)
        center.addWidget(self.mode_box)

        self.task_card, task_body = self._card("Сейчас делаю")
        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Здесь появится команда, которую я принял")
        self.transcript.setFixedHeight(52)
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

        # An empty screen that teaches: three things worth saying, in the owner's words.
        self.examples_box = QWidget()
        examples = QVBoxLayout(self.examples_box)
        examples.setContentsMargins(0, 0, 0, 0)
        examples.setSpacing(6)
        examples.addWidget(label("Можно сказать так:", "sectionHint"))
        for example in EXAMPLES:
            entry = QPushButton(example)
            entry.setObjectName("toolEntry")
            entry.setMinimumHeight(38)
            entry.setToolTip("Подставить эту команду в строку")
            entry.clicked.connect(lambda _, text=example: self._use_example(text))
            examples.addWidget(entry)
        center.addWidget(self.examples_box)
        center.addStretch(1)

        # Right: what is around the task - the day, and the way to connect what is missing.
        self.right_column = QWidget()
        self.right_column.setObjectName("column")
        right = QVBoxLayout(self.right_column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(18)
        self.calendar_card, calendar_body = self._card("Сегодня", f"{datetime.now():%d.%m.%Y}")
        calendar_body.addWidget(MonthCalendar())
        self.calendar_hint = label("Календарь не подключён.", "sectionHint")
        calendar_body.addWidget(self.calendar_hint)
        self.calendar_button = QPushButton("Подключить календарь")
        self.calendar_button.setObjectName("secondary")
        self.calendar_button.clicked.connect(self.open_setup)
        calendar_body.addWidget(self.calendar_button)
        right.addWidget(self.calendar_card, 3)

        suggestion, suggestion_body = self._card("Дальше")
        suggestion.setObjectName("suggestion")
        self.setup_button = QPushButton("Настройка подключений")
        self.setup_button.setObjectName("primary")
        self.setup_button.clicked.connect(self.open_setup)
        suggestion_body.addWidget(self.setup_button)
        self.planner_button = QPushButton("Планировщик и память")
        self.planner_button.setObjectName("secondary")
        self.planner_button.clicked.connect(self.open_planner)
        suggestion_body.addWidget(self.planner_button)
        self.permissions_button = QPushButton("Инструменты и разрешения")
        self.permissions_button.setObjectName("secondary")
        self.permissions_button.setIcon(line_icon("shield", "#e4eeff"))
        self.permissions_button.clicked.connect(self.open_permissions)
        suggestion_body.addWidget(self.permissions_button)
        suggestion_body.addStretch()
        right.addWidget(suggestion, 2)
        self._show_mode_line()

        self._compact_layout: bool | None = None
        self._arrange_dashboard()

    @staticmethod
    def _card(title: str, caption: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = Panel(title, caption)
        return card, card.body

    @Slot(str)
    def _apply_theme(self, theme: str) -> None:
        stylesheet = build_stylesheet(theme)
        self.setStyleSheet(stylesheet)
        for dialog in (self.permission_workbench, self.planner_window):
            if dialog is not None:
                dialog.setStyleSheet(stylesheet)

    def _arrange_dashboard(self) -> None:
        compact = self.width() < 1300
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        for column in (self.left_column, self.center_column, self.right_column):
            self.dashboard_grid.removeWidget(column)
        for index in range(3):
            self.dashboard_grid.setColumnStretch(index, 0)
        self.sidebar.setFixedWidth(64 if compact else 160)
        for button in self.nav_buttons.values():
            button.setText("" if compact else button.accessibleName())
        self.sidebar_greeting.setVisible(not compact)
        self.sidebar_hint.setVisible(not compact)
        if compact:
            self.center_column.setMinimumHeight(560)
            self.dashboard_grid.addWidget(self.center_column, 0, 0, 1, 2)
            self.dashboard_grid.addWidget(self.left_column, 1, 0)
            self.dashboard_grid.addWidget(self.right_column, 1, 1)
            self.dashboard_grid.setColumnStretch(0, 1)
            self.dashboard_grid.setColumnStretch(1, 1)
        else:
            self.center_column.setMinimumHeight(0)
            for index, (column, stretch) in enumerate(
                ((self.left_column, 26), (self.center_column, 48), (self.right_column, 26))
            ):
                self.dashboard_grid.addWidget(column, 0, index)
                self.dashboard_grid.setColumnStretch(index, stretch)

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
        self.nav_buttons[target].setChecked(True)
        if target == "connections":
            self.open_setup()
            return
        if target == "memory":
            # Memory lives in the planner window; the entry opens it there rather than
            # pretending this window has a page for it.
            planner = self._ensure_planner()
            if planner is not None:
                planner.tabs.setCurrentIndex(1)
                self.open_planner()
            return
        widget = self.command_input if target == "home" else self.journal_card
        self.scroll_area.ensureWidgetVisible(widget)
        if target == "home":
            self.command_input.setFocus()
        else:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.setFocus()

    def _record(self, event: ShellEvent) -> None:
        self.log.record(event, self._request_id)
        self.activity.append_event(event)
        if event == ShellEvent.SUCCEEDED:
            self._completed += 1
            self.summary_label.setText(f"Завершено команд: {self._completed}")

    def _set_state(self, state: UiState) -> None:
        self.state = state
        self.state_label.setText(STATE_LABELS[state])
        self.orb.set_state(state)
        self.state_label.setProperty("status", state.value)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.state_changed.emit(state.value)

    def _show_calendar(self) -> None:
        """The day, or the one thing that has to happen before there is a day to show."""
        planner = self.planner_window
        account = planner.mail_session.account if planner is not None else None
        self.calendar_hint.setText(
            f"Календарь Microsoft · {account.address}"
            if account is not None
            else "Календарь не подключён — встречи не видны."
        )
        self.calendar_button.setVisible(account is None)

    def _set_busy(self, busy: bool) -> None:
        self.command_input.setReadOnly(busy)
        self.submit_button.setEnabled(not busy)
        self.run_mode.setEnabled(not busy)
        self.provider_mode.setEnabled(not busy)
        self.autonomy.setEnabled(not busy)
        self.planner_button.setEnabled(not busy)
        self.permissions_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
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
                planner.mail_session, planner.audit, self.config.data_dir, self
            )
            self.setup_window.finished.connect(self._setup_closed)
        self.setup_window.show()
        self.setup_window.raise_()
        self.setup_window.activateWindow()

    def _setup_closed(self) -> None:
        # Something may have been connected while it was open; the day says so at once.
        self._show_calendar()
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
