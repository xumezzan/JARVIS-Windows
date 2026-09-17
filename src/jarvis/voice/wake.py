"""Deciding that a phrase is addressed here, without hearing the rest of the room.

Standing capture used to record whatever was said, transcribe all of it and only then ask
whether the assistant had been named. Locally, but still: every word spoken near the
machine went through a recogniser. This layer asks the narrower question first. The
listener runs on a grammar of exactly two outcomes - the name, or something that is not
the name - so it cannot produce a transcript of anything, and the ordinary recorder starts
only once the name has been heard.

Audio stays inside the killable helper process as a bounded ring of recent blocks: never a
file, never a return value. What crosses back is one boolean. The wait is bounded like
every other operation here, so a standing listener is a sequence of short cycles the owner
can end at any moment, not a process that lives forever.

A false wake is survivable by design rather than by accuracy: it opens a recording, and
whatever is heard goes through the same planner, the same permission engine and the same
confirmations. It can start something reversible; it cannot pay or delete, because those
still ask for a spoken control detail.
"""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Protocol

from jarvis.voice.local import exchange

# One cycle in the helper, plus room for the process to answer before the caller gives up.
WAKE_SECONDS = 120
WAKE_TIMEOUT = WAKE_SECONDS + 15


class Wake(Protocol):
    """Never transcribes. Answers one question: was the assistant addressed just now?"""

    local_only: bool

    async def listen(self, released: Event, ready: Callable[[], None]) -> bool: ...


class LocalWake:
    local_only = True

    def __init__(self, model: Path) -> None:
        self.model = model

    async def listen(self, released: Event, ready: Callable[[], None]) -> bool:
        data = await exchange(
            {"operation": "wake", "model": str(self.model)},
            seconds=WAKE_TIMEOUT,
            released=released,
            ready=ready,
        )
        return bool(data.get("heard"))
