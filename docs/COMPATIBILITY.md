# Совместимость и зависимости

Проверка официальной документации: 2026-09-14–15. Локальное окружение — macOS,
CPython 3.12.7. Цель продукта — Windows 11 x64 / CPython 3.12.
Соответствие документации не равно успешному запуску на Windows.

| Компонент | Решение и источник |
| --- | --- |
| Python | >=3.12; Windows setup и launcher описаны в [официальной документации Python](https://docs.python.org/3.12/using/windows.html) |
| Package/build | `src` layout и pyproject; Hatchling, wheel и sdist по [Python Packaging Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/) |
| pytest | Dev dependency; установка и запуск `python -m pytest` по [pytest](https://docs.pytest.org/en/stable/getting-started.html) |
| pytest-asyncio | Dev dependency для будущих async-тестов; [официальная документация](https://pytest-asyncio.readthedocs.io/en/stable/) |
| Ruff | Dev dependency, lint + format; [установка через pip](https://docs.astral.sh/ruff/installation/) |
| mypy | Dev dependency, strict; [официальное руководство](https://mypy.readthedocs.io/en/stable/getting_started.html) |
| PySide6 (этап 1) | Установлен `PySide6-Essentials`: QtCore/QtGui/QtWidgets входят в базовый пакет, Addons не нужны; [Package Details](https://doc.qt.io/qtforpython-6/package_details.html). Требования: [Getting Started](https://doc.qt.io/qtforpython-6/gettingstarted.html); Windows 11: [Qt for Windows](https://doc.qt.io/qt-6/windows.html) |
| pytest-qt (этап 1) | GUI fixtures, ожидание сигналов и настоящий Qt event loop: [официальный tutorial](https://pytest-qt.readthedocs.io/en/latest/tutorial.html) |

Потоки и таймеры сверены с официальными [QThread](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html)
и [QTimer](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QTimer.html). UI обновляется только
в main thread; остановка кооперативная, без `terminate()`.

Pydantic добавлен на этапе 2. Strict schemas и ограничения mutable/frozen моделей сверены с
[Strict Mode](https://docs.pydantic.dev/latest/concepts/strict_mode/) и
[Models](https://docs.pydantic.dev/latest/concepts/models/). Canonical JSON создаёт immutable
snapshot независимо от того, что frozen-модель может содержать mutable коллекции.
Кооперативные timeout/cancellation сверены с
[Python asyncio tasks](https://docs.python.org/3.12/library/asyncio-task.html).

OpenAI SDK, keyring, voice providers и PyInstaller
добавляются в соответствующих этапах после отдельной проверки официальных docs и
Windows. Их совместимость здесь ещё не подтверждена. Не устанавливать весь будущий стек
ради каркаса. pywinauto должен быть ограничен Windows dependency marker и lazy import.

## Политика версий

Исходный план требует проверки на реальной Windows перед фиксацией версий. Сейчас
`pyproject.toml` содержит зависимости без pin, включая runtime `PySide6-Essentials` и `pydantic`.
Это намеренный временный компромисс: повторная установка может разрешить
другие версии. Фактически проверенные локальные версии записываются в отчётах `MILESTONE_*.md`
как свидетельство запуска, а не как Windows lockfile.

На Windows установить зависимости, выполнить команды из `TESTING.md`, сохранить OS,
архитектуру, Python и вывод `python -m pip list`. После успешной проверки создать
воспроизводимый constraints/lock с разрешёнными версиями, затем повторить чистую установку
с этим lock и те же проверки. Для PySide6 дополнительно проверить настоящее окно.

## Windows Automation — проверка документации 2026-09-15

Pywinauto и psutil добавлены только для Windows, без pin. На текущем Mac они не
устанавливаются и их native-совместимость с Python 3.12/Windows 11 не проверена.
Статический `mypy --platform win32` не считается запуском Windows.

- [Pywinauto Getting Started](https://pywinauto.readthedocs.io/en/latest/getting_started.html):
  UIA backend, семантический выбор элементов.
- [UIAElementInfo](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.uia_element_info.html):
  process/handle/runtime ID/automation ID/role; идентичность повторно проверяется.
- [Microsoft EM_REPLACESEL](https://learn.microsoft.com/en-us/windows/win32/controls/em-replacesel):
  буквальное изменение edit/rich edit с undo, без глобальных клавиш.
- [SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw):
  адресация HWND и ограничение ожидания; не обещает откат операции.
- [Python subprocess API](https://docs.python.org/3/library/asyncio-subprocess.html):
  asyncio-процесс, kill/wait и pipe lifecycle; на Windows используется стандартный Proactor loop.
- [psutil](https://psutil.readthedocs.io/en/latest/): executable и время создания процесса.

Проверить на Windows: реальные версии Notepad (включая Store-вариант), UIA role/class,
наличие editor HWND, текстовый read-back, выбранные вкладки, права доступа и ограничения
foreground focus. Неподдерживаемый editor отклоняется без keyboard fallback. Chrome/VS Code
должны быть установлены в стандартные каталоги; нестандартная установка не угадывается.

## Browser Automation — проверка документации 2026-09-15

Playwright и aiohttp добавлены без pin. Локально установлены Playwright 1.62.0,
Chromium 151.0.7922.34 (build 1234) и aiohttp 3.14.3; Windows 11 ещё не проверена.
Chromium устанавливается отдельно: `python -m playwright install chromium`.

- [Playwright installation](https://playwright.dev/python/docs/intro): Python API, browser binaries,
  поддерживаемые ОС; целевой Windows 11 нужно проверить отдельно.
- [Browser contexts](https://playwright.dev/python/docs/api/class-browsercontext): отдельный
  непостоянный context, offline и блокировка service workers.
- [Routing](https://playwright.dev/python/docs/api/class-route): перехват и fulfil/abort;
  код не передаёт запросы обратно встроенному транспорту Chromium.
- [Locators](https://playwright.dev/python/docs/locators): role + exact accessible name,
  неоднозначный результат отклоняется.
- [aiohttp client](https://docs.aiohttp.org/en/stable/client_advanced.html): custom resolver,
  middleware, DummyCookieJar; проверенные DNS addresses передаются самому connector.
- [CDPSession](https://playwright.dev/python/docs/api/class-cdpsession) и
  [Page.stopLoading](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-stopLoading):
  остановка текущей загрузки в принадлежащем Jarvis Chromium, без кода из страницы.

Текущий adapter предназначен только для Chromium. Firefox/WebKit, пользовательский Chrome
и динамические сайты не проверены и не являются поддерживаемыми fallback.

TLS fallback: certifi 2026.7.22 ([официальный пакет](https://pypi.org/project/certifi/))
предоставляет Mozilla CA только если системный набор Python пуст. Существующие OS roots
сохраняются. `SSLContext(PROTOCOL_TLS_CLIENT)` требует цепочку и hostname, затем
`load_default_certs`/`load_verify_locations`; см. [Python ssl](https://docs.python.org/3.12/library/ssl.html).
Прямое создание context не включает побочный `SSLKEYLOGFILE` механизм
`create_default_context`. TLS validation не отключается. Это исправляет обнаруженный
на локальном python.org macOS Python пустой CA store без изменения системной установки.
