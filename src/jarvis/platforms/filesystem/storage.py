"""Bounded reads and exclusive creation, with no symlink/reparse traversal."""

import fnmatch
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from jarvis.security.filesystem_policy import MAX_FILE_BYTES, FilePolicy
from jarvis.tools.base import ToolError
from jarvis.tools.filesystem import (
    FileEntry,
    FileVersion,
    Search,
    SearchResult,
    WriteFile,
    WriteResult,
)


def windows_platform() -> bool:
    return sys.platform == "win32"


def regular(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ToolError("file_denied")
    return info


def _windows_open(path: Path, *, directory: bool, flags: int = os.O_RDONLY) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel.GetFileAttributesW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    access = 0x80000000 | (0x40000000 if flags & (os.O_WRONLY | os.O_RDWR) else 0)
    # Hold ancestor handles without FILE_SHARE_DELETE to prevent directory redirection.
    share = 3 if directory else 1
    creation = 1 if flags & os.O_EXCL else 3  # CREATE_NEW / OPEN_EXISTING; never truncate.
    attributes = 0x00200000 | (
        0x02000000 if directory else 0
    )  # OPEN_REPARSE_POINT / BACKUP_SEMANTICS.
    handle = kernel.CreateFileW(str(path), access, share, None, creation, attributes, None)
    if handle == ctypes.c_void_p(-1).value:
        error = cast(Any, ctypes).get_last_error()
        if error in (2, 3):
            raise FileNotFoundError
        if error in (80, 183):
            raise FileExistsError
        raise ToolError("file_denied")
    try:
        # GetFileInformationByHandle prevents a replaced path from disguising a reparse handle.
        class Info(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("created", wintypes.FILETIME),
                ("accessed", wintypes.FILETIME),
                ("written", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            ]

        kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        info = Info()
        if (
            not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            or info.attributes & 0x400
        ):
            raise ToolError("file_denied")
        if directory:
            if not info.attributes & 0x10:
                raise ToolError("file_denied")
            result = int(handle)
            handle = None
            return result
        fd = int(cast(Any, msvcrt).open_osfhandle(int(handle), flags | getattr(os, "O_BINARY", 0)))
        handle = None
        return fd
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


@dataclass
class Directory:
    path: Path
    fd: int | None

    def open(self, name: str, flags: int = os.O_RDONLY) -> int:
        if windows_platform():
            return _windows_open(self.path / name, directory=False, flags=flags)
        assert self.fd is not None
        return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)

    def unlink(self, name: str) -> None:
        if self.fd is None:
            (self.path / name).unlink()
        else:
            os.unlink(name, dir_fd=self.fd)

    def publish(self, source: str, target: str, *, replace: bool) -> str | None:
        backup: str | None = None
        if self.fd is None:
            if replace:
                import ctypes
                from ctypes import wintypes

                kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
                kernel.ReplaceFileW.argtypes = [
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ]
                backup = ".jarvis-backup-" + uuid4().hex
                if not kernel.ReplaceFileW(
                    str(self.path / target),
                    str(self.path / source),
                    str(self.path / backup),
                    0,
                    None,
                    None,
                ):
                    raise ToolError("file_failure")
            else:
                os.link(self.path / source, self.path / target)
        elif replace:
            os.replace(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.link(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        if self.fd is not None:
            os.fsync(self.fd)
        return backup


@contextmanager
def directory(path: Path, *, create: bool = False) -> Iterator[Directory]:
    handles: list[int] = []
    try:
        current = Path(path.anchor)
        if windows_platform():
            handles.append(_windows_open(current, directory=True))
            for part in path.parts[1:]:
                current /= part
                if create:
                    current.mkdir(exist_ok=True)
                handles.append(_windows_open(current, directory=True))
            yield Directory(path, None)
        else:
            handles.append(os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for part in path.parts[1:]:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o700, dir_fd=handles[-1])
                handles.append(
                    os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handles[-1])
                )
            yield Directory(path, handles[-1])
    finally:
        for handle in reversed(handles):
            if windows_platform():
                import ctypes
                from ctypes import wintypes

                kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(handle)
            else:
                os.close(handle)


def read(directory: Directory, name: str) -> tuple[bytes, FileVersion]:
    fd = directory.open(name)
    try:
        before = regular(fd)
        if before.st_size > MAX_FILE_BYTES:
            raise ToolError("file_limit")
        chunks = []
        size = 0
        while chunk := os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ToolError("file_limit")
        after = regular(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or size != after.st_size:
            raise ToolError("file_changed")
        data = b"".join(chunks)
        return data, FileVersion(
            device=str(after.st_dev),
            file_id=str(after.st_ino),
            modified_ns=str(after.st_mtime_ns),
            size=size,
            sha256=sha256(data).hexdigest(),
        )
    finally:
        os.close(fd)


def search(policy: FilePolicy, args: Search) -> SearchResult:
    root = policy.path(args.directory)
    pending = [(root, 0)]
    matches: list[FileEntry] = []
    examined = 0
    result_bytes = 0
    truncated = False
    started = monotonic()
    query = args.query.casefold()
    while pending and not truncated:
        path, depth = pending.pop()
        try:
            with (
                directory(path) as parent,
                os.scandir(parent.fd if parent.fd is not None else path) as entries,
            ):
                for entry in entries:
                    if examined >= 20000 or len(matches) >= 100 or monotonic() - started > 3:
                        truncated = True
                        break
                    examined += 1
                    candidate = path / entry.name
                    try:
                        policy.path(str(candidate))
                        info = entry.stat(follow_symlinks=False)
                        if (
                            stat.S_ISLNK(info.st_mode)
                            or getattr(info, "st_file_attributes", 0) & 0x400
                        ):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if depth < 8:
                                pending.append((candidate, depth + 1))
                            else:
                                truncated = True
                            continue
                        name = entry.name.casefold()
                        matched = (
                            name.endswith(query)
                            if query.startswith(".")
                            else fnmatch.fnmatchcase(name, query)
                            if any(char in query for char in "*?[")
                            else query in name
                        )
                        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and matched:
                            item = FileEntry(
                                path=str(candidate),
                                size=info.st_size,
                                modified_ns=str(info.st_mtime_ns),
                            )
                            result_bytes += len(item.model_dump_json().encode())
                            if result_bytes > 160000:
                                truncated = True
                                break
                            matches.append(item)
                    except (ToolError, OSError):
                        continue
        except (PermissionError, NotADirectoryError):
            if path == root:
                raise ToolError("file_denied") from None
            truncated = True
    return SearchResult(
        directory=str(root),
        files=sorted(matches, key=lambda entry: entry.path.casefold()),
        truncated=truncated,
        examined=examined,
    )


@contextmanager
def write_lock(policy: FilePolicy) -> Iterator[None]:
    """Serialize Jarvis commits across its Qt/Electron processes; no advisory-lock bypass claim."""
    with directory(Path(policy.protected), create=True) as state:
        try:
            fd = state.open(".filesystem.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            fd = state.open(".filesystem.lock", os.O_RDWR)
        try:
            regular(fd)
            if not windows_platform():
                import fcntl

                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ToolError("file_changed") from None
            # Windows exclusive writable handle denies competing write handles until closed.
            yield
        finally:
            os.close(fd)


def write(policy: FilePolicy, args: WriteFile) -> WriteResult:
    path = (
        policy.creation_path(args.path)
        if args.expected_version is None
        else policy.path(args.path, write=True)
    )
    with write_lock(policy):
        return _write(policy, args, path)


def _write(policy: FilePolicy, args: WriteFile, path: Path) -> WriteResult:
    content = args.content.encode("utf-8")
    temporary = ".jarvis-part-" + uuid4().hex
    with directory(path.parent, create=args.expected_version is None) as parent:
        mode = 0o600
        if args.expected_version is not None:
            _, current = read(parent, path.name)
            if current != args.expected_version:
                raise ToolError("file_changed")
            if not windows_platform():
                source = parent.open(path.name)
                try:
                    metadata = regular(source)
                    if metadata.st_uid != os.geteuid() or not metadata.st_mode & stat.S_IWUSR:
                        raise ToolError("file_denied")
                    mode = stat.S_IMODE(metadata.st_mode) & 0o777
                finally:
                    os.close(source)
        fd = parent.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        keep_recovery = False
        try:
            with os.fdopen(fd, "wb") as stream:
                if not windows_platform():
                    os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if args.expected_version is not None:
                _, current = read(parent, path.name)
                if current != args.expected_version:
                    raise ToolError("file_changed")
            keep_recovery = windows_platform() and args.expected_version is not None
            backup = parent.publish(temporary, path.name, replace=args.expected_version is not None)
            if args.expected_version is None:
                parent.unlink(temporary)
            _, observed = read(parent, path.name)
            if observed.sha256 != sha256(content).hexdigest():
                raise ToolError("file_changed")
            if backup is not None:
                parent.unlink(backup)
            keep_recovery = False
            return WriteResult(
                path=str(path),
                sha256=observed.sha256,
                bytes_written=len(content),
                created=args.expected_version is None,
            )
        finally:
            if not keep_recovery:
                with suppress(FileNotFoundError):
                    parent.unlink(temporary)
