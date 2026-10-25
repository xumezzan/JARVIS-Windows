"""Bounded JSON reads and atomic replacement; no profile contents in error messages."""

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from jarvis.workspace.models import WorkspaceProfile

MAX_PROFILE_BYTES = 262_144


class WorkspaceFailure(ValueError):
    def __init__(self, code: Literal["storage", "invalid", "limit"]):
        self.code = code
        super().__init__("workspace_" + code)


class WorkspaceStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "workspace_profile.json"

    def load(self) -> WorkspaceProfile:
        try:
            if self.path.is_symlink():
                raise WorkspaceFailure("storage")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            try:
                fd = os.open(self.path, flags)
            except FileNotFoundError:
                return WorkspaceProfile()
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise WorkspaceFailure("storage")
                if info.st_size > MAX_PROFILE_BYTES:
                    raise WorkspaceFailure("limit")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_PROFILE_BYTES + 1)
            finally:
                os.close(fd)
            if len(raw) > MAX_PROFILE_BYTES:
                raise WorkspaceFailure("limit")
            return WorkspaceProfile.model_validate_json(raw)
        except ValidationError:
            raise WorkspaceFailure("invalid") from None
        except OSError:
            raise WorkspaceFailure("storage") from None

    def save(self, profile: WorkspaceProfile) -> None:
        try:
            checked = WorkspaceProfile.model_validate(profile)
            raw = checked.model_dump_json().encode("utf-8")
        except (ValidationError, ValueError):
            raise WorkspaceFailure("invalid") from None
        if len(raw) > MAX_PROFILE_BYTES:
            raise WorkspaceFailure("limit")
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
                raise WorkspaceFailure("storage")
            fd, name = tempfile.mkstemp(prefix=".workspace-", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise WorkspaceFailure("storage") from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
