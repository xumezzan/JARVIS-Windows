# Workspace MCP и Microsoft Calendar — этап 3

## Что меняется

Новый jarvis.mcp подключает явно настроенные WorkspaceProfile.mcp_servers.
Legacy connectors.mcp, его mcp.json, manifest review и уровни ROUTINE/CONFIRM/BLOCKED
сохранены. Новый workspace-контракт по запросу владельца поддерживает stdio и
SAFE/CONFIRM; эти настройки не берутся из annotations или инструкций сервера.

Календарь использует существующие MailSession, ProcessCredentials и MSAL public
client. Calendars.ReadWrite уже входит в SURFACES["mail"]; второй аккаунт, секрет
приложения, новые environment keys и второй OAuth-поток не добавляются.
Legacy calendar.list/create/update/cancel сохраняются.

## Настройка и просмотр MCP

Серверы хранятся в ограниченном атомарном workspace_profile.json. Пример явно
выбранного владельцем HTTPS-сервера:

~~~python
from jarvis.mcp.models import McpServerConfig

server = McpServerConfig(
    server_id="company",
    name="Company MCP",
    transport="http",
    url="https://mcp.example.com/mcp",
    allowed_tools=("search", "create_issue"),
    default_risk="CONFIRM",
)
~~~

Добавьте server в WorkspaceProfile.mcp_servers через WorkspaceStore.save,
сохранив остальные поля существующего профиля. Для HTTP ключ вводится штатной
командой python -m jarvis.security.credentials set mcp_company: пароль/ключ
не передаётся аргументом командной строки и не хранится в JSON.

Для stdio вместо URL указывается фиксированный argv:

~~~python
server = McpServerConfig(
    server_id="local_service",
    name="Trusted local MCP",
    transport="stdio",
    command=("/absolute/path/to/trusted/python", "-m", "installed_mcp_server"),
    allowed_tools=("search",),
    default_risk="CONFIRM",
)
~~~

На Windows первый элемент — абсолютный путь к доверенному executable, например
к python.exe его виртуальной среды. Команда не является shell-строкой.
Сервер устанавливает владелец; Jarvis не скачивает программы и не строит argv
из команд, страниц, результатов сервера или аргументов инструмента.
Shell hosts и относительные executable запрещены.
stdio запускает доверенную программу с правами владельца, это не OS sandbox.

Откройте обычный Qt PlannerWindow → «Workspace MCP». Кнопка получения manifest
запускает только initialize/initialized/tools/list; для stdio она явно запускает
настроенную программу. Имена, описания, схемы, разрешённый набор и итоговый риск
показаны обычным текстом. Только кнопка владельца сохраняет просмотренный набор.
Если проверка перед сохранением обнаруживает изменение профиля другим окном,
сохранение отказывается его перезаписывать.
После просмотра нужен перезапуск: уже работающий registry остаётся sealed.

До просмотра tools не регистрируются, при обычном старте сети и subprocess нет.
Просмотр закреплён SHA-256 по identity сервера, transport, argv/URL, allowlist,
именам, описаниям и полным входным схемам. Изменение уровня риска не маскирует
изменение manifest. Перед каждым tools/call клиент повторно получает список
в том же соединении/stdio-процессе; несовпадение запрещает вызов.
workspace_profile.json недоступен инструментам files.* и fs.*.

## Протокол, схемы и лимиты

Реализован совместимый профиль MCP 2025-06-18:
[lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)
и [transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
Клиент проверяет protocolVersion, JSON-RPC version, exact request ID, result/error
и tools capability, затем посылает notifications/initialized.
Инструкции сервера, annotations, sampling, elicitation и callbacks не исполняются.

HTTP использует общий ServiceTransport, один фиксированный публичный HTTPS
endpoint, OS credentials, без cookies, environment proxies, redirects и retries.
Этот ограниченный профиль поддерживает stateless JSON responses. SSE, HTTP
session headers, потоки и новые версии протокола не объявлены поддержанными:
несовместимый сервер даёт отказ, без fallback или повторения эффекта.
Перед каждым HTTP-запросом проверяются URL и DNS; как и для Chromium preflight,
это не OS firewall и не pinning DNS-адреса в общем ServiceTransport.
Локальный сервис подключается через явно выбранный stdio.

stdio: newline-delimited UTF-8 JSON, stderr отбрасывается, временный cwd,
ограниченный environment без API keys/proxies/PYTHONPATH, ограниченный stdout.
При закрытии/отмене завершается принадлежащее клиенту дерево процессов.
Удалённый эффект отмена ожидания не отзывает.

Схема — ограниченный плоский object с максимум 16 именованными primitive полями:
string/integer/number/boolean/null, required, enum, строковые и числовые границы.
Не поддерживаются вложенные объекты/массивы, refs, regexes, schema downloads
и генерация Python-типов из серверной схемы. Неподдерживаемый manifest отвергается.
Одна строгая McpArguments проверяется pure policy hook при prepare, включая
simulation. В catalog экспортируется просмотренная схема без серверных описаний.

Имена инструментов: mcp.company__search, mcp.company__create_issue.
Normalization collisions и имена длиннее лимита function API отвергаются.
До 8 серверов, 32 manifest tools на сервер, 24 allowed tools, до 8000 UTF-8 байт
аргументов, 262144 байт wire response, 16000 байт текстового результата.
Non-text content и isError не превращаются в успешный ответ.
Все вызовы проходят PermissionEngine/AuditLog; аргументы audit всегда [REDACTED].
Риск берётся из owner config, а create/update/delete/send/post/publish/pay/write
и подобные мутации получают как минимум CONFIRM. Existing PermissionMatrix
может только ужесточать риск, включая legacy mcp_<server_id> и общий mcp.
Таймаут/отмена ограничены ToolSpec и общим Runner budget. Повторов нет.
Ответ MCP подтверждает ответ сервера, не бизнес-эффект в стороннем сервисе.

## Microsoft Calendar

Все три инструмента зарегистрированы в основном PlannerWindow через composition,
в compact PlannerWindow и DesktopService:

| Tool | Риск | Поведение |
| --- | --- | --- |
| calendar.list_events | SAFE | GET /v1.0/me/calendarView за ограниченный UTC-интервал |
| calendar.create_draft_event | SAFE | Полный event snapshot только в RAM текущей MailSession |
| calendar.create_event | CONFIRM | Один POST /v1.0/me/events и независимый GET созданного объекта |

Получите outlook.account в текущей задаче. Даты — точные UTC строки с Z;
псевдонимы участников не заменяют явные email владельца. Runner запрашивает
уточнение для неподтверждённых адресов и требует свежий local_draft из этой задачи.
Черновик сам ничего не отправляет. Graph автоматически приглашает attendees
при реальном создании: [create event](https://learn.microsoft.com/en-us/graph/api/user-post-events?view=graph-rest-1.0).

create_event принимает account, draft_id и полный event. RAM snapshot должен
точно совпасть с утверждённым содержимым. Draft потребляется перед единственным
POST, transactionId привязан к draft_id. Смена аккаунта/отключение очищает RAM.
Неопределённый POST не повторяется. Read-back проверяет ID, организатора,
subject/body, start/end, location, весь набор attendees и transactionId.
Несовпадение оставляет результат неподтверждённым, не «создано».
Доставка приглашений не утверждается: delivery_verified всегда false.

calendarView не следует за недоверенным nextLink и возвращает truncated при
неполной странице: [calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0).
Transport ограничен graph.microsoft.com/v1.0/me и фиксированными маршрутами;
новый контракт не добавляет PATCH/DELETE, /users или shared calendars.
Если старый MSAL consent не содержит календарный scope, владелец подключает
аккаунт штатной кнопкой Outlook; автоматического логина нет.

## Проверка

~~~text
.venv/bin/python -m pytest -q tests/unit/test_mcp_bridge.py tests/unit/test_calendar_tools.py tests/integration/test_workspace_integrations_ui.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build
~~~

На Windows используется .venv\Scripts\python.exe. Fixtures не обращаются
к живому Microsoft, MCP-серверам или OS credential store. Реальный stdio fixture
запускает только локальную синтетическую программу. UI fixtures проверяют
просмотр и конфликт профиля. Реальные MCP-сервисы, OAuth и создание встречи
в пользовательском Microsoft 365 требуют отдельной live-приёмки.

Новые схемы сохраняют общий лимит OpenAI 262144 байта. Повторяющиеся длинные
Windows labels сериализуются без потерь через untrusted_observation_names/name_ref;
Runner и canonical observations остаются полными. Проверка восстанавливает все
96 labels и точные target metadata, сохраняя существующий SoM fixture budget 180000.

Локальная проверка 2026-10-26: Ruff, format-check, mypy, pytest
(1835 passed, 23 skipped), GUI smoke, wheel/sdist build, pip check и 11 Electron
tests прошли с кодом 0. Последнее уточнение read-back отдельно проверено всеми
16 Calendar tests: минимальный 201 принимается по ID, а полный объект проверяется
только GET; повреждённый список участников не считается подтверждением.
Обычный python -m jarvis запущен с временным data_dir;
проверены главный экран и Workspace MCP, затем приложение закрыто штатно с кодом 0.
Сервисы/ключи/микрофон не подключались.

Контрольный SoM probe: PlannerWindow 164798 байт, compact 139484,
DesktopService 138423. Сжатие labels не меняет исходные Step observations.
На этом Mac Qt-проверки используют QT_QPA_PLATFORM=offscreen и
QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/jarvis-multimodal-qt-runtime/plugins/platforms
из-за hidden attributes существующей .venv; для обычного видимого запуска выбран cocoa.
Windows CI проверяет commit PR отдельно; Mac fixtures не заменяют Windows
или live-service acceptance.
