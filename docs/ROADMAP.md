# Roadmap

Цель продукта и её разбор по компонентам — [PRD.md](PRD.md) и [AGENT_PLAN.md](AGENT_PLAN.md).
Здесь только состояние: что сделано, что открыто и что идёт следующим.

## Сделано

### Этапы 0–9 — узкий локальный ассистент

Отчёты: `MILESTONE_0.md` … `MILESTONE_9.md`.

| Этап | Результат | Статус |
| --- | --- | --- |
| 0 Foundation | Документы, пакет, установка, тесты, lint, типы | Реализован |
| 1 Desktop shell | PySide6, состояния, орб, activity, config/logging | Реализован локально |
| 2 Tools / permissions | Типизированный реестр, риск, точные одноразовые истекающие подтверждения, симуляция, аудит | Реализован локально |
| 3 Windows | Открыть любое установленное приложение, фокус, список окон, ввод в наблюдённое поле с чтением обратно | Реализован; native Windows не проверен |
| 4 Browser | Открыть, перейти, поиск, чтение, ввод, клик, вкладки; собственный Chromium | Реализован; JavaScript выключен, POST запрещён |
| 5 Planner | Строгие вызовы, только реестр, ограниченный план, отмена, уточнения | Реализован; длинная задача — 64 действия на durable-запуске |
| 6 Voice | Удержание кнопки, расшифровка, STT/TTS, русские команды, отмена голосом | Реализован; настоящее оборудование не проверено |
| 7 Memory | Профиль меток, просмотр/правка/удаление, уточнение контактов | Реализован локально |
| 8 Outlook | OAuth/Graph, черновики, полный preview, отправка через разрешения | Реализован; живая почта не проверена |
| 9 Установка | Установщик, переносимые проверки, упаковка | Подготовка реализована; чистая Windows не пройдена |

### План 0.2–0.3 — проактивность и широта

Подробности и принятые решения: [AMBIENT_PLAN.md](AMBIENT_PLAN.md),
[PLATFORM_PLAN.md](PLATFORM_PLAN.md).

| Фаза | Результат | Статус |
| --- | --- | --- |
| Платформа | Контракт коннектора, общий HTTP-транспорт, сборка, матрица разрешений, граф знаний, durable-запуски, отбор контекста, роутинг моделей | Готово |
| Коннекторы | Microsoft Calendar, Fireflies, наполнение графа знаний | Готово |
| 0 Уровни риска | `ROUTINE` между `SAFE` и `CONFIRM`: обычная работа без подтверждений | Готово |
| A1 Голосовое подтверждение | Повтор контрольной детали снимка вместо «да» | Готово |
| B Рутины | Фоновые наблюдения, очередь предложений, выключатели, дневной бюджет | Готово |
| C Выведенная память | Что ассистент выучил из завершённых запусков | Готово |
| D MCP | Любой сервер по проверенному манифесту с уровнем на каждый инструмент | Готово |
| A2 Слово активации | Свободные руки: слушатель на грамматике из двух исходов | Готово; живой микрофон не проверен |

## Открыто

Эти ворота закрываются на целевой машине, не здесь. Три из пяти закрыты 17–18.09.

1. ~~Установка одним запросом и запуск из «Пуска»~~ — **закрыто**: установка, повтор без
   загрузок, обновление слота, кириллический путь, конкурентный запуск, ярлык.
   Остаётся чистая ОС без Python и обрыв сети — нужна виртуальная машина.
2. ~~Настоящий микрофон~~ — **закрыто 18.09**: владелец голосом поставил задачу, модель сама
   выбрала пять инструментов, текст лёг в Блокнот. Слово активации на живом микрофоне
   и голосовое подтверждение остаются.
3. Живой Outlook: OAuth, чтение, черновик, отправка. Требует личного аккаунта владельца.
4. ~~Живой DeepSeek как основной провайдер~~ — **закрыто 18.09**.
5. Полный MVP E2E на Windows: ввод в Блокнот с чтением обратно прошёл, поисковая выдача
   заблокирована страницей-проверкой источника (чинится фазой 5).

Матрица приёмки: [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md).

## Дальше: из ассистента в компьютерного агента

Продукт переопределён владельцем: агент получает цель и сам решает, какие программы
открыть и что в них сделать. Сверка показала, что низ подходит, а недостают длина
задачи, глаза, руки и настоящий браузер. План: [AGENT_PLAN.md](AGENT_PLAN.md).

| Фаза | Результат | Статус |
| --- | --- | --- |
| 0 Реальность | Закрыть пять открытых ворот выше | Не начата |
| 1 Длинная задача | 64 шага и час, durable-запуск с возобновлением, живой чеклист, очередь подтверждений, бюджет | **Сделано 17–18.09**: потолки, журнал, фазы, возобновление, бюджет, чеклист, продолжение прерванной задачи, отсчёт времени на подтверждение. **Прогнана живьём 18.09** на реальной Windows с живой моделью: длинная задача доходит до конца, переживает убийство процесса и не дублирует эффекты; эскалация на сильную модель работает. Открыто: очередь подтверждений — нужно решение владельца (Р7) |
| 2 Сервисы API | Asana, Notion, Teams, Excel/OneDrive; сценарий «подготовь всё к встрече» | **Идёт**: Asana — четыре чтения и создание задачи ([ASANA.md](ASANA.md)); Notion — три чтения и две записи ([NOTION.md](NOTION.md)); Teams — чаты, черновик и отправка с подтверждением ([TEAMS.md](TEAMS.md)); OneDrive/Excel — файлы, листы, чтение и запись диапазона ([ONEDRIVE.md](ONEDRIVE.md)); сценарий «подготовь всё к встрече» собран кнопкой ([MEETING.md](MEETING.md)). Ни один живой токен и ни один живой прогон сценария ещё не проверены. Остались каналы Teams — согласие администратора тенанта |
| 3 Глаза | Дерево интерфейса окна, снимок окна, разбор моделью по отдельному согласию | Не начата |
| 4 Руки | Клик по элементу, ограниченные клавиши, буфер обмена, управление окнами, координаты как последний фолбэк | Не начата |
| 5 Браузер | Постоянный профиль, JavaScript, вход владельца руками, классифицированный POST, загрузки | Не начата |
| 6 Один экран | Орб, чеклист, последние действия, календарь, подключения — в одном окне | Не начата |
| 7 Живой голос | Потоковая речь и перебивание; естественный голос как основной путь | Не начата |
| 8 Способ работы | Одобренная последовательность сохраняется и повторяется | Не начата |

Шесть решений владельца (Р1–Р6: экран, руки, браузер, длина задачи, `CRITICAL`,
согласие на снимки в облаке) **приняты 17.09.2026 целиком**. Они разворачивают часть
прежних инвариантов; условия, на которых это сделано, лежат в `AGENTS.md` и
[SECURITY.md](SECURITY.md).

## Обязательные ворота качества

Каждая фаза: focused-тесты → исправления → полный `pytest` → Ruff → `ruff format` →
`mypy --strict` → применимый живой запуск → обновление документов и честный отчёт.

Mock-тест не доказывает работу Windows, микрофона, почты или модели. Каждый новый
инструмент рождается на `CONFIRM` и опускается до `ROUTINE` осознанным решением.
Новый коннектор проверяется на отказ аутентификации, таймаут, дубль и отмену. Новый
сценарий — на пропавшие данные, отказ инструмента, отмену посреди работы и повтор
без дубля.

## Точный следующий prompt

Ворота фазы 0 закрываются только на целевой Windows-машине. Пока она недоступна, работа
продолжается по фазе 1 на машине разработки — второй prompt ниже.

### На целевом Windows-ноутбуке

```text
Close phase 0 on the target Windows 11 x64 laptop: install Jarvis and complete native acceptance.

Read AGENTS.md, docs/AGENT_PLAN.md, docs/MILESTONE_9.md, docs/WINDOWS_ACCEPTANCE.md and
scripts/windows/Install-Jarvis.ps1. Preserve all existing work. Run
scripts/windows/Install-Jarvis.cmd from this complete repository; do not require manual
Python/pip setup. Inspect installation-report.json and the real window, then launch from
Start. Repair any native failures, test clean/repeated installation, offline repeat,
spaces/Cyrillic paths, interrupted download, cancellation, permissions and missing hardware.

Run scripts/windows/Test-Jarvis.ps1 and the full acceptance matrix, including the wake word
and hands-free listening on the real microphone, spoken confirmation by control detail, and
DeepSeek as the live planner provider. For Outlook, let the user choose their own test
account and recipient and approve the exact snapshot in the normal UI. Never bypass OAuth/MFA,
microphone consent, Group Policy or approvals. 202 is not delivery proof. Public search needs
actual search results; the owned Chromium is separate from Chrome.

Record Windows build, runtime and package versions and every passed/failed/pending gate.
Only after real Windows compatibility checks, create a reproducible dependency lock and
repeat clean setup with it. Phase 1 of docs/AGENT_PLAN.md already has its durable core,
written and tested on the development machine; run it for real here instead of rewriting it,
and do not start phase 2 before these gates pass.
```

### На машине разработки, пока Windows недоступна

```text
Phase 1 of docs/AGENT_PLAN.md is built except for the approval queue, which is waiting on a
decision by the owner (Р7 in that document). Do not build the queue before that decision.

Read AGENTS.md, docs/AGENT_PLAN.md (phase 1 and its status section),
tests/unit/test_long_task.py and tests/integration/test_long_task_ui.py, and preserve all
existing work. Useful work available on this machine: exercise the long task against the
offline provider and a long scripted plan and fix what the checklist, the resume list or
the journal get wrong; harden resumption against a journal that is unreadable, full or
concurrently written; and prepare phase 2 connectors as design only, without registering
tools, since phase 2 does not start before phase 0 passes.

Run focused tests, then the full pytest, ruff check, ruff format --check and mypy --strict.
On macOS use pytest --ignore=tests/unit/test_windows_native.py and mypy --platform win32,
and say in the report that these are host adjustments, not repository changes. Do not claim
any Windows, microphone, mail or model behaviour from this machine.
```

