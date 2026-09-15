"""Audio limits, finite failures, trusted outcome speech and killable pipe cleanup."""

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
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
async def test_real_helper_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
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
    task = asyncio.create_task(exchange({"operation": "fixture"}, seconds=0.2))
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
