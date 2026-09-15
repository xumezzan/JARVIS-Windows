"""Killable, bounded MSAL / OS credential helper; no secret in argv, env or files."""

import asyncio
import json
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID


class MailFailure(Exception):
    """Finite public error with no server text or credentials."""

    def __init__(self, code: str = "mail_unavailable") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Credential:
    home_id: str
    token: str = field(repr=False)


class Credentials(Protocol):
    async def call(
        self,
        operation: Literal["connect", "silent", "disconnect"],
        client_id: str,
        home_id: str = "",
    ) -> Credential: ...


class ProcessCredentials:
    async def call(
        self,
        operation: Literal["connect", "silent", "disconnect"],
        client_id: str,
        home_id: str = "",
    ) -> Credential:
        if operation != "disconnect":
            UUID(client_id)
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "jarvis.mail.oauth_helper",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=65536,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        )
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(150 if operation == "connect" else 15):
                process = await asyncio.shield(launch)
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(json.dumps([operation, client_id, home_id]).encode() + b"\n")
                await process.stdin.drain()
                process.stdin.close()
                raw = await process.stdout.readuntil(b"\n")
                await process.wait()
                data = json.loads(raw)
                if process.returncode != 0 or not isinstance(data, dict):
                    raise MailFailure("mail_credentials")
                home, token = data.get("home_id", ""), data.get("token", "")
                if not isinstance(home, str) or not isinstance(token, str):
                    raise MailFailure("mail_credentials")
                if operation != "disconnect" and (
                    not 1 <= len(home) <= 512
                    or not 1 <= len(token) <= 32768
                    or any(not 33 <= ord(c) <= 126 for c in token)
                    or (operation == "silent" and home != home_id)
                ):
                    raise MailFailure("mail_credentials")
                return Credential(home, token)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise MailFailure("mail_credentials") from None
        finally:
            if process is None:
                process = await launch
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
