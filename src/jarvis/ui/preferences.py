"""The two things this window remembers about its owner between launches.

An assistant that greets you by name has to have been told the name, and there is nowhere
in this application that already knows it: the memory profile stores short labels about
work, not an identity, and a Microsoft address is an address rather than a person. So the
name is typed once in the settings section and kept here, in the owner's own data folder,
next to `cloud-consent.json` and under the same rules: a small JSON file, read defensively,
never a place for anything secret.

The theme lives here for the same reason - it is a preference, and asking for it again at
every launch is how a setting becomes a chore.
"""

import json
import unicodedata
from pathlib import Path

MAX_BYTES = 4096
MAX_NAME = 40
# Deliberately narrow: a name, not a note. Nothing here ever becomes a path, an argument
# or a command line, and the same rule that guards the memory labels guards this field.
ALLOWED = " -.'’"


def valid_name(value: str) -> bool:
    text = unicodedata.normalize("NFKC", value).strip()
    return bool(text) and len(text) <= MAX_NAME and all(c.isalpha() or c in ALLOWED for c in text)


class HomePreferences:
    """Owner name and theme, remembered locally and forgiving of a broken file."""

    def __init__(self, path: Path, themes: tuple[str, ...]) -> None:
        self.path = path
        self.themes = themes
        self.name = ""
        self.theme = themes[0]
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
            name = data.get("name")
            if isinstance(name, str) and valid_name(name):
                self.name = unicodedata.normalize("NFKC", name).strip()
            theme = data.get("theme")
            if isinstance(theme, str) and theme in self.themes:
                self.theme = theme
        except (OSError, ValueError, TypeError):
            # A settings file that cannot be read is a greeting without a name, never a
            # failure to start: the window matters more than the preference.
            self.name = ""
            self.theme = self.themes[0]

    def remember(self, name: str, theme: str) -> None:
        self.name = unicodedata.normalize("NFKC", name).strip() if valid_name(name) else ""
        self.theme = theme if theme in self.themes else self.themes[0]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"name": self.name, "theme": self.theme}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return
