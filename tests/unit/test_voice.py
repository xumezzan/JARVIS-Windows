"""Audio limits, finite failures, trusted outcome speech and killable pipe cleanup."""

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import PlanResult, Step
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Status
from jarvis.platforms import audio
from jarvis.voice.contracts import MAX_AUDIO_BYTES, AudioClip, Transcript, VoiceError, spoken_result
from jarvis.voice.local import exchange


@pytest.mark.parametrize("pcm", [b"", b"a", b"a" * (MAX_AUDIO_BYTES + 2)])
def test_audio_bounds(pcm: bytes) -> None:
    with pytest.raises(VoiceError, match="invalid_audio"):
        AudioClip(pcm)


@pytest.mark.parametrize("text,confidence", [("", 1), ("x" * 4001, 1), ("стоп", float("nan"))])
def test_transcript_bounds(text: str, confidence: float) -> None:
    with pytest.raises(VoiceError):
        Transcript(text, confidence)


@pytest.mark.parametrize("text", ["Стоп!", "отмена", "отмени задачу", "остановись"])
def test_exact_russian_cancel(text: str) -> None:
    assert Transcript(text, 0.99).is_cancel
    assert not Transcript(text, 0.4).is_cancel
    assert not Transcript("напиши " + text, 0.99).is_cancel


def test_spoken_result_excludes_all_content() -> None:
    outcome = Outcome(uuid4(), Status.SUCCESS, result_json='{"secret":"private payload"}')
    result = PlanResult("finished", (Step("private tool name", outcome),))
    speech = spoken_result(result)
    assert "Проверено выполненных действий: 1" in speech
    assert "private" not in speech and "secret" not in speech
    assert "Реальные действия не выполнялись" in spoken_result(PlanResult("simulated"))
    assert "Результат не подтверждён" in spoken_result(PlanResult("no_action"))


def test_no_sensitive_repr_or_exception() -> None:
    assert "sensitive" not in repr(Transcript("sensitive", 1))
    assert "sensitive" not in repr(AudioClip(b"sensitive!"))
    assert "sensitive" not in str(VoiceError("sensitive"))


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["cancel", "timeout", "oversized", "invalid", "launch_cancel"])
@pytest.mark.parametrize("helper", ["jarvis.platforms.audio", "jarvis.platforms.elevenlabs"])
async def test_real_helper_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    helper: Literal["jarvis.platforms.audio", "jarvis.platforms.elevenlabs"],
    tmp_path: Path,
) -> None:
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []
    script = tmp_path / "helper.py"
    script.write_text(
        "import sys, time\nsys.stdin.buffer.readline()\n"
        + (
            "sys.stdout.write('x' * 2100000); sys.stdout.flush()\n"
            if scenario == "oversized"
            else ""
        )
        + ("print('not-json', flush=True)\n" if scenario == "invalid" else "")
        + "time.sleep(30)\n"
    )

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await original(sys.executable, "-I", str(script), **kwargs)
        children.append(child)
        started.set()
        if scenario == "launch_cancel":
            await asyncio.sleep(0.05)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    task = asyncio.create_task(exchange({"operation": "fixture"}, seconds=0.2, helper=helper))
    await started.wait()
    if scenario in {"cancel", "launch_cancel"}:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(VoiceError):
            await task
    assert children and all(child.returncode is not None for child in children)


@pytest.mark.asyncio
async def test_missing_model_fails_without_microphone_or_network(tmp_path: Path) -> None:
    with pytest.raises(VoiceError, match="model"):
        await exchange({"operation": "transcribe", "model": str(tmp_path)}, seconds=5)


@pytest.mark.parametrize("error", [False, True])
def test_native_capture_bounds_and_disposes_stream(
    monkeypatch: pytest.MonkeyPatch, error: bool
) -> None:
    disposed: list[bool] = []

    class CallbackStop(Exception):
        pass

    class FakeThread:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

    class Stream:
        def __init__(self, **kwargs: Any) -> None:
            self.callback = kwargs["callback"]
            assert kwargs["samplerate"] == 16000 and kwargs["channels"] == 1

        def __enter__(self) -> "Stream":
            try:
                for _ in range(400):
                    self.callback(b"\x01\x00" * 1600, 1600, None, error)
            except CallbackStop:
                pass
            return self

        def __exit__(self, *args: Any) -> None:
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
    monkeypatch.setattr(audio, "Thread", FakeThread)
    if error:
        with pytest.raises(VoiceError, match="overflow"):
            audio.capture()
    else:
        result = audio.capture()
        assert len(base64.b64decode(str(result["pcm"]))) == MAX_AUDIO_BYTES
    assert disposed == [True]


def test_native_decoder_reuses_model_but_not_utterance_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "am").mkdir()
    (tmp_path / "am" / "final.mdl").touch()
    loads: list[str] = []
    decoders: list[object] = []

    def model(*, model_path: str) -> object:
        loads.append(model_path)
        return object()

    class Decoder:
        def __init__(self, value: object, rate: int) -> None:
            decoders.append(self)

        def SetWords(self, words: bool) -> None:
            pass

        def AcceptWaveform(self, pcm: bytes) -> bool:
            return False

        def FinalResult(self) -> str:
            return '{"text":"проверь систему", "result":[{"conf":0.9}]}'

    monkeypatch.setitem(
        sys.modules,
        "vosk",
        SimpleNamespace(Model=model, KaldiRecognizer=Decoder, SetLogLevel=lambda level: None),
    )
    loaded = audio.load_model(tmp_path)
    payload = {"pcm": base64.b64encode(b"\x01\x00" * 3200).decode()}
    assert audio.recognize(payload, loaded)["confidence"] == 0.9
    assert audio.recognize(payload, loaded)["text"] == "проверь систему"
    assert len(loads) == 1 and len(decoders) == 2


@pytest.mark.parametrize(
    "scenario", ["reuse", "cancel", "oversized", "invalid", "launch_cancel", "timeout"]
)
@pytest.mark.asyncio
async def test_persistent_recognition_pipe_cleanup_and_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
) -> None:
    from jarvis.voice.host import AudioHost

    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []
    script = tmp_path / "persistent.py"
    script.write_text(
        "import sys,json,time\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        ' if request["operation"]=="load": print("{\\"ready\\":true}",flush=True); continue\n'
        + (' print("x"*65536,flush=True)\n' if scenario == "oversized" else "")
        + (' print("invalid-json",flush=True)\n' if scenario == "invalid" else "")
        + (" time.sleep(30)\n" if scenario not in {"reuse", "oversized", "invalid"} else "")
        + ' print("{\\"text\\":\\"fixture\\",\\"confidence\\":0.99}",flush=True)\n'
    )
    loop = asyncio.get_running_loop()

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await original(sys.executable, "-I", str(script), **kwargs)
        children.append(child)
        loop.call_soon_threadsafe(started.set)
        if scenario == "launch_cancel":
            await asyncio.sleep(0.05)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    host = AudioHost(tmp_path)
    if scenario == "timeout":
        host.operation_timeout = 0.1
    try:
        if scenario == "reuse":
            # Warm up on the owner's loop, then call across two unrelated asyncio loops.
            host.warmup()
            for _ in range(2):
                result = await asyncio.to_thread(
                    lambda: asyncio.run(host.call({"operation": "transcribe", "pcm": "AA=="}))
                )
                assert result["text"] == "fixture"
            assert len(children) == 1 and children[0].returncode is None
        else:
            task = asyncio.create_task(host.call({"operation": "transcribe", "pcm": "AA=="}))
            await started.wait()
            if scenario in {"cancel", "launch_cancel"}:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif scenario == "timeout":
                with pytest.raises(VoiceError, match="timeout"):
                    await task
            else:
                with pytest.raises(VoiceError):
                    await task
            assert children and all(child.returncode is not None for child in children)
    finally:
        await asyncio.to_thread(host.shutdown)
    assert all(child.returncode is not None for child in children)


def test_model_speech_is_bounded_and_never_hides_engine_warnings() -> None:
    from pydantic import ValidationError

    from jarvis.core.planner.contracts import Proposal

    with pytest.raises(ValidationError):
        Proposal(kind="finish", spoken_summary="x" * 301)
    with pytest.raises(ValidationError):
        Proposal(kind="clarify", question="Куда?", spoken_summary="done")
    assert (
        spoken_result(PlanResult("finished", spoken_summary="Я проверил систему."))
        == "Я проверил систему."
    )
    for status in ("simulated", "no_action", "error", "cancelled", "timeout", "limit"):
        assert "Я всё выполнил" not in spoken_result(
            PlanResult(status, spoken_summary="Я всё выполнил")
        )
