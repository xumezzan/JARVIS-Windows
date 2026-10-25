"""Persistent, killable recognition owner shared by short-lived UI event loops."""

import asyncio
import concurrent.futures
import json
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from jarvis.voice.contracts import MAX_AUDIO_BYTES, VoiceError


class AudioHost:
    """Own one local model; optional gesture-bound capture shares it for streaming VAD."""

    def __init__(self, model: Path) -> None:
        self.model = model
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._lock = Lock()
        self._ready = Event()
        self._closed = False
        self.operation_timeout = 45.0
        self._serial: asyncio.Lock | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise VoiceError("voice_failed")
            if self._thread is None:
                self._thread = Thread(target=self._run, name="Jarvis audio", daemon=True)
                self._thread.start()
        if not self._ready.wait(3):
            raise VoiceError("timeout")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._serial = asyncio.Lock()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _dispose(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        if process.stdout is not None:
            while await process.stdout.read(65536):
                pass
        await process.wait()

    async def _message(
        self,
        request: dict[str, object],
        *,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        process = self._process
        assert process is not None and process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request).encode() + b"\n")
        await process.stdin.drain()
        release_task: asyncio.Task[None] | None = None

        async def release() -> None:
            assert released is not None and process.stdin is not None
            while not released.is_set():
                await asyncio.sleep(0.02)
            process.stdin.write(
                json.dumps(
                    {
                        "operation": "stop_record",
                        "capture_id": request["capture_id"],
                    }
                ).encode()
                + b"\n"
            )
            await process.stdin.drain()

        try:
            if released is not None:
                release_task = asyncio.create_task(release())
            limit = MAX_AUDIO_BYTES * 2 + 16384 if request.get("operation") == "record" else 32768
            raw = await process.stdout.readuntil(b"\n")
            if len(raw) > limit:
                raise VoiceError("recognition")
            data = json.loads(raw)
            if (
                request.get("operation") in {"record", "wake"}
                and isinstance(data, dict)
                and set(data) == {"ready"}
                and data["ready"] is True
            ):
                if ready is not None:
                    ready()
                raw = await process.stdout.readuntil(b"\n")
                if len(raw) > limit:
                    raise VoiceError("recognition")
                data = json.loads(raw)
        finally:
            if release_task is not None:
                release_task.cancel()
                await asyncio.gather(release_task, return_exceptions=True)
        if not isinstance(data, dict):
            raise VoiceError("recognition")
        if "error" in data:
            raise VoiceError(str(data["error"]))
        return data

    async def _perform(
        self,
        request: dict[str, object] | None,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        assert self._serial is not None
        async with self._serial:
            try:
                async with asyncio.timeout(self.operation_timeout):
                    if self._process is None:
                        # Shield process creation so cancellation can still kill and reap it.
                        launch = asyncio.create_task(
                            asyncio.create_subprocess_exec(
                                sys.executable,
                                "-I",
                                "-m",
                                "jarvis.platforms.audio",
                                "--recognition-host",
                                stdin=asyncio.subprocess.PIPE,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL,
                                limit=MAX_AUDIO_BYTES * 2 + 16384,
                                creationflags=0x08000000 if sys.platform == "win32" else 0,
                            )
                        )
                        try:
                            self._process = await asyncio.shield(launch)
                        except BaseException:
                            self._process = await launch
                            raise
                        await self._message({"operation": "load", "model": str(self.model)})
                    if (
                        request is not None
                        and request.get("operation") in {"record", "wake"}
                        and (released is None or released.is_set())
                    ):
                        raise asyncio.CancelledError  # Release during warmup must never open a mic.
                    return (
                        await self._message(request, released=released, ready=ready)
                        if request is not None
                        else {"ready": True}
                    )
            except BaseException:
                # Never retry a partially issued utterance; discard this pipe before the next call.
                await self._dispose()
                raise

    def _submit(
        self,
        request: dict[str, object] | None,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
    ) -> tuple[
        concurrent.futures.Future[dict[str, Any]], Event, list[asyncio.Task[dict[str, Any]]]
    ]:
        self._start()
        assert self._loop is not None
        future: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        finished = Event()
        tasks: list[asyncio.Task[dict[str, Any]]] = []

        def completed(task: asyncio.Task[dict[str, Any]]) -> None:
            self._tasks.discard(task)
            try:
                value = task.result()
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(value)
            finally:
                finished.set()

        def schedule() -> None:
            task = asyncio.create_task(self._perform(request, released, ready))
            self._tasks.add(task)
            tasks.append(task)
            task.add_done_callback(completed)

        with self._lock:
            if self._closed:
                raise VoiceError("voice_failed")
            self._loop.call_soon_threadsafe(schedule)
        return future, finished, tasks

    def warmup(self) -> None:
        future, _, _ = self._submit(None)

        # A missing model/dependency does not prevent text use or leak a native error.
        def consume(done: concurrent.futures.Future[dict[str, Any]]) -> None:
            with suppress(BaseException):
                done.result()

        future.add_done_callback(consume)

    async def call(
        self,
        request: dict[str, object],
        *,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if request.get("operation") in {"record", "wake"}:
            if released is None or released.is_set():
                raise asyncio.CancelledError
            request = request | {"capture_id": uuid4().hex}
        future, finished, tasks = self._submit(request, released, ready)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            assert self._loop is not None
            self._loop.call_soon_threadsafe(lambda: [task.cancel() for task in tasks])
            await asyncio.to_thread(finished.wait, 5)
            raise
        except TimeoutError:
            raise VoiceError("timeout") from None
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("recognition") from None

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._loop is None or self._thread is None:
            return

        async def close() -> None:
            for task in tuple(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self._dispose()

        future = asyncio.run_coroutine_threadsafe(close(), self._loop)
        try:
            future.result(timeout=6)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=3)
