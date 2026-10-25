"""Validated non-secret settings; environment files are not loaded implicitly."""

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.planner.identifiers import valid_model
from jarvis.files.policy import FilePolicy, default_roots
from jarvis.security.browser_policy import (
    DEFAULT_ORIGINS,
    BrowserMode,
    NetworkPolicy,
    validate_cdp_endpoint,
)
from jarvis.security.filesystem_policy import FilePolicy as DocumentFilePolicy
from jarvis.tools.base import ToolError


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    task_timeout_ms: int = 10000
    activity_limit: int = 200
    browser_origins: tuple[str, ...] = DEFAULT_ORIGINS
    file_roots: tuple[str, ...] = ()
    planner_model: str = ""
    # Ordinary-task model; keep main's positional constructor before optional surfaces.
    fast_model: str = "deepseek-flash"
    demo_duration_ms: int = 2400
    browser_mode: BrowserMode = BrowserMode.STRICT
    browser_cdp_endpoint: str = ""
    filesystem_roots: tuple[Path, ...] = ()

    @property
    def filesystem_policy(self) -> DocumentFilePolicy:
        from jarvis.security.filesystem_policy import filesystem_policy

        return filesystem_policy(self.data_dir, self.filesystem_roots)

    @property
    def browser_policy(self) -> NetworkPolicy:
        return NetworkPolicy(self.browser_origins, mode=self.browser_mode)

    def __post_init__(self) -> None:
        if len(self.filesystem_roots) > 8 or any(
            not path.is_absolute()
            or path == Path(path.anchor)
            or path == Path.home()
            or str(path).startswith(("\\\\", "//"))
            for path in self.filesystem_roots
        ):
            raise ValueError("Invalid filesystem project roots.")
        for name, model in (("planner", self.planner_model), ("fast", self.fast_model)):
            if model and not valid_model(model):
                raise ValueError(f"Invalid {name} model identifier.")
        try:
            _ = self.browser_policy
        except ToolError:
            raise ValueError("Invalid browser origin policy.") from None
        if self.browser_cdp_endpoint:
            validate_cdp_endpoint(self.browser_cdp_endpoint)
            if self.browser_mode is not BrowserMode.LIVE:
                raise ValueError("CDP requires Live Browser mode.")
        try:
            FilePolicy(self.file_roots)
        except ToolError:
            raise ValueError("Invalid file folder policy.") from None
        for name, value, minimum, maximum in (
            ("demo_duration_ms", self.demo_duration_ms, 100, 30000),
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

    try:
        browser_mode = BrowserMode(env.get("JARVIS_BROWSER_MODE", "strict"))
    except ValueError:
        raise ValueError("Invalid browser mode.") from None

    try:
        roots = json.loads(env.get("JARVIS_FS_PROJECT_ROOTS", "[]"))
        if not isinstance(roots, list) or any(
            not isinstance(root, str) or not root or "\x00" in root for root in roots
        ):
            raise ValueError
        filesystem_roots = tuple(Path(root) for root in roots)
    except (ValueError, TypeError):
        raise ValueError("Invalid filesystem project roots.") from None
    return AppConfig(
        data_dir=directory,
        demo_duration_ms=integer("JARVIS_DEMO_DURATION_MS", 2400),
        task_timeout_ms=integer("JARVIS_TASK_TIMEOUT_MS", 10000),
        activity_limit=integer("JARVIS_ACTIVITY_LIMIT", 200),
        planner_model=env.get("JARVIS_PLANNER_MODEL", ""),
        filesystem_roots=filesystem_roots,
        browser_mode=browser_mode,
        browser_cdp_endpoint=env.get("JARVIS_BROWSER_CDP_ENDPOINT", ""),
        fast_model=env.get("JARVIS_FAST_MODEL", "deepseek-flash"),
        browser_origins=tuple(
            part.strip()
            for part in env.get("JARVIS_BROWSER_ORIGINS", ",".join(DEFAULT_ORIGINS)).split(",")
            if part.strip()
        ),
        # Folders the assistant may touch. Unset means the user's own document folders.
        file_roots=tuple(
            part.strip()
            for part in env.get("JARVIS_FILE_ROOTS", ",".join(default_roots())).split(",")
            if part.strip()
        ),
    )
