"""Minimal task launcher with distinct history, tools and settings pages."""

from uuid import UUID, uuid4

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig
from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.observability.logging import ShellLog
from jarvis.ui import workers
from jarvis.ui.activity_log import ActivityLog
from jarvis.ui.compact_planner import PlannerWindow
from jarvis.ui.compact_theme import PALETTES, STYLESHEET, build_stylesheet, load_fonts
from jarvis.ui.components import MonthCalendar
from jarvis.ui.dashboard import OrbWidget, line_icon
from jarvis.ui.demo_worker import DemoOutcome, DemoWorker
from jarvis.ui.permission_workbench import PermissionWorkbench
from jarvis.ui.states import STATE_LABELS, UiState


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
        self.setWindowTitle("Jarvis")
        self.resize(1200, 820)
        self.setMinimumSize(860, 640)
        load_fonts()
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._set_state(UiState.IDLE)
        self._record(ShellEvent.STARTED)
        self.command_input.setFocus()
        self._submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._submit_shortcut.activated.connect(self._submit_current_page)
        self._stop_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._stop_shortcut.activated.connect(self.stop)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("dashboard")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(16, 28, 16, 24)
        side.setSpacing(8)
        self.wordmark = label("Jarvis", "wordmark")
        side.addWidget(self.wordmark)
        side.addSpacing(36)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, title, icon in (
            ("assistant", "Ассистент", "chat"),
            ("history", "История", "clock"),
            ("tools", "Инструменты", "apps"),
            ("settings", "Настройки", "settings"),
        ):
            button = QPushButton(title)
            button.setObjectName("nav")
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.setIcon(line_icon(icon))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.toggled.connect(
                lambda checked, target=key: self._navigate(target) if checked else None
            )
            self.navigation.addButton(button)
            self.nav_buttons[key] = button
            if key == "settings":
                side.addStretch()
            side.addWidget(button)
        self.sidebar_hint = label("На этом устройстве", "sectionHint")
        side.addSpacing(12)
        side.addWidget(self.sidebar_hint)
        root.addWidget(self.sidebar)

        workspace = QVBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        root.addLayout(workspace, 1)
        header = QHBoxLayout()
        header.setContentsMargins(32, 20, 32, 16)
        self.page_title = label("Ассистент", "pageTitle")
        header.addWidget(self.page_title)
        header.addStretch()
        self.notice_label = label("", "state")
        self.notice_label.setAccessibleName("Ошибка открытия рабочего окна")
        self.notice_label.hide()
        header.addWidget(self.notice_label)
        self.stop_button = QPushButton("Остановить демо · Esc")
        self.stop_button.setObjectName("stop")
        self.stop_button.setEnabled(False)
        self.stop_button.hide()
        self.stop_button.clicked.connect(self.stop)
        header.addWidget(self.stop_button)
        workspace.addLayout(header)
        self.page_stack = QStackedWidget()
        workspace.addWidget(self.page_stack, 1)
        self.pages: dict[str, QWidget] = {}
        self.page_scrolls: dict[str, QScrollArea] = {}

        assistant = self._page("assistant", 680)
        assistant.addStretch(2)
        self.orb = OrbWidget()
        self.orb.setMinimumSize(0, 0)
        self.orb.setFixedSize(160, 160)
        assistant.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        assistant_title = label("Что нужно сделать?", "heroTitle")
        assistant_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        assistant.addWidget(assistant_title)
        self.state_label = label("Текстом или голосом", "state")
        self.state_label.setAccessibleName("Способ ввода команды")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        assistant.addWidget(self.state_label)
        assistant.addSpacing(20)
        self.command_frame = QFrame()
        self.command_frame.setObjectName("commandBar")
        composer = QVBoxLayout(self.command_frame)
        composer.setContentsMargins(20, 16, 16, 16)
        composer.setSpacing(12)
        command_label = label("Ваша команда", "sectionHint")
        composer.addWidget(command_label)
        self.command_input = QPlainTextEdit()
        self.command_input.setObjectName("commandInput")
        self.command_input.setAccessibleName("Текстовая команда")
        self.command_input.setPlaceholderText("Например, проверь систему дважды")
        self.command_input.setFixedHeight(80)
        self.command_input.installEventFilter(self)
        command_label.setBuddy(self.command_input)
        composer.addWidget(self.command_input)
        actions = QHBoxLayout()
        self.microphone_button = QPushButton("Голосовой ввод")
        self.microphone_button.setObjectName("quiet")
        self.microphone_button.setIcon(line_icon("mic"))
        self.microphone_button.setAccessibleName("Открыть голосовой ввод в планировщике")
        self.microphone_button.setToolTip("Запись начнётся только по удержанию кнопки в ассистенте")
        self.microphone_button.clicked.connect(self.open_planner)
        actions.addWidget(self.microphone_button)
        actions.addStretch()
        self.planner_button = QPushButton("Продолжить")
        self.planner_button.setObjectName("primary")
        self.planner_button.setToolTip("Открыть команду для проверки · Ctrl+Enter")
        self.planner_button.clicked.connect(self.open_planner)
        actions.addWidget(self.planner_button)
        composer.addLayout(actions)
        assistant.addWidget(self.command_frame)
        hint = label("Проверьте команду и режим перед запуском. Ctrl+Enter — продолжить.", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        assistant.addWidget(hint)
        assistant.addStretch(3)

        history = self._page("history")
        history.addWidget(label("История сеанса", "title"))
        history.addWidget(label("События оболочки с момента открытия приложения.", "muted"))
        history.addSpacing(16)
        self.activity = ActivityLog(self.config.activity_limit)
        self.activity.setObjectName("activity")
        self.activity.setMinimumHeight(320)
        history.addWidget(self.activity, 1)
        self._demo_completed = 0
        self.summary_label = label("Завершено демонстраций: 0", "sectionHint")
        history.addWidget(self.summary_label)

        tools = self._page("tools")
        tools.addWidget(label("Инструменты", "title"))
        tools.addWidget(
            label("Откройте нужное рабочее окно. Действия требуют проверки разрешений.", "muted")
        )
        tools.addSpacing(20)
        self.permissions_button = QPushButton("Браузер и приложения")
        self.permissions_button.setObjectName("toolEntry")
        self.permissions_button.setIcon(line_icon("apps"))
        self.permissions_button.clicked.connect(self.open_permissions)
        tools.addWidget(self.permissions_button)
        tools.addWidget(
            label("Наблюдение окон, Блокнот, VS Code и временный браузерный сеанс.", "muted")
        )
        tools.addSpacing(24)
        self.demo_toggle = QPushButton("Демонстрация интерфейса")
        self.demo_toggle.setObjectName("disclosure")
        self.demo_toggle.setCheckable(True)
        tools.addWidget(self.demo_toggle)
        tools.addWidget(
            label("Проверка ввода, отмены и результата без выполнения команд.", "muted")
        )
        self.demo_panel = QWidget()
        demo = QVBoxLayout(self.demo_panel)
        demo.setContentsMargins(0, 12, 0, 0)
        demo.setSpacing(12)
        self.demo_input = QPlainTextEdit()
        self.demo_input.setAccessibleName("Команда для демонстрации")
        self.demo_input.setPlaceholderText("Введите любой текст для проверки интерфейса")
        self.demo_input.setFixedHeight(80)
        demo.addWidget(self.demo_input)
        demo_controls = QHBoxLayout()
        self.demo_mode = QComboBox()
        self.demo_mode.addItems(["Обычная демонстрация", "Проверить ошибку"])
        self.demo_mode.setAccessibleName("Сценарий демонстрации")
        demo_controls.addWidget(self.demo_mode)
        demo_controls.addStretch()
        self.submit_button = QPushButton("Запустить демо")
        self.submit_button.clicked.connect(self.submit)
        demo_controls.addWidget(self.submit_button)
        demo.addLayout(demo_controls)
        self.validation_label = label("Демонстрация не выполняет введённую команду.", "muted")
        demo.addWidget(self.validation_label)
        self.demo_state_label = label("", "state")
        self.demo_state_label.setAccessibleName("Состояние демонстрации")
        demo.addWidget(self.demo_state_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        demo.addWidget(self.progress)
        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Текст демонстрации появится здесь")
        self.transcript.setFixedHeight(62)
        demo.addWidget(self.transcript)
        self.action_label = label("Результат появится после запуска.", "muted")
        self.action_label.setAccessibleName("Результат задачи")
        demo.addWidget(self.action_label)
        tools.addWidget(self.demo_panel)
        self.demo_panel.hide()
        self.demo_toggle.toggled.connect(self.demo_panel.setVisible)
        tools.addSpacing(24)
        calendar_toggle = QPushButton("Локальный календарь")
        calendar_toggle.setObjectName("disclosure")
        calendar_toggle.setCheckable(True)
        tools.addWidget(calendar_toggle)
        self.calendar_card = QWidget()
        calendar = QVBoxLayout(self.calendar_card)
        calendar.setContentsMargins(0, 12, 0, 0)
        calendar.addWidget(MonthCalendar())
        calendar.addWidget(label("Текущий месяц. Сервисы календаря не подключены.", "muted"))
        tools.addWidget(self.calendar_card)
        self.calendar_card.hide()
        calendar_toggle.toggled.connect(self.calendar_card.setVisible)
        tools.addStretch()

        settings = self._page("settings")
        settings.addWidget(label("Настройки", "title"))
        settings.addWidget(label("Оформление приложения и параметры ассистента.", "muted"))
        settings.addSpacing(24)
        settings.addWidget(label("Оформление", "cardTitle"))
        self.theme_picker = QComboBox()
        self.theme_picker.setObjectName("themePicker")
        self.theme_picker.setAccessibleName("Цветовая тема")
        self.theme_picker.addItems(list(PALETTES))
        self.theme_picker.currentTextChanged.connect(self._apply_theme)
        self._setting_row(
            settings, "Цветовая тема", "Палитра всех окон приложения", self.theme_picker
        )
        self.motion_button = QPushButton("Включена")
        self.motion_button.setObjectName("motion")
        self.motion_button.setCheckable(True)
        self.motion_button.setChecked(True)
        self.motion_button.setAccessibleName("Анимация сферы")
        self.motion_button.toggled.connect(self.orb.set_motion_enabled)
        self.motion_button.toggled.connect(
            lambda enabled: self.motion_button.setText("Включена" if enabled else "Выключена")
        )
        self._setting_row(settings, "Анимация", "Мягкое движение сферы", self.motion_button)
        settings.addWidget(label("Оформление действует до закрытия приложения.", "sectionHint"))
        settings.addSpacing(28)
        settings.addWidget(label("Ассистент", "cardTitle"))
        self.planner_links: list[QPushButton] = []
        for title, description, caption, tab in (
            ("Модель и голос", "Провайдер, локальная модель речи и озвучивание", "Открыть", 3),
            ("Память", "Просмотр и изменение явно сохранённых меток", "Открыть", 1),
            ("Почта Outlook", "Подключение учётной записи и работа с письмами", "Открыть", 2),
        ):
            button = QPushButton(caption)
            button.setAccessibleName(title)
            self.planner_links.append(button)
            button.clicked.connect(lambda checked=False, index=tab: self._open_planner_tab(index))
            self._setting_row(settings, title, description, button)
        settings.addWidget(
            label("Режим выполнения и согласия проверяются перед запуском задачи.", "sectionHint")
        )
        settings.addStretch()
        self._compact_layout: bool | None = None
        self._arrange_dashboard()
        self._navigate("assistant")

    def _page(self, key: str, width: int = 760) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        outer = QWidget()
        outer.setObjectName("page")
        row = QHBoxLayout(outer)
        row.setContentsMargins(32, 24, 32, 32)
        content = QWidget()
        content.setObjectName("column")
        content.setMaximumWidth(width)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        row.addStretch()
        row.addWidget(content, 1)
        row.addStretch()
        scroll.setWidget(outer)
        self.pages[key] = content
        self.page_scrolls[key] = scroll
        self.page_stack.addWidget(scroll)
        return layout

    @staticmethod
    def _setting_row(layout: QVBoxLayout, title: str, description: str, control: QWidget) -> None:
        row = QFrame()
        row.setObjectName("settingRow")
        body = QHBoxLayout(row)
        body.setContentsMargins(0, 16, 0, 16)
        body.setSpacing(24)
        text = QVBoxLayout()
        text.setSpacing(4)
        title_label = label(title)
        title_label.setBuddy(control)
        text.addWidget(title_label)
        text.addWidget(label(description, "muted"))
        body.addLayout(text, 1)
        control.setMinimumWidth(130)
        body.addWidget(control)
        layout.addWidget(row)

    @Slot(str)
    def _apply_theme(self, theme: str) -> None:
        stylesheet = build_stylesheet(theme)
        self.setStyleSheet(stylesheet)
        for dialog in (self.permission_workbench, self.planner_window):
            if dialog is not None:
                dialog.setStyleSheet(stylesheet)

    def _arrange_dashboard(self) -> None:
        compact = self.width() < 1000
        self._compact_layout = compact
        self.sidebar.setFixedWidth(76 if compact else 200)
        for button in self.nav_buttons.values():
            button.setText("" if compact else button.accessibleName())
        self.wordmark.setText("J" if compact else "Jarvis")
        self.sidebar_hint.setVisible(not compact)

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
        self.page_stack.setCurrentWidget(self.page_scrolls[target])
        self.page_title.setText(self.nav_buttons[target].accessibleName())
        self.scroll_area = self.page_scrolls[target]
        if target == "assistant":
            self.command_input.setFocus()
        else:
            self.page_title.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.page_title.setFocus()

    def _submit_current_page(self) -> None:
        if self.page_stack.currentWidget() is self.page_scrolls["assistant"]:
            self.open_planner()
        elif (
            self.page_stack.currentWidget() is self.page_scrolls["tools"]
            and self.demo_panel.isVisible()
        ):
            self.submit()

    def _open_planner_tab(self, index: int) -> None:
        self.open_planner()
        if self.planner_window is not None:
            self.planner_window.tabs.setCurrentIndex(index)

    def _record(self, event: ShellEvent) -> None:
        self.log.record(event, self._request_id)
        self.activity.append_event(event)
        if event == ShellEvent.DEMO_SUCCEEDED:
            self._demo_completed += 1
            self.summary_label.setText(f"Завершено демонстраций: {self._demo_completed}")

    def _set_state(self, state: UiState) -> None:
        self.state = state
        self.demo_state_label.setText(STATE_LABELS[state])
        self.demo_state_label.setProperty("status", state.value)
        self.demo_state_label.style().unpolish(self.demo_state_label)
        self.demo_state_label.style().polish(self.demo_state_label)
        self.state_changed.emit(state.value)

    def _set_busy(self, busy: bool) -> None:
        self.demo_input.setReadOnly(busy)
        self.command_input.setReadOnly(busy)
        self.planner_button.setEnabled(not busy)
        self.microphone_button.setEnabled(not busy)
        self.permissions_button.setEnabled(not busy)
        for link in self.planner_links:
            link.setEnabled(not busy)
        self.submit_button.setEnabled(not busy)
        self.demo_mode.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(0)

    @Slot()
    def submit(self) -> None:
        if self.worker is not None or self._closing:
            return
        command = self.demo_input.toPlainText().strip()
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
        self._record(ShellEvent.DEMO_SUBMITTED)
        worker = DemoWorker(
            self.config.demo_duration_ms,
            self.config.task_timeout_ms,
            self.demo_mode.currentIndex() == 1,
            self,
        )
        self.worker = worker
        worker.executing.connect(self._executing)
        worker.finished.connect(self._finished)
        workers.start(worker)

    @Slot()
    def _executing(self) -> None:
        if self.worker is not None and not self._cancel_requested:
            self._set_state(UiState.EXECUTING)
            self._record(ShellEvent.DEMO_EXECUTING)

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
            self._record(ShellEvent.DEMO_CANCEL_REQUESTED)

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
            DemoOutcome.SUCCESS: (UiState.SUCCESS, ShellEvent.DEMO_SUCCEEDED),
            DemoOutcome.ERROR: (UiState.ERROR, ShellEvent.DEMO_FAILED),
            DemoOutcome.CANCELLED: (UiState.CANCELLED, ShellEvent.DEMO_CANCELLED),
            DemoOutcome.TIMEOUT: (UiState.ERROR, ShellEvent.DEMO_TIMED_OUT),
        }
        state, event = states[outcome]
        self._set_busy(False)
        self._set_state(state)
        self.action_label.setText(EVENT_TEXT[event])
        self._record(event)
        self._request_id = None
        if self.page_stack.currentWidget() is self.page_scrolls["tools"]:
            self.demo_input.setFocus()
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
        self.notice_label.clear()
        self.notice_label.hide()
        if self.permission_workbench is None:
            try:
                self.permission_workbench = PermissionWorkbench(
                    self.config.data_dir,
                    self,
                    browser_host=BrowserHost(
                        self.config.browser_policy,
                        cdp_endpoint=self.config.browser_cdp_endpoint,
                    ),
                )
            except Exception:
                self.notice_label.setText(
                    "Не удалось открыть инструменты. Проверьте каталог данных."
                )
                self.notice_label.show()
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
        self.notice_label.clear()
        self.notice_label.hide()
        if self.planner_window is None:
            try:
                self.planner_window = PlannerWindow(self.config, self)
            except Exception:
                self.notice_label.setText(
                    "Не удалось открыть ассистента. Проверьте журнал и зависимости."
                )
                self.notice_label.show()
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


def run_compact(config: AppConfig, *, smoke_test: bool = False) -> int:
    """Keep the compact shell available without replacing main's connector workbench."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from jarvis.ui import garbage_collection

    application = QApplication(["jarvis-compact"])
    garbage_collection.install()
    window = MainWindow(config, ShellLog(config.data_dir / "logs"))
    application.aboutToQuit.connect(window.shutdown)
    verdict = 1

    def finished(outcome: str) -> None:
        nonlocal verdict
        verdict = 0 if outcome == "success" else 1
        print("Jarvis compact GUI smoke: " + ("success" if verdict == 0 else "failed"))
        window.close()

    if smoke_test:
        window.task_finished.connect(finished)
        window._navigate("tools")
        window.demo_toggle.setChecked(True)
        window.demo_input.setPlainText("Проверка компактного интерфейса")
        QTimer.singleShot(0, window.submit)
        QTimer.singleShot(config.task_timeout_ms + 2000, window.close)
    window.show()
    try:
        result = application.exec()
    finally:
        window.shutdown()
        if workers.drain():
            garbage_collection.stop()
        else:
            verdict = result = 1
    return verdict if smoke_test else result
