"""Replaceable local adapters using bounded, killable native helper pipes."""

import asyncio
import base64
import json
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event
from typing import Any

from jarvis.voice.contracts import MAX_AUDIO_BYTES, AudioClip, Transcript, VoiceError

PIPE_LIMIT = MAX_AUDIO_BYTES * 2 + 16384


async def exchange(
    request: dict[str, object],
    *,
    seconds: float,
    released: Event | None = None,
    ready: Callable[[], None] | None = None,
) -> dict[str, Any]:
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "jarvis.platforms.audio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=PIPE_LIMIT,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    )
    process: asyncio.subprocess.Process | None = None
    release_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(seconds):
            process = await asyncio.shield(launch)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request).encode() + b"\n")
            await process.stdin.drain()

            async def release() -> None:
                assert released is not None and process is not None and process.stdin is not None
                while not released.is_set():
                    await asyncio.sleep(0.02)
                process.stdin.write(b"stop\n")
                await process.stdin.drain()

            if released is not None:
                release_task = asyncio.create_task(release())
            raw = await process.stdout.readuntil(b"\n")
            if raw == b'{"ready":true}\n':
                if ready is not None:
                    ready()
                raw = await process.stdout.readuntil(b"\n")
            response = json.loads(raw)
            await process.wait()
            if not isinstance(response, dict):
                raise VoiceError("voice_failed")
            if "error" in response:
                raise VoiceError(str(response["error"]))
            if process.returncode != 0:
                raise VoiceError("voice_failed")
            return response
    except TimeoutError:
        raise VoiceError("timeout") from None
    except (VoiceError, asyncio.CancelledError):
        raise
    except Exception:
        raise VoiceError("voice_failed") from None
    finally:
        if release_task is not None:
            release_task.cancel()
            with suppress(asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
                await release_task
        if process is None:
            process = await launch
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        # Drain at most the bounded stream buffer; never communicate() on arbitrary output.
        if process.stdout is not None:
            while await process.stdout.read(65536):
                pass
        await process.wait()


class LocalRecorder:
    async def record(self, released: Event, ready: Callable[[], None]) -> AudioClip:
        if released.is_set():
            raise asyncio.CancelledError
        data = await exchange({"operation": "record"}, seconds=35, released=released, ready=ready)
        try:
            return AudioClip(base64.b64decode(data["pcm"], validate=True))
        except Exception:
            raise VoiceError("invalid_audio") from None


class VoskRecognizer:
    local_only = True

    def __init__(self, model: Path) -> None:
        self.model = model

    async def transcribe(self, clip: AudioClip) -> Transcript:
        data = await exchange(
            {
                "operation": "transcribe",
                "model": str(self.model),
                "pcm": base64.b64encode(clip.pcm).decode("ascii"),
            },
            seconds=45,
        )
        try:
            return Transcript(data["text"], float(data["confidence"]))
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("recognition") from None


class LocalSpeaker:
    local_only = True

    async def speak(self, text: str) -> None:
        if not 1 <= len(text) <= 500:
            raise VoiceError("speech")
        await exchange({"operation": "speak", "text": text}, seconds=25)
