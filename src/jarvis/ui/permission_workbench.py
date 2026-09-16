"""Manual local tools surface; no LLM inference or real external integration."""

import json
import sys
from html import escape
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jarvis.browser.host import BrowserHost
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.base import ToolError
from jarvis.tools.browser import BrowserResult, register_browser
from jarvis.tools.local import local_registry
from jarvis.tools.windows import WindowsBackend, WindowsResult, WindowTarget, register_windows
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.browser_controls import BrowserControls
from jarvis.ui.tool_worker import ToolWorker


class PermissionWorkbench(QDialog):
    state_changed = Signal(str)
    task_finished = Signal(str)

    def __init__(
        self,
        data_dir: Path,
        parent: QWidget | None = None,
        *,
        windows_backend: WindowsBackend | None = None,
        browser_host: BrowserHost | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка инструментов и разрешений")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(800, 740)
        self.audit = AuditLog(data_dir / "audit.sqlite3")
        self.registry, self.outbox = local_registry()
        register_windows(self.registry, windows_backend or ProcessBackend())
        self.browser_host = browser_host or BrowserHost()
        register_browser(self.registry, self.browser_host, self.browser_host.policy)
        self._result_tool = ""
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
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        intro = QLabel(
            "Windows: открытие приложений, выбор окна и подтверждаемый ввод в пустой Блокнот. "
            "Браузер: чтение разрешённых страниц и поиск с подтверждением переходов."
            + (
                " На этой ОС доступна только симуляция Windows-инструментов."
                if sys.platform != "win32"
                else ""
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.form = form
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.tool_choice = QComboBox()
        self.tool_choice.addItem("SAFE — локальная проверка", "local.check")
        self.tool_choice.addItem("CONFIRM — тестовое сообщение", "local.append_message")
        self.tool_choice.addItem("CRITICAL — отключено", "local.critical_test")
        self.tool_choice.addItem("BLOCKED — запрещено", "local.blocked_test")
        for title, tool in (
            ("Windows — список окон", "windows.get_open_windows"),
            ("Windows — открыть приложение", "windows.open_app"),
            ("Windows — фокус на окно", "windows.focus_app"),
            ("Windows — текст в пустой Блокнот (CONFIRM)", "windows.type_text"),
        ):
            self.tool_choice.addItem(title, tool)
        for title, operation in (
            ("открыть страницу", "open"),
            ("перейти по адресу", "navigate"),
            ("поиск DuckDuckGo", "search"),
            ("нажать элемент", "click"),
            ("ввести текст", "type"),
            ("прочитать страницу", "read"),
            ("список вкладок", "get_tabs"),
            ("закрыть вкладку", "close"),
        ):
            self.tool_choice.addItem("Браузер — " + title, "browser." + operation)
        form.addRow("Инструмент", self.tool_choice)
        self.simulation = QCheckBox("Simulation Mode — adapters не вызываются")
        self.simulation.setChecked(True)
        form.addRow("Режим", self.simulation)
        self.app_choice = QComboBox()
        # Editable: any installed application can be named, not only these shortcuts.
        self.app_choice.setEditable(True)
        self.app_choice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for title, app in (("Блокнот", "notepad"), ("Chrome", "chrome"), ("VS Code", "code")):
            self.app_choice.addItem(title, app)
        form.addRow("Приложение Windows", self.app_choice)
        self.target_choice = QComboBox()
        self.target_choice.setMinimumContentsLength(20)
        self.target_choice.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.target_choice.addItem("Сначала получите список окон", None)
        self.target_choice.currentIndexChanged.connect(self._target_changed)
        form.addRow("Точное окно", self.target_choice)
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
        self.attachments_label = QLabel("Нет. Тестовый ящик хранит данные только в памяти.")
        self.attachments_label.setWordWrap(True)
        form.addRow("Вложения", self.attachments_label)
        layout.addLayout(form)
        self.browser_controls = BrowserControls()
        layout.addWidget(self.browser_controls)
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
        self.audit_view.setMinimumHeight(150)
        self.tool_choice.currentIndexChanged.connect(self._tool_changed)
        self._tool_changed()

    @Slot()
    def _tool_changed(self) -> None:
        tool = str(self.tool_choice.currentData())
        windows = tool.startswith("windows.")
        self.app_choice.setEnabled(
            windows and tool in ("windows.open_app", "windows.get_open_windows")
        )
        self.target_choice.setEnabled(tool in ("windows.focus_app", "windows.type_text"))
        for widget in (self.account, self.recipient, self.subject):
            widget.setEnabled(tool == "local.append_message")
        self.body.setEnabled(tool in ("local.append_message", "windows.type_text"))
        self.form.setRowVisible(
            self.app_choice, tool in ("windows.open_app", "windows.get_open_windows")
        )
        self.form.setRowVisible(
            self.target_choice, tool in ("windows.focus_app", "windows.type_text")
        )
        for field in (self.account, self.recipient, self.subject, self.attachments_label):
            self.form.setRowVisible(field, tool == "local.append_message")
        self.form.setRowVisible(self.body, tool in ("local.append_message", "windows.type_text"))
        self.outbox_label.setVisible(not windows)
        self.browser_controls.set_tool(tool)
        if tool.startswith("browser."):
            self.outbox_label.hide()

    @Slot()
    def _target_changed(self) -> None:
        target = self.target_choice.currentData()
        self.target_choice.setToolTip(
            "<pre>"
            + escape(
                f"{target.title}\n{target.executable}\nPID {target.pid} · HWND {target.handle}"
            )
            + "</pre>"
            if isinstance(target, WindowTarget)
            else "Выберите наблюдённое окно."
        )

    def _busy(self, value: bool) -> None:
        for widget in (
            self.tool_choice,
            self.simulation,
            self.account,
            self.recipient,
            self.subject,
            self.body,
            self.run_button,
            self.app_choice,
            self.target_choice,
        ):
            widget.setEnabled(not value)
        self.stop_button.setEnabled(value)
        self.browser_controls.setEnabled(not value)
        if not value:
            self._tool_changed()

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
        self._result_tool = tool
        arguments: dict[str, object] = {}
        if tool.startswith("browser."):
            try:
                arguments = self.browser_controls.arguments()
            except ValueError as error:
                self.status_label.setText(str(error))
                return
        if tool == "local.append_message":
            arguments = {
                "account": self.account.text(),
                "recipient": self.recipient.text(),
                "subject": self.subject.text(),
                "body": self.body.toPlainText(),
                "attachments": [],
            }
        elif tool in ("windows.open_app", "windows.get_open_windows"):
            typed = self.app_choice.currentText().strip()
            preset = self.app_choice.currentData()
            arguments = {
                "app": str(preset)
                if preset and typed == self.app_choice.itemText(self.app_choice.currentIndex())
                else typed
            }
        elif tool in ("windows.focus_app", "windows.type_text"):
            target = self.target_choice.currentData()
            if not isinstance(target, WindowTarget):
                self.status_label.setText("Сначала получите список окон и выберите точную цель.")
                return
            arguments = {"target": target.model_dump()}
            if tool == "windows.type_text":
                arguments["text"] = self.body.toPlainText()
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
        if self._result_tool.startswith("windows."):
            if result.status is Status.SUCCESS and result.result_json is not None:
                data = WindowsResult.model_validate_json(result.result_json)
                if self._result_tool in ("windows.get_open_windows", "windows.open_app"):
                    targets = data.windows if data.target is None else [data.target]
                    self.target_choice.clear()
                    for target in targets:
                        self.target_choice.addItem(
                            f"{target.app}: {target.title} · PID {target.pid}"
                            + (" · пустой редактор" if target.empty else ""),
                            target,
                        )
                    text = f"SUCCESS — обнаружено окон: {len(targets)}. Выберите точную цель."
                elif self._result_tool == "windows.type_text":
                    text = "SUCCESS — текст прочитан обратно и совпадает с подтверждённым."
                    self.target_choice.clear()  # Empty-editor snapshot is now stale.
                else:
                    text = "SUCCESS — выбранное окно получило фокус."
            elif result.status not in (Status.SUCCESS, Status.SIMULATED):
                reasons = {
                    "unsupported_platform": "Реальный запуск требует Windows 11.",
                    "application_missing": "Приложение не найдено в поддерживаемых каталогах.",
                    "target_changed": "Окно, вкладка или содержимое изменились. Обновите список.",
                    "control_unsupported": "Редактор этой версии приложения не поддерживается.",
                    "native_timeout": "Windows-адаптер превысил время ожидания.",
                    "native_failure": "Windows-адаптер недоступен или завершился с ошибкой.",
                }
                reason = reasons.get(result.error.value, messages[result.status])
                text = f"{result.status.value} — {reason}"
        if result.may_have_effects and result.status is not Status.SUCCESS:
            text += " Adapter уже был вызван; отсутствие изменений не гарантируется."
        if self._result_tool.startswith("browser."):
            if result.status is Status.SUCCESS and result.result_json is not None:
                text = self.browser_controls.accept_result(
                    self._result_tool, BrowserResult.model_validate_json(result.result_json)
                )
            elif result.status not in (Status.SUCCESS, Status.SIMULATED):
                reasons = {
                    "network_denied": "Адрес, редирект или запрос не разрешён политикой.",
                    "page_changed": "Страница изменилась. Обновите вкладки и прочитайте её заново.",
                    "browser_unavailable": "Установите Chromium для Playwright.",
                    "browser_timeout": "Истекло время ожидания браузера.",
                    "browser_cleanup": "Не удалось полностью завершить работу браузера.",
                    "browser_failure": "Действие браузера не подтверждено наблюдением.",
                }
                reason = reasons.get(result.error.value, messages[result.status])
                text = f"{result.status.value} — {reason}"
                if result.may_have_effects:
                    text += " Уже отправленный запрос не отзывается."
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
            try:
                self.browser_host.shutdown()
            except ToolError:
                self.status_label.setText("ERROR — браузер не подтвердил полное завершение работы.")
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
