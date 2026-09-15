"""Read only an explicitly UI-selected regular file in a killable helper."""

import asyncio
import json
import os
import stat
import sys
from contextlib import suppress
from pathlib import Path

from jarvis.mail.credentials import MailFailure


async def read_attachment(path: str) -> bytes:
    if not 1 <= len(path) <= 4096:
        raise MailFailure("mail_attachment")
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "jarvis.mail.attachments",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=65536,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    )
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(5):
            process = await asyncio.shield(launch)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(path).encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            data = await process.stdout.read(32769)
            # read(n) may return a partial pipe buffer; collect until EOF within the bound.
            while len(data) <= 32768:
                chunk = await process.stdout.read(32769 - len(data))
                if not chunk:
                    break
                data += chunk
            if len(data) > 32768:
                raise MailFailure("mail_attachment_limit")
            await process.wait()
            if process.returncode != 0:
                raise MailFailure("mail_attachment")
            return data
    except asyncio.CancelledError:
        raise
    except Exception:
        raise MailFailure("mail_attachment") from None
    finally:
        if process is None:
            process = await launch
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()


def main() -> int:
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 1
    try:
        raw = sys.stdin.buffer.readline(32769)
        path = json.loads(raw)
        if not isinstance(path, str) or not 1 <= len(path) <= 4096 or Path(path).is_symlink():
            return 1
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                return 1
            data = stream.read(32769)
        if len(data) > 32768:
            return 1
        sys.stdout.buffer.write(data)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
