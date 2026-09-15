# Архитектура

## Текущее состояние

Один Python-пакет, запускаемый через `python -m jarvis` или `jarvis`. Этапы 0–2
реализовали desktop shell, локальные typed tools, PermissionEngine, approvals и audit.
Текстовая команда остаётся fake demo этапа 1. Кнопка «Проверить разрешения» открывает
отдельную ручную проверку локальных инструментов. LLM/Windows/browser/voice adapters отсутствуют.

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
Перед Windows adapters необходимо определить отдельные bounded native-call boundaries.
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
до реальной Windows-проверки. Один локальный процесс, без микросервисов. Настройки читаются
из process environment, `.env` автоматически не загружается. Windows-цель — Windows 11 x64.
Точная установка и границы проверок описаны в `TESTING.md` и `COMPATIBILITY.md`.
