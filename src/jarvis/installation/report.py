"""Installer-only, finite diagnostic reports; never persist child output or user content."""

import json
import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

CheckStatus = Literal["not_checked", "running", "passed", "warning", "failed", "cancelled"]


@dataclass(frozen=True)
class Check:
    name: str
    label: str
    scope: str
    required: bool
    evidence: str
    remedy: str


DEPENDENCY_CHECKS = {
    "import_qt": "PySide6.QtWidgets",
    "import_pydantic": "pydantic",
    "import_pypdf": "pypdf",
    "import_docx": "docx",
    "import_aiohttp": "aiohttp",
    "import_certifi": "certifi",
    "import_keyring": "keyring",
    "import_msal": "msal",
    "import_pywinauto": "pywinauto",
    "import_psutil": "psutil",
    "import_sounddevice": "sounddevice",
    "import_vosk": "vosk",
    "import_pyttsx3": "pyttsx3",
}

CHECKS = (
    Check(
        "preflight",
        "Каталог, место на диске и настройки установки",
        "installation",
        True,
        "Проверены пути и свободное место; пользовательские данные находятся вне установки.",
        "Проверьте права, свободное место и абсолютный JARVIS_DATA_DIR вне JarvisInstall.",
    ),
    Check(
        "prepare",
        "Подготовка или повторное использование компонентов",
        "installation",
        True,
        "Компоненты подготовлены либо совпали с сохранёнными хэшами; это не проверка функций.",
        "Проверьте сеть и доступ к официальным источникам; повторите Install-Jarvis.cmd.",
    ),
    Check(
        "pip_check",
        "Совместимость установленных зависимостей",
        "component",
        True,
        "pip check не обнаружил конфликтов зависимостей.",
        "Повторите установщик; проверьте совместимость пакетов на этой Windows.",
    ),
    *(
        Check(
            name,
            f"Загрузка {module}",
            "component",
            True,
            "Модуль импортирован; его реальные внешние операции этим не проверены.",
            "Повторите установщик; проверьте Windows, native DLL и сообщения защиты ОС.",
        )
        for name, module in DEPENDENCY_CHECKS.items()
    ),
    Check(
        "offline_chromium",
        "Chromium: локальная страница без сети и JavaScript",
        "component",
        True,
        "Браузер запущен; текст локальной страницы прочитан. Публичный поиск не проверен.",
        "Повторите установщик; проверьте запуск Chromium и ограничения защиты ОС.",
    ),
    Check(
        "local_vosk",
        "Загрузка локальной русской модели речи",
        "component",
        True,
        "Модель загружена с локального пути. Запись и распознавание речи не проверены.",
        "Повторите установщик для проверки и восстановления локальной модели.",
    ),
    Check(
        "permissions",
        "Разрешения, симуляция, отмена и временный аудит",
        "offline_functional",
        True,
        "Проверены локальное SAFE-действие, запреты, отсутствие hooks в симуляции, отмена и аудит.",
        "Сохраните отчёт для исправления приложения; внешние действия не проверены.",
    ),
    Check(
        "planner",
        "Локальный планировщик: два проверенных шага",
        "offline_functional",
        True,
        "Офлайн-рецепт выполнил два local.check через PermissionEngine; "
        "облачная модель не проверена.",
        "Сохраните отчёт для исправления локального планировщика.",
    ),
    Check(
        "memory",
        "Память: запись, чтение, изменение и удаление",
        "offline_functional",
        True,
        "Проверена только синтетическая память во временной базе; ваши записи не открывались.",
        "Проверьте возможность создания временных файлов; сохраните отчёт для исправления.",
    ),
    Check(
        "browser_policy",
        "Браузер: политика адресов и безопасная симуляция",
        "offline_functional",
        True,
        "Проверены схемы, запрет приватного URL/POST и SAFE-симуляция без backend; "
        "Chromium, сеть и публичный поиск этим не проверены.",
        "Сохраните отчёт для исправления политики браузера; реальные страницы не открывались.",
    ),
    Check(
        "outlook_local",
        "Outlook: синтетический черновик только в памяти",
        "offline_functional",
        True,
        "Проверены RAM-черновик через PermissionEngine, привязка сессии, чтение и очистка; "
        "remote-запись без approval запрещена. OAuth и Graph не вызывались.",
        "Сохраните отчёт для исправления локальных черновиков; живой Outlook не проверен.",
    ),
    Check(
        "credentials_backend",
        "Создание системного credential backend",
        "discovery",
        True,
        "Создан явный native backend. Чтение/запись секретов и доступ аккаунтов не проверены.",
        "Проверьте наличие системного credential backend; plaintext fallback не используется.",
    ),
    Check(
        "microphone",
        "Обнаружение устройства ввода звука",
        "discovery",
        False,
        "Найдено устройство ввода; разрешение, запись и качество речи ещё не проверены.",
        "Подключите микрофон и проверьте запись явным удержанием кнопки в приложении.",
    ),
    Check(
        "speaker",
        "Обнаружение устройства вывода звука",
        "discovery",
        False,
        "Найдено устройство вывода; слышимость и качество звука ещё не проверены.",
        "Подключите динамик или наушники и выполните явную проверку озвучивания.",
    ),
    Check(
        "russian_voice",
        "Обнаружение русского системного голоса",
        "discovery",
        False,
        "Найден русский SAPI5-голос; фактическое звучание ещё не проверено.",
        "В Windows: Параметры → Время и язык → Речь → Управление голосами → "
        "добавьте русский голос.",
    ),
    Check(
        "package_versions",
        "Версии runtime-пакетов",
        "component",
        True,
        "Записаны версии только разрешённого списка пакетов; "
        "credentials и настройки не записываются.",
        "Повторите установку; проверьте metadata установленных runtime-пакетов.",
    ),
    Check(
        "gui_smoke",
        "Интерфейс: запуск и локальный симулированный сценарий",
        "component",
        True,
        "Native GUI завершил локальный симулированный сценарий во временном каталоге. "
        "Реальные Windows и сервисные действия этим не проверены.",
        "Проверьте интерактивную desktop-сессию и Qt; повторите установщик.",
    ),
    Check(
        "source_integrity",
        "Целостность исходников и квитанция компонентов",
        "installation",
        True,
        "Исходники не изменились во время установки; хэши компонентов сохранены.",
        "Остановите изменения репозитория и повторите установщик.",
    ),
    Check(
        "native_window",
        "Настоящее окно установленного приложения",
        "component",
        True,
        "Получена новая PID-bound квитанция видимого Qt-окна на Windows; "
        "UIA и сценарии ещё не проверены.",
        "Проверьте desktop-сессию, запуск приложения и защиту ОС; повторите установщик.",
    ),
    Check(
        "activation",
        "Активация проверенного слота",
        "installation",
        True,
        "Указатель активной установки записан после успешных автоматических проверок.",
        "Проверьте права на каталог установки и отчёт; сохраните рабочую версию.",
    ),
    Check(
        "start_menu",
        "Повторный запуск из меню Пуск",
        "manual",
        False,
        "",
        "Проверьте bootstrap-отчёт, закройте тестовое окно и запустите Jarvis из меню Пуск.",
    ),
    Check(
        "windows_tools",
        "Windows: приложения, точное подтверждение и ввод",
        "manual",
        False,
        "",
        "Пройдите сценарий пустого Блокнота с настоящим Approve и UIA read-back "
        "по WINDOWS_ACCEPTANCE.md.",
    ),
    Check(
        "public_search",
        "Фактическая публичная поисковая выдача",
        "manual",
        False,
        "",
        "Проверьте реальные результаты поиска в принадлежащем Jarvis Chromium; "
        "challenge не является успехом.",
    ),
    Check(
        "voice_hardware",
        "Речь: запись, распознавание, отмена и слышимый голос",
        "manual",
        False,
        "",
        "Удерживайте кнопку записи, проверьте transcript, отдельно отправьте команду "
        "и проверьте Stop/звук.",
    ),
    Check(
        "live_mail",
        "Outlook: OAuth, черновик, отправка и доставка",
        "manual",
        False,
        "",
        "Выберите тестовый аккаунт и адресата, подключитесь и подтвердите точные действия; "
        "202 не доказывает доставку.",
    ),
    Check(
        "live_model",
        "Облачная модель: реальный запрос",
        "manual",
        False,
        "",
        "Настройте ключ в OS credential store и разрешите один тест; "
        "он передаёт текст и расходует API usage.",
    ),
    Check(
        "elevenlabs",
        "Дополнительный ElevenLabs Free: API и звук",
        "manual",
        False,
        "",
        "Если выбран этот голос, проверьте его отдельно с одноразовым согласием "
        "и Free/quota guards.",
    ),
    Check(
        "windows_mvp_acceptance",
        "Полная Windows-приёмка MVP и воспроизводимая установка",
        "manual",
        False,
        "",
        "Завершите все обязательные строки docs/WINDOWS_ACCEPTANCE.md; "
        "автоматический отчёт их не закрывает.",
    ),
)
CATALOG = {check.name: check for check in CHECKS}
STATUS_LABELS = {
    "not_checked": "НЕ ПРОВЕРЕНО",
    "running": "ПРОВЕРЯЕТСЯ",
    "passed": "ПРОЙДЕНО",
    "warning": "НУЖНА НАСТРОЙКА",
    "failed": "ОШИБКА",
    "cancelled": "ОТМЕНЕНО",
    "automatic_checks_passed": "АВТОМАТИЧЕСКИЕ ПРОВЕРКИ ПРОЙДЕНЫ",
}
ERROR_CODES = {
    "none",
    "check_failed",
    "timeout",
    "invalid_response",
    "missing",
    "discovery_failed",
    "cancelled",
    "setup_failed",
}
PREPARATION_LABELS = {
    "slot_directory": "каталог неактивного слота",
    "isolated_environment": "создание изолированного окружения",
    "application_packages": "установка приложения и зависимостей",
    "browser_download": "загрузка matching Chromium",
    "local_model": "проверка и извлечение локальной модели",
}


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class InstallationReport:
    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.root = root
        self.value: dict[str, object] = {
            "schema": 2,
            "run_id": str(UUID(run_id)) if run_id else str(uuid4()),
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "scope": "automatic_installation_diagnostics",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "status": "running",
            "bootstrap_report": "installation-bootstrap.json",
            "bootstrap_status": "see_matching_run_id",
            "windows_mvp_acceptance": "pending",
            "live_mail": "pending",
            "live_model": "pending",
            "voice_hardware": "pending",
        }
        self.results: dict[str, dict[str, object]] = {
            check.name: {
                "id": check.name,
                "status": "not_checked",
                "code": "none",
                "duration_ms": 0,
            }
            for check in CHECKS
        }
        self.current: str | None = None

    def record(
        self, name: str, status: CheckStatus, *, code: str = "none", duration_ms: int = 0
    ) -> None:
        if (
            name not in CATALOG
            or status not in {"not_checked", "running", "passed", "warning", "failed", "cancelled"}
            or code not in ERROR_CODES
        ):
            raise ValueError("Invalid diagnostic result.")
        if CATALOG[name].scope == "manual" and status != "not_checked":
            raise ValueError("Installer cannot certify manual acceptance.")
        self.current = name if status == "running" else None
        self.results[name].update(status=status, code=code, duration_ms=max(0, duration_ms))
        self.save()

    def finish(self, status: Literal["automatic_checks_passed", "failed", "cancelled"]) -> None:
        if status not in {"automatic_checks_passed", "failed", "cancelled"}:
            raise ValueError("Invalid installation status.")
        if self.current is not None:
            self.results[self.current].update(
                status="cancelled" if status == "cancelled" else "failed",
                code="cancelled" if status == "cancelled" else "setup_failed",
            )
        if status == "automatic_checks_passed" and any(
            self.results[check.name]["status"] != "passed" for check in CHECKS if check.required
        ):
            raise ValueError("Incomplete automatic diagnostics cannot pass.")
        self.current = None
        self.value.update(status=status, finished_at=datetime.now(UTC).isoformat())
        self.save()

    def save(self) -> None:
        self.value["summary"] = {
            status: sum(result["status"] == status for result in self.results.values())
            for status in ("passed", "failed", "warning", "not_checked", "running", "cancelled")
        }
        self.value["diagnostics"] = [
            self.results[check.name]
            | {
                "label": check.label,
                "scope": check.scope,
                "required": check.required,
                "evidence": check.evidence
                if self.results[check.name]["status"] == "passed"
                else "",
                "next_action": check.remedy
                if self.results[check.name]["status"] != "passed"
                else "",
            }
            for check in CHECKS
        ]
        atomic_text(
            self.root / "installation-report.json",
            json.dumps(self.value, ensure_ascii=False, indent=2),
        )
        atomic_text(self.root / "installation-report.md", self.markdown())

    def markdown(self) -> str:
        lines = [
            "# JARVIS — отчёт автоматической проверки установки",
            "",
            f"Результат: **{STATUS_LABELS[str(self.value['status'])]}**.",
            "",
            f"Проверка: `{self.value['run_id']}`. Начало (UTC): {self.value['started_at']}.",
            f"Окончание (UTC): {self.value['finished_at'] or 'проверка ещё идёт'}.",
            "",
            f"Система: {self.value['platform']}. Python: {self.value['python']}.",
            f"Коммит источника обновления: {self.value.get('source_commit') or 'не указан'}.",
            "",
            "Полная работоспособность MVP **ещё не подтверждена**. "
            "Обнаруженное устройство, импорт модуля",
            "и видимое окно не доказывают работу речи, почты, Windows tools или облачной модели.",
            "",
            "Runtime и ярлык проверяет отдельный [bootstrap-отчёт](installation-bootstrap.md).",
            "Сравните run_id: отчёт другой установки не относится к этой проверке.",
            "",
        ]
        preparation_step = self.value.get("preparation_step")
        if isinstance(preparation_step, str) and preparation_step in PREPARATION_LABELS:
            lines += [f"Последний шаг подготовки: {PREPARATION_LABELS[preparation_step]}.", ""]
        attention = [
            check
            for check in CHECKS
            if self.results[check.name]["status"] in {"failed", "warning", "cancelled"}
        ]
        if attention:
            lines += ["## Что требует внимания", ""]
            for check in attention:
                result = self.results[check.name]
                lines.append(
                    f"- {check.label}: {STATUS_LABELS[str(result['status'])]}. {check.remedy}"
                )
            lines.append("")
        lines += ["## Автоматические проверки", ""]
        for scope in (False, True):
            if scope:
                lines += ["", "## Требуют отдельной проверки с участием пользователя", ""]
            for check in CHECKS:
                if (check.scope == "manual") != scope:
                    continue
                result = self.results[check.name]
                status = str(result["status"])
                lines.append(f"- **{STATUS_LABELS[status]} — {check.label}** ({check.name}).")
                if status == "passed":
                    lines.append(f"  {check.evidence}")
                elif status in {"failed", "warning", "cancelled"}:
                    lines.append(f"  Категория: {result['code']}. {check.remedy}")
                elif scope:
                    lines.append(f"  {check.remedy}")
                else:
                    lines.append("  Результат не получен; это не успешная проверка.")
        return "\n".join(lines) + "\n"
