# Jarvis на macOS

Общий Python-движок и Electron работают на Windows 11 и macOS. Имена инструментов
`windows.*` сохранены для совместимости: `ProcessBackend` сам выбирает доверенный
`windows.worker` или `macos.worker`. Модель не выбирает платформу, модуль или команду.
Схемы `WindowTarget`, `EditorTarget`, `ElementTarget`, `NativeRequest`, `WindowsResult`
общие; PermissionEngine, аудит, симуляция и точные подтверждения не обходятся.

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

Поддерживаются список видимых окон, запуск установленных `.app`, фокус точного окна,
наблюдение и ввод в AX-поле, инспекция, нажатие наблюдённого элемента, ограниченные
клавиши, текстовый буфер и снимок активного окна с Set-of-Marks.
`notepad`/«блокнот» открывает TextEdit; доступны Notes, Calculator, Chrome, Safari,
Finder, Terminal, Code, Cursor и другие приложения из системного каталога.
Имя разрешается до bundle/executable: строка задачи не становится путём, кодом или
аргументами запуска. Terminal запускается без команды и аргументов.

Основной адаптер использует PyObjC Cocoa/Quartz/ApplicationServices, загружаемые
только в одноразовом helper. Без них запуск/Chrome/буфер используют фиксированные
`open`, `pbcopy`, `pbpaste`, а список окон — фиксированный JXA/CoreGraphics script.
AX-действия требуют установленных нативных зависимостей; отсутствующая библиотека
возвращает диагностическую причину, а не имитирует успех.

В «Системные настройки → Конфиденциальность и безопасность» пользователь разрешает:

- **Универсальный доступ / Accessibility** — наблюдение и управление элементами.
- **Запись экрана** — снимок окна; Accessibility также нужен для удаления паролей.
- **Микрофон** — только если нужен голос, включаемый видимым жестом пользователя.

При отсутствии доступа helper возвращает `control_unsupported` с конечной причиной
`macos_accessibility_required` или `macos_screen_recording_required` и открывает
фиксированный раздел настроек. Разрешение выдаёт только пользователь; запуск из
Codex/Terminal и запуск `.app` могут требовать отдельного разрешения процесса запуска.

Окна связаны с CGWindowNumber, PID, временем старта процесса и executable.
AX-поля повторно находятся по роли, идентификатору, имени и выбранным вкладкам;
неоднозначное/изменившееся поле отклоняется. Ввод направлен в AXValue конкретного
поля, не использует глобальные клавиши или clipboard и проверяется чтением SHA-256.
Пароли и read-only поля не становятся целями. Клавиши Ctrl+C/V/A/Z переводятся
в Command+C/V/A/Z; Enter/Tab сохраняются, Alt+F4 закрывает выбранное окно через
Command+W. Ctrl+Z — совместимое расширение списка клавиш, требует CONFIRM, как
Enter/закрытие. Вставка проверяет наблюдённый SHA-256 plain-text буфера.

AX/CGWindow используют логические точки. Рамки на JPEG масштабируются относительно
реальной пиксельной ширины/высоты захвата и границ окна, включая Retina; размеры
изображения и payload ограничены. Secure-поля закрашиваются до выдачи пикселей;
неполное/изменившееся дерево запрещает захват. `scope=screen` на macOS пока
отклоняется: полнота редактирования чужих окон не доказана. Используйте
`scope=active_window`. Специальные `windows.calculator_*` контракты Microsoft
Calculator пока отказывают на Mac; обычный Calculator доступен через AX-инструменты.

HUD 360×120 и Ghost Cursor используют логические координаты и все macOS Spaces,
включая fullscreen. Оверлей пропускает клики и не выдаёт approvals.

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
собственное временное тестовое окно. Без Accessibility проверяется реальный отказ.
Для сохранения локального изображения добавьте `--image /tmp/jarvis-macos-smoke.jpg`.
Изображения/clipboard/task text в отчёт и Git не входят. Fixture/CI не заменяют
живую приёмку macOS, Windows, голоса или сервисов.

Источники API: [PyObjC](https://pyobjc.readthedocs.io/en/latest/),
[Apple AXUIElement](https://developer.apple.com/documentation/applicationservices/axuielement_h),
[Apple Accessibility permissions](https://support.apple.com/guide/mac-help/allow-accessibility-apps-to-access-your-mac-mh43185/mac).

Фактические результаты текущего прогона будут записаны после завершения проверок.
