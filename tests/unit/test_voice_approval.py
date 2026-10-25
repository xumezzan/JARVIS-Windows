"""Only a detail of the exact snapshot, spoken as a short answer, confirms anything."""

import json
from uuid import uuid4

import pytest

from jarvis.permissions.approvals import Action
from jarvis.permissions.policies import Mode, Risk
from jarvis.voice.approval import Confirmation, ControlDetail, check, control_detail
from jarvis.voice.contracts import Transcript

DETAIL = ControlDetail("Файл будет перемещён в корзину", "слово из имени файла", "Отчёт")


def detail(
    tool: str, payload: dict[str, object], risk: Risk = Risk.CONFIRM
) -> ControlDetail | None:
    action = Action(uuid4(), tool, json.dumps(payload, ensure_ascii=False), risk, Mode.EXECUTE)
    return control_detail(action)


@pytest.mark.parametrize(
    ("tool", "payload", "word"),
    [
        ("files.recycle", {"path": "C:/Users/o/Documents/квартальный отчёт.docx"}, "квартальный"),
        ("files.recycle", {"path": "C:\\Users\\o\\Документы\\смета.txt"}, "смета"),
        ("outlook.send", {"message": {"subject": "Договор на подпись"}}, "договор"),
        ("calendar.create", {"event": {"subject": "Планёрка команды"}}, "планёрка"),
        ("calendar.update", {"event": {"subject": "Перенос демонстрации"}}, "демонстрации"),
        ("calendar.cancel", {"comment": "Переносим на пятницу"}, "переносим"),
        ("local.append_message", {"subject": "Тест разрешений"}, "разрешений"),
    ],
)
def test_the_detail_is_taken_from_the_snapshot(
    tool: str, payload: dict[str, object], word: str
) -> None:
    found = detail(tool, payload)
    assert found is not None and found.word.casefold() == word
    # It is said and shown together with what it belongs to, never as a bare word.
    assert found.word in found.request and found.action in found.request


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        # A Latin name is not something the local Russian recogniser can return.
        ("files.recycle", {"path": "C:/Users/o/Documents/report.docx"}),
        ("outlook.send", {"message": {"subject": ""}}),
        ("outlook.send", {"message": {"subject": "да, ок"}}),
        ("calendar.cancel", {"comment": ""}),
        ("calendar.create", {"event": {}}),
        # A tool nobody has chosen a detail for keeps the button as its only channel.
        ("outlook.save_draft", {"message": {"subject": "Договор на подпись"}}),
    ],
)
def test_without_a_speakable_detail_there_is_no_voice_channel(
    tool: str, payload: dict[str, object]
) -> None:
    assert detail(tool, payload) is None


@pytest.mark.parametrize("risk", [level for level in Risk if level is not Risk.CONFIRM])
def test_only_a_level_that_is_confirmed_at_all_is_asked_about(risk: Risk) -> None:
    assert detail("files.recycle", {"path": "D:/Документы/смета.txt"}, risk) is None


@pytest.mark.parametrize(
    ("text", "confidence", "verdict"),
    [
        ("отчёт", 0.99, Confirmation.CONFIRMED),
        ("Отчет.", 0.99, Confirmation.CONFIRMED),  # Case, ё and punctuation are not the point.
        ("слово отчёт", 0.99, Confirmation.CONFIRMED),
        ("да", 0.99, Confirmation.MISMATCH),
        ("да, подтверждаю", 0.99, Confirmation.MISMATCH),
        ("хорошо давай", 0.99, Confirmation.MISMATCH),
        ("договор", 0.99, Confirmation.MISMATCH),
        ("отчёт", 0.5, Confirmation.UNCERTAIN),  # Unsure recognition confirms nothing.
        ("отмена", 0.99, Confirmation.REFUSED),
        ("стоп", 0.99, Confirmation.REFUSED),
    ],
)
def test_only_the_named_detail_confirms(text: str, confidence: float, verdict: str) -> None:
    assert check(DETAIL, Transcript(text, confidence)) is verdict


def test_the_prompt_itself_is_not_an_answer() -> None:
    # Whatever repeats the assistant — a television, another person, the microphone catching
    # the tail of its own prompt — produces the whole sentence, not the short answer to it.
    assert check(DETAIL, Transcript(DETAIL.request, 0.99)) is Confirmation.MISMATCH
