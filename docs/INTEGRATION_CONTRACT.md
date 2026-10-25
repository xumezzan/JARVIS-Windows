# Общий контракт интеграций — проект этапа 11

Статус: **предложено, не реализовано**, 2026-09-15. Основа уже существует:
[ToolSpec](../src/jarvis/tools/base.py), [ToolRegistry](../src/jarvis/tools/registry.py)
и PermissionEngine. Текущий ToolSpec имеет строгие parameters/result, risk, policy,
preconditions/execute/verify, timeout, cancellation и idempotency description. Полного
manifest/catalog/auth lifecycle, общего денежного budget и cross-task deduplication пока нет.

## Объявление модуля

| Поле | Обязательное содержание и валидация |
| --- | --- |
| Identity | Стабильный module ID/version, совместимые contract/schema versions, trusted publisher/source и dependency inventory; названия EN/RU, категория каталога |
| Availability | Реализованные операции отдельно от planned/unsupported; platform/account/plan/region constraints, дата/ссылка official verification и фактические test gates |
| Operations | Отдельные namespaced read, local prepare и external mutation; strict input/output, запрет extra/coercion, размеры и enum bounds; никаких arbitrary endpoints/code/selectors/paths |
| Authentication | Official method, точные scopes/roles, account/tenant/workspace identity, consent UI, secure helper backend, renewal/revoke/disconnect и проверяемая очистка |
| Risk / approval | Детерминированная operation-specific classification, точный snapshot/preview, UI authority requirement; unknown risk запрещён; connection не разрешает effects |
| Target / data | Связь service/account/org/record/version/source; разрешённые типы вложений, bytes/size/hash/ID; permitted cloud fields и retention; secret references не secret values |
| Limits | Per operation/adapter/task timeout, page/item/byte bounds, rate-limit units/headers, concurrency, provider calls/tokens/cost reservation; default finite values утверждаются при реализации |
| Verification | Read-back/receipt/observed state, consistency delay, version checks; отличие accepted от confirmed business outcome; bounded read reconciliation |
| Uncertainty | Возможные эффекты, idempotency support/TTL/provider semantics или явное отсутствие; durable operation ID, concurrent lock, duplicate detection и ручное разрешение неопределённости |
| Wizard | Description/status/actions/help, discovery без чтения контента, connection prerequisites, safe minimal health read, account picker, installed-component provenance/consent |
| Lifecycle | Cancellation/checkpoints, process cleanup, expired authorization, 401/403/429/network handling, disabled module, update/schema migration, rollback только где доказуем |

Версионированный manifest — данные доверенного пакета, не код от модели. Статическая
регистрация выполняется до seal реестра в composition root. Новый принятый adapter
добавляет свой пакет/manifest/tests/translations и подключается стандартным registration
path без изменения planner, PermissionEngine или core contracts. Перезапуск для активации
допустим; hot-loading из недоверенного пути не требуется. Каталог показывает manifest
вне зависимости от connected status, но модели доступны только разрешённые capabilities.

## Connection lifecycle и мастер

Предлагаемые состояния: not supported → setup required / available to connect → connecting
→ connected; detected — отдельный признак установленной программы. Ошибка и expired
authorization сохраняют account context для объяснения, но блокируют вызовы до восстановления.
Новый account/organization выбирается явно; смена инвалидирует pending actions, cached
observations и согласия старого account. Disconnect прекращает задачи/подписки, очищает
локальный credential material и объясняет отдельно результат remote revoke.

Существующий Outlook setup сейчас UI-only QThread/helper с audit **вне** каталога engine.
На этапе 11 его миграция в зарегистрированные UI-only setup operations должна сохранить
пользовательские connect/disconnect, точный account и запрет model initiation. Engine и
UI capability проверяют setup intent; это отдельное право, не обычный SAFE tool модели.
Модели не передаются ни setup authority, ни approval issuer, ни credential APIs.

Установщик этапа 9 остаётся внешним доверенным bootstrap. Wizard может запросить конкретную
установку через UI-only setup boundary только после отдельного policy решения, trusted
manifest/provenance и понятного согласия. Нельзя обходить текущий CRITICAL=disabled общим
«Install» tool или вводить код/URL установки из модели. До принятия такой capability мастер
показывает setup required и разрешённый путь установки. OS/MFA/consent выполняет пользователь.

Секреты остаются в OS credential store. Если официальный OAuth exchange требует client
secret, не встраивать общий секрет в desktop binary; до проверенной архитектуры secret
handling соответствующий connection остаётся setup required. Локальный персональный
credential method допустим только при official support и отдельном узком выборе; broad
PAT не объявляется автоматически эквивалентом least-privilege OAuth.

## Выполнение, budget и неизвестный исход

1. Получить только доступный user/account scope и текущие source/target observations.
2. Проверить strict schema/pure synchronous policy и локальный budget/rate allowance;
   подготовить неизменяемое действие без DNS/network/adapter observations. В simulation
   auth/discovery/preconditions/execute/verifier hooks не вызываются вообще.
3. Для CONFIRM показать точный snapshot, дождаться UI-only expiring token. Изменение
   аккаунта/полей/вложений требует нового action; отмена инвалидирует старый.
4. Только в реальном исполнении engine выполняет side-effect-free preconditions и
   проверяет свежие account/record/target identity; после ожиданий повторно проверяет
   cancellation, expiry и budget. Атомарно потребляет token, берёт общий effect-intent
   lock с проверкой дубликата и записывает durable started; только затем execute adapter.
   Между агентами и routines один gateway; истёкшее/отменённое действие не ждёт effect.
5. Проверить результат и отметить verified / accepted / denied / error / cancelled /
   uncertain с may-have-effects; без утверждения о distributed exactly-once.
6. При timeout после выдачи эффекта не повторять. Допустим bounded read-only reconciliation
   через зарегистрированный tool с разрешённым scope. Новый approval сам по себе не
   доказывает отсутствие первого эффекта. Повторная запись разрешима лишь после установления
   состояния и нового намерения пользователя; неизвестность остаётся явной.

Локальный durable intent ledger хранит безопасные opaque IDs/statuses, не тела писем,
токены, raw URLs или secret-derived fingerprints. Источники и пользовательские ссылки
для workflow report хранятся отдельно с ACL/retention; это не бесконтрольное расширение audit.
Provider idempotency keys используются лишь при документированной поддержке с правильными
семантикой/TTL. Автоматических retries текущих issued effects не добавляется.

Budget задаётся общим для orchestrator/roles/routine: steps, elapsed time, calls/tokens,
максимальный денежный расход и paid API reads. До вызова резервируется консервативная верхняя
оценка, после сверяется фактическое usage где доступно. Неизвестная цена/неограничимый расход
блокирует платную операцию либо требует нового конкретного решения; billing у провайдера
не является атомарной транзакцией с локальным budget. 429 отображается с доступным временем
восстановления; не зацикливать worker и не повторять uncertain write.

## Contract acceptance (E08/E09)

- Добавить один synthetic test adapter через стандартный manifest/registration без core
  edits; metadata и EN/RU/status states проверяются harness этапа 11. После этапа 12
  этот же контракт отображается в Modules & Connections (отдельный UI gate).
- Неверный manifest/schema/risk/duplicate name, model-requested installation, неизвестный
  scope и попытка выдать approval отклоняются до effect.
- Fake auth/backend counters равны нулю при simulation, включая discovery и verification.
- Safe real test connection выполняет минимальное чтение; нет send/change для health check.
- Account switch/expired token/denial обнуляют применимые approvals и не смешивают данные.
- Два процесса намерения/роли, повтор event, restart после write timeout не создают второй
  эффект; report сохраняет uncertain, если read-back не разрешает неопределённость.
- Budget exhaustion, cancellation и unavailable audit останавливают новые calls; секреты
  отсутствуют в prompts, memory, logs и tool outputs, включая error paths.

Тестовый adapter доказывает контракт; реальная готовность каждого сервиса требует
его собственной приёмки из [INTEGRATIONS.md](INTEGRATIONS.md).
