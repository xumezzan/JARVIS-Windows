# Проверки этапов 1–6

## Windows 11 / PowerShell

Из корня рабочей копии с Python 3.12 x64:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/integration/test_shell.py tests/unit/test_config.py tests/unit/test_shell_log.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m jarvis
.\.venv\Scripts\jarvis.exe --version
.\.venv\Scripts\python.exe -m build
```

Тесты Qt по умолчанию используют offscreen, а `python -m jarvis` открывает настоящее
окно. Чтобы проверить GUI-тесты с native Windows platform plugin:

```powershell
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_shell.py
Remove-Item Env:QT_QPA_PLATFORM
```

`--smoke-test` открывает окно, отправляет офлайн-команду «проверь систему» в режиме
симуляции, ждёт планировщик, закрывает окно и возвращает 0 только при статусе
`finished`/`simulated`. Ошибка/отмена/timeout дают 1; невалидная конфигурация или
недоступный каталог логов при старте дают 2. Симуляция не вызывает адаптеры, поэтому
smoke-прогон не открывает приложений и не выходит в сеть.

## Ручная проверка окна

1. Запустить без `--smoke-test`. Проверить idle, доступный ввод и отсутствие записи до удержания кнопки в планировщике; permissions открывает тестовое окно.
2. Выбрать «Только симуляция», ввести «проверь систему дважды», нажать стрелку или Ctrl+Enter.
3. Убедиться, что принятый текст отображается буквально, UI остаётся отзывчивым,
   а thinking → executing → success отражают фактические шаги планировщика.
4. Во время следующего запуска нажать Stop/Escape. Получить cancelled, без позднего success.
5. Выбрать «Выполнять действия», ввести «открой блокнот» и убедиться, что Блокнот
   действительно открылся, а в «Текущей задаче» указан `windows.open_app: SUCCESS`.
6. Ввести незнакомую фразу и убедиться, что уточняющий вопрос появляется в главном окне;
   ответ «проверь систему» продолжает задачу.
7. Снять «Автономно», выполнить «открой блокнот и напиши «Привет»» при пустом Блокноте
   и убедиться, что перед вводом текста показан диалог точного подтверждения.
8. Удержать круглую кнопку микрофона, произнести «открой калькулятор», отпустить:
   распознанный текст появляется в строке команды, задача запускается без второго действия,
   итог проговаривается. Проверить, что запись идёт только пока кнопка удерживается.
9. Произнести название несуществующего приложения и убедиться, что ответ —
   `application_missing`, а не запуск чего-то другого.
10. Включить «Свободные руки». Убедиться, что подпись показывает «● Слушаю», обычная
    речь в комнате пропускается, а «Джарвис, проверь систему» запускает задачу. Выключить
    переключатель и убедиться, что запись прекратилась, а не просто скрылась.
11. Отключить микрофон в системе при включённых свободных руках: режим должен выключиться
    сам и показать причину, а не пытаться слушать в цикле.
12. В окне инструментов: `windows.get_open_windows` → выбрать окно → `windows.get_text_fields`
    → `windows.type_text`. Убедиться, что в непустое поле без запроса замены ввод отклоняется
    (`target_changed`), а с заменой поле содержит ровно переданный текст.
13. Открыть окно с полем пароля и убедиться, что оно не появляется в списке полей.
14. Сказать «найди файл отчёт» и убедиться, что показаны только файлы из разрешённых папок.
15. Попросить удалить созданный вами тестовый файл и убедиться, что он оказался **в корзине**,
    а не исчез: откройте корзину и проверьте наличие.
16. Попросить открыть или создать файл вне разрешённых папок и убедиться, что действие
    отклонено на подготовке, без обращения к диску.
6. Закрыть окно во время работы. Процесс должен завершиться без оставшегося QThread.
7. Проверить журнал: служебные JSONL events без текста команды.
8. Изменить масштаб Windows (100%/150%/200%) и проверить читаемость окна/кнопок.

Границы задачи задаёт планировщик (`Limits`), а не переменная окружения:
не более 8 шагов, 3 уточнений, 30 с на ответ провайдера и 180 с на задачу.
Превышение даёт статус `limit` или `timeout` и ненулевой код возврата smoke-прогона.

## macOS/Linux

Interpreter — `.venv/bin/python`, console script — `.venv/bin/jarvis`.
Команды тестов совпадают. Native Qt plugin macOS — `cocoa`:

```bash
QT_QPA_PLATFORM=cocoa .venv/bin/python -m pytest tests/integration/test_shell.py
.venv/bin/python -m jarvis --smoke-test
```

Успех на macOS не считается проверкой Windows. Не добавлять `src` в PYTHONPATH ради
прохождения тестов: сначала установить пакет.

## Покрытие

- Unit: CLI help/version/rejection, конфигурация и пределы, JSONL schema и sessions.
- Integration: installed entrypoint в новом interpreter из временного каталога;
  Qt startup, text submission, responsiveness, success, failure/retry, cancel во время
  thinking/executing, timeout, close/quit cleanup, duplicate submission, input limits,
  literal transcript, отсутствие raw input в журнале, bounded activity.
- `pytest-qt` запускает настоящий Qt event loop и реальные QThread workers. Browser tests
  используют локальный HTTP fixture и настоящий Chromium; Windows UI tests — portable probe.
- `tests/e2e` пока не содержит полного Windows MVP сценария.

Native Windows tests требуют marker `windows` и `--run-windows`; live model — marker `model`
и `--run-model`. `tests/conftest.py` применяет пропуски до запуска тестов.
Никаких внешних записей или запросов OpenAI по умолчанию.

## Сборка

`python -m build` собирает wheel из sdist. Для проверки в чистом Windows venv:

```powershell
py -3.12 -m venv "$env:TEMP\jarvis-wheel-check"
& "$env:TEMP\jarvis-wheel-check\Scripts\python.exe" -m pip install --force-reinstall .\dist\jarvis_windows-0.0.1-py3-none-any.whl
& "$env:TEMP\jarvis-wheel-check\Scripts\python.exe" -I -m jarvis --smoke-test
```

Wheel требует PySide6-Essentials; это ещё не Windows installer/PyInstaller release.

## Будущая приёмка MVP

Chrome search и title → Notepad UIA ввод/чтение без потери данных → follow-up context →
русский push-to-talk → точный preview внешнего действия → запрет без approval →
однократная отправка → stop/timeout/network loss/missing application/prompt injection →
SIMULATED без real effects → factual audit → чистая Windows установка.

Полный сценарий MVP ещё не реализован. Не считать hosted CI или offscreen shell tests
доказательством desktop E2E.

## Troubleshooting

- `py` не найден: установить Python 3.12 x64 с launcher или использовать полный путь к interpreter.
- `No module named jarvis/PySide6`: выполнить `pip install -e ".[dev]"` тем же interpreter.
- Ошибка local log: проверить доступность `JARVIS_DATA_DIR`; raw exception и пути не выводятся.
- Окно не видно: убрать `QT_QPA_PLATFORM=offscreen` из окружения интерактивного запуска.
- На macOS Qt не находит плагины, хотя они установлены: проверить `ls -lO` в
  `.venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforms`. В текущем локальном
  окружении флаг `hidden` повторно применялся к Qt plugins и editable `.pth`. Разовое
  снятие флага не удерживалось. Рабочий venv перенесён за пределы workspace в пользовательский
  cache с сохранением `.venv` как symlink; его абсолютные entry points продолжают работать.
  Для нового окружения при таком симптоме создавайте venv вне workspace. Это локальная
  macOS-проблема, не Windows workaround и не изменение приложения.
- Текстовая команда не открыла приложение: проверьте, что выбран режим «Выполнять действия»
  (а не «Только симуляция») и что формулировка входит в офлайн-набор; произвольные команды
  требуют провайдера OpenAI с настроенными ключом и моделью.
- Зависимость перестала поддерживать окружение: свериться с официальными docs, подобрать
  совместимую версию и проверить Windows перед фиксацией.

## Этап 2: инструменты и подтверждения

Полный набор после этапа 2: 91 тест. Для новых focused tests на Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_permissions.py tests/integration/test_permission_ui.py
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_permission_ui.py tests/integration/test_shell.py
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe -m jarvis
```

Ручная приёмка:

1. Открыть «Проверить разрешения». SAFE в Simulation Mode даёт SIMULATED, local outbox = 0.
2. Выбрать CONFIRM и снять Simulation Mode. Подготовить тестовое сообщение.
3. В preview проверить tool, risk, mode, service, action_type, account, recipient, subject,
   body и attachments. До нажатия Approve счётчик равен 0.
4. Отменить preview: действие не запускается. Подготовить заново и подтвердить: получить
   SUCCESS и счётчик 1, без внешней отправки.
5. Включить simulation и подтвердить новое сообщение: получить SIMULATED, счётчик остаётся 1.
6. Оставить preview открытым более 60 секунд: Approve не должен выдать разрешение.
7. CRITICAL/BLOCKED дают DENIED, без диалога и adapter calls.
8. Stop/Escape/закрытие отменяют ожидание и worker; audit показывает факты без payload/token.
9. Проверить повторное открытие окна: audit сохранён, локальный тестовый ящик пуст.

Тесты ядра проверяют подмену каждого поля сообщения, schema extras/coercion, frozen snapshot
с вложенными коллекциями, TTL/replay/cancel, атомарное конкурентное использование, запрет
переноса token между режимами, preconditions, result schema/verifier, caller cancellation,
timeout и ошибки audit до/после side effect. UI tests проверяют реальный click и отсутствие
approval при одном лишь `dialog.accept()`.

Native macOS команда использует `QT_QPA_PLATFORM=cocoa` вместо windows. Screenshot/GUI
успех на macOS не закрывает Windows acceptance. `--smoke-test` продолжает проверять shell;
подтверждения никогда не выдаются автоматически этим CLI-флагом.

Audit инструментов: `<JARVIS_DATA_DIR>/audit.sqlite3`, таблица `events`, JSON в колонке record.
Если каталог/commit недоступен, запуск adapter запрещён. После старта adapter ошибка или
отмена не гарантирует отсутствие эффекта — UI показывает соответствующее предупреждение.

## Этап 3: Windows Automation

Обычный full suite: 133 passed, 3 Windows acceptance tests skipped на macOS.
Portable focused suite: 42 passed; GUI suite с native Cocoa: 29 passed.
Это проверки контрактов, fake UIA objects и subprocess cleanup, не доказательство UIA.
Три настоящих Windows-сценария требуют флаг `--run-windows`; одного marker недостаточно.

В Windows 11 x64 с интерактивным рабочим столом, обычными правами пользователя:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows.py tests/unit/test_windows_native.py tests/integration/test_windows_ui.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest tests/integration
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m jarvis
# Сохранить и закрыть существующие окна Notepad вручную перед следующей командой.
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_windows_acceptance.py --run-windows -v
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m pip list
```

Native-тесты открывают приложения и оставляют текст `Jarvis integration test` в Блокноте.
Они не закрывают приложения и не удаляют/сохраняют документы. Если Notepad восстановил
непустую вкладку, тест не пишет в неё; подготовьте пустое состояние вручную и повторите.
Chrome/VS Code должны быть установлены для проверки открытия/фокуса; отсутствие даёт
skip с объяснением, а не доказательство успешного запуска. Фиксируйте причины всех skips.

Ручная Windows-приёмка дополнительно:

1. Выбрать каждое приложение, открыть, обновить список, выбрать окно и проверить фокус.
   Уже запущенное приложение используется повторно; нестандартные пути не угадываются.
2. Ввести русский текст, перенос строки и буквальное `{ENTER}` в пустой Notepad через
   CONFIRM. До approval ввод отсутствует; после approval read-back совпадает. `{ENTER}`
   не интерпретируется как клавиша. Диалог показывает HWND/PID/process time/editor/tab/text.
3. Между подготовкой и approval изменить вкладку/окно/текст вручную, затем подтвердить:
   старый target отклоняется. Подготовить новое действие после обновления списка.
4. Попытаться выбрать непустой документ: INVALID, содержимое сохранено. Не редактировать
   целевую вкладку параллельно во время реального ввода: compare-and-write не атомарен.
5. Отменить preview/Stop/закрыть Jarvis во время ожидания. Helper не должен оставаться;
   запущенные приложения и уже выполненные эффекты могут остаться.
6. Проверить неподдерживаемый редактор, недоступные приложения/права и отказ focus:
   явный failure без ложного SUCCESS и без keyboard fallback.
7. Включить simulation: не создаётся helper, не запускаются приложения и не читаются окна.
   Для focus/type GUI требует ранее наблюдённый target, без фиктивных окон.
8. Проверить 100%/150%/200% DPI, прокрутку, полный approval и shutdown. Зафиксировать
   Windows build, Python/Qt/pywinauto/psutil и версии Notepad/Chrome/VS Code.

Переносимые негативные тесты покрывают exact target/approval, process reuse, runtime ID,
выбранную вкладку, непустой редактор, неверный read-back, schema extras/coercion, отсутствующее
приложение, stop и неподдерживаемую ОС. Transport-тесты запускают реальные временные Python
процессы: hang, cancel при создании и после запуска, oversized/invalid reply, kill/reap.
Текст payload не передаётся в argv. Windows process termination всё ещё требует native проверки.

Helper сообщает только конечные категории: unsupported_platform, application_missing,
target_changed, control_unsupported, native_timeout, native_failure. Повторный успех после
ошибки требует нового request; автоматического повторения действия нет.

## Этап 4: браузерные проверки

Установите Chromium **до** full suite; отсутствие binary — ошибка setup, а не skip.
Обычные тесты используют только свой loopback fixture с явно переданной конструктору
test policy. Ни production config, ни tool args не разрешают loopback. Проверка POST
выполняется только на локальном `/submit`, никакой реальной отправки сообщений нет.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/unit/test_browser.py tests/integration/test_browser.py tests/integration/test_browser_ui.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_browser_ui.py
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m jarvis
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe -m build
```

Ручной сценарий: открыть инструменты → проверить simulation без браузера → снять simulation
→ открыть `https://example.com/` с approval → прочитать заголовок/текст → список вкладок.
Поиск: выбрать DuckDuckGo, запрос `OpenAI`, проверить URL и подтвердить. Прочитать title
и убедиться, что это именно поисковые результаты, а не challenge/ошибка: ответ 202
отклоняется транспортом, принимается только 200. Отказ сайта не
обходить включением JS, cookie/profile reuse или отключением TLS. Результаты локального
поискового fixture не считаются проверкой публичного DuckDuckGo.

На поддерживаемой GET-форме: прочитать → выбрать textbox → ввести буквальный текст
с approval → выбрать наблюдённую submit кнопку → проверить все поля и полный URL →
отменить/подготовить заново/подтвердить. После изменения страницы старое действие должно
отклоняться. Попытки POST, unsafe URL, private DNS, popup и redirect должны отклоняться.
Закрыть выбранную вкладку с approval; закрыть окно инструментов и проверить завершение
его временного browser context/driver. Введённые данные этого сеанса не сохраняются.

Покрытие: реальные Chromium/HTTP/Qt, GET/POST fixture и отсутствие запроса до approval,
отсутствие cookies/background requests, обрыв без повтора, timeout/stop, сохранение соседней
вкладки, actual DOM mutation перед verifier, popup, strict schemas, подмена подтверждения,
policy в simulation, private/mixed DNS и checked-address connector, TLS verification без
session key logs. `browser.search` в default suite использует замену transport destination
на fixture; настоящий Chromium и exact search URL/verification остаются включены.

Фактические результаты и публичная проверка — в [MILESTONE_4.md](MILESTONE_4.md).
Windows UIA и Chromium на Windows нужно принять отдельно; три Windows skips остаются открыты.

Проверка wheel в отдельном окружении на Windows (после build):

```powershell
py -3.12 -m venv .wheel-check
.\.wheel-check\Scripts\python.exe -m pip install dist/jarvis_windows-0.0.1-py3-none-any.whl
.\.wheel-check\Scripts\python.exe -m playwright install chromium
.\.wheel-check\Scripts\python.exe -I -m jarvis --smoke-test
.\.wheel-check\Scripts\python.exe -m pip check
```

Не использовать editable install в этом окружении. Локально отдельный Qt harness с `-I`
также проверил через UI sim/open/type/GET/close/shutdown установленного wheel; только
контролируемый HTTP fixture был прочитан из `tests/browser_support.py` рабочей копии.

## Этап 5: планировщик и реальный провайдер

Обычная suite использует offline recipes/scripted proposals/HTTP protocol fixtures;
реальный Chromium работает только с локальным fixture. Блокнот проверяется portable probe.
Результаты текущей проверки и setup: [MILESTONE_5.md](MILESTONE_5.md).

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_planner.py tests/unit/test_openai_provider.py tests/integration/test_planner_ui.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m jarvis
```

В открытом приложении: «Планировщик команд» → `проверь систему дважды` → два SIMULATED;
снять Simulation Mode → два SUCCESS. Неизвестная офлайн-команда вызывает уточнение.
На Windows подготовить пустой Notepad и выполнить `открой блокнот и напиши «Привет»`:
до approval текста нет, preview включает точный process/window/editor/tab и текст,
после approval read-back совпадает. Stop во время уточнения/approval не даёт позднему
ответу выполнить следующий шаг. Проверить также закрытие/повторное открытие окна.

Live provider проверяется **отдельно**, отправляет синтетическую команду в OpenAI и расходует
API usage. Модель задаётся доступным ID; ключ вводится скрыто через setup CLI, не через env:

```powershell
.\.venv\Scripts\python.exe -m jarvis.security.credentials set
$env:JARVIS_PLANNER_MODEL = Read-Host "Responses API model ID"
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_model_acceptance.py --run-model -q
```

На macOS используйте `.venv/bin/python` и задайте несекретный `JARVIS_PLANNER_MODEL` перед
командой. Тест отправляет полный каталог схем и проверяет один local.check proposal;
не исполняет инструменты и не является полной живой многошаговой приёмкой. Затем в GUI
выбрать OpenAI, указать модель, разрешить передачу данных и проверить ту же команду в
симуляции/реальном локальном режиме. Не вводить реальные секреты/личные страницы в тест.

Windows acceptance предыдущих adapters (на реальной Windows, после сохранения документов):
`python -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q`.
Без соответствующих флагов три Windows test cases и один live model case пропускаются.

## Этап 6: локальный голос

Обычные тесты не записывают микрофон, не озвучивают звук, не читают ключи и не обращаются
к внешним провайдерам. Native capture callback проверяется с подменённым устройством;
транспорт проверяется настоящими тестовыми процессами. Это не аппаратная приёмка.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,voice]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/unit/test_voice.py tests/integration/test_voice_ui.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m jarvis --smoke-test
.\.venv\Scripts\python.exe -m build
$env:QT_QPA_PLATFORM = "windows"
$env:JARVIS_VOSK_MODEL = Read-Host "Папка распакованной русской модели Vosk"
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_voice_acceptance.py --run-voice -v -s
Remove-Item Env:QT_QPA_PLATFORM
Remove-Item Env:JARVIS_VOSK_MODEL
```

Последний тест открывает настоящее окно и ждёт 90 секунд: пользователь удерживает кнопку,
говорит «проверь систему дважды», отпускает, проверяет текст и нажимает запуск. Тест сам
не нажимает микрофон. Он ожидает два SIMULATED и завершение системной русской речи.
При недоступных зависимостях/устройстве/голосе он падает, а не сообщает фиктивный успех.
`JARVIS_VOSK_MODEL` используется только этим тестом; в обычном UI папка выбирается вручную.
На macOS используется `QT_QPA_PLATFORM=cocoa` и `.venv/bin/python`.

Ручная приёмка:

1. Запуск/открытие окна без удержания не включает микрофон. Проверить разрешение ОС,
   отсутствие устройства, тишину, короткое нажатие и предел 30 секунд.
2. Произнести русскую команду, проверить расшифровку, исправить её. До отдельного запуска
   нет вызова Runner. Неуверенное распознавание отмечено явно.
3. В реальном режиме выполнить ввод в пустой Блокнот. До UI approval текста нет; после
   подтверждения read-back совпадает. «Подтверждаю» голосом не выдаёт разрешения.
4. Проверить «стоп» и «отмена» во время ожидания провайдера, уточнения и approval с явным
   удержанием кнопки. Другие фразы во время задачи не заменяют выполняемую команду.
5. Stop/Escape/закрытие/потеря фокуса во время записи и распознавания не оставляют
   поздней расшифровки. Escape в modal prompt отменяет всю задачу.
6. Остановить TTS; микрофон не работает одновременно с речью. Проверить отключённый звук,
   отсутствующий русский голос, отсутствие записи/аудиофайлов и содержимого в журналах.
7. Проверить читаемость интерфейса и approval при Windows DPI 100/150/200%, повторное
   открытие окна и отсутствие helper после закрытия. Зафиксировать версии ОС, Python,
   Notepad, sounddevice, Vosk, pyttsx3, модель и системный голос.

Результаты: [MILESTONE_6.md](MILESTONE_6.md). Голосовая приёмка не закрывает весь Windows MVP.

## Этап 7 — память

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_memory.py tests/integration/test_memory_ui.py -q
```

Новые тесты проверяют restart/edit/delete/clear, bounds/TTL, malformed storage, optimistic
conflict, cancellation/rollback, selected context, cloud disclosure, отсутствие автоматического
сохранения текста/речи, недоверенные инструкции и невозможность взять target из прошлой задачи.
Точная ручная Windows-приёмка и её ограничения: [MILESTONE_7.md](MILESTONE_7.md).


## Этап 8 — Outlook

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_permissions.py tests/unit/test_outlook.py tests/integration/test_mail_ui.py -q
```

Обычные тесты используют синтетические MSAL, Graph и credential backend. Покрыты exact
approval/replay/concurrency/expiry, account switching, attachments/read-back, uncertain POST,
cancel, unavailable audit, untrusted email и Planner/Qt границы. Ручной live OAuth/mail
сценарий с выбранными пользователем тестовыми аккаунтом и получателем, Windows-команды
и фактические результаты: [MILESTONE_8.md](MILESTONE_8.md). Live email тест автоматически
не запускается и не подтверждает отправку за пользователя.

## Этап 9: установка и приёмка на целевой Windows

Подготовлен `scripts/windows/Install-Jarvis.cmd`: runtime готовится автоматически.
`scripts/windows/Test-Jarvis.ps1` создаёт отдельное acceptance-окружение, запускает полный
набор и native Qt; switches `-NativeApps`, `-Voice`, `-LiveModel` включают соответствующие
явные проверки. Точные команды, recovery cases и полный ручной сценарий:
[WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md). Фактические результаты Mac:
[MILESTONE_9.md](MILESTONE_9.md). Ни один native/live gate ещё не закрыт.
