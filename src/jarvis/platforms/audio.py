"""Native audio helper. Fixed operations, local models/voices, no network or recordings on disk."""

import base64
import importlib
import json
import os
import sys
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any, cast

from jarvis.voice.contracts import MAX_AUDIO_BYTES, MAX_SECONDS, SAMPLE_RATE, AudioClip, VoiceError
from jarvis.voice.vad import FRAME_BYTES, EnergyEndpoint


def capture(
    pending: bytes = b"",
    *,
    model: Any = None,
    released: Event | None = None,
    auto_stop_on_silence: bool = False,
    ready: Callable[[], None] | None = None,
) -> dict[str, object]:
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except ImportError:
        raise VoiceError("voice_failed") from None
    stop = released if released is not None else Event()
    failed = Event()
    pcm = bytearray()
    try:
        detector = EnergyEndpoint.configured()
    except ValueError:
        raise VoiceError("voice_config") from None
    frames_to_decode: Queue[bytes] = Queue(maxsize=8)
    decoder = None
    if auto_stop_on_silence:
        if model is None:
            raise VoiceError("model")
        decoder = importlib.import_module("vosk").KaldiRecognizer(model, SAMPLE_RATE)

    def release() -> None:
        # EOF also ends capture if the parent disappears. No raw command interpretation.
        # Raw descriptor read: a daemon must not hold Python's buffered stdin lock
        # during interpreter shutdown at the automatic recording limit.
        if not pending:
            os.read(sys.stdin.fileno(), 16)
        stop.set()

    if released is None:
        Thread(target=release, daemon=True).start()

    def receive(data: Any, frames: int, timing: Any, status: Any) -> None:
        if status:
            failed.set()
            stop.set()
            raise sd.CallbackAbort
        if stop.is_set():
            raise sd.CallbackStop
        chunk = bytes(data)
        if len(chunk) != FRAME_BYTES or frames != 1600:
            failed.set()
            stop.set()
            raise sd.CallbackAbort
        if len(pcm) + len(chunk) > MAX_AUDIO_BYTES:
            stop.set()
            raise sd.CallbackStop
        pcm.extend(chunk)
        ended = detector.feed(chunk)
        if decoder is not None:
            try:
                frames_to_decode.put_nowait(chunk)
            except Full:
                failed.set()
                stop.set()
                raise sd.CallbackAbort from None
        if auto_stop_on_silence and ended:
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
            if ready is not None:
                ready()
            else:
                print('{"ready":true}', flush=True)
            if decoder is None:
                stop.wait(MAX_SECONDS)
            else:
                from time import monotonic

                deadline = monotonic() + MAX_SECONDS
                while not stop.is_set() and monotonic() < deadline:
                    try:
                        frame = frames_to_decode.get(timeout=0.1)
                    except Empty:
                        continue
                    if decoder.AcceptWaveform(frame):
                        decoder.Result()  # Consume streaming endpoints; final decoding stays local.
                while not frames_to_decode.empty():
                    if decoder.AcceptWaveform(frames_to_decode.get_nowait()):
                        decoder.Result()
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


def load_model(model_path: Path) -> Any:
    if not model_path.is_dir() or not (model_path / "am" / "final.mdl").is_file():
        raise VoiceError("model")
    try:
        from vosk import Model, SetLogLevel  # type: ignore[import-untyped]

        SetLogLevel(-1)
        return Model(model_path=str(model_path))
    except ImportError:
        raise VoiceError("voice_failed") from None
    except Exception:
        raise VoiceError("model") from None


WAKE_GRAMMAR = '["джарвис", "слушай джарвис", "[unk]"]'


def wait_for_wake(model: Any, released: Event, ready: Callable[[], None]) -> dict[str, object]:
    """Local, opted-in bounded listening; no background PCM/text leaves this process."""
    if os.environ.get("JARVIS_VOICE_WAKE_WORD", "0") != "1":
        raise VoiceError("voice_config")
    try:
        EnergyEndpoint.configured()
    except ValueError:
        raise VoiceError("voice_config") from None
    from time import monotonic

    try:
        if model.vosk_model_find_word("джарвис") < 0:
            raise VoiceError("wake_model")
        decoder = importlib.import_module("vosk").KaldiRecognizer(model, SAMPLE_RATE, WAKE_GRAMMAR)
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("wake_model") from None
    sd = importlib.import_module("sounddevice")
    frames: Queue[bytes] = Queue(maxsize=8)
    failed = Event()

    def receive(data: Any, count: int, timing: Any, status: Any) -> None:
        if released.is_set():
            raise sd.CallbackStop
        chunk = bytes(data)
        if status or count != 1600 or len(chunk) != FRAME_BYTES:
            failed.set()
            released.set()
            raise sd.CallbackAbort
        try:
            frames.put_nowait(chunk)
        except Full:
            failed.set()
            released.set()
            raise sd.CallbackAbort from None

    if released.is_set():
        return {"wake": False}
    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=1600, callback=receive
        ):
            ready()
            deadline = monotonic() + MAX_SECONDS
            while not released.is_set() and monotonic() < deadline:
                try:
                    frame = frames.get(timeout=0.1)
                except Empty:
                    continue
                final = decoder.AcceptWaveform(frame)
                data = json.loads(decoder.Result() if final else decoder.PartialResult())
                text = data.get("text" if final else "partial", "")
                if isinstance(text, str) and "джарвис" in text.casefold().split():
                    return {"wake": True}
        if failed.is_set():
            raise VoiceError("overflow")
        return {"wake": False}
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("device") from None


def recognize(request: dict[str, Any], model: Any = None) -> dict[str, object]:
    if model is None:
        model = load_model(Path(request.get("model", "")))
    try:
        from vosk import KaldiRecognizer

        clip = AudioClip(base64.b64decode(request["pcm"], validate=True))
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
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


def recognition_host() -> int:
    """One model per process; capture opens a device only for an explicit record request."""
    limit = MAX_AUDIO_BYTES * 2 + 16384
    model = None
    recording: tuple[str, Event, Event, Thread] | None = None

    def emit(result: dict[str, object]) -> None:
        print(json.dumps(result, ensure_ascii=True), flush=True)

    def record(
        capture_id: str, released: Event, finished: Event, enabled: bool, wake: bool
    ) -> None:
        try:
            result = (
                wait_for_wake(model, released, lambda: emit({"ready": True}))
                if wake
                else capture(
                    model=model,
                    released=released,
                    auto_stop_on_silence=enabled,
                    ready=lambda: emit({"ready": True}),
                )
            )
        except Exception as exc:
            result = {"error": exc.code if isinstance(exc, VoiceError) else "voice_failed"}
        # Mark the slot finished before emitting the response which permits the next call.
        finished.set()
        emit(result)

    try:
        for index in range(10000):
            raw = sys.stdin.buffer.readline(limit + 1)
            if not raw:
                return 0
            if len(raw) > limit or not raw.endswith(b"\n"):
                return 2
            try:
                request = json.loads(raw)
                operation = request.get("operation")
                if operation == "stop_record":
                    if recording is not None and request.get("capture_id") == recording[0]:
                        recording[1].set()
                    continue
                if recording is not None:
                    if not recording[2].is_set():
                        return 2  # No overlapping requests or audio operations.
                    recording[3].join()
                    recording = None
                if index == 0 and operation == "load":
                    model = load_model(Path(request["model"]))
                    result: dict[str, object] = {"ready": True}
                elif model is not None and operation == "transcribe":
                    result = recognize(request, model)
                elif model is not None and operation in {"record", "wake"}:
                    capture_id = request.get("capture_id")
                    enabled = request.get("auto_stop_on_silence", False)
                    if (
                        not isinstance(capture_id, str)
                        or len(capture_id) != 32
                        or type(enabled) is not bool
                    ):
                        raise VoiceError("voice_failed")
                    released, finished = Event(), Event()
                    thread = Thread(
                        target=record,
                        args=(capture_id, released, finished, enabled, operation == "wake"),
                    )
                    recording = (capture_id, released, finished, thread)
                    thread.start()
                    continue
                else:
                    raise VoiceError("voice_failed")
            except Exception as exc:
                result = {"error": exc.code if isinstance(exc, VoiceError) else "voice_failed"}
            emit(result)
        return 0
    finally:
        if recording is not None:
            recording[1].set()
            recording[3].join(timeout=2)


def main() -> int:
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 2
    if sys.argv[1:] == ["--recognition-host"]:
        return recognition_host()
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
            result = capture(pending)
        elif operation == "transcribe":
            result = recognize(request)
        elif operation == "speak":
            result = speak(request)
        else:
            raise VoiceError("voice_failed")
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"error": exc.code if isinstance(exc, VoiceError) else "voice_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
