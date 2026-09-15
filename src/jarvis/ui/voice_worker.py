"""Cancellable Qt bridge. No approval authority or tool adapters cross this boundary."""

import asyncio
from contextlib import suppress
from threading import Event

from PySide6.QtCore import QThread, Signal

from jarvis.voice.contracts import Recognizer, Recorder, Speaker, Transcript, VoiceError


class VoiceWorker(QThread):
    phase = Signal(str)

    def __init__(
        self,
        recorder: Recorder,
        recognizer: Recognizer,
        speaker: Speaker,
        *,
        speech: str = "",
    ) -> None:
        super().__init__()
        if not recognizer.local_only or not speaker.local_only:
            raise ValueError("Cloud audio requires a separate consent implementation.")
        self.recorder, self.recognizer, self.speaker = recorder, recognizer, speaker
        self.speech = speech
        self.released = Event()
        self.cancelled = Event()
        self.transcript: Transcript | None = None
        self.error = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[None] | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        self.released.set()
        if self.loop is not None and not self.loop.is_closed():
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._cancel_task)

    def _cancel_task(self) -> None:
        if self.task is not None and not self.task.cancelling():
            self.task.cancel()

    async def _work(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self.cancelled.is_set():
            return
        async with asyncio.timeout(80):
            if self.speech:
                self.phase.emit("speaking")
                await self.speaker.speak(self.speech)
            else:
                clip = await self.recorder.record(
                    self.released, lambda: self.phase.emit("recording")
                )
                if self.cancelled.is_set():
                    return
                self.phase.emit("transcribing")
                self.transcript = await self.recognizer.transcribe(clip)

    def run(self) -> None:
        try:
            asyncio.run(self._work())
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            self.error = "timeout"
        except VoiceError as exc:
            self.error = exc.code
        except Exception:
            self.error = "voice_failed"
        finally:
            self.speech = ""
            if self.cancelled.is_set():
                self.transcript = None
