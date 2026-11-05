# Jarvis на macOS

## Постоянная иконка и локальное обновление

Для установки в общие «Программы» один раз выполните из подготовленного checkout:

```sh
.venv/bin/python scripts/macos/manage_installation.py --install "$PWD"
```

Приложение располагается в `/Applications/Jarvis.app` и регистрируется в LaunchServices.
Его можно запускать двойным щелчком из Finder → Программы и закрепить в Dock.
Прежний `~/Applications/Jarvis.app` перенаправляется на этот экземпляр;
исходная копия сохраняется для восстановления вне списка приложений.

Явно установленный пользовательский LaunchAgent `org.jarvis.desktop.sync` следит
за готовым `src/jarvis/desktop_assets/manifest.json` и повторяет проверку каждые 30 с.
После изменения frontend завершайте работу обычным `npm --prefix desktop run build`.
Когда Jarvis закрыт, проверенная сборка атомарно заменяет ту же `.app`, сохраняя
иконку, bundle ID, настройки и OS credentials. Открытое приложение не перезапускается:
обновление ждёт закрытия. Python-код привязан к выбранному checkout и подхватывается
при следующем запуске. Неудачная сборка не публикуется.

Это синхронизация с локальным проектом: агент не делает git pull, не запускает npm,
не запускает Jarvis, не включает микрофон и не передаёт данные в сеть.
Установленная копия helper хранится в
`~/Library/Application Support/JarvisDesktop/maintenance/` и работает независимо
от последующего merge PR с исходниками установщика.
`source.json` содержит выбранный checkout; `status.json` — последнее состояние:
`current`, `updated`, `waiting_for_close` или `update_failed`.

Чтобы отключить синхронизацию, выполните:

```sh
launchctl bootout "gui/$(id -u)/org.jarvis.desktop.sync"
```

Для отключения после следующего входа также переместите
`~/Library/LaunchAgents/org.jarvis.desktop.sync.plist` из LaunchAgents.
Само приложение продолжит запускаться, настройки и ключи не удаляются.

Общий Python-движок и Electron работают на Windows 11 и macOS. Имена инструментов
`windows.*` сохранены для совместимости: `ProcessBackend` сам выбирает доверенный
`windows.worker` или `macos.worker`. Модель не выбирает платформу, модуль или команду.
Схемы `WindowTarget`, `EditorTarget`, `ElementTarget`, `NativeRequest`, `WindowsResult`
общие; PermissionEngine, аудит, симуляция и точные подтверждения не обходятся.

## Интеграция Windows и Mac — 04.11.2026

После добавления нативных адаптеров объединены обновления зависимостей,
[управление окном](MACOS_WINDOW_CONTROLS.md) и
[пауза при физическом вводе](MACOS_INPUT_TAKEOVER.md). Пересобраны поставляемые
Electron assets и их manifest. HUD сохраняет поддержку fullscreen Spaces и
возвращает нативные кнопки при подтверждении; оба свойства проверяются вместе.
CI проверяет Python и Electron на Windows/macOS, включая запуск Electron binary.
Исторические отчёты отдельных PR не заменяют проверки интеграционной версии.
Голос, живые аккаунты, чистая установка Windows и автономный Mac-дистрибутив
остаются отдельными незакрытыми проверками/задачами.

## Запуск на Mac

Нужны Python 3.12+, Node.js, поддерживаемый текущим Vite, и нативная macOS-сессия.
Зависимости не фиксируются без проверки целевой платформы.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,voice]'
.venv/bin/python -m playwright install chromium
npm --prefix desktop ci
npm --prefix desktop run build
.venv/bin/python scripts/package_desktop.py
```

После подготовки дважды нажмите `scripts/macos/Launch-Jarvis.command` или откройте
`~/Applications/Jarvis.app`. Приложение можно добавить в Dock; LaunchServices
использует его обычную bundle identity. `.command` проверяет актуальность bundle.
Это локальная сборка, привязанная к этому checkout и `.venv`, а не переносимый DMG.
Не перемещайте и не удаляйте проект/окружение. Подготовка чистого Mac без Python/Node,
Developer ID, notarization и автономный updater в этот сценарий не входят.

Секреты хранятся в macOS Keychain. Установщик Windows не запускается на Mac:
его CLI явно направляет на macOS launcher, хотя `supported_host()` узнаёт обе ОС.

## Нативные действия и разрешения

Поддерживаются список видимых окон, запуск приложения по любому имени из системного
каталога или явному пути `.app`, фокус по handle/подстроке заголовка/процесса,
инспекция AX, скриншоты с Set-of-Marks, универсальные клики, комбинации клавиш,
ввод текста и системный буфер. Перевод названий берётся из метаданных приложений;
таблиц псевдонимов и рецептов для отдельных программ нет.
LLM (`OpenAIProvider`, по умолчанию `gpt-4o`) выбирает последовательность действий.
Обычный запуск не передаёт приложению командную строку или shell-код.

Основной адаптер использует PyObjC Cocoa/Quartz/ApplicationServices, загружаемые
только в одноразовом helper. Без них запуск/Chrome/буфер используют фиксированные
`open`, `pbcopy`, `pbpaste`, а список окон — фиксированный JXA/CoreGraphics script.
AX-действия требуют установленных нативных зависимостей; отсутствующая библиотека
возвращает диагностическую причину, а не имитирует успех.

Повреждённые или слишком большие Info.plist отдельного приложения пропускаются,
а не ломают весь каталог. Холодный запуск ждёт реальное окно до 14 секунд в
общем бюджете helper 20 секунд, без повторного `open`. Не готовое AX-поле не
превращает успешное открытие окна в ошибку: возвращается заново проверенный
WindowTarget без редактора, и право на ввод из такого результата не появляется.

В «Системные настройки → Конфиденциальность и безопасность» пользователь разрешает:

- **Универсальный доступ / Accessibility** — наблюдение и управление элементами.
- **Запись экрана** — снимок окна; Accessibility также нужен для удаления паролей.
- **Микрофон** — только если нужен голос, включаемый видимым жестом пользователя.

При отсутствии доступа helper возвращает `control_unsupported` с конечной причиной
`macos_accessibility_required` или `macos_screen_recording_required` и открывает
фиксированный раздел настроек. Разрешение выдаёт только пользователь; запуск из
Codex/Terminal и запуск `.app` могут требовать отдельного разрешения процесса запуска.

Окна связаны с CGWindowNumber, PID, временем старта процесса и executable.
Изменение заголовка или runtime ID AX-дерева не меняет идентичность окна.
AX-поля повторно находятся по семантическим признакам; пароли не становятся целями.
Если доступный AX-контрол поддерживает запись, ввод проверяется чтением SHA-256.
Для кастомных контролов адаптер автоматически вставляет текст через Command-V,
сохраняя и восстанавливая форматы NSPasteboard. Такой результат имеет
`performed=true`, но `text_verified=false`, пока содержимое нельзя прочитать обратно.
Это квитанция ввода; планировщик должен проверить результат новым наблюдением.
При недоступном AXPress клик использует центр текущего элемента; `(x, y)` проверяются
по актуальным границам выбранного окна. Для ввода можно выбрать текущее активное окно.
Комбинация клавиш — модификаторы и одна клавиша, без исполняемого синтаксиса.
Обычные действия SAFE; распознанные отправка, публикация, оплата и удаление CONFIRM.

AX/CGWindow используют логические точки. Рамки на JPEG масштабируются относительно
реальной пиксельной ширины/высоты захвата и границ окна, включая Retina; размеры
изображения и payload ограничены. Secure-поля закрашиваются до выдачи пикселей;
неполное/изменившееся дерево запрещает захват. `scope=screen` на macOS пока
отклоняется: полнота редактирования чужих окон не доказана. Используйте
`scope=active_window`. Отдельных инструментов для конкретных приложений нет.

HUD 360×120 и Ghost Cursor используют логические координаты и все macOS Spaces,
включая fullscreen. Оверлей пропускает клики и не выдаёт approvals.

Chromium лениво включает дерево страницы. Для известного executable и только при
выданном Accessibility helper запрашивает AXRole/AXEnhancedUserInterface и ждёт
реальный AXWebArea; ответ setter не доказывает готовность. Никакие системные
разрешения или VoiceOver-настройки не меняются. Это поведение описано в
[исходниках Chromium](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/chrome_browser_application_mac.mm).
Headed-контексты на Mac используют реальный viewport окна, а не эмуляцию его размера.

## Файлы и браузер

Сохранены существующие `files.open`/`files.recycle` и versioned
`fs.search`/`fs.read_document`/`fs.write_file`; новых псевдонимов
`fs.open_in_os`/`fs.delete_to_trash` нет. Начальные папки macOS — собственные
Desktop/Documents/Downloads. Явный allowlist остаётся обязательным. `/System`,
`/Library/Keychains`, `.ssh`, `.env` и `.env.*` запрещены обеими политиками, в том
числе через symlink. Исполняемые Mac-документы не открываются как обычные файлы.
Корзина — exclusive atomic move в `~/.Trash`, без замены старого содержимого.
Другой файловый том отклоняется вместо copy/delete. Рабочий браузер на Mac ищет
Chrome, Edge и Brave; прежняя проверка владельца CDP listener/PID сохраняется.

## Восстановление установленного приложения — 04.11.2026

Проверка начата с `main` `c007a55`. Установленный `~/Applications/Jarvis.app`
указывал на отдельную копию от 1 ноября, а editable installation в `.venv`
импортировал другую старую копию. Сборка `.app` и editable installation обновлены
из текущего checkout; manifest установленного UI совпал с поставляемым manifest.
`npm --prefix desktop run build` также обновил локальный `desktop/dist`.
Старая сгенерированная `desktop/release/Jarvis.app` с тем же bundle ID перенесена
в резервный каталог кеша с расширением `.disabled`, чтобы не выбирать её вместо
установленного приложения. Её содержимое сохранено.
Нумерованные сохранённые ссылки `.venv 3` и `desktop/node_modules 3` сохранены
и исключены из Git/sdist: упаковка не должна обходить их внешние зависимости.

В синхронизируемом `src` обнаружено 111 выгруженных iCloud файлов `.pyc`.
Их каталоги перенесены в резервную папку в `~/Library/Caches/JarvisDev`, без
удаления исходников. В локальном venv добавлен `jarvis_local_bytecode_cache.pth`,
задающий `sys.pycache_prefix` в `~/Library/Caches/JarvisDesktop/bytecode`, включая
изолированные `-I` helpers. Это локальная настройка окружения, не часть wheel;
после замены venv её нужно проверить заново. Импорт текущего DesktopService
после ремонта занял менее двух секунд вместо зависания на старом bytecode.

Живая проверка обнаружила и исправила две ошибки:

- TextEdit: служебное CG-окно sharing/titlebar больше не выбирается вместо
  настоящего AX-окна документа. При известном `AXDescription` failure текстовая
  область определяется по доступному AXIdentifier; ошибки обязательных атрибутов
  остаются отказом. Единственный Document выбирается отдельно от поля размера
  шрифта; два документа не дают автоматического поля ввода.
- Electron Wake Word: явный opt-in из настроек передаётся в приватном запросе
  прогретому AudioHost, а не зависит только от environment при его запуске.
  Частичные гипотезы не активируют запись: используется существующая проверка
  законченного сегмента и уверенности самого слова. Выключение, Stop и отмена
  сохраняют прежние ограничения.

На этом Mac через установленный Electron подтверждены запуск Calculator,
ввод в новый несохранённый TextEdit с Ghost preview, рабочий Chrome/CDP,
настоящие результаты DuckDuckGo через `browser.search` + `browser.read`, HUD,
сохранение профиля из 14 приложений и восстановление браузера после перезапуска.
Отдельный native probe завершил все 10 проверок; clipboard восстановлен со всеми
форматами. После явного включения Wake Word проверены настоящий микрофон,
активация записи, автостоп по тишине и появление распознанного текста в UI.
После проверки ожидание Wake Word выключено; автостоп остаётся настроенным.
Аудио и транскрипт в отчёт не включены. OpenAI не вызывался по выбору владельца;
`gpt-4o` сохранён в настройках, активный провайдер остаётся offline до ввода ключа.

Целевые прогоны: 153 passed для native/launcher/planner и 163 passed для
voice/VAD/DesktopService. Полный pytest локально не запускался. Это приёмка
указанных Mac-сценариев; живые Windows, внешние аккаунты и полный продуктовый
сценарий остаются отдельными проверками.

## Проверка

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pip check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
.venv/bin/python scripts/macos/smoke_native.py --report /tmp/jarvis-macos-smoke.json
```

Живой probe не вызывает модель и не входит в аккаунты. Он проходит через
PermissionEngine, временно проверяет текстовый буфер и восстанавливает все его
форматы, если владелец не изменил буфер, считает реальные окна и снимает только
собственное временное тестовое окно. В нём проверяются фокус, AX-поле, буквальный
ввод с независимым readback, Command+A и semantic click. Без Accessibility проверяется
реальный отказ; exit code 2 означает, что разрешение не выдано, 1 — неполную приёмку.
Для сохранения локального изображения добавьте `--image /tmp/jarvis-macos-smoke.jpg`.
Изображения/clipboard/task text в отчёт и Git не входят. Fixture/CI не заменяют
живую приёмку macOS, Windows, голоса или сервисов.

Источники API: [PyObjC](https://pyobjc.readthedocs.io/en/latest/),
[Apple AXUIElement](https://developer.apple.com/documentation/applicationservices/axuielement_h),
[Apple Accessibility permissions](https://support.apple.com/guide/mac-help/allow-accessibility-apps-to-access-your-mac-mh43185/mac).

## Фактическая проверка 30 октября 2026

MacBook, macOS 27.2, Apple Silicon arm64, Python 3.12.7:

- Полный pytest: **2116 passed, 24 skipped**. Пропущены явные live Windows/model/voice
  сценарии и updater fixtures, для которых на этом Mac нет PowerShell.
- Mypy: **366 файлов без ошибок**, отдельно обычная macOS-конфигурация и
  `--platform win32`. Ruff check и format-check прошли; `pip check` — без конфликтов.
- Production Electron build и **23 Node tests** прошли.
- `.app` создана и проверена codesign. Реальный packaged E2E подтвердил
  `app.isPackaged=true`, запуск установленного Chrome с отдельным профилем/CDP,
  сохранение модели/голосовых настроек и повторное подключение к тому же browser PID
  после перезапуска. Микрофон, аккаунты и модель в этом прогоне не использовались.
- Живой native probe через PermissionEngine: **все 10 проверок SUCCESS**,
  включая точный ввод, клавишу, клик и JPEG с 5 метками; изображение **1280×776**.
  Содержимое clipboard восстановлено со всеми форматами. До выдачи доступа
  отдельно наблюдался корректный `macos_accessibility_required`.
- Дополнительно подтверждён реальный запуск Calculator по имени и CG identity.
  В отдельном Chrome for Testing с локальной страницей прошли native viewport,
  inspect, снимок с **16 метками** и `browser.type` через PermissionEngine с
  настоящей геометрией Ghost. Native/CSS viewport совпали: **1200×731** логических точек.
- `python -m jarvis --smoke-test` — success в симуляции. `python -m build` собрал
  sdist и wheel. Проверен состав: macOS workers и prebuilt UI присутствуют,
  `.venv`, node_modules и локальные инструменты исключены. Для sdist включён
  `skip-excluded-dirs`, чтобы Hatchling не обходил сохранённые окружения iCloud.

[Санитизированный native-отчёт](macos-native-acceptance-20261030.json) ·
[Разметка тестового окна](images/macos-native-fixture-20261030.jpg).

Проверки CI на Windows и macOS запускаются в PR #71. Живые Windows-действия,
голосовое оборудование, внешние аккаунты и полный продуктовый сценарий этим
изменением не объявляются принятыми. HumanTakeover hardware hooks остаются
Windows-реализацией; наличие Mac Ghost overlay не доказывает приёмку этого monitor.
