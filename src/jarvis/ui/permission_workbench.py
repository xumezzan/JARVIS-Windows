"""Manual local tools surface; no LLM inference or real external integration."""

import json
from pathlib import Path

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
    QVBoxLayout,
    QWidget,
)

from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.local import local_registry
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.tool_worker import ToolWorker


class PermissionWorkbench(QDialog):
    state_changed = Signal(str)
    task_finished = Signal(str)

    def __init__(self, data_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка инструментов и разрешений")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(800, 740)
        self.audit = AuditLog(data_dir / "audit.sqlite3")
        self.registry, self.outbox = local_registry()
        self.approvals = ApprovalStore()
        self.authority = self.approvals.take_authority(self.audit.approved)
        self.engine = PermissionEngine(self.registry, self.approvals, self.audit)
        self.action: Action | None = None
        self.approval_dialog: ApprovalDialog | None = None
        self.worker: ToolWorker | None = None
        self._closing = False
        self._closed = False
        self._build_ui()
        self.refresh_audit()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel("Только локальные тестовые инструменты. Внешние сервисы не подключены.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.tool_choice = QComboBox()
        self.tool_choice.addItem("SAFE — локальная проверка", "local.check")
        self.tool_choice.addItem("CONFIRM — тестовое сообщение", "local.append_message")
        self.tool_choice.addItem("CRITICAL — отключено", "local.critical_test")
        self.tool_choice.addItem("BLOCKED — запрещено", "local.blocked_test")
        form.addRow("Инструмент", self.tool_choice)
        self.simulation = QCheckBox("Simulation Mode — adapters не вызываются")
        self.simulation.setChecked(True)
        form.addRow("Режим", self.simulation)
        self.account = QLineEdit("test-account")
        self.recipient = QLineEdit("local-recipient")
        self.subject = QLineEdit("Тест разрешений")
        for title, widget in (
            ("Аккаунт", self.account),
            ("Получатель", self.recipient),
            ("Тема", self.subject),
        ):
            widget.setMaxLength(200 if widget is not self.subject else 500)
            form.addRow(title, widget)
        self.body = QPlainTextEdit("Jarvis permission test")
        self.body.setMaximumHeight(100)
        form.addRow("Содержание", self.body)
        form.addRow("Вложения", QLabel("Нет. Тестовый ящик хранит данные только в памяти."))
        layout.addLayout(form)
        controls = QHBoxLayout()
        self.run_button = QPushButton("Подготовить и запустить")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.prepare)
        self.stop_button = QPushButton("Остановить / отменить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.run_button)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.status_label = QLabel("Готово к проверке. По умолчанию включена симуляция.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.outbox_label = QLabel("Сообщений в локальной памяти: 0")
        layout.addWidget(self.outbox_label)
        layout.addWidget(QLabel("Audit — последние 200 событий, без текста команд и токенов"))
        self.audit_view = QPlainTextEdit()
        self.audit_view.setReadOnly(True)
        layout.addWidget(self.audit_view, 1)

    def _busy(self, value: bool) -> None:
        for widget in (
            self.tool_choice,
            self.simulation,
            self.account,
            self.recipient,
            self.subject,
            self.body,
            self.run_button,
        ):
            widget.setEnabled(not value)
        self.stop_button.setEnabled(value)

    def refresh_audit(self) -> None:
        if self._closed:
            return
        try:
            rows = [json.loads(row) for row in self.audit.recent()]
            self.audit_view.setPlainText(
                "\n".join(
                    f"{row['timestamp']}  {row['tool']}  {row['event']}  "
                    f"{row['status'] or row['permission_decision']}  {row['error']}"
                    for row in rows
                )
            )
        except Exception:
            self.status_label.setText("Журнал недоступен. Выполнение без аудита запрещено.")

    @Slot()
    def prepare(self) -> None:
        if self.action is not None or self.worker is not None or self._closing:
            return
        tool = str(self.tool_choice.currentData())
        arguments: dict[str, object] = {}
        if tool == "local.append_message":
            arguments = {
                "account": self.account.text(),
                "recipient": self.recipient.text(),
                "subject": self.subject.text(),
                "body": self.body.toPlainText(),
                "attachments": [],
            }
        prepared = self.engine.prepare(
            tool,
            arguments,
            Mode.SIMULATION if self.simulation.isChecked() else Mode.EXECUTE,
        )
        if isinstance(prepared, Outcome):
            self._show_outcome(prepared)
            return
        self.action = prepared
        self._busy(True)
        if prepared.risk is Risk.CONFIRM:
            self.status_label.setText("awaiting approval — проверьте точные данные в диалоге.")
            self.state_changed.emit("awaiting approval")
            self.approval_dialog = ApprovalDialog(prepared, self.authority, self)
            self.approval_dialog.finished.connect(self._approval_finished)
            self.approval_dialog.open()
        else:
            self._execute()
        self.refresh_audit()

    @Slot(int)
    def _approval_finished(self, result: int) -> None:
        dialog = self.approval_dialog
        if dialog is None:
            return
        self.approval_dialog = None
        if result == QDialog.DialogCode.Accepted and dialog.token is not None and not self._closing:
            self._execute(dialog)
        else:
            if self.action is not None:
                self.engine.cancel(self.action)
            self.action = None
            self._busy(False)
            self.status_label.setText("CANCELLED — подтверждение отменено, выполнение не началось.")
            self.state_changed.emit("cancelled")
            self.task_finished.emit(Status.CANCELLED.value)
            self.refresh_audit()
        dialog.token = None
        dialog.deleteLater()

    def _execute(self, dialog: ApprovalDialog | None = None) -> None:
        assert self.action is not None
        self.worker = ToolWorker(
            self.engine,
            self.action,
            dialog.token if dialog else None,
            self,
        )
        self.worker.finished.connect(self._worker_finished)
        self.status_label.setText("executing — проверка разрешений и выполнение…")
        self.state_changed.emit("executing")
        self.worker.start()

    @Slot()
    def _worker_finished(self) -> None:
        if self.worker is None:
            return
        self.worker.wait()
        result = self.worker.outcome
        self.worker.deleteLater()
        self.worker = None
        self.action = None
        self._busy(False)
        self._show_outcome(result)
        if self._closing:
            self.close()

    def _show_outcome(self, result: Outcome) -> None:
        messages = {
            Status.SUCCESS: "Проверенный локальный результат. Внешней отправки не было.",
            Status.SIMULATED: "Только симуляция. Ни один adapter не вызван.",
            Status.DENIED: "Действие отклонено проверкой разрешений или условий.",
            Status.INVALID: "Инструмент или аргументы недопустимы.",
            Status.CANCELLED: "Задача отменена.",
            Status.TIMEOUT: "Истекло время выполнения.",
            Status.ERROR: "Результат не подтверждён; проверьте журнал.",
        }
        text = f"{result.status.value} — {messages[result.status]}"
        if result.may_have_effects and result.status is not Status.SUCCESS:
            text += " Adapter уже был вызван; отсутствие изменений не гарантируется."
        self.status_label.setText(text)
        self.outbox_label.setText(f"Сообщений в локальной памяти: {self.outbox.count}")
        self.state_changed.emit(result.status.value.lower())
        self.refresh_audit()
        self.task_finished.emit(result.status.value)

    @Slot()
    def stop(self) -> None:
        if self.action is not None:
            self.engine.cancel(self.action)
        if self.approval_dialog is not None:
            self.approval_dialog.reject()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closing = True
        self.stop()
        self.engine.cancel_all()
        if self.worker is not None:
            self.worker.wait()
            self._worker_finished()
        if not self._closed:
            self.audit.close()
            self._closed = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.stop()
        if self.worker is not None:
            event.ignore()
            return
        self.shutdown()
        super().reject()
        event.accept()

    def reject(self) -> None:
        # Escape uses the same cooperative close path as the title-bar close button.
        self.close()
