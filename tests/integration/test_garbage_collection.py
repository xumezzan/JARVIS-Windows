"""Worker allocations cannot choose the destruction thread of a cyclic QWidget."""

import gc
import weakref
from collections.abc import Iterator
from threading import Thread, get_ident

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from jarvis.ui import garbage_collection, workers


@pytest.fixture
def collector(qtbot: QtBot) -> Iterator[garbage_collection.GuiCollector]:
    assert workers.drain()
    garbage_collection.stop()
    before = gc.isenabled()
    gc.enable()
    guard = garbage_collection.install()
    yield guard
    assert workers.drain()
    garbage_collection.stop()
    if not before:
        gc.disable()


def test_worker_allocations_leave_widget_collection_to_gui_timer(
    qtbot: QtBot, collector: garbage_collection.GuiCollector
) -> None:
    destroyed_on: list[int] = []
    main_thread = get_ident()
    widget = QWidget()
    widget.cycle = widget  # type: ignore[attr-defined]
    widget.destroyed.connect(lambda: destroyed_on.append(get_ident()))
    reference = weakref.ref(widget)
    del widget

    def allocate_cycles() -> None:
        for _ in range(10000):
            cycle: list[object] = []
            cycle.append(cycle)

    worker = Thread(target=allocate_cycles)
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert reference() is not None and destroyed_on == []
    qtbot.waitUntil(lambda: reference() is None, timeout=5000)
    assert destroyed_on == [main_thread]


def test_explicit_collection_from_a_worker_is_refused(
    collector: garbage_collection.GuiCollector,
) -> None:
    errors: list[str] = []

    def wrong_thread() -> None:
        try:
            collector.collect()
        except RuntimeError as error:
            errors.append(str(error))

    worker = Thread(target=wrong_thread)
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert errors == ["Qt reference cycles must be collected on the GUI thread."]


@pytest.mark.parametrize("was_enabled", [True, False])
def test_stop_restores_previous_gc_setting(qtbot: QtBot, was_enabled: bool) -> None:
    assert workers.drain()
    garbage_collection.stop()
    before = gc.isenabled()
    try:
        if was_enabled:
            gc.enable()
        else:
            gc.disable()
        first = garbage_collection.install()
        assert garbage_collection.install() is first
        assert not gc.isenabled()
        garbage_collection.stop()
        assert gc.isenabled() is was_enabled
    finally:
        garbage_collection.stop()
        if before:
            gc.enable()
        else:
            gc.disable()
