"""Real desktop/IPC with synthetic Graph, credentials and audio; never used by the product."""

from collections.abc import Callable
from pathlib import Path

from jarvis.config import AppConfig
from jarvis.desktop import service
from jarvis.desktop.voice import DesktopVoice
from jarvis.voice.contracts import AudioClip, Transcript
from tests.desktop_runtime_fixture import FixtureProvider, FixtureVoice
from tests.mail_support import FakeCredentials, FakeGraph


class Audio(FixtureVoice):
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def transcribe(self, clip: AudioClip) -> Transcript:
        return Transcript(
            "стоп" if (self.directory / "voice-stop").exists() else "подтвердить", 0.99
        )


class FixtureService(service.DesktopService):
    def __init__(self, config: AppConfig, write: Callable[[dict[str, object]], None]) -> None:
        super().__init__(config, write, provider=FixtureProvider())
        self.mail_session.credentials = FakeCredentials()
        self.mail_session.graph = FakeGraph()
        audio = Audio(config.data_dir)
        self.voice = DesktopVoice(
            self.publish,
            self.transcript,
            self._voice_start,
            self.cancel,
            recorder=audio,
            recognizer=audio,
            speaker=audio,
        )


if __name__ == "__main__":
    service.DesktopService = FixtureService  # type: ignore[misc, assignment]
    raise SystemExit(service.main())
