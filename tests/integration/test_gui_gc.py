"""Qt wrapper cycles must be destroyed by the GUI thread, after worker drain."""

import gc
from threading import Thread, get_ident

from PySide6.QtWidgets import QApplication, QWidget

from jarvis.ui.garbage_collection import GuiGarbageCollector


def test_qt_cycles_are_collected_on_the_gui_thread(qapp: QApplication) -> None:
    collector = GuiGarbageCollector(qapp)
    destroyed: list[int] = []
    window = QWidget()
    window.destroyed.connect(lambda: destroyed.append(get_ident()))
    window.cycle = window  # type: ignore[attr-defined]
    del window
    assert not gc.isenabled() and not destroyed
    collector.collect()
    assert destroyed == [get_ident()]
    collector.close()


def test_worker_cannot_use_gui_collector(qapp: QApplication) -> None:
    collector = GuiGarbageCollector(qapp)
    errors: list[str] = []

    def attempt() -> None:
        try:
            collector.collect()
        except RuntimeError:
            errors.append("wrong_thread")

    worker = Thread(target=attempt)
    worker.start()
    worker.join()
    assert errors == ["wrong_thread"]
    collector.close()
