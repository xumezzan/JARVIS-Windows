"""Cancellable Qt bridge. No approval authority or tool adapters cross this boundary."""

import asyncio
from contextlib import suppress
from threading import Event

from PySide6.QtCore import QThread, Signal

from jarvis.voice.contracts import Recognizer, Recorder, Speaker, Transcript, VoiceError
from jarvis.voice.elevenlabs import ElevenLabsSpeaker
from jarvis.voice.wake import WAKE_TIMEOUT, Wake


class VoiceWorker(QThread):
    phase = Signal(str)

    def __init__(
        self,
        recorder: Recorder,
        recognizer: Recognizer,
        speaker: Speaker,
        *,
        speech: str = "",
        listen: bool = False,
        wake: Wake | None = None,
    ) -> None:
        super().__init__()
        authorized_cloud = (
            isinstance(speaker, ElevenLabsSpeaker)
            and bool(speech)
            and speaker.authorized_text == speech
            and not speaker.used
        )
        if not recognizer.local_only or (not speaker.local_only and not authorized_cloud):
            raise ValueError("Cloud audio requires a separate consent implementation.")
        if wake is not None and not wake.local_only:
            # Standing listening is the last place a cloud ear would be acceptable.
            raise ValueError("A standing listener is local only.")
        self.recorder, self.recognizer, self.speaker = recorder, recognizer, speaker
        self.wake = wake
        self.heard = False
        self.speech = speech
        self.listen = listen
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
        async with asyncio.timeout(WAKE_TIMEOUT if self.wake is not None else 80):
            if self.wake is not None:
                self.phase.emit("waiting")
                # The listener answers a yes-or-no question; nothing is recognised here.
                self.heard = await self.wake.listen(self.released, lambda: self.phase.emit("armed"))
            elif self.speech:
                self.phase.emit("speaking")
                await self.speaker.speak(self.speech)
            else:
                clip = await self.recorder.record(
                    self.released, lambda: self.phase.emit("recording"), self.listen
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
