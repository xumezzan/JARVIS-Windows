"""Desktop entry point with CLI metadata and an explicit bounded GUI smoke mode."""

import argparse
import os
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from jarvis.config import load_config


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the shell; help and version remain usable without initializing Qt."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis: локальный ассистент с типизированными инструментами.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('jarvis-windows')}"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run one simulated offline command, close, and report its result.",
    )
    parser.add_argument(
        "--startup-report", type=Path, help="Write a PID-bound visible-window receipt for setup."
    )
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except ValueError as error:
        print(f"Jarvis configuration error: {error}", file=sys.stderr)
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from jarvis.observability.logging import ShellLog
    from jarvis.ui import workers
    from jarvis.ui.main_window import MainWindow
    from jarvis.ui.theme import ICON

    try:
        log = ShellLog(config.data_dir / "logs")
    except OSError:
        print(
            "Jarvis cannot create its local log. Check JARVIS_DATA_DIR permissions.",
            file=sys.stderr,
        )
        return 2
    if sys.platform == "win32":
        import ctypes

        # Without its own identity Windows files the window under the interpreter that
        # started it: a Python icon in the taskbar, and nothing called Jarvis to find.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.Desktop")
    application = QApplication(["jarvis"])
    application.setApplicationName("Jarvis")
    application.setOrganizationName("Jarvis")
    application.setWindowIcon(QIcon(str(ICON)))
    window = MainWindow(config, log)
    application.aboutToQuit.connect(window.shutdown)
    smoke_result = 1
    # A real command finishes in well under a second, so the smoke run must not close the
    # window before the startup receipt has been written.
    receipt_pending = args.startup_report is not None
    smoke_done = False

    def finish_smoke(outcome: str) -> None:
        nonlocal smoke_result, smoke_done
        smoke_result = 0 if outcome in ("finished", "simulated") else 1
        verdict = "success" if smoke_result == 0 else "failed"
        print(f"Jarvis GUI smoke: {verdict} ({outcome})")
        smoke_done = True
        if not receipt_pending:
            window.close()

    if args.smoke_test:
        window.task_finished.connect(finish_smoke)
        # Simulation and the offline provider: the smoke run never touches a real adapter.
        window.run_mode.setCurrentIndex(1)
        window.provider_mode.setCurrentIndex(0)
        window.command_input.setPlainText("проверь систему")
        QTimer.singleShot(0, window.submit)
        QTimer.singleShot(config.task_timeout_ms + 2000, window.close)
    window.show()
    if args.startup_report is not None:
        from jarvis.installation.setup import atomic_json

        def report_startup() -> None:
            nonlocal receipt_pending
            try:
                atomic_json(
                    args.startup_report,
                    {
                        "pid": os.getpid(),
                        "ppid": os.getppid(),
                        "platform": application.platformName(),
                        "visible": window.isVisible(),
                    },
                )
            except OSError:
                print("Jarvis startup receipt could not be written.", file=sys.stderr)
                window.close()
                return
            receipt_pending = False
            if smoke_done:
                window.close()

        QTimer.singleShot(500, report_startup)
    try:
        result = application.exec()
    finally:
        window.shutdown()
        # A closed window is not a finished worker: a run whose panel was deleted still
        # holds its thread, and leaving before it returns would abort the process.
        workers.drain()
    return smoke_result if args.smoke_test else result
