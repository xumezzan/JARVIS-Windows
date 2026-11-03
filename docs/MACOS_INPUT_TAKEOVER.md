# Пауза при вмешательстве пользователя на Mac

Планировщик включает нативный монитор только на время задачи в режиме выполнения.
Нажатие мыши, прокрутка, клавиатура или движение физического указателя рядом с
наблюдённой целью ставят Jarvis на паузу. После двух секунд покоя планировщик
отменяет старый подготовленный шаг, заново читает текущую цель через PermissionEngine
и продолжает с новым наблюдением. Старая цель и старое подтверждение не переносятся.
Симуляция монитор не запускает; завершение и отмена задачи закрывают его.

Это отдельная доработка общего HumanTakeover из Ghost Cursor. Нативные Mac-инструменты
и launcher остаются в PR #71; этот PR основан непосредственно на `main` и не включает
изменения соседних PR #71–73. До их интеграции обычные `windows.*` в `main` сохраняют
прежние платформенные ограничения.

## Как работает монитор

`platforms/macos/input.py` использует пассивный `CGEventTap` текущей пользовательской
сессии. Он не изменяет, не блокирует и не генерирует ввод. События с ненулевым PID
программного источника не считаются физическим вмешательством. Это существенно:
живой эксперимент показал, что даже HID-счётчики `CGEventSourceCounterForEventType`
увеличиваются при `CGEventPost` и не подходят для различения собственных нажатий Jarvis.

В памяти остаются только счётчики, текущее удержание кнопок/клавиш и модификаторов.
Символы клавиш, набранный текст, события и история ввода не сохраняются и не отправляются.
Координаты читаются в логических точках Quartz; активное окно определяется по PID и
CGWindowNumber без использования заголовков или содержимого окон. Подтверждение в
диалоге временно приостанавливает детектор, сохраняя прежнюю логику защиты от повторной
паузы из-за ещё удерживаемой кнопки подтверждения.

Нужен ранее выданный системный доступ **Конфиденциальность и безопасность → Мониторинг
ввода**. Проверка `CGPreflightListenEventAccess` не запрашивает и не меняет разрешение.
Отсутствующий доступ, зависимость, отказ создания tap или отключение tap останавливают
задачу с `input_monitor_unavailable`. Ошибка содержит фиксированный текст; нативная
диагностика и данные пользователя не попадают в результат. Запись экрана и микрофон
для самого монитора не нужны.

## Проверка

Обычные pytest используют тихий fixture poller, не устанавливают глобальные hooks и
не требуют системного доступа. Сам Mac-адаптер тестируется с fake Quartz: пассивный
режим, программный и физический ввод, быстрые нажатия, модификаторы, координаты,
отказы и освобождение ресурсов. Opt-in Windows acceptance сохраняет настоящий poller.
Общие тесты проверяют отмену старого действия и обновление browser/desktop observations.

```sh
python -m pip install -e '.[dev]'
python -m pytest tests/unit/test_macos_input.py tests/unit/test_ghost_cursor.py tests/unit/test_planner.py tests/unit/test_report.py
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m mypy --platform win32
python -m pip check
QT_QPA_PLATFORM=offscreen python -m jarvis --smoke-test
python -m build
```

Mypy запускается с конфигурацией хоста и отдельно с `--platform win32`. В Windows
FFI динамические `WinDLL`/`WINFUNCTYPE` получают локальный `cast(Any, ctypes)`, поскольку
macOS stubs не предоставляют эти Windows-only имена. Поведение вызовов не меняется.
В тесте отказа tree cleanup эмулируется только NTFS short-path adapter; настоящий
дочерний процесс, timeout, подавление его вывода и проверка завершения сохраняются.

Для отдельной живой проверки с доступом Input Monitoring:

```sh
python scripts/macos/check_takeover.py --seconds 15
```

За это время переместите указатель рядом с исходной позицией или один раз нажмите и
отпустите кнопку мыши/клавишу, затем оставьте ввод в покое не менее двух секунд.
Скрипт сам ничего не нажимает, не читает текст и не открывает документы. JSON содержит
только счётчики и статусы: `passed` требует наблюдавшихся паузы и покоя;
`requires_physical_input` означает отсутствие полного живого сценария, а не успех.
Exit code 2 означает неполную проверку или недоступный монитор.

Для Windows regression после установки `.[dev]` и Chromium:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pip check
python -m jarvis --smoke-test
python -m build
```

## Состояние на 3 ноября 2026

- Работа ведётся в `fix/macos-human-takeover` от `main` 5792446.
- Финальный локальный pytest: **2019 passed, 24 skipped**. Пропущены opt-in Windows,
  model/voice и updater fixtures без PowerShell. Последний focused прогон: **117 passed**.
- Оба mypy (host и win32), Ruff, format-check, `pip check`, Qt simulated smoke,
  sdist/wheel build, production Electron build и **22 Node tests** прошли.
- Реальный Electron E2E с отдельным venv подтвердил `system.info` SUCCESS на Darwin,
  симуляцию, отмену, renderer isolation, отсутствие записи микрофона от synthetic click,
  навигацию, responsive layout и завершение дочернего runtime.
- На этом Mac нативный tap действительно создаётся, читает состояние и закрывает поток.
- С отдельным нативным probe из checkout PR #71 повторно прошли все 10 операций через
  PermissionEngine, с восстановлением всех форматов clipboard. Наблюдавшиеся реальные
  CGEventPost KeyDown/KeyUp имели программный источник и не увеличили физические счётчики.
- Fixture/CI не подтверждают аппаратную приёмку мыши/клавиатуры или полный продуктовый
  сценарий. Голос, аккаунты и живой Windows этим изменением не объявляются проверенными.
- Отдельный 15-секундный observer запустил настоящий tap, но не увидел физических
  событий; он честно вернул `requires_physical_input`, exit 2. Следующий ручной шаг —
  повторить этот observer с настоящим нажатием и двумя секундами покоя.
- В PR запускаются одинаковые Python gates и Node overlay contracts на Windows/macOS;
  hosted результаты проверяются отдельно перед завершением работы над изменением.
- Проверки из синхронизируемого Documents могут зависнуть на выгруженных iCloud `.pyc`
  и SQLite-кешах. Финальные прогоны выполняются из отдельного checkout в Application
  Support и отдельного venv; рабочая копия установленного Jarvis не используется.

Проверить, откуда конкретное окружение действительно импортирует движок:

```sh
python -I -c 'import jarvis; print(jarvis.__file__)'
```

Это важно при нескольких checkout и общих `.venv`: имя каталога запуска не доказывает,
что editable installation указывает на тот же исходный код.

Источники API: [Apple CGEvent tapCreate](https://developer.apple.com/documentation/coregraphics/cgevent/tapcreate(tap:place:options:eventsofinterest:callback:userinfo:)),
[Apple CGEventSource](https://developer.apple.com/documentation/coregraphics/cgeventsource),
[Apple CGPreflightListenEventAccess](https://developer.apple.com/documentation/coregraphics/cgpreflightlisteneventaccess()).
