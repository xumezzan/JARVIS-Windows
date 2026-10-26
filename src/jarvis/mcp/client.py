"""MCP 2025-06-18: bounded stdio and stateless HTTPS JSON, without remote instructions."""

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from jarvis.connectors.http import Route, ServiceTransport
from jarvis.connectors.mcp.protocol import PROTOCOL_VERSION, McpFailure
from jarvis.core.planner.contracts import ProviderError
from jarvis.mcp.models import McpArguments, McpServerConfig, McpToolDefinition, encoded
from jarvis.platforms.mcp_process import stop_process
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.security.credentials import load_api_key


class Wire(Protocol):
    async def exchange(self, request: dict[str, Any]) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class HttpWire:
    def __init__(self, config: McpServerConfig) -> None:
        assert config.url is not None
        self.config = config
        parts = urlsplit(config.url)
        self.path = parts.path or "/"
        self.transport = ServiceTransport(
            "https://" + parts.netloc,
            (Route("POST", re.escape(self.path)),),
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
            },
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            success_statuses=(200, 202),
        )

    async def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.config.url is not None
        await NetworkPolicy(mode=BrowserMode.LIVE).validate_live_request(self.config.url, "POST")
        # This uses the existing per-server OS credential backend, never environment keys.
        key = await load_api_key("mcp_" + self.config.server_id)
        try:
            status, body = await self.transport.request(key, "POST", self.path, request)
            if "id" not in request and (status != 202 or body):
                raise McpFailure("mcp_response")
            return body
        finally:
            key = ""

    async def close(self) -> None:
        pass  # ServiceTransport owns a single-shot context per exchange.


class StdioWire:
    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.working: TemporaryDirectory[str] | None = None

    async def start(self) -> None:
        if self.process is not None:
            return
        executable = Path(self.config.command[0])
        if not executable.is_file():
            raise McpFailure("mcp_request")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "SYSTEMDRIVE",
                "TMP",
                "TEMP",
                "TMPDIR",
                "HOME",
                "USERPROFILE",
                "LOCALAPPDATA",
                "PATH",
                "LANG",
                "LC_ALL",
            }
        }
        environment["PYTHONUTF8"] = "1"
        self.working = TemporaryDirectory(prefix="jarvis-mcp-")
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *self.config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self.working.name,
                env=environment,
                limit=self.config.max_response_bytes + 1,
                start_new_session=sys.platform != "win32",
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        )
        try:
            self.process = await asyncio.shield(launch)
        except BaseException:
            self.process = await launch
            await self.close()
            raise

    async def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        assert self.process is not None and self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(encoded(request) + b"\n")
        await self.process.stdin.drain()
        if "id" not in request:
            return {}
        size = 0
        for _ in range(17):
            raw = await self.process.stdout.readline()
            size += len(raw)
            if not raw or not raw.endswith(b"\n") or size > self.config.max_response_bytes:
                raise McpFailure("mcp_response")
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise McpFailure("mcp_response")
            if message.get("method") == "notifications/tools/list_changed":
                raise McpFailure("mcp_changed")
            if "id" not in message and isinstance(message.get("method"), str):
                continue  # Bounded notifications are data, never callbacks into Jarvis.
            return message
        raise McpFailure("mcp_response")

    async def close(self) -> None:
        try:
            if self.process is not None:
                await stop_process(self.process)
        finally:
            self.process = None
            if self.working is not None:
                self.working.cleanup()
                self.working = None


class McpClient:
    def __init__(self, config: McpServerConfig, wire: Wire | None = None) -> None:
        self.config = McpServerConfig.model_validate_json(config.model_dump_json())
        self.wire = wire or (StdioWire(config) if config.transport == "stdio" else HttpWire(config))

    async def __aenter__(self) -> "McpClient":
        try:
            result = await self.rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "jarvis-workspace", "version": "0.0.1"},
                },
            )
            if result.get("protocolVersion") != PROTOCOL_VERSION:
                raise McpFailure("mcp_response")
            capabilities = result.get("capabilities")
            if not isinstance(capabilities, dict) or not isinstance(
                capabilities.get("tools"), dict
            ):
                raise McpFailure("mcp_response")
            await self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            await self.wire.close()
            raise

    async def __aexit__(self, *args: object) -> None:
        await self.wire.close()

    async def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        if len(encoded(request)) > 32768:
            raise McpFailure("mcp_request")
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                response = await self.wire.exchange(request)
            if len(encoded(response)) > self.config.max_response_bytes:
                raise McpFailure("mcp_response")
            return response
        except asyncio.CancelledError:
            raise
        except McpFailure:
            raise
        except ProviderError:
            raise McpFailure("mcp_credentials") from None
        except Exception:
            raise McpFailure("mcp_network") from None

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request = {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params}
        response = await self._exchange(request)
        if response.get("jsonrpc") != "2.0" or response.get("id") != request["id"]:
            raise McpFailure("mcp_response")
        if "error" in response:
            raise McpFailure("mcp_refused")
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpFailure("mcp_response")
        return result

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        result = await self.rpc("tools/list", {})
        rows = result.get("tools")
        if not isinstance(rows, list) or len(rows) > 32 or result.get("nextCursor"):
            raise McpFailure("mcp_response")
        try:
            tools = tuple(
                McpToolDefinition(
                    name=row["name"],
                    description=row.get("description", ""),
                    input_schema=row["inputSchema"],
                )
                for row in rows
                if isinstance(row, dict)
            )
            if len(tools) != len(rows):
                raise ValueError("Invalid manifest row.")
            self.config.with_review(tools)  # Validate names, allowlist and schema collisions only.
            return tools
        except Exception:
            raise McpFailure("mcp_response") from None

    async def call_tool(self, name: str, args: McpArguments) -> dict[str, Any]:
        if name not in self.config.allowed_tools:
            raise McpFailure("mcp_request")
        return await self.rpc("tools/call", {"name": name, "arguments": dict(args.arguments)})


ClientFactory = Callable[[McpServerConfig], McpClient]


async def discover(
    config: McpServerConfig, factory: ClientFactory = McpClient
) -> tuple[McpToolDefinition, ...]:
    async with asyncio.timeout(config.timeout_seconds):
        async with factory(config) as client:
            return await client.list_tools()
