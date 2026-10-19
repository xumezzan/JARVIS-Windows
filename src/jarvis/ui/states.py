"""Visible shell states; inactive future capabilities never pretend to be running."""

from enum import StrEnum


class UiState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting approval"
    AWAITING_INPUT = "awaiting input"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


STATE_LABELS: dict[UiState, str] = {
    UiState.IDLE: "Готов к вводу",
    UiState.LISTENING: "Запись речи",
    UiState.THINKING: "Планирование шага",
    UiState.AWAITING_APPROVAL: "Ожидание подтверждения",
    UiState.AWAITING_INPUT: "Жду ответ в чате",
    UiState.EXECUTING: "Выполнение команды",
    UiState.SUCCESS: "Команда завершена",
    UiState.ERROR: "Ошибка выполнения",
    UiState.CANCELLED: "Выполнение отменено",
}
