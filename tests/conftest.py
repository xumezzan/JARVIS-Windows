"""Headless by default; set QT_QPA_PLATFORM=cocoa/windows for native GUI verification."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
