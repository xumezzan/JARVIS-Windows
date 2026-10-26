# Режимы браузера

Новый профильный режим этапа 2 описан в [AUTHENTICATED_WORKSPACE_BROWSER.md](AUTHENTICATED_WORKSPACE_BROWSER.md):
постоянный `<data_dir>/browser_profile`, `JARVIS_BROWSER_CDP_URL`, SPA-поля,
per-tab marks и переключение приложений. Таблица ниже описывает сохранённые legacy режимы.

По запросу пользователя от 2026-10-24 добавлен **Live Browser** и opt-in подключение
к видимому Chrome через CDP. Прежние ограничения этапа 4 теперь относятся к
**Strict Sandbox**, который остаётся режимом по умолчанию. Это изменение браузерного
адаптера, а не завершение Windows-приёмки или реализация следующих этапов roadmap.

| | Strict Sandbox | Live Browser | Live Browser + CDP |
| --- | --- | --- | --- |
| Настройка `JARVIS_BROWSER_MODE` | `strict` (default) | `live` | `live` |
| `JARVIS_BROWSER_CDP_ENDPOINT` | пусто | пусто | `http://localhost:9222` |
| Окно | принадлежащий Jarvis headless Chromium | отдельный видимый Chromium | существующий Chrome с отладкой |
| JavaScript / сабресурсы / HTTPS-редиректы | выключены | включены | включены |
| Cookies / входы | отсутствуют | временный контекст | существующий контекст Chrome |
| Сайты | `JARVIS_BROWSER_ORIGINS` | обычные публичные HTTPS | обычные публичные HTTPS |
| Завершение Jarvis | закрывает свой браузер | закрывает свой браузер | отсоединяется, сохраняет Chrome и вкладки |

Настройки читаются из окружения процесса при запуске. `.env` автоматически не читается.
Модель и страницы не могут изменить режим или адрес CDP. Endpoint допускает только
HTTP loopback (`localhost`, `127.0.0.1`, `[::1]`) с явным портом, без credentials,
пути API, query или fragment. При недоступном CDP возвращается ошибка; скрытого
переключения в другой Chromium нет.

## Запуск на Windows 11

Отдельный Chromium с JavaScript:

```powershell
$env:JARVIS_BROWSER_MODE = 'live'
Remove-Item Env:JARVIS_BROWSER_CDP_ENDPOINT -ErrorAction SilentlyContinue
python -m jarvis
```

Для CDP сначала запустите Chrome в отдельном постоянном профиле. Пример для стандартной
установки Chrome; если Chrome установлен в другом месте, используйте его фактический путь:

```powershell
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 `
  "--user-data-dir=$env:LOCALAPPDATA\Jarvis\ChromeProfile"
```

Войдите в нужные сервисы в этом окне Chrome. В терминале запуска Jarvis:

```powershell
$env:JARVIS_BROWSER_MODE = 'live'
$env:JARVIS_BROWSER_CDP_ENDPOINT = 'http://localhost:9222'
python -m jarvis
# Или npm --prefix desktop start для Electron-приложения.
```

Начиная с Chrome 136, `--remote-debugging-port` для стандартного каталога профиля
не поддерживается: нужен отдельный `--user-data-dir`. Jarvis не копирует основной
профиль/cookies и не перезапускает пользовательский Chrome. Подключиться к обычному
уже открытому окну без включённого CDP нельзя.
[Официальное объяснение Chrome](https://developer.chrome.com/blog/remote-debugging-port).

На macOS эквивалентный запуск Chrome:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Library/Application Support/Jarvis/ChromeProfile"
JARVIS_BROWSER_MODE=live JARVIS_BROWSER_CDP_ENDPOINT=http://localhost:9222 python -m jarvis
```

`browser.get_tabs` подключается лениво и возвращает до восьми существующих допустимых
вкладок первого контекста Chrome; внутренние, пустые и локальные страницы пропускаются.
После него `browser.read` получает текст и элементы нужной вкладки. `browser.open/search`
создают вкладку в том же контексте и выводят её на передний план. Команда
«открой chrome и найди …» сразу вызывает `browser.search` в настроенном BrowserSession,
вместо отдельного запуска Chrome через Windows и поиска в другом браузере.

## Наблюдения и действия

Все восемь browser tools остаются зарегистрированными в PermissionEngine. Simulation
проверяет схемы и URL синхронно, без Playwright, DNS, credentials или сети. Live-схемы
содержат `service=live-browser`, `account=chrome-context`; это обозначение контекста,
а не подтверждение личности вошедшего пользователя. Strict сохраняет прежние значения.

`INSPECT` выполняет фиксированный код приложения. Для live добавлены JS-кнопки,
`role=button`, search/email/url/tel поля и `contenteditable=true`; пароли не становятся
редактируемыми tool targets. Locator использует role и точное уникальное имя.
Неоднозначные/неименованные элементы пропускаются. Нет произвольного JS, selectors,
клавиш или CDP-команд из параметров модели.

Сохраняются UUID вкладки/документа, URL/origin, отпечатки элементов, значения полей,
повторное наблюдение до действия и проверка результата. Live-отпечаток страницы включает
ограниченные текст/заголовок, поддерживаемые элементы и состояния форм, чтобы постоянно
меняющийся служебный HTML SPA не ломал все наблюдения. Это не hash всего DOM, как в Strict.
Изменение наблюдаемых данных требует нового snapshot; живой сайт может потребовать
повторного чтения. Нажатие не означает успешное завершение бизнес-операции сайта.

Навигация, чтение и ввод остаются SAFE. JS-кнопки и live form buttons требуют CONFIRM:
их обработчики нельзя классифицировать только по GET action. Точный наблюдённый POST
формы допускается в Live с CONFIRM; в Strict production POST остаётся запрещён.
Обычные ссылки сохраняют существующую классификацию опасных подписей. Введённый текст
может обрабатываться JS сайта сразу; работа с авторизованным контекстом расширяет доступ
и требует осознанного включения Live. Содержимое страницы остаётся недоверенными данными.

Прочитанный текст возвращается существующему planner, который может передать его
зарегистрированным Windows-инструментам в наблюдённое приложение. OfflineProvider
сохраняет конечный набор команд и не превращается в универсальную модель планирования.
Реальное копирование в Windows-приложения требует отдельной Windows E2E-проверки.

## Сетевая граница Live

`LiveNetworkGuard` использует CDP Fetch Request interception для каждого запроса
подключённой страницы, включая каждый HTTP-редирект, изображения, JS, CSS и fetch/XHR.
Транспорт, TLS, credentials и cookies остаются внутри Chrome. Проверка допускает только
публичный HTTPS и отклоняет loopback, private, link-local, multicast, reserved и mixed DNS
answers. Тестовый HTTP loopback доступен только через constructor capability, отсутствующую
в runtime config и tool schemas. CDP loopback — отдельный канал управления, не разрешение
сайту обращаться к localhost.

Управляемые контексты блокируют service workers. Для подключённых вкладок временно
включается bypass service worker; WebSocket-соединения блокируются. Сабресурсы и JS
могут выполнять POST и другие HTTP-методы как в обычном браузере: PermissionEngine
авторизует инструмент, а не каждый запрос, сделанный кодом сайта. Это намеренное отличие
от Strict; для почтовой отправки продолжает использоваться Outlook adapter.

**Live не является сетевым sandbox уровня Strict.** DNS проверяется перед передачей
запроса Chromium, который разрешает имя самостоятельно: адрес не закрепляется за socket,
поэтому DNS rebinding не исключён. Уже работающие workers, расширения, WebRTC,
неподключённые вкладки, новые popup и отдельные cross-process targets не покрываются
этой проверкой. Загрузка до подключения не отменяется; настройки/разрешения внешнего
Chrome принадлежат пользователю. Нужна OS/proxy-изоляция, если требуется жёсткая гарантия
недоступности LAN для всего процесса. Strict сохраняет offline context и отдельный
aiohttp connector с проверенными закреплёнными DNS-адресами.

Закрытие окна Jarvis снимает его перехватчики и CDP-соединение; внешний context/browser
не закрывается. Уже выполненные сайтом запросы, изменения и авторизация не откатываются.
Cookies не экспортируются в Jarvis, prompts, SQLite или журналы.

API: [Playwright connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp).

## Проверка

В изолированном окружении после `python -m pip install -e ".[dev]"` и
`python -m playwright install chromium`:

```sh
python -m pytest tests/unit/test_browser.py tests/unit/test_browser_live.py tests/unit/test_config.py tests/unit/test_planner.py tests/integration/test_browser.py tests/integration/test_browser_live.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m jarvis --smoke-test
python -m build
```

Локальные тесты используют только собственный fixture HTTP server, без внешних сервисов
или пользовательских профилей. CDP-тест запускает отдельный настоящий Chromium process,
подключается к уже существующей вкладке, проверяет cookies и редирект, затем доказывает,
что после отключения BrowserSession процесс, вкладка и cookies сохранены.
Native Windows, пользовательские входы в сервисы и публичный поиск этим не подтверждаются.

Фактическая проверка 2026-10-24 на macOS / Python 3.12 в изолированном
`/tmp/jarvis-permissions-qa-20261024`:

- Полный `python -m pytest -q`: **743 passed, 5 skipped**, 47.52 s.
  Пропущены opt-in live model, voice и native Windows acceptance.
- `QT_QPA_PLATFORM=cocoa` + browser/planner UI tests: **18 passed**.
- Видимый Live Browser (`headless=False`) открыл fixture через PermissionEngine,
  вернул наблюдённые текст и элементы, затем закрыл свой Chromium.
- Отдельный CDP integration test доказал сохранение внешнего Chromium, вкладки и
  fixture cookie после `BrowserSession.close`; пользовательский профиль не использовался.
- Ruff lint/format, mypy, `git diff --check`: пройдены.
- `QT_QPA_PLATFORM=cocoa python -m jarvis --smoke-test`: **success**.
- `python -m build`: wheel и sdist собраны.
- Проверка Electron environment filter: Live/CDP settings сохраняются, provider key
  и proxy variables отбрасываются.

Первоначальный полный прогон в `.venv-reference` остановился на collection из-за
скрытого `.pth`; для окончательной проверки использовано указанное внешнее окружение.
Первый полный прогон в нём выявил устаревшее E2E-ожидание запуска второго браузера;
оно обновлено и повторный полный suite пройден. Проверка не обращалась к публичным
сайтам, OS credential store, микрофону или пользовательским аккаунтам.
