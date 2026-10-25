"""Where the owner meets a server's self-description and decides what it may do.

This panel exists because of one rule: an MCP tool does not assign itself a level. What
arrives from a server is a list of names and sentences - data - and it becomes a usable
tool only after the owner has read it here and set a level on each line. The default is
confirmation; lowering something to routine is a decision, not a default.

Reading a server is network work and stays off the Qt loop. Writing the review is a small
local file, written whole. Nothing here executes a tool: the reviewed set reaches the
registry when the planner is composed, which is why a fresh review asks for a restart
rather than quietly changing the tools of a session already running.
"""

import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.connectors.mcp.connector import McpConnector
from jarvis.connectors.mcp.manifest import McpTool, Server, load_servers, save_servers
from jarvis.connectors.mcp.protocol import McpFailure
from jarvis.permissions.policies import Risk
from jarvis.security.credentials import setup_command
from jarvis.ui import workers

NEW = "Новый сервер"
CHOICES = (
    ("Подтверждение", Risk.CONFIRM),
    ("Рутина — без вопроса", Risk.ROUTINE),
    ("Запрещено", Risk.BLOCKED),
)
FAILURES = {
    "mcp_credentials": "Ключ сервера недоступен. Настройте его и повторите.",
    "mcp_denied": "Сервер отказал в доступе этому ключу.",
    "mcp_missing": "По этому адресу нет такого эндпоинта.",
    "mcp_rate_limited": "Сервер ограничил частоту обращений. Повторите позже.",
    "mcp_network": "Сервер недоступен по сети.",
    "mcp_response": "Ответ не похож на манифест MCP: сессии и потоки не поддерживаются.",
    "mcp_request": "Запрос не разрешён собственной таблицей маршрутов.",
    "mcp_refused": "Сервер отклонил запрос списка инструментов.",
    "mcp_changed": "Набор инструментов изменился.",
}


def plain(text: str) -> QLabel:
    """A server's own words are shown as text, never as markup."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ManifestWorker(QThread):
    """One `tools/list` call. It reads; it never calls a tool."""

    def __init__(self, connector: McpConnector) -> None:
        super().__init__()
        self.connector = connector
        self.tools: tuple[McpTool, ...] = ()
        self.error = ""

    def run(self) -> None:
        try:
            self.tools = asyncio.run(self.connector.manifest())
        except McpFailure as failure:
            self.error = failure.code
        except Exception:
            self.error = "mcp_network"


class McpPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mcpPanel")
        self.path = path
        self.servers: tuple[Server, ...] = ()
        self.draft: Server | None = None
        self.worker: ManifestWorker | None = None
        self.levels: dict[str, QComboBox] = {}
        self.closed = False
        body = QVBoxLayout(self)
        body.addWidget(
            plain(
                "Сервер MCP сам рассказывает, что он умеет, поэтому его слово не становится "
                "разрешением. Прочитайте набор, проставьте уровень каждому инструменту и "
                "подтвердите набор целиком. Непроверенный инструмент не выполняется. Если "
                "сервер изменит набор, проверка снимается и он вернётся сюда."
            )
        )
        self.chooser = QComboBox()
        self.chooser.setAccessibleName("Сервер MCP")
        self.chooser.currentIndexChanged.connect(self._chosen)
        body.addWidget(self.chooser)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(36)
        self.name.setPlaceholderText("mcp_github")
        self.origin = QLineEdit()
        self.origin.setMaxLength(200)
        self.origin.setPlaceholderText("https://mcp.example.test")
        self.endpoint = QLineEdit()
        self.endpoint.setMaxLength(120)
        self.endpoint.setPlaceholderText("/mcp")
        form.addRow("Имя", self.name)
        form.addRow("Адрес", self.origin)
        form.addRow("Путь", self.endpoint)
        body.addLayout(form)
        body.addWidget(
            plain(
                "Только https без порта, ключ хранится в системном хранилище: "
                + setup_command("mcp_<имя>")
            )
        )
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Сохранить сервер")
        self.save_button.setObjectName("primary")
        self.remove_button = QPushButton("Удалить сервер")
        self.read_button = QPushButton("Прочитать инструменты")
        for button, callback in (
            (self.save_button, self._save),
            (self.remove_button, self._remove),
            (self.read_button, self._read),
        ):
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        body.addLayout(buttons)
        body.addWidget(plain("Инструменты сервера"))
        self.tools_layout = QVBoxLayout()
        body.addLayout(self.tools_layout)
        decisions = QHBoxLayout()
        self.approve_button = QPushButton("Подтвердить набор")
        self.revoke_button = QPushButton("Снять проверку")
        for button, callback in (
            (self.approve_button, self._approve),
            (self.revoke_button, self._revoke),
        ):
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            decisions.addWidget(button)
        body.addLayout(decisions)
        self.status = plain("")
        self.status.setAccessibleName("Состояние серверов MCP")
        body.addWidget(self.status)
        body.addStretch(1)
        self._reload()

    # Configuration

    def _reload(self) -> None:
        try:
            self.servers = load_servers(self.path)
            self.status.setText(self._state())
        except ValueError as error:
            # A broken file leaves no servers at all rather than half of them.
            self.servers = ()
            self.status.setText(str(error))
        self.chooser.blockSignals(True)
        self.chooser.clear()
        self.chooser.addItem(NEW, "")
        for server in self.servers:
            self.chooser.addItem(server.service, server.service)
        self.chooser.blockSignals(False)
        self._chosen()

    def _state(self) -> str:
        if not self.servers:
            return "Серверы не настроены."
        pending = [server.service for server in self.servers if not server.current]
        if pending:
            return "Ждут проверки: " + ", ".join(pending)
        return "Все наборы подтверждены."

    def _chosen(self) -> None:
        service = str(self.chooser.currentData() or "")
        found = next((server for server in self.servers if server.service == service), None)
        self.draft = found
        self.name.setText(found.service if found else "")
        self.origin.setText(found.origin if found else "")
        self.endpoint.setText(found.path if found else "")
        self._render(found.tools if found else ())

    def _form(self) -> Server | None:
        try:
            return Server(
                service=self.name.text().strip(),
                origin=self.origin.text().strip(),
                path=self.endpoint.text().strip() or "/",
                reviewed="",
                tools=self.draft.tools if self.draft else (),
            )
        except ValueError:
            self.status.setText(
                "Имя вида mcp_<буквы>, адрес https без порта и пути, путь начинается с «/»."
            )
            return None

    def _store(self, server: Server) -> None:
        others = tuple(item for item in self.servers if item.service != server.service)
        try:
            save_servers(self.path, (*others, server))
        except (ValueError, OSError):
            self.status.setText("Не удалось записать файл серверов MCP.")
            return
        self._reload()
        index = self.chooser.findData(server.service)
        if index >= 0:
            self.chooser.setCurrentIndex(index)

    def _save(self) -> None:
        server = self._form()
        if server is None:
            return
        # Address changes are part of the pin, so saving a new one drops the old review.
        previous = next((s for s in self.servers if s.service == server.service), None)
        if previous is not None and (previous.origin, previous.path) == (
            server.origin,
            server.path,
        ):
            server = Server(
                server.service, server.origin, server.path, previous.reviewed, previous.tools
            )
        self._store(server)
        self.status.setText("Сервер сохранён. Прочитайте его набор инструментов.")

    def _remove(self) -> None:
        service = str(self.chooser.currentData() or "")
        if not service:
            return
        try:
            save_servers(self.path, tuple(s for s in self.servers if s.service != service))
        except (ValueError, OSError):
            self.status.setText("Не удалось записать файл серверов MCP.")
            return
        self._reload()
        self.status.setText("Сервер удалён. Его инструменты исчезнут после перезапуска.")

    # The server's own account of itself

    def _read(self) -> None:
        server = self._form()
        if server is None or self.worker is not None or self.closed:
            return
        self.busy_changed.emit(True)
        self._controls(False)
        self.status.setText("Читаю набор инструментов…")
        worker = ManifestWorker(McpConnector(server))
        self.worker = worker
        worker.finished.connect(self._read_done)
        workers.start(worker)

    def _read_done(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        self._controls(True)
        self.busy_changed.emit(False)
        if worker.error:
            self.status.setText(FAILURES.get(worker.error, FAILURES["mcp_network"]))
        elif not worker.tools:
            self.status.setText("Сервер не назвал ни одного пригодного инструмента.")
        else:
            base = self.draft or self._form()
            if base is None:
                return
            # A fresh reading is unreviewed by construction; levels chosen before are kept.
            self.draft = base.with_tools(worker.tools)
            stored = next((s for s in self.servers if s.service == base.service), None)
            if stored is not None and stored.current and stored.reviewed != self.draft.digest:
                # The owner has just seen that the server changed: the file must not go on
                # claiming a review, or the next session would register the old set again.
                self._store(self.draft)
                self.status.setText(
                    "Набор изменился, проверка снята. Прочитайте его заново и подтвердите."
                )
                return
            self._render(self.draft.tools)
            self.status.setText(
                f"Прочитано инструментов: {len(worker.tools)}. Проставьте уровни и "
                "подтвердите набор."
            )
        worker.deleteLater()

    def _controls(self, enabled: bool) -> None:
        for widget in (
            self.chooser,
            self.name,
            self.origin,
            self.endpoint,
            self.save_button,
            self.remove_button,
            self.read_button,
            self.approve_button,
            self.revoke_button,
        ):
            widget.setEnabled(enabled)

    def _render(self, tools: tuple[McpTool, ...]) -> None:
        while self.tools_layout.count():
            item = self.tools_layout.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.deleteLater()
        self.levels = {}
        for tool in tools:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(plain(f"{tool.remote} · {tool.description}"), 1)
            level = QComboBox()
            level.setAccessibleName("Уровень инструмента " + tool.remote)
            for title, risk in CHOICES:
                level.addItem(title, risk.name)
            chosen = level.findData(tool.risk.name)
            level.setCurrentIndex(chosen if chosen >= 0 else 0)
            self.levels[tool.remote] = level
            line.addWidget(level)
            self.tools_layout.addWidget(row)

    # The owner's decision

    def _approve(self) -> None:
        draft = self.draft
        if draft is None or not draft.tools or self.worker is not None:
            self.status.setText("Сначала прочитайте набор инструментов сервера.")
            return
        levels = {
            remote: Risk[str(box.currentData())]
            for remote, box in self.levels.items()
            if str(box.currentData()) in Risk.__members__
        }
        try:
            reviewed = draft.with_levels(levels)
        except ValueError:
            self.status.setText("Уровень может быть только рутиной, подтверждением или запретом.")
            return
        self._store(reviewed)
        self.status.setText(
            "Набор подтверждён. Инструменты появятся в планировщике после перезапуска."
        )

    def _revoke(self) -> None:
        service = str(self.chooser.currentData() or "")
        found = next((server for server in self.servers if server.service == service), None)
        if found is None:
            return
        self._store(Server(found.service, found.origin, found.path, "", found.tools))
        self.status.setText("Проверка снята. Инструменты сервера больше не выполняются.")

    def shutdown(self) -> None:
        self.closed = True
        if self.worker is not None:
            self.worker.wait()
            self._read_done()
