"""Free-only cloud speech policy with synthetic API and credential fixtures."""

import asyncio
from typing import Any

import pytest

from jarvis.platforms import elevenlabs as api
from jarvis.voice import elevenlabs as voice
from jarvis.voice.contracts import MAX_AUDIO_BYTES, VoiceError


def subscription(**changes: Any) -> dict[str, Any]:
    return {
        "tier": "free",
        "can_extend_character_limit": False,
        "allowed_to_extend_character_limit": False,
        "max_credit_limit_extension": 0,
        "character_count": 10,
        "character_limit": 1000,
        **changes,
    }


class FixtureClient(api.Client):
    def __init__(self, **changes: Any) -> None:
        super().__init__("synthetic-key")
        self.subscription = subscription(**changes)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.audio = b"\0\0" * 100
        self.fail = False

    def data(self, path: str) -> dict[str, Any]:
        self.calls.append((path, None))
        if path == api.VOICE_PATH:
            return {
                "voices": [
                    {"category": "premade", "voice_id": "default", "name": "Default"},
                    {"category": "professional", "voice_id": "library", "name": "Library"},
                    {
                        "category": "premade",
                        "voice_id": "paid",
                        "name": "Paid",
                        "available_for_tiers": ["starter"],
                    },
                ]
            }
        return self.subscription

    def request(self, path: str, payload: dict[str, Any] | None = None) -> bytes:
        self.calls.append((path, payload))
        if self.fail:
            raise VoiceError("eleven_network")
        return self.audio


@pytest.mark.parametrize(
    "changes",
    [
        {"tier": "starter"},
        {"tier": None},
        {"can_extend_character_limit": True},
        {"allowed_to_extend_character_limit": True},
        {"max_credit_limit_extension": "unlimited"},
        {"max_credit_limit_extension": None},
        {"max_credit_limit_extension": True},
        {"character_count": -1},
        {"character_limit": False},
        {"character_count": 1000},
        {"character_count": 999},
    ],
)
def test_no_post_on_paid_unknown_or_exhausted_account(changes: dict[str, Any]) -> None:
    client = FixtureClient(**changes)
    with pytest.raises(VoiceError):
        client.synthesize("default", "Привет")
    assert all(payload is None for _, payload in client.calls)


def test_fresh_guard_fixed_model_and_no_retry() -> None:
    client = FixtureClient()
    assert client.synthesize("default", "Привет") == client.audio
    assert client.calls[-2] == ("/v1/user/subscription", None)
    assert client.calls[-1] == (
        "/v1/text-to-speech/default?output_format=pcm_16000",
        {"text": "Привет", "model_id": "eleven_flash_v2_5", "language_code": "ru"},
    )
    client.calls.clear()
    client.fail = True
    with pytest.raises(VoiceError, match="eleven_network"):
        client.synthesize("default", "Привет")
    assert sum(payload is not None for _, payload in client.calls) == 1


@pytest.mark.parametrize("voice_id", ["library", "paid", "../evil", "missing"])
def test_unavailable_voices_never_generate(voice_id: str) -> None:
    client = FixtureClient()
    with pytest.raises(VoiceError, match="eleven_voice"):
        client.synthesize(voice_id, "Привет")
    assert all(payload is None for _, payload in client.calls)


@pytest.mark.parametrize(
    "audio", [b"", b"a", b"a" * (MAX_AUDIO_BYTES + 2)], ids=["empty", "short", "oversized"]
)
def test_bad_audio_never_reaches_playback(audio: bytes) -> None:
    client = FixtureClient()
    client.audio = audio
    with pytest.raises(VoiceError, match="invalid_audio"):
        client.synthesize("default", "Привет")


@pytest.mark.asyncio
async def test_authorization_bound_and_consumed_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def exchange(request: dict[str, object], **kwargs: Any) -> dict[str, Any]:
        calls.append(request)
        raise VoiceError("eleven_network")

    monkeypatch.setattr(voice, "exchange", exchange)
    speaker = voice.ElevenLabsSpeaker("default", "a" * 64, "Привет")
    assert "Привет" not in repr(speaker)
    with pytest.raises(VoiceError, match="eleven_consent"):
        await speaker.speak("changed")
    assert not calls
    with pytest.raises(VoiceError, match="eleven_network"):
        await speaker.speak("Привет")
    with pytest.raises(VoiceError, match="eleven_consent"):
        await speaker.speak("Привет")
    assert len(calls) == 1 and speaker.authorized_text == ""


def test_fixed_endpoint_and_redirect_guard() -> None:
    from email.message import Message
    from urllib.request import Request

    client = api.Client("synthetic")
    for path in ("https://evil.invalid", "/v1/user/subscription?redirect=1", "/v1/billing"):
        with pytest.raises(VoiceError, match="eleven_network"):
            client.request(path)
    with pytest.raises(VoiceError, match="eleven_network"):
        api.NoRedirect().redirect_request(
            Request(api.BASE), None, 302, "redirect", Message(), "https://evil.invalid"
        )


def test_key_stays_in_store_and_changed_key_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        value = ""

        def set_password(self, service: str, account: str, value: str) -> None:
            assert (service, account) == (api.SERVICE, api.ACCOUNT)
            self.value = value

        def get_password(self, service: str, account: str) -> str:
            return self.value

        def delete_password(self, service: str, account: str) -> None:
            self.value = ""

    store = Store()
    monkeypatch.setattr(api, "native_store", lambda: store)
    assert api.perform({"operation": "set", "key": "synthetic"}) == {"saved": True}
    with pytest.raises(VoiceError, match="eleven_changed"):
        api.perform({"operation": "speak", "account": "wrong"})
    assert api.perform({"operation": "delete"}) == {"deleted": True}
    assert not store.value


@pytest.mark.asyncio
async def test_cancel_consumes_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(voice, "exchange", exchange)
    speaker = voice.ElevenLabsSpeaker("default", "a" * 64, "Привет")
    with pytest.raises(asyncio.CancelledError):
        await speaker.speak("Привет")
    assert speaker.used and not speaker.authorized_text
