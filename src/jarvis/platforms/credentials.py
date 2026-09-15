"""Explicit OS credential backend, never plugin discovery or plaintext fallback."""

import sys
from collections.abc import Callable
from typing import cast

from keyring.backend import KeyringBackend


def native_store() -> KeyringBackend:
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return cast(Callable[[], KeyringBackend], WinVaultKeyring)()
    elif sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return cast(Callable[[], KeyringBackend], Keyring)()
    else:
        raise RuntimeError("OS credential backend unavailable.")
