"""Platform effects on files: the recycle bin and the user's own default application.

Reading, writing and listing are ordinary portable I/O and live in the tool itself; only
these two operations need the operating system's own shell, so only they are adapters.
"""

import asyncio
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from jarvis.tools.base import ToolError


class LocalFiles:
    """The real machine. Deletion always goes to the recycle bin, never past it."""

    async def recycle(self, path: Path) -> None:
        await asyncio.to_thread(self._recycle, path)

    @staticmethod
    def _recycle(path: Path) -> None:
        if sys.platform != "win32":
            raise ToolError("unsupported_platform")

        class Operation(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SHFileOperationW.argtypes = [ctypes.POINTER(Operation)]
        shell.SHFileOperationW.restype = ctypes.c_int
        # FO_DELETE with ALLOWUNDO: the file lands in the recycle bin and stays recoverable.
        # NOCONFIRMATION/NOERRORUI/SILENT keep the shell from opening windows of its own,
        # which would block a helper that must stay cancellable.
        request = Operation(
            None, 3, str(path) + "\0\0", None, 0x0040 | 0x0010 | 0x0400 | 0x0004, False, None, None
        )
        if shell.SHFileOperationW(ctypes.byref(request)) or request.fAnyOperationsAborted:
            raise ToolError("file_failure")

    async def open_with_default_app(self, path: Path) -> None:
        await asyncio.to_thread(self._open, path)

    @staticmethod
    def _open(path: Path) -> None:
        # ShellExecute on an exact resolved document path: no command line is parsed and no
        # argument is taken from the caller.
        try:
            os.startfile(str(path))  # noqa: S606 - resolved document inside an allowed folder
        except OSError:
            raise ToolError("file_failure") from None
