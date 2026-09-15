"""Headless by default; set QT_QPA_PLATFORM=cocoa/windows for native GUI verification."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-windows",
        action="store_true",
        default=False,
        help="Run native desktop acceptance; opens apps and leaves test text in Notepad.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32" and config.getoption("--run-windows"):
        return
    skip = pytest.mark.skip(reason="Requires real Windows and explicit --run-windows opt-in.")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)
