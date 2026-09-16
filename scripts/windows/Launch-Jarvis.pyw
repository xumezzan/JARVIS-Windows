"""Stable desktop launcher: stdlib only, no dependency installation or live provider access."""

import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parent
receipt = root / ("startup-" + uuid4().hex + ".json")
try:
    active = json.loads((root / "active.json").read_text(encoding="utf-8"))
    if active["slot"] not in ("a", "b") or active["model"] != "vosk-model-small-ru-0.22":
        raise ValueError("Invalid installation pointer")
    slot = root / "slots" / active["slot"]
    env = dict(os.environ)
    data_path = env.get("JARVIS_DATA_DIR")
    if data_path and (
        not Path(data_path).is_absolute() or Path(data_path).resolve().is_relative_to(root)
    ):
        raise ValueError("Data directory must be an absolute path outside installation")
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(slot / "browsers")
    env["JARVIS_VOSK_MODEL"] = str(slot / "models" / active["model"])
    env["QT_QPA_PLATFORM"] = "windows"
    process = subprocess.Popen(
        [
            str(slot / "venv" / "Scripts" / "pythonw.exe"),
            "-I",
            "-m",
            "jarvis",
            "--startup-report",
            str(receipt),
        ],
        env=env,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    observed = False
    while time.monotonic() < deadline and process.poll() is None:
        try:
            value = json.loads(receipt.read_text(encoding="utf-8"))
            # A venv redirector starts the interpreter as its child; accept either identity.
            if isinstance(value, dict) and (
                value.get("platform") == "windows"
                and value.get("visible") is True
                and process.pid in (value.get("pid"), value.get("ppid"))
            ):
                observed = True
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    if not observed:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        raise ValueError("Window not observed")
except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
    ctypes.windll.user32.MessageBoxW(
        None,
        "Jarvis could not start. Check that JARVIS_DATA_DIR is an absolute path outside "
        "JarvisInstall. Run scripts/windows/Install-Jarvis.cmd from the repository to repair.",
        "Jarvis",
        0x10,
    )
    raise SystemExit(1) from None
finally:
    receipt.unlink(missing_ok=True)
