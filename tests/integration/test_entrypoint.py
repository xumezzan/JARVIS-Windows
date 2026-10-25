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


@pytest.mark.integration
def test_help_prints_where_the_console_cannot_encode_russian(tmp_path: Path) -> None:
    """The first Windows CI run found this: the description is Russian, the console was not.

    `jarvis --help` died with UnicodeEncodeError before printing anything, on a machine whose
    locale gives it a code page without Cyrillic. It passed everywhere it had been run until
    then, because those consoles happened to be Russian.

    Run without -I on purpose: isolated mode ignores PYTHONIOENCODING, which is why the
    application reconfigures its own streams rather than trusting the environment - and it is
    also why the narrow code page has to be asked for here in a way the child will honour.
    """
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", "--help"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONIOENCODING": "cp1252",
            "QT_QPA_PLATFORM": "offscreen",
            "JARVIS_DATA_DIR": str(tmp_path / "data"),
        },
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "jarvis" in result.stdout.lower()


def test_startup_receipt_describes_fresh_shown_window(tmp_path: Path) -> None:
    receipt = tmp_path / "Асаль startup.json"
    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "jarvis", "--smoke-test", "--startup-report", str(receipt)],
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "JARVIS_DATA_DIR": str(tmp_path / "data"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.communicate(timeout=30)
        assert process.returncode == 0
        value = json.loads(receipt.read_text())
        assert value.keys() == {"pid", "ppid", "platform", "visible"}
        assert value["platform"] == "offscreen" and value["visible"] is True
        # A venv redirector starts the interpreter as its child; accept either identity.
        assert process.pid in (value["pid"], value["ppid"])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
