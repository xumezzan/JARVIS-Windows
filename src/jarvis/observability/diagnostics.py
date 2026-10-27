"""Finite failure metadata and trusted explanations; never native exception text."""

from enum import StrEnum


class Stage(StrEnum):
    PREPARE = "prepare"
    OBSERVED_TARGET = "_observed_target"
    POLICY = "policy"
    APPROVAL = "approval"
    PRECONDITIONS = "preconditions"
    EXECUTE = "execute"
    VERIFY = "verify"
    AUDIT = "audit"


class Reason(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    TARGET_NOT_OBSERVED = "target_not_observed"
    STALE_MARK = "stale_mark"
    STALE_ELEMENT = "stale_element"
    CLIPBOARD_NOT_OBSERVED = "clipboard_not_observed"
    WINDOW_IDENTITY_CHANGED = "window_identity_changed"
    WINDOW_NOT_FOREGROUND = "window_not_foreground"
    EDITOR_CHANGED = "editor_changed"
    EDITOR_NOT_WRITABLE = "editor_not_writable"
    AMBIGUOUS_WINDOW = "ambiguous_window"
    APPLICATION_BLOCKED = "application_blocked"
    EFFECT_ALREADY_ISSUED = "effect_already_issued"


REASONS: dict[str | None, str] = {
    Reason.INVALID_ARGUMENTS: (
        "Аргументы не соответствуют схеме инструмента. Сверьте поля с каталогом инструментов."
    ),
    Reason.TARGET_NOT_OBSERVED: (
        "Цель не найдена в успешных наблюдениях текущей задачи. Сначала получите "
        "свежий список окон, inspect или screenshot; скопируйте точную цель."
    ),
    Reason.STALE_MARK: (
        "Номер элемента отсутствует в последнем наблюдении этой цели. Выполните "
        "свежий inspect или screenshot и выберите новый mark_id."
    ),
    Reason.STALE_ELEMENT: (
        "Элемент или окно не совпадает с текущим наблюдением. Выполните свежий "
        "inspect или screenshot."
    ),
    Reason.CLIPBOARD_NOT_OBSERVED: (
        "Содержимое буфера не связано с последним наблюдавшимся SHA-256. Сначала "
        "прочитайте или задайте буфер заново."
    ),
    Reason.WINDOW_IDENTITY_CHANGED: (
        "Изменилась идентичность окна: процесс, время запуска, handle, executable "
        "или runtime_id. Получите свежий список окон."
    ),
    Reason.WINDOW_NOT_FOREGROUND: (
        "Windows не передала фокус выбранному окну. Сначала вызовите "
        "windows.focus_app и проверьте активное окно."
    ),
    Reason.EDITOR_CHANGED: (
        "Состояние выбранного поля изменилось после наблюдения. Снова вызовите "
        "windows.get_text_fields; замена требует overwrite=true."
    ),
    Reason.EDITOR_NOT_WRITABLE: (
        "Поле защищено, доступно только для чтения или не поддерживает безопасный "
        "ввод. Выберите обычное редактируемое поле."
    ),
    Reason.AMBIGUOUS_WINDOW: (
        "Селектор не определяет одно окно. Получите список окон и выберите точный target."
    ),
    Reason.APPLICATION_BLOCKED: (
        "Запуск этого интерпретатора или системного хоста запрещён политикой запуска."
    ),
    Reason.EFFECT_ALREADY_ISSUED: (
        "Это действие уже было выдано и могло выполниться. Повтор не выполняется; "
        "сначала проверьте результат наблюдением."
    ),
}

ERRORS = {
    "invalid_request": REASONS[Reason.INVALID_ARGUMENTS],
    "unobserved_target": REASONS[Reason.TARGET_NOT_OBSERVED],
    "precondition_failed": (
        "Условие перед выполнением не выполнено. Обновите наблюдение цели перед "
        "следующим действием."
    ),
    "target_changed": REASONS[Reason.STALE_ELEMENT],
    "page_changed": "Страница изменилась. Прочитайте её заново и используйте новый PageTarget.",
    "verification_failed": (
        "Проверка результата не подтвердила действие. Сначала осмотрите текущее "
        "состояние; действие могло уже выполниться."
    ),
    "application_missing": (
        "Приложение не найдено в каталоге Windows. Проверьте установленное имя или "
        "получите список открытых окон."
    ),
    "application_ambiguous": "Найдено несколько приложений. Уточните установленное имя.",
    "control_unsupported": (
        "Элемент не поддерживает выбранную операцию. Обновите inspect и выберите "
        "поддерживаемый элемент."
    ),
    "policy_denied": (
        "Уровень риска запрещает действие. Планировщик не может изменить эту политику."
    ),
    "approval_invalid": (
        "Точное подтверждение отсутствует, истекло или было отклонено. Задача остановлена."
    ),
    "audit_unavailable": "Не удалось записать надёжный audit. Выполнение остановлено.",
    "cancelled": "Выполнение отменено. Уже выданные действия не откатываются.",
    "timeout": "Истёк срок выполнения операции.",
    "native_timeout": "Windows helper не ответил в отведённое время и был остановлен.",
    "network_denied": "Адрес или доступ к сети не соответствует политике выбранного браузера.",
    "unsupported_platform": "Для этой операции необходим Windows runtime.",
}


def failure_message(error: str, reason: str | None = None, may_have_effects: bool = False) -> str:
    """Only trusted catalog text crosses audit/provider/UI boundaries."""
    message = REASONS.get(
        reason, ERRORS.get(error, "Инструмент завершился с ошибкой; результат не подтверждён.")
    )
    if may_have_effects:
        message += (
            " Действие могло выполниться: не повторяйте его автоматически, сначала "
            "проверьте состояние."
        )
    return message
