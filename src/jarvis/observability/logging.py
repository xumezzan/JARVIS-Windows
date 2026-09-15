"""Local rotating JSONL shell metadata, deliberately excluding commands and exceptions."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.observability.events import ShellEvent


class ShellLog:
    """Separate from the future tool audit; accepts only finite events and generated IDs."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid4()
        self.path = directory / "shell.jsonl"
        self._handler = RotatingFileHandler(
            self.path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger = logging.Logger(f"jarvis.shell.{self.session_id}")
        self._logger.addHandler(self._handler)
        self._logger.propagate = False
        self._closed = False

    def record(self, event: ShellEvent, request_id: UUID | None = None) -> None:
        if not isinstance(event, ShellEvent):
            raise TypeError("Shell logs accept only ShellEvent values.")
        if request_id is not None and not isinstance(request_id, UUID):
            raise TypeError("Request ID must be a UUID.")
        if not self._closed:
            self._logger.info(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "session_id": str(self.session_id),
                        "request_id": str(request_id) if request_id else None,
                        "actor": "desktop_shell",
                        "event": event.value,
                        "mode": "demo",
                    }
                )
            )

    def close(self) -> None:
        if not self._closed:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._closed = True
