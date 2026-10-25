# Сборка локальных изменений — 2026-10-25

Историческая запись первоначального коммита `b319693` до согласования с `main`.
Последующее устранение конфликтов PR #59 описано в [MERGE_PR59.md](MERGE_PR59.md).

Изменения нескольких локальных сессий собраны в ветке `codex/elevenlabs-free`
поверх `4775009`. Проверка выполнена на macOS с Python 3.12.7 из `.venv`.

## Источники и Git

- Проверены `git status`, `git branch -a -vv`, `git worktree list --porcelain`
  и `git stash list`, затем выполнен `git fetch --all --no-tags`.
- Обнаружен один worktree; stashes отсутствуют. Все остальные локальные ветки
  (`main`, `codex/milestone-3-windows`, `codex/milestone-5-planner`,
  `codex/milestones-3-9`) уже являются предками исходного HEAD.
  Отдельных локальных коммитов для переноса нет.
- Полученный `origin/main` на момент проверки — `82b71d7`, на 95 коммитов впереди
  исходного HEAD. Его отдельная линия разработки не включалась в эту сборку.
  Публикация выполняется в текущую ветку, `origin/main` не изменяется.
- `.gitignore` покрывает `.venv/`, все каталоги `.venv-*`, `node_modules/`,
  Python-кэши, `build/`, `dist/` и TypeScript build metadata. Это проверено
  через `git check-ignore`; уже отслеживаемых игнорируемых файлов нет.
- Исторические аудиты сохранены в `docs/`; в корне временных аудитов нет.
  Скриншоты и отчёты в `desktop/qa`, `docs/images` и `reference` сохранены
  как материалы предыдущих проверок, а не как результаты нового GUI-прогона.

## Состав сборки

- Windows UIA, Set-of-Marks, clipboard и ограниченные hotkeys с проверкой
  свежих наблюдений, идентичности окна/процесса и содержимого.
- Multimodal OpenAIProvider, актуальный screenshot, ограниченный scratchpad
  и task memory с отдельным согласием на передачу истории.
- Файловые инструменты и AST sandbox в изолированных workers, ограничения
  рабочих каталогов и версионированное подтверждение перезаписи.
- AudioHost, локальный Vosk, EnergyEndpoint/VAD и явно включаемый wake word.
- Electron runtime, Task HUD, подсветка целей и отключённые по умолчанию
  watchers; перенос интерфейса из `design-preview` в `desktop`.
- Browser modes, Windows installer/updater и диагностика установки.

## Повторная проверка

| Команда | Результат |
|---|---|
| `.venv/bin/python -m ruff check .` | All checks passed |
| `git diff --check` | Exit 0, замечаний нет |
| `.venv/bin/python -m ruff format --check .` | 224 files already formatted |
| `.venv/bin/python -m mypy` | No issues found in 165 source files |
| `.venv/bin/python -m pytest -q` | 992 passed, 5 skipped, 78.78 s |
| `node --test desktop/electron/*.test.cjs` | 10 passed |
| `npm --prefix desktop run build` | TypeScript и Vite build успешны |
| `QT_QPA_PLATFORM=offscreen .venv/bin/python -m jarvis --smoke-test` | Jarvis GUI smoke: success |
| `.venv/bin/python -m build` | Успешно собраны wheel и sdist |

Пропущены один live model test, один hardware voice test и три Windows cases.
Нативный Windows, реальный микрофон/динамики, live OpenAI, Outlook OAuth/mail
и повторный интерактивный Electron-прогон этой сборкой не проверялись.
Предыдущие отчёты сохраняют собственные даты и границы проверки.

Чтобы получить именно эту сборку на Windows, используйте ветку
`origin/codex/elevenlabs-free`. Updater по умолчанию получает `main` согласно
[WINDOWS_UPDATE.md](WINDOWS_UPDATE.md); это отдельный путь обновления.
