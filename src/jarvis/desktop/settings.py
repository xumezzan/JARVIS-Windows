"""Explicit non-secret UI preferences; no consent, credentials or approval state."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from jarvis.core.planner.identifiers import valid_model
from jarvis.tools.base import ToolModel
from jarvis.workspace.models import WorkspaceBrowserMode
from jarvis.workspace.store import JsonSettingsStore


class UiSettings(ToolModel):
    provider: Literal["offline", "openai"] = "offline"
    planner_model: str = Field(default="", max_length=100)
    auto_start: bool = False
    speech_enabled: bool = False
    task_memory_enabled: bool = True
    wake_word: bool = False
    auto_stop_on_silence: bool = False
    browser_mode: WorkspaceBrowserMode | None = None
    vosk_model_path: str = Field(default="", max_length=2000, repr=False)
    managed_browser: bool = False

    @field_validator("planner_model")
    @classmethod
    def model_name(cls, value: str) -> str:
        if value and not valid_model(value):
            raise ValueError("Invalid model identifier.")
        return value


class UiSettingsStore(JsonSettingsStore[UiSettings]):
    """Reuse the bounded regular-file/atomic-write implementation with a strict schema."""

    def __init__(self, data_dir: Path):
        super().__init__(data_dir / "ui_settings.json", UiSettings)
