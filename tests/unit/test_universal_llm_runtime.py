"""Arbitrary user tasks always reach the configured universal LLM planner."""

from pathlib import Path

import pytest
from tests.integration.test_desktop_service import request, until

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.openai_provider import INSTRUCTIONS, OpenAIProvider
from jarvis.desktop.protocol import Settings
from jarvis.desktop.service import DesktopService
from jarvis.desktop.settings import UiSettings, UiSettingsStore


@pytest.mark.parametrize("saved", [{}, {"provider": "offline", "planner_model": ""}])
def test_legacy_settings_select_openai_without_changing_consent(
    tmp_path: Path, saved: dict[str, str]
) -> None:
    settings = UiSettings.model_validate(saved)
    assert settings.provider == "openai" and settings.planner_model == "gpt-4o"
    UiSettingsStore(tmp_path).save(settings)
    assert UiSettingsStore(tmp_path).load() == settings
    assert Settings.model_validate({"provider": "offline"}).provider == "openai"
    assert not (tmp_path / "cloud_task_policy.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "Собери таблицу в неизвестной программе お仕事 2027",
        "Рассчитай выражение, перенеси результат и проверь оформление",
        "открой сайт example.test",
    ],
)
async def test_unfamiliar_commands_are_passed_unchanged_to_openai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    calls: list[tuple[str, str]] = []

    class FixtureOpenAI:
        def __init__(self, model: str) -> None:
            self.model = model

        async def propose(self, data: PlannerInput) -> Proposal:
            calls.append((self.model, data.command))
            return Proposal(kind="finish")

    monkeypatch.setattr("jarvis.desktop.service.OpenAIProvider", FixtureOpenAI)
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        await service.handle(request("run", command=command, cloud_consent=True))
        await until(lambda: service.worker is None)
        assert calls == [("gpt-4o", command)]
        assert service.prompt is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_missing_openai_key_returns_actionable_error_without_recipe_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MissingKey:
        def __init__(self, model: str) -> None:
            assert model == "gpt-4o"

        async def propose(self, data: PlannerInput) -> Proposal:
            raise ProviderError("credentials")

    async def forbidden_offline(self: OfflineProvider, data: PlannerInput) -> Proposal:
        pytest.fail("A user task must never fall back to an offline recipe")

    monkeypatch.setattr("jarvis.desktop.service.OpenAIProvider", MissingKey)
    monkeypatch.setattr(OfflineProvider, "propose", forbidden_offline)
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        await service.handle(request("run", command="открой любое приложение", cloud_consent=True))
        await until(lambda: service.worker is None)
        assert service.task.state == "error" and not service.task.steps
        assert "Ключ OpenAI недоступен" in service.notice
        assert "jarvis.security.credentials set openai" in service.notice
        assert "credentials" in service.task.summary
    finally:
        await service.shutdown()


def test_universal_instructions_use_atomic_tools_and_real_context_flag() -> None:
    assert "Ты — универсальный оператор ПК" in INSTRUCTIONS
    assert "any application name or installed path" in INSTRUCTIONS
    assert "clipboard_get/clipboard_set" in INSTRUCTIONS
    assert "notepad opens" not in INSTRUCTIONS
    assert OpenAIProvider("gpt-4o").needs_desktop_context
