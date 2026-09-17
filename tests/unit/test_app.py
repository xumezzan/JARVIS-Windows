"""Check the installed CLI contract without implementing future tools."""

from importlib.metadata import version
from pathlib import Path

import pytest

from jarvis.app import main


def test_help_explains_scope(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    assert "simulated offline command" in capsys.readouterr().out


def test_invalid_configuration_fails_before_gui(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JARVIS_TASK_TIMEOUT_MS", "invalid")
    assert main([]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_unwritable_log_location_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("existing file")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(occupied))
    assert main([]) == 2
    output = capsys.readouterr().err
    assert "cannot create its local log" in output
    assert str(occupied) not in output


def test_version_matches_installed_distribution(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"jarvis {version('jarvis-windows')}"


def test_commands_are_not_silently_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["open", "notepad"])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_application_has_an_icon_of_its_own() -> None:
    """A program the owner cannot find in the taskbar is a program that is not there."""
    from jarvis.ui.theme import ICON

    data = ICON.read_bytes()
    assert ICON.name == "jarvis.ico" and data[:4] == b"\x00\x00\x01\x00"
    count = int.from_bytes(data[4:6], "little")
    sizes = {data[6 + index * 16] or 256 for index in range(count)}
    # Windows picks a different size for the taskbar, the Start menu and Alt-Tab.
    assert {16, 32, 48, 256} <= sizes
