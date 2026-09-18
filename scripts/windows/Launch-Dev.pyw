"""Start Jarvis from this checkout, so a restart is all it takes to see a change.

The desktop shortcut the installer makes points into `%LOCALAPPDATA%\\JarvisInstall`: a
copy of the application frozen at the moment of the last install. That is the right thing
for using Jarvis and the wrong thing for working on it - the window keeps showing the code
from before the edit, which is a confusing way to discover that nothing was wrong with the
edit.

This launcher starts the repository's own virtual environment instead. The package is
installed there in editable mode, so the window that opens is whatever the files say right
now; closing it and clicking again is the whole update cycle.

It borrows two things from a real installation when one exists beside it: the Playwright
browsers and the speech model, because they are large downloads and there is no reason for
a development run to keep a second copy. Neither is required - without them the browser
tools and the microphone say they are unavailable, and everything else works.

Standard library only: this runs before the application's dependencies are on the path.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
INTERPRETER = REPOSITORY / ".venv" / "Scripts" / "pythonw.exe"
LOG = Path(os.environ.get("TEMP", REPOSITORY)) / "jarvis-dev.log"
INSTALLATION = Path(os.environ.get("LOCALAPPDATA", "")) / "JarvisInstall"


def complain(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, "Jarvis (dev)", 0x10)


def borrowed() -> dict[str, str]:
    """The heavy assets of an installed Jarvis, if this machine has one."""
    try:
        active = json.loads((INSTALLATION / "active.json").read_text(encoding="utf-8"))
        slot = INSTALLATION / "slots" / str(active["slot"])
        if str(active["slot"]) not in ("a", "b") or not slot.is_dir():
            return {}
        found = {"PLAYWRIGHT_BROWSERS_PATH": str(slot / "browsers")}
        model = slot / "models" / str(active["model"])
        if model.is_dir():
            found["JARVIS_VOSK_MODEL"] = str(model)
        return found
    except (OSError, ValueError, KeyError, TypeError):
        # A development run without the extras is a development run, not a failure.
        return {}


def main() -> int:
    if not INTERPRETER.exists():
        complain(
            "No virtual environment in this checkout.\n\n"
            "Open PowerShell in " + str(REPOSITORY) + " and run:\n"
            "  py -3.12 -m venv .venv\n"
            '  .\\.venv\\Scripts\\python -m pip install -e ".[dev]"'
        )
        return 1
    environment = {**os.environ, **borrowed(), "QT_QPA_PLATFORM": "windows"}
    # pythonw has nowhere to print, so a start that fails would fail silently. The output
    # goes to a file the message below can point at.
    with LOG.open("w", encoding="utf-8", errors="replace") as output:
        process = subprocess.Popen(
            [str(INTERPRETER), "-m", "jarvis"],
            env=environment,
            cwd=str(REPOSITORY),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        # Long enough to catch a start that breaks on an import or a syntax error, short
        # enough that a window which is merely slow is left alone to appear.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.2)
    if process.poll() not in (None, 0):
        complain(
            "Jarvis stopped right after starting (exit "
            + str(process.returncode)
            + ").\n\nWhat it printed is in:\n"
            + str(LOG)
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
