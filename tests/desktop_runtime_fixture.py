"""Electron integration fixture. No device, credential, network or real effect adapter."""

import asyncio
import base64
import json
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.desktop import service
from jarvis.desktop.system_tools import DesktopOfflineProvider
from jarvis.desktop.voice import DesktopVoice
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.contracts import AudioClip, Transcript
from jarvis.voice.host import AudioHost
from jarvis.voice.local import VoskRecognizer


class Empty(ToolModel):
    pass


class FixtureProvider:
    def __init__(self) -> None:
        self.offline = DesktopOfflineProvider()

    async def propose(self, data: PlannerInput) -> Proposal:
        if data.command == "clarify fixture":
            return (
                Proposal(kind="finish")
                if data.answers
                else Proposal(kind="clarify", question="Какое окно выбрать?")
            )
        if data.command not in {"confirm fixture", "overlay fixture"}:
            return await self.offline.propose(data)
        if not data.steps:
            return Proposal(
                kind="call",
                tool="test.overlay" if data.command == "overlay fixture" else "test.effect",
            )
        return Proposal(kind="finish", spoken_summary="Проверка завершена.")


class FixtureVoice:
    local_only = True

    async def record(
        self, released: Event, ready: Callable[[], None], listen: bool = False
    ) -> AudioClip:
        ready()
        while not released.is_set():
            await asyncio.sleep(0.005)
        return AudioClip(b"\0\0" * 3200)

    async def transcribe(self, clip: AudioClip) -> Transcript:
        return Transcript("проверь систему", 0.99)

    async def speak(self, text: str) -> None:
        await asyncio.sleep(0.05)


class FixtureWakeHost(AudioHost):
    def warmup(self) -> None:
        pass

    async def call(
        self,
        request: dict[str, object],
        *,
        released: Event | None = None,
        ready: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        operation = request["operation"]
        if ready:
            ready()
        if operation in {"wake", "record"}:
            assert released is not None
            marker = self.model / ("activate-wake" if operation == "wake" else "finish-command")
            while not marker.exists() and not released.is_set():
                await asyncio.sleep(0.01)
            if marker.exists():
                marker.unlink()
            return (
                {"wake": not released.is_set()}
                if operation == "wake"
                else {"pcm": base64.b64encode(bytes(6400)).decode()}
            )
        return {"text": "проверь систему", "confidence": 0.99}


class FixtureWakeRecognizer(VoskRecognizer):
    def __init__(self, model: Path) -> None:
        self.model = model
        self.host = FixtureWakeHost(model)


OriginalService = service.DesktopService


class FixtureService(OriginalService):
    def __init__(self, config: AppConfig, write: Callable[[dict[str, object]], None]) -> None:
        super().__init__(config, write, provider=FixtureProvider())
        registry = ToolRegistry()

        async def check(args: Empty, context: ExecutionContext) -> bool:
            return True

        async def execute(args: Empty, context: ExecutionContext) -> Empty:
            (self.config.data_dir / "effect.json").write_text(json.dumps({"performed": True}))
            return Empty()

        async def verify(args: Empty, result: Empty, context: ExecutionContext) -> bool:
            return (self.config.data_dir / "effect.json").is_file()

        registry.register(
            ToolSpec(
                "test.effect", "Fixture effect", Risk.CONFIRM, Empty, Empty, check, execute, verify
            )
        )

        async def overlay(args: Empty, context: ExecutionContext) -> Empty:
            from jarvis.tools.windows import Rectangle

            await self._highlight(Rectangle(left=80, top=100, right=280, bottom=170))
            (self.config.data_dir / "target-presented").touch()
            await asyncio.sleep(3)
            await context.checkpoint()
            return Empty()

        registry.register(
            ToolSpec(
                "test.overlay",
                "Overlay fixture",
                Risk.SAFE,
                Empty,
                Empty,
                check,
                overlay,
                lambda args, result, context: check(args, context),
            )
        )
        # Include system.info as well, keeping the existing offline path for voice.
        from jarvis.desktop.system_tools import desktop_registry

        system_registry, _ = desktop_registry(self.config.data_dir)
        system_spec = system_registry.get("system.info")
        assert system_spec is not None
        registry._tools["system.info"] = system_spec
        store = ApprovalStore()
        self.registry = registry
        self.engine = PermissionEngine(registry, store, self.audit)
        self._authority = store.take_authority(self.audit.approved)
        fixture = FixtureVoice()
        self.voice = DesktopVoice(
            self.publish,
            self.transcript,
            self._voice_start,
            self.cancel,
            recorder=fixture,
            recognizer=FixtureWakeRecognizer(config.data_dir)
            if os.environ.get("JARVIS_VOICE_WAKE_WORD") == "1"
            else fixture,
            speaker=fixture,
        )


if __name__ == "__main__":
    service.DesktopService = FixtureService  # type: ignore[misc, assignment]
    raise SystemExit(service.main())
