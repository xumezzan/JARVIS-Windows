"""A started worker outlives its widget: deleting the panel must not take the process."""

import os
import subprocess
import sys
import time
import weakref
from collections.abc import Iterator
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QThread
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from jarvis.ui import workers
from jarvis.ui.connection_probe import ConnectionProbe

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def finish_started_workers() -> Iterator[None]:
    yield
    # The real entry point drains after its event loop; this module exits directly.
    assert workers.drain(), "An owned Qt worker did not finish before test teardown."


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


def test_a_running_worker_is_held_until_it_finishes(qtbot: QtBot) -> None:
    worker = Sleeper(0.4)
    workers.start(worker)
    assert worker.started_running.wait(5)
    assert workers.pending() == 1
    assert workers.drain()
    assert worker.isFinished()


def test_a_finished_worker_is_released(qtbot: QtBot) -> None:
    worker = Sleeper(0.0)
    workers.start(worker)
    assert worker.wait(5000)
    # The registry is an anti-crash measure, not a second owner: nothing accumulates.
    assert workers.pending() == 0


def test_finished_cpp_worker_is_deleted_on_the_gui_thread(qtbot: QtBot) -> None:
    destroyed_on: list[int] = []
    main_thread = get_ident()
    worker = Sleeper(0.0)
    worker.destroyed.connect(lambda: destroyed_on.append(get_ident()))
    workers.start(worker)
    assert worker.wait(5000)
    qtbot.waitUntil(lambda: not isValid(worker))
    assert destroyed_on == [main_thread]
    assert workers.pending() == 0


def test_completed_worker_survives_until_deferred_cpp_deletion(qtbot: QtBot) -> None:
    worker = Sleeper(0.0)
    reference = weakref.ref(worker)
    workers.start(worker)
    assert worker.wait(5000)
    del worker
    # No GUI event has delivered finished/deleteLater yet. Do not hand ownership to GC.
    assert reference() is not None
    assert workers.pending() == 0
    qtbot.waitUntil(lambda: reference() is None)


def test_connection_probe_stops_after_current_bounded_lookup(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = Event(), Event()
    calls: list[str] = []

    async def lookup(provider: str) -> bool:
        calls.append(provider)
        started.set()
        assert release.wait(5)
        return False

    monkeypatch.setattr("jarvis.ui.connection_probe.has_api_key", lookup)
    probe = ConnectionProbe(tmp_path)
    workers.start(probe)
    assert started.wait(5)
    probe.requestInterruption()
    release.set()
    assert probe.wait(5000)
    assert len(calls) == 1
    assert probe.states == {calls[0]: False}
