"""Persistent asyncio owner across short-lived Qt ToolWorker loops; lazy browser startup."""

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, cast

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from jarvis.browser.session import BrowserSession
from jarvis.platforms.windows.ghost import CursorTarget
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy, validate_cdp_endpoint
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.browser import BrowserCommand, BrowserResult
from jarvis.workspace.models import WorkspaceProfile

if TYPE_CHECKING:
    from jarvis.config import AppConfig


class BrowserHost:
    def __init__(
        self,
        policy: NetworkPolicy | None = None,
        *,
        headless: bool | None = None,
        cdp_endpoint: str = "",
        profile_dir: Path | None = None,
        workspace: WorkspaceProfile | None = None,
    ) -> None:
        self.policy = policy or NetworkPolicy()
        self.headless = self.policy.mode is BrowserMode.STRICT if headless is None else headless
        self.cdp_endpoint = validate_cdp_endpoint(cdp_endpoint) if cdp_endpoint else ""
        if self.cdp_endpoint and self.policy.mode is BrowserMode.STRICT:
            raise ValueError("CDP requires Live Browser mode.")
        self.profile_dir = profile_dir
        self.workspace = WorkspaceProfile.model_validate(workspace or WorkspaceProfile())
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._session: BrowserSession | None = None
        self._lock = Lock()
        self._serial: asyncio.Lock | None = None
        self._closed = False
        self._ready = Event()
        self.before_action: Callable[[CursorTarget], Awaitable[None]] | None = None
        self.operation_timeout = 30.0 if self.policy.mode is BrowserMode.WORKSPACE else 15.0

    @classmethod
    def from_config(cls, config: "AppConfig") -> "BrowserHost":
        return cls(
            config.browser_policy,
            cdp_endpoint=config.browser_cdp_endpoint,
            profile_dir=config.browser_profile_dir,
            workspace=config.workspace_profile,
        )

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise ToolError("browser_cleanup")
            if self._thread is None:
                self._thread = Thread(target=self._run, name="Jarvis browser", daemon=True)
                self._thread.start()
        if not self._ready.wait(3):
            raise ToolError("browser_unavailable")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._session = BrowserSession(
            self.policy,
            headless=self.headless,
            cdp_endpoint=self.cdp_endpoint,
            profile_dir=self.profile_dir,
            workspace=self.workspace,
        )
        self._serial = asyncio.Lock()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(
                cast(asyncio.BaseEventLoop, loop).shutdown_default_executor(timeout=3)
            )
            loop.close()

    async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
        await context.checkpoint()
        owner_loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._start)
        assert self._loop is not None
        finished = Event()

        async def perform() -> BrowserResult:
            assert self._serial is not None and self._session is not None
            async with self._serial:

                async def preview(target: CursorTarget) -> None:
                    async def present() -> None:
                        if self.before_action is not None:
                            await self.before_action(target)

                    if self.before_action is not None:
                        await asyncio.wrap_future(
                            asyncio.run_coroutine_threadsafe(
                                present(),
                                owner_loop,
                            )
                        )

                self._session.before_action = preview if self.before_action is not None else None
                try:
                    async with asyncio.timeout(self.operation_timeout):
                        return await self._session.dispatch(command)
                except BaseException:
                    await self._session.cancel_pending()
                    if not self._session.pages:
                        await self._session.close()
                    raise

        future: concurrent.futures.Future[BrowserResult] = concurrent.futures.Future()
        tasks: list[asyncio.Task[BrowserResult]] = []

        def completed(task: asyncio.Task[BrowserResult]) -> None:
            finished.set()
            try:
                result = task.result()
            except BaseException as error:
                if not future.done():
                    future.set_exception(error)
            else:
                if not future.done():
                    future.set_result(result)

        def schedule() -> None:
            task = asyncio.create_task(perform())
            tasks.append(task)
            task.add_done_callback(completed)

        def cancel() -> None:
            for task in tasks:
                task.cancel()

        self._loop.call_soon_threadsafe(schedule)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            self._loop.call_soon_threadsafe(cancel)
            if not await asyncio.to_thread(finished.wait, 10):
                raise ToolError("browser_cleanup") from None
            raise
        except (TimeoutError, PlaywrightTimeoutError):
            raise ToolError("browser_timeout") from None
        except ToolError:
            raise
        except Exception:
            raise ToolError("browser_failure") from None

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._loop is None or self._session is None or self._thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._session.close(), self._loop)
        try:
            future.result(timeout=10)
        except (TimeoutError, concurrent.futures.CancelledError):
            raise ToolError("browser_cleanup") from None
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=4)
        if self._thread.is_alive():
            raise ToolError("browser_cleanup")
