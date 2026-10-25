# Интеграции: возможности, доступ и проверка

## Статус и метод проверки

Дата проверки официальной документации: **2026-09-15**. «Подтверждено документацией»
означает наличие описанного API и его ограничений, а не работающий адаптер JARVIS,
доступ конкретного пользователя, оплаченный тариф или успешную live-проверку.
В этой задаче реальные аккаунты не подключались и внешние действия не выполнялись.
Перед реализацией каждого адаптера повторно проверить документацию, версии API,
permissions, тариф, регион и доступ выбранного аккаунта; записать новую дату и результаты.

Сейчас Outlook M8 реализован локально; live OAuth/почта и native Windows остаются
непроверенными в M9. Остальные сервисы ниже — план. Новые возможности Outlook
(например, загрузка входящих вложений и специальные reply/search-сценарии) не считаются
реализованными по наличию Graph API. Текущий M8 запрашивает `User.Read`,
`Mail.ReadWrite`, `Mail.Send`; более узкие feature-specific grants в таблице —
требование будущего подключения, а не уже внедрённое поведение. Загрузка входящих
вложений не реализована. Ограничения текущего `/me`-адаптера сохраняются.
Порядок и зависимости этапов определяет [ROADMAP](ROADMAP.md); предлагаемый контракт —
[INTEGRATION_CONTRACT](INTEGRATION_CONTRACT.md), границы — [ARCHITECTURE](ARCHITECTURE.md)
и [SECURITY](SECURITY.md). Общие решения — [OPEN_DECISIONS](OPEN_DECISIONS.md),
сквозная приёмка — [PRODUCT_ACCEPTANCE](PRODUCT_ACCEPTANCE.md).

## Общие правила для каждого сервиса

- Разделить чтение, подготовку предложения в локальной RAM и внешнее изменение на
  отдельные зарегистрированные операции со строгими схемами и PermissionEngine.
  Внешний draft, создание записи, комментарий, приглашение, отправка и изменение
  требуют **CONFIRM**, если конкретная операция вообще разрешена. Более высокий risk
  не понижается: CRITICAL требует отдельного решения вне MVP, BLOCKED никогда не исполняется.
  Наличие OAuth scope или подключённого аккаунта не является approval.
- Ограниченное чтение доступно только в явно разрешённом контексте аккаунта/организации.
  API-read может передавать запрос провайдеру, расходовать квоту или иметь цену;
  wizard должен раскрывать это до enablement. Не включать платные API/тарифы автоматически.
  Передача содержания другому AI-провайдеру требует отдельного cloud disclosure.
- Connect/disconnect, выбор аккаунта/tenant/workspace/region, перечень предоставленных
  прав, минимальная безопасная read-проверка, истёкшая авторизация и повторный вход
  обязательны. Account switch/disconnect инвалидирует контекст и ожидающие approvals.
  MFA, системный consent и ввод секретов выполняет пользователь в доверенном UI.
- Секреты доступны только доверенному адаптеру через существующий OS credential store;
  не копировать `.env`/SQLite-рецепты из примеров провайдеров. OAuth-секрет общего
  приложения нельзя раздавать в desktop package. Если API требует confidential client,
  способ безопасного подключения должен быть решён до реализации соответствующего этапа.
- Account/organization/target/content/attachments и source ID входят в неизменный
  снимок действия; изменение или отмена инвалидирует approval. Read-back проверяет
  результат, а не только HTTP-успех. Контент сервисов и вывод модели остаются недоверенными.
- Ограничить страницы, байты, время, параллелизм и расходы. Обрабатывать 401/403,
  недоступный сервис, 429/Retry-After, неполную выборку и отмену без зависания UI.
  Повтор чтения допустим только в пределах отдельно заданной политики и task budget;
  simulation не вызывает адаптер. **Не повторять внешнее действие с неопределённым
  результатом.** Использовать идентификаторы, доступную идемпотентность и read-back
  для сверки, но не как основание повторно отправить действие.
- Недоступный источник означает «нет покрытия», а не «ничего нового». Подключение
  не разрешает автоматическое ingestion в память, передачу записи третьим лицам
  или выполнение background routine. У каждого адаптера собственная live-приёмка
  на выбранных пользователем данных; тест подключения не пишет рабочие данные.

В таблице **R** — разрешённое ограниченное чтение; **P** — локальная подготовка;
**C** — внешнее действие через CONFIRM; **D** — отдельное решение о политике,
возможность отключена до такого решения. Эти сокращения не заменяют risk schema.

## Матрица сервисов

| Сервис | Желаемые операции | Подтверждено документацией | Подключение / аккаунты | Минимальные права: примеры | Риск | Ограничения и открытые проверки | Источник | Этап |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Asana | Проекты, задачи, исполнители, сроки, статусы, комментарии; задачи из источников | Task CRUD, project tasks, comment stories | OAuth, выбранный workspace; desktop secret exchange требует решения | `tasks:read`, `projects:read`, `stories:read`; отдельно `tasks:write`, `stories:write` | R/P/C | Custom statuses зависят от схемы; read/write scopes независимы; live доступ неизвестен | [OAuth/scopes](https://developers.asana.com/docs/oauth), [детали](#asana--m13) | M13 |
| Claude | Заменяемый provider; анализ и подготовка | Messages API, client tool schemas | Отдельный Console/API account, ключ выбранного workspace | Не-admin API key; OAuth scopes неприменимы к выбранному key flow | Cloud disclosure/budget; P; эффекты отдельно C | Подписка чата не включает API; без hosted tools и собственной authority | [API](https://platform.claude.com/docs/en/api/overview), [детали](#claude--m14) | M14 |
| Outlook mail | Search/read, вложения, drafts, send/reply | Mail/attachment/draft/send APIs; search зависит от endpoint/account | MSAL public client + PKCE; personal или work/school; только `/me` в M8 | `Mail.ReadBasic` для metadata; `Mail.Read`; отдельно `Mail.ReadWrite`, `Mail.Send` | R/P/C | 202 ≠ доставка; новые search/reply/вложения требуют расширения; shared mailbox исключён | [Mail](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0), [детали](#outlook-mail--m8-m9-m13) | M8/M9; расширения M13 |
| Outlook Calendar | Расписание, конфликты, создание/перенос встреч | calendarView, create/update event; getSchedule для work/school | Отдельный calendar consent к выбранному Microsoft account/calendar | `Calendars.ReadBasic` / `Calendars.Read`; отдельно `Calendars.ReadWrite` | R/P/C | Personal account не поддерживает getSchedule; recurrence, timezone и уведомления точны | [Calendar](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule?view=graph-rest-1.0), [детали](#outlook-calendar--m13) | M13 |
| Teams | Chats/channels/messages, meeting info, send/reply | Graph message reads/sends и onlineMeeting metadata | Microsoft 365 work/school tenant; membership/consent | `Chat.Read`, `ChannelMessage.Read.All`, `OnlineMeetings.Read`; send отдельно | R/P/C | Personal accounts не поддерживаются; chat-send scope discrepancy требует проверки | [Graph](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0), [детали](#teams--m15) | M15 |
| Notion | Search/read, страницы и записи database | Title search, data-source query, page/block create/update | Public OAuth с page picker или workspace internal connection | `Read content`; отдельно `Insert content`, `Update content` | R/P/C | Только разрешённые страницы; title search неполон; desktop OAuth secret design | [Capabilities](https://developers.notion.com/reference/capabilities), [детали](#notion--m15) | M15 |
| Fireflies.ai | Записи, transcripts, summaries, action items | GraphQL transcript search/read; generated summaries; записи зависят от тарифа | User API key, выбранный account/workspace | Granular read scope не подтверждён; локальный allowlist | R/P; позже C | Audio/video URLs Pro+, expiry; чтение не разрешает распространение | [Transcript](https://docs.fireflies.ai/schema/transcript), [детали](#firefliesai--m16-m32) | M16; media effects M32 |
| Loom | Найти/read metadata, links, transcript; отдельно create/upload/distribute | Именованные Loom tools официального Rovo MCP v2; не общий REST API | OAuth 2.1; Loom workspace, связанный с Atlassian site | `read:loom:agent-interface`; отдельно write/search scopes | R/P; позже C/D | Search/credits/account eligibility проверить; SDK capture отдельный boundary | [Supported tools](https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/), [детали](#loom--m16-m32) | M16; create/upload/distribute M32 |
| Instagram | Read/write comments/replies; отдельно DMs/posts | Comment management/replies на own media professional account; messaging/publishing APIs | Instagram Login или Facebook Login; Business/Creator | `instagram_business_basic`, `instagram_business_manage_comments`; DM/publish отдельно | R/P/C | Не arbitrary public comments; eligibility/review и message window требуют проверки | [Meta Instagram](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00), [детали](#instagram--m17-m31) | M17; DMs/posts M31 |
| Facebook | Read/write comments/replies; отдельно Messenger/posts | Page auth, Messenger и Page Reel flow; точные comment endpoints ещё investigation | Facebook Login, выбранная managed Page, роль/task | `pages_messaging` для Messenger; comment grant list требует проверки | R/P/C | Нет обещания personal profile/inbox; 24-hour/eligible opt-in для Messenger | [Meta Messenger](https://www.postman.com/meta/messenger-platform-api/documentation/iyp204x/messenger-platform-api), [детали](#facebook--m17-m31) | M17; DMs/posts M31 |
| QuickBooks | Invoices, payment status, unpaid summary; records write отдельно | Accounting entity queries/reads и mutations; Balance/DueDate | QuickBooks Online company `realmId`, OAuth 2.0; secret exchange требует решения | `com.intuit.quickbooks.accounting` шире read; payment scope исключён | R/P; позже C/D | Online ≠ Desktop; balance ≠ settlement; не money movement | [Intuit API](https://www.postman.com/intuit-developer/intuit-developer-quickbooks-online-accounting-api/overview), [детали](#quickbooks--m18-m29) | M18; records write M29 |
| AWS | Resource inventory, status, costs; mutations отдельно | Именованные service reads, STS identity, CE cost; Health при подходящем плане | Federation/role, short-lived credentials; account/region/service selection | Точные actions, например `ec2:DescribeInstances`, `ce:GetCostAndUsage` | R с cost controls; позже D | CE reads платные; Health entitlement; никаких blanket admin grants | [IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html), [детали](#aws--m19-m30) | M19; условные mutations M30 |
| Microsoft admin office | Users/groups/licenses read; admin changes отдельно | Graph directory/subscription reads; продукт условно Microsoft 365 Admin Center | Work/school tenant, delegated OAuth, роль/admin consent | `User.ReadBasic.All`, минимальный group read, `LicenseAssignment.Read.All` | R/P; позже D | Продукт/workflows уточнить; Outlook consent не admin consent | [Users](https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0), [детали](#microsoft-admin-office--m19-m30) | M19; условные mutations M30 |
| Omadli Group | Конкретные внутренние workflows после discovery | Известно только: собственная система компании; API/операции неизвестны | URL/auth/role предоставляет владелец; API first, UI отдельно | Определить по документированной роли/операции | Пока не классифицирован; R/P/C/D после discovery | Не придумывать URL, модель данных или coverage | Запрос пользователя; [детали](#omadli-group--m20-m24) | M20 API pilot; M24 UI pilot при отсутствии API |
| 1Password | Получение конкретного секрета доверенным адаптером | SDK desktop auth и service accounts; Windows описан, live не проверен | Desktop consent или узкий service account/vault; OS store сохраняется | Read выбранного vault + локальный item/field allowlist | Внутренняя credential operation; эффекты отдельно | Нет model-visible vault tools; не импортировать весь vault/не заменять OS store | [SDK](https://www.1password.dev/sdks), [детали](#1password--m21) | M21 |

## Asana — M13

API поддерживает задачи с assignee, completion и due dates; комментарии представлены
stories. Пользовательское поле «Status», section и завершённость нельзя считать одной
универсальной сущностью: прочитать схему выбранного проекта и явно сопоставить поля.
`tasks:write` не включает `tasks:read`; `stories:write` не включает чтение комментариев.
`users:read` добавляется только при необходимости расширенных данных исполнителя.
[Task model](https://developers.asana.com/reference/tasks), [create](https://developers.asana.com/reference/createtask),
[update](https://developers.asana.com/reference/updatetask), [comment](https://developers.asana.com/reference/createstoryfortask).

Регистрация OAuth должна разрешать выбранный workspace. Документация описывает PKCE,
но требует client secret при token exchange; это не доказательство secretless native flow.
Решить user-owned registration/допустимый token flow либо отдельно обоснованный безопасный
exchange. PAT существует, но не является автоматически минимальным доступом.
Секрет общего приложения не включается в installer. Лимиты зависят от free/paid domain,
search ограничен отдельно. [OAuth](https://developers.asana.com/docs/oauth),
[scopes](https://developers.asana.com/docs/oauth-scopes), [PAT](https://developers.asana.com/docs/personal-access-token),
[limits](https://developers.asana.com/docs/rate-limits).

**Приёмка:** прочитать выбранный project; подготовить задачу со ссылкой на разрешённый
email/meeting; показать workspace/project, assignee ID/name, title/body, срок и status;
после точного approval создать один раз и прочитать обратно поля и ссылку. Подмена
проекта/исполнителя, expired approval и lost response не создают дубль. Connect probe
не создаёт задачу. Live workspace/role/plan остаются непроверенными.

## Claude — M14

Messages API возвращает ответ и client `tool_use` proposals со схемами, а их исполнение
остаётся приложению. Server tools существуют у провайдера, но не включаются в JARVIS
по умолчанию. Claude работает как сменный provider либо ограниченный инструмент анализа;
дополнительная модель не меняет режим, разрешения, account или provider settings.
[Messages/API](https://platform.claude.com/docs/en/api/overview),
[tool use](https://platform.claude.com/docs/claude/docs/tool-use).

Нужен отдельный Console/API account и user-owned не-admin ключ, ограниченный выбранным
workspace, где это доступно. Ключ хранится существующим credential helper; точные
model availability, расходы и требования к обработке корпоративного содержания
проверяются перед enablement. Chat subscription не доказывает API entitlement.
[Authentication](https://platform.claude.com/docs/en/manage-claude/authentication),
[отдельная API-подписка/оплата](https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-subscription-pro-max-team-or-enterprise-plans-why-do-i-have-to-pay-separately-to-use-the-claude-api-and-console).

**Приёмка:** после cloud/spend consent выполнить bounded запрос на синтетическом
несекретном тексте; валидировать результат и отклонить malicious/unknown tool proposals.
Отозванный ключ, неверная модель, превышение budget и отмена завершаются фактическим
статусом. Предложенное внешнее действие получает отдельный exact approval обычным UI.
Connection check не запускает платную генерацию без disclosure.

## Outlook mail — M8, M9, M13

M8 сохраняет один signed-in `/me` account, MSAL authorization code + PKCE, OS cache,
home ID/Graph ID/address binding, явные connect/disconnect и полный approved snapshot.
Документация Graph подтверждает metadata с `Mail.ReadBasic`, body/attachment reading
с `Mail.Read`, remote draft с `Mail.ReadWrite`, send с отдельным `Mail.Send` для personal
и work/school accounts. Это не расширяет зарегистрированные M8 tools автоматически.
[List messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0),
[attachments](https://learn.microsoft.com/en-us/graph/api/message-list-attachments?view=graph-rest-1.0),
[draft](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0),
[auth code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow).

Microsoft Search API message search документирован для собственной почты work/school
account; personal/delegated mailbox coverage нельзя обещать по этой документации.
Расширения search, incoming attachments и reply semantics в M13 требуют конкретных
endpoint/schema/provenance проверок. Shared mailboxes, aliases и generic Graph POST
остаются за пределами существующего адаптера.
[Search limitations](https://learn.microsoft.com/en-us/graph/search-concept-messages).

**Приёмка:** выбранный пользователем test account/recipient, ограниченное read,
remote draft read-back, точный preview To/Cc/Bcc/subject/body/attachment hashes,
UI approval и однократный send. `202 Accepted` означает принятый запрос, а не доставку;
не повторять uncertain send. RAM draft SAFE, remote draft/send/reply CONFIRM. Inbound
attachment fetch не считается реализованным из-за поддержки исходящих выбранных файлов.
Native/live M8 gates закрываются в M9; reply thread/source binding и новое поведение —
отдельной приёмкой M13.
[Send semantics](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

## Outlook Calendar — M13

`calendarView` с `Calendars.ReadBasic` документирован для personal и work/school
accounts; полный контент может потребовать `Calendars.Read`. Own-calendar create/update
использует `Calendars.ReadWrite`; free/busy `getSchedule` не поддерживает personal
delegated accounts. Неизвестная доступность участника означает неполную проверку
конфликтов, а не свободное время. Calendar scope запрашивается отдельно от mail.
[Calendar view](https://learn.microsoft.com/en-us/graph/api/user-list-calendarview?view=graph-rest-1.0),
[getSchedule](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule?view=graph-rest-1.0),
[create](https://learn.microsoft.com/en-us/graph/api/calendar-post-events?view=graph-rest-1.0).

Изменение встречи может отправить attendee updates. Указать organizer/calendar,
старое/новое время, zone, recipients и recurrence scope; не потерять existing online
meeting body. Group/shared calendars и online-meeting creation требуют отдельной
поддержки. Проверять поддерживаемую mailbox timezone.
[Update semantics](https://learn.microsoft.com/en-us/graph/api/event-update?view=graph-rest-1.0).

**Приёмка:** read schedule → локальное предложение → точный before/after CONFIRM →
однократный create/reschedule → read-back event ID, времени и участников. Проверить
DST ambiguity, stale conflict data, series/occurrence, недоступного attendee, denial
и lost response. `transactionId` помогает дедупликации, но не разрешает retry uncertain
операции и не доказывает получение приглашения.

## Teams — M15

Graph v1.0 документирует chat reads с `Chat.Read`, channel reads с
`ChannelMessage.Read.All`, meeting metadata с `OnlineMeetings.Read` для work/school
accounts. Personal Microsoft accounts у этих endpoints не поддерживаются. Выбрать
tenant, проверить лицензию/роль/membership и фактический admin consent. Meeting metadata
не включает автоматически право читать/распространять transcript или recording.
[Chat](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0),
[channels](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0),
[meeting](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-get?view=graph-rest-1.0).

Для channel send/reply документирован `ChannelMessage.Send`. На дату проверки таблица
chat-send в endpoint docs также показывает `ChannelMessage.Send`, тогда как permissions
reference определяет `ChatMessage.Send` для пользовательских chat messages. **Это
несогласованность официальных источников:** точный chat endpoint/scope должен пройти
проверку в выбранном test tenant; не запрашивать оба или широкие права ради обхода ошибки.
App-only migration и RSC — другие схемы установки/consent, не штатная отправка от
личного пользователя по умолчанию.
[Send endpoint](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0),
[ChatMessage.Send](https://learn.microsoft.com/en-us/graph/permissions-reference#chatmessagesend).

**Приёмка:** прочитать выбранный chat/channel; подготовить сообщение локально; после
CONFIRM, связанного с tenant/account/target/members/reply parent/text/mentions, отправить
один раз и прочитать returned ID. Ошибка tenant, изменённая цель, отсутствие scope,
429 и uncertain result не приводят к публикации или дублю. Connect сам не отправляет.

## Notion — M15

Документированы title search по разрешённым pages/data sources, filtered data-source
query, page/block creation/update. Это не обещание полного текстового поиска по всей
компании. Проверить schema выбранного data source; computed/system properties нельзя
записывать как обычные поля. Использовать согласованный `Notion-Version`; на дату
проверки документация показывает `2026-03-11`.
[Search](https://developers.notion.com/reference/post-search),
[limitations](https://developers.notion.com/reference/search-optimizations-and-limitations),
[query](https://developers.notion.com/reference/query-a-data-source),
[create page](https://developers.notion.com/reference/post-page).

Public OAuth использует page picker и client secret при обмене токена: desktop exchange
требует решения до реализации. Альтернатива для выбранного workspace — internal
connection, создаваемое workspace owner, с явно расшаренными страницами. PAT действует
с правами пользователя и не использует page picker; это не минимальный default.
Read/Insert/Update content независимы, для проверки write нужен read. User/email
capability не добавлять без необходимости; shared parent включает дочерние страницы.
[Authorization](https://developers.notion.com/guides/get-started/authorization),
[capabilities](https://developers.notion.com/reference/capabilities).

**Приёмка:** прочитать известную shared page → локальный diff → CONFIRM с workspace,
parent/page/data-source IDs, schema и точными properties/blocks → write один раз →
read-back. Unshared page, stale schema, revoked token и неполный search отражаются честно.
Ограничить запросы по connection и workspace; 429 или lost response не создают дубль.
[Request limits](https://developers.notion.com/reference/request-limits).

## Fireflies.ai — M16, M32

GraphQL `transcripts` документирует keyword/date/organizer/participant filtering,
pagination, source links и speaker/timestamp text. `summary.action_items` — сгенерированная
строка, а не проверенная задача с надёжным owner/date. API доступен на разных тарифах,
но audio/video URLs требуют Pro+ и истекают через 24 часа; video зависит от наличия
записи и настройки recording. Тариф и реальные права пользователя не проверялись.
[Query](https://docs.fireflies.ai/graphql-api/query/transcripts),
[summary](https://docs.fireflies.ai/schema/summary),
[recording fields](https://docs.fireflies.ai/schema/transcript),
[API availability](https://guide.fireflies.ai/articles/3737786777-learn-about-fireflies-api).

Использовать user API key через native setup и OS store. Проверенная документация
этого flow не определяет granular read-only OAuth scopes; не выдумывать их.
Установить локальные account/workspace/operation bounds; Super Admin bypass privacy
не выбирается по умолчанию. Ограничения запросов зависят от тарифа; query возвращает
не более 50 transcripts за запрос, а неполная выборка отображается явно.
[Authorization](https://docs.fireflies.ai/fundamentals/authorization),
[Super Admin](https://docs.fireflies.ai/fundamentals/super-admin),
[limits](https://docs.fireflies.ai/fundamentals/limits).

M16 только читает разрешённые источники и готовит source-linked предложения.
Создание Asana task/calendar event использует отдельный CONFIRM целевого адаптера.
`uploadAudio` документирован для удалённого URL, но не включён в запрошенный read scope:
наличие API не добавляет upload capability в JARVIS. Нельзя делать файл публичным ради API.
Bot invitation и запись также не входят; distribution оценивается отдельно на M32
только при выборе такого сценария и проверенном official route. [Upload](https://docs.fireflies.ai/graphql-api/mutation/upload-audio).

**Приёмка:** найти известную разрешённую meeting и отклонить недоступную; сохранить URL
и timestamps, отделить оригинал transcript от AI summary. Выделить action item с
цитируемым источником; неоднозначный owner/date уточнить. До destination approval
нет удалённой задачи/встречи. Missing transcript, plan gate, revoked key, 429 и partial
response дают неполное покрытие, а не «задач нет». M32 дополнительно проверяет exact
source/audience и отсутствие retry uncertain distribution; upload/processing приёмка
для Loom описана ниже.

## Loom — M16, M32

Официальный Loom FAQ говорит об отсутствии общего open API; одновременно официальный
Atlassian Rovo MCP v2 описывает конкретные Loom tools. Поэтому планируется именно
этот именованный интерфейс для Loom workspaces, связанных с Atlassian site, а не
универсальный Loom REST API. `getLoomVideo` возвращает metadata, optional transcript,
links/action items; есть own/shared listing, comments и временный MP4 URL. Полноту
поиска, keyword filtering и доступ legacy workspace нужно проверить на выбранном site.
[API FAQ](https://support.atlassian.com/loom/docs/does-loom-have-an-open-api),
[MCP tools](https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/).

Предпочтителен интерактивный OAuth 2.1, выбранный site/user context и
`read:loom:agent-interface`. Write — отдельный `write:loom:agent-interface`;
cross-app search — `search:rovo:agent-interface`, с отдельной оценкой credits.
API-token route зависит от admin enablement. Доступ к tool groups контролируется
администратором; наличие Loom приложения не доказывает eligibility.
[Auth](https://support.atlassian.com/atlassian-ai-gateway/docs/authentication-and-authorization/),
[admin permission groups](https://support.atlassian.com/security-and-access-policies/docs/Configure-Atlassian-Rovo-MCP-server-permission/),
[credits/setup](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/).

Доверенная обёртка M11 регистрирует только проверенные MCP operations/schema и проводит
их через PermissionEngine. Tool metadata/инструкции провайдера не устанавливают локальный
risk и не добавляют новые tools автоматически. M32 разделяет upload, publish, notify,
link distribution и visibility change. `shareLoomVideo` уведомляет пользователей,
но не изменяет visibility; widening access требует отдельной классификации и остаётся
выключенным до неё. LoomSDK recording имеет browser/cookie/privacy ограничения и
не совместимо по умолчанию с текущим script-disabled browser. Capture зависит от M24
и явного visible consent; native embedding/entitlement остаются investigation.
[SDK FAQ](https://support.atlassian.com/loom/docs/loomsdk-faq/),
[privacy](https://support.atlassian.com/loom/docs/use-looms-privacy-settings).

**Приёмка:** eligible site и least-privilege user; read known owned/shared video,
deny inaccessible video; missing transcript/search coverage видны. Access revocation
запрещает использовать stale cached content. У unlinked workspace честный setup-required
или unsupported. M32 binds hash/metadata/workspace/visibility/audience и отдельно
проверяет upload accepted, processing, published и notified; uncertain effect не повторяется.

## Instagram — M17, M31

Проверенная официальная Meta collection документирует Business/Creator accounts,
management/replies к comments на media подключённого аккаунта и media publishing.
Это не разрешает arbitrary comments на любой публикации или consumer-account access.
Instagram Login не требует linked Facebook Page; Facebook Login flow требует Page,
связанную с professional account. Выбрать один flow и минимальные права под него.
[Meta Instagram API](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00),
[Instagram Login](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login).

Instagram Login: `instagram_business_basic`, для comments отдельно
`instagram_business_manage_comments`; DMs — `instagram_business_manage_messages`,
publishing — `instagram_business_content_publish`. Facebook Login использует другую
семью scopes (`instagram_basic`, `instagram_manage_comments`, `pages_read_engagement`);
Page discovery/role и точный grant set проверить для выбранного flow.
[Scopes](https://www.postman.com/meta/instagram/folder/1z5vxzu/instagram-api-with-instagram-login),
[Facebook Login](https://www.postman.com/meta/instagram/folder/9cgqucg/instagram-api-with-facebook-login).

M17 ограничен подтверждёнными read/comment/reply operations. Direct Meta developer
guides не удалось извлечь в этой проверке; источники выше — Meta-owned official Postman.
До реализации повторно прочитать moderation, App Review/Advanced Access/business
verification требования; неподтверждённый top-level-comment endpoint не обещать.
[Moderation guide для повторной проверки](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/comment-moderation).

M31 отдельно проверяет DMs и publishing. По официальной messaging collection recipient
должен ранее написать professional account; group messaging не поддерживается.
Проверить текущие response windows и Requests-folder coverage. Comment reply не
является согласием на private reply. [Messaging](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api).

**Приёмка:** eligible test account, известный media/comment/source ID, локальный reply,
exact account/media/parent/content CONFIRM и read-back опубликованного результата.
Changed parent/wrong account блокируются; connect не публикует. Отдельные M31 tests
отклоняют ineligible DM recipient, проверяют message status без обещания прочтения,
а для posts проверяют format/ownership/processing/visibility и exact approved payload.

## Facebook — M17, M31

План ограничивается выбранными managed Pages. Официальная Meta Facebook collection
подтверждает Page token selection и Page Reel publishing/status flow; она не доказывает
все comment endpoints или personal-profile publishing. **Точные read/create/reply
comment endpoints и scopes остаются investigation gate M17.** Direct developer guides
не удалось извлечь при проверке, поэтому строки ниже не объявляют API готовым.
[Meta Facebook](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api),
[Pages guide для проверки](https://developers.facebook.com/docs/pages-api/posts/),
[comments reference для проверки](https://developers.facebook.com/docs/graph-api/reference/object/comments/).

Facebook Login, Page selection, допустимая роль/task и secure Page token обязательны.
`pages_show_list`, `pages_read_engagement`, `pages_read_user_content`,
`pages_manage_engagement` — кандидаты для проверки comment scopes, **не утверждённый
grant list**. `pages_manage_posts` также требует exact publishing endpoint/review check.
Не запрашивать всю группу «на всякий случай».
[Permissions для повторной проверки](https://developers.facebook.com/docs/permissions/).

Messenger M31 отдельно: официальная collection требует `pages_messaging`, Page actor
и сообщение от recipient в стандартном 24-hour window либо отдельно разрешённый
opt-in вне него. Не обещать доступ к personal inbox/friends или любому public content;
UI automation не обходит ограничения Meta.
[Meta Messenger](https://www.postman.com/meta/messenger-platform-api/documentation/iyp204x/messenger-platform-api).

**Приёмка:** сначала подтвердить operation/account/permission eligibility по актуальным
источникам; затем selected test Page/post/comment, scoped read, локальный текст,
exact Page/parent/content CONFIRM и read-back. Denied role, expired token и uncertain
response не дают публикацию/дубль. M31 независимо проверяет messaging window/recipient,
а publishing — media processing и visibility. Пока comment gate не закрыт, catalog
показывает not yet supported, а не connected capability.

## QuickBooks — M18, M29

Сначала уточнить **QuickBooks Online или Desktop**: Online API не доказывает Desktop
совместимость. Official Accounting collection описывает query/read и mutations
invoices/payments; `com.intuit.quickbooks.accounting` — широкий accounting scope,
а не read-only invoice permission. M18 раскрывает это и технически предоставляет
только зарегистрированные read/prepare tools. Выбрать company `realmId`, environment,
user role, country и subscription; sandbox не равен production access.
[Intuit Accounting API](https://www.postman.com/intuit-developer/intuit-developer-quickbooks-online-accounting-api/overview).

Invoice `Balance` — остаток после учтённых payments, `DueDate` — основание определения
просрочки. Показывать source date/currency/calculation; это не доказательство bank
settlement. Динамические Intuit entity/scopes pages не дали readable content; проверены
official collection/SDK references, а current field/rate-limit behavior нужно обновить
перед реализацией. [Invoice reference](https://static.developer.intuit.com/sdkdocs/qbv3doc/ippdotnetdevkitv3/html/f69ef5c3-ece2-c146-24a2-855dae75b3a2.htm).

OAuth 2.0 setup использует client secret. Решить безопасный desktop/distribution flow
без embedded/shared secret и sample token files. Payments — отдельный API/scope;
`com.intuit.quickbooks.payment` не запрашивается. M18 не создаёт financial records,
не отправляет invoices и не перемещает деньги. M29 отдельно классифицирует record
creation/update; money movement остаётся исключённым.
[Official OAuth guidance](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/docs/_sources/authorization.rst.txt),
[Payments scope](https://developers.intuit.com/app/developer/qbpayments/docs/develop/authentication-and-authorization/oauth-2.0).

**Приёмка:** известные unpaid/part-paid/paid sandbox invoices, bounded query, правильный
balance/due-date/currency summary, неполное покрытие при missing pages/fields. Read
paths не могут вызвать write/email/payment. M29 требует field-level diff, свежую
revision/SyncToken где применимо, exact CONFIRM и read-back; сомнительный outcome
не повторяется, а destructive consequences требуют отдельного risk решения.

## AWS — M19, M30

Сначала определить конкретные services/accounts/regions. `DescribeInstances` — пример
EC2 inventory, не поддержка всего AWS; STS `GetCallerIdentity` подтверждает principal/account,
но не права на ресурсы. Resource state, account Health events и public status — разные
источники. Federation/role и short-lived credentials предпочтительны root/static key;
allowlist exact read actions, никакого blanket admin policy или plaintext credential file.
[EC2](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstances.html),
[STS](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html),
[IAM practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html).

`ce:GetCostAndUsage` требует разрешённого billing access и settings организации.
**Cost Explorer API reads платные**; до подключения cost module раскрыть текущую цену,
поставить request/spend budget и получить enablement. Discovery не включает платные
hourly данные и не меняет поддержку. На дату проверки AWS Health API требует eligible
support plan (Business Support+, Enterprise Support или Unified Operations с указанными
в документации переходными исключениями); отсутствие entitlement — missing coverage.
[CE permissions](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ce.html),
[access](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-access.html),
[pricing](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/),
[Health eligibility](https://docs.aws.amazon.com/health/latest/APIReference/Welcome.html).

**Приёмка M19:** выбранные account/role/region и known resource state; bounded billing
period с freshness/currency/estimated status; запрет other account/region/write;
missing Health/billing coverage явно отображено. Проверить throttling/pagination,
session expiry и denied billing. M30 не активируется одним roadmap заголовком:
infra/IAM/deletion/scaling/commitments требуют отдельных решений, точных diff,
ограничений области воздействия и стоимости; CRITICAL остаётся выключенным до решения.

## Microsoft admin office — M19, M30

Название **предварительно** означает Microsoft 365 Admin Center; точный продукт,
tenant/cloud и workflows нужно уточнить. Graph adapter покрывает только документированные
operations, не все admin-center screens. Work/school account, отдельная роль/consent
и tenant binding обязательны; существующий Outlook consent не даёт admin authority.
`/users` для basic read документирует `User.ReadBasic.All`; personal и guest caller
не поддерживаются. Более полные поля требуют обоснованного расширения.
[Users](https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0).

Group reads имеют `GroupMember.Read.All`/`Group.Read.All` варианты; выбрать минимальный
для нужных fields и перепроверить endpoint. `LicenseAssignment.Read.All` документирован
для subscription/license reads, но delegated role requirements зависят от операции.
User `licenseDetails` на дату проверки не поддерживает app-only permission: daemon
не может предполагать все read capabilities. Write-capable permission из таблицы
Graph не оправдывает его запрос read module.
[Groups](https://learn.microsoft.com/en-us/graph/api/group-list?view=graph-rest-1.0),
[subscriptions](https://learn.microsoft.com/en-us/graph/api/subscribedsku-list?view=graph-rest-1.0),
[license details](https://learn.microsoft.com/en-us/graph/api/user-list-licensedetails?view=graph-rest-1.0).

**Приёмка M19:** ограниченная известная выборка users/groups/SKUs выбранного test
tenant, правильные tenant/fields, missing role/admin consent, personal-account rejection,
eventual consistency, expiry и disconnect. Никаких create user/password reset/role grant/
license assignment/security setting изменений. M30 требует отдельных capability/risk
решений; credential/security операции могут оставаться CRITICAL/BLOCKED.

## Omadli Group — M20, M24

Подтверждено только сообщением пользователя: это собственная система компании.
Не предоставлены URL, назначение, workflows, API, модель данных, authentication,
roles или test environment. Это открытые входные данные, а не повод придумать SaaS.

M20 получает у владельца адрес/назначение, конкретные read/write сценарии, API docs/version,
authorized test role и denied case, boundaries организации, MFA/session policy,
network/deployment, data classification/residency/retention, safe records и evidence
для read-back/audit. Если подходит документированный API, проектируется dedicated
adapter и один ограниченный API pilot. Риски классифицируются по реальным operations;
название системы не делает её записи SAFE.

Если API недостаточен, M24 отдельно оценивает interface pilot после M22–M24 boundaries:
user sign-in/MFA, observed state/controls, exact account/organization/record/fields,
preconditions и post-action UI read-back. Не извлекать cookies, не выдавать модели
generic selectors, не проигрывать координаты вслепую. Private-network access требует
отдельной контролируемой политики; текущий browser policy не получает скрытого исключения.

**Приёмка:** владелец выбирает один documented workflow и разрешённую роль; denied
role/case не раскрывает данные. Минимальное read и отдельно approved write, если оно
в scope, имеют source/record identity, проверку preconditions, read-back и audit.
Changed interface, expired login, unavailable source, cancellation и uncertain result
останавливают выполнение без дублей. Нет подходящего API/UI route — unsupported
coverage/manual handoff. «Новые клиенты» и briefing coverage не обещаются до discovery.

## 1Password — M21

Official SDK документирует desktop-app authorization (включая Windows setup) и service
accounts. Это не real Windows/Hello acceptance. Service account может иметь selected
vault permissions; встроенные Personal/Private/Employee vault недоступны, а изменение
выбранных permissions требует нового service account. Выбрать narrow dedicated vault
с read и без write/share/create-vault; vault grant всё равно шире одного item/field.
[SDK](https://www.1password.dev/sdks), [concepts](https://www.1password.dev/sdks/concepts),
[service accounts](https://www.1password.dev/service-accounts/get-started).

Предлагается **опциональный** trusted secret provider: native UI связывает opaque
credential handle с конкретным authorized account/vault/item/field; bounded helper
получает секрет только для фиксированного destination бизнес-адаптера. Модель не
получает vault list/search/read tools, секрет, raw item path или recovery information.
Credential acquisition — внутренний шаг авторизованной операции, не универсальный
model-visible SAFE tool и не approval внешнего действия.

Существующий OS credential store сохраняется для local/bootstrap/cache credentials.
Не переносить весь vault, не заменять backend, не использовать `.env`/token files,
clipboard, prompts, memory, logs, errors или tool results для секретов. SDK/desktop
prompt, locked state, helper secrecy и organizational policy требуют отдельной проверки.
1Password Connect добавляет серверную инфраструктуру и не нужен без конкретного
обоснованного deployment. [Connect](https://www.1password.dev/connect).

**Приёмка:** native setup выбирает non-production canary; один fixed adapter успешно
аутентифицируется, а canary отсутствует во всём model-visible/audit/memory output.
Wrong item/field/account/destination отклоняется; locked desktop, denied prompt,
revoked credential, недоступный OS store, cancellation/disconnect завершаются безопасно.
Нет vault export, recovery access, sharing, rotation или secret writes в этом этапе.

## Решения и повторные проверки перед реализацией

1. Приоритеты и конкретные eligible accounts/workspaces/tenants; владельцы app registrations,
   роли, admin consent, тарифы и production review.
2. Безопасный confidential OAuth exchange для Asana, Notion и QuickBooks в локальном
   desktop-продукте; не добавлять сервер без конкретного обоснования.
3. Teams chat-send scope discrepancy; точные Facebook comment endpoint/scope mappings;
   Meta App Review и production eligibility после повторного чтения primary guides.
4. Asana status schemas, Notion allowed page roots/data-source schema, Outlook
   personal/work account search и conflict coverage.
5. QuickBooks Online/Desktop/company/country; AWS services/accounts/regions, Health
   entitlement и cost budget; точный Microsoft admin office продукт/workflows.
6. Loom Atlassian-linked workspace, transcript/search coverage и Rovo credits;
   Fireflies plan/recording visibility; раздельные права на чтение и redistribution.
7. Omadli Group discovery inputs; 1Password auth mode/vault/organization policy;
   Claude model/API budget и корпоративные требования к обработке данных.

Эти вопросы не блокируют документацию или текущую M9-приёмку. Они становятся воротами
только соответствующего будущего адаптера; неподдерживаемые операции остаются выключены.
