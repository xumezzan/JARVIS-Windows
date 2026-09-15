"""Desktop entry point with CLI metadata and an explicit bounded GUI smoke mode."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from jarvis.config import load_config


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the shell; help and version remain usable without initializing Qt."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis Desktop Preview: local demonstration, no command execution.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('jarvis-windows')}"
    )
    parser.add_argument(
        "--smoke-test", action="store_true", help="Run one GUI demo, close, and report its result."
    )
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except ValueError as error:
        print(f"Jarvis configuration error: {error}", file=sys.stderr)
        return 2

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from jarvis.observability.logging import ShellLog
    from jarvis.ui.main_window import MainWindow

    try:
        log = ShellLog(config.data_dir / "logs")
    except OSError:
        print(
            "Jarvis cannot create its local log. Check JARVIS_DATA_DIR permissions.",
            file=sys.stderr,
        )
        return 2
    application = QApplication(["jarvis"])
    application.setApplicationName("Jarvis")
    application.setOrganizationName("Jarvis")
    window = MainWindow(config, log)
    application.aboutToQuit.connect(window.shutdown)
    smoke_result = 1

    def finish_smoke(outcome: str) -> None:
        nonlocal smoke_result
        smoke_result = 0 if outcome == "success" else 1
        print(f"Jarvis GUI smoke: {outcome}")
        window.close()

    if args.smoke_test:
        window.task_finished.connect(finish_smoke)
        window.command_input.setPlainText("Проверка интерфейса Jarvis")
        QTimer.singleShot(0, window.submit)
        QTimer.singleShot(config.task_timeout_ms + 2000, window.close)
    window.show()
    try:
        result = application.exec()
    finally:
        window.shutdown()
    return smoke_result if args.smoke_test else result
