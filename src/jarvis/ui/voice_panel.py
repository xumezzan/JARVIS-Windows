"""Visible push-to-talk and transcript review; speech commands, cancels and confirms.

Confirming by voice is not a spoken yes: the panel speaks one control detail of the exact
action and accepts only that word back. See `voice/approval.py` for why.
"""

import json
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
from jarvis.ui import workers
from jarvis.ui.elevenlabs_dialog import ElevenLabsDialog
from jarvis.ui.voice_worker import VoiceWorker
from jarvis.voice.approval import CONFIRMATION_TEXT, Confirmation, ControlDetail, check
from jarvis.voice.contracts import (
    ERROR_TEXT,
    Recognizer,
    Recorder,
    Speaker,
    Transcript,
    spoken_result,
)
from jarvis.voice.elevenlabs import ElevenLabsSpeaker
from jarvis.voice.local import LocalRecorder, LocalSpeaker, VoskRecognizer
from jarvis.voice.wake import LocalWake, Wake

# Hands-free acts only on a phrase addressed to the assistant by name. The local model
# is small, so near-misses of the same name are accepted; anything else is ignored.
WAKE_WORDS = frozenset(
    {"джарвис", "джарвес", "джарвиз", "жарвис", "джавис", "ярвис", "джарис", "jarvis"}
)


# What the owner can see at a glance, in every state the microphone can be in. Standing
# listening must never be invisible, so this line is separate from ordinary messages and
# is the one thing that always says whether something is listening.
MICROPHONE = {
    "off": "○ Микрофон выключен.",
    "armed": "◉ Жду обращения «Джарвис». Слушается только имя, речь не распознаётся.",
    "recording": "● Идёт запись.",
    "transcribing": "◐ Микрофон выключен, идёт распознавание.",
    "speaking": "♪ Микрофон выключен, идёт озвучивание.",
}


def wake_command(text: str) -> str:
    """Return the command addressed to the assistant, or an empty string to ignore it."""
    words = [word.strip(".,!?:;…") for word in text.strip().casefold().split()]
    for index, word in enumerate(words[:2]):
        if word in WAKE_WORDS:
            return " ".join(words[index + 1 :]).strip()
    return ""


def installed_model() -> str:
    """The Russian model the repository installer unpacks next to the application, if present.

    Only a local directory that really holds a model is offered; nothing is downloaded here.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return ""
    root = Path(base) / "JarvisInstall"
    candidates: list[Path] = []
    # The installer keeps the previous slot for rollback, so the active one is asked first.
    try:
        active = json.loads((root / "active.json").read_text(encoding="utf-8")).get("slot")
        if active in ("a", "b"):
            candidates.extend(sorted((root / "slots" / str(active) / "models").glob("*")))
    except (OSError, ValueError, AttributeError):
        pass
    candidates.extend(sorted(root.glob("slots/*/models/*")))
    for candidate in candidates:
        if (candidate / "am" / "final.mdl").is_file():
            return str(candidate)
    return ""


class HoldButton(QPushButton):
    """Only a mouse/key press starts capture; click()/signal emission do not start it."""

    held = Signal()
    released_hold = Signal()
    interrupted = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setAutoDefault(False)
        self.holding = False
        self.wired = False

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
    # One Confirmation value per finished voice confirmation; the dialog decides what it means.
    confirmation = Signal(str)

    def __init__(
        self,
        *,
        recorder: Recorder | None = None,
        recognizer: Recognizer | None = None,
        speaker: Speaker | None = None,
        wake: Wake | None = None,
    ) -> None:
        super().__init__()
        self.recorder = recorder or LocalRecorder()
        self.recognizer = recognizer
        self.speaker = speaker or LocalSpeaker()
        self.wake = wake
        self.cloud_selection: tuple[str, str] | None = None
        self.settings_dialog: ElevenLabsDialog | None = None
        self.worker: VoiceWorker | None = None
        self.planning = False
        self.cancel_only = False
        self.closed = False
        self.was_cancelled = False
        self.voice_task = False
        self.hands_free = False
        self.listening = False
        self.armed = False
        self.woken = False
        self.confirming: ControlDetail | None = None
        self.confirm_stage = ""
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
        self.model_path.setText(os.environ.get("JARVIS_VOSK_MODEL", "") or installed_model())
        self.model_path.setPlaceholderText("Папка распакованной русской модели Vosk")
        self.browse = QPushButton("Выбрать модель…")
        self.browse.setAutoDefault(False)
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.model_path, 1)
        row.addWidget(self.browse)
        body.addLayout(row)
        self.cloud_settings = QPushButton("Настроить голос ElevenLabs Free…")
        self.cloud_settings.setAutoDefault(False)
        self.cloud_settings.clicked.connect(self._cloud_settings)
        body.addWidget(self.cloud_settings)
        row = QHBoxLayout()
        self.hold = self._button("Удерживайте для записи команды")
        row.addWidget(self.hold)
        self.stop_button = QPushButton("Остановить голос и задачу")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.stop_button)
        self.speech_enabled = QCheckBox("Озвучивать итог системным голосом")
        self.speech_enabled.toggled.connect(
            lambda enabled: None if enabled else self._clear_cloud()
        )
        row.addWidget(self.speech_enabled)
        body.addLayout(row)
        hint = QLabel(
            "Локальный голос: аудио не отправляется в облако. Удерживайте кнопку или пробел "
            "на ней, затем отпустите. До 30 секунд. Проверьте текст ниже перед запуском. "
            "Во время задачи эта кнопка слушает только «стоп» / «отмена»."
        )
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.indicator = QLabel(MICROPHONE["off"])
        self.indicator.setTextFormat(Qt.TextFormat.PlainText)
        self.indicator.setWordWrap(True)
        self.indicator.setAccessibleName("Индикатор микрофона")
        body.addWidget(self.indicator)
        self.status = QLabel("Микрофон выключен.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Состояние микрофона и речи")
        body.addWidget(self.status)
        self.message.connect(self.status.setText)

    def _cloud_settings(self) -> None:
        if self.worker is not None or self.planning or self.closed:
            return
        self.cloud_selection = None
        self.speech_enabled.setText("Озвучивать итог системным голосом")
        dialog = ElevenLabsDialog(self)
        self.settings_dialog = dialog
        dialog.selected.connect(self._select_cloud)
        self.busy_changed.emit(True)
        try:
            dialog.exec()
        finally:
            self.settings_dialog = None
            self.busy_changed.emit(False)
            dialog.deleteLater()

    def _select_cloud(self, voice_id: str, account: str) -> None:
        self.cloud_selection = (voice_id, account)
        self.speech_enabled.setChecked(True)
        self.speech_enabled.setText("Следующий итог: ElevenLabs; далее — системный голос")

    def _clear_cloud(self) -> None:
        self.cloud_selection = None
        self.speech_enabled.setText("Озвучивать итог системным голосом")

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите локальную русскую модель Vosk")
        if path:
            self.model_path.setText(path)

    def _button(self, text: str) -> HoldButton:
        button = HoldButton(text)
        self.attach(button)
        return button

    def attach(self, button: HoldButton) -> None:
        """Wire a hold control placed by another window; capture still needs a real press.

        A reused control is detached from any previous panel first, so one press can never
        start two captures.
        """
        if button.wired:
            for signal in (button.held, button.released_hold, button.interrupted):
                signal.disconnect()
        button.wired = True
        button.held.connect(lambda: self._record(button))
        button.released_hold.connect(lambda: self.release(button))
        button.interrupted.connect(lambda: self.cancel() if self.capture_button is button else None)

    def set_hands_free(self, enabled: bool) -> None:
        """Standing listening is an explicit, visible mode; it is never on by default."""
        self.hands_free = enabled
        if enabled:
            self.listen()
            return
        if self.listening or self.armed:
            self.cancel()
        else:
            self.message.emit("Свободные руки выключены. Микрофон выключен.")

    def listen(self) -> None:
        """Arm the listener. It waits for the name and recognises nothing until it hears it."""
        if (
            not self.hands_free
            or self.closed
            or self.planning
            or self.worker is not None
            or self.settings_dialog is not None
        ):
            return
        if self.recognizer is None and not self.model_path.text().strip():
            self.hands_free = False
            self.message.emit(ERROR_TEXT["model"])
            return
        self.was_cancelled = False
        self.capture_button = None
        self.cancel_only = False
        self.woken = False
        self.armed = True
        self.voice_task = False
        self._launch("", wake=True)

    def _record_phrase(self) -> None:
        """The name was heard, so the ordinary standing capture runs, exactly as before."""
        self.armed = False
        self.woken = True
        self.listening = True
        self.voice_task = True
        self._launch("", listen=True)

    def _resume(self) -> None:
        if self.hands_free and not self.closed and self.worker is None and not self.planning:
            QTimer.singleShot(200, self.listen)

    def announce(self, text: str) -> None:
        """Speak one short prompt of the application's own words, with the local voice.

        Never used for tool output, page text or message content, and it never consumes a
        one-use cloud voice consent, which belongs to the spoken task result.
        """
        if not self.speech_enabled.isChecked() or self.worker is not None or self.closed:
            return
        spoken = " ".join(text.split())[:300]
        if spoken:
            self._launch(spoken, cloud=False)

    def confirm(self, detail: ControlDetail) -> None:
        """Speak one control detail of the exact action, then capture one short answer.

        The microphone is never opened by the appearance of a dialog: this runs when the
        owner presses the confirmation control, or while standing capture is already their
        chosen, visibly indicated mode.
        """
        if self.closed or self.settings_dialog is not None or self.confirming is not None:
            return
        if self.recognizer is None and not self.model_path.text().strip():
            self.message.emit(ERROR_TEXT["model"])
            self.confirmation.emit(Confirmation.STOPPED)
            return
        self.confirming = detail
        self.confirm_stage = ""
        self.was_cancelled = False
        if self.worker is not None:
            # Free the microphone first; the confirmation starts when that capture ends.
            self.worker.cancel()
            return
        self._confirm_next(spoke=False)

    def _confirm_next(self, spoke: bool) -> None:
        detail = self.confirming
        if detail is None or self.closed:
            return
        if not spoke and self.speech_enabled.isChecked():
            # Local voice only, and never the one-use cloud consent, which belongs to results.
            self.confirm_stage = "speaking"
            self._launch(detail.request, cloud=False)
        else:
            self.confirm_stage = "listening"
            self._launch("", listen=True)
        if self.worker is None:
            # The adapter refused to start; the confirmation surface keeps its button.
            self.confirming = None
            self.confirm_stage = ""
            self.confirmation.emit(Confirmation.STOPPED)

    def _confirm_finished(self, worker: VoiceWorker, transcript: Transcript | None) -> None:
        """Route one finished worker of a confirmation. Only a repeated detail confirms."""
        detail = self.confirming
        if detail is None:
            return
        listening = self.confirm_stage == "listening"
        self.confirm_stage = ""
        if self.closed or self.was_cancelled or (worker.cancelled.is_set() and not listening):
            self.confirming = None
            self.confirmation.emit(Confirmation.STOPPED)
            return
        if not listening:
            self._confirm_next(spoke=True)
            return
        self.confirming = None
        if worker.cancelled.is_set():
            outcome = Confirmation.STOPPED
        elif worker.error or transcript is None:
            outcome = Confirmation.UNHEARD
        else:
            outcome = check(detail, transcript)
        self.message.emit(CONFIRMATION_TEXT[outcome])
        self.confirmation.emit(outcome)
        self._resume()

    def stop_confirming(self) -> None:
        """The confirmation surface closed; drop the capture without ending the session."""
        if self.confirming is None:
            return
        self.confirming = None
        self.confirm_stage = ""
        if self.worker is not None:
            self.worker.cancel()

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
        if (
            self.closed
            or self.settings_dialog is not None
            or self.worker is not None
            or not button.holding
        ):
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

    def _launch(
        self, speech: str, cloud: bool = True, listen: bool = False, wake: bool = False
    ) -> None:
        speaker = self.speaker
        if speech and cloud and self.cloud_selection is not None:
            voice_id, account = self.cloud_selection
            self._clear_cloud()
            speaker = ElevenLabsSpeaker(voice_id, account, speech)
        model = Path(self.model_path.text())
        try:
            worker = VoiceWorker(
                self.recorder,
                self.recognizer or VoskRecognizer(model),
                speaker,
                speech=speech,
                listen=listen,
                wake=(self.wake or LocalWake(model)) if wake else None,
            )
        except ValueError:
            self.message.emit("Облачный голос не подключён. Выберите локальный адаптер.")
            return
        self.worker = worker
        self.model_path.setEnabled(False)
        self.browse.setEnabled(False)
        self.speech_enabled.setEnabled(False)
        self.cloud_settings.setEnabled(False)
        self.busy_changed.emit(True)
        worker.phase.connect(self._phase)
        worker.finished.connect(self._finished)
        workers.start(worker)

    def _indicate(self, state: str) -> None:
        """The one line that always says what the microphone is doing."""
        self.indicator.setText(MICROPHONE[state])

    def _phase(self, phase: str) -> None:
        if self.worker is None or self.worker.cancelled.is_set() or self.closed:
            return
        if phase in ("waiting", "armed"):
            self._indicate("armed")
            if phase == "armed":
                self.message.emit(
                    "Свободные руки: жду обращения «Джарвис». Команда записывается "
                    "только после имени."
                )
            return
        self._indicate(phase)
        if self.confirming is not None:
            self.message.emit(
                {
                    "recording": "● Слушаю подтверждение. Произнесите «"
                    + self.confirming.word
                    + "».",
                    "transcribing": "Микрофон выключен. Проверка произнесённого слова…",
                    "speaking": "Микрофон выключен. Проговариваю действие и контрольное слово…",
                }[phase]
            )
            return
        listening = {
            "recording": "● Слушаю. Скажите «Джарвис» и команду; запись прервётся на паузе.",
            "transcribing": "Микрофон выключен. Распознавание локально…",
            "speaking": "Микрофон выключен. Озвучивание результата…",
        }
        held = {
            "recording": "● Идёт запись. Отпустите кнопку для распознавания; Stop отменяет.",
            "transcribing": "Микрофон выключен. Распознавание локально…",
            "speaking": "Микрофон выключен. Озвучивание результата…",
        }
        self.message.emit((listening if self.listening else held)[phase])

    def release(self, button: HoldButton) -> None:
        if self.worker is not None and self.capture_button is button:
            self.worker.released.set()
            self.message.emit("Завершение записи; микрофон выключается…")

    def cancel(self) -> None:
        self._clear_cloud()
        self.listening = False
        self.armed = False
        self.woken = False
        self._indicate("off")
        if self.settings_dialog is not None:
            self.settings_dialog.reject()
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
        if worker.wake is not None:
            self._woken(worker)
            worker.deleteLater()
            return
        self.capture_button = None
        # Whatever the outcome, the microphone is closed by now and the line says so.
        self._indicate("off")
        listening, self.listening = self.listening, False
        self.model_path.setEnabled(not self.planning)
        self.browse.setEnabled(not self.planning)
        self.speech_enabled.setEnabled(not self.planning)
        self.cloud_settings.setEnabled(not self.planning)
        self.busy_changed.emit(False)
        transcript = worker.transcript
        worker.transcript = None
        if self.confirming is not None:
            self._confirm_finished(worker, transcript)
            worker.deleteLater()
            return
        if worker.cancelled.is_set() or self.closed:
            self.message.emit("Микрофон и озвучивание выключены. Отменено.")
        elif worker.error:
            # A quiet room is the normal outcome of standing capture, not a failure.
            self.message.emit(
                "Тишина. Продолжаю слушать."
                if listening and worker.error == "silence"
                else ERROR_TEXT[worker.error]
            )
            if listening and worker.error in ("silence", "recognition"):
                self.voice_task = False
                self._resume()
                worker.deleteLater()
                return
            self.hands_free = False
        elif transcript is not None:
            if listening:
                self._addressed(transcript)
                worker.deleteLater()
                return
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
                    + "Распознано: "
                    + transcript.text[:120]
                )
        else:
            self.deadline.stop()
            self.voice_task = False
            self.message.emit("Микрофон выключен. Озвучивание завершено.")
            self._resume()
        worker.deleteLater()

    def _woken(self, worker: VoiceWorker) -> None:
        """One listening cycle ended. It either heard the name, or it heard nothing of note."""
        self.armed = False
        self._indicate("off")
        self.busy_changed.emit(False)
        if self.closed or self.was_cancelled or worker.cancelled.is_set():
            self.message.emit("Микрофон выключен. Слушание остановлено.")
            return
        if worker.error:
            # A listener that cannot run must not silently fall back to recording the room.
            self.hands_free = False
            self.message.emit(ERROR_TEXT[worker.error])
            return
        if worker.heard:
            self.message.emit("Услышал «Джарвис». Говорите команду.")
            self._record_phrase()
            return
        self._resume()

    def _addressed(self, transcript: Transcript) -> None:
        """Only a phrase that names the assistant becomes a command; the rest is dropped."""
        if transcript.is_cancel:
            self.cancel_requested.emit()
            self.voice_task = False
            self._resume()
            return
        command = wake_command(transcript.text)
        if not command and self.woken:
            # The name was heard by the listener, so repeating it is not asked of the owner.
            command = transcript.text.strip()
        self.woken = False
        if not command:
            self.message.emit("Пропущено: обращения «Джарвис» не было. Продолжаю слушать.")
            self.voice_task = False
            self._resume()
            return
        self.message.emit("Принято: " + command[:120])
        self.transcript_ready.emit(command)

    def set_planning(self, planning: bool) -> None:
        self.planning = planning
        self.hold.setText(
            "Удерживайте и скажите «стоп»" if planning else "Удерживайте для записи команды"
        )
        idle = not planning and self.worker is None
        self.model_path.setEnabled(idle)
        self.browse.setEnabled(idle)
        self.speech_enabled.setEnabled(idle)
        self.cloud_settings.setEnabled(idle)

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
        self.hands_free = False
        self.armed = False
        self.cancel()
        if self.worker is not None:
            self.worker.wait()
            self._finished()
