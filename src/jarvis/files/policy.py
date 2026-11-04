"""Where the assistant may look and write.

Pure and synchronous, like the browser's origin policy, so the same decision is made in
simulation and in execution. A path is accepted only after it has been resolved, so a link
or a "..." segment cannot lead outside the folders the user allowed.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from jarvis.files.protected import macos_path_denied
from jarvis.tools.base import ToolError

# Folders a person keeps their own documents in. The installation, the repository and the
# system directories are deliberately not here.
USER_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
MACOS_EXECUTABLE = frozenset(
    {
        ".app",
        ".command",
        ".sh",
        ".bash",
        ".zsh",
        ".py",
        ".scpt",
        ".scptd",
        ".workflow",
        ".dylib",
        ".so",
    }
)

# Extensions that run code when opened or that Windows treats as executable. They are never
# written, renamed into, or opened, whatever the command says.
EXECUTABLE = frozenset(
    {
        ".ade",
        ".adp",
        ".bat",
        ".chm",
        ".cmd",
        ".com",
        ".cpl",
        ".dll",
        ".exe",
        ".hta",
        ".inf",
        ".ins",
        ".jar",
        ".js",
        ".jse",
        ".lnk",
        ".msc",
        ".msi",
        ".msp",
        ".pif",
        ".ps1",
        ".psm1",
        ".reg",
        ".scf",
        ".scr",
        ".sct",
        ".sys",
        ".url",
        ".vb",
        ".vbe",
        ".vbs",
        ".wsf",
        ".wsh",
    }
)

# Files whose whole content is plain text, so reading one cannot smuggle in a payload and
# writing one cannot produce a document format by accident.
TEXT = frozenset(
    {
        ".cfg",
        ".conf",
        ".csv",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".rst",
        ".srt",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

MAX_TEXT_BYTES = 400_000


def default_roots() -> tuple[str, ...]:
    """The user's own folders that actually exist on this machine."""
    home = Path.home()
    names = ("Desktop", "Documents", "Downloads") if sys.platform == "darwin" else USER_FOLDERS
    return tuple(str(home / name) for name in names if (home / name).is_dir())


@dataclass(frozen=True)
class FilePolicy:
    """An explicit allowlist of folders. An empty list denies every path."""

    roots: tuple[Path, ...]
    denied_roots: tuple[Path, ...]

    def __init__(
        self, roots: tuple[str, ...] | None = None, *, denied_roots: tuple[str, ...] = ()
    ) -> None:
        chosen = default_roots() if roots is None else roots
        resolved: list[Path] = []
        for raw in chosen:
            if not raw or len(raw) > 4096:
                raise ToolError("path_denied")
            candidate = Path(raw)
            if not candidate.is_absolute():
                raise ToolError("path_denied")
            resolved.append(self.normalise(candidate))
        object.__setattr__(self, "roots", tuple(dict.fromkeys(resolved)))
        object.__setattr__(
            self, "denied_roots", tuple(self.normalise(Path(raw)) for raw in denied_roots)
        )

    @staticmethod
    def normalise(path: Path) -> Path:
        try:
            return Path(os.path.realpath(str(path)))
        except OSError:
            raise ToolError("path_denied") from None

    def inside(self, path: Path) -> bool:
        return (
            not macos_path_denied(path)
            and not any(path == root or path.is_relative_to(root) for root in self.denied_roots)
            and any(path == root or path.is_relative_to(root) for root in self.roots)
        )

    def check(self, raw: str) -> Path:
        """Resolve one caller-supplied path and prove it stays inside an allowed folder."""
        if not raw or len(raw) > 4096 or "\x00" in raw:
            raise ToolError("path_denied")
        candidate = Path(raw)
        if (
            not candidate.is_absolute()
            or candidate.drive.startswith("\\\\")
            or macos_path_denied(candidate)
        ):
            raise ToolError("path_denied")
        resolved = self.normalise(candidate)
        if not self.inside(resolved):
            raise ToolError("path_denied")
        return resolved

    def writable(self, path: Path) -> Path:
        """A path that may be created, replaced, renamed or removed."""
        if path.suffix.lower() in EXECUTABLE or (
            sys.platform == "darwin" and path.suffix.lower() in MACOS_EXECUTABLE
        ):
            raise ToolError("file_unsupported")
        return path

    def readable_text(self, path: Path) -> Path:
        if path.suffix.lower() not in TEXT:
            raise ToolError("file_unsupported")
        return path

    def openable(self, path: Path) -> Path:
        """A document may be handed to its own application; a program may not."""
        if (
            path.suffix.lower() in EXECUTABLE
            or not path.suffix
            or (sys.platform == "darwin" and path.suffix.lower() in MACOS_EXECUTABLE)
        ):
            raise ToolError("file_unsupported")
        return path
