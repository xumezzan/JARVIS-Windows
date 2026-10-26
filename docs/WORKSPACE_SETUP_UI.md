# Workspace Setup UI — этап 4

В Electron откройте **Пространство**. Настройте имя, компанию, должность и правила
работы; добавьте 14 приложений Omadli Group одной кнопкой либо создайте своё.
У каждого приложения доступны название, включение и точный HTTPS URL.
Внутренний адрес Omadli Group не угадывается: приложение выключено до его настройки.
Для корпоративного ERP есть отдельное явное разрешение частной сети;
loopback/link-local, password-manager domains и site tiers сохраняют ограничения.

Кнопка пресета дополняет профиль, сохраняя пользовательские приложения, изменённые
URL, identity, playbooks и MCP-review. Повторный выбор не дублирует приложения.
Новые приложения выключены, имеют `CONFIRM` и не получают origin до ввода URL.
Индикатор SAFE/CONFIRM показывает metadata `default_mutation_risk`, а не разрешение
выполнить действие: окончательное решение остаётся за PermissionEngine.
Отправка, публикация и автозаполнение 1Password сохраняют подтверждения.

Правки формы сохраняются при переходе к ассистенту и обратно. **Сохранить
пространство** записывает профиль атомарно через WorkspaceStore. **Загрузить
сохранённое** явно заменяет несохранённую форму последней сохранённой версией.

## Применение и протокол

Настройки применяются **после перезапуска Jarvis**. Панель показывает сохранённый
выбор, действующий режим движка и необходимость перезапуска. Существующие host,
registry и Runner не меняются посреди задачи; на время выполнения/записи настройки
недоступны. Чтение/сохранение не открывает браузер, не записывает микрофон и не
вызывает сервисы. `JARVIS_BROWSER_MODE` и trusted CDP startup остаются приоритетными;
endpoint, cookies и credentials нельзя задать через форму.

Приватный JSONL использует одну новую операцию `workspace`:

```json
{"id":"00000000-0000-0000-0000-000000000001","operation":"workspace","workspace":{"operation":"read"}}
```

Ответ `state.workspace` содержит:

- `profile`: только `user`, `apps`, `browser_mode`;
- `revision`: SHA-256 всего текущего WorkspaceProfile, включая скрытые поля;
- `restart_required`: сохранённый профиль отличается от активного;
- `active_browser_mode`: действующий режим, включая startup override.

Для записи передайте `workspace.operation="save"`, последнюю `revision` и `profile`.
Для пресета: `operation="preset"`, `preset="omadli_group"` и последнюю `revision`.
Смена скрытого MCP-review тоже инвалидирует форму (`workspace_changed`). Это
оптимистическая проверка перед записью; WorkspaceStore не предоставляет межпроцессную
транзакцию compare-and-swap для одновременно начатых записей.

MCP-конфигурация и playbooks не принимаются как редактируемые поля. Они берутся из
последнего файла, а полный профиль повторно валидируется. Неверный URL даёт конечную
ошибку `workspace_invalid`, без закрытия pipe и без содержимого формы в сообщении.
Ошибки хранения/размера не содержат путей и пользовательского текста. Лимит JSONL
request остаётся 131072 байта; main заранее отклоняет крупную форму, сохраняя связь.
Preload предоставляет только фиксированный `jarvis.workspace(command)`; main
проверяет sender, main frame, URL и число аргументов. Approval API не добавляется.

Профиль остаётся untrusted context. Сохранение не выдаёт cloud consent, approval,
наблюдавшиеся targets или полномочия MCP; в модели передаётся прежний ограниченный
`planner_context()` при согласии на облачную задачу.

## Проверки бюджета и интерфейса

`scripts/probe_multimodal_budget.py` создаёт изолированные реальные registry трёх
оболочек. Используются все 14 включённых приложений, заполненная synthetic identity,
playbooks, один reviewed MCP-инструмент с реальной схемой, 96 UIA элементов с
максимальными Unicode names и текущий JPEG размером 49152 байта / base64 65536 байт.
Проверяется фактически сериализованный запрос OpenAIProvider, без network/credentials.

| Runtime | Инструментов | Размер запроса, байт |
| --- | ---: | ---: |
| DesktopService | 41 | 148879 |
| PlannerWindow + connectors | 80 | 175254 |
| CompactPlannerWindow | 44 | 149940 |

Все результаты `< 200000 < 262144`. Произвольные дополнительные MCP-схемы и
максимально заполненные пользовательские поля не обещают тот же размер;
действующий `context_limit` остаётся fail-closed до transport.

Python regression сверяет имена полных Desktop/Qt registry с labels. Node проверяет
новые browser/calendar labels, обе динамические MCP-схемы именования и isolated
preload. Реальный Electron smoke проверяет профиль, пресет, custom app, отклонение
URL с последующим исправлением, навигацию без потери правок, запись JSON и повторный
запуск с новым browser mode. Обе ширины 1536 и 1024 проверены без overflow;
renderer errors и расхождения labels отсутствуют. Данные только synthetic.

Локальный результат 2026-10-26: pytest **1846 passed, 23 skipped**; Ruff check и
format-check, mypy (338 source files), Node **13/13**, Electron build и Workspace
E2E прошли. Пропуски: live model/voice/Windows и 17 updater checks без PowerShell.
`pip check`, GUI smoke (simulation) и wheel/sdist build также прошли.

```sh
.venv/bin/python scripts/probe_multimodal_budget.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
npm --prefix desktop run qa:workspace
.venv/bin/python -m pip check
.venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build
```

На Windows используйте `.venv\Scripts\python.exe` вместо `.venv/bin/python`.
Electron QA явно выбирает проектную `.venv`, чтобы не зависеть от устаревшего
кэша desktop runtime. Обычный `npm --prefix desktop start` проходит `ensurePython()`:
неполный кэш отвергается проверкой imports, выбирается работоспособное окружение.
В локальном кэше обнаружен отсутствующий psutil; штатный ensurePython проверен и
выбрал проектную `.venv`, без изменения старого кэша.

Эти проверки выполнены на macOS. Hosted Windows CI проверяет Python gates;
настоящие Windows UI, live CDP/accounts, Graph/MCP, микрофон и 1Password требуют
отдельной приёмки на целевой машине.
