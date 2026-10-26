"""Per-page CDP checks. Cookie/header contents never enter tool results or audit."""

import asyncio
from typing import Any

from playwright.async_api import CDPSession, Page

from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ToolError


class LiveNetworkGuard:
    """Fetch intercepts each redirect hop, unlike Playwright's first-request routing.

    This guards attached page traffic. Existing workers, extensions, WebRTC and the
    Chrome process itself are outside this boundary; see docs/BROWSER_MODES.md.
    """

    def __init__(self, policy: NetworkPolicy) -> None:
        self.policy = policy
        self.connections: dict[Page, CDPSession] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.failures: set[Page] = set()
        self._dns_slots = asyncio.Semaphore(16)
        self._stopping = False
        self._attach_lock = asyncio.Lock()

    async def attach(self, page: Page) -> None:
        async with self._attach_lock:
            if page in self.connections:
                return
            self._stopping = False
            connection = await page.context.new_cdp_session(page)
            self.connections[page] = connection

            def paused(event: dict[str, Any]) -> None:
                if self._stopping:
                    return  # Fetch.disable below releases the pause when disconnecting.
                task = asyncio.create_task(self._request(page, connection, event))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

            connection.on("Fetch.requestPaused", paused)
            await connection.send("Network.enable")
            await connection.send("Network.setBypassServiceWorker", {"bypass": True})
            await connection.send(
                "Network.setBlockedURLs", {"urls": ["ws://*", "wss://*", "ftp://*", "file://*"]}
            )
            await connection.send("Fetch.enable", {"patterns": [{"requestStage": "Request"}]})

    async def _request(self, page: Page, connection: CDPSession, event: dict[str, Any]) -> None:
        request_id = event["requestId"]
        try:
            async with self._dns_slots:
                await self.policy.validate_live_request(
                    event["request"]["url"], event["request"].get("method", "GET")
                )
        except asyncio.CancelledError:
            # Detach/Fetch.disable releases pending requests during shutdown.
            raise
        except Exception:
            try:
                await connection.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
                )
            except Exception:
                if not page.is_closed():
                    self.failures.add(page)
        else:
            try:
                await connection.send("Fetch.continueRequest", {"requestId": request_id})
            except Exception:
                if not page.is_closed():
                    self.failures.add(page)

    async def detach(self, page: Page) -> None:
        connection = self.connections.pop(page, None)
        self.failures.discard(page)
        if connection is None or page.is_closed():
            return
        async with asyncio.timeout(2):
            await connection.send("Fetch.disable")
            await connection.send("Network.setBypassServiceWorker", {"bypass": False})
            await connection.send("Network.setBlockedURLs", {"urls": []})
            await connection.detach()

    async def close(self) -> None:
        self._stopping = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        outcomes = await asyncio.gather(
            *(self.detach(page) for page in list(self.connections)), return_exceptions=True
        )
        self.connections.clear()
        self.failures.clear()
        if any(isinstance(outcome, BaseException) for outcome in outcomes):
            raise ToolError("browser_cleanup")
