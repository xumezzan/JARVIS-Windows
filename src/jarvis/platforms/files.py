"""Platform effects on files: the recycle bin and the user's own default application.

Reading, writing and listing are ordinary portable I/O and live in the tool itself; only
these two operations need the operating system's own shell, so only they are adapters.
"""

import asyncio
import ctypes
import errno
import importlib
import os
import stat
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

from jarvis.platforms import is_windows
from jarvis.tools.base import ToolError

POSIX_OS = importlib.import_module("os")


class LocalFiles:
    """The real machine. Deletion always goes to the recycle bin, never past it."""

    async def recycle(self, path: Path) -> None:
        await asyncio.to_thread(self._recycle, path)

    @staticmethod
    def _recycle(path: Path) -> None:
        if sys.platform == "darwin":
            LocalFiles._macos_recycle(path)
            return
        if not is_windows():
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

        shell = importlib.import_module("ctypes").WinDLL("shell32", use_last_error=True)
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

    @staticmethod
    def _macos_recycle(path: Path) -> None:
        """Atomic, exclusive move into the owner's Trash; never copy or overwrite.

        renameatx_np(RENAME_EXCL) works without PyObjC. Directory descriptors keep
        the Trash destination bound even if another process replaces its path.
        A different filesystem is refused instead of doing a destructive copy/delete.
        """
        try:
            if not path.is_absolute() or not stat.S_ISREG(path.lstat().st_mode):
                raise ToolError("file_unsupported")
            trash = Path.home() / ".Trash"
            trash.mkdir(mode=0o700, exist_ok=True)
            flags = os.O_RDONLY | POSIX_OS.O_DIRECTORY | POSIX_OS.O_NOFOLLOW
            trash_fd = os.open(trash, flags)
            try:
                info = os.fstat(trash_fd)
                if info.st_uid != POSIX_OS.getuid():
                    raise ToolError("file_denied")
                source_fd = os.open(path.parent, flags)
                try:
                    for attempt in range(8):
                        # A collision never replaces an earlier trashed document.
                        name = (
                            path.name if attempt == 0 else f"jarvis-{uuid4().hex}{path.suffix[:20]}"
                        )
                        try:
                            LocalFiles._macos_rename_exclusive(source_fd, path.name, trash_fd, name)
                            return
                        except FileExistsError:
                            continue
                    raise ToolError("file_conflict")
                finally:
                    os.close(source_fd)
            finally:
                os.close(trash_fd)
        except OSError:
            raise ToolError("file_failure") from None

    @staticmethod
    def _macos_rename_exclusive(source_fd: int, source: str, trash_fd: int, name: str) -> None:
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        rename = library.renameatx_np
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(source_fd, os.fsencode(source), trash_fd, os.fsencode(name), 0x00000004):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError
            raise OSError(error, "Trash move failed")

    async def open_with_default_app(self, path: Path) -> None:
        await asyncio.to_thread(self._open, path)

    @staticmethod
    def _open(path: Path) -> None:
        # ShellExecute on an exact resolved document path: no command line is parsed and no
        # argument is taken from the caller.
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    ["/usr/bin/open", str(path)],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                )
                return
            if not is_windows():
                raise ToolError("unsupported_platform")
            importlib.import_module("os").startfile(str(path))
        except (OSError, subprocess.SubprocessError):
            raise ToolError("file_failure") from None
