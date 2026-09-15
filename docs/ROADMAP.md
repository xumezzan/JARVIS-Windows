# Roadmap

Работа идёт по этапам из исходного плана v1.0. Новый этап запускается отдельной задачей.
Текущий scope — этап 9. Установщик и переносимые проверки подготовлены; живой OAuth/почта,
настоящий микрофон/STT/TTS, живой OpenAI API и native Windows-приёмка ожидаются.
Результаты: `MILESTONE_0.md`, `MILESTONE_1.md`, `MILESTONE_2.md`, `MILESTONE_3.md`, `MILESTONE_4.md`, `MILESTONE_5.md`, `MILESTONE_6.md`, `MILESTONE_7.md`, `MILESTONE_8.md`, `MILESTONE_9.md`.

| Этап | Результат и критерий выхода | Статус |
| --- | --- | --- |
| 0 Foundation | Документы, package, установка, тесты, lint, types, команды для Windows | Реализован; native Windows не проверен |
| 1 Desktop shell | PySide6, состояния, текст, fake worker без блокировки, activity, config/logging, smoke | Реализован локально; native Windows не проверен |
| 2 Tools / permissions | Typed registry, risk, exact expiring single-use approvals, simulation, audit | Реализован локально; native Windows не проверен |
| 3 Windows | open/focus/list/type, Notepad read-back, Chrome/VS Code, UIA без координат | Реализован; portable tests пройдены, native Windows не проверен |
| 4 Browser | open/navigate/search/click/type/read/tabs/close, Playwright, domain policy, timeouts | Реализован локально: статический анонимный Chromium; Windows не проверена |
| 5 Planner | Strict calls, только registry, bounded plan, cancellation, уточнения, проверенные ответы | Реализован локально; offline/протокол/Qt/Chromium проверены, live API и Windows не проверены |
| 6 Voice | Push-to-talk, transcript, STT/TTS, русские команды, voice/button cancel | Реализован локально; fixtures/Qt/helper tests, реальное оборудование и Windows не проверены |
| 7 Memory | Профиль и короткий контекст, view/edit/delete, уточнение контактов, без secrets | Реализован локально; Windows и live cloud не проверены |
| 8 External service | Outlook OAuth/API, drafts, полный preview, send через permissions | Реализован локально; live OAuth/почта и Windows не проверены |
| 9 E2E / packaging | Реальный Windows-сценарий, failure tests, упаковка и установка из репозитория по одному запросу | Переносимая подготовка реализована; чистая Windows и полный E2E pending |

## Целевой сценарий установки

Разработка ведётся на Mac. На другом Windows-ноутбуке пользователь открывает репозиторий
в Codex и просит установить Jarvis. Этап 9 должен автоматизировать подготовку runtime,
зависимостей, браузера и русской модели, создание ярлыка и проверку запуска; ручная
подготовка Python не должна требоваться. Обязательные системные подтверждения и личный
вход в сервисы остаются за пользователем.
Подробные требования: [WINDOWS_INSTALLATION_PLAN.md](WINDOWS_INSTALLATION_PLAN.md).
Установщик: `scripts/windows/Install-Jarvis.cmd`. Реальные результаты и ограничения —
[MILESTONE_9.md](MILESTONE_9.md); финальная приёмка — [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md).

## Обязательные ворота качества

Каждый этап: focused tests → исправления → full suite → Ruff lint/format → mypy →
применимый реальный запуск → обновление docs и отчёт. Mock-тест не доказывает работу
Windows. Перед подключением Gmail/Outlook должны пройти негативные тесты PermissionEngine.

Этап 2 обязательно проверяет SAFE без approval, CONFIRM без/с token, подмену аргументов,
expiry, reuse, cancellation, BLOCKED, CRITICAL, concurrent consumption и отсутствие
реального side effect в simulation. Этапы 3–4 проверяют наблюдаемый результат, а не только
возврат вызова API. Этап 9 закрывает сетевые сбои, отсутствие приложения, ошибочную речь,
prompt injection и emergency stop.

## После MVP

0.2 — память, configurable permissions, Gmail/Calendar, routines. 0.3 — дополнительные
интеграции и skills. 0.4 — офисные приложения и профессиональные профили. 1.0 — стабильный
installer, подписанные обновления, административные политики и поддержка.

## Точный следующий prompt

```text
Continue Milestone 9 on the target Windows 11 x64 laptop: install Jarvis and complete native acceptance.

Read AGENTS.md, docs/MILESTONE_9.md, docs/WINDOWS_ACCEPTANCE.md and
scripts/windows/Install-Jarvis.ps1. Preserve all existing work. Run
scripts/windows/Install-Jarvis.cmd from this complete repository; do not require manual
Python/pip setup. Inspect installation-report.json and the real window, then launch from
Start. Repair any native failures, test clean/repeated installation, offline repeat,
spaces/Cyrillic paths, interrupted download, cancellation, permissions and missing hardware.

Run scripts/windows/Test-Jarvis.ps1 and the full PRD acceptance matrix. Native apps are
opt-in. Voice needs the user's actual hold gesture. For Outlook, let the user choose their
own test account and recipient and approve the exact snapshot in the normal UI. Never
bypass OAuth/MFA, microphone consent, Group Policy or approvals. 202 is not delivery proof.
Public search needs actual search results; the owned Chromium is separate from Chrome.

Record Windows build, runtime and package versions and every passed/failed/pending gate.
Only after real Windows compatibility checks, create a reproducible dependency lock and
repeat clean setup with it. Do not call the MVP complete until mandatory Windows E2E and
clean installation pass. If still on Mac, keep native gates pending; do not add post-MVP work.
```
