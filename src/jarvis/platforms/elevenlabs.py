"""Killable credential/network/playback helper. Fixed endpoints; no cloud microphone input."""

import hashlib
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from email.message import Message
from typing import Any, NoReturn

from jarvis.platforms.credentials import native_store
from jarvis.platforms.protocol import emit
from jarvis.security.credentials import valid_key
from jarvis.voice.contracts import MAX_AUDIO_BYTES, SAMPLE_RATE, VoiceError

SERVICE = "Jarvis/ElevenLabs"
ACCOUNT = "default"
BASE = "https://api.elevenlabs.io"
MODEL = "eleven_flash_v2_5"
VOICE_PATH = "/v2/voices?page_size=100&category=premade"


def free_remaining(data: dict[str, Any], required: int = 0) -> int:
    """Deny unknown/paid/overage-enabled accounts. No billing-changing endpoint exists here."""
    if (
        data.get("tier") != "free"
        or data.get("can_extend_character_limit") is not False
        or data.get("allowed_to_extend_character_limit") is not False
        or type(data.get("max_credit_limit_extension")) is not int
        or data["max_credit_limit_extension"] != 0
    ):
        raise VoiceError("eleven_free")
    used, limit = data.get("character_count"), data.get("character_limit")
    if type(used) is not int or type(limit) is not int or not 0 <= used <= limit <= 1_000_000:
        raise VoiceError("eleven_free")
    remaining = limit - used
    # Reserve at least one credit per character, even though Flash may cost less.
    if required > remaining:
        raise VoiceError("eleven_quota")
    return remaining


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> NoReturn:
        raise VoiceError("eleven_network")


class Client:
    def __init__(self, key: str) -> None:
        if not valid_key(key):
            raise VoiceError("eleven_credentials")
        self._key = key

    def request(self, path: str, payload: dict[str, Any] | None = None) -> bytes:
        allowed_get = path in {"/v1/user/subscription", VOICE_PATH}
        allowed_post = re.fullmatch(
            r"/v1/text-to-speech/[A-Za-z0-9_-]{1,64}\?output_format=pcm_16000", path
        )
        if (payload is None and not allowed_get) or (payload is not None and not allowed_post):
            raise VoiceError("eleven_network")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        if not context.get_ca_certs():
            import certifi

            context.load_verify_locations(certifi.where())
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )
        request = urllib.request.Request(
            BASE + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "xi-api-key": self._key,
                "Content-Type": "application/json",
                "Accept": "application/json" if payload is None else "audio/pcm",
            },
            method="GET" if payload is None else "POST",
        )
        try:
            with opener.open(request, timeout=10) as response:
                limit = 1_000_000 if payload is None else MAX_AUDIO_BYTES
                raw = response.read(limit + 1)
                if response.status != 200 or not raw or len(raw) > limit:
                    raise VoiceError("eleven_network")
                if payload is not None:
                    content_type = response.headers.get_content_type()
                    if content_type not in {"audio/pcm", "audio/x-pcm", "application/octet-stream"}:
                        raise VoiceError("invalid_audio")
                return bytes(raw)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in {401, 403}:
                raise VoiceError("eleven_credentials") from None
            if code in {402, 429}:
                raise VoiceError("eleven_quota") from None
            raise VoiceError("eleven_network") from None
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("eleven_network") from None

    def data(self, path: str) -> dict[str, Any]:
        try:
            value = json.loads(self.request(path))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("eleven_network") from None

    def voices(self) -> list[dict[str, str]]:
        records = self.data(VOICE_PATH).get("voices")
        if not isinstance(records, list) or len(records) > 100:
            raise VoiceError("eleven_voice")
        result = []
        for voice in records:
            if not isinstance(voice, dict) or voice.get("category") != "premade":
                continue
            tiers = voice.get("available_for_tiers")
            if tiers and (not isinstance(tiers, list) or "free" not in tiers):
                continue
            voice_id, name = voice.get("voice_id"), voice.get("name")
            if (
                isinstance(voice_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", voice_id)
                and isinstance(name, str)
                and 1 <= len(name) <= 80
                and all(ord(c) >= 32 for c in name)
            ):
                result.append({"id": voice_id, "name": name})
        if not result:
            raise VoiceError("eleven_voice")
        return result

    def synthesize(self, voice_id: str, text: str) -> bytes:
        if not 1 <= len(text) <= 500:
            raise VoiceError("speech")
        if voice_id not in {voice["id"] for voice in self.voices()}:
            raise VoiceError("eleven_voice")
        # Fresh check immediately before the only effectful call. No automatic retries.
        free_remaining(self.data("/v1/user/subscription"), len(text))
        raw = self.request(
            f"/v1/text-to-speech/{voice_id}?output_format=pcm_16000",
            {"text": text, "model_id": MODEL, "language_code": "ru"},
        )
        if not raw or len(raw) % 2 or len(raw) > MAX_AUDIO_BYTES:
            raise VoiceError("invalid_audio")
        return raw


def playback(pcm: bytes) -> None:
    import sounddevice as sd  # type: ignore[import-untyped]

    # Local output only, bounded to 30 seconds; helper termination releases the device.
    with sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as output:
        for offset in range(0, len(pcm), 3200):
            if output.write(pcm[offset : offset + 3200]):
                raise VoiceError("speech")


def perform(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation not in {"set", "delete", "voices", "speak"}:
        raise VoiceError("voice_failed")
    store = native_store()
    if operation == "set":
        key = request.get("key")
        if not isinstance(key, str) or not valid_key(key):
            raise VoiceError("eleven_credentials")
        store.set_password(SERVICE, ACCOUNT, key)
        return {"saved": True}
    if operation == "delete":
        if store.get_password(SERVICE, ACCOUNT) is not None:
            store.delete_password(SERVICE, ACCOUNT)
        return {"deleted": True}
    key = store.get_password(SERVICE, ACCOUNT) or ""
    client = Client(key)
    account = hashlib.sha256(key.encode()).hexdigest()
    if operation == "voices":
        remaining = free_remaining(client.data("/v1/user/subscription"))
        return {"voices": client.voices(), "remaining": remaining, "account": account}
    if request.get("account") != account:
        raise VoiceError("eleven_changed")
    voice_id, text = request.get("voice_id"), request.get("text")
    if not isinstance(voice_id, str) or not isinstance(text, str):
        raise VoiceError("speech")
    pcm = client.synthesize(voice_id, text)
    try:
        playback(pcm)
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("speech") from None
    return {"spoken": True}


def main() -> int:
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 2
    try:
        raw = sys.stdin.buffer.readline(8193)
        if len(raw) > 8192 or not raw.endswith(b"\n"):
            raise VoiceError("voice_failed")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise VoiceError("voice_failed")
        emit(json.dumps(perform(request), ensure_ascii=True))
        return 0
    except Exception as error:
        code = error.code if isinstance(error, VoiceError) else "eleven_credentials"
        emit(json.dumps({"error": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
