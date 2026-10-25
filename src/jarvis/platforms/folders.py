"""OS folder discovery, including redirected Windows Known Folders (e.g. OneDrive)."""

import sys
from pathlib import Path
from typing import Any, cast


def windows_platform() -> bool:
    return sys.platform == "win32"


def working_folders() -> tuple[tuple[str, Path], ...]:
    names = ("Desktop", "Downloads", "Documents")
    if not windows_platform():
        return tuple((name, Path.home() / name) for name in names)
    import ctypes
    import uuid
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    shell = cast(Any, ctypes).WinDLL("shell32", use_last_error=True)
    ole = cast(Any, ctypes).WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    folders = []
    for name, key in zip(
        names,
        (
            "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
            "374DE290-123F-4565-9164-39C4925E467B",
            "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
        ),
        strict=True,
    ):
        guid = Guid((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(key).bytes_le))
        output = ctypes.c_void_p()
        if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(output)) != 0:
            continue  # No guessed fallback to a different directory on discovery failure.
        try:
            folders.append((name, Path(ctypes.wstring_at(output))))
        finally:
            ole.CoTaskMemFree(output)
    return tuple(folders)
