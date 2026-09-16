from pathlib import Path

import pytest

from jarvis.config import AppConfig, load_config


def test_non_secret_settings(tmp_path: Path) -> None:
    config = load_config({"JARVIS_DATA_DIR": str(tmp_path), "JARVIS_TASK_TIMEOUT_MS": "500"})
    assert config.data_dir == tmp_path
    assert config.task_timeout_ms == 500
    assert config.activity_limit == 200


@pytest.mark.parametrize("value", ["0", "-1", "60001", "not-a-number"])
def test_invalid_duration_is_rejected_without_echo(value: str) -> None:
    with pytest.raises(ValueError) as error:
        load_config({"JARVIS_TASK_TIMEOUT_MS": value})
    assert "not-a-number" not in str(error.value)


def test_config_limits_activity(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AppConfig(tmp_path, activity_limit=0)


def test_browser_origins_are_explicit_nonsecret_settings() -> None:
    config = load_config({"JARVIS_BROWSER_ORIGINS": "https://example.com,https://docs.python.org"})
    assert config.browser_origins == ("https://example.com", "https://docs.python.org")
    assert load_config({"JARVIS_BROWSER_ORIGINS": ""}).browser_origins == ()
    for value in (
        "http://127.0.0.1:8000",
        "https://user:SECRET@example.com",
        "https://example.com/path",
    ):
        with pytest.raises(ValueError) as error:
            load_config({"JARVIS_BROWSER_ORIGINS": value})
        assert "SECRET" not in str(error.value)
