"""Private bounded credential pipe and a no-echo setup CLI. Never source credentials from env."""

import asyncio
import getpass
import sys
from contextlib import suppress

from jarvis.core.planner.contracts import ProviderError

SERVICE = "Jarvis/OpenAI"
ACCOUNT = "default"


async def load_api_key() -> str:
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "jarvis.security.credentials",
            "--pipe",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8192,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    )
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(8):
            process = await asyncio.shield(launch)
            assert process.stdout is not None
            raw = await process.stdout.readuntil(b"\n")
            await process.wait()
            key = raw.decode().strip()
            if process.returncode != 0 or not valid_key(key):
                raise ProviderError("credentials")
            return key
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ProviderError("credentials") from None
    finally:
        if process is None:
            process = await launch
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
        await process.wait()


def valid_key(key: str) -> bool:
    return 1 <= len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key)


def main() -> int:
    try:
        from jarvis.platforms.credentials import native_store

        store = native_store()
        if sys.argv[1:] == ["set"] and sys.stdin.isatty():
            key = getpass.getpass("OpenAI API key (скрытый ввод): ")
            if not valid_key(key):
                raise ValueError
            store.set_password(SERVICE, ACCOUNT, key)
            print("Ключ сохранён в системном хранилище.")
            return 0
        if sys.argv[1:] == ["--pipe"] and not sys.stdin.isatty() and not sys.stdout.isatty():
            key = store.get_password(SERVICE, ACCOUNT) or ""
            if not valid_key(key):
                return 1
            sys.stdout.write(key + "\n")
            return 0
    except Exception:
        pass
    if sys.argv[1:] != ["--pipe"]:
        print("Ключ недоступен. Настройка: python -m jarvis.security.credentials set")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
