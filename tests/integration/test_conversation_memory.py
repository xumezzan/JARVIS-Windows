"""Two independent runners, restart recovery, consent, opt-out and finite storage failures."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_desktop_service import request, until
from tests.unit.test_planner import Scripted

from jarvis.config import AppConfig
from jarvis.core.planner.offline import call
from jarvis.desktop.service import DesktopService
from jarvis.memory.store import MemoryFailure
from jarvis.permissions.policies import Mode


@pytest.mark.asyncio
async def test_full_task_context_survives_runner_cleanup_restart_and_follows_up(
    tmp_path: Path,
) -> None:
    first = Scripted(
        [
            call("memory.scratchpad_write", {"key": "draft", "value": "Тот самый текст\nСтрока 2"}),
            *[call("system.info", {})] * 10,
            call("python.sandbox_eval", {"code": "1 + 1"}),
        ]
    )
    service = DesktopService(AppConfig(tmp_path), lambda value: None, provider=first)
    try:
        service.start("Открой блокнот документ", Mode.EXECUTE)
        runner = service.runner
        assert runner is not None
        # This exercises twelve real audited steps and a disposable process, not a
        # five-second latency contract. Wait for the production-bounded task itself.
        worker = service.worker
        assert worker is not None
        await asyncio.wait_for(asyncio.shield(worker), timeout=service.limits.total_seconds)
        assert service.worker is None
        assert service.task.state == "finished" and len(service.task.steps) == 12
        assert runner.scratchpad == {} and runner.task_memory.recent == ()
        saved = service.conversation.read()
        assert len(saved) == 1 and saved[0].scratchpad[0].value == "Тот самый текст\nСтрока 2"
        assert len(service.playbooks) == 1
        assert len(service.playbooks[0].route) == 11
        assert "Тот самый текст" not in service.playbooks[0].model_dump_json()
    finally:
        await service.shutdown()
    second = Scripted([])
    restarted = DesktopService(AppConfig(tmp_path), lambda value: None, provider=second)
    try:
        restarted.start("А теперь этот же текст в другом чате", Mode.SIMULATION)
        await until(lambda: restarted.worker is None)
        recalled = second.inputs[0].task_memory.recent[0]
        assert recalled.command == "Открой блокнот документ"
        assert recalled.scratchpad[0].value == "Тот самый текст\nСтрока 2"
        assert second.inputs[0].steps == () and not second.inputs[0].scratchpad
        assert restarted.conversation.read() == saved  # Simulation did not write history.
        second.inputs.clear()
        restarted.start("Пожалуйста открой блокноте документы", Mode.SIMULATION)
        await until(lambda: restarted.worker is None)
        assert len(second.inputs[0].task_memory.playbooks) == 1
        await restarted.handle(request("memory", memory={"operation": "experience_clear"}))
        await until(lambda: restarted.auxiliary is None)
        assert restarted.conversation.read() == () and restarted.playbooks == ()
        assert restarted.experience_store.load() == ((), ())
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_cloud_history_requires_separate_one_task_consent_and_voice_cannot_supply_it(
    tmp_path: Path,
) -> None:
    events: list[dict[str, object]] = []
    provider = Scripted([call("system.info", {})])
    service = DesktopService(AppConfig(tmp_path), events.append, provider=provider)
    try:
        service.start("Проверь систему", Mode.EXECUTE)
        # Consent is the contract here, not a five-second startup deadline on a
        # loaded Windows runner. Await the actual production-bounded audited task.
        worker = service.worker
        assert worker is not None
        await asyncio.wait_for(asyncio.shield(worker), timeout=service.limits.total_seconds)
        assert service.task.state == "finished"
        await service.handle(
            request("configure", settings={"provider": "openai", "model": "fixture"})
        )
        for consent in (False, True, False):
            await service.handle(
                request(
                    "run", command="Тот же текст", cloud_consent=True, conversation_consent=consent
                )
            )
            await until(lambda: service.worker is None)
            assert bool(provider.inputs[-1].task_memory.recent) is consent
        count = service.started_count
        await service.handle(request("run", command="Текст", conversation_consent=True))
        assert service.started_count == count
        assert events[-2]["error"] == "cloud_consent_required"
        assert not service._voice_start("Тот же текст", Mode.EXECUTE)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_opt_out_blocks_both_capture_and_injection(tmp_path: Path) -> None:
    provider = Scripted(
        [call("memory.scratchpad_write", {"key": "draft", "value": "Private text"})]
    )
    service = DesktopService(AppConfig(tmp_path), lambda value: None, provider=provider)
    try:
        await service.handle(request("configure", settings={"task_memory_enabled": False}))
        service.start("private command", Mode.EXECUTE)
        await until(lambda: service.worker is None)
        assert service.task.state == "finished"
        assert provider.inputs[0].task_memory.recent == ()
        assert service.experience_store.load() == ((), ())
        assert b"Private text" not in service.experience_store.path.read_bytes()
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_storage_failure_keeps_actual_task_result_and_clears_stale_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda value: None)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise MemoryFailure("storage")

    monkeypatch.setattr(service.experience_store, "load", broken)
    monkeypatch.setattr(service.experience_store, "remember", broken)
    try:
        service.start("проверь систему", Mode.EXECUTE)
        await until(lambda: service.worker is None)
        assert service.task.state == "finished" and service.completed_count == 1
        assert service.conversation.read() == ()
        assert "не удалось сохранить" in service.notice
        assert len(json.dumps(service.snapshot()).encode()) < 1048576
    finally:
        await service.shutdown()
