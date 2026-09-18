# Архитектура

## Текущее состояние

Один Python-пакет, запускаемый через `python -m jarvis` или `jarvis`. Этапы 0–9 и фазы
плана 0.2–0.3 реализовали desktop shell, локальные typed tools, PermissionEngine с пятью
уровнями, approvals, audit, durable-запуски, граф знаний, коннекторы, рутины и голос со
словом активации.
Текстовая команда главного окна запускает планировщик того же сеанса: MainWindow владеет одним PlannerWindow, берёт из него реестр, движок разрешений и audit, и показывает его уточнения и подтверждения в своём окне. Кнопка «Открыть инструменты» открывает
ручное окно локальных, Windows и браузерных инструментов. Windows UIA изолирован в helper-процессе;
Playwright имеет отдельный asyncio owner. Отдельное окно планировщика использует offline
recipes или OpenAI Responses; локальный голос и управляемая память реализованы.
Outlook подключается явно через MSAL/Graph; настоящая почта ещё не проверена.

## Целевая архитектура

Продукт — компьютерный агент: владелец ставит цель, ассистент выбирает средства.
Десять компонентов постановки и модули, которые за них отвечают. План и порядок работ —
[AGENT_PLAN.md](AGENT_PLAN.md).

| Компонент | Модули | Состояние |
| --- | --- | --- |
| Voice Input / STT | `voice/`, `platforms/audio.py`, `ui/voice_panel.py` | Есть; оборудование не проверено |
| AI Brain / LLM | `core/planner/routing.py`, `deepseek_provider.py`, `openai_provider.py` | Есть; Claude как инструмент разбора — нет |
| Planner | `core/planner/runner.py`, `core/workflow/` | Есть на короткой задаче; длинная задача с возобновлением — фаза 1 |
| Briefing | `core/meeting.py` | Сводка к встрече: пять `SAFE`-чтений через движок разрешений, каждая строка с источником |
| Tool & MCP Layer | `tools/`, `connectors/` | Outlook, календарь, Fireflies, Asana, Notion, чаты Teams, OneDrive/Excel, MCP; каналы Teams — остаток фазы 2 |
| Computer Control | `tools/windows.py`, `platforms/windows/` | Открыть, сфокусировать, перечислить, напечатать; клик, клавиши, буфер — фаза 4 |
| Vision / Screen | — | Нет; дерево интерфейса и снимок окна — фаза 3 |
| Memory | `memory/`, `knowledge/`, `core/context/` | Есть; способ работы как память — фаза 8 |
| Permission System | `permissions/`, `voice/approval.py`, `ui/approval_dialog.py` | Есть; `CRITICAL` ждёт решения Р5 |
| TTS | `voice/local.py`, `voice/elevenlabs.py`, `core/report.py` | Есть; потоковая речь и перебивание — фаза 7 |
| UI | `ui/main_window.py`, `ui/dashboard.py`, `ui/planner_window.py` | Есть; один экран с чеклистом — фаза 6 |

Три границы, которые новые фазы обязаны сохранить, потому что на них держится всё
остальное: наблюдение принадлежит задаче (цель действия увидена в ней, а не вспомнена),
токен подтверждения выдаёт только UI, и любой внешний текст — данные, а не полномочие.

## Поток инструментов

```text
Manual UI / bounded Planner Runner
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
выдачи approval. Почтовые/Windows/browser tool adapters вызываются только через engine.
OAuth connect/disconnect — отдельный UI-only setup через QThread и killable helper с audit;
этот путь отсутствует в каталоге модели и не даёт permission authority.

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
| `ui/approval_dialog.py` | Read-only полный preview; token из кнопки или из совпавшей контрольной детали |
| `ui/permission_workbench.py` | Ручная проверка, режим, outcome, local outbox count, audit view |
| `ui/tool_worker.py` | Один `asyncio.run(engine.execute(...))` внутри QThread |
| `core/planner` | Provider protocol, offline recipes, OpenAI strict calls, bounded Runner |
| `ui/planner_window.py`, `ui/planner_worker.py` | Текстовая команда, отдельный QThread, prompts и stop |
| `security/credentials.py`, `platforms/credentials.py` | Killable credential pipe и явный OS backend |
| `security/cloud_consent.py` | Разрешение на облако и ID модели между запусками; повреждённый файл = нет согласия |
| `core/planner/identifiers.py` | Одно правило ID модели для настроек, окна согласия и провайдера |
| `voice`, `memory` | Локальный push-to-talk и явно управляемые метки |
| `core/routines` | Фоновые наблюдения, очередь предложений, выключатели и дневной бюджет |
| `core/report.py` | Что именно сделано: текст для экрана и для голоса из снимка и исхода |
| `ui/routine_panel.py` | Вкладка «Рутины»: выключатели, предложения, то же окно подтверждения |
| `mail`, `tools/outlook.py`, `ui/mail_panel.py` | MSAL/helper, Graph, RAM drafts/attachments, typed mail tools и UI |

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

SAFE и ROUTINE проходят policy без интерактивного согласия. CONFIRM требует capability даже в
simulation. CRITICAL отключён; BLOCKED всегда запрещён. Неизвестный risk не разрешается.

В EXECUTE engine проверяет наличие approval перед preconditions, затем после них повторно
проверяет cancellation/expiry и потребляет token. Перед вызовом adapter нужен успешный
SQLite commit записи started. Результат валидируется схемой и отдельным verifier.
Автоматических retries нет. Каждый подготовленный request исполняется максимум один раз.

В SIMULATION выполняются schema и policy/approval checks, а все adapter hooks —
preconditions, execute и verify — пропускаются. Итог только SIMULATED: это не доказательство
выполнимости реальной операции. Approval нельзя переносить между режимами.

## Отмена, потоки и тайм-аут

Оболочка больше не содержит отдельного demo-потока; её состояние повторяет события планировщика. Для инструментов
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

SQLite memory и OAuth ещё не реализованы. Ключ OpenAI хранится в OS credential store,
не в памяти модели. Retrieved content никогда не меняет policy или tool registry.

## Решения

Python >=3.12, `src` layout, Hatchling; PySide6-Essentials и Pydantic. Версии не закреплены
до реальной Windows-проверки. Одно локальное приложение с короткоживущим native helper, без микросервисов. Настройки читаются
из process environment, `.env` автоматически не загружается. Windows-цель — Windows 11 x64.
Точная установка и границы проверок описаны в `TESTING.md` и `COMPATIBILITY.md`.

## Windows Automation, этап 3

`tools/windows.py` содержит portable strict schemas и четыре ToolSpec. `get_open_windows`,
`open_app`, `focus_app` — SAFE; `type_text` — ROUTINE. Регистрация не импортирует pywinauto.
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

## Files

`jarvis.files.policy` — чистая политика путей: разрешённые папки, разрешение пути до
сравнения, запрет исполняемых расширений. `jarvis.tools.files` — типизированные
инструменты поверх неё; эффекты, принадлежащие оболочке ОС (корзина и открытие документа
в его приложении), вынесены в адаптер `jarvis.platforms.files`.

## Browser Automation, этап 4

`tools/browser.py` регистрирует восемь strict tools. `read`/`get_tabs` — SAFE;
`open`/`navigate`/`search`/`click`/`type`/`close` — ROUTINE. Названия `read` и `close`
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

## LLM Planner и Orchestrator, этап 5

`Provider.propose(PlannerInput)` возвращает ровно один strict `Proposal`: зарегистрированный
вызов, вопрос или finish без текста об успехе. `Runner` последовательно вызывает
`PermissionEngine.prepare/execute`; provider получает только JSON metadata каталога,
текст команды, ответы пользователя и неизменяемые outcomes прошлых шагов. Исполняемые
callbacks реестра, engine, authority и токены ему не передаются.

Runner ограничен 8 действиями, 3 уточнениями, 30 секундами на провайдера и 180 на задачу.
Длинная задача (`Limits.long()`) поднимает потолки до 64 действий, 60 секунд на провайдера
и часа на задачу, но только вместе с журналом запуска: `durable` без `journal` даёт ошибку
`journal_required` до первого предложения, поэтому длина никогда не держится на доверии.
Бюджет (`Budget`) считает обращения к модели и останавливает задачу статусом `budget`;
счёт идёт в шагах, а не в деньгах, потому что цену вызова нечем проверить.
Фазы durable-запуска — `running`, `waiting_approval`, `waiting_input`, `done`, `failed`,
`cancelled`: по ним после перезапуска видно, чего задача ждала. Запись фазы никогда не
меняет исход задачи; недоступный журнал оставляет запуск незавершённым, и это безопасная
видимость — такой запуск предлагается к возобновлению, а не считается сделанным.
Возобновление (`planner/resume.py`) восстанавливает из журнала только личность и исход
шагов: аргументов и результатов там нет и не будет. Поэтому цель, наблюдённая до
перезапуска, после него целью не является — задача обязана посмотреть заново (И5).
Общий deadline включает approval/clarification; собственные тайм-ауты инструментов сохраняются.
Нет автоматических retries: ошибка/denial/verification failure останавливают задачу.
Windows/browser target обязан присутствовать в SUCCESS observation этой задачи; элемент —
в той же странице. Adapter повторно проверяет актуальность identity/DOM перед действием.
В симуляции нет таких наблюдений, и зависимые цели не синтезируются.

`Checklist` (`ui/checklist.py`) строит строку на шаг из тех же событий `notify()`: шаг
закрывается пришедшим статусом, ожидание называет себя подтверждением или ответом, а шаг,
который только планировался, со списка снимается. Окно планировщика показывает
незавершённые запуски (`WorkflowStore.unfinished()`) и продолжает выбранный: запрос и
режим берутся из записи, шаги до перезапуска помечены отдельно.

`ApprovalDialog` ведёт обратный отсчёт по остатку заявки (`ApprovalAuthority.remaining` —
чтение, не власть) и по его истечении отвечает отказом сам: снимок мёртв, и держать им
задачу не за что. Это следствие длинной задачи — час работы против 60 секунд на снимок.

`PlannerWorker` владеет asyncio loop внутри QThread. Prompts содержат случайный request ID;
UI отвечает через thread-safe callback, совпадение ID защищает от запоздалого ответа.
Worker получает opaque ApprovalToken, но не issuer. Stop/close отменяют Runner, pending
approval и adapter; завершение собирает результаты до уничтожения worker и BrowserHost.
У каждого окна свой временный browser context и outbox, общий файл минимального audit.

OpenAI adapter использует фиксированный Responses HTTPS endpoint через aiohttp. Каждый
шаг — самостоятельный запрос с bounded context; без server-side conversation/response ID,
`store=false`, без hosted tools и parallel tool calls. Строгие схемы получены из Pydantic
metadata реестра. Ответ разбирается как данные и повторно проходит локальную валидацию.
OfflineProvider — отдельный набор учебных рецептов, позволяющий проверить UI без LLM.

OS credentials вынесены в platform adapter: конкретные WinVaultKeyring/macOS Keyring,
без динамического выбора backend. Короткоживущий subprocess передаёт ключ в приватный
pipe за срок до 8 секунд; cancellation завершает и собирает процесс. Setup CLI читает
ключ через getpass в TTY. Адаптер не читает OPENAI_API_KEY и не пишет ключ в файлы/SQLite.

## Этап 6: Voice

`voice/contracts.py` задаёт bounded AudioClip/Transcript, Recorder/Recognizer/Speaker и
детерминированный spoken_result. `voice/local.py` реализует локальный subprocess transport.
`platforms/audio.py` изолирует PortAudio/sounddevice, Vosk и системные TTS drivers.
Необязательные библиотеки импортируются внутри helper; текстовый режим работает без них.

`ui/voice_panel.py` владеет видимым push-to-talk, выбором локальной модели, состоянием,
отменой и пятиминутным deadline. `ui/voice_worker.py` выполняет async adapters в QThread,
отменяет/собирает дочерние процессы и подавляет устаревшие результаты. Все voice workers
исключают одновременные capture/STT/TTS. Аудио не сохраняется и не отправляется по сети.

Transcript → редактируемая команда PlannerWindow → ручной запуск → существующий Runner
→ PermissionEngine. Voice не получает ApprovalAuthority: `voice/approval.py` выбирает из
снимка одну произносимую контрольную деталь и возвращает вердикт по расшифровке, а token
по-прежнему выдаёт `ApprovalDialog` через UI-owned authority и записывает канал
(`Channel.UI` / `Channel.VOICE`) в audit. Инструмент без детали голосового канала не имеет.
После завершения Runner опционально озвучивает короткую сводку из engine outcomes.
Dashboard microphone только открывает планировщик.

## Отчёт о сделанном

`core/report.py` — единственное место, где собирается фраза «что я сделал». Источников
ровно два: снимок действия (`Step.payload`, та же нормализованная строка, которую владелец
видел в подтверждении) и исход движка. Ответы инструментов и сервисов туда не попадают,
как и напечатанный текст, тело письма и любые секреты; каждое поле ограничено по длине.

`written()` идёт в главное окно и в вывод планировщика и может называть адрес, путь и хост.
`spoken()` уходит в голос и опускает то, что русский локальный синтез не произнесёт, а также
ограничивает число перечисленных действий. Инструмент, которого нет в таблице `DEEDS`, не
превращается в предложение: про него отчёт молчит. Эффект, который мог быть выдан, но не
подтверждён, называется неподтверждённым.

## Рутины фазы B

`core/routines/contracts.py` задаёт рутину как доверенный код: `observe` возвращает
очередное фиксированное наблюдение, `notice` — предложения с ограниченным текстом и
точными аргументами. Модель в фоне не участвует вовсе, поэтому ответ сервиса не может стать
именем инструмента, аргументом или расписанием.

`core/routines/runner.py` выполняет цикл как обычный run: `WorkflowStore.start`, журнал,
`step_key`, движок разрешений и audit. Наблюдение допускается только `SAFE`; обратимое
действие выполняется, лишь когда владелец разрешил это конкретной рутине; `CONFIRM` не
выполняется никогда и попадает в очередь. `ApprovalAuthority` рутине не передаётся.
`already_issued` смотрит последние запуски журнала, поэтому уже выданный эффект не
повторяется и после перезапуска процесса.

`core/routines/state.py` хранит три выключателя, время последнего запуска и дневной счётчик
в `routines.json`; нечитаемый файл означает «всё выключено». `core/routines/proposals.py` —
очередь в памяти: TTL 30 минут, не более 20 поводов, повторный повод не дублируется, а
пропавший из последнего наблюдения удаляется.

`ui/routine_panel.py` владеет таймером (30 секунд), выполняет цикл в `QThread` и открывает
предложение через `engine.prepare` в тот же `ApprovalDialog`. Уведомлений нет: панель просто
перерисовывается.

## Память этапа 7

`memory/models.py` — строгие Entry/Hint/MemoryContext; `memory/store.py` — SQLite profile,
транзакции, конфликт редактирования, TTL и bounded failure; `memory/session.py` — явные
временные метки, monotonic TTL и reset. `ui/memory_panel.py` отделяет UI edit/select от
QThread I/O. Это пользовательские настройки, не model-callable tools. Планировщик не имеет
API записи/удаления памяти. Задача получает immutable выбранный MemoryContext через Worker,
Runner передаёт его провайдеру как данные и освобождает после завершения. Контактная
неоднозначность проверяется до первого provider call; targets принимаются только из
наблюдений этой задачи. Опциональный cloud использует отдельное согласие на метки.


## Outlook

Почтовая сессия принадлежит PlannerWindow. Один реестр содержит старые инструменты и
шесть `outlook.*` инструментов. Ручная панель и Runner используют один PermissionEngine.
MSAL/OS storage работают в killable helper; Graph HTTP — bounded asyncio в Qt worker.
Профиль/адрес проверяются до POST; session identity инвалидируется при переключении.
Черновики и байты вложений остаются RAM-состоянием окна. Подробнее: [MILESTONE_8.md](MILESTONE_8.md).

## Установка (этап 9, native-приёмка pending)

`installation/` — отдельно запускаемый stdlib bootstrap, без регистрации инструментов
и без исполнения при обычном старте. PowerShell готовит runtime; Python готовит
проверяемый неактивный слот с venv/assets и атомарно меняет указатель после native smoke
и свежего visible-window receipt. Перезапуск восстанавливает компоненты, не касается
профиля/SQLite/OS credentials. Stable launcher не зависит от пути репозитория.
См. [MILESTONE_9.md](MILESTONE_9.md).
