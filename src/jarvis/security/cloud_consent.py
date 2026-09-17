"""What the owner has already agreed to send to the cloud, and which model receives it.

The answer used to live only in the open window, so every launch asked again — and an
answer that has to be repeated is one the owner stops reading. It is kept here instead,
beside the rest of the session's data, and holds until it is taken back in the planner.

Consent is stored as a plain flag next to the identifier it was given for, because consent
to send a command somewhere is consent to a named recipient. Anything else in the file is
read as no consent: a missing, damaged or oversized file asks the question again rather
than letting a guess put the owner's text on the wire. A file that cannot be written has
the same effect on the next launch, which is why the failure is silent here — the worst it
costs is the question, and the session it was given in still honours it.
"""

import json
from pathlib import Path

from jarvis.core.planner.identifiers import valid_model

MAX_BYTES = 4096


class CloudConsent:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.granted = False
        self.model = ""
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists() or self.path.is_symlink():
                return
            if self.path.stat().st_size > MAX_BYTES:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            model = data.get("model")
            if isinstance(model, str) and valid_model(model):
                self.model = model
            self.granted = data.get("granted") is True and bool(self.model)
        except (OSError, ValueError, TypeError):
            self.granted = False
            self.model = ""

    def remember(self, model: str, granted: bool) -> None:
        """Consent without an identifier is consent to nothing, so it is not recorded as one."""
        self.model = model if valid_model(model) else ""
        self.granted = bool(granted) and bool(self.model)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"granted": self.granted, "model": self.model}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return
