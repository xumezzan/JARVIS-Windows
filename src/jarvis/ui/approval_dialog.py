"""Trusted confirmation boundary. Opening/accepting a dialog alone cannot grant approval."""

import json
from contextlib import suppress

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalToken, Channel
from jarvis.permissions.policies import Mode
from jarvis.ui.voice_panel import VoicePanel
from jarvis.voice.approval import CONFIRMATION_TEXT, Confirmation, control_detail


class ApprovalDialog(QDialog):
    approved = Signal()

    def __init__(
        self,
        action: Action,
        authority: ApprovalAuthority,
        parent: QWidget | None = None,
        *,
        voice: VoicePanel | None = None,
    ) -> None:
        super().__init__(parent)
        self.action = action
        self._authority = authority
        self.token: ApprovalToken | None = None
        # Voice is offered only for an action that carries a detail the owner can repeat;
        # for anything else the button stays the only channel.
        self.detail = control_detail(action) if voice is not None else None
        self.voice = voice if self.detail is not None else None
        self.armed = False
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
            else "Письмо будет отправлено через Outlook. Проверьте аккаунт, Кому/Копия/Скрытая "
            "копия, полный текст и идентичности вложений. Доставка проверяется отдельно."
            if action.tool == "outlook.send"
            else "Полный черновик и вложения будут переданы в Outlook. Это внешняя запись."
            if action.tool == "outlook.save_draft"
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
        self.listen_button: QPushButton | None = None
        if self.voice is not None and self.detail is not None:
            # The word is shown at the same moment it is spoken: the owner confirms what is
            # on screen, and a general «да» is never one of the answers.
            detail = QLabel(
                f"Голосом: произнесите {self.detail.label} — «{self.detail.word}». "
                "Общее «да» не подтверждает. «Отмена» отказывает."
            )
            detail.setTextFormat(Qt.TextFormat.PlainText)
            detail.setWordWrap(True)
            layout.addWidget(detail)
            self.listen_button = QPushButton("Слушать подтверждение голосом")
            self.listen_button.setAutoDefault(False)
            self.listen_button.clicked.connect(self._listen)
            layout.addWidget(self.listen_button)
            self.voice.confirmation.connect(self._heard)
            self.finished.connect(self._release_voice)
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

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Standing capture is already the owner's visible choice, so the confirmation can arm
        # itself. Otherwise the microphone opens only on their press of the button below.
        if self.voice is not None and self.detail is not None and self.voice.hands_free:
            self._listen()

    @Slot()
    def _listen(self) -> None:
        if self.voice is None or self.detail is None or self.armed or self.token is not None:
            return
        self.armed = True
        if self.listen_button is not None:
            self.listen_button.setEnabled(False)
        self.voice.confirm(self.detail)

    @Slot(str)
    def _heard(self, outcome: str) -> None:
        # The panel has one voice for the whole session, so an answer belongs to whichever
        # confirmation asked for it. A dialog that armed nothing confirms nothing.
        if self.voice is None or self.detail is None or self.token is not None or not self.armed:
            return
        self.armed = False
        if self.listen_button is not None:
            self.listen_button.setEnabled(True)
        verdict = Confirmation(outcome)
        if verdict is Confirmation.CONFIRMED:
            self._grant(Channel.VOICE)
        elif verdict is Confirmation.REFUSED:
            self.reject()
        else:
            self.error_label.setText(CONFIRMATION_TEXT[verdict])

    @Slot()
    def _release_voice(self) -> None:
        """The decision is made; no later transcript may reach a dialog that is closing."""
        voice, self.voice = self.voice, None
        if voice is not None:
            with suppress(RuntimeError):
                voice.confirmation.disconnect(self._heard)
            voice.stop_confirming()

    @Slot()
    def _approve(self) -> None:
        self._grant(Channel.UI)

    def _grant(self, channel: Channel) -> None:
        self.approve_button.setEnabled(False)
        try:
            self.token = self._authority.approve(self.action, channel)
        except Exception:
            self.error_label.setText(
                "Подтверждение недоступно или истекло. Подготовьте действие заново."
            )
            return
        self.approved.emit()
        self.accept()
