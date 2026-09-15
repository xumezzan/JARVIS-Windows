"""Opt-in local hardware check. Recording still requires a real hold gesture in a visible UI."""

import os
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.ui.planner_window import PlannerWindow


@pytest.mark.voice
def test_russian_voice_review_simulation_and_speech(qtbot: QtBot, tmp_path: Path) -> None:
    assert os.environ.get("QT_QPA_PLATFORM") in {"windows", "cocoa"}, "Use a native Qt desktop."
    model = Path(os.environ.get("JARVIS_VOSK_MODEL", ""))
    assert (model / "am" / "final.mdl").is_file(), "Set JARVIS_VOSK_MODEL to a local Russian model."
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.voice.model_path.setText(str(model))
    window.voice.speech_enabled.setChecked(True)
    window.voice.status.setText(
        "Проверка: удерживайте кнопку, дождитесь записи и скажите «проверь систему дважды». "
        "Отпустите, проверьте/исправьте текст и нажмите запуск. Есть 90 секунд."
    )
    window.show()
    try:
        with qtbot.waitSignal(window.task_finished, timeout=90000) as outcome:
            pass  # The user records, reviews and submits; the test never synthesizes a gesture.
        assert outcome.args == ["simulated"]
        assert window.output.toPlainText().count("local.check:") == 2
        qtbot.waitUntil(lambda: window.voice.worker is None, timeout=30000)
        assert "Озвучивание завершено" in window.voice.status.text()
    finally:
        window.shutdown()
