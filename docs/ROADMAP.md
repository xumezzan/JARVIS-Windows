# Roadmap

Обновлено 2026-09-15: расширение требований, без реализации новых функций.
План v1.0 сохранён в доступной форме этапов 0–9; отдельный оригинал не найден.
**Ближайшая работа остаётся этапом 9 на Windows.** Завершение текущей документационной
задачи не закрывает его. Никаких сроков или процентов готовности не назначено.

## Фактическое состояние MVP

| Этап | Результат и граница | Статус / свидетельство |
| --- | --- | --- |
| 0 Foundation | Package, документы, установка, quality gates | Реализован локально; [отчёт](MILESTONE_0.md), native pending |
| 1 Desktop shell | PySide6, fake worker, состояния, activity, smoke | Реализован локально; [отчёт](MILESTONE_1.md); основная строка остаётся demo |
| 2 Tools / permissions | Strict registry, deterministic risk, immutable single-use approvals, simulation, audit | Реализован локально; [отчёт](MILESTONE_2.md), native pending |
| 3 Windows | Allowlisted open/focus/list, CONFIRM empty-Notepad type/read-back | Portable tests; [отчёт](MILESTONE_3.md); native UIA pending |
| 4 Browser | Собственный статический анонимный Chromium, exact policy/approvals | Локально реализован; [отчёт](MILESTONE_4.md); Windows/public search pending |
| 5 Planner | Offline/OpenAI, один tool за шаг, лимиты/уточнения/отмена | Локально реализован; [отчёт](MILESTONE_5.md); live model и Windows pending |
| 6 Voice | Held push-to-talk, local STT/TTS, transcript review, voice cancel | Локально реализован; [отчёт](MILESTONE_6.md); hardware/native pending |
| 7 Memory | Явные profile/session labels, TTL, выбор на задачу | Локально реализован; [отчёт](MILESTONE_7.md); не company KB, Windows/live cloud pending |
| 8 Outlook | UI-only OAuth setup, Graph read, RAM/remote draft, exact send | Локально реализован; [отчёт](MILESTONE_8.md); live OAuth/почта/native pending |
| 9 E2E / packaging | Установка из репозитория одной командой, native MVP, reproducible release | Переносимая подготовка реализована; [отчёт](MILESTONE_9.md); чистая Windows и полный E2E pending |

[ElevenLabs Free TTS](ELEVENLABS.md) уже добавлен отдельным запросом: одноразовый opt-in,
OS key store, Free/overage/quota guard. Live API/звук/Windows pending. Исторические Mac
проверки перечислены в отчёте; они не запускались заново ради планирования.

### Незавершённый этап 9 — приоритет и граница

Дополнение 2026-10-14: добавлена [автоматическая диагностика установки](INSTALLATION_DIAGNOSTICS.md)
с JSON/Markdown-отчётами на успехе, сбое и отмене, независимыми компонентными проверками
и локальными functional probes. Это подготовка этапа 9 на Mac; native-приёмка остаётся
открытой. Фактические команды и результаты дополнены в [MILESTONE_9.md](MILESTONE_9.md).

- Scope: [WINDOWS_INSTALLATION_PLAN.md](WINDOWS_INSTALLATION_PLAN.md),
  [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md), реальный запуск из «Пуск», чистая/
  повторная/offline установка, hardware, live Outlook, opt-in model, public search,
  негативные сценарии, Windows constraints и подтверждённая целостность артефактов.
- Dependencies: полная текущая копия репозитория, Windows 11 x64 с desktop, реальные
  устройства и пользовательские test account/recipient/consents там, где нужны.
- Exclusions: новые интеграции, onboarding wizard, locale migration, visual/background.
- Exit: все обязательные native gates, полный исходный MVP и packaging пройдены с
  версионным отчётом; после native compatibility создан lock и повторена clean install.
  Skipped и startup receipt не являются полным E2E. На Mac этап остаётся открытым.

Целевая установка из Codex без ручной Python/pip подготовки сохраняется. Новый мастер
этапа 12 работает **после установки**, не подменяя bootstrap и OS consent этапа 9.

## Правила расширения

Все этапы 10–32 ниже **предложены, не реализованы**. Это рекомендуемый порядок, который
пользователь может пересмотреть по [открытым решениям](OPEN_DECISIONS.md). В работу берётся
один явно запрошенный bounded milestone. Каждый начинается после MVP; Dependencies
указывают технические предпосылки сверх этапа 9, а не требуют всех неиспользуемых сервисов.

Каждый этап проверяет strict schemas, PermissionEngine, no adapter hooks in simulation,
UI-only exact approvals, cancellation, time/resource budgets, safe audit, verified outcomes,
account separation и отсутствие повторного внешнего эффекта после неопределённого ответа.
Готовность адаптера включает реальные разрешённые test-account проверки нужных операций;
успешный fixture, logo, app detection или API docs недостаточны. Недоступный API получает
статус investigation/not supported, а не фиктивно завершённую продуктовую capability.

English-default вводится на этапе 10 с полной RU локализацией. Он не добавляется задним
числом к историческим MVP результатам. Все последующие новые экраны обязаны поддерживать
оба языка. Денежные/API budgets этапа 11 предшествуют новым paid-provider/routine scopes.

## Базовые расширения и первые интеграции

| Этап | Scope | Exclusions | Dependencies | Проверяемый exit |
| --- | --- | --- | --- | --- |
| 10 Language / EN–RU | English first launch/UI/conversation/voice, видимый RU switch, Settings → Language, независимый язык разговора, locale/time zone, каталоги переводов и проверенные voice assets | Новые сервисы, перевод исходных данных/approval payload, скрытые downloads | 9; выбор EN/RU локальных speech assets и native проверка | E03 и доступные UI/settings/pending-payload части E02/E10: EN/RU текст и реальный голос, cross-language UI, switching без потери данных/reconnect; wizard части проверяются после 12 |
| 11 Integration contract | Versioned manifest, trusted registration, operations/schemas/auth/scopes/risk/verifiers, connection lifecycle, общий budget и deduplication протокол; UI-only setup-команды через engine с отдельной authority | Model-install plugins, произвольные endpoints, замена OS credentials, runtime hot loading | 10; [контракт](INTEGRATION_CONTRACT.md) | E08/E09: новый тестовый adapter без изменений core; invalid metadata/scopes rejected; effect once; simulation не вызывает даже setup hooks; перечень платных reads и limits |
| 12 First-run setup wizard | Все 7 экранов, opt-in discovery, полный каталог включая planned, настройка только принятых модулей, resume, Modules & Connections | Реализация будущих adapters через кнопку; автоплатежи, unattended sign-in/MFA/install | 10, 11; существующие принятые 3–8 | E01/E02/E11: clean Windows → English welcome → discovery → выбранный Outlook → safe verified first task; RU switch, denial, offline, multiple accounts, cancel/restart |
| 13 Mail / Asana / Outlook Calendar | Bounded Outlook search/reply/authorized incoming attachment read; Asana project/task/assignee/due/status/comment read + confirmed mutations; календарь read/free-busy где доступен, create/reschedule с точным before/after | Не весь cross-service orchestrator; tenant-wide access, auto invite/send, произвольные attachment downloads/execution | 11, 12; app registration/auth решение, test workspace/Microsoft account | Проверить bounded mail search/reply provenance/attachment limits; по одному task/comment и event создать/прочитать обратно; изменение project/user/time zone требует нового approval; конфликт/нет free-busy видны; duplicate timeout не повторяет write |
| 14 Claude provider / analysis tool | Replaceable provider и bounded analysis/content tool там, где полезен; API disclosure, secret helper, общий token/cost budget | Самостоятельные hosted effects/approval, предположение что subscription даёт API | 11, 12; выбранный доступный model/API account | Один и тот же сценарий через offline и Claude даёт engine-derived report; неверный tool output отклоняется; budget/consent/stop; credentials не в prompt |
| 15 Teams + Notion | Выбранные Teams chats/channels/messages/meeting metadata, prepare/send; Notion page/search/data-source entries read/create/update | Неограниченная история организации, произвольная запись всех meeting endpoints; exhaustive Notion search | 11, 12; scope/account/admin и confidential OAuth решения | В выбранной области read + один exact confirmed message/page update с read-back; недоступные meeting scopes/источники указаны; wrong workspace и revoked access fail closed |
| 16 Meeting/video sources | Fireflies recordings/transcripts/summaries/action items и Loom доступные metadata/links/transcripts через проверенный официальный маршрут; source references | Запись встреч, создание/upload/публикация видео, перераспространение recording | 11, 12; API/MCP access и retention решение | Разрешённый источник → action-item proposal со ссылкой/временем; inaccessible transcript/expired URL отмечены; source access не даёт distribution authority |
| 17 Social comments | Instagram/Facebook comments read, отдельные CONFIRM comment/reply operations для поддержанного account type | DMs и post publishing (31), personal-account promises, moderation/delete без отдельной policy | 11, 12; Meta app review/account/permission gates | Read/reply выбранного comment ID в test asset; exact account/page/media/thread/text approval, read-back; revoked permissions и 429 без дубля |
| 18 QuickBooks read | Invoices, payment status, bounded unpaid summary с источниками и временем | Финансовые записи/изменения (29), money movement | 11, 12; безопасная OAuth схема, sandbox company | Read-only allowlist даже при широком OAuth scope; unpaid report сверяется с источником; currencies/partial payment и stale data видны |
| 19 AWS / Microsoft 365 admin read | Отдельные read adapters: выбранные AWS resources/status/costs и M365 users/groups/licenses | Infrastructure/security/account/license changes (30), новые платные services/support | 11, 12; точный admin product, account/region/service, IAM/admin согласия | По выбранному AWS service/account/region и M365 tenant сверить read; denied scope/support/API charges показаны; вне выбранной области access denied |
| 20 Omadli discovery / API pilot | Уточнить purpose/URL/roles/auth/workflows; оценить API; при наличии — один согласованный read workflow и отдельно классифицированное изменение | Выдуманная SaaS/API schema; UI automation до 22–24; доступ сверх роли | 11, 12; D06 и разрешённый test environment | Документированный контракт и role tests; API pilot имеет source/target/read-back. Если API нет, завершён только discovery, операция остаётся pending до 24 |
| 21 1Password trusted secret adapter | Один конкретный secret для одного авторизованного operation, minimal vault/item scope, совместимость с OS store | Vault browsing моделью, prompts/logs/tool results с secrets, автозамена OS store | 11, 12; service-account/desktop method и права | Fake-secret leakage tests + opt-in trusted test: получает только adapter; denied secret/expired auth fail closed; удаление connection, no plaintext fallback |

## Управление, память и процессы

| Этап | Scope | Exclusions | Dependencies | Проверяемый exit |
| --- | --- | --- | --- | --- |
| 22 Authenticated dynamic browser | Отдельный adapter для перечисленных workflows/sites; service-specific action policy, controlled session, DOM/state verification, user-owned auth | Изменение текущего anonymous adapter, импорт cookies/profile, generic POST/JS/shell/model selectors, обход site restrictions | 11, 12; official API insufficiency и security design review для каждого сайта | На allowlisted test site user sign-in → bounded operation → verified outcome; redirect/popup/download/injection/stale target blocked; isolated session уничтожается по правилам |
| 23 Additional Windows input / transfer | Перечисленные UIA controls, tabs/windows переключение; controlled copy/transfer с точным source/destination/content | Глобальные keys/clipboard fallback для текущего Notepad, произвольные apps, silent source reading | 11, 12; native app/version support matrix | На реальном Windows подтвердить source/target identity, exact transfer и read-back; wrong tab/nonempty target остановка; отмена не затирает данные |
| 24 Screen / visual / multi-monitor | Scoped screenshots, OCR/visual model по отдельному disclosure, DPI/monitor mapping, observable visual targets; один Omadli UI pilot если API недостаточен | Always-on capture, secret/login capture, blind coordinate replay, universal app support | 22 и/или 23 для маршрута; 20 для Omadli; 11 budgets/consent | E06 + native multi-monitor 100/150/200% DPI; target move/overlay/low confidence → stop; API/UI Omadli outcome read-back в разрешённой роли |
| 25 Personal memory / company knowledge | Личная долговременная память отдельно от source-backed company KB; opt-in ingestion, ACL, provenance/timestamps, correction/delete/disable, conflict/staleness | Автоингест всей компании, хранение secrets/approvals, inference recipient authority | 11, 12; D13/D14 storage/processing; принятые источники по выбору | E12: separation users/orgs/accounts, source revocation, expired/conflicting knowledge требуют уточнения; delete/disable исключают retrieval; no source = no claim |
| 26 Multi-step workflows / roles | Bounded cross-service plan, source lineage, partial reports, shared budgets/locks/duplicate prevention; roles email/calendar/tasks/social/finance/knowledge/browser/Windows | Новые authorities/microservices, agents approve agents, unconditional retries, общий approval на неизвестный план | 11, 13, 25; прочие adapters только для включённых roles | E04/E09: email → exact Asana task → calendar proposal/change → factual report; competing roles не дублируют действие; partial failure сохраняет истинные outcomes |
| 27 Background routines / briefing | Explicit schedule/event enablement, chosen sources, time zone/quiet hours/notifications, bounded reads/proposals, disable и resume semantics | Default external writes; обещание работы на выключенном PC; скрытая запись | 26; 13/17/18/20 или 24 по выбранным источникам; D15 | E05: morning report по email/calendar/Asana/QB/social/Omadli с coverage/staleness; sleep/offline/skipped явно видны; disable прекращает будущие runs; pending write ждёт отдельный UI approval |
| 28 Learning from demonstrations | Visible scoped recording, editable parameterized conditions/actions/verifiers, review, safe trial, versions/delete, authorized reuse | Capture secrets, blind coordinates, replay approvals, обучение любой задаче за один раз | 22–24 для выбранного маршрута, 26; retention consent | E07: запись → редактирование → simulation и разрешённый trial → повтор; changed UI/schema stops; secrets excluded; удаление очищает workflow/observations по retention |

Обычный tool/workflow предпочтителен отдельной роли, если нет независимой сложной
аналитики. Роли не требуют отдельных процессов/сервисов; native helpers остаются
технической изоляцией существующих платформенных вызовов.

## Отдельные условные расширения с повышенным риском

Эти этапы не разрешают CRITICAL автоматически. До явного решения о policy, минимальных
полномочиях, способе подтверждения и test environment соответствующие actions выключены.
Если необходимы server-side компоненты для OAuth или расписания, это отдельное архитектурное
решение; текущая локальная архитектура не меняется документацией.

| Этап | Scope | Exclusions | Dependencies | Проверяемый exit |
| --- | --- | --- | --- | --- |
| 29 Financial records | По одной выбранной QuickBooks create/update operation; точный before/after, бухгалтерские роли, concurrency/version checks | Money movement, bank/payment execution, налоговая/бухгалтерская корректность без проверки | 18, 11, D12/D21; отдельная risk decision | Sandbox read-back + opt-in test approved operation; stale record/unknown outcome blocks repeat; классификация доказана для каждой операции |
| 30 Infrastructure / administration changes | Раздельные capability gates для AWS resource/config и M365 account/group/license/security changes; оценка cost/privilege/destruction | Blanket admin consent, скрытое повышение привилегий, обход BLOCKED, автоматическое enable CRITICAL | 19, 11; D05/D07/D21; отдельное решение на каждое семейство | Sandbox/test tenant, exact before/after/resource/account/region, independent verify, concurrent update/permission denial/partial effect; неутверждённые семейства disabled |
| 31 Social DMs / publishing | Instagram и Facebook DMs отдельно от post publishing, платформенные window/account/media/review ограничения | Возможность писать любому аккаунту; согласие из одного connection; bulk spam | 17, 11; API/account review конкретной capability | Для каждого поддержанного маршрута exact destination/content/media approval, provider receipt/read-back и ограничений; unavailable API = unsupported |
| 32 Video creation / upload / distribution | Отдельно оценить Loom creation/recording SDK, upload и publish/share; Fireflies distribution только при проверенном official route и правах | Автоматическое перераспространение meeting data; право записи из права чтения | 16, 11; для capture — 24 и принятая SDK/browser boundary; consent/privacy/licensing/API eligibility | Явный recording consent, выбранные bytes/audience/visibility в snapshot, upload и share проверены отдельно; отказ/неопределённость не порождают повтор |

Прежние направления после MVP (профессиональные/коммерческие профили, подписанные обновления,
административные политики и поддержка) сохраняются как release-backlog без обещанных дат.
Стабильная начальная упаковка относится к 9; промышленная доставка обновлений потребует
собственного будущего scope. Gmail не выбран: email service MVP — Outlook; новый email
provider возможен только отдельным решением через общий контракт.

## Проверки и связанные документы

Engineering milestones: focused tests → исправления → full pytest → Ruff lint/format →
mypy → применимый native GUI/smoke/build → документация фактических результатов. Mock
не доказывает Windows/API/hardware. Windows dependency pins — только после native проверки.
Для этой documentation-only задачи проверяются тексты, ссылки, матрицы, git diff и
сохранение code/dependency files; code tests не объявляются выполненными.

- [Карта возможностей](CAPABILITY_MAP.md), [интеграции и источники](INTEGRATIONS.md).
- [Контракт адаптера](INTEGRATION_CONTRACT.md), [архитектура](ARCHITECTURE.md), [безопасность](SECURITY.md).
- [Onboarding](ONBOARDING.md), [будущая E2E приёмка](PRODUCT_ACCEPTANCE.md), [решения](OPEN_DECISIONS.md).

## Точный следующий prompt

```text
Continue Milestone 9 on the target Windows 11 x64 laptop: install JARVIS and complete native acceptance.

Read AGENTS.md, docs/PRD.md, docs/ROADMAP.md, docs/MILESTONE_9.md,
docs/WINDOWS_INSTALLATION_PLAN.md, docs/WINDOWS_ACCEPTANCE.md, docs/ELEVENLABS.md and
scripts/windows/Install-Jarvis.ps1. Inspect the actual code and existing changes;
preserve all existing work. This task is only the unfinished MVP Milestone 9.
Do not implement Milestones 10–32 or the new onboarding/localization requirements yet.

Run scripts/windows/Install-Jarvis.cmd from this complete repository; do not require
manual Python/pip setup. Inspect installation-report.json and the real window, then
launch from Start. Repair native failures. Verify clean/repeated installation, offline
repeat, spaces/Cyrillic paths, interrupted download, cancellation, permissions and
missing hardware. Preserve user data and the active installation on failure.

Run scripts/windows/Test-Jarvis.ps1 and the current MVP acceptance matrix in
docs/WINDOWS_ACCEPTANCE.md. Native apps are opt-in. Voice requires the user's actual
hold gesture. For Outlook, let the user choose their test account and recipient and
approve the exact snapshot in the normal UI. Never bypass OAuth/MFA, microphone consent,
Group Policy, SmartScreen, UAC or approvals. A 202 response is not delivery proof.
Public search requires actual search results; the owned Chromium is separate from Chrome.
Preserve optional ElevenLabs Free guards and report its live checks separately.

Record Windows build, runtime/package versions and each passed/failed/pending gate.
Only after real Windows compatibility checks, create a reproducible dependency lock,
resolve the documented artifact-integrity gates and repeat clean setup with it.
Do not call the MVP complete until mandatory native E2E and packaging/clean installation
pass. If still on Mac, keep native gates pending and report the need for the target
Windows laptop; do not substitute post-MVP implementation for native acceptance.
```

## Отдельный запрос: фоновые подсказки (25 октября 2026)

Реализован opt-in `EventWatcher` только для `DesktopService`: локальные подсказки
по clipboard и новые важные входящие Outlook каждые 5 минут, corner HUD и
необязательный системный голос. Создание шаблона ответа в RAM — по кнопке с
точным адресом; отправка сохраняет CONFIRM. Это узкое расширение по прямому
запросу пользователя, не завершение будущих milestones и не Windows acceptance.
Границы, команды и фактические результаты: [EVENT_WATCHERS.md](EVENT_WATCHERS.md).
