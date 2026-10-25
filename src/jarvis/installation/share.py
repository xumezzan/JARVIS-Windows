"""Self-contained installer report; only bounded, allowlisted observations are shared.

This module and its CLI use only the standard library, including after a failed pip
installation. Input reports are data, never instructions or approval authority.
"""

import argparse
import html
import json
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from jarvis.installation.probe import PACKAGES, VERSION_PATTERN
from jarvis.installation.report import (
    CATALOG,
    CHECKS,
    ERROR_CODES,
    PREPARATION_LABELS,
    STATUS_LABELS,
)

LIMIT = 256_000
PHASES = {
    "host": ("Windows 11 x64 и интерактивный сеанс", "Проверьте ОС и desktop-сессию."),
    "managed_root_lock": (
        "Каталог установки и блокировка",
        "Закройте другой установщик; проверьте права и безопасный каталог установки.",
    ),
    "runtime": (
        "Python: целостность и запуск",
        "Проверьте сеть, свободное место и разрешения Windows; повторите Install-Jarvis.cmd.",
    ),
    "application_setup": (
        "Установка приложения и автоматические проверки",
        "Исправьте указанные ниже ошибки и повторите Install-Jarvis.cmd.",
    ),
    "launcher_shortcut": (
        "Создание файла запуска и ярлыка",
        "Проверьте права на меню Пуск; создание ярлыка не доказывает запуск из него.",
    ),
}
STATES = {"not_checked", "running", "passed", "failed", "cancelled", "warning"}
HEX = re.compile(r"[a-f0-9]{64}")
UNAVAILABLE = {
    "missing": "Отчёт отсутствует; результаты не получены.",
    "invalid": "Отчёт повреждён или имеет неподдерживаемый формат; результаты не приняты.",
    "stale": "Отчёт относится к другой попытке установки; его результаты не использованы.",
}


@dataclass(frozen=True)
class Capability:
    label: str
    checks: tuple[str, ...]
    functional: bool = False


CAPABILITIES = (
    Capability("Разрешения, симуляция, отмена и аудит", ("permissions",), True),
    Capability("Офлайн-планировщик: два локальных шага", ("planner",), True),
    Capability("Память: синтетические запись, чтение, изменение и удаление", ("memory",), True),
    Capability("Браузер: локальная политика и симуляция без эффектов", ("browser_policy",), True),
    Capability("Outlook: синтетический RAM-черновик без подключения", ("outlook_local",), True),
    Capability("Интерфейс и видимое окно", ("gui_smoke", "native_window")),
    Capability("Браузер: запуск и локальное чтение", ("offline_chromium",)),
    Capability(
        "Речь: загрузка модели и обнаружение устройств",
        (
            "local_vosk",
            "microphone",
            "speaker",
            "russian_voice",
        ),
    ),
    Capability("Системное хранилище ключей: создание backend", ("credentials_backend",)),
)

# A separate line for each real scenario prevents component discovery from turning
# an unperformed scenario green. The installer never accepts manual pass assertions.
SCENARIOS = (
    ("Запуск Jarvis из меню Пуск", "start_menu"),
    ("Windows: открыть Notepad, Chrome и VS Code; список и фокус окон", "windows_tools"),
    ("Windows: ввод в пустой Notepad после Approve и точное чтение обратно", "windows_tools"),
    ("Браузер: вкладки, переход, чтение, ввод и GET-формы через PermissionEngine", "public_search"),
    ("Поиск: фактические публичные результаты", "public_search"),
    ("Память: выбор метки на задачу и отдельное согласие на облачный контекст", "live_model"),
    ("Голос: удержание, запись, распознавание, проверка текста и отмена", "voice_hardware"),
    ("Голос: слышимое локальное озвучивание", "voice_hardware"),
    ("Outlook: вход Microsoft, аккаунт, список и чтение писем", "live_mail"),
    ("Outlook: локальный черновик в памяти окна", "live_mail"),
    ("Outlook: подтверждённый удалённый черновик и проверка чтением", "live_mail"),
    ("Outlook: подтверждённая отправка принятого запроса", "live_mail"),
    ("Outlook: получение точного письма тестовым адресатом", "live_mail"),
    ("OpenAI: реальный запрос и многошаговая задача", "live_model"),
    ("ElevenLabs Free (необязательно): согласие, квота и слышимый звук", "elevenlabs"),
    (
        "Полный Windows MVP; чистая, повторная, offline-установка и восстановление",
        "windows_mvp_acceptance",
    ),
)


def plain(path: Path) -> None:
    for entry in (path, *path.parents):
        if entry.is_symlink() or entry.is_junction():
            raise OSError("Report paths must not be links.")


def read_object(path: Path) -> dict[str, Any]:
    plain(path)
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Report too large.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid report.")
    return value


def matching(path: Path, run_id: str) -> tuple[dict[str, Any], str | None]:
    try:
        value = read_object(path)
        if value.get("schema") != 2:
            raise ValueError("Invalid schema.")
        if str(UUID(value["run_id"])) != run_id:
            return {}, "stale"
        return value, None
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {}, "invalid"


def diagnostics(value: dict[str, Any]) -> dict[str, tuple[str, str, int]]:
    rows = value.get("diagnostics")
    if not isinstance(rows, list) or len(rows) > len(CHECKS):
        raise ValueError("Invalid diagnostics.")
    results: dict[str, tuple[str, str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid diagnostic.")
        name, status, code = (row.get(key) for key in ("id", "status", "code"))
        duration = row.get("duration_ms", 0)
        if (
            not isinstance(name, str)
            or name not in CATALOG
            or name in results
            or not isinstance(status, str)
            or status not in STATES
            or not isinstance(code, str)
            or code not in ERROR_CODES
            or type(duration) is not int
            or not 0 <= duration <= 86_400_000
            or (CATALOG[name].scope == "manual" and status != "not_checked")
            or (status == "passed" and code != "none")
        ):
            raise ValueError("Invalid diagnostic.")
        results[name] = (status, code, duration)
    if not isinstance(value.get("status"), str) or value["status"] not in {
        "running",
        "failed",
        "cancelled",
        "automatic_checks_passed",
    }:
        raise ValueError("Invalid installation status.")
    if value["status"] == "automatic_checks_passed" and any(
        results.get(check.name, ("not_checked",))[0] != "passed"
        for check in CHECKS
        if check.required
    ):
        raise ValueError("Incomplete automatic diagnostics.")
    return results


def phases(value: dict[str, Any]) -> dict[str, str]:
    rows = value.get("phases")
    if not isinstance(rows, list) or len(rows) > len(PHASES):
        raise ValueError("Invalid bootstrap.")
    results: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid phase.")
        name, status = row.get("id"), row.get("status")
        if (
            not isinstance(name, str)
            or name not in PHASES
            or name in results
            or not isinstance(status, str)
            or status not in STATES - {"warning"}
        ):
            raise ValueError("Invalid phase.")
        results[name] = status
    if not isinstance(value.get("status"), str) or value["status"] not in STATES - {
        "warning",
        "not_checked",
    }:
        raise ValueError("Invalid bootstrap status.")
    if value["status"] == "passed" and any(results.get(name) != "passed" for name in PHASES):
        raise ValueError("Incomplete bootstrap.")
    return results


def timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return "не получено"
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.utcoffset() is not None else "не получено"
    except ValueError:
        return "не получено"


class Document:
    def __init__(self) -> None:
        self.md: list[str] = []
        self.html: list[str] = []

    def heading(self, text: str, level: int = 2) -> None:
        self.md += ["#" * level + " " + text, ""]
        self.html.append(f"<h{level}>{html.escape(text)}</h{level}>")

    def paragraph(self, text: str) -> None:
        self.md += [text, ""]
        self.html.append("<p>" + html.escape(text) + "</p>")

    def table(self, headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> None:
        self.md += [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        self.html.append(
            "<table><thead><tr>"
            + "".join("<th scope='col'>" + html.escape(h) + "</th>" for h in headers)
            + "</tr></thead><tbody>"
        )
        for row in rows:
            self.md.append(
                "| "
                + " | ".join(cell.replace("|", " / ").replace("\n", " ") for cell in row)
                + " |"
            )
            self.html.append(
                "<tr>" + "".join("<td>" + html.escape(cell) + "</td>" for cell in row) + "</tr>"
            )
        self.md.append("")
        self.html.append("</tbody></table>")

    def render(self) -> tuple[str, str]:
        css = (
            "body{max-width:90rem;margin:2rem auto;padding:0 1rem;"
            "font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#fff}"
            "table{width:100%;border-collapse:collapse;margin:1rem 0 2rem}"
            "th,td{text-align:left;vertical-align:top;padding:.6rem;border:1px solid #bbb;"
            "overflow-wrap:anywhere}th{background:#eef2f5}h1{font-size:1.8rem}"
            "h2{margin-top:2rem}@media(max-width:700px){body{font-size:14px}"
            "th,td{padding:.3rem}}@media print{body{margin:0;max-width:none}"
            "tr{break-inside:avoid}}"
        )
        page = (
            "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
            "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            "<title>Jarvis — отчёт установки и функций</title><style>"
            + css
            + "</style></head><body>"
            + "\n".join(self.html)
            + "</body></html>\n"
        )
        return "\n".join(self.md), page


def render_report(root: Path, application_root: Path, run_id: str) -> tuple[str, str]:
    run_id = str(UUID(run_id))
    bootstrap, bootstrap_error = matching(root / "installation-bootstrap.json", run_id)
    app, app_error = matching(application_root / "installation-report.json", run_id)
    phase_results: dict[str, str] = {}
    results: dict[str, tuple[str, str, int]] = {}
    try:
        if bootstrap_error is None:
            phase_results = phases(bootstrap)
    except ValueError:
        bootstrap, bootstrap_error = {}, "invalid"
    try:
        if app_error is None:
            results = diagnostics(app)
    except ValueError:
        app, app_error = {}, "invalid"
    document = Document()
    document.heading("Jarvis — установка и проверка функций", 1)
    document.paragraph("Этот документ можно отправить целиком. Соседние файлы и журналы не нужны.")
    document.paragraph(
        f"Попытка установки: {run_id}. Полная работоспособность MVP пока НЕ ПОДТВЕРЖДЕНА."
    )
    for label, value, error in (
        ("Установщик", bootstrap, bootstrap_error),
        ("Автодиагностика приложения", app, app_error),
    ):
        status = STATUS_LABELS[str(value["status"])] if error is None else "НЕ ПРОВЕРЕНО"
        document.paragraph(f"{label}: {status}." + (" " + UNAVAILABLE[error] if error else ""))
    document.paragraph(
        "Начало (UTC): "
        + timestamp(bootstrap.get("started_at_utc", app.get("started_at")))
        + ". Окончание (UTC): "
        + timestamp(bootstrap.get("finished_at_utc", app.get("finished_at")))
        + "."
    )
    windows_build = bootstrap.get("windows_build")
    if type(windows_build) is int and 0 <= windows_build <= 1_000_000:
        document.paragraph(f"Windows build: {windows_build}.")
    document.paragraph(
        "ПРОЙДЕНО относится только к описанной проверке. ОШИБКА — сбой этой проверки. "
        "НЕ ПРОВЕРЕНО — результата нет, это не отказ и не успех функции. "
        "Компоненты могут работать при непроверенном реальном сценарии."
    )
    counts = {
        state: sum(
            results.get(check.name, ("not_checked",))[0] == state
            for check in CHECKS
            if check.scope != "manual"
        )
        for state in STATES
    }
    document.paragraph(
        f"Автоматические проверки: пройдено {counts['passed']}; ошибок {counts['failed']}; "
        f"нужна настройка {counts['warning']}; отменено {counts['cancelled']}; "
        f"не проверено {counts['not_checked']}; выполняется {counts['running']}."
    )
    preparation = app.get("preparation_step")
    if isinstance(preparation, str) and preparation in PREPARATION_LABELS:
        document.paragraph("Последний шаг подготовки: " + PREPARATION_LABELS[preparation] + ".")
    document.heading("Установка")
    document.table(
        ("Этап", "Результат", "Следующий шаг"),
        [
            (
                label,
                STATUS_LABELS[phase_results.get(name, "not_checked")],
                "—" if phase_results.get(name) == "passed" else remedy,
            )
            for name, (label, remedy) in PHASES.items()
        ],
    )
    document.heading("Что проверено в функциях")
    capability_rows = []
    for capability in CAPABILITIES:
        states = [results.get(name, ("not_checked", "none", 0))[0] for name in capability.checks]
        if all(state == "passed" for state in states):
            status = (
                "ПРОЙДЕНО: локальный сценарий"
                if capability.functional
                else "КОМПОНЕНТЫ ПРОВЕРЕНЫ; реальный сценарий НЕ ПРОВЕРЕН"
            )
        elif any(state in {"failed", "cancelled", "warning"} for state in states):
            status = "ТРЕБУЕТ ВНИМАНИЯ; см. отдельные проверки ниже"
        else:
            status = "НЕ ПРОВЕРЕНО / проверка не завершена"
        evidence = (
            " ".join(
                CATALOG[name].evidence
                for name in capability.checks
                if results.get(name, ("",))[0] == "passed"
            )
            or "Завершённого доказательства нет."
        )
        capability_rows.append((capability.label, status, evidence))
    document.table(("Возможность", "Результат", "Граница доказательства"), capability_rows)
    document.heading("Все реальные сценарии, требующие отдельной проверки")
    document.paragraph(
        "Автоматическая установка не выполняет эти сценарии и не отмечает их успешными. "
        "Вход, ключи, удержание микрофона и точные подтверждения выполняет пользователь "
        "в приложении. Ниже указано, что нужно проверить следующим шагом."
    )
    document.table(
        ("Функция / сценарий", "Результат", "Как проверить"),
        [(label, "НЕ ПРОВЕРЕНО", CATALOG[name].remedy) for label, name in SCENARIOS],
    )
    document.heading("Подробные автоматические проверки")
    document.table(
        ("Проверка", "Результат", "Категория / время", "Доказательство или следующий шаг"),
        [
            (
                f"{check.label} ({check.name})",
                STATUS_LABELS[result[0]],
                f"{result[1]} / {result[2]} мс",
                check.evidence if result[0] == "passed" else check.remedy,
            )
            for check in CHECKS
            if check.scope != "manual"
            for result in [results.get(check.name, ("not_checked", "none", 0))]
        ],
    )
    document.heading("Версия и воспроизводимость")
    fingerprint = app.get("source")
    document.paragraph(
        "SHA-256 поставляемых исходников: "
        + (
            fingerprint
            if isinstance(fingerprint, str) and HEX.fullmatch(fingerprint)
            else "не получен"
        )
        + ". Это отпечаток входов установщика, а не доказательство опубликованной версии GitHub."
    )
    python = app.get("python")
    document.paragraph(
        "Python: "
        + (
            python
            if isinstance(python, str) and len(python) <= 80 and VERSION_PATTERN.fullmatch(python)
            else "не получен"
        )
        + "."
    )
    checks = app.get("checks")
    packages = checks.get("packages") if isinstance(checks, dict) else None
    version_rows = []
    if isinstance(packages, dict):
        for package in PACKAGES:
            version = packages.get(package)
            if (
                isinstance(version, str)
                and len(version) <= 80
                and VERSION_PATTERN.fullmatch(version)
            ):
                version_rows.append((package, version))
    document.table(("Runtime-пакет", "Версия"), version_rows or [("Версии", "не получены")])
    document.heading("Что отправить и что остаётся сделать")
    document.paragraph(
        "Отправьте Jarvis-Report.html или Jarvis-Report.md. В документ включены только "
        "фиксированные результаты и разрешённые версии; адреса почты, ключи, содержимое "
        "писем, запись речи, имена устройств и пользовательские пути не экспортируются."
    )
    document.paragraph(
        "При ошибке сохраните этот документ и исправьте указанный шаг. Повторная установка "
        "создаёт новый отчёт. Для полной приёмки нужен настоящий Windows-сценарий: "
        "запуск из Пуск → браузер и поиск → пустой Notepad с Approve → память → речь → "
        "Outlook → выбранная облачная модель. Результаты живых проверок этот автоматический "
        "документ не подменяет."
    )
    document.paragraph(
        "Новые интеграции, календарный сервис, мастер настройки, произвольное управление "
        "компьютером и фоновая запись не входят в текущие реализованные функции."
    )
    return document.render()


def export_report(root: Path, application_root: Path, run_id: str) -> None:
    plain(root)
    markdown, page = render_report(root, application_root, run_id)
    for name, content in (("Jarvis-Report.html", page), ("Jarvis-Report.md", markdown)):
        path = root / name
        plain(path)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=root, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            plain(path)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--application-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        export_report(args.root, args.application_root, args.run_id)
        return 0
    except (OSError, ValueError, TypeError, RecursionError):
        # The PowerShell fallback remains usable. No path/exception enters the document.
        return 1
