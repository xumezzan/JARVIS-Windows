# Новое приложение Jarvis Desktop

15 октября 2026: по запросу пользователя убраны заглушки, прежнее `design-preview/` заменено приложением `desktop/`. Сохранены визуальные токены и отдельная процедурная сфера; состояние и содержимое экрана переписаны. Подставных встреч, профиля, подключений, шагов и успешного таймера больше нет.

## Архитектура

Electron управляет frameless окном1536×1024. React получает только фактическое состояние от дочернего Python-процесса. Связь — ограниченный JSON-протокол в приватных stdin/stdout pipes, без HTTP-сервера и без произвольного shell. Среда Python очищается от API keys, прокси и произвольной конфигурации pip. Типизированные операции status/run/cancel/configure/respond/voice/memory/mail/eleven ограничены схемами; сохранены UUID запросов и лимиты размеров/времени. Произвольных вызовов инструментов в протоколе нет.

`src/jarvis/desktop/service.py` — чистый asyncio-сервис с Runner и PermissionEngine, без Qt и скрытого PlannerWindow. Наследуемая Qt-оболочка остаётся отдельной точкой входа. `system.info` читает реальные ограниченные сведения ОС/диска и проверяет результат. Офлайн-рецепты остаются конечными; неизвестная команда вызывает уточнение.

История задач хранится только в RAM, максимум20 строк; счётчики начатых и завершённых задач независимы от этого лимита. Публикуемые наблюдения ограничены по длине. Симуляция проходит политику без вызова настоящего адаптера. Renderer изолирован contextIsolation/sandbox, Node и внешняя навигация отключены, IPC проверяет отправителя, основной frame, URL, аргументы.

Подписи инструментов синхронизированы с registry 24 октября 2026 в
`desktop/src/runtime.ts:toolLabel()`: все 26 зарегистрированных инструментов имеют
русские названия для хода работы, результатов и каталога. Добавлены screenshot,
clipboard, hotkeys, window inspection/clicks и `memory.scratchpad_write`;
исправлены реальные имена `outlook.list`, `outlook.read`, `outlook.local_draft`.
Старые frontend-имена Outlook и `scratchpad.set` имеют совместимые подписи, но не
добавляют aliases в backend registry. Неизвестное имя показывается без изменения.
`windows.type_text` подписан «Ввод текста в окно», без ограничения Блокнотом.

Точное подтверждение показано в React: доверенный жест в изолированном preload передаёт ID ожидающего prompt, Python проверяет снимок, срок и одноразовость, записывает аудит и только затем выдаёт capability. Облачный провайдер требует отдельной видимой галочки согласия на одну задачу. Запись микрофона также связана с доверенным удержанием в preload; OAuth открывается системным браузером. Закрытие Electron завершает принадлежащий ему процесс, задачи и helpers.

## Запуск и границы

См. [desktop/README.md](../desktop/README.md). `npm --prefix desktop start` собирает renderer, готовит изолированное Python-окружение и открывает приложение. Прежний `python -m jarvis` сохранён как совместимая оболочка. Существующий Windows-установщик пока поставляет именно её; Electron-установка и native Windows acceptance остаются отдельной непроверенной работой. Эта задача не изменяет milestones10–32.

Универсальное понимание запросов требует настройки модели. Только существующий Outlook поддерживается как почтовый сервис. Голосу нужны локальная модель и пользовательский жест записи. Живые модель, OAuth/почта, микрофон/звук и Windows этой задачей не проверялись.

## Фактическая проверка

Playwright запускает настоящий Electron и Python с временными данными, проверяет чистый старт, выполнение `проверь систему` с реальными сведениями Mac, симуляцию без наблюдений, настоящее уточнение/отмену, каталог и навигацию, голосовые настройки без записи, изоляцию renderer, custom window controls и завершение процесса. Снимки сделаны на1536×1024,1280×900 и390×844. Результаты: [runtime-verification.json](../desktop/qa/runtime-verification.json).

[Чистый старт](../desktop/qa/desktop-clean.png) · [Результат команды](../desktop/qa/desktop-system-result.png) · [Компактный](../desktop/qa/compact-runtime.png) · [Мобильный](../desktop/qa/mobile-runtime.png).

Исторические проверки на Mac (15 октября 2026; актуальная проверка — [VOICE_RUNTIME.md](VOICE_RUNTIME.md)):

- `python -m pytest`: **585 passed, 5 skipped**. Пропущены только явно отключённые живые model/voice/Windows acceptance.
- `python -m ruff check .`, `python -m ruff format --check .`: успешно, проверка формата пройдена.
- `python -m mypy`: успешно, 125 исходных файлов.
- `QT_QPA_PLATFORM=offscreen python -m jarvis --smoke-test`: `Jarvis GUI smoke: success`.
- `python -m build`: sdist и wheel собраны.
- `npm --prefix desktop run build`: TypeScript и production Vite build прошли.
- `npm --prefix desktop run setup`: автоматически подготовлено собственное изолированное окружение; проверка импорта прошла.
- `node desktop/scripts/verify-desktop.cjs`: настоящий установленный движок; все проверки пройдены, ошибок renderer нет. Начало и конец команды4000 символов доступны на всех трёх ширинах; примечание сеанса находится внутри карточки.
- `npm --prefix desktop start`: приложение открыто, нативная проверка окна показала подключённый движок, нулевые счётчики, пустую историю и выключенный микрофон.

Полные Python-проверки выполнялись в отдельном `/tmp/jarvis-reference-venv-20261015`, установленном через `python -m pip install -e ".[dev]"`. Платформенная Windows- и живая модель/почта/аудио-приёмка не подменяются этими результатами.

Изменения и фактические проверки от 24 октября 2026: [VOICE_RUNTIME.md](VOICE_RUNTIME.md).

Проверка подписей 24 октября 2026: `npm --prefix desktop run build` прошёл
(TypeScript и Vite). Динамическая сверка Python registry с импортированным
`toolLabel()` покрыла все 26 инструментов, четыре совместимых имени и fallback
неизвестного инструмента. Impeccable detector для `desktop/src/runtime.ts` вернул
пустой список; `git diff --check` прошёл. Настоящий Electron с временным data directory
и Python runtime показал все 26 подписей в каталоге на 1536×1024 и 1280×1024:
переполнения строк и ошибок renderer нет. Начальная UI-проверка читала список до
React render; после ожидания появления списка проверка прошла. Windows-инструменты
и почта при этой проверке не исполнялись; нативный Windows UI не проверялся.

## HUD и подсветка цели (24 октября 2026)

При реальной задаче Runner (`working`, `execute`) Electron скрывает workbench и показывает
отдельное окно 320×110 в правом нижнем углу рабочего монитора. `CompactHud` использует
ту же процедурную `JarvisOrb`, текущий инструмент и фактический номер шага. Окно прозрачное,
frameless, `screen-saver` always-on-top, `focusable: false`, `skipTaskbar: true`;
`setIgnoreMouseEvents(true, {forward: true})` пропускает клики. Основное окно сохраняет
размер, maximized-состояние и React-состояние. Симуляция остаётся в workbench.

`Ctrl+Alt+J` возвращает workbench до конца текущей задачи; `Ctrl+Alt+Esc` отменяет задачу
и возвращает окно (на Mac используется Cmd вместо Ctrl). Если OS не зарегистрировала оба
сочетания, workbench не скрывается. Запрос approval/clarification автоматически возвращает
интерактивное окно; завершение/ошибка/отмена/разрыв runtime убирают HUD. После обычного
завершения окно возвращается без перехвата фокуса у целевого приложения. Запись по удержанию
по-прежнему требует нативного фокуса; переход к HUD не должен отменять уже начатый TTS.

Нативный click-helper после проверки WindowTarget/ElementTarget отправляет только Rectangle
по приватному pipe и ждёт receipt. Electron переводит физические Windows-координаты в DIP,
показывает отдельную click-through рамку, ждёт два animation frame и 300 мс, затем отвечает
Python. После receipt helper повторно проверяет идентичность/геометрию/foreground/occlusion
и только затем делает один клик. Для координатного fallback рамка 24×24 помечает точку;
границы произвольного элемента из изображения не выдумываются. Таймаут, отмена или изменение
цели прерывают клик. Receipt не является approval, не доступен основному renderer и не
меняет PermissionEngine. В legacy Qt callback отсутствует, прежний протокол сохранён.

У браузерных click-операций видимой BrowserSession рамка рисуется в viewport самой страницы
на 300 мс с `pointer-events: none`, затем удаляется и цель перепроверяется. Это исключает
догадки о высоте Chrome toolbar и масштабе дисплея. В headless/симуляции визуальных эффектов
нет. DOM-marker не содержит текста, полей формы или обработчиков и удаляется при отмене.

Проверки:

```sh
npm --prefix desktop run qa:electron
# POSIX fixture QA; без реального микрофона, аккаунтов и Windows-кликов:
JARVIS_PYTHON=/path/to/test-venv/bin/python npm --prefix desktop run qa:overlay
JARVIS_PYTHON=/path/to/test-venv/bin/python npm --prefix desktop run qa:voice
python -m pytest tests/unit/test_target_highlight.py tests/integration/test_browser_highlight.py
```

Результаты текущей Mac-проверки: `desktop/qa/overlay-verification.json`,
`desktop/qa/overlay-hud.png`; настоящий Electron и fixture-адаптер проверяют paint receipt,
отмену, возврат approval и размеров, read-only sandboxed HUD. Это не Windows acceptance.
Голосовой Electron QA в этой среде останавливается на нативном `isFocused() == false`;
проверка не ослаблена, аппаратный голос не проверен. Старый `.venv-reference` имеет
неработающие Qt plugins/hidden editable `.pth`; Python-проверки выполнялись в новом venv
`/tmp/jarvis-hud-qa-20261024`, установленном через `pip install -e '.[dev,voice]'`.

На Windows 11 нужно отдельно запустить `npm --prefix desktop run qa:electron`,
`python -m pytest tests/unit/test_target_highlight.py`, затем реальную задачу с
`windows.inspect_active_window` → `windows.click_element` и нормальным exact approval,
если его требует политика. Проверить: UIA Invoke и coordinate fallback, клик сквозь HUD,
обычное/maximized окно, Stop/restore hotkeys, два монитора с 100/150/200% DPI и отключение
монитора, изменение цели за 300 мс. UAC secure desktop/elevated applications и Wayland
не подтверждены; не повышать привилегии ради оверлея. Electron API:
https://www.electronjs.org/docs/latest/api/browser-window и
https://www.electronjs.org/docs/latest/api/screen.

## Фоновые подсказки (25 октября 2026)

`DesktopService` владеет opt-in `EventWatcher`: локальный буфер раз в 2 секунды,
важные входящие Outlook раз в 5 минут, тихий corner HUD и отдельно включаемый
системный голос. Текст разбирается после проверки команды пользователем;
почтовый шаблон создаётся через `outlook.local_draft` после нажатия на точный
адрес. Настройки сеансовые, по умолчанию выключены. Сценарии, ограничения,
проверки и Windows-приёмка — [EVENT_WATCHERS.md](EVENT_WATCHERS.md).
