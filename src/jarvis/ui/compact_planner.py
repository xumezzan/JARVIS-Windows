"""Manual text planning surface with opt-in cloud disclosure and exact step approvals."""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
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
from jarvis.core.planner.contracts import Limits, Provider, Step
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.mail.session import MailSession
from jarvis.memory.store import MemoryFailure, MemoryStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.browser import register_browser
from jarvis.tools.filesystem import register_filesystem
from jarvis.tools.local import LocalOutbox, local_registry
from jarvis.tools.outlook import register_outlook
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import WindowsBackend, register_windows
from jarvis.ui import workers
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
        local_tools: tuple[ToolRegistry, LocalOutbox] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jarvis — ассистент")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(960, 820)
        self.override_provider = provider
        self.workspace_profile = config.workspace_profile
        self.limits = limits or Limits()
        self.host = browser_host or BrowserHost.from_config(config)
        self.registry, self.outbox = local_tools if local_tools is not None else local_registry()
        self.offline_commands_label = "локальные" if local_tools is not None else "учебные"
        register_browser(self.registry, self.host, self.host.policy, workspace=self.host.workspace)
        register_windows(self.registry, windows_backend or ProcessBackend())
        register_filesystem(self.registry, config.filesystem_policy)
        self.mail_session = mail_session or MailSession()
        register_outlook(self.registry, self.mail_session)
        self.audit = AuditLog(config.data_dir / "audit.sqlite3")
        store = ApprovalStore()
        self.engine = PermissionEngine(self.registry, store, self.audit)
        self.authority = store.take_authority(self.audit.approved)
        self.worker: PlannerWorker | None = None
        self.approval_dialog: ApprovalDialog | None = None
        self.question_dialog: QDialog | None = None
        self.answer_input: QLineEdit | None = None
        self.answer_button: QPushButton | None = None
        self._closing = False
        self._closed = False
        self._mail_active = False
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        task = QWidget()
        self.tabs.addTab(task, "Задача")
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
        layout.setContentsMargins(12, 20, 12, 8)
        layout.setSpacing(12)
        title = note("Что нужно сделать?")
        title.setObjectName("title")
        layout.addWidget(title)
        intro = note(
            "Опишите задачу текстом или голосом. При необходимости Jarvis запросит уточнение."
        )
        intro.setObjectName("muted")
        layout.addWidget(intro)
        self.command = QPlainTextEdit()
        self.command.setAccessibleName("Команда для ассистента — проверьте текст перед запуском")
        self.command.setMinimumHeight(96)
        self.command.setMaximumHeight(120)
        self.command.setPlaceholderText(
            "Например: проверь систему дважды\nИли: открой блокнот и напиши «Привет»"
        )
        layout.addWidget(self.command)
        self.voice = voice_panel or VoicePanel()
        self.voice.transcript_ready.connect(self.command.setPlainText)
        self.voice.auto_start_requested.connect(self._voice_start)
        self.voice.cancel_requested.connect(self.stop)
        self.voice.busy_changed.connect(self._voice_busy)
        layout.addWidget(self.voice)

        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(12, 20, 12, 16)
        settings_layout.setSpacing(12)
        settings_title = note("Параметры ассистента")
        settings_title.setObjectName("title")
        settings_layout.addWidget(settings_title)
        settings_intro = note("Выберите обработку команд и настройте голосовой ввод.")
        settings_intro.setObjectName("muted")
        settings_layout.addWidget(settings_intro)
        provider_heading = note("Обработка команд")
        provider_heading.setObjectName("cardTitle")
        settings_layout.addWidget(provider_heading)
        provider_form = QFormLayout()
        provider_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        provider_form.setSpacing(12)
        self.provider_choice = QComboBox()
        self.provider_choice.setAccessibleName("Обработка команд")
        self.provider_choice.addItems(
            [f"Офлайн — {self.offline_commands_label} команды", "OpenAI — облачная модель"]
        )
        provider_form.addRow("Провайдер", self.provider_choice)
        settings_layout.addLayout(provider_form)
        provider_note = note(
            f"Офлайн распознаёт {self.offline_commands_label} команды без облачной модели. "
            "OpenAI получает команду, "
            "уточнения и результаты инструментов после вашего согласия на вкладке «Задача»."
        )
        provider_note.setObjectName("muted")
        settings_layout.addWidget(provider_note)
        self.model_settings = QWidget()
        model_layout = QVBoxLayout(self.model_settings)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(8)
        model_form = QFormLayout()
        model_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.model = QLineEdit(config.planner_model)
        self.model.setAccessibleName("Идентификатор модели OpenAI Responses API")
        self.model.setPlaceholderText("Идентификатор доступной вам модели Responses API")
        self.model.setMaxLength(100)
        model_form.addRow("Модель OpenAI", self.model)
        model_layout.addLayout(model_form)
        credential_note = note(
            "Ключ: python -m jarvis.security.credentials set (в терминале). "
            "Он хранится в Windows Credential Locker / macOS Keychain. "
            "Не вставляйте ключ в команду."
        )
        credential_note.setObjectName("muted")
        model_layout.addWidget(credential_note)
        settings_layout.addWidget(self.model_settings)
        settings_layout.addSpacing(16)
        voice_heading = note("Голос")
        voice_heading.setObjectName("cardTitle")
        settings_layout.addWidget(voice_heading)
        settings_layout.addWidget(self.voice.settings)
        settings_layout.addStretch(1)
        session_note = note(
            "Live Browser · CDP: подключённый Chrome сохраняет вкладки, входы и cookies."
            if self.host.cdp_endpoint
            else f"Браузер: {self.host.policy.mode.value}. "
            "Сеанс временный; его вкладки закрываются вместе с окном."
        )
        session_note.setObjectName("muted")
        settings_layout.addWidget(session_note)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)
        self.tabs.addTab(settings_scroll, "Настройки")

        task_options = QHBoxLayout()
        self.provider_summary = note("")
        self.provider_summary.setObjectName("muted")
        task_options.addWidget(self.provider_summary, 1)
        self.settings_button = QPushButton("Настройки…")
        self.settings_button.setAutoDefault(False)
        self.settings_button.clicked.connect(self.open_settings)
        task_options.addWidget(self.settings_button)
        layout.addLayout(task_options)
        self.simulation = QCheckBox("Симуляция — инструменты не выполняются")
        self.simulation.setChecked(True)
        layout.addWidget(self.simulation)
        self.cloud_box = QWidget()
        cloud = QVBoxLayout(self.cloud_box)
        cloud.setContentsMargins(0, 0, 0, 0)
        cloud.setSpacing(8)
        layout.addWidget(self.cloud_box)
        self.cloud_consent = QCheckBox(
            "Разрешаю отправить команду, уточнения, результаты инструментов, "
            "пути и текст прочитанных файлов, скриншоты, scratchpad и профиль "
            "рабочего пространства в OpenAI"
        )
        cloud.addWidget(self.cloud_consent)
        self.cloud_disclosure = note(
            "API тарифицируется отдельно. В облачном режиме запросы модели отправляются "
            "даже при симуляции инструментов. "
            "store=false не означает отсутствие хранения у провайдера."
        )
        self.cloud_disclosure.setObjectName("muted")
        cloud.addWidget(self.cloud_disclosure)
        self.provider_choice.currentIndexChanged.connect(self._provider_changed)
        self.model.textChanged.connect(self._update_provider_summary)
        self._provider_changed()
        buttons = QHBoxLayout()
        self.run_button = QPushButton("Запустить задачу")
        self.run_button.setAutoDefault(False)
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = note("Готово. По умолчанию: офлайн-провайдер и симуляция.")
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Шаги и результаты задачи")
        self.output.setPlaceholderText("Здесь появятся шаги и результаты задачи.")
        self.output.setMinimumHeight(120)
        layout.addWidget(self.output, 1)
        self.memory.busy_changed.connect(
            lambda value: self._voice_busy(self.voice.worker is not None)
        )
        self.mail.busy_changed.connect(self._mail_busy)

    @Slot()
    def open_settings(self) -> None:
        """Keep optional provider and voice configuration out of the task workspace."""
        self.tabs.setCurrentIndex(3)

    @Slot()
    def _provider_changed(self) -> None:
        cloud = self.provider_choice.currentIndex() == 1
        self.cloud_box.setVisible(cloud)
        self.model_settings.setVisible(cloud)
        self._update_provider_summary()

    @Slot()
    def _update_provider_summary(self) -> None:
        if self.provider_choice.currentIndex() == 1:
            model = self.model.text().strip() or "модель не выбрана"
            self.provider_summary.setText("OpenAI · " + model)
        else:
            self.provider_summary.setText(
                f"Офлайн · {self.offline_commands_label} команды без облачной модели"
            )

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
    def _voice_start(self) -> None:
        self.start()
        if self.worker is not None:
            self.voice.acknowledge()
        else:
            self.voice.message.emit(self.status.text())

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
                    self.status.setText(
                        "Для OpenAI нужно разрешить передачу команды, наблюдений "
                        "и прочитанного текста файлов."
                    )
                    return
                try:
                    provider = OpenAIProvider(self.model.text())
                except ValueError:
                    self.status.setText("Укажите идентификатор модели Responses API.")
                    return
            else:
                provider = OfflineProvider()
        self.output.clear()
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
            workspace=self.workspace_profile,
        )
        self.worker.progress_event.connect(self._event)
        self.worker.prompt.connect(self._prompt)
        self.worker.finished.connect(self._finished)
        self.memory.consume_selection()
        self.cloud_consent.setChecked(False)
        workers.start(self.worker)

    @Slot(str, object)
    def _event(self, kind: str, value: object) -> None:
        if self._closing or self.worker is None:
            return
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
            self.status.setText("Ожидается подтверждение точного действия.")
            dialog = ApprovalDialog(prompt.value, self.authority, self)
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
            question = QDialog(self)
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
