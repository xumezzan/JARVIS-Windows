# Этап 1 — Desktop Shell

Реализация: 2026-09-14–15. Проверено локально на macOS arm64, Python 3.12.7.
Windows 11 не был доступен; Windows-приёмка остаётся открытой.

## Что работает

- Русский интерфейс PySide6: текстовая команда, принятый текст, текущая задача,
  состояния, прогресс и история действий.
- Отдельный fake worker: thinking → executing → success/error. Введённая команда
  не исполняется и не передаётся worker. Выбор «Проверить ошибку» демонстрирует failure/retry.
- Stop/Escape прерывает ожидание; cancellation имеет приоритет над поздним success.
  Повторный запуск во время работы не создаёт второй поток.
- Монотонный timeout, отмена при закрытии, ожидание завершения worker перед очисткой.
- Кнопки микрофона и permissions отключены с объяснениями. Состояния listening и
  awaiting approval подготовлены, но не активируются фиктивно.
- Ограниченная история, неизменяемая конфигурация из process environment, JSONL с ротацией.
  Ввод и transcript не сохраняются; журнал принимает только enum событий и UUID.
- `--help`, `--version`, автоматический `--smoke-test`. В маленьком окне доступна
  прокрутка вместо обрезания элементов.

![Работающее окно на macOS](images/desktop-shell.png)

## Изменённые файлы

- `src/jarvis/app.py`, `src/jarvis/config.py` — запуск GUI и проверка конфигурации.
- `src/jarvis/ui/main_window.py`, `states.py`, `demo_worker.py`, `activity_log.py`,
  `theme.py` — окно, состояния, фоновые ожидания и оформление.
- `src/jarvis/observability/events.py`, `logging.py` — конечный словарь событий и JSONL.
- `tests/conftest.py`, `tests/integration/test_shell.py`, `test_entrypoint.py`,
  `tests/unit/test_app.py`, `test_config.py`, `test_shell_log.py` — тестирование.
- `pyproject.toml`, `.env.example`, `.gitignore`, `AGENTS.md`, `README.md` — зависимости,
  настройка окружения и инструкции.
- Документы PRD, ARCHITECTURE, SECURITY, ROADMAP, TESTING, COMPATIBILITY и этот отчёт;
  `docs/images/desktop-shell.png` — снимок настоящего окна macOS.

## Проверки и фактические результаты

| Команда/проверка | Результат |
| --- | --- |
| `python -m pytest tests/integration/test_shell.py tests/unit/test_config.py tests/unit/test_shell_log.py` | Первоначальные focused tests: 20 passed |
| `python -m pytest` | 29 passed после добавления проверок ошибочного log path и малого окна |
| `QT_QPA_PLATFORM=cocoa python -m pytest tests/integration/test_shell.py` | 14 passed с native macOS platform plugin |
| `python -m pytest tests/integration/test_shell.py::test_small_window_scrolls_instead_of_clipping` | 1 passed после уточнения type assertion |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | Все файлы отформатированы |
| `python -m mypy` | No issues found in 25 source files |
| `python -m pip check` | No broken requirements found |
| `QT_QPA_PLATFORM=cocoa python -m jarvis --smoke-test` | Окно открылось и закрылось; `Jarvis GUI smoke: success`, exit 0 |
| `python -m build` | Wheel и sdist успешно собраны, wheel построен из sdist |
| Чистый временный venv + установка wheel с зависимостями | Native GUI smoke успешен, console script печатает 0.0.1 |
| Чистый wheel, duration 500 ms / timeout 100 ms | `Jarvis GUI smoke: timeout`, exit 1 |
| Визуальная проверка обычного и уменьшенного окна | Проверены читаемость, disabled controls, отсутствие наложений; добавлена прокрутка |

В этой рабочей копии команды использовали `.venv/bin/python`. Обычные тесты запускают
Qt offscreen; native suite и smoke использовали Cocoa. Ни один из этих результатов
не является проверкой Windows UI Automation или всего MVP.

Проверенные версии: PySide6-Essentials/Qt/shiboken6 6.11.2, pytest-qt 4.5.0,
pytest 9.1.1, Ruff 0.16.7, mypy 2.3.1; build 1.6.1 и Hatchling 1.32.0.
Официальные источники приведены в `COMPATIBILITY.md`.

## Решения и ограничения

1. Выбран PySide6-Essentials: необходимые QtWidgets/QtCore/QtGui доступны без Addons.
   Qt/asyncio bridge пока не нужен; будущий orchestrator добавляется отдельно.
2. Это demo mode этапа 1, а не Simulation Mode с permission checks. Настоящие tools,
   approvals, LLM, голос, память и внешние сервисы не реализовывались.
3. Данные журнала ограничены его интерфейсом. Это не универсальная redaction для
   будущих SDK и не audit tool executions. Credentials для запуска не нужны.
4. На текущем Mac системный `hidden` повторно устанавливался на файлы локального `.venv`,
   мешая Qt plugins и editable `.pth`. Venv вынесен в пользовательский cache за пределами
   workspace; `.venv` сохранён как symlink. Код приложения не содержит обхода этой проблемы.
5. Windows, масштабирование Windows и чистая Windows-установка ещё не проверены.
   Точные команды и ручная приёмка находятся в `TESTING.md`.
6. Зависимости остаются без pin до Windows verification, поэтому разрешённые версии
   при новой установке могут измениться. Wheel не является готовым Windows installer.
7. Все изменения находятся локально. Commit/push в GitHub в этом этапе не выполнялись.

## Следующий prompt

Полный prompt `Implement Milestone 2: Tool Registry and Permission Engine` находится
в `ROADMAP.md`. Следующий этап — typed registry, risk policy, точные одноразовые approvals,
Simulation Mode и настоящий audit с обязательными негативными тестами.
