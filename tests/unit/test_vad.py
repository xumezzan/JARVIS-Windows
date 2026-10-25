"""Deterministic PCM endpointing, real stream processing and bounded helper protocol."""

import asyncio
import base64
import struct
import sys
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.platforms import audio
from jarvis.voice.host import AudioHost
from jarvis.voice.local import LocalRecorder
from jarvis.voice.vad import FRAME_BYTES, EnergyEndpoint, rms_energy

LOUD = struct.pack("<h", 1500) * 1600
QUIET = bytes(FRAME_BYTES)


def test_rms_signed_pcm_and_no_endpoint_before_sustained_speech() -> None:
    assert rms_energy(LOUD) == 1500
    assert rms_energy(struct.pack("<h", -32768) * 1600) == 32768
    detector = EnergyEndpoint()
    assert not any(detector.feed(QUIET) for _ in range(40))
    assert not detector.feed(LOUD)
    assert not any(detector.feed(QUIET) for _ in range(20))
    assert not detector.speech_started


def test_endpoint_is_exactly_twelve_quiet_frames_after_speech() -> None:
    detector = EnergyEndpoint()
    assert not any(detector.feed(LOUD) for _ in range(3))
    assert detector.speech_started
    assert not any(detector.feed(QUIET) for _ in range(11))
    assert detector.feed(QUIET)


def test_resumed_speech_resets_silence_counter() -> None:
    detector = EnergyEndpoint()
    for _ in range(3):
        detector.feed(LOUD)
    for _ in range(11):
        detector.feed(QUIET)
    assert not detector.feed(LOUD)
    assert not any(detector.feed(QUIET) for _ in range(11))
    assert detector.feed(QUIET)


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_invalid_rms_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValueError):
        EnergyEndpoint(threshold=threshold)


@pytest.mark.parametrize("manual", [False, True])
def test_capture_streams_every_frame_to_vosk_and_stops_without_timeout(
    monkeypatch: pytest.MonkeyPatch,
    manual: bool,
) -> None:
    decoded = Event()
    released = Event()
    chunks = [LOUD] * 3 + [QUIET] * (2 if manual else 12)
    seen: list[bytes] = []
    disposed: list[bool] = []
    ready: list[bool] = []
    errors: list[BaseException] = []

    class CallbackStop(Exception):
        pass

    class Decoder:
        def __init__(self, model: object, rate: int) -> None:
            assert rate == 16000

        def AcceptWaveform(self, frame: bytes) -> bool:
            seen.append(frame)
            decoded.set()
            return len(seen) == 4

        def Result(self) -> str:
            return '{"text":"fixture endpoint"}'

    class Stream:
        def __init__(self, **kwargs: Any) -> None:
            self.callback = kwargs["callback"]
            assert kwargs["blocksize"] == 1600 and kwargs["dtype"] == "int16"

        def produce(self) -> None:
            try:
                for chunk in chunks:
                    decoded.clear()
                    try:
                        self.callback(chunk, 1600, None, False)
                    except CallbackStop:
                        break
                    assert decoded.wait(2), "Streaming decoder did not consume the frame"
                if manual:
                    released.set()
            except BaseException as error:
                errors.append(error)
                released.set()

        def __enter__(self) -> "Stream":
            self.thread = Thread(target=self.produce)
            self.thread.start()
            return self

        def __exit__(self, *args: Any) -> None:
            released.set()
            self.thread.join(timeout=3)
            assert not self.thread.is_alive()
            disposed.append(True)

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            RawInputStream=Stream,
            CallbackStop=CallbackStop,
            CallbackAbort=CallbackStop,
        ),
    )
    monkeypatch.setitem(sys.modules, "vosk", SimpleNamespace(KaldiRecognizer=Decoder))
    result = audio.capture(
        model=object(),
        released=released,
        auto_stop_on_silence=True,
        ready=lambda: ready.append(True),
    )
    assert not errors
    assert base64.b64decode(str(result["pcm"])) == b"".join(chunks)
    assert seen == chunks
    assert ready == disposed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["auto", "release", "cancel"])
async def test_host_record_protocol_is_bounded_gesture_scoped_and_reuses_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
) -> None:
    script = tmp_path / "record_helper.py"
    script.write_text(
        "import base64\n"
        "from jarvis.platforms import audio\n"
        "audio.load_model=lambda path: object()\n"
        "def capture(*,model,released,auto_stop_on_silence,ready):\n"
        " assert model is not None and auto_stop_on_silence\n"
        " ready()\n"
        + (" assert released.wait(5)\n" if scenario != "auto" else "")
        + " return {'pcm':base64.b64encode(bytes(48000)).decode()}\n"
        "audio.capture=capture\n"
        "audio.recognize=lambda request,model: {'text':'local','confidence':0.99}\n"
        "raise SystemExit(audio.recognition_host())\n"
    )
    real_launch = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await real_launch(sys.executable, "-I", str(script), **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    host = AudioHost(tmp_path)
    recorder = LocalRecorder(host=host, auto_stop_on_silence=True)
    released = Event()
    ready = asyncio.Event()

    def on_ready() -> None:
        ready.set()
        if scenario == "release":
            released.set()

    try:
        task = asyncio.create_task(recorder.record(released, on_ready))
        await asyncio.wait_for(ready.wait(), timeout=5)
        if scenario == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert children[0].returncode is not None
        else:
            clip = await asyncio.wait_for(task, timeout=5)
            assert len(clip.pcm) == 48000  # Larger than the old recognition reply cap.
            assert (await host.call({"operation": "transcribe", "pcm": "AA=="}))["text"] == "local"
            assert len(children) == 1
    finally:
        await asyncio.to_thread(host.shutdown)
    assert all(child.returncode is not None for child in children)


@pytest.mark.asyncio
async def test_release_during_model_warmup_never_dispatches_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "slow_model.py"
    marker = tmp_path / "record-dispatched"
    script.write_text(
        "import json,sys,time\n"
        "from pathlib import Path\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if r['operation']=='load':\n"
        "  time.sleep(0.1); print(json.dumps({'ready':True}),flush=True)\n"
        f" elif r['operation']=='record': Path({str(marker)!r}).touch()\n"
    )
    real_launch = asyncio.create_subprocess_exec

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        return await real_launch(sys.executable, "-I", str(script), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    host = AudioHost(tmp_path)
    released = Event()
    handle = asyncio.get_running_loop().call_later(0.02, released.set)
    try:
        with pytest.raises(asyncio.CancelledError):
            await LocalRecorder(host=host, auto_stop_on_silence=True).record(released, lambda: None)
        assert not marker.exists()
    finally:
        handle.cancel()
        await asyncio.to_thread(host.shutdown)
