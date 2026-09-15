"""Visible push-to-talk and transcript review; speech can cancel but never approve."""

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QHideEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.planner.contracts import PlanResult
from jarvis.ui.voice_worker import VoiceWorker
from jarvis.voice.contracts import ERROR_TEXT, Recognizer, Recorder, Speaker, spoken_result
from jarvis.voice.local import LocalRecorder, LocalSpeaker, VoskRecognizer


class HoldButton(QPushButton):
    """Only a mouse/key press starts capture; click()/signal emission do not start it."""

    held = Signal()
    released_hold = Signal()
    interrupted = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setAutoDefault(False)
        self.holding = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.holding = True
            self.held.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.holding:
            self.holding = False
            self.released_hold.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat() and not self.holding:
            self.holding = True
            self.held.emit()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat() and self.holding:
            self.holding = False
            self.released_hold.emit()
        super().keyReleaseEvent(event)

    def _interrupt(self) -> None:
        if self.holding:
            self.holding = False
            self.interrupted.emit()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._interrupt()
        super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._interrupt()
        super().hideEvent(event)


class VoicePanel(QWidget):
    transcript_ready = Signal(str)
    cancel_requested = Signal()
    busy_changed = Signal(bool)
    message = Signal(str)

    def __init__(
        self,
        *,
        recorder: Recorder | None = None,
        recognizer: Recognizer | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        super().__init__()
        self.recorder = recorder or LocalRecorder()
        self.recognizer = recognizer
        self.speaker = speaker or LocalSpeaker()
        self.worker: VoiceWorker | None = None
        self.planning = False
        self.cancel_only = False
        self.closed = False
        self.was_cancelled = False
        self.voice_task = False
        self.capture_button: HoldButton | None = None
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(300000)
        self.deadline.timeout.connect(self._expired)
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.model_path = QLineEdit()
        self.model_path.setMaxLength(2000)
        self.model_path.setText(os.environ.get("JARVIS_VOSK_MODEL", ""))
        self.model_path.setPlaceholderText("Папка распакованной русской модели Vosk")
        self.browse = QPushButton("Выбрать модель…")
        self.browse.setAutoDefault(False)
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.model_path, 1)
        row.addWidget(self.browse)
        body.addLayout(row)
        row = QHBoxLayout()
        self.hold = self._button("Удерживайте для записи команды")
        row.addWidget(self.hold)
        self.stop_button = QPushButton("Остановить голос и задачу")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.stop_button)
        self.speech_enabled = QCheckBox("Озвучивать итог системным голосом")
        row.addWidget(self.speech_enabled)
        body.addLayout(row)
        hint = QLabel(
            "Локальный голос: аудио не отправляется в облако. Удерживайте кнопку или пробел "
            "на ней, затем отпустите. До 30 секунд. Проверьте текст ниже перед запуском. "
            "Во время задачи эта кнопка слушает только «стоп» / «отмена»."
        )
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.status = QLabel("Микрофон выключен.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Состояние микрофона и речи")
        body.addWidget(self.status)
        self.message.connect(self.status.setText)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите локальную русскую модель Vosk")
        if path:
            self.model_path.setText(path)

    def _button(self, text: str) -> HoldButton:
        button = HoldButton(text)
        button.held.connect(lambda: self._record(button))
        button.released_hold.connect(lambda: self.release(button))
        button.interrupted.connect(lambda: self.cancel() if self.capture_button is button else None)
        return button

    def stop_controls(self, dialog: QWidget) -> QWidget:
        """Keep a visible microphone indicator and Stop accessible in modal prompts."""
        dialog.installEventFilter(self)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.addWidget(self._button("Удерживайте и скажите «стоп» для отмены задачи"))
        status = QLabel(self.status.text())
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        self.message.connect(status.setText)
        layout.addWidget(status)
        stop = QPushButton("Остановить задачу")
        stop.setAutoDefault(False)
        stop.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(stop)
        return box

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.cancel_requested.emit()
            return True
        return super().eventFilter(watched, event)

    def _record(self, button: HoldButton) -> None:
        if self.closed or self.worker is not None or not button.holding:
            return
        if self.recognizer is None and not self.model_path.text().strip():
            self.message.emit(ERROR_TEXT["model"])
            return
        self.was_cancelled = False
        self.capture_button = button
        self.cancel_only = self.planning
        if not self.planning:
            self.voice_task = True
            self.deadline.start()
        self.message.emit("Микрофон запускается… Дождитесь надписи «Идёт запись».")
        self._launch("")

    def _launch(self, speech: str) -> None:
        try:
            worker = VoiceWorker(
                self.recorder,
                self.recognizer or VoskRecognizer(Path(self.model_path.text())),
                self.speaker,
                speech=speech,
            )
        except ValueError:
            self.message.emit("Облачный голос не подключён. Выберите локальный адаптер.")
            return
        self.worker = worker
        self.model_path.setEnabled(False)
        self.browse.setEnabled(False)
        self.speech_enabled.setEnabled(False)
        self.busy_changed.emit(True)
        worker.phase.connect(self._phase)
        worker.finished.connect(self._finished)
        worker.start()

    def _phase(self, phase: str) -> None:
        if self.worker is None or self.worker.cancelled.is_set() or self.closed:
            return
        self.message.emit(
            {
                "recording": "● Идёт запись. Отпустите кнопку для распознавания; Stop отменяет.",
                "transcribing": "Микрофон выключен. Распознавание локально…",
                "speaking": "Микрофон выключен. Озвучивание результата…",
            }[phase]
        )

    def release(self, button: HoldButton) -> None:
        if self.worker is not None and self.capture_button is button:
            self.worker.released.set()
            self.message.emit("Завершение записи; микрофон выключается…")

    def cancel(self) -> None:
        if self.voice_task and not self.planning:
            self.transcript_ready.emit("")
        self.was_cancelled = True
        self.voice_task = False
        self.deadline.stop()
        if self.worker is not None:
            self.worker.cancel()
            self.message.emit("Остановка микрофона и озвучивания…")
        else:
            self.message.emit("Микрофон выключен. Голосовой ввод отменён.")

    def _expired(self) -> None:
        self.cancel_requested.emit()
        self.transcript_ready.emit("")
        self.message.emit("Истекли 5 минут голосовой задачи. Введите новую команду.")

    def _finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        self.capture_button = None
        self.model_path.setEnabled(not self.planning)
        self.browse.setEnabled(not self.planning)
        self.speech_enabled.setEnabled(not self.planning)
        self.busy_changed.emit(False)
        transcript = worker.transcript
        worker.transcript = None
        if worker.cancelled.is_set() or self.closed:
            self.message.emit("Микрофон и озвучивание выключены. Отменено.")
        elif worker.error:
            self.message.emit(ERROR_TEXT[worker.error])
        elif transcript is not None:
            if transcript.is_cancel:
                self.cancel_requested.emit()
            elif self.cancel_only:
                self.message.emit(
                    "Микрофон выключен. Команда отмены не распознана; используйте Stop."
                )
            else:
                self.transcript_ready.emit(transcript.text)
                self.message.emit(
                    "Микрофон выключен. "
                    + ("Распознавание неуверенное. " if transcript.uncertain else "")
                    + "Проверьте и исправьте текст ниже, затем нажмите «Запустить планировщик»."
                )
        else:
            self.deadline.stop()
            self.voice_task = False
            self.message.emit("Микрофон выключен. Озвучивание завершено.")
        worker.deleteLater()

    def set_planning(self, planning: bool) -> None:
        self.planning = planning
        self.hold.setText(
            "Удерживайте и скажите «стоп»" if planning else "Удерживайте для записи команды"
        )
        idle = not planning and self.worker is None
        self.model_path.setEnabled(idle)
        self.browse.setEnabled(idle)
        self.speech_enabled.setEnabled(idle)

    def finish_plan(self, result: PlanResult) -> None:
        self.set_planning(False)
        if self.worker is not None:
            self.cancel()
        elif self.speech_enabled.isChecked() and not self.was_cancelled and not self.closed:
            self._launch(spoken_result(result))
        else:
            self.deadline.stop()
            self.voice_task = False

    def shutdown(self) -> None:
        self.closed = True
        self.cancel()
        if self.worker is not None:
            self.worker.wait()
            self._finished()
