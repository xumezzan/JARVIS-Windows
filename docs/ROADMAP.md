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
| 5 Planner | Строгие вызовы, только реестр, ограниченный план, отмена, уточнения | Реализован; 8 действий на задачу |
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

Эти ворота блокируют всё остальное и закрываются на целевой машине, не здесь.

1. Установка на чистую Windows 11 одним запросом и запуск из меню «Пуск».
2. Настоящий микрофон: слово активации, запись, распознавание, голосовое подтверждение.
3. Живой Outlook: OAuth, чтение, черновик, отправка.
4. Живой DeepSeek как основной провайдер обычной работы.
5. Полный MVP E2E на Windows.

Матрица приёмки: [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md).

## Дальше: из ассистента в компьютерного агента

Продукт переопределён владельцем: агент получает цель и сам решает, какие программы
открыть и что в них сделать. Сверка показала, что низ подходит, а недостают длина
задачи, глаза, руки и настоящий браузер. План: [AGENT_PLAN.md](AGENT_PLAN.md).

| Фаза | Результат | Статус |
| --- | --- | --- |
| 0 Реальность | Закрыть пять открытых ворот выше | Не начата |
| 1 Длинная задача | 64 шага и час, durable-запуск с возобновлением, живой чеклист, очередь подтверждений, бюджет | Не начата |
| 2 Сервисы API | Asana, Notion, Teams, Excel/OneDrive; сценарий «подготовь всё к встрече» | Не начата |
| 3 Глаза | Дерево интерфейса окна, снимок окна, разбор моделью по отдельному согласию | Не начата |
| 4 Руки | Клик по элементу, ограниченные клавиши, буфер обмена, управление окнами, координаты как последний фолбэк | Не начата |
| 5 Браузер | Постоянный профиль, JavaScript, вход владельца руками, классифицированный POST, загрузки | Не начата |
| 6 Один экран | Орб, чеклист, последние действия, календарь, подключения — в одном окне | Не начата |
| 7 Живой голос | Потоковая речь и перебивание; естественный голос как основной путь | Не начата |
| 8 Способ работы | Одобренная последовательность сохраняется и повторяется | Не начата |

Шесть решений владельца (Р1–Р6: экран, руки, браузер, длина задачи, `CRITICAL`,
согласие на снимки в облаке) должны быть приняты до фазы 1. Они разворачивают часть
прежних инвариантов, и без них работа встанет посреди фазы.

## Обязательные ворота качества

Каждая фаза: focused-тесты → исправления → полный `pytest` → Ruff → `ruff format` →
`mypy --strict` → применимый живой запуск → обновление документов и честный отчёт.

Mock-тест не доказывает работу Windows, микрофона, почты или модели. Каждый новый
инструмент рождается на `CONFIRM` и опускается до `ROUTINE` осознанным решением.
Новый коннектор проверяется на отказ аутентификации, таймаут, дубль и отмену. Новый
сценарий — на пропавшие данные, отказ инструмента, отмену посреди работы и повтор
без дубля.

## Точный следующий prompt

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
repeat clean setup with it. Do not start phase 1 of docs/AGENT_PLAN.md until these gates pass,
and do not implement later phases early.
```
