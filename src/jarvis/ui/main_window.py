"""Desktop shell with isolated local permission tests; no external integrations or model."""

from uuid import UUID, uuid4

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
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

from jarvis.config import AppConfig
from jarvis.observability.events import EVENT_TEXT, ShellEvent
from jarvis.observability.logging import ShellLog
from jarvis.ui.activity_log import ActivityLog
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
        self.resize(1120, 800)
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
        central.setMinimumHeight(800)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(central)
        self.setCentralWidget(self.scroll_area)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(232)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 24, 20, 20)
        side.setSpacing(18)
        side.addWidget(label("JARVIS", "brand"))
        side.addWidget(label("Ваш рабочий помощник", "muted"))
        side.addWidget(label("DESKTOP PREVIEW", "badge"))
        side.addSpacing(12)
        side.addWidget(label("ПОДКЛЮЧЕНИЯ", "muted"))
        side.addWidget(
            label("Приложения не подключены.\nChrome, Notepad и VS Code — в следующих этапах.")
        )
        self.microphone_button = QPushButton("Микрофон недоступен")
        self.microphone_button.setEnabled(False)
        self.microphone_button.setToolTip("Push-to-talk появится на этапе 6. Запись не ведётся.")
        side.addWidget(self.microphone_button)
        side.addWidget(label("Голос: не записывается", "muted"))
        side.addWidget(label("РАЗРЕШЕНИЯ", "muted"))
        side.addWidget(label("Доступны локальные тестовые инструменты и точные подтверждения."))
        self.permissions_button = QPushButton("Проверить разрешения")
        self.permissions_button.clicked.connect(self.open_permissions)
        side.addWidget(self.permissions_button)
        side.addStretch()
        side.addWidget(label("Состояния будущих этапов", "muted"))
        side.addWidget(label("○ listening — этап 6\n● Подтверждения доступны", "muted"))
        side.addWidget(label("Текст остаётся в этом окне.\nВ журнале — только события.", "muted"))
        layout.addWidget(sidebar)

        body = QVBoxLayout()
        body.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(label("Начнём с команды", "title"), 1)
        heading.addWidget(label("ЭТАП 2", "badge"))
        body.addLayout(heading)
        body.addWidget(
            label("Локальная демонстрация интерфейса. Программы и сайты не открываются.", "muted")
        )
        self.state_label = label("", "state")
        self.state_label.setAccessibleName("Состояние задачи")
        body.addWidget(self.state_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        body.addWidget(self.progress)

        command_title = label("Ваша команда   ·   Ctrl+Enter для запуска", "muted")
        self.command_input = QPlainTextEdit()
        self.command_input.setAccessibleName("Текстовая команда")
        self.command_input.setPlaceholderText(
            "Например: Открой Блокнот и напиши Jarvis test successful"
        )
        self.command_input.setFixedHeight(88)
        command_title.setBuddy(self.command_input)
        body.addWidget(command_title)
        body.addWidget(self.command_input)
        self.validation_label = label("До 4 000 символов. Не вводите пароли и ключи.", "muted")
        body.addWidget(self.validation_label)

        controls = QHBoxLayout()
        self.demo_mode = QComboBox()
        self.demo_mode.addItems(["Обычная демонстрация", "Проверить ошибку"])
        self.demo_mode.setAccessibleName("Сценарий демонстрации")
        controls.addWidget(self.demo_mode, 1)
        self.submit_button = QPushButton("Запустить демо")
        self.submit_button.setObjectName("primary")
        self.submit_button.clicked.connect(self.submit)
        controls.addWidget(self.submit_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setObjectName("stop")
        self.stop_button.setToolTip("Остановить текущую демонстрацию (Esc)")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.stop_button)
        body.addLayout(controls)

        current = QGroupBox("Текущая задача")
        current_layout = QVBoxLayout(current)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Принятый текст команды")
        self.transcript.setPlaceholderText("Принятая команда появится здесь")
        self.transcript.setFixedHeight(64)
        current_layout.addWidget(self.transcript)
        self.action_label = label("Введите текст, чтобы проверить цикл демонстрации.")
        self.action_label.setAccessibleName("Результат задачи")
        current_layout.addWidget(self.action_label)
        body.addWidget(current)

        activity = QGroupBox("История действий")
        activity_layout = QVBoxLayout(activity)
        self.activity = ActivityLog(self.config.activity_limit)
        activity_layout.addWidget(self.activity)
        body.addWidget(activity, 1)
        layout.addLayout(body, 1)

    def _record(self, event: ShellEvent) -> None:
        self.log.record(event, self._request_id)
        self.activity.append_event(event)

    def _set_state(self, state: UiState) -> None:
        self.state = state
        self.state_label.setText(f"{STATE_LABELS[state]}  ·  {state.value}")
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
                self.permission_workbench = PermissionWorkbench(self.config.data_dir, self)
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
