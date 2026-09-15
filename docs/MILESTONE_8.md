# Этап 8 — Outlook через OAuth / Microsoft Graph

Дата: 2026-09-15. Пользователь выбрал **Outlook**. Реализация и fixture-проверки выполнены
на macOS / Python 3.12.7. Живые OAuth, почта, Windows, OpenAI и голосовое оборудование
**не проверены**. Это не завершение MVP; установщик и Windows E2E относятся к этапу 9.

## Что доступно

Откройте **Планировщик → Outlook**. Панель использует существующую тему, прокрутку,
plain-text поля и существующий ApprovalDialog.

| Инструмент | Эффект | Разрешение |
| --- | --- | --- |
| `outlook.account` | Текущий явно подключённый аккаунт и новая идентичность сеанса | SAFE |
| `outlook.list` | До 10 писем: Входящие, Отправленные или Черновики | SAFE |
| `outlook.read` | Одно письмо как недоверенные данные, без HTML-рендеринга | SAFE |
| `outlook.local_draft` | До 10 черновиков только в памяти окна | SAFE |
| `outlook.save_draft` | Создать новый черновик в Outlook; проверить чтением | CONFIRM |
| `outlook.send` | Отправить полный снимок через `/me/sendMail` | CONFIRM |

Ручная панель запрашивает 5 писем; лимит инструмента — 10. Пагинация, поиск произвольным
OData, удаление писем, shared mailboxes, aliases, HTML-композиция и календарь не добавлены.
Локальный черновик можно выбрать и вернуть в редактор. Он исчезает при закрытии окна,
смене/отключении аккаунта. Удаление вложений делает старые ссылки черновиков непригодными;
заново выберите файлы. Удаления или редактирования существующего удалённого черновика нет.

**Симуляция** проходит schema/policy/approval и не вызывает даже credential, precondition
или verification hooks адаптера. Подключение — отдельная явная настройка, открывающая
настоящий вход Microsoft после галочки согласия; переключатель симуляции относится
к почтовым инструментам, а не к этой настройке.

## Настройка для живой проверки — выполняет пользователь

1. В Microsoft Entra зарегистрируйте своё приложение. Для личного Outlook.com и рабочих
   аккаунтов нужен соответствующий тип поддерживаемых аккаунтов. Текущая реализация
   использует public Microsoft cloud и authority `https://login.microsoftonline.com/common`.
2. В Authentication добавьте платформу **Mobile and desktop applications**, redirect URI
   **`http://localhost`** для системного браузера. Client secret не создавайте и не вводите.
3. Делегированные Graph scopes: **User.Read**, **Mail.ReadWrite**, **Mail.Send**.
   `User.Read` нужен для проверки `/me`; `Mail.ReadWrite` — для чтения и удалённого черновика;
   `Mail.Send` отдельно разрешает отправку. MSAL добавляет OpenID/profile/offline_access
   для входа и обновления токенов. Application permissions, Contacts и Calendar не нужны.
   Политика организации может потребовать согласие администратора; обхода нет.
4. Введите Application (client) ID в панели Jarvis, отметьте согласие и нажмите
   **Подключить / сменить аккаунт**. Войдите сами в системном браузере. Пароль, MFA и
   согласие остаются у Microsoft. Отмена или отказ не подключают аккаунт.
5. Проверьте показанный адрес. После смены аккаунта поля, локальные черновики, наблюдения,
   вложения и approvals сбрасываются. При новом открытии окна нужен явный вход; скрытого
   автоматического подключения нет.
6. **Отключить и удалить локальные токены** удаляет кэш Jarvis в OS credential store.
   Это не выход из браузера и не отзыв серверного consent. При необходимости отдельно
   отзовите доступ приложения в настройках Microsoft. При ошибке удаления UI не заявляет,
   что токены удалены; повторите явное отключение после устранения проблемы хранилища.

Сейчас для тестов не регистрировалось приложение и не использовались личные аккаунты.
Client ID не является секретом; токены, пароли и client secrets не передавайте в чат,
команды, репозиторий, env-файлы или модель.

## Отправка и вложения

Введённые адреса — обычные ASCII mailbox адреса с доменом: имена, роли, display-name
синтаксис и переносы строк отклоняются. До 10 адресов в каждом поле Кому/Копия/Скрытая
копия, тема до 500 символов, текст до 16 000 символов. Общий Action по-прежнему ≤64 KiB.

До 3 файлов по 32 KiB. Только выбранный пользователем regular file читает killable helper
с лимитом 5 секунд. Файлы не открываются по путям из модели. Байты сохраняются в RAM;
снимок содержит имя, размер, SHA-256 и случайный ID. Изменение исходного файла после
загрузки не меняет эти байты. Для новых байтов файл нужно выбрать заново.

Полный JSON preview показывает service, account (Graph user ID, адрес, идентичность
подключения), тип действия, все To/Cc/Bcc, тему, текст и идентичности вложений. Только
существующая кнопка подтверждения выдаёт одноразовый token (TTL 60 секунд с prepare).
Программный `accept()`, уточнение или голос не выдают authority. Изменение формы/режима,
отмена и смена аккаунта инвалидируют подготовленный запрос.

Удалённый черновик создаётся одним POST. Затем GET проверяет ID, isDraft, полный текст,
получателей и содержимое вложений. Несовпадение — ошибка с возможным уже созданным
черновиком, без повтора POST. Отправка всегда передаёт полный показанный message;
она не отправляет изменяемый удалённый draft по ID.

**`202 Accepted` означает только принятие запроса Microsoft. Доставка не подтверждена.**
Итог инструмента — `state=accepted`, `delivery_verified=false`; панель показывает это
явно. Отсутствие ответа, timeout, cancellation или ошибка аудита после POST не доказывают
отсутствия отправки. Повторных сетевых попыток нет. Проверяйте Отправленные и тестового
получателя отдельно, прежде чем вручную готовить новую отправку.

## Архитектура и границы

- `mail/oauth_helper.py`: официальная MSAL, auth code + PKCE в системном браузере,
  без embedded browser/broker, client secret и доступа к профилям/cookies браузера.
- MSAL cache — только явный Windows Credential Locker или macOS Keychain backend.
  ASCII chunks до 1000 символов, общий предел 64 000 символов. Manifest с digest
  инвалидируется перед записью; частичный кэш не загружается. Disconnect удаляет все
  фиксированные slots, включая остатки прерванной записи. Совместимость реального
  Windows credential backend остаётся непроверенной.
- Killable credential helper: 150 секунд на interactive connect, 15 на silent/disconnect;
  stdout только bounded private pipe, stderr отключён, никаких credential в argv/env/logs.
  Refresh доступен только для точного MSAL home_account_id. Перед почтовой операцией
  повторный `/me` проверяет Graph ID и адрес. Нет fallback в plaintext или другого аккаунта.
- OAuth HTTP принимает только HTTPS `login.microsoftonline.com`; Graph — только фиксированные
  `/me` маршруты HTTPS `graph.microsoft.com/v1.0`. Нет proxies из env, cookies, redirects
  и retries; response до 256 KiB, HTTP timeout 10/12 секунд. Новых generic browser POST нет.
- Почтовые вызовы выполняются через sealed registry и PermissionEngine. Audit до adapter
  остаётся обязательным. Connect/disconnect — UI-only setup вне каталога планировщика;
  они отдельно пишут безопасные STARTED/FINISHED под `user_ui_connection` до/после работы.
  Ошибка аудита блокирует начало OAuth; сбой финальной записи не даёт успешного результата.
- QThread/asyncio для операций, killable helpers для OS credentials и чтения вложения.
  Stop/закрытие ждут завершения отмены. Выданный запрос Microsoft не отзывается задним числом.
- Планировщик требует аккаунт из SUCCESS-наблюдений текущей задачи. Для чтения нужен ID
  из текущего списка. Для состава письма адреса должны буквально присутствовать в команде
  или ответах пользователя; иначе детерминированное уточнение до approval. Результаты,
  письма и память не разрешают выбрать получателя. Simulation не создаёт наблюдения аккаунта;
  последовательность зависимых почтовых шагов без наблюдения отклоняется.
- OpenAI opt-in получает почтовые наблюдения как недоверенные tool results в рамках
  прежнего disclosure команды/наблюдений. Секреты и байты вложений в tools не входят.
  Метки памяти передаются только по прежнему отдельному согласию. Автозапоминания писем нет.
- Офлайн-провайдер сохраняет учебные рецепты; почта доступна через ручную вкладку или
  opt-in модель. Старые voice/transcript/approval, CRITICAL и BLOCKED границы сохранены.

## Проверки

Использовано изолированное `.venv`; `python -m pip install -e ".[dev]"` выполнено.
Добавлена неприкреплённая зависимость `msal` (локально установилась 1.38.0); pins не вводились.
Никакие fixture-тесты не обращаются к Microsoft, OpenAI, настоящим credentials или микрофону.

| Проверка | Фактический результат |
| --- | --- |
| Focused Outlook unit/Qt | **50 passed**, 1.68 s |
| Полный `python -m pytest -q` | **388 passed, 5 skipped**, 30.41 s |
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed, 125 файлов |
| `python -m mypy` | Passed, 103 source files |
| `python -m mypy --platform win32` | Passed; static analysis, не Windows runtime |
| Native Cocoa mail/planner/voice/memory Qt | **50 passed**, 16.66 s |
| `python -m pip check` | No broken requirements found |
| Cocoa `python -m jarvis --smoke-test` | success, exit 0 |
| `python -m build` | Wheel и sdist собраны |
| `git diff --check` | Passed |

Пять пропусков: 3 Windows acceptance, 1 live OpenAI, 1 voice hardware. Живая почта
проверяется отдельно вручную; дополнительного автоматического live send нет.

Негативные PermissionEngine тесты перед реализацией: **62 passed**. Проверены OAuth/cache
fixtures, disconnect/reconnect, changed account/home ID, expiry/reuse/concurrent consume,
To/Cc/Bcc/body/attachment mutation, изменённый remote draft, неопределённый POST, cancellation,
audit unavailable, недоверенные письма, account/recipient provenance, Qt confirmation,
programmatic accept без authority, simulation без hooks и прежние voice/memory сценарии.

Настоящий `python -m jarvis` запущен на Cocoa с отдельным `/tmp/jarvis-milestone8-ui`.
Через native computer-use открыты планировщик и Outlook, осмотрены текст, поля и прокрутка.
OAuth-кнопка не запускалась, никаких писем не отправлялось.

## Windows 11: команды и живая приёмка

Команды для разработчика; установка без предварительного Python — будущий этап 9.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,voice]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/unit/test_permissions.py tests/unit/test_outlook.py tests/integration/test_mail_ui.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_mail_ui.py -q
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m jarvis
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe -m build
```

Для живой почты сначала явно выберите **свой тестовый аккаунт и тестового получателя**.

1. Пройдите setup выше. Проверьте отказ/отмену входа, точный аккаунт и повторное подключение.
2. В simulation проверьте send preview + Cancel: писем и черновиков в сервисе нет.
3. В execute получите список, прочитайте выбранное письмо. Проверьте обычный текст и
   отсутствие переходов/загрузки удалённых изображений из письма.
4. Создайте локальный черновик: в Outlook он не появляется. После отдельного CONFIRM
   создайте remote draft и сравните To/Cc/Bcc, тему, текст и небольшой тестовый файл.
5. Подготовьте новое тестовое письмо. До подтверждения письма нет. Нажмите подтверждение
   сами, проверьте статус «принял запрос», затем Отправленные и получение точного письма.
   Не считать `202` или fixture SUCCESS доказательством доставки.
6. Проверьте изменение поля при подготовке, истечение 60 секунд, отмену/закрытие окна,
   смену аккаунта и недоступный credential/audit backend. Старый approval не исполняется.
7. При сетевом сбое после запуска вручную установите фактический результат до повтора.
   Проверьте отсутствие второго POST. Удалите только явно выбранные тестовые данные сами.
8. Отключитесь; проверьте удаление локального кэша, отсутствие активного сеанса и helpers.
   Зафиксируйте Windows build, версии библиотек, вид аккаунта и реальные результаты.

## Официальные источники, проверены 2026-09-15

- [MSAL Python API](https://msal-python.readthedocs.io/en/latest/) — interactive public client,
  PKCE, timeout, token cache и HTTP seam.
- [OAuth authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
  — desktop public client, PKCE, loopback redirect и отсутствие client secret.
- [Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
  — Mail.Send, JSON attachments, Sent Items и ограничение `202 Accepted`.
- [Create message](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0)
  — удалённый draft, Mail.ReadWrite и `201 Created`.
- [Get message](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0)
  — чтение message и plain-text body preference.
- [Get user](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0)
  — `/me` и delegated User.Read для собственной идентичности.
