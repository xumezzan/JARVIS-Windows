"""Finite shell events: arbitrary user text cannot become a log message."""

from enum import StrEnum


class ShellEvent(StrEnum):
    STARTED = "shell_started"
    SUBMITTED = "command_submitted"
    EXECUTING = "command_executing"
    SUCCEEDED = "command_succeeded"
    FAILED = "command_failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "command_cancelled"
    TIMED_OUT = "command_timed_out"
    CLOSED = "shell_closed"


EVENT_TEXT: dict[ShellEvent, str] = {
    ShellEvent.STARTED: "Оболочка запущена. Команды выполняются через движок разрешений.",
    ShellEvent.SUBMITTED: "Команда принята. Планирование первого шага…",
    ShellEvent.EXECUTING: "Инструмент выполняется.",
    ShellEvent.FAILED: "Выполнение остановлено с ошибкой.",
    ShellEvent.SUCCEEDED: "Планировщик завершил работу. Проверьте факты по шагам.",
    ShellEvent.CANCEL_REQUESTED: "Запрошена остановка. Уже выданные действия не отзываются.",
    ShellEvent.CANCELLED: "Задача отменена.",
    ShellEvent.TIMED_OUT: "Истекло время задачи.",
    ShellEvent.CLOSED: "Оболочка закрыта.",
}
