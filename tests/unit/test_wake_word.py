"""Local wake grammar, cancellation and consent regressions; no real microphone/model."""

import asyncio
import base64
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.config import AppConfig
from jarvis.desktop.protocol import Settings
from jarvis.desktop.service import DesktopService
from jarvis.desktop.voice import DesktopVoice
from jarvis.platforms import audio
from jarvis.voice.contracts import VoiceError
from jarvis.voice.host import AudioHost
from jarvis.voice.local import VoskRecognizer
from jarvis.voice.vad import EnergyEndpoint


async def until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_rms_threshold_configuration_and_quiet_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_VOICE_RMS_THRESHOLD", "800")
    monkeypatch.setenv("JARVIS_VOICE_RMS_CALIBRATE", "1")
    detector = EnergyEndpoint.configured()
    assert detector.threshold == 800
    assert not detector.feed(struct.pack("<h", 100) * 1600)
    assert not detector.feed(struct.pack("<h", 200) * 1600)
    assert detector.threshold == 450
    for _ in range(3):
        assert not detector.feed(struct.pack("<h", 1000) * 1600)
    assert detector.speech_started
    assert not any(detector.feed(bytes(3200)) for _ in range(11))
    assert detector.feed(bytes(3200))


def test_calibration_never_counts_separated_noise_as_voiced_onset() -> None:
    detector = EnergyEndpoint(calibrate=True)
    detector.feed(struct.pack("<h", 1000) * 1600)
    detector.feed(bytes(3200))
    detector.feed(bytes(3200))
    assert detector.voiced_frames == 0
    detector.feed(struct.pack("<h", 1000) * 1600)
    detector.feed(struct.pack("<h", 1000) * 1600)
    assert not detector.speech_started


@pytest.mark.parametrize("value", ["nan", "inf", "0", "32769", "bad"])
def test_bad_threshold_is_finite_configuration_failure(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_RMS_THRESHOLD", value)
    with pytest.raises(ValueError):
        EnergyEndpoint.configured()


def test_wake_is_disabled_without_explicit_startup_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_VOICE_WAKE_WORD", raising=False)
    with pytest.raises(VoiceError, match="voice_config"):
        audio.wait_for_wake(object(), Event(), lambda: None)


@pytest.mark.parametrize(
    "text,expected",
    [("джарвис", True), ("слушай джарвис", True), ("антиджарвис", False), ("[unk]", False)],
)
@pytest.mark.parametrize("final", [False, True])
def test_native_wake_uses_exact_local_grammar_without_returning_pcm_or_text(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    expected: bool,
    final: bool,
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    stopped, decoded = Event(), Event()
    disposed: list[bool] = []
    model = SimpleNamespace(vosk_model_find_word=lambda word: 1)

    class Stop(Exception):
        pass

    class Decoder:
        def __init__(self, actual: object, rate: int, grammar: str) -> None:
            assert actual is model and rate == 16000
            assert grammar == '["джарвис", "слушай джарвис", "[unk]"]'

        def AcceptWaveform(self, frame: bytes) -> bool:
            assert len(frame) == 3200
            return final

        def Result(self) -> str:
            import json

            decoded.set()
            return json.dumps({"text": text})

        def PartialResult(self) -> str:
            import json

            decoded.set()
            return json.dumps({"partial": text})

    class Stream:
        def __init__(self, **kwargs: Any) -> None:
            self.callback = kwargs["callback"]

        def produce(self) -> None:
            self.callback(bytes(3200), 1600, None, False)
            decoded.wait(2)
            stopped.set()

        def __enter__(self) -> "Stream":
            self.thread = Thread(target=self.produce)
            self.thread.start()
            return self

        def __exit__(self, *args: object) -> None:
            stopped.set()
            self.thread.join(timeout=3)
            assert not self.thread.is_alive()
            disposed.append(True)

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(RawInputStream=Stream, CallbackStop=Stop, CallbackAbort=Stop),
    )
    monkeypatch.setitem(sys.modules, "vosk", SimpleNamespace(KaldiRecognizer=Decoder))
    assert audio.wait_for_wake(model, stopped, lambda: None) == {"wake": expected}
    assert disposed == [True]


def test_unsupported_wake_model_fails_before_opening_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    with pytest.raises(VoiceError, match="wake_model"):
        audio.wait_for_wake(
            SimpleNamespace(vosk_model_find_word=lambda word: -1), Event(), lambda: None
        )


def recognizer_fixture(monkeypatch: pytest.MonkeyPatch, path: Path) -> VoskRecognizer:
    monkeypatch.setattr(VoskRecognizer, "warmup", lambda self: None)
    return VoskRecognizer(path)


@pytest.mark.asyncio
async def test_wake_transitions_to_endpoint_recording_and_review_without_gesture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    recognizer = recognizer_fixture(monkeypatch, tmp_path)
    calls: list[dict[str, object]] = []
    texts: list[str] = []
    starts: list[str] = []

    async def call(request: dict[str, object], **kwargs: Any) -> dict[str, object]:
        calls.append(request)
        if kwargs.get("ready"):
            kwargs["ready"]()
        if request["operation"] == "wake":
            return {"wake": True}
        if request["operation"] == "record":
            assert request["auto_stop_on_silence"] is True
            return {"pcm": base64.b64encode(bytes(6400)).decode()}
        return {"text": "проверь систему", "confidence": 0.99}

    monkeypatch.setattr(recognizer.host, "call", call)

    def start(text: str, mode: object) -> bool:
        starts.append(text)
        return True

    voice = DesktopVoice(
        lambda: None,
        texts.append,
        start,
        lambda: None,
        recognizer=recognizer,
    )
    voice.wake_allowed = True
    try:
        voice.reconcile_wake(True)
        await until(lambda: bool(texts) and voice.task is None)
        assert [c["operation"] for c in calls] == ["wake", "record", "transcribe"]
        assert texts == ["проверь систему"] and not starts
        assert voice.phase == "idle" and not voice.background
        assert voice.wake_review
        voice.reconcile_wake(True)
        assert voice.wake_task is None
    finally:
        await voice.shutdown()


@pytest.mark.asyncio
async def test_stop_rejects_a_late_wake_and_does_not_rearm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    recognizer = recognizer_fixture(monkeypatch, tmp_path)
    entered = asyncio.Event()

    async def call(request: dict[str, object], **kwargs: Any) -> dict[str, object]:
        assert request["operation"] == "wake"
        entered.set()
        while not kwargs["released"].is_set():
            await asyncio.sleep(0.005)
        return {"wake": True}

    monkeypatch.setattr(recognizer.host, "call", call)
    voice = DesktopVoice(
        lambda: None,
        lambda text: pytest.fail("Late wake produced a transcript"),
        lambda text, mode: False,
        lambda: None,
        recognizer=recognizer,
    )
    voice.wake_allowed = True
    try:
        voice.reconcile_wake(True)
        await entered.wait()
        voice.cancel()
        await voice.stop_wake()
        voice.reconcile_wake(True)
        assert voice.wake_suspended and voice.wake_task is None and voice.task is None
        assert voice.phase == "idle" and "выключен" in voice.status
    finally:
        await voice.shutdown()


@pytest.mark.asyncio
async def test_wake_autostart_does_not_supply_cloud_consent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    recognizer = recognizer_fixture(monkeypatch, tmp_path)

    async def call(request: dict[str, object], **kwargs: Any) -> dict[str, object]:
        if request["operation"] == "wake":
            return {"wake": True}
        if request["operation"] == "record":
            return {"pcm": base64.b64encode(bytes(6400)).decode()}
        return {"text": "проверь систему", "confidence": 0.99}

    monkeypatch.setattr(recognizer.host, "call", call)
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    service.settings = Settings(provider="openai", model="fixture", auto_start=True)
    service.voice.recognizer = recognizer
    service.voice.auto_start = True
    service.voice.wake_allowed = True
    try:
        service.publish()
        await until(lambda: service.voice.wake_review and service.voice.task is None)
        assert service.worker is None and service.started_count == 0
        assert "разрешите" in service.notice
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_real_host_protocol_reuses_one_model_for_wake_record_and_transcribe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "1")
    script = tmp_path / "wake_helper.py"
    marker = tmp_path / "model-loads"
    script.write_text(
        "import base64\nfrom pathlib import Path\nfrom jarvis.platforms import audio\n"
        f"marker=Path({str(marker)!r})\n"
        "def load(path):\n"
        " marker.write_text(marker.read_text()+'1' if marker.exists() else '1')\n"
        " return object()\n"
        "def wake(model,released,ready): ready(); return {'wake':True}\n"
        "def record(**kwargs):\n"
        " assert kwargs['auto_stop_on_silence']; kwargs['ready']()\n"
        " return {'pcm':base64.b64encode(bytes(6400)).decode()}\n"
        "audio.load_model=load\naudio.wait_for_wake=wake\naudio.capture=record\n"
        "audio.recognize=lambda request,model: {'text':'local','confidence':0.99}\n"
        "raise SystemExit(audio.recognition_host())\n"
    )
    actual_launch = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await actual_launch(sys.executable, "-I", str(script), **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    host = AudioHost(tmp_path)
    try:
        wake = await host.call({"operation": "wake"}, released=Event(), ready=lambda: None)
        assert wake == {"wake": True}
        recorded = await host.call(
            {"operation": "record", "auto_stop_on_silence": True},
            released=Event(),
            ready=lambda: None,
        )
        assert len(base64.b64decode(recorded["pcm"])) == 6400
        result = await host.call({"operation": "transcribe", "pcm": recorded["pcm"]})
        assert result["text"] == "local" and marker.read_text() == "1"
        assert len(children) == 1 and children[0].returncode is None
    finally:
        await asyncio.to_thread(host.shutdown)
    assert children[0].returncode is not None
