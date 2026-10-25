"""Visible Electron QA with a synthetic clipboard; never reads the user's clipboard."""

from jarvis.desktop import service

TRACE = (
    "Traceback (most recent call last):\n"
    + '  File "example.py", line 12, in run\n' * 12
    + "ValueError: synthetic clipboard failure"
)
OriginalService = service.DesktopService


class WatcherFixtureService(OriginalService):
    clipboard_reads = 0

    async def _clipboard(self) -> str:
        self.clipboard_reads += 1
        return "initial synthetic clipboard" if self.clipboard_reads == 1 else TRACE


if __name__ == "__main__":
    service.DesktopService = WatcherFixtureService  # type: ignore[misc]
    raise SystemExit(service.main())
