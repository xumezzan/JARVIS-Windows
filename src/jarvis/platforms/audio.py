"""Native audio helper. Fixed operations, local models/voices, no network or recordings on disk."""

import base64
import json
import os
import sys
from array import array
from collections import deque
from io import BufferedReader
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any, cast

from jarvis.platforms.protocol import emit
from jarvis.voice.contracts import MAX_AUDIO_BYTES, MAX_SECONDS, SAMPLE_RATE, AudioClip, VoiceError

# Hands-free segmentation, in 100 ms blocks of 16 kHz mono audio.
SPEECH_PEAK = 700  # int16 peak that counts as speech even in a silent room
NOISE_BLOCKS = 3  # the first 0.3 s measures the room instead of the speaker
SPEECH_BLOCKS = 3  # a click or a door is not a phrase
QUIET_BLOCKS = 12  # 1.2 s of quiet ends the phrase
WAIT_BLOCKS = 200  # 20 s without speech ends the attempt so the caller can retry

# Standing listening, bounded like everything else: one cycle of two minutes, a ring of
# three seconds of recent audio, and a grammar that can only answer with the name.
WAKE_SECONDS = 120
RING_BLOCKS = 30
WAKE_NAME = "джарвис"
# Measured: the name spoken to the machine comes back at 1.0, an accidental match at 0.555.
WAKE_CONFIDENCE = 0.9
GRAMMAR = '["джарвис", "[unk]"]'


class Segmenter:
    """Decides when a spoken phrase has ended, from block peak amplitudes alone.

    Deliberately content-blind: it sees loudness, never audio, and holds no recording.
    The first blocks measure the room, so a noisy place raises the bar instead of
    treating its own hum as speech.
    """

    def __init__(self) -> None:
        self.noise = 0
        self.blocks = 0
        self.voiced = 0
        self.quiet = 0
        self.started = False

    def feed(self, peak: int) -> bool:
        """Take one block's peak amplitude; return True when the phrase is over."""
        self.blocks += 1
        if self.blocks <= NOISE_BLOCKS:
            self.noise = max(self.noise, peak)
            return False
        if peak >= max(SPEECH_PEAK, self.noise * 3):
            self.voiced += 1
            self.quiet = 0
            if self.voiced >= SPEECH_BLOCKS:
                self.started = True
        elif self.started:
            self.quiet += 1
            if self.quiet >= QUIET_BLOCKS:
                return True
        else:
            self.voiced = 0
        return False


def stop_on_word(pending: bytes, stop: Event) -> None:
    """End the operation when the parent says so, or when the parent is gone at all."""

    def release() -> None:
        # EOF also ends capture if the parent disappears. No raw command interpretation.
        # Raw descriptor read: a daemon must not hold Python's buffered stdin lock
        # during interpreter shutdown at the automatic recording limit.
        if not pending:
            os.read(sys.stdin.fileno(), 16)
        stop.set()

    Thread(target=release, daemon=True).start()


def capture(pending: bytes = b"", listen: bool = False) -> dict[str, object]:
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except ImportError:
        raise VoiceError("voice_failed") from None
    stop = Event()
    failed = Event()
    speech = Event()
    pcm = bytearray()
    segmenter = Segmenter()
    stop_on_word(pending, stop)

    def receive(data: Any, frames: int, timing: Any, status: Any) -> None:
        if status:
            failed.set()
            stop.set()
            raise sd.CallbackAbort
        if stop.is_set():
            raise sd.CallbackStop
        chunk = bytes(data)
        if len(pcm) + len(chunk) > MAX_AUDIO_BYTES:
            stop.set()
            raise sd.CallbackStop
        pcm.extend(chunk)
        if not listen:
            return
        # Peak amplitude only: cheap enough for the audio callback and never inspects content.
        samples = array("h", chunk)
        finished = segmenter.feed(max(max(samples), -min(samples)) if samples else 0)
        if segmenter.started:
            speech.set()
        if finished:
            stop.set()
            raise sd.CallbackStop

    try:
        # No input stream before an explicit UI request. Device is the user's OS default.
        if stop.is_set():
            raise VoiceError("silence")
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1600,
            callback=receive,
        ):
            emit('{"ready":true}')
            if listen and not speech.wait(WAIT_BLOCKS / 10) and not stop.is_set():
                stop.set()
                raise VoiceError("silence")
            stop.wait(MAX_SECONDS)
            stop.set()
        if failed.is_set():
            raise VoiceError("overflow")
        if len(pcm) < 3200:
            raise VoiceError("silence")
        return {"pcm": base64.b64encode(bytes(pcm)).decode("ascii")}
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("device") from None
    finally:
        pcm.clear()


def recognizer_for(request: dict[str, Any], grammar: str = "") -> Any:
    """One local Vosk recogniser, with an explicit model path and no downloading."""
    model_path = Path(request.get("model", ""))
    if not model_path.is_dir() or not (model_path / "am" / "final.mdl").is_file():
        raise VoiceError("model")
    from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore[import-untyped]

    SetLogLevel(-1)
    model = Model(model_path=str(model_path))
    if grammar:
        # A grammar of two outcomes: the name, or something that is not it. With it the
        # listener cannot produce a transcript of the room even if it wanted to.
        return KaldiRecognizer(model, SAMPLE_RATE, grammar)
    return KaldiRecognizer(model, SAMPLE_RATE)


def named(raw: str) -> bool:
    """True only when a finished segment recognised the name itself, and was sure of it.

    Two measurements on the installed Russian model shaped this rule. A grammar this small
    keeps offering its only real word while a phrase is still in flight, so an ordinary
    sentence shows the name in a partial hypothesis and then finalises as "[unk]": partials
    are not consulted at all. And a finalised segment can still name it by accident - one
    ordinary phrase came back as the name with confidence 0.555, while every real utterance
    of it came back at 1.0. So what decides is the word's own confidence, not its presence.
    """
    try:
        answer = json.loads(raw)
    except ValueError:
        return False
    words = answer.get("result")
    if not isinstance(words, list):
        return False
    for word in words:
        if not isinstance(word, dict) or isinstance(word.get("conf"), bool):
            continue
        said = str(word.get("word", "")).casefold().strip(".,!?:;")
        confidence = word.get("conf")
        sure = isinstance(confidence, int | float) and float(confidence) >= WAKE_CONFIDENCE
        if said == WAKE_NAME and sure:
            return True
    return False


def watch(request: dict[str, Any], pending: bytes = b"") -> dict[str, object]:
    """Wait for the name, and answer only whether it was heard.

    Audio never leaves this process: recent blocks live in a bounded ring that is consumed
    as it fills and dropped when the operation ends. Nothing is written to disk, and the
    result carries no sound and no words.
    """
    try:
        import sounddevice as sd
    except ImportError:
        raise VoiceError("voice_failed") from None
    try:
        recognizer = recognizer_for(request, GRAMMAR)
        # Word confidences are the whole decision, so they are asked for explicitly.
        recognizer.SetWords(True)
    except VoiceError:
        raise
    except ImportError:
        raise VoiceError("voice_failed") from None
    except Exception:
        raise VoiceError("model") from None
    stop = Event()
    failed = Event()
    heard = Event()
    blocks: deque[bytes] = deque(maxlen=RING_BLOCKS)
    stop_on_word(pending, stop)

    def receive(data: Any, frames: int, timing: Any, status: Any) -> None:
        if status:
            failed.set()
            stop.set()
            raise sd.CallbackAbort
        if stop.is_set():
            raise sd.CallbackStop
        # The callback only hands the block over; recognition happens off the audio thread.
        blocks.append(bytes(data))

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1600,
            callback=receive,
        ):
            emit('{"ready":true}')
            deadline = monotonic() + WAKE_SECONDS
            while not stop.is_set() and monotonic() < deadline:
                if not blocks:
                    stop.wait(0.02)
                    continue
                block = blocks.popleft()
                # Only a segment the decoder has finished with is evidence of anything.
                if recognizer.AcceptWaveform(block) and named(recognizer.Result()):
                    heard.set()
                    break
            stop.set()
        if failed.is_set():
            raise VoiceError("overflow")
        return {"heard": heard.is_set()}
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("device") from None
    finally:
        blocks.clear()


def recognize(request: dict[str, Any]) -> dict[str, object]:
    try:
        # The model is checked before the audio: a missing model is its own answer.
        recognizer = recognizer_for(request)
        clip = AudioClip(base64.b64decode(request["pcm"], validate=True))
        recognizer.SetWords(True)
        segments: list[dict[str, Any]] = []
        for offset in range(0, len(clip.pcm), 8000):
            if recognizer.AcceptWaveform(clip.pcm[offset : offset + 8000]):
                segments.append(json.loads(recognizer.Result()))
        segments.append(json.loads(recognizer.FinalResult()))
        text = " ".join(str(segment.get("text", "")) for segment in segments).strip()
        if not text:
            raise VoiceError("silence")
        confidences = [
            float(word["conf"]) for segment in segments for word in segment.get("result", [])
        ]
        return {"text": text, "confidence": min(confidences, default=0.0)}
    except VoiceError:
        raise
    except ImportError:
        raise VoiceError("voice_failed") from None
    except Exception:
        raise VoiceError("recognition") from None


def speak(request: dict[str, Any]) -> dict[str, object]:
    text = request.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= 500:
        raise VoiceError("speech")
    try:
        import pyttsx3  # type: ignore[import-untyped]

        # Explicit native engine; never install or select a cloud driver.
        driver = {"win32": "sapi5", "darwin": "nsss"}.get(sys.platform, "espeak")
        engine = pyttsx3.init(driverName=driver)
        try:
            russian = []
            for voice in engine.getProperty("voices"):
                languages = " ".join(
                    item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
                    for item in voice.languages
                ).lower()
                if (
                    "ru" in languages
                    or "russian" in voice.id.lower()
                    or "ru-ru" in voice.id.lower()
                ):
                    russian.append(voice)
            if not russian:
                raise VoiceError("speech")
            completed: list[bool] = []
            engine.connect("finished-utterance", lambda name, completed: finished(completed))

            def finished(value: bool) -> None:
                completed.append(value)

            engine.setProperty("voice", russian[0].id)
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
            if not completed or not completed[-1]:
                raise VoiceError("speech")
            return {"spoken": True}
        finally:
            engine.stop()
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("speech") from None


def main() -> int:
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 2
    try:
        limit = MAX_AUDIO_BYTES * 2 + 16384
        raw = bytearray()
        reader = cast(BufferedReader, sys.stdin.buffer)
        while b"\n" not in raw:
            chunk = reader.read1(min(65536, limit - len(raw)))
            if not chunk:
                raise VoiceError("voice_failed")
            raw.extend(chunk)
        line, _, pending = bytes(raw).partition(b"\n")
        request = json.loads(line)
        operation = request.get("operation")
        if operation == "record":
            result = capture(pending, bool(request.get("listen")))
        elif operation == "wake":
            result = watch(request, pending)
        elif operation == "transcribe":
            result = recognize(request)
        elif operation == "speak":
            result = speak(request)
        else:
            raise VoiceError("voice_failed")
        emit(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        emit(json.dumps({"error": exc.code if isinstance(exc, VoiceError) else "voice_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
