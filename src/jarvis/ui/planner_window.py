"""Manual text planning surface with opt-in cloud disclosure and exact step approvals."""

from pathlib import Path

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
from jarvis.core.composition import MCP_FILE, build
from jarvis.core.context.assembly import assemble
from jarvis.core.planner.contracts import Limits, PlanResult, Provider, Step
from jarvis.core.planner.deepseek_provider import DeepSeekProvider
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.core.planner.resume import restore
from jarvis.core.planner.routing import EscalatingRouter
from jarvis.core.report import written
from jarvis.core.workflow.journal import RunJournal
from jarvis.core.workflow.models import RunRecord
from jarvis.core.workflow.store import WorkflowFailure
from jarvis.mail.session import MailSession
from jarvis.memory.store import MemoryFailure, MemoryStore
from jarvis.permissions.approvals import Action
from jarvis.permissions.policies import Mode
from jarvis.security.cloud_consent import CloudConsent
from jarvis.security.credentials import setup_command
from jarvis.tools.windows import WindowsBackend
from jarvis.ui import workers
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.checklist import Checklist
from jarvis.ui.connections_panel import ConnectionsPanel
from jarvis.ui.mail_panel import MailPanel
from jarvis.ui.mcp_panel import McpPanel
from jarvis.ui.memory_panel import MemoryPanel
from jarvis.ui.planner_worker import PlannerWorker, Prompt
from jarvis.ui.routine_panel import RoutinePanel
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
        self.config = config
        self.override_provider = provider
        self.router: EscalatingRouter | None = None
        self.limits = limits or Limits.long()
        # One composition root owns what exists in this session; the window only uses it.
        self.bench = build(
            config,
            browser_host=browser_host,
            windows_backend=windows_backend,
            mail_session=mail_session,
        )
        self.host = self.bench.browser_host
        self.registry = self.bench.registry
        self.outbox = self.bench.outbox
        self.files = self.bench.files
        self.mail_session = self.bench.mail_session
        self.audit = self.bench.audit
        self.engine = self.bench.engine
        self.authority = self.bench.authority
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
        self.memory = MemoryPanel(
            MemoryStore(config.data_dir / "memory.sqlite3"), self.bench.derived
        )
        memory_scroll = QScrollArea()
        memory_scroll.setWidgetResizable(True)
        memory_scroll.setWidget(self.memory)
        self.tabs.addTab(memory_scroll, "Память")
        self.mail = MailPanel(self.mail_session, self.engine, self.authority, self.audit)
        mail_scroll = QScrollArea()
        mail_scroll.setWidgetResizable(True)
        mail_scroll.setWidget(self.mail)
        self.tabs.addTab(mail_scroll, "Outlook")
        self.mcp = McpPanel(Path(config.data_dir) / MCP_FILE)
        mcp_scroll = QScrollArea()
        mcp_scroll.setWidgetResizable(True)
        mcp_scroll.setWidget(self.mcp)
        self.tabs.addTab(mcp_scroll, "Серверы MCP")
        self.connections = ConnectionsPanel()
        connections_scroll = QScrollArea()
        connections_scroll.setWidgetResizable(True)
        connections_scroll.setWidget(self.connections)
        self.tabs.addTab(connections_scroll, "Подключения")
        layout = QVBoxLayout(task)
        layout.addWidget(
            note(
                "Команда → один предложенный шаг → проверка разрешений → наблюдение результата. "
                "Браузерный сеанс временный: вкладки и введённый текст удаляются при закрытии окна."
            )
        )
        self.provider_choice = QComboBox()
        self.provider_choice.addItems(
            [
                "Офлайн: учебные команды, без LLM",
                "Облако: DeepSeek, сложные задачи — OpenAI",
            ]
        )
        layout.addWidget(self.provider_choice)
        self.cloud_box = QWidget()
        cloud = QVBoxLayout(self.cloud_box)
        # An answer given once holds; a setting from the environment still outranks it.
        self.cloud_memory = CloudConsent(config.data_dir / "cloud-consent.json")
        self.model = QLineEdit(config.planner_model or self.cloud_memory.model)
        self.model.setPlaceholderText("Модель OpenAI для сложных задач, например gpt-5.4-mini")
        self.model.setMaxLength(100)
        cloud.addWidget(self.model)
        cloud.addWidget(
            note(
                "Ключи (в терминале, по одному на поставщика): "
                + setup_command("deepseek")
                + "  и  "
                + setup_command("openai")
                + ". Они хранятся в Windows Credential Locker / macOS Keychain. "
                "Не вставляйте ключ в команду."
            )
        )
        self.cloud_consent = QCheckBox(
            "Разрешаю отправить команду, уточнения и результаты инструментов в DeepSeek, "
            "а для сложных задач — в OpenAI"
        )
        cloud.addWidget(self.cloud_consent)
        cloud.addWidget(
            note(
                "API тарифицируется отдельно. В облачном режиме запросы модели отправляются "
                "даже при симуляции инструментов. "
                "store=false не означает отсутствие хранения у провайдера."
            )
        )
        self.cloud_consent.setChecked(self.cloud_memory.granted and bool(self.model.text()))
        # Connected after the stored answer is restored, so reading it is not a fresh answer.
        self.cloud_consent.toggled.connect(self.remember_cloud)
        self.model.editingFinished.connect(self.remember_cloud)
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
        unfinished = QHBoxLayout()
        self.unfinished = QComboBox()
        self.unfinished.setAccessibleName("Незавершённые задачи")
        self.resume_button = QPushButton("Продолжить прерванную задачу")
        self.resume_button.clicked.connect(self.resume)
        unfinished.addWidget(self.unfinished, 1)
        unfinished.addWidget(self.resume_button)
        layout.addLayout(unfinished)
        layout.addWidget(
            note(
                "Прерванная задача возобновляется по своему журналу: уже выданные действия "
                "не повторяются. Что она видела до перезапуска, не сохраняется — цели "
                "наблюдаются заново."
            )
        )
        self._resume: RunRecord | None = None
        self._list_unfinished()
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
        self.checklist = Checklist()
        layout.addWidget(self.checklist)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)
        # Routines need the voice panel, so their tab joins once the panel exists. They are
        # off until the owner switches them on, and this window only hosts the surface.
        self.routines = RoutinePanel(
            self.bench.routines, self.engine, self.authority, self.audit, voice=self.voice
        )
        routines_scroll = QScrollArea()
        routines_scroll.setWidgetResizable(True)
        routines_scroll.setWidget(self.routines)
        self.tabs.addTab(routines_scroll, "Рутины")
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
        self.unfinished.setEnabled(not value)
        self.resume_button.setEnabled(not value and bool(self._unfinished))
        self.stop_button.setEnabled(value)
        self.voice.set_planning(value)
        self._voice_busy(self.voice.worker is not None)

    @Slot()
    def remember_cloud(self) -> None:
        """Keep the last answer for the next launch. Unticking here is how it is taken back."""
        self.cloud_memory.remember(self.model.text().strip(), self.cloud_consent.isChecked())

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
        resume, self._resume = self._resume, None
        command = self.command.toPlainText()
        if not command.strip() or len(command) > 4000:
            self.status.setText("Введите команду от 1 до 4 000 символов.")
            return
        try:
            memory = self.memory.snapshot()
        except (ValueError, MemoryFailure):
            self.status.setText("Проверьте вкладку «Память»: загрузка и лимит выбранных записей.")
            return
        # Knowledge is selected by the command, so it is assembled here rather than chosen
        # in a panel; it leaves the machine under the same consent as the chosen labels.
        context = assemble(command, self.bench.knowledge, memory=memory, derived=self.bench.derived)
        if (
            self.provider_choice.currentIndex() == 1
            and not context.empty
            and not self.memory.cloud_consent.isChecked()
        ):
            self.status.setText(
                "Во вкладке «Память» разрешите передачу контекста задачи или снимите выбор меток."
            )
            return
        provider = self.override_provider
        if provider is None:
            if self.provider_choice.currentIndex() == 1:
                if not self.cloud_consent.isChecked():
                    self.status.setText("Для OpenAI нужно разрешить передачу команды и наблюдений.")
                    return
                try:
                    # Ordinary work runs on the fast model; the strong one takes over only
                    # when the task proves multi-layered or the fast answer is unusable.
                    self.router = EscalatingRouter(
                        DeepSeekProvider(self.config.fast_model),
                        OpenAIProvider(self.model.text()),
                    )
                    provider = self.router
                except ValueError:
                    self.status.setText("Укажите идентификатор модели Responses API.")
                    return
            else:
                provider = OfflineProvider()
        self.output.clear()
        if not context.knowledge.empty:
            # Say exactly what the command pulled in, rather than letting it travel unseen.
            named = ", ".join(
                f"{hint.name} ({', '.join(hint.services) or 'без сервисов'})"
                for hint in context.knowledge.entities
            )
            self.output.appendPlainText("Контекст задачи: " + named)
        if not context.derived.empty:
            # Learned words travel under the same consent, so they are named just as plainly.
            phrases = ", ".join(f"{hint.phrase} → {hint.means}" for hint in context.derived.phrases)
            self.output.appendPlainText("Выученное для задачи: " + phrases)
        self.last_result = None
        self.voice.was_cancelled = False
        self._busy(True)
        mode = Mode.SIMULATION if self.simulation.isChecked() else Mode.EXECUTE
        limits, journal = self._durable(command, mode, resume)
        steps = restore(resume) if resume is not None else ()
        self.checklist.reset()
        self.checklist.restore(steps)
        self.worker = PlannerWorker(
            self.registry,
            self.engine,
            provider,
            command,
            mode,
            limits,
            memory,
            context.knowledge,
            context.derived,
            self.bench.harvester,
            self.bench.learner,
            journal,
            steps,
        )
        self.worker.progress_event.connect(self._event)
        self.worker.prompt.connect(self._prompt)
        self.worker.finished.connect(self._finished)
        self.memory.consume_selection()
        workers.start(self.worker)

    def _list_unfinished(self) -> None:
        """Offer what was left in flight, newest first, with nothing invented about it."""
        self.unfinished.clear()
        self._unfinished: tuple[RunRecord, ...] = ()
        try:
            self._unfinished = self.bench.workflows.unfinished()
        except WorkflowFailure:
            self.unfinished.addItem("Журнал задач недоступен")
        else:
            for record in self._unfinished:
                waiting = {
                    "waiting_approval": "ждала подтверждения",
                    "waiting_input": "ждала ответа",
                }.get(record.phase, "прервана")
                done = sum(1 for step in record.steps if not step.unresolved)
                journalled = f"{done}/{len(record.steps)}"
                self.unfinished.addItem(
                    f"{record.request[:70]} — {waiting}, шагов в журнале: {journalled}"
                )
            if not self._unfinished:
                self.unfinished.addItem("Незавершённых задач нет")
        self.resume_button.setEnabled(bool(self._unfinished))

    @Slot()
    def resume(self) -> None:
        """Pick up an interrupted run: same request, same journal, same mode."""
        index = self.unfinished.currentIndex()
        if self.worker is not None or not (0 <= index < len(self._unfinished)):
            return
        record = self._unfinished[index]
        self._resume = record
        self.command.setPlainText(record.request)
        # The mode belongs to the run, not to the checkbox: a task that was executing does
        # not continue as a simulation, and a simulation does not become execution.
        self.simulation.setChecked(record.mode == Mode.SIMULATION.value)
        self.start()

    def _durable(
        self, command: str, mode: Mode, resume: RunRecord | None = None
    ) -> tuple[Limits, RunJournal | None]:
        """Open the run this task will be carried by, or say why it has to stay short.

        The long bounds are only honest while a journal records what was issued, so an
        unusable store costs the task its length rather than its safety: it runs under the
        short bounds, without resumption, and the owner is told that on screen.
        """
        if not self.limits.durable:
            return self.limits, None
        if resume is not None:
            return self.limits, RunJournal(self.bench.workflows, resume.id)
        try:
            record = self.bench.workflows.start(command, mode)
        except WorkflowFailure:
            self.output.appendPlainText(
                "Журнал задач недоступен: задача идёт короткой и не переживёт перезапуск."
            )
            return Limits(), None
        return self.limits, RunJournal(self.bench.workflows, record.id)

    @Slot(str, object)
    def _event(self, kind: str, value: object) -> None:
        if self._closing or self.worker is None:
            return
        self.progress_event.emit(kind, value)
        self.checklist.record(kind, value)
        if kind == "budget" and isinstance(value, tuple):
            spent, ceiling = value
            self.status.setText(f"Обращений к модели: {spent} из {ceiling}.")
        elif kind == "thinking":
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
            self.checklist.wait("approval")
            # The voice panel adds the spoken channel: the same authority, the same snapshot,
            # reached by repeating one detail of it instead of by a click.
            dialog = ApprovalDialog(
                prompt.value, self.authority, self.prompt_parent, voice=self.voice
            )
            self.approval_dialog = dialog
            dialog_layout = dialog.layout()
            assert dialog_layout is not None
            dialog_layout.addWidget(self.voice.stop_controls(dialog))

            def approved(result: int) -> None:
                self.approval_dialog = None
                self.checklist.wait(None)
                worker.respond(
                    prompt.id, dialog.token if result == QDialog.DialogCode.Accepted else None
                )
                dialog.deleteLater()

            dialog.finished.connect(approved)
            dialog.open()
        elif prompt.kind == "clarification" and isinstance(prompt.value, str):
            self.status.setText("Нужно уточнение команды.")
            self.checklist.wait("clarification")
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
                self.checklist.wait(None)
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
        self.checklist.settle()
        # A run that stopped mid-step is now offered for resumption; one that closed is not.
        self._list_unfinished()
        # What was done, in the owner's words, above the engine's own account of it.
        self.output.appendPlainText("\n".join(written(worker.outcome)))
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
        self.mcp.shutdown()
        self.connections.shutdown()
        self.routines.shutdown()
        if self.worker is not None:
            self.stop()
            self.worker.wait()
            self._finished()
        if not self._closed:
            self._closed = True
            self.bench.shutdown()

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
