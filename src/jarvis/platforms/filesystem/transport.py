"""Kill/reap disposable workers, with bounded pipes and an allowlisted OS environment."""

import asyncio
import os
import sys
import tempfile
from contextlib import suppress
from typing import Literal

import psutil
from pydantic import Field

from jarvis.platforms.filesystem.worker import MAX_REPLY, MAX_REQUEST, Request
from jarvis.security.filesystem_policy import FilePolicy
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, canonical
from jarvis.tools.filesystem import DocumentResult, EvalResult, SearchResult, WriteResult


class Reply(ToolModel):
    error: (
        Literal[
            "file_denied",
            "file_missing",
            "file_changed",
            "file_exists",
            "file_limit",
            "file_unsupported",
            "file_failure",
            "computation_rejected",
            "computation_limit",
        ]
        | None
    ) = None
    result: dict[str, object] | None = Field(default=None)


class FileWorker:
    def __init__(self, policy: FilePolicy, timeout: float = 6) -> None:
        if not 0 < timeout <= 8:
            raise ValueError("Invalid worker timeout.")
        self.policy, self.timeout = policy, timeout

    async def call(self, operation: str, args: ToolModel, context: ExecutionContext) -> ToolModel:
        request = Request.model_validate(
            {"operation": operation, "policy": self.policy, "arguments": canonical(args)}
        )
        payload = request.model_dump_json().encode() + b"\n"
        if len(payload) > MAX_REQUEST:
            raise ToolError("file_limit")
        await context.checkpoint()
        # No HOME, provider keys, proxy config, PYTHONPATH, profiles or user credentials.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL"}
        }
        environment["PYTHONUTF8"] = "1"
        with tempfile.TemporaryDirectory(prefix="jarvis-file-worker-") as working:
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "jarvis.platforms.filesystem.worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=environment,
                    cwd=working,
                    limit=MAX_REPLY + 1,
                    creationflags=0x08000000 if sys.platform == "win32" else 0,
                )
            )
            process: asyncio.subprocess.Process | None = None
            memory_exceeded = False
            monitor: asyncio.Task[None] | None = None
            try:
                process = await asyncio.shield(launch)

                async def watch_memory() -> None:
                    nonlocal memory_exceeded
                    assert process is not None
                    try:
                        observed = psutil.Process(process.pid)
                        while process.returncode is None:
                            if observed.memory_info().rss > 512 * 1024 * 1024:
                                memory_exceeded = True
                                with suppress(ProcessLookupError):
                                    process.kill()
                                return
                            await asyncio.sleep(0.02)
                    except psutil.NoSuchProcess:
                        return
                    except psutil.AccessDenied:
                        memory_exceeded = True
                        with suppress(ProcessLookupError):
                            process.kill()

                monitor = asyncio.create_task(watch_memory())
                await context.checkpoint()
                async with asyncio.timeout(self.timeout):
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    process.stdin.close()
                    raw = await process.stdout.readuntil(b"\n")
                    if len(raw) > MAX_REPLY:
                        raise ToolError("file_limit")
                    await process.wait()
                    if process.returncode != 0:
                        raise ToolError("file_failure")
                    reply = Reply.model_validate_json(raw)
                    if reply.error is not None:
                        raise ToolError(reply.error)
                    if reply.result is None:
                        raise ToolError("file_failure")
                    schemas: dict[str, type[ToolModel]] = {
                        "fs.search": SearchResult,
                        "fs.read_document": DocumentResult,
                        "fs.write_file": WriteResult,
                        "python.sandbox_eval": EvalResult,
                    }
                    return schemas[operation].model_validate(reply.result)
            except TimeoutError:
                raise ToolError("worker_timeout") from None
            except ToolError:
                raise
            except Exception:
                raise ToolError("file_limit" if memory_exceeded else "file_failure") from None
            finally:
                if monitor is not None:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                if process is None:
                    process = await launch
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.communicate()
                await process.wait()
