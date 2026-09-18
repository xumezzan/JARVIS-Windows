"""The screen a new machine opens with: what is connected, and how to connect the rest.

An assistant that can reach nothing looks exactly like an assistant that is broken. On a
fresh laptop Jarvis has no model key, no mailbox and no tokens, and until now it said none
of that: the window came up empty and the setup lived in a tab of another window and, for
most services, in a terminal command nobody had been told about.

This window is the answer to that, and it is deliberately a list rather than a wizard. The
owner connects what they need in whatever order they like, sees at a glance what is still
missing, and can close it at any point - a half-connected Jarvis works, just with fewer
services, and it says which ones.

It opens by itself once, the first time the application runs on a machine. After that it is
a button, because a setup screen that greets you every morning is a setup screen you learn
to dismiss without reading.
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.ui.connections_panel import ConnectionsPanel, plain

MARKER = "setup.json"


def first_run(data_dir: Path) -> bool:
    """Whether this machine has never been shown the setup screen.

    A directory that cannot be read counts as a first run: showing the screen once too
    often costs a click, and never showing it costs the owner the whole application.
    """
    try:
        return not (Path(data_dir) / MARKER).exists()
    except OSError:
        return True


def mark_shown(data_dir: Path) -> None:
    """Remember that it was shown. Failing to remember only means showing it again."""
    try:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).write_text(json.dumps({"shown": True}), encoding="utf-8")
    except OSError:
        return


class SetupWindow(QDialog):
    def __init__(
        self,
        session: MailSession,
        audit: AuditLog,
        data_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jarvis — настройка")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(940, 760)
        self.data_dir = Path(data_dir)
        root = QVBoxLayout(self)
        root.addWidget(
            plain(
                "Подключите то, что нужно. Ничего не обязательно: без модели Jarvis "
                "выполняет только учебные команды, без Microsoft не видит почту и "
                "календарь, без токенов — свои сервисы. Всё это можно добавить позже."
            )
        )
        self.panel = ConnectionsPanel(session=session, audit=audit)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        root.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_button = QPushButton("Закрыть")
        self.close_button.setObjectName("primary")
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        root.addLayout(row)
        mark_shown(self.data_dir)

    def shutdown(self) -> None:
        self.panel.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
