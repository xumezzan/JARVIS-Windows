# Проверки этапов 1–2

## Windows 11 / PowerShell

Из корня рабочей копии с Python 3.12 x64:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
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

`--smoke-test` открывает окно, отправляет фиксированный demo text, ждёт worker, закрывает
окно и возвращает 0 только при успешном demo outcome. Ошибка/отмена/timeout дают 1;
невалидная конфигурация или недоступный каталог логов при старте дают 2.

## Ручная проверка окна

1. Запустить без `--smoke-test`. Проверить idle, доступный ввод и отключённый микрофон; permissions открывает тестовое окно.
2. Ввести русскую команду, нажать «Запустить демо» или Ctrl+Enter.
3. Убедиться, что принятый текст отображается буквально, UI остаётся отзывчивым,
   а thinking → executing → success относятся только к демонстрации.
4. Во время следующего запуска нажать Stop/Escape. Получить cancelled, без позднего success.
5. Выбрать «Проверить ошибку», запустить, получить error и затем успешно повторить обычное демо.
6. Закрыть окно во время работы. Процесс должен завершиться без оставшегося QThread.
7. Проверить журнал: служебные JSONL events без текста команды.
8. Изменить масштаб Windows (100%/150%/200%) и проверить читаемость окна/кнопок.

Для воспроизводимого timeout:

```powershell
$env:JARVIS_DEMO_DURATION_MS = "2400"
$env:JARVIS_TASK_TIMEOUT_MS = "100"
.\.venv\Scripts\python.exe -m jarvis --smoke-test
Remove-Item Env:JARVIS_TASK_TIMEOUT_MS
Remove-Item Env:JARVIS_DEMO_DURATION_MS
```

Ожидаемый результат — `Jarvis GUI smoke: timeout`, exit code 1.

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
- `pytest-qt` запускает настоящий Qt event loop и реальные QThread workers. I/O adapters
  здесь отсутствуют; тесты не притворяются проверкой будущих автоматизаций.
- `tests/e2e` пока не содержит полного Windows MVP сценария.

Будущие Windows/adapters тесты должны требовать явного opt-in и marker `windows`.
Один marker сам по себе не отключает тест: при добавлении реализовать opt-in fixture/option
и пропуск на неподдерживаемой ОС. Никаких внешних записей по умолчанию.

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

Этот сценарий не реализован в этапе 1. Не считать hosted CI или offscreen shell tests
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
- Команда не открыла приложение: это ожидаемо, текущий этап демонстрирует только интерфейс.
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
