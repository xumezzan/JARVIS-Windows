"""Typed file tools bounded by an explicit folder policy.

Every path is decided by the policy before anything touches the disk, in simulation as
well as in execution. Nothing here accepts a pattern the caller can turn into a command,
and no tool reaches outside the allowed folders even by following a link.
"""

import asyncio
import os
from collections.abc import Callable, Iterator
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from jarvis.files.policy import MAX_TEXT_BYTES, FilePolicy
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

MAX_TEXT = 200_000
MAX_SCANNED = 20_000


def digest(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


class FileBackend(Protocol):
    async def recycle(self, path: Path) -> None: ...

    async def open_with_default_app(self, path: Path) -> None: ...


class FindFiles(ToolModel):
    query: str = Field(min_length=1, max_length=200)
    folder: str = Field(default="", max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class ListFolder(ToolModel):
    folder: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=50, ge=1, le=100)


class ReadFile(ToolModel):
    path: str = Field(min_length=1, max_length=4096)


class WriteFile(ToolModel):
    service: Literal["local-files"] = "local-files"
    action_type: Literal["write_text_file"] = "write_text_file"
    path: str = Field(min_length=1, max_length=4096)
    text: str = Field(max_length=MAX_TEXT, repr=False)
    overwrite: bool = False


class RenameFile(ToolModel):
    service: Literal["local-files"] = "local-files"
    action_type: Literal["rename_in_place"] = "rename_in_place"
    path: str = Field(min_length=1, max_length=4096)
    new_name: str = Field(min_length=1, max_length=255)


class RecycleFile(ToolModel):
    service: Literal["local-files"] = "local-files"
    action_type: Literal["move_to_recycle_bin"] = "move_to_recycle_bin"
    path: str = Field(min_length=1, max_length=4096)


class OpenFile(ToolModel):
    service: Literal["local-files"] = "local-files"
    action_type: Literal["open_in_default_application"] = "open_in_default_application"
    path: str = Field(min_length=1, max_length=4096)


class FileEntry(ToolModel):
    path: str = Field(max_length=4096)
    name: str = Field(max_length=255)
    folder: bool = False
    size: int = Field(default=0, ge=0)


class FileListing(ToolModel):
    entries: list[FileEntry] = Field(default_factory=list, max_length=100)
    complete: bool = True


class FileContent(ToolModel):
    path: str = Field(max_length=4096)
    text: str = Field(default="", max_length=MAX_TEXT, repr=False)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool = False


class FileReceipt(ToolModel):
    path: str = Field(max_length=4096)
    sha256: str = Field(default="", pattern=r"^(|[a-f0-9]{64})$")
    exists: bool = False


def entry_for(path: Path) -> FileEntry:
    try:
        stat = path.stat()
        size = 0 if path.is_dir() else int(stat.st_size)
    except OSError:
        size = 0
    return FileEntry(path=str(path), name=path.name, folder=path.is_dir(), size=size)


def walk(policy: FilePolicy, start: Path) -> Iterator[Path]:
    """Depth-first over allowed folders, refusing to follow a link out of them."""
    scanned = 0
    stack = [start]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            scanned += 1
            if scanned > MAX_SCANNED:
                return
            try:
                resolved = policy.normalise(child)
            except ToolError:
                continue
            if not policy.inside(resolved):
                continue
            yield child
            if child.is_dir() and not child.is_symlink():
                stack.append(child)


def read_text(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        raise ToolError("file_missing")
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            raise ToolError("file_too_large")
        raw = path.read_bytes()[:MAX_TEXT_BYTES]
    except OSError:
        raise ToolError("file_failure") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError("file_unsupported") from None
    return (text[:MAX_TEXT], len(text) > MAX_TEXT)


def gate[T: ToolModel](check: Callable[[T], object]) -> Callable[[T], None]:
    """Adapt a path check to the registry's pure policy hook, which returns nothing."""

    def decide(args: T) -> None:
        check(args)

    return decide


def register_files(registry: ToolRegistry, policy: FilePolicy, backend: FileBackend) -> None:
    """One family, one policy object; every hook re-checks the path it was given."""

    def folder_of(raw: str) -> Path:
        path = policy.check(raw)
        if not path.is_dir():
            raise ToolError("file_missing")
        return path

    async def find_check(args: FindFiles, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def find_run(args: FindFiles, context: ExecutionContext) -> FileListing:
        roots = [folder_of(args.folder)] if args.folder else list(policy.roots)
        wanted = args.query.casefold()

        def search() -> tuple[list[FileEntry], bool]:
            found: list[FileEntry] = []
            for root in roots:
                for path in walk(policy, root):
                    if wanted in path.name.casefold():
                        found.append(entry_for(path))
                        if len(found) >= args.limit:
                            return found, False
            return found, True

        await context.checkpoint()
        entries, complete = await asyncio.to_thread(search)
        return FileListing(entries=entries, complete=complete)

    async def find_verify(args: FindFiles, result: FileListing, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return all(
            args.query.casefold() in entry.name.casefold() and policy.inside(Path(entry.path))
            for entry in result.entries
        )

    async def list_check(args: ListFolder, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def list_run(args: ListFolder, context: ExecutionContext) -> FileListing:
        folder = folder_of(args.folder)

        def listing() -> tuple[list[FileEntry], bool]:
            entries: list[FileEntry] = []
            try:
                children = sorted(folder.iterdir())
            except OSError:
                raise ToolError("file_failure") from None
            for child in children:
                if policy.inside(policy.normalise(child)):
                    entries.append(entry_for(child))
                if len(entries) >= args.limit:
                    return entries, False
            return entries, True

        await context.checkpoint()
        entries, complete = await asyncio.to_thread(listing)
        return FileListing(entries=entries, complete=complete)

    async def list_verify(args: ListFolder, result: FileListing, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return all(policy.inside(Path(entry.path)) for entry in result.entries)

    async def read_check(args: ReadFile, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return policy.check(args.path).is_file()

    async def read_run(args: ReadFile, context: ExecutionContext) -> FileContent:
        path = policy.readable_text(policy.check(args.path))
        await context.checkpoint()
        text, truncated = await asyncio.to_thread(read_text, path)
        return FileContent(path=str(path), text=text, sha256=digest(text), truncated=truncated)

    async def read_verify(args: ReadFile, result: FileContent, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return result.sha256 == digest(result.text)

    async def write_check(args: WriteFile, context: ExecutionContext) -> bool:
        path = policy.check(args.path)
        await context.checkpoint()
        if path.exists() and not args.overwrite:
            raise ToolError("file_conflict")
        if path.exists() and not path.is_file():
            raise ToolError("file_unsupported")
        return path.parent.is_dir()

    async def write_run(args: WriteFile, context: ExecutionContext) -> FileReceipt:
        path = policy.readable_text(policy.writable(policy.check(args.path)))
        existed = path.exists()
        if existed and not args.overwrite:
            raise ToolError("file_conflict")
        await context.checkpoint()

        def save() -> None:
            temporary = path.with_name(path.name + ".jarvis-part")
            try:
                temporary.write_text(args.text, encoding="utf-8", newline="\n")
                os.replace(temporary, path)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise ToolError("file_failure") from None

        await asyncio.to_thread(save)
        return FileReceipt(path=str(path), sha256=digest(args.text), exists=existed)

    async def write_verify(args: WriteFile, result: FileReceipt, context: ExecutionContext) -> bool:
        path = policy.check(args.path)
        await context.checkpoint()
        text, truncated = await asyncio.to_thread(read_text, path)
        return not truncated and digest(text) == digest(args.text) == result.sha256

    async def rename_check(args: RenameFile, context: ExecutionContext) -> bool:
        path = policy.check(args.path)
        await context.checkpoint()
        if not path.exists():
            raise ToolError("file_missing")
        if (path.parent / args.new_name).exists():
            raise ToolError("file_conflict")
        return True

    async def rename_run(args: RenameFile, context: ExecutionContext) -> FileReceipt:
        path = policy.writable(policy.check(args.path))
        destination = policy.writable(policy.check(str(path.parent / args.new_name)))
        if destination.exists():
            raise ToolError("file_conflict")
        await context.checkpoint()

        def move() -> None:
            try:
                path.rename(destination)
            except OSError:
                raise ToolError("file_failure") from None

        await asyncio.to_thread(move)
        return FileReceipt(path=str(destination), exists=True)

    async def rename_verify(
        args: RenameFile, result: FileReceipt, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        source = policy.check(args.path)
        return Path(result.path).exists() and not source.exists()

    async def recycle_check(args: RecycleFile, context: ExecutionContext) -> bool:
        path = policy.check(args.path)
        await context.checkpoint()
        if not path.exists():
            raise ToolError("file_missing")
        return path.is_file()

    async def recycle_run(args: RecycleFile, context: ExecutionContext) -> FileReceipt:
        path = policy.writable(policy.check(args.path))
        if not path.is_file():
            raise ToolError("file_unsupported")
        await context.checkpoint()
        await backend.recycle(path)
        return FileReceipt(path=str(path), exists=False)

    async def recycle_verify(
        args: RecycleFile, result: FileReceipt, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return not policy.check(args.path).exists()

    async def open_check(args: OpenFile, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return policy.check(args.path).is_file()

    async def open_run(args: OpenFile, context: ExecutionContext) -> FileReceipt:
        path = policy.openable(policy.check(args.path))
        if not path.is_file():
            raise ToolError("file_missing")
        await context.checkpoint()
        await backend.open_with_default_app(path)
        return FileReceipt(path=str(path), exists=True)

    async def open_verify(args: OpenFile, result: FileReceipt, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return policy.check(args.path).is_file()

    registry.register(
        ToolSpec(
            "files.find",
            "Найти файлы по части имени в разрешённых папках.",
            Risk.SAFE,
            FindFiles,
            FileListing,
            find_check,
            find_run,
            find_verify,
            timeout_seconds=20,
            policy=gate(lambda args: policy.check(args.folder) if args.folder else None),
        )
    )
    registry.register(
        ToolSpec(
            "files.list_folder",
            "Показать содержимое разрешённой папки.",
            Risk.SAFE,
            ListFolder,
            FileListing,
            list_check,
            list_run,
            list_verify,
            timeout_seconds=15,
            policy=gate(lambda args: policy.check(args.folder)),
        )
    )
    registry.register(
        ToolSpec(
            "files.read_text",
            "Прочитать небольшой текстовый файл.",
            Risk.SAFE,
            ReadFile,
            FileContent,
            read_check,
            read_run,
            read_verify,
            timeout_seconds=15,
            policy=gate(lambda args: policy.readable_text(policy.check(args.path))),
        )
    )
    registry.register(
        ToolSpec(
            "files.write_text",
            "Создать или заменить текстовый файл.",
            # Inside the owner's own folders, and an existing file is never replaced
            # unless the caller asked for it.
            Risk.ROUTINE,
            WriteFile,
            FileReceipt,
            write_check,
            write_run,
            write_verify,
            timeout_seconds=15,
            policy=gate(
                lambda args: policy.readable_text(policy.writable(policy.check(args.path)))
            ),
        )
    )
    registry.register(
        ToolSpec(
            "files.rename",
            "Переименовать файл или папку на месте.",
            # Renaming back restores the previous state exactly.
            Risk.ROUTINE,
            RenameFile,
            FileReceipt,
            rename_check,
            rename_run,
            rename_verify,
            timeout_seconds=15,
            policy=gate(lambda args: rename_names(policy, args)),
        )
    )
    registry.register(
        ToolSpec(
            "files.recycle",
            "Переместить файл в корзину. Безвозвратного удаления нет.",
            Risk.CONFIRM,
            RecycleFile,
            FileReceipt,
            recycle_check,
            recycle_run,
            recycle_verify,
            timeout_seconds=20,
            policy=gate(lambda args: policy.writable(policy.check(args.path))),
            cancellation="The shell may finish a recycle it already started.",
        )
    )
    registry.register(
        ToolSpec(
            "files.open",
            "Открыть документ в его обычном приложении.",
            # Opening shows a document; it does not alter it, and programs are refused.
            Risk.ROUTINE,
            OpenFile,
            FileReceipt,
            open_check,
            open_run,
            open_verify,
            timeout_seconds=20,
            policy=gate(lambda args: policy.openable(policy.check(args.path))),
            cancellation="The application may keep running after the task stops.",
        )
    )


def rename_names(policy: FilePolicy, args: RenameFile) -> None:
    """A new name is a name, never a path: no separator, drive or parent may appear in it."""
    path = policy.writable(policy.check(args.path))
    if args.new_name in (".", "..") or set(args.new_name) & set('\\/:*?"<>|'):
        raise ToolError("path_denied")
    policy.writable(policy.check(str(path.parent / args.new_name)))
