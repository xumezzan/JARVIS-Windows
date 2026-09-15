"""Desktop shell with isolated local permission tests; no external integrations or model."""

from datetime import datetime
from uuid import UUID, uuid4

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
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
from jarvis.ui.dashboard import OrbWidget, line_icon
from jarvis.ui.demo_worker import DemoOutcome, DemoWorker
from jarvis.ui.permission_workbench import PermissionWorkbench
from jarvis.ui.states import STATE_LABELS, UiState
from jarvis.ui.theme import STYLESHEET


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
        self._request_id: UUID | None = None
        self._cancel_requested = False
        self._closing = False
        self._closed = False
        self.setWindowTitle("JARVIS • Desktop Preview")
        self.resize(1480, 940)
        self.setMinimumSize(860, 640)
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
        central.setMinimumSize(1230, 840)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(central)
        self.setCentralWidget(self.scroll_area)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 16, 22, 22)
        root.setSpacing(24)
        header = QHBoxLayout()
        mark = label("◉", "brandMark")
        header.addWidget(mark)
        header.addWidget(label("Jarvis", "wordmark"))
        header.addStretch()
        header.addWidget(label("ЛОКАЛЬНЫЙ ПОМОЩНИК", "eyebrow"))
        root.addLayout(header)
        columns = QHBoxLayout()
        columns.setSpacing(20)
        root.addLayout(columns, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(120)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, title, icon_name in (
            ("home", "Главная", "home"),
            ("chat", "Чат", "chat"),
            ("tasks", "Задачи", "tasks"),
            ("calendar", "Календарь", "calendar"),
            ("apps", "Приложения", "apps"),
            ("settings", "Настройки", "settings"),
        ):
            button = QPushButton(title)
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
        side.addWidget(label("Рад вас видеть", "greeting"))
        side.addWidget(label("Начнём день\nс хороших идей.", "muted"))
        columns.addWidget(sidebar)

        left = QVBoxLayout()
        left.setSpacing(18)
        activity_card, activity_body = self._card("Последние действия")
        self.activity = ActivityLog(self.config.activity_limit)
        self.activity.setObjectName("activity")
        activity_body.addWidget(self.activity, 1)
        activity_body.addWidget(label("История текущего сеанса", "muted"))
        left.addWidget(activity_card, 3)
        self.apps_card, apps_body = self._card("Приложения")
        apps_body.addWidget(label("Внешние сервисы пока не подключены", "muted"))
        app_grid = QGridLayout()
        app_grid.setSpacing(10)
        for index, (title, monogram, color) in enumerate(
            (
                ("Outlook", "O", "#59a9f8"),
                ("Calendar", "31", "#6f9eff"),
                ("Notion", "N", "#e6ebf0"),
                ("Asana", "•••", "#f67a89"),
                ("Slack", "#", "#64cbbb"),
                ("QuickBooks", "qb", "#7bc879"),
                ("Instagram", "◎", "#da86b4"),
                ("Facebook", "f", "#619df9"),
            )
        ):
            tile = QFrame()
            tile.setObjectName("appTile")
            tile.setToolTip(f"{title} — не подключено")
            tile.setAccessibleName(f"{title}: не подключено")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(4, 8, 4, 8)
            symbol = label(monogram, "appSymbol")
            symbol.setStyleSheet(f"color: {color};")
            symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile_layout.addWidget(symbol)
            caption = label(title, "appCaption")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile_layout.addWidget(caption)
            app_grid.addWidget(tile, index // 4, index % 4)
        apps_body.addLayout(app_grid)
        left.addWidget(self.apps_card, 2)
        columns.addLayout(left, 30)

        center = QVBoxLayout()
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
        center.addWidget(self.orb, 1)
        voice_controls = QHBoxLayout()
        voice_controls.setSpacing(22)
        voice_controls.addStretch()
        keyboard = QPushButton()
        keyboard.setIcon(line_icon("keyboard", "#d3deed", 28))
        keyboard.setIconSize(QSize(25, 25))
        keyboard.setObjectName("round")
        keyboard.setFixedSize(52, 52)
        keyboard.setAccessibleName("Ввести команду с клавиатуры")
        keyboard.setToolTip("Ввести команду с клавиатуры")
        keyboard.clicked.connect(lambda: self._navigate("chat"))
        voice_controls.addWidget(keyboard)
        self.microphone_button = QPushButton()
        self.microphone_button.setObjectName("microphone")
        self.microphone_button.setIcon(line_icon("mic", "#81bfff", 32))
        self.microphone_button.setIconSize(QSize(30, 30))
        self.microphone_button.setFixedSize(72, 72)
        self.microphone_button.setEnabled(False)
        self.microphone_button.setAccessibleName("Микрофон недоступен. Запись не ведётся")
        self.microphone_button.setToolTip("Push-to-talk появится на этапе 6. Запись не ведётся.")
        voice_controls.addWidget(self.microphone_button)
        self.stop_button = QPushButton()
        self.stop_button.setIcon(line_icon("close", "#d3deed", 28))
        self.stop_button.setIconSize(QSize(26, 26))
        self.stop_button.setObjectName("round")
        self.stop_button.setFixedSize(52, 52)
        self.stop_button.setAccessibleName("Остановить")
        self.stop_button.setToolTip("Остановить текущую демонстрацию (Esc)")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        voice_controls.addWidget(self.stop_button)
        voice_controls.addStretch()
        center.addLayout(voice_controls)
        voice_hint = label("Голос скоро появится · запись выключена", "muted")
        voice_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(voice_hint)
        center.addSpacing(14)
        self.demo_mode = QComboBox()
        self.demo_mode.addItems(["Обычная демонстрация", "Проверить ошибку"])
        self.demo_mode.setAccessibleName("Сценарий демонстрации")
        center.addWidget(self.demo_mode)
        command_frame = QFrame()
        command_frame.setObjectName("commandBar")
        command_layout = QHBoxLayout(command_frame)
        command_layout.setContentsMargins(14, 8, 10, 8)
        command_layout.addWidget(label("✦", "spark"))
        self.command_input = QPlainTextEdit()
        self.command_input.setObjectName("commandInput")
        self.command_input.setAccessibleName("Текстовая команда")
        self.command_input.setPlaceholderText("Спросите или введите команду…")
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
        columns.addLayout(center, 39)

        right = QVBoxLayout()
        right.setSpacing(18)
        self.calendar_card, calendar_body = self._card("Сегодня", f"{datetime.now():%d.%m.%Y}")
        calendar_icon = label("", "calendarEmpty")
        calendar_icon.setPixmap(line_icon("calendar", "#8197b7", 40).pixmap(40, 40))
        calendar_body.addStretch()
        calendar_body.addWidget(calendar_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_title = label("День открыт для ваших планов", "emptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        calendar_body.addWidget(empty_title)
        empty_hint = label(
            "Подключение календаря появится\nв одном из следующих обновлений.", "muted"
        )
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        calendar_body.addWidget(empty_hint)
        calendar_body.addStretch()
        right.addWidget(self.calendar_card, 3)

        self.task_card, task_body = self._card("Сводка", "Этот сеанс")
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
        task_body.addWidget(label("ТЕКУЩАЯ КОМАНДА", "eyebrow"))
        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Здесь появится ваша команда")
        self.transcript.setFixedHeight(62)
        task_body.addWidget(self.transcript)
        self.action_label = label("Введите текст, чтобы проверить цикл демонстрации.", "muted")
        self.action_label.setAccessibleName("Результат задачи")
        task_body.addWidget(self.action_label)
        task_body.addStretch()
        quote = label("«Большие дела начинаются с маленького шага.»", "quote")
        task_body.addWidget(quote)
        right.addWidget(self.task_card, 3)

        suggestion, suggestion_body = self._card("Попробуем в деле?")
        suggestion.setObjectName("suggestion")
        suggestion_body.addWidget(
            label(
                "Откройте инструменты, чтобы управлять приложениями и проверять разрешения.",
                "muted",
            )
        )
        self.permissions_button = QPushButton("Открыть инструменты")
        self.permissions_button.setObjectName("primary")
        self.permissions_button.setIcon(line_icon("shield", "#e4eeff"))
        self.permissions_button.clicked.connect(self.open_permissions)
        suggestion_body.addWidget(self.permissions_button)
        right.addWidget(suggestion, 2)
        columns.addLayout(right, 35)

    @staticmethod
    def _card(title: str, caption: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        body = QVBoxLayout(card)
        body.setContentsMargins(20, 20, 20, 20)
        body.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label(title, "cardTitle"), 1)
        if caption:
            heading.addWidget(label(caption, "muted"))
        body.addLayout(heading)
        return card, body

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
        self.state_label.setText(STATE_LABELS[state].upper())
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
        self.permission_workbench.show()

    @Slot(int)
    def _permissions_closed(self, result: int) -> None:
        if self.permission_workbench is not None:
            self.permission_workbench.shutdown()
            self.permission_workbench.deleteLater()
            self.permission_workbench = None
