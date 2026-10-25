"""Platform adapters; imported only at the execution boundary."""

import sys


def is_windows() -> bool:
    """Runtime platform check shared by portable, fixture-tested adapter entry points."""
    return sys.platform == "win32"
