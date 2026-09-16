"""Validated non-secret settings; environment files are not loaded implicitly."""

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jarvis.security.browser_policy import DEFAULT_ORIGINS, NetworkPolicy
from jarvis.tools.base import ToolError


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    task_timeout_ms: int = 10000
    activity_limit: int = 200
    browser_origins: tuple[str, ...] = DEFAULT_ORIGINS
    planner_model: str = ""

    def __post_init__(self) -> None:
        if self.planner_model and not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,99}", self.planner_model
        ):
            raise ValueError("Invalid planner model identifier.")
        try:
            NetworkPolicy(self.browser_origins)
        except ToolError:
            raise ValueError("Invalid browser origin policy.") from None
        for name, value, minimum, maximum in (
            ("task_timeout_ms", self.task_timeout_ms, 100, 60000),
            ("activity_limit", self.activity_limit, 10, 1000),
        ):
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}.")


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Read only explicitly supported settings; do not echo invalid values."""
    env = os.environ if environ is None else environ
    if sys.platform == "win32":
        base = Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    directory = Path(env.get("JARVIS_DATA_DIR", str(base / "Jarvis"))).expanduser()

    def integer(key: str, default: int) -> int:
        try:
            return int(env.get(key, str(default)))
        except ValueError:
            raise ValueError(f"{key} must be an integer.") from None

    return AppConfig(
        data_dir=directory,
        task_timeout_ms=integer("JARVIS_TASK_TIMEOUT_MS", 10000),
        activity_limit=integer("JARVIS_ACTIVITY_LIMIT", 200),
        planner_model=env.get("JARVIS_PLANNER_MODEL", ""),
        browser_origins=tuple(
            part.strip()
            for part in env.get("JARVIS_BROWSER_ORIGINS", ",".join(DEFAULT_ORIGINS)).split(",")
            if part.strip()
        ),
    )
