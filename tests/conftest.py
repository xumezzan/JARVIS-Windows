"""Headless by default; set QT_QPA_PLATFORM=cocoa/windows for native GUI verification."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-voice",
        action="store_true",
        default=False,
        help="Use the microphone while Space is held in a visible window; speaks a short result.",
    )
    parser.addoption(
        "--run-model",
        action="store_true",
        default=False,
        help="Send a small command to OpenAI using the OS credential store; incurs API usage.",
    )
    parser.addoption(
        "--run-windows",
        action="store_true",
        default=False,
        help="Run native desktop acceptance; opens apps and leaves test text in Notepad.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason="Requires real Windows and explicit --run-windows opt-in.")
    for item in items:
        if "voice" in item.keywords and not config.getoption("--run-voice"):
            item.add_marker(
                pytest.mark.skip(reason="Requires explicit --run-voice and local model.")
            )
        if "windows" in item.keywords and not (
            sys.platform == "win32" and config.getoption("--run-windows")
        ):
            item.add_marker(skip)
        if "model" in item.keywords and not config.getoption("--run-model"):
            item.add_marker(
                pytest.mark.skip(reason="Requires explicit --run-model and OS key setup.")
            )
