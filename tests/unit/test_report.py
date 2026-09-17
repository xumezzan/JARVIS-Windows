"""The assistant says what it did — from the snapshot it acted on, never from an answer."""

import json
from uuid import uuid4

import pytest

from jarvis.connectors.instants import parse
from jarvis.core.planner.contracts import PlanResult, Step
from jarvis.core.report import spoken, written
from jarvis.observability.audit import ErrorCode
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Status

SECRET = "PRIVATE_BODY_SENTINEL"


def step(
    tool: str,
    arguments: dict[str, object] | None = None,
    *,
    status: Status = Status.SUCCESS,
    error: ErrorCode = ErrorCode.NONE,
    effects: bool = False,
) -> Step:
    return Step(
        tool,
        Outcome(uuid4(), status, error, effects, result_json='{"answer": "' + SECRET + '"}'),
        json.dumps(arguments or {}, ensure_ascii=False),
    )


def letter(subject: str = "Перенос встречи") -> dict[str, object]:
    return {
        "message": {
            "to": ["ivan@example.com", "anna@example.com"],
            "subject": subject,
            "body": SECRET,
        }
    }


def test_a_sent_letter_is_reported_with_what_it_was_about() -> None:
    result = PlanResult("finished", (step("outlook.send", letter()),))
    lines = written(result)
    assert lines[0] == "Готово."
    assert lines[1] == "Отправил письмо «Перенос встречи» на ivan@example.com и ещё 1."
    # Aloud the address is left out: the local Russian voice cannot pronounce it anyway.
    said = spoken(result)
    assert "Отправил письмо «Перенос встречи»." in said and "example.com" not in said


def test_a_created_meeting_names_its_subject_and_time() -> None:
    start = "2026-09-17T13:00:00Z"
    result = PlanResult(
        "finished",
        (step("calendar.create", {"event": {"subject": "Планёрка", "start": start}}),),
    )
    local = parse(start).astimezone()
    assert (
        written(result)[1] == f"Создал встречу «Планёрка» на {local.day} сентября, {local:%H:%M}."
    )


def test_files_and_applications_are_named_by_what_the_owner_would_call_them() -> None:
    result = PlanResult(
        "finished",
        (
            step(
                "files.write_text", {"path": "C:\\Users\\o\\Документы\\Отчёт.txt", "text": SECRET}
            ),
            step("files.recycle", {"path": "D:/Документы/Смета.docx"}),
            step("windows.open_app", {"app": "калькулятор"}),
            step("browser.open", {"url": "https://example.com/very/long/path?q=secret"}),
        ),
    )
    lines = written(result)[1:]
    assert lines == (
        "Записал файл Отчёт.txt.",
        "Переместил в корзину Смета.docx.",
        "Открыл калькулятор.",
        "Открыл в браузере example.com.",
    )
    # A query string is not part of "what I did", and neither is the text of a file.
    assert SECRET not in " ".join(lines) and "q=secret" not in " ".join(lines)


def test_what_was_typed_is_never_repeated_back() -> None:
    result = PlanResult(
        "finished",
        (
            step(
                "windows.type_text",
                {"target": {"app": "блокнот", "title": "Безымянный"}, "text": SECRET},
            ),
        ),
    )
    assert written(result)[1] == "Напечатал текст в блокнот."
    assert SECRET not in " ".join(written(result))


def test_an_unconfirmed_effect_is_reported_as_unconfirmed() -> None:
    result = PlanResult(
        "timeout",
        (
            step(
                "outlook.send",
                letter(),
                status=Status.TIMEOUT,
                error=ErrorCode.TIMEOUT,
                effects=True,
            ),
        ),
        "timeout",
    )
    lines = written(result)
    assert lines[0] == "Не уложился по времени."
    assert lines[1].startswith("Отправил письмо «Перенос встречи»") and "проверьте" in lines[1]
    assert lines[-1] == "Уже выданные действия не отменяются; проверьте их результат."
    assert "не отменяются" in spoken(result)


def test_a_refusal_says_plainly_why_nothing_happened() -> None:
    result = PlanResult(
        "error",
        (step("outlook.send", letter(), status=Status.DENIED, error=ErrorCode.APPROVAL),),
        "approval_invalid",
    )
    assert written(result)[1] == (
        "Не отправил письмо «Перенос встречи» на ivan@example.com и ещё 1 — подтверждения не было."
    )


def test_a_task_that_only_looked_says_so() -> None:
    result = PlanResult("finished", (step("outlook.list", {"folder": "inbox"}),))
    assert written(result) == ("Готово.", "Ничего не менял — только посмотрел.")
    assert "только посмотрел" in spoken(result)


def test_a_simulation_claims_nothing() -> None:
    result = PlanResult("simulated", (step("outlook.send", letter(), status=Status.SIMULATED),))
    lines = written(result)
    assert lines == (
        "Это была симуляция: ничего на самом деле не выполнялось.",
        "Шагов в плане: 1.",
    )
    assert "Отправил" not in " ".join(lines) and "симуляция" in spoken(result)


def test_nothing_a_tool_or_a_model_said_reaches_the_report() -> None:
    result = PlanResult("finished", (step("unknown.tool", {"anything": SECRET}),))
    # An unknown tool is not invented into a sentence, and its answer is not quoted.
    assert written(result) == ("Готово.", "Ничего не менял — только посмотрел.")
    assert SECRET not in spoken(result)


def test_long_values_are_bounded() -> None:
    result = PlanResult("finished", (step("outlook.send", letter("П" * 500)),))
    line = written(result)[1]
    assert len(line) < 200 and "П" * 61 not in line


@pytest.mark.parametrize("status", ["finished", "no_action", "error", "cancelled", "limit"])
def test_every_ending_has_words_of_its_own(status: str) -> None:
    said = spoken(PlanResult(status))  # type: ignore[arg-type]
    assert said and said[0].isupper() and said.endswith(".")


def test_speech_stays_short_when_a_lot_happened() -> None:
    steps = tuple(
        step("files.open", {"path": f"D:/Документы/Файл{index}.txt"}) for index in range(5)
    )
    said = spoken(PlanResult("finished", steps))
    assert said.count("Открыл файл") == 3 and "И ещё действий: 2." in said
    assert len(written(PlanResult("finished", steps))) == 6
