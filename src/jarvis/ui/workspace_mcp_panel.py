"""Explicit manifest discovery/review for servers configured in WorkspaceProfile."""

import asyncio
import json
from contextlib import suppress

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QComboBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from jarvis.mcp.bridge import tool_risk
from jarvis.mcp.client import discover
from jarvis.mcp.models import McpServerConfig, McpToolDefinition
from jarvis.ui import workers
from jarvis.workspace.models import WorkspaceProfile
from jarvis.workspace.store import WorkspaceFailure, WorkspaceStore


class WorkspaceManifestWorker(QThread):
    def __init__(self, config: McpServerConfig) -> None:
        super().__init__()
        self.config = config
        self.tools: tuple[McpToolDefinition, ...] = ()
        self.failed = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[tuple[McpToolDefinition, ...]] | None = None

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        self.loop = loop
        try:
            task = loop.create_task(discover(self.config))
            self.task = task
            if self.isInterruptionRequested():
                task.cancel()
            self.tools = loop.run_until_complete(task)
        except asyncio.CancelledError:
            self.failed = True
        except Exception:
            self.failed = True
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            self.task = None
            self.loop = None
            loop.close()

    def cancel(self) -> None:
        self.requestInterruption()
        if self.loop is not None and self.task is not None:
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.task.cancel)


class WorkspaceMcpPanel(QWidget):
    def __init__(self, store: WorkspaceStore) -> None:
        super().__init__()
        self.store = store
        self.snapshot = WorkspaceProfile()
        self.worker: WorkspaceManifestWorker | None = None
        self.review: McpServerConfig | None = None
        self.closed = False
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Workspace MCP: выберите настроенный сервер и получите manifest. "
            "Для stdio эта кнопка запускает указанную владельцем программу. "
            "Просмотр не вызывает tools/call. После сохранения перезапустите Jarvis."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.servers = QComboBox()
        layout.addWidget(self.servers)
        self.inspect = QPushButton("Получить workspace manifest")
        self.inspect.clicked.connect(self.discover)
        layout.addWidget(self.inspect)
        self.manifest = QPlainTextEdit()
        self.manifest.setReadOnly(True)
        self.manifest.setAccessibleName("Workspace MCP manifest и схемы аргументов")
        layout.addWidget(self.manifest)
        self.save = QPushButton("Я просмотрел manifest — сохранить разрешённый набор")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.accept_review)
        layout.addWidget(self.save)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.reload()

    def reload(self) -> None:
        self.servers.clear()
        try:
            self.snapshot = self.store.load()
        except WorkspaceFailure:
            self.status.setText("Профиль рабочего пространства недоступен.")
            self.inspect.setEnabled(False)
            return
        for server in self.snapshot.mcp_servers:
            if server.enabled:
                self.servers.addItem(server.name + " (" + server.server_id + ")", server.server_id)
        self.inspect.setEnabled(self.servers.count() > 0)

    def discover(self) -> None:
        if self.worker is not None or self.closed:
            return
        selected = self.servers.currentData()
        config = next(
            (item for item in self.snapshot.mcp_servers if item.server_id == selected), None
        )
        if config is None:
            return
        self.review = None
        self.save.setEnabled(False)
        self.inspect.setEnabled(False)
        self.servers.setEnabled(False)
        self.manifest.clear()
        worker = WorkspaceManifestWorker(config)
        self.worker = worker
        worker.finished.connect(self.loaded)
        workers.start(worker)

    def loaded(self) -> None:
        worker, self.worker = self.worker, None
        self.inspect.setEnabled(True)
        self.servers.setEnabled(True)
        if self.closed or worker is None:
            return
        if worker.failed:
            self.status.setText("Manifest недоступен или содержит неподдерживаемую схему.")
            return
        self.review = worker.config.with_review(worker.tools)
        self.manifest.setPlainText(
            json.dumps(
                {
                    "server_id": worker.config.server_id,
                    "transport": worker.config.transport,
                    "command": worker.config.command,
                    "url": worker.config.url,
                    "tools": [
                        {
                            **tool.model_dump(mode="json"),
                            "allowed": tool.name in worker.config.allowed_tools,
                            "risk": tool_risk(worker.config, tool.name).value,
                        }
                        for tool in worker.tools
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self.save.setEnabled(True)
        self.status.setText(
            "Это данные сервера. Сохраните только после просмотра имён, описаний и схем."
        )

    def accept_review(self) -> None:
        if self.review is None or self.closed:
            return
        try:
            current = self.store.load()
            if current != self.snapshot:
                self.status.setText("Профиль изменён другим окном. Загрузите manifest заново.")
                self.review = None
                self.save.setEnabled(False)
                self.reload()
                return
            updated = WorkspaceProfile.model_validate(
                current.model_dump()
                | {
                    "mcp_servers": tuple(
                        self.review if item.server_id == self.review.server_id else item
                        for item in current.mcp_servers
                    ),
                }
            )
            self.store.save(updated)
            self.snapshot = updated
        except (WorkspaceFailure, ValueError):
            self.status.setText("Не удалось сохранить просмотренный manifest.")
            return
        self.save.setEnabled(False)
        self.status.setText("Сохранено. Перезапустите Jarvis для регистрации нового набора.")

    def shutdown(self) -> None:
        self.closed = True
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
