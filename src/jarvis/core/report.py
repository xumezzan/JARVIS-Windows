"""What the assistant actually did, said the way a person would say it.

Until now the end of a task sounded like a machine reporting on itself: "the planner
finished, two actions verified". That is a status, not an answer to "what did you do", and
it is a large part of why the assistant still felt like a tool. It never told the owner
about the owner's own work.

The sentences here are assembled by trusted code from two things: the action snapshot the
owner caused - the same one they saw in the confirmation - and the outcome the engine
verified afterwards. Nothing a model wrote and nothing a service answered gets in. A page's
text, a letter's body, a reply from Graph are untrusted data and stay where they are, and
every field that does appear is short and bounded.

Two versions leave here. The written one may name an address, a path or a host, because the
owner reads it on their own screen. The spoken one keeps only what a Russian voice can
pronounce - for the same reason the voice confirmation asks for a Russian word.

An unresolved effect is reported as unresolved. "I sent it, but I could not confirm it" is
the sentence that makes the rest of the report worth believing.
"""

import json
from collections.abc import Callable

from jarvis.connectors.instants import parse
from jarvis.core.planner.contracts import PlanResult, Step
from jarvis.observability.audit import ErrorCode
from jarvis.permissions.policies import Status

MAX_DETAIL = 60
MAX_DEEDS = 12
SPOKEN_DEEDS = 3

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

HEADLINE: dict[str, str] = {
    "finished": "Готово.",
    "simulated": "Это была симуляция: ничего на самом деле не выполнялось.",
    "no_action": "Я ничего не сделал.",
    "error": "Остановился на ошибке.",
    "cancelled": "Остановился по вашей команде.",
    "timeout": "Не уложился по времени.",
    "limit": "Дошёл до предела шагов или уточнений.",
}

# Finite, plain reasons. The engine's own code stays in the technical summary next to this.
REASON: dict[ErrorCode, str] = {
    ErrorCode.APPROVAL: "подтверждения не было",
    ErrorCode.POLICY: "это запрещено",
    ErrorCode.PRECONDITION: "цель изменилась",
    ErrorCode.TARGET_CHANGED: "цель изменилась",
    ErrorCode.PAGE_CHANGED: "страница изменилась",
    ErrorCode.APPLICATION_MISSING: "приложение не нашлось",
    ErrorCode.APPLICATION_AMBIGUOUS: "название приложения неоднозначное",
    ErrorCode.PATH_DENIED: "папка не разрешена",
    ErrorCode.FILE_MISSING: "файла нет",
    ErrorCode.FILE_CONFLICT: "такой файл уже есть",
    ErrorCode.FILE_TOO_LARGE: "файл слишком большой",
    ErrorCode.NETWORK_DENIED: "адрес не разрешён",
    ErrorCode.TIMEOUT: "истекло время",
    ErrorCode.NATIVE_TIMEOUT: "истекло время",
    ErrorCode.BROWSER_TIMEOUT: "истекло время",
    ErrorCode.CANCELLED: "вы остановили задачу",
    ErrorCode.VERIFICATION: "результат не совпал с задуманным",
    ErrorCode.REPLAY: "это уже выполнялось",
}


def _plain(value: object, limit: int = MAX_DETAIL) -> str:
    """One bounded line of text, whoever wrote it."""
    return " ".join(str(value).split())[:limit] if isinstance(value, str) and value.strip() else ""


def _quoted(value: object, limit: int = MAX_DETAIL) -> str:
    text = _plain(value, limit)
    return f" «{text}»" if text else ""


def _field(args: object, *path: str) -> object:
    value = args
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _file(value: object) -> str:
    return _plain(value, 300).replace("\\", "/").rsplit("/", 1)[-1][:MAX_DETAIL]


def _host(value: object) -> str:
    """The site, not the whole address: a query string is not part of "what I did"."""
    return _plain(value, 300).split("//", 1)[-1].split("/", 1)[0][:MAX_DETAIL] or "страницу"


def _when(value: object) -> str:
    text = _plain(value, 20)
    try:
        moment = parse(text).astimezone()
    except ValueError:
        return ""
    return f" на {moment.day} {MONTHS[moment.month - 1]}, {moment:%H:%M}"


def _app(args: dict[str, object]) -> str:
    return (
        _plain(_field(args, "target", "app"))
        or _plain(_field(args, "target", "title"))
        or "приложении"
    )


def _send(args: dict[str, object], spoken: bool) -> str:
    subject = _quoted(_field(args, "message", "subject"))
    recipients = _field(args, "message", "to")
    listed = recipients if isinstance(recipients, list) else []
    to = [item for item in listed if isinstance(item, str)]
    if spoken or not to:
        return "отправил письмо" + subject
    more = f" и ещё {len(to) - 1}" if len(to) > 1 else ""
    return "отправил письмо" + subject + " на " + _plain(to[0]) + more


def _draft(args: dict[str, object], spoken: bool) -> str:
    return "сохранил в Outlook черновик письма" + _quoted(_field(args, "message", "subject"))


def _event(verb: str) -> Callable[[dict[str, object], bool], str]:
    def phrase(args: dict[str, object], spoken: bool) -> str:
        return (
            verb
            + " встречу"
            + _quoted(_field(args, "event", "subject"))
            + _when(_field(args, "event", "start"))
        )

    return phrase


DEEDS: dict[str, Callable[[dict[str, object], bool], str]] = {
    "outlook.send": _send,
    "outlook.save_draft": _draft,
    "calendar.create": _event("создал"),
    "calendar.update": _event("изменил"),
    "calendar.cancel": lambda args, spoken: "отменил встречу",
    "files.write_text": lambda args, spoken: "записал файл " + _file(args.get("path")),
    "files.rename": lambda args, spoken: (
        "переименовал " + _file(args.get("path")) + " в " + _plain(args.get("new_name"))
    ),
    "files.recycle": lambda args, spoken: "переместил в корзину " + _file(args.get("path")),
    "files.open": lambda args, spoken: "открыл файл " + _file(args.get("path")),
    "windows.open_app": lambda args, spoken: "открыл " + (_plain(args.get("app")) or "приложение"),
    "windows.focus_app": lambda args, spoken: "переключился на " + _app(args),
    # The typed text itself is the owner's content, so the report says where, never what.
    "windows.type_text": lambda args, spoken: "напечатал текст в " + _app(args),
    "browser.open": lambda args, spoken: "открыл в браузере " + _host(args.get("url")),
    "browser.navigate": lambda args, spoken: "перешёл на " + _host(args.get("url")),
    "browser.search": lambda args, spoken: (
        "поискал" + (_quoted(args.get("query")) or " в браузере")
    ),
    "browser.click": lambda args, spoken: (
        "нажал" + (_quoted(_field(args, "element", "name")) or " на элемент страницы")
    ),
    "browser.type": lambda args, spoken: "заполнил поле" + _quoted(_field(args, "element", "name")),
    "browser.close": lambda args, spoken: "закрыл вкладку",
    "local.append_message": lambda args, spoken: "добавил тестовое сообщение",
}


def _deed(step: Step, spoken: bool) -> str:
    """The phrase for what this step was for, or nothing when it only looked at something."""
    build = DEEDS.get(step.tool)
    if build is None:
        return ""
    try:
        args = json.loads(step.payload or "{}")
    except ValueError:
        return ""
    return build(args, spoken) if isinstance(args, dict) else ""


def _sentence(step: Step, spoken: bool) -> str:
    deed = _deed(step, spoken)
    if not deed:
        return ""
    if step.outcome.status is Status.SUCCESS:
        return deed[0].upper() + deed[1:] + "."
    if step.outcome.may_have_effects:
        # The dangerous case: a service may have accepted it and the answer never arrived.
        return deed[0].upper() + deed[1:] + ", но подтвердить не смог — проверьте."
    return "Не " + deed + " — " + REASON.get(step.outcome.error, "не получилось") + "."


def _unresolved(result: PlanResult) -> bool:
    return result.status != "finished" and any(
        step.outcome.may_have_effects and step.outcome.status is not Status.SUCCESS
        for step in result.steps
    )


def _looked(result: PlanResult) -> bool:
    return any(step.outcome.status is Status.SUCCESS for step in result.steps)


def written(result: PlanResult) -> tuple[str, ...]:
    """The report for the screen: the owner's own addresses, paths and names are fine here."""
    lines = [HEADLINE.get(result.status, HEADLINE["error"])]
    if result.status == "simulated":
        lines.append(f"Шагов в плане: {len(result.steps)}.")
        return tuple(lines)
    deeds = [line for line in (_sentence(step, False) for step in result.steps) if line]
    if deeds:
        lines.extend(deeds[:MAX_DEEDS])
    elif _looked(result):
        lines.append("Ничего не менял — только посмотрел.")
    if len(deeds) > MAX_DEEDS:
        lines.append(f"И ещё действий: {len(deeds) - MAX_DEEDS}.")
    if _unresolved(result):
        lines.append("Уже выданные действия не отменяются; проверьте их результат.")
    return tuple(lines)


def spoken(result: PlanResult) -> str:
    """The report for the voice: only what the local Russian voice can actually pronounce."""
    if result.status == "simulated":
        return HEADLINE["simulated"]
    deeds = [line for line in (_sentence(step, True) for step in result.steps) if line]
    parts = [HEADLINE.get(result.status, HEADLINE["error"]), *deeds[:SPOKEN_DEEDS]]
    if not deeds and _looked(result):
        parts.append("Ничего не менял — только посмотрел.")
    if len(deeds) > SPOKEN_DEEDS:
        parts.append(f"И ещё действий: {len(deeds) - SPOKEN_DEEDS}.")
    if _unresolved(result):
        parts.append("Уже выданные действия не отменяются.")
    return " ".join(parts)
