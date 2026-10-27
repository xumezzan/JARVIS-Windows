"""Standing ordinary-task permission and exact, one-use serious-task cloud review."""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from tests.unit.test_planner import Scripted

from jarvis.config import AppConfig
from jarvis.core.planner.offline import call
from jarvis.desktop.cloud_consent import CloudPolicy, CloudPolicyStore, review_reasons
from jarvis.desktop.protocol import CloudReviewReply, Request
from jarvis.desktop.service import DesktopService
from jarvis.desktop.settings import UiSettings, UiSettingsStore
from jarvis.permissions.policies import Mode


@pytest.mark.parametrize(
    "command",
    [
        "Оплати счёт",
        "Отправь сообщение Ивану",
        "Удали старые файлы",
        "Перезапиши отчёт",
        "Измени настройки аккаунта",
        "Сначала прочитай, затем сравни, потом проверь, далее составь отчёт",
    ],
)
def test_review_policy_matches_owner_categories(command: str) -> None:
    assert review_reasons(command)


@pytest.mark.parametrize("command", ["Проверь систему", "Открой калькулятор", "Найди документ"])
def test_ordinary_tasks_do_not_need_review(command: str) -> None:
    assert not review_reasons(command)


def test_standing_policy_does_not_change_legacy_ui_settings(tmp_path: Path) -> None:
    store = UiSettingsStore(tmp_path)
    store.save(UiSettings(provider="openai", planner_model="fixture"))
    before = (tmp_path / "ui_settings.json").read_bytes()
    assert not CloudPolicyStore(tmp_path).load().routine_cloud_consent
    CloudPolicyStore(tmp_path).save(CloudPolicy(routine_cloud_consent=True))
    assert (tmp_path / "ui_settings.json").read_bytes() == before
    assert store.load().planner_model == "fixture"


def service_for(path: Path, provider: Scripted) -> DesktopService:
    UiSettingsStore(path).save(UiSettings(provider="openai", planner_model="fixture"))
    CloudPolicyStore(path).save(CloudPolicy(routine_cloud_consent=True))
    return DesktopService(AppConfig(path), lambda event: None, provider=provider)


async def finished(service: DesktopService) -> None:
    worker = service.worker
    assert worker is not None
    await asyncio.wait_for(asyncio.shield(worker), service.limits.total_seconds)


async def until_review(service: DesktopService) -> None:
    async with asyncio.timeout(10):
        while service.cloud_review is None:
            await asyncio.sleep(0.005)


def answer(identifier: str, current: bool = True, history: bool = False) -> Request:
    return Request(
        id=uuid4(),
        operation="cloud_review",
        cloud_review=CloudReviewReply(
            id=identifier,
            allow_current=current,
            allow_history=history,
        ),
    )


@pytest.mark.asyncio
async def test_ordinary_task_uses_saved_permission_without_a_question(tmp_path: Path) -> None:
    provider = Scripted([call("system.info", {})])
    service = service_for(tmp_path, provider)
    try:
        assert service.start("Проверь систему", Mode.EXECUTE)
        await finished(service)
        assert provider.inputs and service.task.state == "finished"
        assert service.cloud_review is None and service.prompt is None
        assert CloudPolicyStore(tmp_path).load().routine_cloud_consent
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_history", [False, True])
async def test_serious_task_waits_and_history_is_one_task_only(
    tmp_path: Path, allow_history: bool
) -> None:
    provider = Scripted([call("system.info", {})])
    service = service_for(tmp_path, provider)
    try:
        assert service.start("Проверь систему", Mode.EXECUTE)
        await finished(service)
        provider.inputs.clear()
        assert not service.start("Отправь сообщение", Mode.EXECUTE, cloud_consent=True)
        review = service.cloud_review
        assert review is not None and not provider.inputs and service.worker is None
        await service.handle(answer(review.id, history=allow_history))
        await finished(service)
        assert bool(provider.inputs[0].task_memory.recent) is allow_history
        count = len(provider.inputs)
        await service.handle(answer(review.id, history=True))
        assert len(provider.inputs) == count and service.worker is None
        assert not service.start("Отправь второе сообщение", Mode.EXECUTE)
        assert service.cloud_review is not None and service.cloud_review.id != review.id
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["deny", "cancel", "expired", "changed", "wrong_id"])
async def test_unaccepted_or_changed_review_never_calls_provider(
    tmp_path: Path, scenario: str
) -> None:
    provider = Scripted([])
    service = service_for(tmp_path, provider)
    try:
        assert not service.start("Удали файлы", Mode.EXECUTE)
        pending = service.cloud_review
        assert pending is not None
        if scenario == "cancel":
            service.cancel()
        else:
            if scenario == "expired":
                service.cloud_review = replace(pending, expires_at=0)
            if scenario == "changed":
                service.settings = service.settings.model_copy(update={"model": "other-model"})
            await service.handle(
                answer(
                    uuid4().hex if scenario == "wrong_id" else pending.id,
                    current=scenario != "deny",
                )
            )
        assert not provider.inputs and service.worker is None and service.cloud_review is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_long_task_pauses_before_the_fifth_provider_call(tmp_path: Path) -> None:
    provider = Scripted([call("system.info", {}) for _ in range(5)])
    service = service_for(tmp_path, provider)
    try:
        assert service.start("Проверь систему", Mode.EXECUTE)
        await until_review(service)
        assert len(provider.inputs) == 4
        review = service.cloud_review
        assert review is not None and review.future is not None
        await service.handle(answer(review.id))
        await finished(service)
        assert len(provider.inputs) == 6 and service.task.state == "finished"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_cancel_during_long_task_review_sends_no_more_context(tmp_path: Path) -> None:
    provider = Scripted([call("system.info", {}) for _ in range(5)])
    service = service_for(tmp_path, provider)
    try:
        assert service.start("Проверь систему", Mode.EXECUTE)
        await until_review(service)
        service.cancel()
        await finished(service)
        assert len(provider.inputs) == 4 and service.task.state == "cancelled"
    finally:
        await service.shutdown()
