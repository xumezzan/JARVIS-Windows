"""Explicit Free-account setup and one-shot text disclosure. No microphone access."""

import asyncio
from contextlib import suppress
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui import workers
from jarvis.voice.contracts import ERROR_TEXT, VoiceError
from jarvis.voice.elevenlabs import PREVIEW
from jarvis.voice.local import exchange


class SettingsWorker(QThread):
    def __init__(self, request: dict[str, object]) -> None:
        super().__init__()
        self.request = request
        self.result: dict[str, Any] = {}
        self.error = ""
        self.cancelled = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[None] | None = None

    def cancel(self) -> None:
        self.cancelled = True
        if self.loop is not None and not self.loop.is_closed():
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._cancel)

    def _cancel(self) -> None:
        if self.task is not None and not self.task.cancelling():
            self.task.cancel()

    async def _work(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if not self.cancelled:
            self.result = await exchange(
                self.request, seconds=55, helper="jarvis.platforms.elevenlabs"
            )

    def run(self) -> None:
        try:
            asyncio.run(self._work())
        except asyncio.CancelledError:
            pass
        except VoiceError as exc:
            self.error = exc.code
        except Exception:
            self.error = "voice_failed"
        finally:
            self.request.clear()
            if self.cancelled:
                self.result.clear()


class ElevenLabsDialog(QDialog):
    selected = Signal(str, str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Голос ElevenLabs Free")
        self.setMinimumWidth(540)
        self.worker: SettingsWorker | None = None
        self.account = ""
        self.operation = ""
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.resize(720, 650)
        for text in (
            "Бесплатный аккаунт ElevenLabs нужен отдельно. Доступны стандартные голоса "
            "аккаунта, в пределах его лимита. Платные тарифы и оплата сверх лимита блокируются.",
            "API-ключ сохраняется в системном хранилище. «Загрузить голоса» обращается "
            "к ElevenLabs для проверки аккаунта и получения списка; аудио не отправляется.",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            body.addWidget(label)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(4096)
        self.key.setPlaceholderText("API-ключ ElevenLabs — вводите только здесь")
        self.key.setAccessibleName("API-ключ ElevenLabs")
        body.addWidget(self.key)
        self.save = QPushButton("Сохранить ключ")
        self.remove = QPushButton("Удалить сохранённый ключ")
        self.refresh = QPushButton("Загрузить голоса и проверить Free-лимит")
        self.voices = QComboBox()
        self.voices.setAccessibleName("Голос ElevenLabs")
        self.consent = QCheckBox("Разрешаю одну следующую озвучку выбранным голосом")
        disclosure = QLabel(
            "В ElevenLabs уйдёт только текст пробы или краткий итог следующей задачи. "
            "Провайдер может хранить текст и созданное аудио. Озвучка расходует лимит, "
            "в том числе в симуляции. Микрофон и текст команды остаются локальными "
            "в голосовом модуле. После одной озвучки снова используется системный голос."
        )
        disclosure.setWordWrap(True)
        disclosure.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        preview_text = QLabel(f"Проба: {PREVIEW}")
        preview_text.setWordWrap(True)
        self.preview = QPushButton("Прослушать пробу (расходует лимит)")
        self.use = QPushButton("Озвучить следующий итог этим голосом")
        self.close_button = QPushButton("Закрыть / остановить")
        self.status = QLabel("Системный голос доступен без аккаунта и лимитов.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        for widget in (
            self.save,
            self.remove,
            self.refresh,
            self.voices,
            disclosure,
            self.consent,
            preview_text,
            self.preview,
            self.use,
            self.status,
        ):
            body.addWidget(widget)
        outer.addWidget(self.close_button)
        for button in (
            self.save,
            self.remove,
            self.refresh,
            self.preview,
            self.use,
            self.close_button,
        ):
            button.setAutoDefault(False)
        self.save.clicked.connect(self._save)
        self.remove.clicked.connect(lambda: self._start({"operation": "delete"}))
        self.refresh.clicked.connect(lambda: self._start({"operation": "voices"}))
        self.preview.clicked.connect(self._preview)
        self.use.clicked.connect(self._use)
        self.close_button.clicked.connect(self.reject)
        self.key.textChanged.connect(self._invalidate)
        self.voices.currentIndexChanged.connect(lambda: self.consent.setChecked(False))

    def _invalidate(self) -> None:
        self.account = ""
        self.voices.clear()
        self.consent.setChecked(False)

    def _save(self) -> None:
        key = self.key.text()
        self.key.clear()
        self._start({"operation": "set", "key": key})

    def _start(self, request: dict[str, object]) -> None:
        if self.worker is not None:
            return
        self.operation = str(request["operation"])
        self.consent.setChecked(False)
        if self.operation != "speak":
            self._invalidate()
        self.worker = SettingsWorker(request)
        for widget in (
            self.key,
            self.save,
            self.remove,
            self.refresh,
            self.voices,
            self.consent,
            self.preview,
            self.use,
        ):
            widget.setEnabled(False)
        self.status.setText("Микрофон выключен. Выполняется запрос…")
        self.worker.finished.connect(self._finished)
        workers.start(self.worker)

    def _finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        for widget in (
            self.key,
            self.save,
            self.remove,
            self.refresh,
            self.voices,
            self.consent,
            self.preview,
            self.use,
        ):
            widget.setEnabled(True)
        if worker.cancelled:
            self.status.setText("Остановлено. Уже отправленная озвучка может расходовать лимит.")
        elif worker.error:
            self.status.setText(ERROR_TEXT[worker.error])
        elif self.operation == "voices":
            data = worker.result
            try:
                account, remaining, voices = data["account"], data["remaining"], data["voices"]
                if (
                    not isinstance(account, str)
                    or type(remaining) is not int
                    or not isinstance(voices, list)
                ):
                    raise ValueError
                for voice in voices:
                    self.voices.addItem(voice["name"], voice["id"])
                self.account = account
                self.status.setText(f"Free: осталось {remaining} кредитов на момент проверки.")
            except (KeyError, TypeError, ValueError):
                self._invalidate()
                self.status.setText(ERROR_TEXT["eleven_network"])
        else:
            self.status.setText(
                {
                    "set": "Ключ сохранён. Теперь загрузите голоса.",
                    "delete": "Ключ удалён из системного хранилища.",
                    "speak": "Проба завершена. Для следующей озвучки нужно новое согласие.",
                }[self.operation]
            )
        worker.result.clear()
        worker.deleteLater()

    def _authorized(self) -> bool:
        if not self.account or not self.voices.currentData():
            self.status.setText(ERROR_TEXT["eleven_voice"])
            return False
        if not self.consent.isChecked():
            self.status.setText(ERROR_TEXT["eleven_consent"])
            return False
        return True

    def _preview(self) -> None:
        if self._authorized():
            self._start(
                {
                    "operation": "speak",
                    "account": self.account,
                    "voice_id": self.voices.currentData(),
                    "text": PREVIEW,
                }
            )

    def _use(self) -> None:
        if self._authorized():
            self.consent.setChecked(False)
            self.selected.emit(self.voices.currentData(), self.account)
            self.accept()

    def done(self, result: int) -> None:
        self.consent.setChecked(False)
        self.key.clear()
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait()
            self._finished()
        super().done(result)
