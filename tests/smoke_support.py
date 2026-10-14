"""Exercise the actual smoke entry point while detecting forbidden background probes."""

import sys
from unittest.mock import patch

from jarvis.app import main
from jarvis.ui.voice_worker import VoiceWorker


def isolated_smoke() -> int:
    calls: list[str] = []

    async def credential_probe(provider: str) -> bool:
        calls.append("credentials")
        return False

    def audio_worker(worker: VoiceWorker) -> None:
        # A regression records its attempt, without ever reaching audio hardware.
        calls.append("audio")

    with (
        patch("jarvis.ui.connection_probe.has_api_key", credential_probe),
        patch.object(VoiceWorker, "run", audio_worker),
    ):
        result = main(["--smoke-test"])
    if calls:
        print("Smoke attempted a personal connection or audio probe.")
        return 3
    print("isolated smoke verified")
    return result


if __name__ == "__main__":
    sys.exit(isolated_smoke())
