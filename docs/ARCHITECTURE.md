# Архитектура

## Текущее состояние

Один Python-пакет, запускаемый через `python -m jarvis` или `jarvis`. Этапы 0–4
реализовали desktop shell, локальные typed tools, PermissionEngine, approvals и audit.
Текстовая команда остаётся fake demo этапа 1. Кнопка «Открыть инструменты» открывает
ручное окно локальных, Windows и браузерных инструментов. Windows UIA изолирован в helper-процессе;
Playwright имеет отдельный asyncio owner; LLM и voice adapters отсутствуют.

## Поток инструментов

```text
Manual UI (future: planner)
  -> ToolRegistry + strict Pydantic arguments
  -> PermissionEngine.prepare
  -> immutable Action snapshot
  -> deterministic risk policy
  -> ApprovalDialog + UI-only ApprovalAuthority for CONFIRM
  -> PermissionEngine.execute
  -> pure preconditions -> atomic token consume -> durable audit
  -> adapter -> strict result schema -> verifier
  -> factual Outcome -> audit -> UI
```

Реестр закрывается для регистрации при создании engine; дубликаты, неизвестные имена и
невалидные схемы не выполняются. `discover()` возвращает metadata и схемы, не методы
выдачи approval. Ни один production adapter не вызывается из UI напрямую.

## Модули

| Модуль | Реализация |
| --- | --- |
| `tools/base.py` | `ToolModel`, generic `ToolSpec`, context и canonical JSON |
| `tools/registry.py` | Регистрация, discovery, граница валидации входов/выходов |
| `tools/local.py` | SAFE check; CONFIRM append в память; CRITICAL/BLOCKED test descriptors |
| `permissions/policies.py` | Risk, Mode, Status и детерминированная политика |
| `permissions/approvals.py` | Immutable Action, opaque token, UI issuer, TTL, атомарный consume |
| `permissions/engine.py` | Prepare/execute/cancel, bounded pending requests, тайм-аут и audit gateway |
| `observability/audit.py` | SQLite audit, metadata без содержимого и bearer tokens |
| `ui/approval_dialog.py` | Read-only полный preview; token только из обработчика кнопки |
| `ui/permission_workbench.py` | Ручная проверка, режим, outcome, local outbox count, audit view |
| `ui/tool_worker.py` | Один `asyncio.run(engine.execute(...))` внутри QThread |
| `core`, `voice`, `memory`, `security` | Границы следующих этапов; реальных интеграций пока нет |

## Снимок и подтверждение

Pydantic применяет strict validation и запрет extra fields; mutable вход не сохраняется
в Action. Нормализованные параметры хранятся как строка canonical JSON. Preview и execute
используют один снимок; список вложений также скопирован в JSON.

Подпись включает request UUID, tool, полные параметры, risk и mode. Для сообщения это
service/action_type/account/recipient/subject/body/attachments. Изменённый снимок отклоняется
с аннулированием исходного approval. Перестановка ключей и неуказанные defaults нормализуются.

`ApprovalAuthority` создаётся единожды в trusted UI composition root. Его нет в engine API
или schema discovery. `QDialog.accept()` без нажатия кнопки не создаёт token. Authority
проверяет, что запрос существует и совпадает с подготовленным снимком, и записывает
факт approval до возврата token. TTL — 60 секунд от подготовки, по монотонным часам.
Второй issuer, повторная выдача и повторный consume невозможны. Lock защищает конкурентный consume.

## Выполнение и симуляция

SAFE проходит policy без интерактивного согласия. CONFIRM требует capability даже в
simulation. CRITICAL отключён; BLOCKED всегда запрещён. Неизвестный risk не разрешается.

В EXECUTE engine проверяет наличие approval перед preconditions, затем после них повторно
проверяет cancellation/expiry и потребляет token. Перед вызовом adapter нужен успешный
SQLite commit записи started. Результат валидируется схемой и отдельным verifier.
Автоматических retries нет. Каждый подготовленный request исполняется максимум один раз.

В SIMULATION выполняются schema и policy/approval checks, а все adapter hooks —
preconditions, execute и verify — пропускаются. Итог только SIMULATED: это не доказательство
выполнимости реальной операции. Approval нельзя переносить между режимами.

## Отмена, потоки и тайм-аут

Обычный shell demo остаётся отдельным bounded Event/QThread из этапа 1. Для инструментов
Qt worker владеет короткоживущим asyncio loop. Engine ограничивает время preconditions,
execute и verify общим timeout инструмента. Watcher проверяет thread-safe Event с шагом
10 ms и отменяет async task. Закрытие/Stop/Escape аннулируют pending approvals и дожидаются
завершения worker. Отмена до старта сохраняется как bounded cancellation result, а не INVALID.

Завершённый и проверенный результат не переписывается поздней отменой. После начала adapter
ошибка/cancel/timeout содержит `may_have_effects=true`: UI не обещает откат или отсутствие
изменений. Отказ финального audit не превращается в success и не разрешает повтор.

Контракт отмены кооперативный: trusted adapters обязаны отдавать управление event loop.
CPU-blocking code, native calls и подавление CancelledError не изолируются Python-классами.
Windows adapter использует описанную ниже границу отдельного процесса.
Pending requests ограничены 256; неисполняемые запросы старше 5 минут удаляются при prepare;
approval store очищает истёкшие grants при обращении. Cancellation cache также ограничен 256.

## Данные и журнал

Shell JSONL сохраняет только собственные конечные события и UUID. Tool audit хранится в
`<data_dir>/audit.sqlite3`: UTC timestamp, session/request ID, actor, зарегистрированное имя
инструмента, risk/mode, permission decision, execution status, duration, error category,
result summary и возможный эффект. Аргументы скрыты целиком; получатели, body, exception
text, typed result data, токены и signature не сохраняются в audit. Unknown tool names
логируются как `unknown`, исключая утечку через имя.

Local outbox — память одного окна проверки. Verifier читает receipt и сравнивает сохранённый
payload с исходным снимком. При закрытии окна сообщения исчезают. Вложения в текущем
контракте — только metadata name/SHA-256; настоящие файлы не читаются и не отправляются.
Будущему внешнему adapter понадобится замороженное содержимое файлов и повторная проверка.

SQLite memory, OAuth и keyring credentials ещё не реализованы. Долговременные секреты
не относятся к памяти модели. Retrieved content никогда не меняет policy или tool registry.

## Решения

Python >=3.12, `src` layout, Hatchling; PySide6-Essentials и Pydantic. Версии не закреплены
до реальной Windows-проверки. Одно локальное приложение с короткоживущим native helper, без микросервисов. Настройки читаются
из process environment, `.env` автоматически не загружается. Windows-цель — Windows 11 x64.
Точная установка и границы проверок описаны в `TESTING.md` и `COMPATIBILITY.md`.

## Windows Automation, этап 3

`tools/windows.py` содержит portable strict schemas и четыре ToolSpec. `get_open_windows`,
`open_app`, `focus_app` — SAFE; `type_text` — CONFIRM. Регистрация не импортирует pywinauto.
`platforms/windows/transport.py` запускает фиксированный модуль helper через Python `-I`,
без shell. `worker.py` лениво импортирует `native.py` только на Windows. Pywinauto/psutil
ограничены dependency marker `sys_platform == 'win32'`.

Каждый вызов helper ограничен 8 секундами, полный tool — 30 секундами. Startup shield
позволяет забрать и завершить процесс даже при отмене во время создания. При timeout/stop
helper убивается, его pipes осушаются и процесс ожидается; отмена не оставляет фоновый
native thread. ОС или целевое приложение могут завершить уже выданное действие.
Запущенные пользовательские приложения не уничтожаются и документы не закрываются.

Payload идёт только через stdin; stdout — одна ограниченная UTF-8 JSON-строка, stderr
отбрасывается. Это внутренний протокол доверенного приложения, не security sandbox.
Вызовы check_open/check_target в preconditions только читают. Изменяющие вызовы идут
после token consumption и durable started audit. Ошибки передаются конечными кодами.

Окна фильтруются по полным allowlisted путям процесса: System32 Notepad и пакет Microsoft
WindowsNotepad; стандартные Chrome/VS Code в Program Files или LocalAppData. Нет выбора
произвольного executable/argv, PATH поиска, shell, URL аргументов или auto-install.
Open возвращает существующее подходящее окно либо запускает приложение и наблюдает окно.
Focus сравнивает foreground HWND после вызова Windows API и повторно в verifier.

Target содержит app, PID + process creation time, executable, HWND, UIA runtime ID и title.
Для Notepad добавляются runtime ID, role, class, automation ID, native HWND редактора
и выбранные TabItem runtime IDs. UIA ищет видимый enabled Edit/Document с разрешённым
классом Edit/RichEdit; неоднозначный/виртуальный редактор без native HWND отклоняется.
Перед вводом проверяются identity, вкладка и пустое содержимое. `EM_REPLACESEL` с undo и
`SendMessageTimeoutW` адресуется конкретному редактору; клавиатура и clipboard не используются.
Текст читается через UIA, сравнивается SHA-256 с нормализацией CRLF, затем читается ещё раз
в verifier. Полный текст прочитанного документа не попадает в результат или audit.

UI показывает только наблюдённые targets и блокирует выбор во время approval/execution.
После ввода пустой snapshot удаляется. Смена/закрытие окна, вкладки или появление текста
отклоняют устаревший snapshot. Между последней проверкой и обработкой Windows-сообщения
остаётся короткая гонка с действиями пользователя; OS API не предоставляет атомарного
compare-and-write. Во время подтверждённого ввода не редактируйте целевую вкладку параллельно.

## Browser Automation, этап 4

`tools/browser.py` регистрирует восемь strict tools. `read`/`get_tabs` — SAFE;
`open`/`navigate`/`search`/`click`/`type`/`close` — CONFIRM. Названия `read` и `close`
соответствуют текущему roadmap; в исходном плане это `read_page` и `close_tab`.
`ToolSpec.policy` — чистая синхронная проверка во время normalize, до snapshot;
она работает и в simulation. Она не вызывает browser/DNS/adapters. Prepared snapshots
неизменяемы, а реальная precondition повторно наблюдает цель после проверки approval.

`BrowserHost` лениво создаёт поток с постоянным asyncio loop. Он сериализует команды,
получаемые от короткоживущих Qt workers. `BrowserSession` владеет Playwright и единственным
непостоянным Chromium context. UI хранит только наблюдения и вызывает PermissionEngine;
DOM операции выполняются вне UI thread. Конструктор и simulation браузер не запускают.

`browser/network.py` — единственный HTTP transport документов: aiohttp resolver передаёт
connector только проверенные публичные addresses. Browser context остаётся offline;
route handler сравнивает URL/method/body с точным однократным grant главного документа.
Любые subresources/frames/popups/неожиданные requests блокируются. JS отключён, CSP
дополнительно блокирует скрипты/frames/objects/base. Cookies, proxy environment, auth,
redirects, downloads и automatic retries не используются. Возвращается только HTML до 1 MB.

`browser/inspection.py` содержит фиксированное read-only DOM inspection. Доступные цели
имеют semantic role + exact name (ровно одно совпадение); snapshot включает tab/document
UUID, главный frame, URL/origin, DOM hash, element hash, значение и полное содержимое формы.
После ввода/навигации выполняется новое наблюдение, затем независимая verification.
Текст страницы отображается буквально и не может выдать approval или запустить следующий tool.

Границы: 8 вкладок, 50 элементов, до 12 000 символов текста страницы, 4 000 символов ввода,
64 KB action snapshot, HTTP 8 секунд, browser phase 15 секунд, tool 45 секунд. Stop отменяет
HTTP и Playwright operation; незавершённая загрузка останавливается фиксированным CDP
Page.stopLoading. Новая неоткрывшаяся вкладка удаляется, соседние сохраняются. Shutdown
закрывает принадлежащий окну временный context и driver с ограниченными ожиданиями.
Ошибки очистки сообщаются отдельно; это не hard-kill sandbox против зависшего/скомпрометированного
browser binary и не rollback уже отправленного запроса.

После сетевого отказа существующая вкладка получает локальный error document (502),
явно помеченный как сообщение Jarvis, а не текст сайта. Navigation tool всё равно
завершается ERROR. Это сохраняет возможность получить свежий target, прочитать сообщение,
выполнить новый переход или подтвердить закрытие; ошибку нельзя превратить в SUCCESS.
