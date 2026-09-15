# Этап 0 — результаты

Дата: 2026-09-14. Исходный план: «Jarvis AI Desktop Assistant — план разработки и Codex
Master Prompt», v1.0. Его initial task ограничивает реализацию этапом 0.

## Итог

Создан устанавливаемый каркас Jarvis 0.0.1 с консольным entry point и документацией.
Локальные проверки пройдены на macOS arm64, CPython 3.12.7. Windows-проверка остаётся
невыполненной; полноценный MVP и desktop shell не заявлены готовыми.

## Добавленные файлы

- `AGENTS.md`, `README.md`, `pyproject.toml`, `.gitignore`, `.env.example`.
- `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`,
  `docs/TESTING.md`, `docs/COMPATIBILITY.md`, `docs/MILESTONE_0.md`.
- `src/jarvis/__init__.py`, `src/jarvis/__main__.py`, `src/jarvis/app.py`,
  `src/jarvis/py.typed` и `__init__.py` в `core`, `tools`, `permissions`, `voice`,
  `memory`, `security`, `ui`, `observability`.
- `tests/unit/test_app.py`, `tests/integration/test_entrypoint.py`, `tests/e2e/README.md`.

## Команды и наблюдаемые результаты

Все команды выполнены из корня репозитория. `.venv` и `dist` исключены из Git.

| Команда | Результат |
| --- | --- |
| `python3.12 -m venv .venv` | Изолированное окружение создано |
| `.venv/bin/python -m pip install -e '.[dev]'` | Editable installation успешна |
| `.venv/bin/python -m pytest tests/unit/test_app.py` | 3 passed |
| `.venv/bin/python -m pytest` | 6 passed, включая свежий interpreter вне source tree |
| `.venv/bin/python -m ruff format .` | Исправлено форматирование 5 файлов после первоначальных замечаний |
| `.venv/bin/python -m ruff check .` | All checks passed |
| `.venv/bin/python -m ruff format --check .` | Все файлы отформатированы |
| `.venv/bin/python -m mypy` | No issues found in 13 source files |
| `.venv/bin/python -m pip check` | No broken requirements found |
| `.venv/bin/python -m jarvis` | Код 0, явное сообщение о готовности только каркаса |
| `.venv/bin/jarvis --version` | jarvis 0.0.1 |
| `.venv/bin/python -m build` | Созданы sdist и wheel; wheel собран из sdist |

Дополнительно wheel установлен с `pip install --no-deps` в новый временный venv.
Из каталога вне репозитория успешно выполнены `python -I -m jarvis` и `jarvis --version`.
Проверено наличие `py.typed` и отсутствие `.env`/`__pycache__` в wheel.
Исходники и конфигурация не содержат реальных credentials; тестам секреты не нужны.

Разрешённые локально версии: pytest 9.1.1, pytest-asyncio 1.4.0, Ruff 0.16.7,
mypy 2.3.1, build 1.6.1; изолированный build использовал Hatchling 1.32.0.
Это запись результатов, а не pin зависимостей и не доказательство Windows-совместимости.

## Решения и оставшиеся ограничения

- Runtime использует только Python standard library. GUI/Pydantic/SDK добавляются по этапам.
- Версии dev/build зависимостей пока не закреплены, согласно требованию проверки Windows
  перед pinning. Повторная установка может получить другие версии.
- Windows 11 и PySide6 не запускались в этом окружении. Точные команды проверки
  каркаса находятся в `TESTING.md`; настоящее окно проверяется начиная с этапа 1.
- PermissionEngine, audit, simulation и управление секретами пока описаны как требования,
  а не реализованы. Наличие security-документа не является тестом их защиты.
- Изменения подготовлены в локальной рабочей копии; публикация в GitHub не выполнялась.

## Следующий этап

Точный prompt `Implement Milestone 1: Runnable Desktop Shell` со scope и acceptance
приведён в `ROADMAP.md`. Он добавляет PySide6, состояния, текстовый ввод, fake worker,
activity и structured logging, оставляя реальную автоматизацию следующим этапам.
