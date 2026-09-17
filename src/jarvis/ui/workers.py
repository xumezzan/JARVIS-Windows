"""A started worker outlives whoever started it.

Dropping the last reference to a running `QThread` destroys it, and Qt answers that with
`qFatal`: the process aborts, in whatever frame happened to trigger the collection rather
than anywhere near the thread. Panels do wait for their workers before they close, but a
widget can be deleted without being asked - a parent goes away, a window is torn down
while a slow read is still in flight - and then that wait never runs. So the run itself
holds the reference and gives it up only once the thread has actually finished.
"""

from time import monotonic

from PySide6.QtCore import QThread

_running: list[QThread] = []


def _finished(worker: QThread) -> bool:
    try:
        return worker.isFinished()
    except RuntimeError:
        # Deleting the C++ thread while it ran would have aborted, so this one is done.
        return True


def _release() -> None:
    _running[:] = [worker for worker in _running if not _finished(worker)]


def start(worker: QThread) -> None:
    """Start the worker and keep it reachable for the length of its run."""
    _release()
    _running.append(worker)
    worker.start()


def pending() -> int:
    """How many started workers are still running."""
    _release()
    return len(_running)


def drain(timeout_ms: int = 10000) -> bool:
    """Wait for the workers still running; the process must not leave ahead of them.

    The budget covers the whole set rather than each worker, so a stuck run delays the
    exit once instead of once per thread.
    """
    deadline = monotonic() + timeout_ms / 1000
    finished = True
    for worker in tuple(_running):
        if not _finished(worker):
            left = max(0, int((deadline - monotonic()) * 1000))
            finished = worker.wait(left) and finished
    _release()
    return finished
