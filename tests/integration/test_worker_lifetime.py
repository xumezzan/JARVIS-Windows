"""A started worker outlives its widget: deleting the panel must not take the process."""

import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QThread

from jarvis.ui import workers

ROOT = Path(__file__).resolve().parents[2]


class Sleeper(QThread):
    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.seconds = seconds
        self.started_running = Event()

    def run(self) -> None:
        self.started_running.set()
        time.sleep(self.seconds)


@pytest.mark.integration
def test_deleting_a_panel_mid_run_does_not_abort_the_process() -> None:
    """The failure kills the interpreter, so only a child process can be asked about it."""
    result = subprocess.run(
        [sys.executable, "-m", "tests.worker_support"],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # A fatal Qt abort leaves a non-zero code and no further output, not a traceback.
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-2:] == ["deleted", "drained"]


def test_a_running_worker_is_held_until_it_finishes() -> None:
    worker = Sleeper(0.4)
    workers.start(worker)
    assert worker.started_running.wait(5)
    assert workers.pending() == 1
    assert workers.drain()
    assert worker.isFinished()


def test_a_finished_worker_is_released() -> None:
    worker = Sleeper(0.0)
    workers.start(worker)
    assert worker.wait(5000)
    # The registry is an anti-crash measure, not a second owner: nothing accumulates.
    assert workers.pending() == 0
