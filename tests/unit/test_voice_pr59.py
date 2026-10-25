"""Audio limits, finite failures, trusted outcome speech and killable pipe cleanup."""

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.planner.contracts import PlanResult
from jarvis.platforms import audio
from jarvis.voice.contracts import VoiceError, spoken_result


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
