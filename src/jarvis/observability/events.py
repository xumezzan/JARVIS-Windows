"""Finite shell events: arbitrary user text cannot become a log message."""

from enum import StrEnum


class ShellEvent(StrEnum):
    STARTED = "shell_started"
    SUBMITTED = "demo_submitted"
    EXECUTING = "demo_executing"
    SUCCEEDED = "demo_succeeded"
    FAILED = "demo_failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "demo_cancelled"
    TIMED_OUT = "demo_timed_out"
    CLOSED = "shell_closed"


EVENT_TEXT: dict[ShellEvent, str] = {
    ShellEvent.STARTED: "Оболочка запущена. Доступна локальная демонстрация.",
    ShellEvent.SUBMITTED: "Демонстрация принята. Подготовка…",
    ShellEvent.EXECUTING: "Выполняется фоновая демонстрация.",
    ShellEvent.SUCCEEDED: "Демонстрация завершена. Команда не исполнялась.",
    ShellEvent.FAILED: "Демонстрация завершилась ошибкой. Можно повторить запуск.",
    ShellEvent.CANCEL_REQUESTED: "Запрошена остановка демонстрации.",
    ShellEvent.CANCELLED: "Демонстрация отменена.",
    ShellEvent.TIMED_OUT: "Превышено время ожидания демонстрации.",
    ShellEvent.CLOSED: "Оболочка закрыта.",
}
