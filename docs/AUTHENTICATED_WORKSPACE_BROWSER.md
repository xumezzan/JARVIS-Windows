# Authenticated Workspace Browser — этап 2

Strict Sandbox остаётся режимом по умолчанию. Новый BrowserMode.WORKSPACE
(authenticated_workspace) использует включённые приложения WorkspaceProfile,
постоянный браузерный профиль либо уже запущенный Chrome с CDP.
Legacy JARVIS_BROWSER_MODE=live и JARVIS_BROWSER_CDP_ENDPOINT сохранены.
Четыре новых инструмента навигации/автозаполнения регистрируются только после
явного выбора authenticated mode; Strict сохраняет прежние восемь инструментов.

## Постоянный профиль

Выберите WorkspaceProfile.browser_mode = "authenticated_workspace" либо задайте
переменную процесса перед запуском:

~~~powershell
$env:JARVIS_BROWSER_MODE = 'authenticated_workspace'
Remove-Item Env:JARVIS_BROWSER_CDP_URL -ErrorAction SilentlyContinue
Remove-Item Env:JARVIS_BROWSER_CDP_ENDPOINT -ErrorAction SilentlyContinue
python -m jarvis
~~~

Jarvis лениво вызывает launch_persistent_context с каталогом
**<data_dir>/browser_profile**. JavaScript, cookies, разрешённые сабресурсы и
HTTPS-редиректы включены. Вход выполняет сам владелец в видимом окне.
Cookies/local storage остаются в браузерном профиле между запусками.
Cookie store и extension internals не являются инструментами планировщика.
Файловые контракты files.* и fs.* не допускают чтение этого каталога, даже
если data_dir находится внутри разрешённых документов. Каталог исключён из Git;
symlink/UNC profile directory отвергается.

При закрытии Jarvis его собственный Chromium закрывается, профиль сохраняется.
Занятый профиль даёт browser_unavailable, без перехода на другой профиль и
без экспорта cookies. Production-запуск не использует Playwright
--password-store=basic/mock keychain; согласия OS keychain остаются действиями
владельца. Только constructor-injected HTTP fixtures сохраняют стандартный
тестовый mock keychain Chromium.

## Подключение к Chrome владельца

~~~powershell
$env:JARVIS_BROWSER_MODE = 'authenticated_workspace'
$env:JARVIS_BROWSER_CDP_URL = 'http://127.0.0.1:9222'
python -m jarvis
~~~

Chrome должен уже работать с отладкой и нужными входами. Jarvis не включает
отладку тайно и не переносит обычный профиль. Современный Chrome требует
отдельный --user-data-dir для запуска с --remote-debugging-port:
[правило Chrome](https://developer.chrome.com/blog/remote-debugging-port).
В этот отдельный профиль владелец входит сам.

Endpoint принимается только из trusted startup configuration: HTTP loopback
с явным портом, без credentials/path/query/fragment. Старый и новый env aliases
могут совпадать; конфликт вызывает конечную ошибку конфигурации.
Явный JARVIS_BROWSER_MODE=strict не совмещается с CDP.
Модель не получает полей endpoint/profile directory/transport overrides.

При закрытии сессии Jarvis снимает только свои route/Fetch handlers и
отсоединяется. Внешний Chrome, его вкладки и входы сохраняются.
Не принадлежащие рабочей сессии вкладки владельца не закрываются и
не становятся инструментальными целями.

## Origins и корпоративная сеть

Workspace Browser разрешает точные origins включённых приложений плюс trusted
JARVIS_BROWSER_ORIGINS. В отличие от legacy Live, произвольный публичный origin
не разрешается. Добавляйте необходимые CDN/SSO origins в AppEntry.allowed_origins
явно: страница не может разрешить себе новый домен.
Private/loopback/link-local, localhost, metadata endpoints и file:// блокируются.
Network checks выполняются на начальных запросах, ресурсах, popups и redirect hops.
Первая загрузка popup без доступного frame блокируется целиком, даже если её
URL разрешён: к такой вкладке ещё нельзя привязать guard редиректов.
SSO, требующий подобного popup, нуждается в ручном входе владельца до подключения
Jarvis. Навигация и редиректы уже управляемой вкладки поддерживаются.

Для собственного корпоративного ERP есть отдельное явное разрешение:

~~~python
from jarvis.workspace.models import AppEntry

erp = AppEntry(
    id="corporate_erp",
    name="Corporate ERP",
    category="custom_erp",
    kind="web",
    launch_url="https://erp.company.internal/home",
    allowed_origins=("https://erp.company.internal",),
    allow_private_network=True,
)
~~~

Без allow_private_network=True частный DNS-адрес этого сайта запрещён.
Разрешение действует только для точных origins включённого custom_erp,
включая порт, и только для RFC1918/IPv6 ULA DNS-ответов. Оно не разрешает
IP literals, wildcards, целые доменные суффиксы, 127.*, localhost,
link-local/metadata или смешанный DNS-ответ с запрещённым адресом.
TLS verification не отключается. Флаг хранится в профиле, но не передаётся
как инструкция модели.

Это защита контролируемого браузерного трафика, а не OS firewall: Chromium
разрешает DNS самостоятельно. Нельзя обещать изоляцию от DNS rebinding,
уже запущенных внешних workers/extensions, WebRTC и неподключённых вкладок
чужого Chrome. WebSockets и service workers страниц остаются ограничены.
Приватные endpoints проверены fixtures; корпоративная сеть владельца
требует отдельной live-приёмки.

## SPA, номера элементов и ввод

Инспектор видит обычные ссылки/поля и contenteditable (включая plaintext-only),
ARIA textbox/button/tab/menuitem/combobox/option. В наблюдении до 96 элементов
и 40 000 UTF-8 байт метаданных элементов. Парольные/OTP/private поля и скрытые
токены не читаются для значений или fingerprint и не попадают в PageView.
Элемент без подписи получает короткое локальное имя. Одинаковые подписи
допустимы: действие привязано к наблюдавшемуся DOM node, а не к первому имени.

PageTarget связывает tab/document/origin/URL/fingerprint и observation_id.
browser.click/type принимают либо полный element, либо mark_id (1..96)
вместе с точным target. Runner разрешает номер **до** PermissionEngine.prepare,
так что классификатор, audit и UI approval получают полный элемент.
Номер относится к последнему наблюдению этой вкладки, не к другой вкладке,
скриншоту или исторической памяти. После изменений используются свежие
результаты. Неизвестные/дублирующиеся/stale номера и изменившийся DOM отвергаются.

Playwright fill фокусирует поле и выполняет native text insertion с input
events, включая contenteditable:
[официальное поведение](https://playwright.dev/python/docs/input).
Текст проверяется по тому же безопасному DOM node, затем возвращается свежее
наблюдение. Ни произвольного JS, ни selectors, ни clipboard в аргументах нет.
Fixtures проверяют русский/многострочный ввод и bubbling inputType=insertText;
это не live acceptance реальных редакторов Notion/Asana/Claude/Teams/Meta.

## Инструменты и риск

| Инструмент | Контракт | Риск |
| --- | --- | --- |
| browser.open_workspace_app | app_id из enabled web/hybrid AppEntry | SAFE |
| browser.scroll | target, direction: up/down/left/right, amount: 1..3000 пикселей | SAFE |
| browser.press_key | target, key: Enter/Escape/Tab/PageDown/PageUp | Enter CONFIRM; остальные SAFE |
| browser.trigger_1password_autofill | target разрешённого обычного сайта | CONFIRM |
| browser.type | target, element или mark_id, text | SAFE, site policy имеет приоритет |
| browser.click | target, element или mark_id | Чувствительные/неоднозначные JS actions CONFIRM |

App switching обновляет существующие вкладки и ищет origin приложения.
Для общего origin, например Outlook Mail/Calendar, предпочтение получает нужный
path. При необходимости сменить route вкладка с несохранённым черновиком
сохраняется, приложение открывается отдельно. Нативные приложения, выключенные
записи, неизвестный ID или отсутствующий URL отвергаются. Workspace ограничен
32 вкладками, legacy режимы сохраняют прежний предел 8.

Publish/Post/Comment/Send/Delete/Pay и русские публикация/отправка/удаление
требуют CONFIRM. Ввод черновика не даёт права публикации. Неизвестная JS-кнопка
не становится SAFE из-за названия или workspace_notes.
Более строгие существующие tiers сохраняются: менеджеры паролей не читаются,
финансовые/административные сайты остаются read-only до реализации усиленного Р5.
Уровень из профиля не понижает реальную политику; оплата/админ-изменения не
объявлены доступными из одного прохождения теста на классификатор.

1Password-инструмент посылает фиксированный Fill in Browser shortcut
(Ctrl+\ на Windows, Cmd+\ на macOS) в выбранную вкладку после точного approval:
[документация 1Password](https://support.1password.com/keyboard-shortcuts/).
Приложение/расширение 1Password устанавливает и разблокирует владелец.
Jarvis не открывает vault/extension DOM, не читает пароли и не копирует их
в clipboard. Выход — только autofill_requested с tab ID, без чтения полей
после действия. Это receipt отправленной команды, **не подтверждение**
наличия расширения, заполнения или входа: реальная связка 1Password/Chrome/OS
требует live-проверки владельцем. Старые наблюдения сброшены; новый контекст
получают через get_tabs/read.

## Проверка

~~~text
.venv/bin/python -m pytest -q tests/unit/test_browser_workspace.py tests/integration/test_browser_workspace.py tests/integration/test_browser_live.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build
~~~

На Windows — .venv\Scripts\python.exe вместо .venv/bin/python. Полный набор
также запускается в Windows CI. На этом Mac для Qt-проверок нужен явный путь
QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/jarvis-multimodal-qt-runtime/plugins/platforms
и QT_QPA_PLATFORM=offscreen из-за hidden attributes существующей .venv.
Реальные аккаунты, корпоративный DNS, 1Password и Windows UIA не выдаются за
проверенные по fixtures; ordinary tests не обращаются к ним.

Локальная проверка 2026-10-26: Ruff, format-check, mypy (324 source files),
полный pytest (1789 passed, 23 skipped), smoke-test и wheel/sdist build прошли
с кодом 0. После последнего изменения очистки сессии отдельно прошли все
35 browser integration tests. Electron: 11 tests passed. Контрольный запрос
с 96 максимальными SoM-элементами и изображением остаётся меньше 180 000 байт;
общий предел OpenAI 262 144 байта сохранён. Windows CI проверяет commit PR
отдельно; эти результаты не являются live-приёмкой сервисов или устройства.
