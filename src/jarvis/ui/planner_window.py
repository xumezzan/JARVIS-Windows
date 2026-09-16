"""Manual text planning surface with opt-in cloud disclosure and exact step approvals."""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Limits, PlanResult, Provider, Step
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.files.policy import FilePolicy
from jarvis.mail.session import MailSession
from jarvis.memory.store import MemoryFailure, MemoryStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.platforms.files import LocalFiles
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.browser import register_browser
from jarvis.tools.files import register_files
from jarvis.tools.local import local_registry
from jarvis.tools.outlook import register_outlook
from jarvis.tools.windows import WindowsBackend, register_windows
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.mail_panel import MailPanel
from jarvis.ui.memory_panel import MemoryPanel
from jarvis.ui.planner_worker import PlannerWorker, Prompt
from jarvis.ui.voice_panel import VoicePanel


def note(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


class PlannerWindow(QDialog):
    task_finished = Signal(str)
    progress_event = Signal(str, object)

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        *,
        provider: Provider | None = None,
        browser_host: BrowserHost | None = None,
        windows_backend: WindowsBackend | None = None,
        limits: Limits | None = None,
        voice_panel: VoicePanel | None = None,
        mail_session: MailSession | None = None,
        embed_voice: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jarvis — планировщик команд")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 790)
        self.override_provider = provider
        self.limits = limits or Limits()
        self.host = browser_host or BrowserHost(NetworkPolicy(config.browser_origins))
        self.registry, self.outbox = local_registry()
        register_browser(self.registry, self.host, self.host.policy)
        register_windows(self.registry, windows_backend or ProcessBackend())
        self.files = FilePolicy(config.file_roots)
        register_files(self.registry, self.files, LocalFiles())
        self.mail_session = mail_session or MailSession()
        register_outlook(self.registry, self.mail_session)
        self.audit = AuditLog(config.data_dir / "audit.sqlite3")
        store = ApprovalStore()
        self.engine = PermissionEngine(self.registry, store, self.audit)
        self.authority = store.take_authority(self.audit.approved)
        self.worker: PlannerWorker | None = None
        self.last_result: PlanResult | None = None
        # The surface that owns approval and clarification dialogs; the main window sets
        # itself here so a command typed there never needs this window on screen.
        self.prompt_parent: QWidget = self
        self.approval_dialog: ApprovalDialog | None = None
        self.question_dialog: QDialog | None = None
        self.answer_input: QLineEdit | None = None
        self.answer_button: QPushButton | None = None
        self._closing = False
        self._closed = False
        self._mail_active = False
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        task = QWidget()
        self.tabs.addTab(task, "Команда")
        self.memory = MemoryPanel(MemoryStore(config.data_dir / "memory.sqlite3"))
        memory_scroll = QScrollArea()
        memory_scroll.setWidgetResizable(True)
        memory_scroll.setWidget(self.memory)
        self.tabs.addTab(memory_scroll, "Память")
        self.mail = MailPanel(self.mail_session, self.engine, self.authority, self.audit)
        mail_scroll = QScrollArea()
        mail_scroll.setWidgetResizable(True)
        mail_scroll.setWidget(self.mail)
        self.tabs.addTab(mail_scroll, "Outlook")
        layout = QVBoxLayout(task)
        layout.addWidget(
            note(
                "Команда → один предложенный шаг → проверка разрешений → наблюдение результата. "
                "Браузерный сеанс временный: вкладки и введённый текст удаляются при закрытии окна."
            )
        )
        self.provider_choice = QComboBox()
        self.provider_choice.addItems(["Офлайн: учебные команды, без LLM", "OpenAI: Responses API"])
        layout.addWidget(self.provider_choice)
        self.cloud_box = QWidget()
        cloud = QVBoxLayout(self.cloud_box)
        self.model = QLineEdit(config.planner_model)
        self.model.setPlaceholderText("Идентификатор доступной вам модели Responses API")
        self.model.setMaxLength(100)
        cloud.addWidget(self.model)
        cloud.addWidget(
            note(
                "Ключ: python -m jarvis.security.credentials set (в терминале). "
                "Он хранится в Windows Credential Locker / macOS Keychain. "
                "Не вставляйте ключ в команду."
            )
        )
        self.cloud_consent = QCheckBox(
            "Разрешаю отправить команду, уточнения и результаты инструментов в OpenAI"
        )
        cloud.addWidget(self.cloud_consent)
        cloud.addWidget(
            note(
                "API тарифицируется отдельно. В облачном режиме запросы модели отправляются "
                "даже при симуляции инструментов. "
                "store=false не означает отсутствие хранения у провайдера."
            )
        )
        layout.addWidget(self.cloud_box)
        self.cloud_box.hide()
        self.provider_choice.currentIndexChanged.connect(
            lambda index: self.cloud_box.setVisible(index == 1)
        )
        self.command = QPlainTextEdit()
        self.command.setMaximumHeight(110)
        self.command.setPlaceholderText(
            "проверь систему дважды\nоткрой https://example.com/\nоткрой блокнот и напиши «Привет»"
        )
        self.voice = voice_panel or VoicePanel()
        self.voice.transcript_ready.connect(self.command.setPlainText)
        self.voice.cancel_requested.connect(self.stop)
        self.voice.busy_changed.connect(self._voice_busy)
        if embed_voice:
            # Otherwise the owning window places the same panel next to its command bar.
            layout.addWidget(self.voice)
        layout.addWidget(self.command)
        self.simulation = QCheckBox("Simulation Mode — инструменты не выполняются")
        self.simulation.setChecked(True)
        layout.addWidget(self.simulation)
        self.autonomous = QCheckBox("Автономный режим — шаги CONFIRM подтверждаются без диалога")
        layout.addWidget(self.autonomous)
        layout.addWidget(
            note(
                "В автономном режиме подтверждение выдаётся тем же одноразовым токеном, "
                "привязанным к точному снимку действия, но без вашего просмотра. "
                "Ошибочный шаг выполнится без остановки; BLOCKED и CRITICAL остаются запрещены."
            )
        )
        buttons = QHBoxLayout()
        self.run_button = QPushButton("Запустить планировщик")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.status = note("Готово. По умолчанию: офлайн-провайдер и симуляция.")
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)
        self.memory.busy_changed.connect(
            lambda value: self._voice_busy(self.voice.worker is not None)
        )
        self.mail.busy_changed.connect(self._mail_busy)

    def _mail_busy(self, value: bool) -> None:
        self._mail_active = value
        self.voice.set_planning(value)
        self._voice_busy(self.voice.worker is not None)

    def _busy(self, value: bool) -> None:
        for widget in (
            self.provider_choice,
            self.model,
            self.cloud_consent,
            self.command,
            self.simulation,
            self.autonomous,
            self.run_button,
            self.memory,
            self.mail,
        ):
            widget.setEnabled(not value)
        self.stop_button.setEnabled(value)
        self.voice.set_planning(value)
        self._voice_busy(self.voice.worker is not None)

    def _voice_busy(self, value: bool) -> None:
        busy = value or self.voice.planning or self.mail.busy
        self.run_button.setEnabled(not busy and self.memory.worker is None)
        self.command.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.mail.setEnabled(
            not value and self.worker is None and (not self.voice.planning or self._mail_active)
        )

    @Slot()
    def start(self) -> None:
        if (
            self.worker is not None
            or self.voice.worker is not None
            or self.voice.settings_dialog is not None
            or self.mail.busy
            or self._closing
        ):
            return
        command = self.command.toPlainText()
        if not command.strip() or len(command) > 4000:
            self.status.setText("Введите команду от 1 до 4 000 символов.")
            return
        try:
            memory = self.memory.snapshot()
        except (ValueError, MemoryFailure):
            self.status.setText("Проверьте вкладку «Память»: загрузка и лимит выбранных записей.")
            return
        if (
            self.provider_choice.currentIndex() == 1
            and not memory.empty
            and not self.memory.cloud_consent.isChecked()
        ):
            self.status.setText(
                "Во вкладке «Память» разрешите передачу выбранных меток или снимите выбор."
            )
            return
        provider = self.override_provider
        if provider is None:
            if self.provider_choice.currentIndex() == 1:
                if not self.cloud_consent.isChecked():
                    self.status.setText("Для OpenAI нужно разрешить передачу команды и наблюдений.")
                    return
                try:
                    provider = OpenAIProvider(self.model.text())
                except ValueError:
                    self.status.setText("Укажите идентификатор модели Responses API.")
                    return
            else:
                provider = OfflineProvider()
        self.output.clear()
        self.last_result = None
        self.voice.was_cancelled = False
        self._busy(True)
        self.worker = PlannerWorker(
            self.registry,
            self.engine,
            provider,
            command,
            Mode.SIMULATION if self.simulation.isChecked() else Mode.EXECUTE,
            self.limits,
            memory,
        )
        self.worker.progress_event.connect(self._event)
        self.worker.prompt.connect(self._prompt)
        self.worker.finished.connect(self._finished)
        self.memory.consume_selection()
        self.worker.start()

    @Slot(str, object)
    def _event(self, kind: str, value: object) -> None:
        if self._closing or self.worker is None:
            return
        self.progress_event.emit(kind, value)
        if kind == "thinking":
            self.status.setText(f"Подготовка следующего шага ({value})…")
        elif kind == "executing":
            self.status.setText("Выполняется: " + str(value))
        elif kind == "tool" and isinstance(value, Step):
            self.output.appendPlainText(value.tool + ": " + value.outcome.status.value)
            if value.outcome.result_json is not None:
                self.output.appendPlainText(
                    "Наблюдение инструмента (данные):\n" + value.outcome.result_json[:60000]
                )

    @Slot(object)
    def _prompt(self, prompt: Prompt) -> None:
        worker = self.worker
        if worker is None or self._closing or worker.runner.cancelled.is_set():
            return
        if prompt.kind == "approval" and isinstance(prompt.value, Action):
            if self.autonomous.isChecked():
                self._approve_without_review(prompt.id, prompt.value)
                return
            self.status.setText("Ожидается подтверждение точного действия.")
            dialog = ApprovalDialog(prompt.value, self.authority, self.prompt_parent)
            self.approval_dialog = dialog
            dialog_layout = dialog.layout()
            assert dialog_layout is not None
            dialog_layout.addWidget(self.voice.stop_controls(dialog))

            def approved(result: int) -> None:
                self.approval_dialog = None
                worker.respond(
                    prompt.id, dialog.token if result == QDialog.DialogCode.Accepted else None
                )
                dialog.deleteLater()

            dialog.finished.connect(approved)
            dialog.open()
        elif prompt.kind == "clarification" and isinstance(prompt.value, str):
            self.status.setText("Нужно уточнение команды.")
            self.voice.announce(prompt.value)
            question = QDialog(self.prompt_parent)
            question.setWindowTitle("Уточнение команды")
            question.setWindowModality(Qt.WindowModality.WindowModal)
            question.resize(620, 250)
            body = QVBoxLayout(question)
            body.addWidget(note("Вопрос планировщика; это не подтверждение действия."))
            body.addWidget(note(prompt.value))
            answer = QLineEdit()
            answer.setMaxLength(4000)
            body.addWidget(answer)
            button = QPushButton("Ответить")
            button.clicked.connect(lambda: question.accept() if answer.text().strip() else None)
            body.addWidget(button)
            body.addWidget(self.voice.stop_controls(question))
            self.question_dialog, self.answer_input, self.answer_button = question, answer, button

            def answered(result: int) -> None:
                self.question_dialog = None
                worker.respond(
                    prompt.id, answer.text() if result == QDialog.DialogCode.Accepted else None
                )
                question.deleteLater()

            question.finished.connect(answered)
            question.open()

    def _approve_without_review(self, prompt_id: str, action: Action) -> None:
        """Autonomous mode: the user enabled standing approval instead of per-step review.

        The token still comes from the UI-owned authority, is audited before issue, expires,
        and stays bound to this exact action snapshot; only the human check is skipped.
        """
        worker = self.worker
        if worker is None:
            return
        try:
            token = self.authority.approve(action)
        except ValueError:
            worker.respond(prompt_id, None)
            return
        self.status.setText("Автономное подтверждение выдано: " + action.tool)
        self.output.appendPlainText("Автономное подтверждение (без просмотра): " + action.tool)
        worker.respond(prompt_id, token)

    def run_command(
        self, command: str, *, execute: bool, autonomous: bool, cloud: bool = False
    ) -> bool:
        """Programmatic entry for the main window's command bar. Returns False if not started."""
        self.command.setPlainText(command)
        self.simulation.setChecked(not execute)
        self.autonomous.setChecked(autonomous)
        self.provider_choice.setCurrentIndex(1 if cloud else 0)
        self.start()
        return self.worker is not None

    @Slot()
    def stop(self) -> None:
        self.voice.cancel()
        self.mail.stop()
        if self.worker is not None:
            self.worker.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText("Остановка; уже выданные действия не отзываются…")
        for dialog in (self.approval_dialog, self.question_dialog):
            if dialog is not None:
                dialog.reject()

    @Slot()
    def _finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        for dialog in (self.approval_dialog, self.question_dialog):
            if dialog is not None:
                dialog.reject()
        self.worker = None
        self.last_result = worker.outcome
        self.status.setText(worker.outcome.summary)
        self._busy(False)
        self.voice.finish_plan(worker.outcome)
        self.task_finished.emit(worker.outcome.status)
        worker.deleteLater()
        if self._closing:
            self.close()

    def shutdown(self) -> None:
        self._closing = True
        self.voice.shutdown()
        self.memory.shutdown()
        self.mail.shutdown()
        if self.worker is not None:
            self.stop()
            self.worker.wait()
            self._finished()
        if not self._closed:
            self._closed = True
            try:
                self.host.shutdown()
            finally:
                self.audit.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.voice.cancel()
        if self.worker is not None:
            self.stop()
            event.ignore()
        else:
            self.shutdown()
            super().closeEvent(event)

    def reject(self) -> None:
        # Escape must cancel and drain the worker, just like the window close button.
        self._closing = True
        self.voice.cancel()
        if self.worker is not None:
            self.stop()
        else:
            self.shutdown()
            super().reject()
