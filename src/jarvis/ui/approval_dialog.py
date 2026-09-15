"""Trusted confirmation boundary. Opening/accepting a dialog alone cannot grant approval."""

import json

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalToken
from jarvis.permissions.policies import Mode


class ApprovalDialog(QDialog):
    approved = Signal()

    def __init__(
        self,
        action: Action,
        authority: ApprovalAuthority,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.action = action
        self._authority = authority
        self.token: ApprovalToken | None = None
        self.setWindowTitle("Подтверждение точного действия")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        title = QLabel(f"{action.risk.value}  •  {action.mode.value}  •  {action.tool}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        notice = (
            "Симуляция: реальное действие выполняться не будет."
            if action.mode is Mode.SIMULATION
            else "Проверьте адрес, цель и содержание. Переход передаст запрос указанному сайту."
            if action.tool.startswith("browser.")
            else "Действие выполнится в выбранном приложении Windows."
            if action.tool.startswith("windows.")
            else "Тестовый ящик локален. Сообщения никуда не отправляются."
        )
        self.notice = QLabel(notice)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Точные данные подтверждаемого действия")
        self.preview.setPlainText(
            json.dumps(
                {
                    "tool": action.tool,
                    "risk": action.risk.value,
                    "mode": action.mode.value,
                    "arguments": json.loads(action.payload),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        layout.addWidget(self.preview)
        self.error_label = QLabel("Подтверждение действует до 60 секунд с подготовки действия.")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.reject)
        self.approve_button = QPushButton("Подтвердить показанное действие")
        self.approve_button.setObjectName("primary")
        self.approve_button.setAutoDefault(False)
        self.approve_button.clicked.connect(self._approve)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.approve_button)
        layout.addLayout(buttons)

    @Slot()
    def _approve(self) -> None:
        self.approve_button.setEnabled(False)
        try:
            self.token = self._authority.approve(self.action)
        except Exception:
            self.error_label.setText(
                "Подтверждение недоступно или истекло. Подготовьте действие заново."
            )
            return
        self.approved.emit()
        self.accept()
