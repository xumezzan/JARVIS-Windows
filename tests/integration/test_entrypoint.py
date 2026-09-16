"""Exercise the installed module in a fresh interpreter outside the source tree."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.parametrize("arguments", [["--smoke-test"], ["--help"], ["--version"]])
def test_installed_module_entrypoint(tmp_path: Path, arguments: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "jarvis", *arguments],
        cwd=tmp_path,
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "JARVIS_DATA_DIR": str(tmp_path / "data"),
            "JARVIS_DEMO_DURATION_MS": "100",
            "JARVIS_TASK_TIMEOUT_MS": "1000",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "jarvis" in result.stdout.lower()
    assert "Traceback" not in result.stderr
    if arguments == ["--smoke-test"]:
        assert "GUI smoke: success" in result.stdout


def test_startup_receipt_describes_fresh_shown_window(tmp_path: Path) -> None:
    receipt = tmp_path / "Асаль startup.json"
    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "jarvis", "--smoke-test", "--startup-report", str(receipt)],
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "JARVIS_DATA_DIR": str(tmp_path / "data"),
            "JARVIS_DEMO_DURATION_MS": "1000",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.communicate(timeout=30)
        assert process.returncode == 0
        assert json.loads(receipt.read_text()) == {
            "pid": process.pid,
            "ppid": os.getpid(),
            "platform": "offscreen",
            "visible": True,
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
