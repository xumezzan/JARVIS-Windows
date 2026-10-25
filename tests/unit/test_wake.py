"""The standing listener: it decides, it does not transcribe. No microphone is used."""

import json
from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from tests.voice_support import VoiceFixture

from jarvis.platforms.audio import GRAMMAR, WAKE_CONFIDENCE, WAKE_NAME, WAKE_SECONDS, named
from jarvis.ui.voice_worker import VoiceWorker
from jarvis.voice.contracts import VoiceError
from jarvis.voice.local import exchange
from jarvis.voice.wake import WAKE_TIMEOUT, LocalWake


class CloudEar:
    local_only = False

    async def listen(self, released: Event, ready: Callable[[], None]) -> bool:
        return True


def test_the_listener_can_only_ever_answer_with_the_name() -> None:
    grammar = json.loads(GRAMMAR)
    # Two outcomes exist: the name, and "not the name". No transcript can come out of it.
    assert set(grammar) == {WAKE_NAME, "[unk]"}


def final(word: str, confidence: float) -> str:
    return json.dumps({"text": word, "result": [{"word": word, "conf": confidence}]})


def test_only_a_confident_finished_segment_counts_as_hearing_the_name() -> None:
    assert named(final(WAKE_NAME, 1.0))
    assert not named(final("[unk]", 1.0))
    # Measured on the real model: an ordinary phrase named it at 0.555, the owner at 1.0.
    assert not named(final(WAKE_NAME, 0.555))
    # A partial hypothesis wanders onto the only word the grammar knows, so it is ignored.
    assert not named(json.dumps({"partial": WAKE_NAME}))
    # A text without word confidences decides nothing either.
    assert not named(json.dumps({"text": WAKE_NAME}))
    assert not named(json.dumps({"text": ""})) and not named("not json at all")


def test_a_listening_cycle_is_bounded_and_answered_before_the_caller_gives_up() -> None:
    assert 0 < WAKE_SECONDS <= 300 and 0.5 < WAKE_CONFIDENCE <= 1
    assert WAKE_TIMEOUT > WAKE_SECONDS


@pytest.mark.asyncio
async def test_a_missing_model_stops_the_listener_before_any_device(tmp_path: Path) -> None:
    # The real helper process runs; it refuses on the model and never opens a microphone.
    with pytest.raises(VoiceError, match="model"):
        await exchange({"operation": "wake", "model": str(tmp_path)}, seconds=20)


@pytest.mark.asyncio
async def test_the_listener_asks_for_nothing_and_answers_with_one_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    async def fake_exchange(
        request: dict[str, object],
        *,
        seconds: float,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
        helper: str = "jarvis.platforms.audio",
    ) -> dict[str, object]:
        sent.update(request)
        if ready is not None:
            ready()
        # Even if a helper answered with audio, nothing but the decision is taken from it.
        return {"heard": True, "pcm": "AAAA"}

    monkeypatch.setattr("jarvis.voice.wake.exchange", fake_exchange)
    heard = await LocalWake(Path("model")).listen(Event(), lambda: None)
    assert heard is True
    assert sent == {"operation": "wake", "model": "model"}


def test_a_standing_listener_is_local_only() -> None:
    fixture = VoiceFixture()
    with pytest.raises(ValueError):
        VoiceWorker(fixture, fixture, fixture, wake=CloudEar())


@pytest.mark.asyncio
async def test_listening_leaves_no_audio_behind(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    with pytest.raises(VoiceError):
        await exchange({"operation": "wake", "model": str(folder)}, seconds=20)
    # The helper answers with a decision, so there is nothing to write down in the first place.
    assert list(folder.iterdir()) == []
