"""Desktop demo shell with separate permission and bounded text-planner windows."""

from datetime import datetime
from uuid import UUID, uuid4

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
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
from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.observability.logging import ShellLog
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.ui.activity_log import ActivityLog
from jarvis.ui.components import IconButton, MonthCalendar, Panel
from jarvis.ui.dashboard import OrbWidget, line_icon
from jarvis.ui.demo_worker import DemoOutcome, DemoWorker
from jarvis.ui.permission_workbench import PermissionWorkbench
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.states import STATE_LABELS, UiState
from jarvis.ui.theme import PALETTES, STYLESHEET, build_stylesheet, load_fonts


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
        self.worker: DemoWorker | None = None
        self.permission_workbench: PermissionWorkbench | None = None
        self.planner_window: PlannerWindow | None = None
        self._request_id: UUID | None = None
        self._cancel_requested = False
        self._closing = False
        self._closed = False
        self.setWindowTitle("JARVIS • Desktop Preview")
        self.resize(1480, 940)
        self.setMinimumSize(860, 640)
        load_fonts()
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
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
        for key, title, icon_name in (
            ("home", "Главная", "home"),
            ("chat", "Чат", "chat"),
            ("tasks", "Задачи", "tasks"),
            ("calendar", "Календарь", "calendar"),
            ("apps", "Инструменты", "apps"),
            ("settings", "Настройки", "settings"),
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
        self.left_column = QWidget()
        self.left_column.setObjectName("column")
        left = QVBoxLayout(self.left_column)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(18)
        activity_card, activity_body = self._card("История действий", "Этот сеанс")
        self.activity = ActivityLog(self.config.activity_limit)
        self.activity.setObjectName("activity")
        activity_body.addWidget(self.activity, 1)
        activity_body.addWidget(label("История текущего сеанса", "muted"))
        left.addWidget(activity_card, 3)
        self.apps_card, apps_body = self._card("Инструменты")
        apps_body.addWidget(label("Приложения под вашим контролем", "sectionHint"))
        for title, icon in (("Браузер", "globe"), ("Блокнот", "document"), ("VS Code", "code")):
            entry = QPushButton(title)
            entry.setObjectName("toolEntry")
            entry.setIcon(line_icon(icon, "#8dc2ff"))
            entry.setIconSize(QSize(22, 22))
            entry.setMinimumHeight(52)
            entry.setToolTip(f"{title}: открыть окно инструментов и разрешений")
            entry.clicked.connect(self.open_permissions)
            apps_body.addWidget(entry)
        apps_body.addWidget(label("Действия проходят проверку разрешений", "sectionHint"))
        left.addWidget(self.apps_card, 2)

        self.center_column = QWidget()
        self.center_column.setObjectName("column")
        center = QVBoxLayout(self.center_column)
        center.setContentsMargins(0, 6, 0, 0)
        center.setSpacing(10)
        hero_title = label("Jarvis", "heroTitle")
        hero_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(hero_title)
        self.state_label = label("", "state")
        self.state_label.setAccessibleName("Состояние задачи")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.state_label)
        self.orb = OrbWidget()
        self.motion_button.toggled.connect(self.orb.set_motion_enabled)
        center.addWidget(self.orb, 1)
        voice_controls = QHBoxLayout()
        voice_controls.setSpacing(22)
        voice_controls.addStretch()
        keyboard = IconButton("keyboard", "Ввести команду с клавиатуры")
        keyboard.clicked.connect(lambda: self._navigate("chat"))
        voice_controls.addWidget(keyboard)
        self.microphone_button = QPushButton()
        self.microphone_button.setObjectName("microphone")
        self.microphone_button.setIcon(line_icon("mic", "#81bfff", 32))
        self.microphone_button.setIconSize(QSize(30, 30))
        self.microphone_button.setFixedSize(72, 72)
        self.microphone_button.setAccessibleName("Открыть голосовой ввод в планировщике")
        self.microphone_button.setToolTip(
            "Открыть голосовой ввод. Запись начнётся по удержанию кнопки."
        )
        self.microphone_button.clicked.connect(self.open_planner)
        voice_controls.addWidget(self.microphone_button)
        self.stop_button = IconButton("close", "Остановить демонстрацию (Esc)")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        voice_controls.addWidget(self.stop_button)
        voice_controls.addStretch()
        center.addLayout(voice_controls)
        voice_hint = label("Голос в планировщике · запись только по удержанию", "muted")
        voice_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(voice_hint)
        center.addSpacing(14)
        self.demo_mode = QComboBox()
        self.demo_mode.addItems(["Обычная демонстрация", "Проверить ошибку"])
        self.demo_mode.setAccessibleName("Сценарий демонстрации")
        self.demo_mode.setMaximumWidth(230)
        center.addWidget(self.demo_mode, 0, Qt.AlignmentFlag.AlignHCenter)
        command_label = label("Ваша команда", "sectionHint")
        center.addWidget(command_label)
        command_frame = QFrame()
        self.command_frame = command_frame
        command_frame.setObjectName("commandBar")
        command_layout = QHBoxLayout(command_frame)
        command_layout.setContentsMargins(14, 8, 10, 8)
        command_symbol = label("")
        command_symbol.setPixmap(line_icon("command", "#8dc2ff", 20).pixmap(20, 20))
        command_layout.addWidget(command_symbol)
        self.command_input = QPlainTextEdit()
        self.command_input.setObjectName("commandInput")
        self.command_input.installEventFilter(self)
        command_label.setBuddy(self.command_input)
        self.command_input.setAccessibleName("Текстовая команда")
        self.command_input.setPlaceholderText("Что нужно сделать?")
        self.command_input.setFixedHeight(58)
        command_layout.addWidget(self.command_input, 1)
        self.submit_button = QPushButton()
        self.submit_button.setObjectName("send")
        self.submit_button.setIcon(line_icon("arrow", "#e4eeff", 28))
        self.submit_button.setIconSize(QSize(26, 26))
        self.submit_button.setFixedSize(42, 42)
        self.submit_button.setAccessibleName("Запустить демо")
        self.submit_button.setToolTip("Запустить демо · Ctrl+Enter")
        self.submit_button.clicked.connect(self.submit)
        command_layout.addWidget(self.submit_button)
        center.addWidget(command_frame)
        self.validation_label = label("Текстовый ввод — демо · Ctrl+Enter для запуска", "inputHint")
        self.validation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.validation_label)

        self.right_column = QWidget()
        self.right_column.setObjectName("column")
        right = QVBoxLayout(self.right_column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(18)
        self.calendar_card, calendar_body = self._card("Сегодня", f"{datetime.now():%d.%m.%Y}")
        calendar_body.addWidget(MonthCalendar())
        calendar_body.addWidget(label("Календарь не подключён", "sectionHint"))
        right.addWidget(self.calendar_card, 3)

        self.task_card, task_body = self._card("Текущая задача")
        self._demo_completed = 0
        summary_row = QHBoxLayout()
        summary_icon = label("")
        summary_icon.setPixmap(line_icon("check", "#70e2c7", 26).pixmap(26, 26))
        summary_row.addWidget(summary_icon)
        self.summary_label = label("Завершено демонстраций: 0")
        summary_row.addWidget(self.summary_label, 1)
        task_body.addLayout(summary_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        task_body.addWidget(self.progress)

        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Начните с одной команды")
        self.transcript.setFixedHeight(62)
        task_body.addWidget(self.transcript)
        self.action_label = label("Её текст и результат появятся здесь.", "muted")
        self.action_label.setAccessibleName("Результат задачи")
        task_body.addWidget(self.action_label)
        task_body.addStretch()

        right.addWidget(self.task_card, 3)

        suggestion, suggestion_body = self._card("От команды к действию")
        suggestion.setObjectName("suggestion")
        suggestion_body.addWidget(
            label(
                "Откройте инструменты, чтобы управлять приложениями и проверять разрешения.",
                "muted",
            )
        )
        self.permissions_button = QPushButton("Открыть инструменты")
        self.permissions_button.setObjectName("secondary")
        self.permissions_button.setIcon(line_icon("shield", "#e4eeff"))
        self.permissions_button.clicked.connect(self.open_permissions)
        suggestion_body.addWidget(self.permissions_button)
        self.planner_button = QPushButton("Открыть планировщик")
        self.planner_button.setObjectName("primary")
        self.planner_button.clicked.connect(self.open_planner)
        suggestion_body.addWidget(self.planner_button)
        right.addWidget(suggestion, 2)
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
            self.center_column.setMinimumHeight(590)
            self.orb.setMinimumHeight(240)
            self.orb.setMaximumHeight(280)
            self.dashboard_grid.addWidget(self.center_column, 0, 0, 1, 2)
            self.dashboard_grid.addWidget(self.left_column, 1, 0)
            self.dashboard_grid.addWidget(self.right_column, 1, 1)
            self.dashboard_grid.setColumnStretch(0, 1)
            self.dashboard_grid.setColumnStretch(1, 1)
        else:
            self.center_column.setMinimumHeight(0)
            self.orb.setMinimumHeight(330)
            self.orb.setMaximumHeight(16777215)
            for index, (column, stretch) in enumerate(
                ((self.left_column, 30), (self.center_column, 40), (self.right_column, 34))
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
        if target == "settings":
            self.open_permissions()
            return
        destinations = {
            "home": self.orb,
            "chat": self.command_input,
            "tasks": self.task_card,
            "calendar": self.calendar_card,
            "apps": self.apps_card,
        }
        widget = destinations[target]
        self.scroll_area.ensureWidgetVisible(widget)
        if target == "chat":
            self.command_input.setFocus()
        else:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.setFocus()

    def _record(self, event: ShellEvent) -> None:
        self.log.record(event, self._request_id)
        self.activity.append_event(event)
        if event == ShellEvent.SUCCEEDED:
            self._demo_completed += 1
            self.summary_label.setText(f"Завершено демонстраций: {self._demo_completed}")

    def _set_state(self, state: UiState) -> None:
        self.state = state
        self.state_label.setText(STATE_LABELS[state])
        self.orb.set_state(state)
        self.state_label.setProperty("status", state.value)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.state_changed.emit(state.value)

    def _set_busy(self, busy: bool) -> None:
        self.command_input.setReadOnly(busy)
        self.submit_button.setEnabled(not busy)
        self.demo_mode.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(0)

    @Slot()
    def submit(self) -> None:
        if self.worker is not None or self._closing:
            return
        command = self.command_input.toPlainText().strip()
        if not command or len(command) > 4000:
            self.validation_label.setText("Введите команду длиной от 1 до 4 000 символов.")
            return
        self.validation_label.setText(
            "Текст принят только для демонстрации. Он не записывается в журнал."
        )
        self.transcript.setPlainText(command)
        self._request_id = uuid4()
        self._cancel_requested = False
        self._set_busy(True)
        self._set_state(UiState.THINKING)
        self.action_label.setText("План демо: подготовить → подождать в фоне → показать итог.")
        self._record(ShellEvent.SUBMITTED)
        worker = DemoWorker(
            self.config.demo_duration_ms,
            self.config.task_timeout_ms,
            self.demo_mode.currentIndex() == 1,
            self,
        )
        self.worker = worker
        worker.executing.connect(self._executing)
        worker.finished.connect(self._finished)
        worker.start()

    @Slot()
    def _executing(self) -> None:
        if self.worker is not None and not self._cancel_requested:
            self._set_state(UiState.EXECUTING)
            self._record(ShellEvent.EXECUTING)

    @Slot()
    def stop(self) -> None:
        if self.planner_window is not None:
            self.planner_window.stop()
        if self.permission_workbench is not None:
            self.permission_workbench.stop()
        if self.worker is not None and not self._cancel_requested:
            self._cancel_requested = True
            self.worker.cancel()
            self.stop_button.setEnabled(False)
            self.action_label.setText("Остановка демонстрации…")
            self._record(ShellEvent.CANCEL_REQUESTED)

    @Slot()
    def _finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        outcome = DemoOutcome.CANCELLED if self._cancel_requested else worker.outcome
        self.worker = None
        worker.deleteLater()
        states = {
            DemoOutcome.SUCCESS: (UiState.SUCCESS, ShellEvent.SUCCEEDED),
            DemoOutcome.ERROR: (UiState.ERROR, ShellEvent.FAILED),
            DemoOutcome.CANCELLED: (UiState.CANCELLED, ShellEvent.CANCELLED),
            DemoOutcome.TIMEOUT: (UiState.ERROR, ShellEvent.TIMED_OUT),
        }
        state, event = states[outcome]
        self._set_busy(False)
        self._set_state(state)
        self.action_label.setText(EVENT_TEXT[event])
        self._record(event)
        self._request_id = None
        self.command_input.setFocus()
        self.task_finished.emit(outcome.value)
        if self._closing:
            self.close()

    def shutdown(self) -> None:
        """Fallback for application-level quit; the demo's Event makes the join immediate."""
        if self.planner_window is not None:
            self.planner_window.shutdown()
        if self.permission_workbench is not None:
            self.permission_workbench.shutdown()
        if self.worker is not None:
            self.stop()
            self.worker.wait()
            self._finished()
        if not self._closed:
            self._record(ShellEvent.CLOSED)
            self.log.close()
            self._closed = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self.worker is not None:
            self.stop()
            event.ignore()
            return
        self.shutdown()
        event.accept()

    @Slot()
    def open_permissions(self) -> None:
        if self.worker is not None or self._closing:
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
    def open_planner(self) -> None:
        if self.worker is not None or self._closing:
            return
        if self.planner_window is None:
            try:
                self.planner_window = PlannerWindow(self.config, self)
            except Exception:
                self.action_label.setText(
                    "Не удалось открыть планировщик. Проверьте журнал и зависимости."
                )
                return
            self.planner_window.command.setPlainText(self.command_input.toPlainText())
            self.planner_window.finished.connect(self._planner_closed)
        self.planner_window.setStyleSheet(self.styleSheet())
        self.planner_window.show()

    @Slot(int)
    def _planner_closed(self, result: int) -> None:
        if self.planner_window is not None:
            self.planner_window.shutdown()
            self.planner_window.deleteLater()
            self.planner_window = None
