"""Collect Python reference cycles only on the thread that owns the Qt interface.

Python's automatic cycle collector runs in whichever thread crosses its allocation
threshold. A cycle can own a QWidget, whose C++ destructor must run on the GUI thread.
Keep ordinary reference counting, but schedule cycle collection through a GUI QTimer
while the application is alive. This also applies to embedded workbenches started
without the main entry point. Explicit gc.collect() elsewhere must obey the same rule.
"""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Slot

_collector: "GuiCollector | None" = None


class GuiCollector(QObject):
    def __init__(self, application: QCoreApplication) -> None:
        super().__init__(application)
        self.was_enabled = gc.isenabled()
        self.restored = False
        gc.disable()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.collect)
        self.timer.start()

    @Slot()
    def collect(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Qt reference cycles must be collected on the GUI thread.")
        gc.collect()

    def restore(self) -> None:
        # No Qt access: application.destroyed can call this during C++ teardown.
        if not self.restored:
            self.restored = True
            if self.was_enabled:
                gc.enable()


def _restore() -> None:
    global _collector
    collector, _collector = _collector, None
    if collector is not None:
        collector.restore()


def install() -> GuiCollector:
    """Install once, before starting any worker, on the application's owning thread."""
    global _collector
    application = QCoreApplication.instance()
    if application is None or QThread.currentThread() != application.thread():
        raise RuntimeError("GUI collection requires an application and its owning thread.")
    if _collector is None:
        _collector = GuiCollector(application)
        application.destroyed.connect(_restore)
    return _collector


def stop() -> None:
    """Restore the previous GC setting after all workers have been joined."""
    collector = _collector
    if collector is not None:
        if QThread.currentThread() != collector.thread():
            raise RuntimeError("GUI collection must stop on its owning thread.")
        collector.timer.stop()
        collector.collect()
        _restore()
        collector.deleteLater()
