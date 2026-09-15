"""Visible shell states; inactive future capabilities never pretend to be running."""

from enum import StrEnum


class UiState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting approval"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


STATE_LABELS: dict[UiState, str] = {
    UiState.IDLE: "Готов к вводу",
    UiState.LISTENING: "Запись речи",
    UiState.THINKING: "Подготовка демонстрации",
    UiState.AWAITING_APPROVAL: "Ожидание подтверждения",
    UiState.EXECUTING: "Демонстрация выполняется",
    UiState.SUCCESS: "Демонстрация завершена",
    UiState.ERROR: "Ошибка демонстрации",
    UiState.CANCELLED: "Демонстрация отменена",
}
