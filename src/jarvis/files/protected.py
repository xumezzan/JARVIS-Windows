"""macOS system and secret-path exclusions shared by both file contracts."""

import os
from pathlib import Path

from jarvis.platforms import is_macos


def macos_path_denied(path: Path) -> bool:
    """A configured document root never grants system folders or local secrets."""
    if not is_macos():
        return False
    if any(_secret_component(part) for part in path.parts):
        return True
    resolved = Path(os.path.realpath(path))
    parts = tuple(part.casefold() for part in resolved.parts)
    if any(_secret_component(part) for part in parts):
        return True
    for root in (
        Path("/System"),
        Path("/Library/Keychains"),
        Path.home() / "Library/Keychains",
        Path.home() / ".ssh",
    ):
        protected = tuple(part.casefold() for part in Path(os.path.realpath(root)).parts)
        if parts[: len(protected)] == protected:
            return True
    return False


def _secret_component(part: str) -> bool:
    part = part.casefold()
    return part in {".ssh", ".env"} or part.startswith(".env.")
