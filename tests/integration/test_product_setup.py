"""Persistent preferences and an idle browser switch without accounts or hardware."""

import json
from pathlib import Path

import pytest
from tests.integration.test_desktop_service import request

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig, load_config
from jarvis.desktop.service import DesktopService
from jarvis.desktop.settings import UiSettings, UiSettingsStore
from jarvis.permissions.policies import Mode
from jarvis.platforms.workspace_browser import ENDPOINT
from jarvis.security.browser_policy import BrowserMode
from jarvis.tools.base import ExecutionContext
from jarvis.tools.browser import BrowserCommand, BrowserResult
from jarvis.workspace.store import WorkspaceFailure


@pytest.mark.asyncio
async def test_preferences_survive_restart_without_consent_or_background_watchers(
    tmp_path: Path,
) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        await service.handle(
            request(
                "configure",
                settings={
                    "provider": "openai",
                    "model": "test-model",
                    "auto_start": True,
                    "wake_word": True,
                    "auto_stop_on_silence": True,
                    "watch_clipboard": True,
                },
            )
        )
        stored = json.loads((tmp_path / "ui_settings.json").read_bytes())
        assert stored["planner_model"] == "test-model" and stored["wake_word"] is True
        assert "watch_clipboard" not in stored and "cloud_consent" not in stored
        assert not service.voice.wake_allowed and service.voice.wake_task is None
    finally:
        await service.shutdown()
    restored = DesktopService(load_config({"JARVIS_DATA_DIR": str(tmp_path)}), lambda event: None)
    try:
        assert restored.settings.auto_start and restored.settings.wake_word
        assert restored.settings.auto_stop_on_silence and not restored.settings.watch_clipboard
        assert not restored.start("test", Mode.SIMULATION, cloud_consent=False)
        assert restored.started_count == 0
    finally:
        await restored.shutdown()


def test_ui_settings_are_bounded_and_do_not_accept_authority(tmp_path: Path) -> None:
    store = UiSettingsStore(tmp_path)
    assert store.load() == UiSettings() and not store.path.exists()
    for value in ({"cloud_consent": True}, {"wake_word": "true"}, {"planner_model": "x\nsecret"}):
        store.path.write_text(json.dumps(value))
        with pytest.raises(WorkspaceFailure, match="workspace_invalid"):
            store.load()
    store.path.write_bytes(b" " * 262145)
    with pytest.raises(WorkspaceFailure, match="workspace_limit"):
        store.load()


@pytest.mark.asyncio
async def test_browser_switch_rebuilds_catalog_and_preserves_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    old_engine = service.engine

    class FakeHost(BrowserHost):
        async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
            assert command.operation == "get_tabs"
            await context.checkpoint()
            return BrowserResult()

    monkeypatch.setattr(service.browser_launcher, "launch", lambda: ENDPOINT)
    monkeypatch.setattr(
        BrowserHost,
        "from_config",
        staticmethod(
            lambda config: FakeHost(
                config.browser_policy,
                cdp_endpoint=config.browser_cdp_endpoint,
                workspace=config.workspace_profile,
            )
        ),
    )
    try:
        authority = service._authority
        await service.handle(request("workspace_browser"))
        assert service.workspace_browser["state"] == "connected"
        assert service.engine is not old_engine and service._authority is authority
        assert (
            service.host.cdp_endpoint == ENDPOINT
            and service.config.browser_mode is BrowserMode.WORKSPACE
        )
        assert "browser.open_workspace_app" in {row["name"] for row in service.registry.discover()}
        stored = UiSettingsStore(tmp_path).load()
        assert stored.managed_browser and stored.browser_mode == "authenticated_workspace"
        assert service.prompt is None and service.started_count == 0
        assert "browser.get_tabs" in " ".join(service.audit.recent())
        config = load_config({"JARVIS_DATA_DIR": str(tmp_path), "JARVIS_BROWSER_MODE": "strict"})
        assert config.browser_mode is BrowserMode.STRICT
        assert not config.file_policy.inside(tmp_path / "workspace_chrome_profile/Cookies")
        assert not config.file_policy.inside(tmp_path / "ui_settings.json")
    finally:
        await service.shutdown()
