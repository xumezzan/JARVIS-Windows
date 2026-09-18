"""What is actually connected, asked once and off the interface thread.

The window shows a grid of services, and the whole value of that grid is that a tile says
the truth: a service with no key looks unconnected, because an assistant that appears to
reach eight things and reaches three is worse than one that reaches three and says so.

Answering it means asking the credential store, and that store is deliberately out of
process - one short-lived helper per service, which answers "1" or "0" and never a key. Five
of those in a row belong in a worker rather than between two paint events.
"""

import asyncio
from pathlib import Path

from PySide6.QtCore import QThread

from jarvis.core.composition import MCP_FILE, reviewed_servers
from jarvis.security.credentials import SERVICES, has_api_key
from jarvis.ui.voice_panel import installed_model


class ConnectionProbe(QThread):
    """One pass over the services this machine could have. It never carries a key back."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.states: dict[str, bool] = {}

    def run(self) -> None:
        found: dict[str, bool] = {}
        for provider in sorted(SERVICES):
            try:
                found[provider] = asyncio.run(has_api_key(provider))
            except Exception:
                found[provider] = False
        try:
            found["voice"] = bool(installed_model())
        except Exception:
            found["voice"] = False
        try:
            found["mcp"] = bool(reviewed_servers(self.data_dir / MCP_FILE))
        except Exception:
            found["mcp"] = False
        self.states = found
