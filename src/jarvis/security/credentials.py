"""Private bounded credential pipe and a no-echo setup CLI. Never source credentials from env."""

import asyncio
import getpass
import re
import sys
from contextlib import suppress

from jarvis.core.planner.contracts import ProviderError

ACCOUNT = "default"
# One entry per vendor, named in full. A key is never shared between providers, and the
# allowlist keeps an argument from ever becoming an arbitrary credential-store name.
SERVICES = {
    "openai": "Jarvis/OpenAI",
    "deepseek": "Jarvis/DeepSeek",
    "fireflies": "Jarvis/Fireflies",
    "asana": "Jarvis/Asana",
}
SERVICE = SERVICES["openai"]
VENDORS = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "fireflies": "Fireflies",
    "asana": "Asana",
}
# An MCP server is named by the owner, so its entry is a pattern rather than a fixed
# name - but a pattern in trusted code, which an argument still cannot step outside.
MCP = re.compile(r"mcp_[a-z][a-z0-9_]{0,31}")


def service_of(provider: str) -> str:
    if provider in SERVICES:
        return SERVICES[provider]
    if MCP.fullmatch(provider):
        return "Jarvis/MCP/" + provider.removeprefix("mcp_")
    raise ProviderError("credentials")


def vendor_of(provider: str) -> str:
    return VENDORS.get(provider) or "MCP " + provider.removeprefix("mcp_")


def known(provider: str) -> bool:
    return provider in SERVICES or MCP.fullmatch(provider) is not None


def setup_command(provider: str = "") -> str:
    """The command that actually works here: this interpreter, not whichever is on PATH.

    Jarvis runs from its own virtual environment, so a bare `python` finds an interpreter
    that has never heard of it and answers with a missing-module error. Printing the real
    executable turns the advice into something the owner can paste and have work.
    """
    executable = sys.executable or "python"
    quoted = f'"{executable}"' if " " in executable else executable
    return f"{quoted} -m jarvis.security.credentials set {provider}".rstrip()


async def load_api_key(provider: str = "openai") -> str:
    service_of(provider)  # Refuse an unknown vendor before spawning anything.
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "jarvis.security.credentials",
            "--pipe",
            provider,
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


def console(stream: object, standard_input: bool) -> bool:
    """True only for a real interactive console, never for a redirected or null stream.

    Windows reports the null device as a character device, so isatty() alone answers True
    for the helper's own stdin=DEVNULL and would refuse every legitimate pipe. Anything
    that cannot be identified is treated as a console, so the key is never written out.
    """
    reader = getattr(stream, "isatty", None)
    if reader is None or not reader():
        return False
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        mode = wintypes.DWORD()
        handle = kernel32.GetStdHandle(wintypes.DWORD(-10 if standard_input else -11))
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return True


def main() -> int:
    try:
        from jarvis.platforms.credentials import native_store

        store = native_store()
        arguments = sys.argv[1:]
        provider = arguments[1] if len(arguments) == 2 and known(arguments[1]) else "openai"
        if arguments[:1] == ["set"] and len(arguments) <= 2 and console(sys.stdin, True):
            key = getpass.getpass(f"{vendor_of(provider)} API key (скрытый ввод): ")
            if not valid_key(key):
                raise ValueError
            store.set_password(service_of(provider), ACCOUNT, key)
            print(f"Ключ {vendor_of(provider)} сохранён в системном хранилище.")
            return 0
        if (
            arguments[:1] == ["--pipe"]
            and len(arguments) <= 2
            and not console(sys.stdin, True)
            and not console(sys.stdout, False)
        ):
            key = store.get_password(service_of(provider), ACCOUNT) or ""
            if not valid_key(key):
                return 1
            sys.stdout.write(key + "\n")
            return 0
    except Exception:
        pass
    if sys.argv[1:2] != ["--pipe"]:
        vendors = "[openai|deepseek|fireflies|mcp_<имя>]"
        print("Ключ недоступен. Настройка: " + setup_command(vendors))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
