"""Platform adapters; imported only at the execution boundary."""

import sys


def is_windows() -> bool:
    """Runtime platform check shared by portable, fixture-tested adapter entry points."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """Keep platform routing a runtime decision in portable fixture/type checks."""
    return sys.platform == "darwin"
