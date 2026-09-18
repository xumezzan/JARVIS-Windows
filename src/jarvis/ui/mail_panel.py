"""Explicit Outlook setup and composition; only the existing approval dialog grants authority."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.connectors.teams.connector import SURFACE as TEAMS_SURFACE
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account, Attachment, MailResult, Message
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalAuthority
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Decision, Mode, Risk, Status
from jarvis.tools.base import ExecutionContext
from jarvis.ui import workers
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.tool_worker import ToolWorker


class MailWorker(QThread):
    def __init__(self, work: Callable[[ExecutionContext], Awaitable[object]]) -> None:
        super().__init__()
        self.work = work
        self.cancelled = Event()
        self.result: object = None
        self.error = ""

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        async def run() -> object:
            job = asyncio.ensure_future(self.work(ExecutionContext(self.cancelled)))

            async def watch() -> None:
                while not self.cancelled.is_set():
                    await asyncio.sleep(0.02)

            watcher = asyncio.create_task(watch())
            try:
                done, _ = await asyncio.wait(
                    (job, watcher), timeout=160, return_when=asyncio.FIRST_COMPLETED
                )
                if job in done:
                    return await job
                raise MailFailure("mail_cancelled")
            finally:
                job.cancel()
                watcher.cancel()
                await asyncio.gather(job, watcher, return_exceptions=True)

        try:
            self.result = asyncio.run(run())
        except MailFailure as error:
            self.error = error.code
        except Exception:
            self.error = "mail_unavailable"


class MailPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(
        self,
        session: MailSession,
        engine: PermissionEngine,
        authority: ApprovalAuthority,
        audit: AuditLog,
    ) -> None:
        super().__init__()
        self.session, self.engine, self.authority = session, engine, authority
        self.audit = audit
        self.worker: MailWorker | ToolWorker | None = None
        self.action: Action | None = None
        self.dialog: ApprovalDialog | None = None
        self.attachments: list[Attachment] = []
        self._closing = False
        root = QVBoxLayout(self)
        self.identity = self.note("Outlook не подключён.")
        root.addWidget(self.identity)
        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.client = QLineEdit()
        self.client.setMaxLength(36)
        form.addRow("Application (client) ID", self.client)
        form.addRow(
            self.note(
                "Регистрация Microsoft: Mobile and desktop, redirect http://localhost. "
                "Вход откроется в системном браузере. Пароль вводится только у Microsoft. "
                "Разрешения: профиль, чтение/запись почты и отправка. Teams — отдельная "
                "кнопка и отдельное согласие на тот же аккаунт: чтение чатов и отправка "
                "сообщения с подтверждением."
            )
        )
        self.consent = QCheckBox("Разрешаю подключить Outlook с указанными правами")
        form.addRow(self.consent)
        row = QHBoxLayout()
        self.connect_button = QPushButton("Подключить / сменить аккаунт")
        self.disconnect_button = QPushButton("Отключить и удалить локальные токены")
        self.teams_button = QPushButton("Разрешить Teams")
        self.connect_button.clicked.connect(self.connect_account)
        self.disconnect_button.clicked.connect(self.disconnect_account)
        self.teams_button.clicked.connect(self.allow_teams)
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addWidget(self.teams_button)
        form.addRow(row)
        self.simulation = QCheckBox("Симуляция почтовых инструментов")
        self.simulation.setChecked(True)
        form.addRow(self.simulation)
        self.operation = QComboBox()
        for label, value in (
            ("Список писем", "outlook.list"),
            ("Прочитать выбранное письмо", "outlook.read"),
            ("Сохранить локальный черновик", "outlook.local_draft"),
            ("Сохранить черновик в Outlook — подтверждение", "outlook.save_draft"),
            ("Отправить письмо — подтверждение", "outlook.send"),
        ):
            self.operation.addItem(label, value)
        form.addRow("Действие", self.operation)
        self.folder = QComboBox()
        for label, value in (
            ("Входящие", "inbox"),
            ("Отправленные", "sentitems"),
            ("Черновики", "drafts"),
        ):
            self.folder.addItem(label, value)
        form.addRow("Папка", self.folder)
        self.messages = QComboBox()
        form.addRow("Полученные письма", self.messages)
        self.drafts = QComboBox()
        self.drafts.addItem("Выберите сохранённый локальный черновик", None)
        self.drafts.currentIndexChanged.connect(self.load_draft)
        form.addRow("Локальные черновики", self.drafts)
        self.to, self.cc, self.bcc, self.subject = (
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
        )
        for title, edit in (
            ("Кому", self.to),
            ("Копия", self.cc),
            ("Скрытая копия", self.bcc),
            ("Тема", self.subject),
        ):
            edit.setMaxLength(500 if edit is self.subject else 2600)
            form.addRow(title, edit)
        self.recipient_hint = self.note(
            "Точные email-адреса через запятую. Для имени или роли сначала уточните адрес."
        )
        form.addRow(self.recipient_hint)
        self.body = QPlainTextEdit()
        self.body.setMaximumHeight(130)
        form.addRow("Текст письма", self.body)
        self.file_label = self.note("Вложений нет. До 3 файлов, каждый до 32 KiB.")
        form.addRow(self.file_label)
        files = QHBoxLayout()
        self.files_layout = files
        self.add_file = QPushButton("Добавить файл")
        self.clear_files = QPushButton("Убрать вложения")
        self.add_file.clicked.connect(self.attach_file)
        self.clear_files.clicked.connect(self.remove_files)
        files.addWidget(self.add_file)
        files.addWidget(self.clear_files)
        form.addRow(files)
        self.run_button = QPushButton("Подготовить действие")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.prepare)
        form.addRow(self.run_button)
        root.addWidget(self.form_box)
        self.stop_button = QPushButton("Остановить / отменить")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        root.addWidget(self.stop_button)
        self.status = self.note(
            "Сначала подключите аккаунт. Отправка требует отдельного подтверждения."
        )
        root.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(160)
        root.addWidget(self.output)
        self.operation.currentIndexChanged.connect(self.show_fields)
        self._form = form
        self.show_fields()
        for edit in (self.to, self.cc, self.bcc, self.subject, self.client):
            edit.textChanged.connect(self.invalidate_preview)
        self.body.textChanged.connect(self.invalidate_preview)
        self.simulation.toggled.connect(self.invalidate_preview)
        self.operation.currentIndexChanged.connect(self.invalidate_preview)

    def invalidate_preview(self) -> None:
        if self.dialog is not None:
            self.stop()

    @staticmethod
    def note(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def show_fields(self) -> None:
        tool = str(self.operation.currentData())
        compose = tool in ("outlook.local_draft", "outlook.save_draft", "outlook.send")
        for widget in (self.to, self.cc, self.bcc, self.subject, self.body, self.drafts):
            self._form.setRowVisible(widget, compose)
        self._form.setRowVisible(self.folder, tool == "outlook.list")
        self._form.setRowVisible(self.messages, tool == "outlook.read")
        self._form.setRowVisible(self.recipient_hint, compose)
        self._form.setRowVisible(self.file_label, compose)
        self._form.setRowVisible(self.files_layout, compose)
        self.add_file.setEnabled(compose)
        self.clear_files.setEnabled(compose)

    @property
    def busy(self) -> bool:
        return self.worker is not None or self.dialog is not None

    def set_busy(self, value: bool) -> None:
        self.form_box.setEnabled(not value)
        self.stop_button.setEnabled(value)
        self.busy_changed.emit(value)

    def launch(
        self, work: Callable[[ExecutionContext], Awaitable[object]], *, connection: bool = False
    ) -> None:
        if self.busy or self._closing:
            return
        self.set_busy(True)
        self.status.setText("Выполняется… Остановить можно кнопкой ниже.")

        async def audited(context: ExecutionContext) -> object:
            request_id = uuid4()
            try:
                self.audit.write(
                    AuditEvent(
                        AuditKind.STARTED,
                        request_id,
                        "outlook.account",
                        None,
                        Mode.EXECUTE,
                        Decision.ALLOW,
                        actor="user_ui_connection",
                    )
                )
            except Exception:
                raise MailFailure("mail_audit") from None
            status = Status.ERROR
            try:
                result = await work(context)
                status = Status.SUCCESS
                return result
            except asyncio.CancelledError:
                status = Status.CANCELLED
                raise
            finally:
                try:
                    self.audit.write(
                        AuditEvent(
                            AuditKind.FINISHED,
                            request_id,
                            "outlook.account",
                            None,
                            Mode.EXECUTE,
                            Decision.ALLOW,
                            status,
                            error=ErrorCode.NONE
                            if status is Status.SUCCESS
                            else ErrorCode.EXECUTION,
                            may_have_effects=True,
                            actor="user_ui_connection",
                        )
                    )
                except Exception:
                    self.session.detach()
                    raise MailFailure("mail_audit") from None

        worker = MailWorker(audited if connection else work)
        self.worker = worker
        worker.finished.connect(self.finished)
        workers.start(worker)

    def reset_account(self) -> None:
        self.engine.cancel_all()
        self.session.detach()
        self.attachments.clear()
        self.messages.clear()
        self.drafts.clear()
        self.drafts.addItem("Выберите сохранённый локальный черновик", None)
        self.output.clear()
        for edit in (self.to, self.cc, self.bcc, self.subject):
            edit.clear()
        self.body.clear()
        self.file_label.setText("Вложений нет.")
        self.identity.setText("Outlook не подключён.")

    def connect_account(self) -> None:
        if self.busy or not self.consent.isChecked():
            self.status.setText("Подключение требует галочку согласия и Application (client) ID.")
            return
        from uuid import UUID

        try:
            client_id = str(UUID(self.client.text()))
        except ValueError:
            self.status.setText(
                "Application (client) ID должен быть UUID из регистрации Microsoft."
            )
            return
        self.reset_account()
        self.consent.setChecked(False)
        self.launch(lambda context: self.session.connect(client_id, context), connection=True)

    def allow_teams(self) -> None:
        """One more consent on the account that is already signed in.

        Teams is asked for separately rather than folded into the Outlook consent: a tenant
        that refuses chat scopes then costs Jarvis the chats and leaves the mailbox working.
        """
        account = self.session.account
        if self.busy:
            return
        if account is None:
            self.status.setText("Сначала подключите аккаунт Microsoft, потом разрешите Teams.")
            return

        async def allow(context: ExecutionContext) -> object:
            await self.session.consent(account, TEAMS_SURFACE, context)
            return "Teams разрешён для этого аккаунта: чаты читаются, отправка спрашивает."

        self.launch(allow, connection=True)

    def disconnect_account(self) -> None:
        if self.busy:
            return
        self.reset_account()
        self.consent.setChecked(False)

        async def disconnect(context: ExecutionContext) -> object:
            await self.session.disconnect()
            return (
                "Аккаунт отключён, локальные токены удалены. "
                "Согласие Microsoft можно отозвать в аккаунте."
            )

        self.launch(disconnect, connection=True)

    def attach_file(self) -> None:
        if self.busy or self.session.account is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Выберите вложение до 32 KiB")
        if not path:
            return

        async def attach(context: ExecutionContext) -> object:
            from jarvis.mail.attachments import read_attachment

            file = Path(path)
            content = await read_attachment(path)
            await context.checkpoint()
            return self.session.attach(file.name, content)

        self.launch(attach)

    def remove_files(self) -> None:
        self.invalidate_preview()
        self.session.clear_attachments()
        self.attachments.clear()
        self.file_label.setText("Вложений нет.")

    def load_draft(self) -> None:
        value = self.drafts.currentData()
        if not isinstance(value, Message):
            return
        for edit, addresses in ((self.to, value.to), (self.cc, value.cc), (self.bcc, value.bcc)):
            edit.setText(", ".join(addresses))
        self.subject.setText(value.subject)
        self.body.setPlainText(value.body)
        self.attachments = list(value.attachments)
        self.file_label.setText("\n".join(a.name for a in self.attachments) or "Вложений нет.")

    def prepare(self) -> None:
        if self.busy or self.session.account is None:
            self.status.setText("Сначала подключите Outlook.")
            return
        tool = str(self.operation.currentData())
        args: dict[str, object] = {"account": self.session.account.model_dump()}
        if tool == "outlook.list":
            args["folder"] = self.folder.currentData()
        elif tool == "outlook.read":
            args["message_id"] = self.messages.currentData()
        else:
            try:
                message = Message(
                    to=list(x.strip() for x in self.to.text().split(",") if x.strip()),
                    cc=list(x.strip() for x in self.cc.text().split(",") if x.strip()),
                    bcc=list(x.strip() for x in self.bcc.text().split(",") if x.strip()),
                    subject=self.subject.text(),
                    body=self.body.toPlainText(),
                    attachments=list(self.attachments),
                )
                args["message"] = message.model_dump()
            except ValueError:
                self.status.setText(
                    "Проверьте точные email, текст до 16 000 символов и вложения. "
                    "Имена требуют уточнения."
                )
                return
        result = self.engine.prepare(
            tool, args, Mode.SIMULATION if self.simulation.isChecked() else Mode.EXECUTE
        )
        if isinstance(result, Outcome):
            self.status.setText("Действие отклонено: " + result.error.value)
            return
        self.action = result
        self.set_busy(True)
        if result.risk is Risk.CONFIRM:
            dialog = ApprovalDialog(result, self.authority, self)
            self.dialog = dialog

            def resolved(code: int) -> None:
                self.dialog = None
                if code == QDialog.DialogCode.Accepted and dialog.token is not None:
                    self.worker = ToolWorker(self.engine, result, dialog.token)
                    self.worker.finished.connect(self.finished)
                    workers.start(self.worker)
                else:
                    self.engine.cancel(result)
                    self.action = None
                    self.status.setText("Действие отменено.")
                    self.set_busy(False)
                dialog.deleteLater()

            dialog.finished.connect(resolved)
            dialog.open()
        else:
            self.worker = ToolWorker(self.engine, result)
            self.worker.finished.connect(self.finished)
            workers.start(self.worker)

    def stop(self) -> None:
        if not self.busy:
            return
        if self.action is not None:
            self.engine.cancel(self.action)
        if self.worker is not None:
            self.worker.cancel()
        if self.dialog is not None:
            self.dialog.reject()
        self.status.setText(
            "Остановка. Уже выданный запрос мог быть выполнен; "
            "не повторяйте отправку автоматически."
        )

    def finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        self.action = None
        if isinstance(worker, MailWorker):
            result = worker.result
            if worker.error:
                self.status.setText("Операция не завершена: " + worker.error)
            elif isinstance(result, Account):
                self.identity.setText("Outlook · " + result.address)
                self.status.setText("Аккаунт подключён. Выберите почтовое действие.")
            elif isinstance(result, Attachment):
                self.attachments.append(result)
                self.file_label.setText(
                    "\n".join(f"{a.name} · {a.size} B" for a in self.attachments)
                )
                self.status.setText("Вложение загружено в память.")
            else:
                self.status.setText(str(result))
        else:
            outcome = worker.outcome
            self.status.setText(
                outcome.status.value
                + " · "
                + outcome.error.value
                + (
                    " · Запрос мог быть выполнен. Не повторяйте отправку автоматически."
                    if outcome.may_have_effects and outcome.status is not Status.SUCCESS
                    else ""
                )
            )
            if outcome.result_json:
                result = MailResult.model_validate_json(outcome.result_json)
                self.output.setPlainText(result.data)
                if result.state == "accepted":
                    self.status.setText("Microsoft принял запрос. Доставка не подтверждена.")
                if result.state == "listed":
                    self.messages.clear()
                    for row in json.loads(result.data):
                        self.messages.addItem(str(row.get("subject", "")), row.get("id"))
                if result.state == "local_draft":
                    self.drafts.clear()
                    self.drafts.addItem("Выберите сохранённый локальный черновик", None)
                    for _, message in self.session.drafts():
                        self.drafts.addItem(message.subject or "Без темы", message)
        self.set_busy(False)
        worker.deleteLater()

    def shutdown(self) -> None:
        self._closing = True
        self.stop()
        if self.worker is not None:
            self.worker.wait()
            self.finished()
        self.reset_account()
