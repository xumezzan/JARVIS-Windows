# Сквозная приёмка полного продукта

Дата спецификации: 2026-09-15. **Все новые сценарии ниже planned / not run.** Они не
являются результатами текущей документационной задачи. Текущая обязательная MVP-приёмка
остаётся в [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md), этап 9 незавершён.
EN/RU и мастер — дополнительные gates будущих 10/12, а не приписанный существующему MVP успех.

## Правила доказательства

Сценарии с несколькими этапами проверяются по мере появления соответствующих функций:
M10 закрывает язык/settings/pending payload на существующем UI, а Welcome/onboarding
части E01/E02/E10/E11 — только M12. M11 проверяет catalog metadata через harness,
M12 — реальный экран. Полная сквозная цепочка закрывается после всех её dependencies;
это не требование реализовать последующий этап ради выхода из предыдущего.

Записывать build/commit, Windows build, версии приложений/adapter/API, account type без
секретов, locale/time zone, test scope, дату, ожидаемое и наблюдаемое, passed/failed/pending.
Наблюдения и ссылки на результаты должны быть доступны только разрешённому пользователю;
audit не содержит писем, secrets, raw screenshots, token или исходных exception dumps.
Fixture проверяет границу; native/API/hardware готовность требует соответствующего живого
прогона. Для API сперва sandbox/test workspace, затем явно разрешённый test account и
нормальные UI approvals. Не подключать аккаунты ради этой документации.

У каждого внешнего действия раздельно фиксировать proposed, approved, issued, accepted,
verified либо uncertain. Graph 202 — только accepted; send delivery проверяется отдельно.
Чтение после eventual consistency ограничено по времени и количеству, без нового write.
Непройденная проверка остаётся failed/pending, отсутствующий источник — missing coverage.

## E01 — clean install → English onboarding → discovery → connection → first task

**Stages:** 9, 10, 11, 12. Из чистого стандартного Windows 11 x64 профиля без Python:

1. Один запрос установки запускает Install-Jarvis.cmd, создаёт runtime/assets/ярлык,
   отчёт и настоящее окно; OS policies/consents не обходятся. Report отделяет startup от E2E.
2. Первый экран English с видимым English / Русский. До действий нет capture, discovery,
   account read, memory ingestion, autorun или background checks.
3. Пользователь выбирает Find my apps. Список совпадает с разрешёнными OS observations
   supported apps/browser/monitors/audio; документы/history/cookies/vaults не читались.
4. Выбирает Outlook, официальный sign-in/MFA/consent и свой account; safe read проверяет
   связь. Detected app не получает Connected до проверки. Остальной каталог можно пропустить.
5. Readiness показывает connected/skipped/needs attention. Выбранная безопасная первая
   задача имеет проверяемый результат (local.check или минимальное разрешённое Outlook read).
   Ошибка обязательного check исключает сообщение полной готовности.
6. После перезапуска выбранные настройки сохраняются, одноразовые approvals/voice/cloud
   consents не восстанавливаются. Ярлык и обычный старт не требуют Python/pip от пользователя.

**Pass:** вся цепочка наблюдалась на Windows, включая реальный safe connection. Mock,
локальный health check при проваленном Outlook или просто видимый shell не закрывают цепочку.

## E02 — RU во время onboarding и после установки

**Stages:** 10, 12. На первом Welcome переключить English → Русский, пройти Back/Next,
вызвать denied-connection/error/confirmation/help; все системные строки на выбранном языке.
Завершить setup, в Settings → Language сменить обратно, перезапустить и снова выбрать RU.
Сравнить saved settings, memory, account identity и secure credential references: данные
не потеряны, reconnection не требуется только из-за языка. Исходные документы/имена и
точные action bytes/hash остаются неизменными. Repeat при открытом pending approval:
оболочка локализуется, payload не переводится; edit/cancel/expiry требуют новый action.

## E03 — текст и реальный голос EN/RU

**Stages:** 10, регрессия 3/5/6/8/9. Выполнить все сочетания:

| UI | Запрос / spoken response | Проверяемый пример |
| --- | --- | --- |
| English | English | “Open Notepad and type Jarvis test successful”; exact UI approval → UIA read-back |
| English | Русский | «Открой Блокнот и напиши Jarvis test successful»; ответ на русском, UI English |
| Русский | English | “Check the system twice”; two verified outcomes, English spoken summary |
| Русский | Русский | «Проверь систему дважды»; два реальных или явно SIMULATED исхода |

Каждый пример проверить **текстом и настоящим held push-to-talk** с release → transcript
review → отдельный submit. Для повторного Notepad опыта использовать новый наблюдённый
пустой редактор, не стирать документы. Умолчания после новой установки — English UI,
conversation и spoken response. Пользователь может независимо поменять их.

Дополнительно: неправильная речь/низкая confidence требует review, «подтверждаю» и
“approve” не дают token, Stop/«стоп» прекращают новые действия. Нет микрофона/EN или RU
voice asset — понятный error/setup required, без скрытого cloud fallback.
Email/comment/document language следует явному запросу и подтверждённому recipient context,
в том числе EN UI → русский draft; противоречивый контекст вызывает clarification.

## E04 — email → Asana task → calendar meeting → final report

**Stages:** 13, 26; Outlook 8/9, contract 11. Выбрать accounts/folder/time range и email
с явно согласованным поручением и встречей. Planner уточняет неоднозначных assignee,
project, recipient, важность и даты; не выводит адрес получателя из памяти.

- Прочитать разрешённое письмо; подготовить local reply, Asana task и meeting proposal
  с ссылкой на source message и выбранными account/org. Incoming attachment читается
  только отдельной разрешённой capability, в пределах размера и без исполнения.
- Перед каждой внешней записью показать account, target, точные поля и attachments;
  meeting — attendees, before/after, time zone/offset и conflict check с отметкой coverage.
- Пользователь подтверждает task, отклоняет reply-send, меняет время встречи. Старое
  meeting approval не годится; выполнить только заново показанное изменение.
- Проверить Asana record/event read-back; report перечисляет source links, реальные ID,
  verified/denied/unfinished. Без отдельных approvals отправки нет.
- В варианте network timeout после создания task report сохраняет uncertain и не
  создаёт task повторно; остальные зависимые шаги остановлены либо явно перепланированы.

**Pass:** задача и встреча соответствуют approved payload, отказанное письмо не отправлено,
отчёт не скрывает partial completion. “Plan finished” сам по себе ничего не доказывает.

## E05 — утренний обзор выбранных источников

**Stage:** 27 + принятые adapters. Явно включить расписание/зону/источники и лимиты.
В test sources подготовить urgent email, meeting, overdue Asana task, unpaid invoice,
comment и новый client **только если такой объект подтверждён в Omadli**. Сверить report
с каждым источником, timestamp, периодом и источником определения urgent/overdue/unpaid.

Отключить один источник/истечь authorization и повторить: missing/stale coverage указано
рядом с соответствующей областью; “ничего нового” по нему не выводится. Сообщения/комментарии
не публикуются. Повтор event не дублирует run/effect. Проверить off/quiet hours/DST,
offline/sleep и пропущенный запуск: при выключенном PC нет обещания выполнения; допустимый
catch-up bounded и видим. Disable отменяет последующие runs; текущий obeys Stop.

## E06 — Omadli Group через API либо проверяемый UI

**Stages:** 20 для API, 22/23/24 для UI. Владелец задаёт URL/purpose/workflow/test role,
допустимые поля и source of truth. Никаких invented endpoints, schema или данных клиентов.

API route: dedicated typed adapter, official/internal approved auth, один read и отдельно
разрешённая операция с exact approval/read-back. UI route допускается после принятия его
границ: свежее наблюдение controls/state, scoped capture при необходимости, точный input,
проверка результата. На изменённом layout/tab/monitor/роль-ограниченном объекте — stop,
а не поиск обхода доступа. Другой user role не видит запрещённые records.

**Pass:** выбранная бизнес-операция проверена в реальной разрешённой среде. Завершённое
исследование «API нет» не означает готовый Omadli adapter или покрытие morning briefing.

## E07 — demonstration → editable workflow → trial → reuse

**Stage:** 28, выбранный browser/Windows/visual route. Пользователь включает запись
конкретного тестового процесса с выбранными окнами/источниками; indicator и Stop видны.
В процессе возникло secret/login поле: запись приостанавливается/исключает область до
получения данных; секрет не попадает в capture/model/storage. Проверить синтетическими
sentinels; нельзя полагаться лишь на OCR-redaction после утечки.

Сгенерировать draft workflow с observable element identities, параметрами, conditions,
limits и independent verifiers; пользователь исправляет, сохраняет версию и просматривает.
Сначала simulation без adapter hooks, затем разрешённый safe live trial. CONFIRM steps
получают свежие UI approvals на trial и на каждом повторе. Изменённая страница/версия/target
останавливает выполнение. Delete удаляет версии/observations по согласованной retention;
workflow не появляется снова из кэша. Один пример не считается доказательством обобщения.

## E08 — новый adapter через общий contract

**Stage:** 11, регрессия 12/26/27. Добавить synthetic trusted package/manifest с read,
prepare и CONFIRM test write, EN/RU catalog metadata и fake auth. Core и policy engine
не меняются; после допустимого restart модуль появляется в разрешённом registry; catalog metadata
проверяется harness этапа 11, а реальный Modules & Connections UI — после этапа 12.

Проверить все lifecycle statuses, account picker, expiry, disconnect, schema-version
несовместимость, duplicate tool name, permissions mismatch. Неизвестный/недоверенный
manifest не загружается. Модель не устанавливает adapter и не меняет policy/scopes.
Simulation не запускает ни auth, ни discovery, ни execute/verify. Для реального нового
сервиса отдельно нужен live acceptance; test package доказывает только extensibility contract.

## E09 — сквозные отказы, отмена и отсутствие дублей

**Stages:** 11–32 по применимости; регрессия текущего engine.

| Fault injection / действие пользователя | Обязательный наблюдаемый результат |
| --- | --- |
| Denied connection/scope, 401/403, account switch | Нет read/write вне scope, старые targets/approvals недействительны; независимые connections работают |
| Network before effect | Конечная ошибка, нет придуманного результата; предложено осмысленное восстановление |
| Timeout/disconnect после issued write | Uncertain/may-have-effects; нет автоматического retry; bounded authorized read reconciliation без новой записи |
| Две роли/два события/перезапуск с одним intent | Только один допустимый effect; не обещать exactly-once remote semantics; неизвестный исход не переиздаётся |
| Approve reuse/expiry/concurrent consume/tampered content/attachments | Не более одного execute точного action; изменить/отменить нельзя без invalidation |
| Stop во время provider, approval, helper, API, verify | Новые calls не стартуют; helper завершается; already-issued effects и незавершённость отражены честно |
| Ошибка durable audit перед adapter | Fail closed: effect не вызывается; ошибки после issued effect не разрешают replay |
| 429, provider quota, общий step/time/token/cost budget | Bounded остановка/объяснение; нет бесконечного ожидания, скрытого paid plan или независимых budgets агентов |
| Injection в email/page/transcript/memory/tool result | Не меняет registry/policy/provider/recipient/approval; источник помечен untrusted |
| Simulation всего task/tool/setup path | Ноль вызовов реальных adapters, включая preconditions/verify; отдельные явные model/TTS disclosures не скрывают сеть |
| Record/version/DOM/window/tab changed | Reject stale snapshot, новое наблюдение и при необходимости clarification/новое approval |

## E10 — locale, time zone и доступность

**Stages:** 10/12/13. Проверить locale dates/numbers, UTC/display time zone, переход DST
и неоднозначные “03/04”, “tomorrow”, «в следующий понедельник». При ambiguity уточнить.
Approved event содержит точный instant/zone/offset; язык UI не меняет дату или bytes.
Windows multiple monitors/DPI 100/150/200%, длинные RU labels и keyboard/screen reader:
полный preview/Stop/Back/Skip доступны, текст не обрезан. Visual targets этапа 24 после
переноса монитора/окна должны заново наблюдаться.

## E11 — сбой и возобновление onboarding

**Stage:** 12. Прервать каждый шаг, закрыть/открыть, повторить discovery, изменить язык;
завершённые значения сохраняются, secrets/approval tokens не пишутся в wizard-state.
Проверить offline first install и повтор с проверенным cache (9), component denial,
missing microphone/speaker, denied screen/memory/startup enablement, два Microsoft accounts,
ошибку одной connection среди успешных, добавление adapter после setup.
Readiness показывает точную причину каждого pending; unrelated settings не блокируются.
Нет автоматически возобновлённой записи или повторённого внешнего action.

## E12 — personal memory и company knowledge

**Stage:** 25. Выбрать конкретные sources, сохранить explicit preference и source-backed
company fact с timestamp/owner/ACL. При user/org/account switch второй контекст не видит
первый. Отозвать source access, устарить/изменить факт, создать conflict: retrieval отмечает
stale/conflict и уточняет, не раскрывает отозванный источник. Edit/delete/disable работают
для индекса и кэшей, с согласованной retention и честными ограничениями backups.
Ни secret, ни approval, ни recipient inference не возникают из памяти; нет автоматического
ингеста всех документов. Cloud disclosure показывает только выбранный допустимый контекст.

## Сервисные и поздние gates

Для каждой интеграции обязательны её operation-specific acceptance и account/permission
ограничения из [INTEGRATIONS.md](INTEGRATIONS.md). M29/M30/M31/M32 проверяются отдельно:
financial records, AWS/M365 privileged changes, social DMs/posts, video create/upload/share
не считаются принятыми по read-тестам. Неутверждённые critical families остаются выключены,
BLOCKED — запрещены всегда. Доступ recording не разрешает distribution.
