"""OS resource ceilings for disposable workers; never import native Windows code elsewhere."""

import sys
from typing import Any, cast

from jarvis.tools.base import ToolError

_job: Any = None


def windows_platform() -> bool:
    return sys.platform == "win32"


def limit_worker() -> None:
    global _job
    if not windows_platform():
        import resource

        def cap(kind: int, maximum: int) -> None:
            _, hard = resource.getrlimit(kind)
            maximum = min(maximum, hard) if hard != resource.RLIM_INFINITY else maximum
            resource.setrlimit(kind, (maximum, maximum))

        cap(resource.RLIMIT_CPU, 4)
        # Darwin rejects these memory rlimits on supported current hosts. The parent RSS
        # watchdog applies there; unlike Linux/Windows this is not a hard allocation cap.
        if sys.platform != "darwin":
            cap(resource.RLIMIT_AS, 512 * 1024 * 1024)
        cap(resource.RLIMIT_FSIZE, 128 * 1024)
        return
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("min_working", ctypes.c_size_t),
            ("max_working", ctypes.c_size_t),
            ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "read_ops",
                "write_ops",
                "other_ops",
                "read_bytes",
                "write_bytes",
                "other_bytes",
            )
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", IO),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _job = kernel.CreateJobObjectW(None, None)
    settings = Extended()
    settings.basic.flags = 0x2 | 0x100 | 0x2000  # CPU time, process memory, kill-on-close.
    settings.basic.process_time = 4 * 10_000_000
    settings.process_memory = 512 * 1024 * 1024
    if (
        not _job
        or not kernel.SetInformationJobObject(
            _job, 9, ctypes.byref(settings), ctypes.sizeof(settings)
        )
        or not kernel.AssignProcessToJobObject(_job, kernel.GetCurrentProcess())
    ):
        raise ToolError("file_failure")
