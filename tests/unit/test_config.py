from pathlib import Path

import pytest

from jarvis.config import AppConfig, load_config


def test_non_secret_settings(tmp_path: Path) -> None:
    config = load_config({"JARVIS_DATA_DIR": str(tmp_path), "JARVIS_DEMO_DURATION_MS": "500"})
    assert config.data_dir == tmp_path
    assert config.demo_duration_ms == 500
    assert config.task_timeout_ms == 10000


@pytest.mark.parametrize("value", ["0", "-1", "30001", "not-a-number"])
def test_invalid_duration_is_rejected_without_echo(value: str) -> None:
    with pytest.raises(ValueError) as error:
        load_config({"JARVIS_DEMO_DURATION_MS": value})
    assert "not-a-number" not in str(error.value)


def test_config_limits_activity(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AppConfig(tmp_path, activity_limit=0)
