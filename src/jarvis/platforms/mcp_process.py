"""Owned MCP process cleanup on both supported development and target platforms."""

import asyncio
import os
import signal
import sys
from contextlib import suppress

import psutil


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Kill the owned process group/tree, never a process chosen by model input."""
    descendants: list[psutil.Process] = []
    with suppress(psutil.Error):
        descendants = psutil.Process(process.pid).children(recursive=True)
    # Snapshot children before EOF: a server may exit immediately when stdin closes,
    # after which Windows can no longer enumerate its orphaned descendants.
    if process.stdin is not None:
        process.stdin.close()
    for child in reversed(descendants):
        with suppress(psutil.Error):
            child.kill()
    if sys.platform != "win32":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    async with asyncio.timeout(2):
        await process.wait()
