"""Async voice controller. The private UI gesture starts capture; speech never approves."""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event

from jarvis.permissions.policies import Mode
from jarvis.voice.contracts import ERROR_TEXT, Recognizer, Recorder, Speaker, VoiceError
from jarvis.voice.local import LocalRecorder, LocalSpeaker, VoskRecognizer


class DesktopVoice:
    def __init__(
        self,
        publish: Callable[[], None],
        transcript: Callable[[str], None],
        start: Callable[[str, Mode], bool],
        cancel: Callable[[], None],
        *,
        recorder: Recorder | None = None,
        recognizer: Recognizer | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        self.publish, self.transcript, self.start, self.cancel_plan = (
            publish,
            transcript,
            start,
            cancel,
        )
        model = os.environ.get("JARVIS_VOSK_MODEL", "")
        self.recognizer = recognizer or (VoskRecognizer(Path(model)) if model else None)
        self.recorder = recorder or LocalRecorder(
            host=self.recognizer.host if isinstance(self.recognizer, VoskRecognizer) else None,
            auto_stop_on_silence=os.environ.get("JARVIS_VOICE_AUTO_STOP_ON_SILENCE", "0") == "1",
        )
        self.speaker = speaker or LocalSpeaker()
        if self.recognizer is not None and not self.recognizer.local_only:
            raise ValueError("Cloud recognition requires separate consent.")
        self.task: asyncio.Task[None] | None = None
        self.released = Event()
        self.phase = "idle"
        self.status = "Микрофон выключен."
        self.auto_start = False
        self.speech_enabled = False
        self.closed = False
        self.wake_enabled = os.environ.get("JARVIS_VOICE_WAKE_WORD", "0") == "1"
        self.wake_allowed = False  # serve() publishes the opt-in indicator before arming.
        self.wake_suspended = False
        self.wake_review = False
        self.wake_task: asyncio.Task[None] | None = None
        self.wake_released = Event()
        self.wake_generation = 0
        self.wake_mode = Mode.SIMULATION
        self.background = False
        if isinstance(self.recognizer, VoskRecognizer):
            self.recognizer.warmup()

    def _track(self, task: asyncio.Task[None]) -> None:
        self.task = task

        def finished(done: asyncio.Task[None]) -> None:
            # Cancellation can happen before the coroutine's first instruction/finally.
            if self.task is done:
                self.task = None
                self.phase = "idle"
                if done.cancelled():
                    self.status = "Голосовая операция отменена. Микрофон выключен."
                self.publish()

        task.add_done_callback(finished)

    def phase_changed(self, phase: str, status: str) -> None:
        self.phase, self.status = phase, status
        self.publish()

    def reconcile_wake(self, available: bool) -> None:
        if (
            not self.wake_enabled
            or not self.wake_allowed
            or self.closed
            or self.wake_suspended
            or self.wake_review
            or not available
            or self.task is not None
        ):
            self.pause_wake()
            return
        if self.wake_task is not None or self.phase not in {
            "idle",
            "wake_listening",
            "wake_starting",
        }:
            return
        if not isinstance(self.recognizer, VoskRecognizer):
            self.wake_suspended = True
            self.status = "Для ожидания «Джарвис» выберите локальную модель Vosk."
            return
        self.wake_generation += 1
        self.wake_released = Event()
        self.wake_task = asyncio.create_task(self._wake(self.wake_generation))
        self.phase_changed("wake_starting", "Запуск ожидания «Джарвис»… Микрофон будет включён.")

    def pause_wake(self) -> None:
        if self.wake_task is not None and not self.wake_released.is_set():
            self.wake_generation += 1
            self.wake_released.set()

    async def stop_wake(self) -> None:
        pending = self.wake_task
        self.pause_wake()
        if pending is not None and pending is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(pending), timeout=2)
            except TimeoutError:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def _wake(self, generation: int) -> None:
        assert isinstance(self.recognizer, VoskRecognizer)
        host = self.recognizer.host
        loop = asyncio.get_running_loop()

        def listening() -> None:
            if generation == self.wake_generation:
                self.phase_changed("wake_listening", "● Микрофон включён. Жду «Джарвис».")

        def ready() -> None:
            loop.call_soon_threadsafe(listening)

        failed = False
        try:
            while generation == self.wake_generation and not self.wake_released.is_set():
                result = await host.call(
                    {"operation": "wake"}, released=self.wake_released, ready=ready
                )
                if result.get("wake") is True and generation == self.wake_generation:
                    self.wake_generation += 1  # Ignore a queued listening receipt after activation.
                    self.wake_review = True
                    self.wake_task = None
                    self.record(self.wake_mode, False, wake_triggered=True)
                    return
        except asyncio.CancelledError:
            pass
        except VoiceError as exc:
            failed = True
            self.wake_suspended = True
            self.status = ERROR_TEXT[exc.code]
        except Exception:
            failed = True
            self.wake_suspended = True
            self.status = ERROR_TEXT["voice_failed"]
        finally:
            if self.wake_task is asyncio.current_task():
                self.wake_task = None
                if self.task is None:
                    self.phase = "idle"
                    if not failed:
                        self.status = "Ожидание «Джарвис» приостановлено. Микрофон выключен."
                self.publish()

    def record(self, mode: Mode, cancel_only: bool, *, wake_triggered: bool = False) -> None:
        if self.closed or self.task is not None:
            raise ValueError("busy")
        if self.recognizer is None:
            raise VoiceError("model")
        self.released = Event()
        self.pause_wake()
        self.background = wake_triggered
        self.phase_changed(
            "starting",
            "«Джарвис» услышан. Скажите команду."
            if wake_triggered
            else "Запуск микрофона… Удерживайте кнопку.",
        )
        self._track(asyncio.create_task(self._record(mode, cancel_only, wake_triggered)))

    async def _record(self, mode: Mode, cancel_only: bool, wake_triggered: bool = False) -> None:
        try:
            async with asyncio.timeout(80):
                await self.stop_wake()
                recorder = self.recorder
                if wake_triggered:
                    assert isinstance(self.recognizer, VoskRecognizer)
                    recorder = LocalRecorder(host=self.recognizer.host, auto_stop_on_silence=True)
                if isinstance(self.recorder, LocalRecorder) and isinstance(
                    self.recognizer, VoskRecognizer
                ):
                    self.recorder.host = self.recognizer.host
                clip = await recorder.record(
                    self.released,
                    lambda: self.phase_changed(
                        "recording",
                        "● Микрофон включён. Скажите команду; пауза завершит запись."
                        if wake_triggered
                        else "● Идёт запись. Отпустите кнопку.",
                    ),
                )
                self.phase_changed("transcribing", "Микрофон выключен. Распознаю…")
                assert self.recognizer is not None
                result = await self.recognizer.transcribe(clip)
            # Clear before starting the planner: an audio worker must not block its own command.
            self.task = None
            if result.is_cancel:
                self.cancel_plan()
            elif cancel_only:
                self.status = "Команда отмены не распознана. Используйте Stop."
            else:
                self.wake_review = self.wake_enabled
                self.transcript(result.text)
                if self.auto_start and not result.uncertain and self.start(result.text, mode):
                    self.speak("Принял, выполняю")
                else:
                    self.status = (
                        "Распознавание неуверенное. Проверьте текст и запустите команду."
                        if result.uncertain
                        else "Проверьте текст команды перед запуском."
                    )
        except asyncio.CancelledError:
            self.status = "Голосовой ввод отменён. Микрофон выключен."
        except VoiceError as exc:
            self.status = ERROR_TEXT[exc.code]
        except TimeoutError:
            self.status = ERROR_TEXT["timeout"]
        except Exception:
            self.status = ERROR_TEXT["voice_failed"]
        finally:
            self.background = False
            # An acknowledgement may already own a new speech task.
            if self.task is asyncio.current_task():
                self.task = None
            if self.task is None:
                self.phase = "idle"
            self.publish()

    def speak(self, text: str, speaker: Speaker | None = None) -> None:
        if self.closed or self.task is not None:
            raise ValueError("busy")
        self.pause_wake()
        self._track(asyncio.create_task(self._speak(text, speaker or self.speaker)))
        self.phase_changed("speaking", "Микрофон выключен. Говорю…")

    async def _speak(self, text: str, speaker: Speaker) -> None:
        try:
            async with asyncio.timeout(60):
                await self.stop_wake()
                await speaker.speak(text)
            self.status = "Озвучивание завершено. Микрофон выключен."
        except asyncio.CancelledError:
            self.status = "Озвучивание остановлено."
        except VoiceError as exc:
            self.status = ERROR_TEXT[exc.code]
        except Exception:
            self.status = ERROR_TEXT["speech"]
        finally:
            self.task = None
            self.phase = "idle"
            self.publish()

    def release(self) -> None:
        self.released.set()

    def cancel(self, *, suspend_wake: bool = True) -> None:
        if suspend_wake:
            self.wake_suspended = True
        self.pause_wake()
        self.released.set()
        if self.task is not None:
            self.task.cancel()

    async def drain(self) -> None:
        self.cancel(suspend_wake=False)
        await self.stop_wake()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)

    async def shutdown(self) -> None:
        self.closed = True
        await self.drain()
        if isinstance(self.recognizer, VoskRecognizer):
            await asyncio.to_thread(self.recognizer.shutdown)
