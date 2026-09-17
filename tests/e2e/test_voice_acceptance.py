"""Opt-in local hardware check. Recording still requires a real hold gesture in a visible UI."""

import os
import sys
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


def say(text: str, target: Path) -> Path:
    """Synthesised Russian speech, so the listener can be judged without a microphone."""
    import pyttsx3  # type: ignore[import-untyped]

    driver = {"win32": "sapi5", "darwin": "nsss"}.get(sys.platform, "espeak")
    engine = pyttsx3.init(driverName=driver)
    try:
        russian = [
            voice
            for voice in engine.getProperty("voices")
            if "ru" in str(voice.id).lower() or "russ" in str(voice.name).lower()
        ]
        assert russian, "Install a Russian system voice to run this check."
        engine.setProperty("voice", russian[0].id)
        engine.setProperty("rate", 170)
        engine.save_to_file(text, str(target))
        engine.runAndWait()
    finally:
        engine.stop()
    assert target.is_file(), "The system voice produced no audio file."
    return target


def decided(path: Path, model: Path) -> bool:
    """Run the real detector's rule over a clip: finalised segments only."""
    import wave

    from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore[import-untyped]

    from jarvis.platforms.audio import GRAMMAR, named

    SetLogLevel(-1)
    with wave.open(str(path), "rb") as source:
        rate, frames = source.getframerate(), source.getnframes()
        pcm = source.readframes(frames)
    recognizer = KaldiRecognizer(Model(model_path=str(model)), rate, GRAMMAR)
    recognizer.SetWords(True)
    for offset in range(0, len(pcm), 4000):
        if recognizer.AcceptWaveform(pcm[offset : offset + 4000]) and named(recognizer.Result()):
            return True
    return named(recognizer.FinalResult())


@pytest.mark.voice
def test_the_listener_hears_its_name_and_not_an_ordinary_sentence(tmp_path: Path) -> None:
    """The real local model, synthesised speech and no microphone at all.

    The measurement that shaped the detector: a restricted grammar keeps offering its only
    known word while a phrase is still in flight, so ordinary sentences show the name in a
    partial hypothesis and then finalise as "[unk]". Deciding on finalised segments alone
    separates the two cleanly.
    """
    model = Path(os.environ.get("JARVIS_VOSK_MODEL", ""))
    assert (model / "am" / "final.mdl").is_file(), "Set JARVIS_VOSK_MODEL to a local Russian model."
    assert decided(say("Джарвис", tmp_path / "name.wav"), model)
    assert decided(say("Джарвис проверь систему", tmp_path / "command.wav"), model)
    assert not decided(say("сегодня в городе дождь", tmp_path / "weather.wav"), model)
    assert not decided(say("вчера мы обсуждали проект с коллегами", tmp_path / "work.wav"), model)
