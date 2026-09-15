# Этап 5 — LLM Planner и Orchestrator

Дата проверки: 2026-09-15. Ветка: `codex/milestone-5-planner`, база `7f8a4bb`.
Планировщик реализован и проверен локально на macOS. **Живой OpenAI API, OS credential
round-trip и native Windows-приёмка ещё не проверены.** Весь MVP не завершён.

## Что реализовано

- Отдельное окно «Планировщик команд»: текст, progress, уточнения, точный approval каждого
  CONFIRM шага, Stop/закрытие, фактические результаты. Существующий dashboard и демо сохранены.
- Заменяемый `Provider`, strict `Proposal` и последовательный `Runner`. Каждый инструмент,
  включая чтение, проходит `PermissionEngine`. У модели нет API выполнения/выдачи approval.
- До 8 действий, 3 уточнений, 30 секунд на провайдера, 180 секунд на всю задачу. Ошибка
  останавливает цепочку без повтора выданных эффектов; возможные эффекты отражаются в итоге.
- Наблюдённые Windows/browser targets обязательны. Точный target/element копируется из
  SUCCESS результата текущей задачи, а adapter повторно проверяет актуальность перед действием.
- Simulation включена по умолчанию: ноль вызовов tool adapter hooks, нет выдуманных
  наблюдений. Итог — SIMULATED, а `finish` без действий не объявляет задачу успешной.
- Офлайн-учебные рецепты для local.check, открытия/ввода в Блокнот, browser open/search/read.
  Неизвестная формулировка запрашивает уточнение. Этот провайдер не является LLM.
- OpenAI Responses adapter через aiohttp: fixed HTTPS endpoint, strict function schemas,
  один call за шаг, отдельный structured control output, `store=false`, bounded request/response,
  без hosted tools, cookies, env proxies, redirects, сохранённых conversations и retries.
- OS key store через явный platform backend и killable helper с приватным pipe. CLI setup
  использует скрытый терминальный ввод. Ключ не читается из OPENAI_API_KEY/файлов и не входит
  в command/observations, audit или shell log. Ключ в этом этапе не запрашивался и не читался.
- Облачный режим требует разрешения пользователя на передачу команды, уточнений и результатов.
  Даже при симуляции инструментов запрос LLM расходует API usage. `store=false` не обещает
  нулевого хранения у провайдера. Политики Windows/browser из этапов 3–4 сохранены.

## Фактические проверки

Python 3.12.7, macOS arm64, PySide6 6.11.2, Pydantic 2.13.5, keyring 25.7.0,
Playwright 1.62.0 / Chromium 151.0.7922.34, aiohttp 3.14.3. Зависимости не закреплялись.

| Проверка | Фактический результат |
| --- | --- |
| `python -m pip install -e ".[dev]"` | Успешно в изолированном `.venv` |
| Новые focused runner/provider/Qt tests | Пройдены; включены в full suite |
| `python -m pytest` | **251 passed, 4 skipped**, 25.65 s |
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed |
| `python -m mypy` | Passed, 75 source files |
| `python -m mypy --platform win32` | Passed; это статический анализ, не Windows runtime |
| `QT_QPA_PLATFORM=cocoa python -m pytest tests/integration/test_planner_ui.py tests/integration/test_shell.py -q` | **30 passed**, 12.15 s |
| `QT_QPA_PLATFORM=cocoa python -m jarvis --smoke-test` | `Jarvis GUI smoke: success`, exit 0 |
| `QT_QPA_PLATFORM=cocoa python -m jarvis` | Реальное окно проверено через UI: два SIMULATED, затем два SUCCESS; Escape закрывает planner, главное окно закрывается, exit 0 |
| `python -m build` | Wheel + sdist собраны с изолированным Hatchling |
| Установка wheel в новый venv, запуск из `/tmp` | Импорт из site-packages; Runner выполнил два local.check с SUCCESS |
| GUI smoke из установленного wheel в новом venv | `Jarvis GUI smoke: success`, exit 0 |

Пропуски: три случая native Windows требуют `--run-windows` и Windows; один живой API
test требует `--run-model`, настроенного ключа и модели. Ничего из этого не выдаётся за
проверенное fixtures. При интерактивном запуске macOS выдала диагностическую строку
TSMSendMessageToUIServer/CFMessagePortSendRequest; ввод, выполнение и выход работали.

Покрыты multi-step results, clarification/re-observation, unknown tool/schema coercion/extras,
CRITICAL/BLOCKED, no/wrong approval, generic QDialog.accept без кнопки, limits, provider failure,
provider/overall timeout, stop во время provider/tool/approval/clarification, close/Escape,
failed verification без повтора, no success from plan alone и неизменность журнала без контента.
PermissionEngine suite продолжает проверять expiry/replay/changed action/atomic consumption.

Реальный локальный HTTP/Chromium тест открывает страницу после approval, берёт наблюдённый
target для следующего browser.read и проверяет отсутствие фоновых запросов. Windows
open→type→read-back выполнен через portable probe, не через Windows. Протокольные fixtures
проверяют strict payload/control, отказ/неполный/множественный output, отсутствие approval
tool и отдельное поле для prompt injection в наблюдениях. Credential helper cancellation
проверена с настоящим дочерним процессом и его завершением/сборкой.

## Запуск на Windows 11

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m jarvis
```

В GUI открыть «Планировщик команд» и ввести `проверь систему дважды`. Сначала проверить
симуляцию, затем локальное выполнение. На Windows подготовить пустой Блокнот, выполнить
`открой блокнот и напиши «Привет»`, проверить точный preview и read-back после approval.
Предварительно сохранить существующие документы; тест не заменяет их содержимое.

Для OpenAI пользователь вводит ключ в собственном терминале:

```powershell
.\.venv\Scripts\python.exe -m jarvis.security.credentials set
$env:JARVIS_PLANNER_MODEL = Read-Host "Responses API model ID"
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_model_acceptance.py --run-model -q
.\.venv\Scripts\python.exe -m jarvis
```

Ключ хранится под сервисом `Jarvis/OpenAI`, аккаунтом `default`. В UI выбрать OpenAI,
указать доступную модель с strict function calling и разрешить передачу данных. Тест
отправляет синтетическую команду и каталог схем, проверяя один call без исполнения adapters.
После него отдельно проверить многошаговый GUI-сценарий. Ключ в чат или `.env` не вставлять.
На macOS заменить путь Python на `.venv/bin/python`; Linux cloud backend пока отсутствует.

Native acceptance предыдущих этапов:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
```

## Ограничения и следующий этап

Живой API не вызывался: совместимость конкретной модели со всем каталогом, ключ, account
limits, качество произвольного планирования и многошаговая работа с реальной моделью
остаются открытой приёмкой. Разметка untrusted data не доказывает устойчивость модели к
любой prompt injection; локальные permissions и отсутствие approval capability обязательны.
Пользовательский ввод не проходит универсальный фильтр секретов — не вводить их в команды.

Нет голоса, долговременной памяти, OAuth/email, production browser POST или installer.
Текстовая строка dashboard по кнопке «Запустить демо» всё ещё запускает демо; реальная
команда запускается в окне планировщика. Следующий этап — **6: Voice / push-to-talk**;
точный prompt находится в [ROADMAP.md](ROADMAP.md).
