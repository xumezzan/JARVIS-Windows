# Согласование PR #59 с main

Ветка `codex/elevenlabs-free` содержала локальную сборку `b319693`; её база —
`4775009`. В `origin/main` к моменту интеграции находился `82b71d7` с 95 дополнительными
коммитами. GitHub показывал `CONFLICTING / DIRTY`; пробное слияние выявило 49
конфликтующих файлов. CI для исходного конфликтующего PR отсутствовал.

История объединяется merge-коммитом с обоими родителями. Исходная локальная сборка
сохранена в истории и локальной ветке `codex/backup-pr59-before-main-20261025`.
Удалённый `main` этой работой не изменяется.

## Решения по компонентам

- Сохранены connectors, knowledge/derived context, модельный router, журнал длительных
  задач, permission matrix, Windows calculator и запуск установленных приложений из main.
- Windows UIA дополнена SoM, screenshots, clipboard, ограниченными hotkeys и подсветкой.
  Сохранены семантический выбор текстового поля, нормализация RichEdit CR/CRLF/LF,
  неглубокий поиск окон перед чтением выбранного редактора и проверка свежих digests.
- PlannerInput/Runner объединяют knowledge/derived context, task memory, scratchpad и
  актуальный screenshot. Журнал длительных задач и ограничения бюджета сохранены;
  обычная задача ограничена 30 шагами / 300 секундами, длительная требует журнала.
- Основная Qt-оболочка из main остаётся точкой входа по умолчанию. Компактная оболочка
  и её планировщик сохранены отдельно: `python -m jarvis --compact`. Демонстрация
  компактного интерфейса пишет отдельные demo events и не заявляет выполнение команды.
- Electron, Task HUD, watchers и headless runtime сохранены. AudioHost/Vosk переиспользует
  локальную модель; Qt hands-free segmentation и confidence-based wake listener из main
  сосуществуют с явно включаемыми Electron wake/VAD. Микрофонные тесты используют fixtures.
- Сохранены `files.*` из main и отдельные versioned `fs.*`/AST sandbox, зарегистрированные
  также в общем Qt composition root. Их схемы корней и версий не взаимозаменяемы.
- Установщик сохраняет source_commit, обработку venv redirector, проверки ownership marker,
  ярлыки в меню «Пуск» и на рабочем столе, standalone HTML/Markdown диагностику и run_id.
- Strict/Live Browser сохраняют запрещённые и read-only домены main. Live POST не может
  обойти read-only tier. Обычные действия и чувствительные подтверждения классифицируются
  по нормализованному снимку внутри PermissionEngine.

## Проверка интеграции

Проверено на macOS, Python 3.12.7:

| Проверка | Результат |
|---|---|
| Полный `python -m pytest -q --tb=short` | 1681 passed, 23 skipped, 137.66 s |
| Дополнительный регрессионный прогон native bounds / merged composition | 10 passed, 0.65 s; включает добавленный затем случай 101 окна |
| Ruff lint / format | Успешно; 391 Python-файл |
| mypy | Успешно; 316 source files |
| `git diff --cached --check` | Exit 0, неразрешённых конфликтов и маркеров нет |
| `python -m pip check` | No broken requirements found |
| `python -m build` | Wheel и sdist собраны |
| Обычный и `--compact` GUI smoke | Оба успешны |
| `node --test desktop/electron/*.test.cjs` | 10 passed |
| Desktop TypeScript/Vite build | Успешно |
| `npm --prefix desktop run qa:electron` | 14 проверок реального Electron, renderer errors = 0 |

Новый Electron-отчёт сохранён в
[`desktop/qa/merge-pr59-runtime-verification.json`](../desktop/qa/merge-pr59-runtime-verification.json).
Исторические скриншоты и отчёты не перезаписаны. В штатном QA-скрипте исправлено
устаревшее предположение, что история присутствует в DOM на странице ассистента:
проверка сначала открывает страницу истории и затем возвращается к ассистенту.

В 23 пропуска входят 6 явных live/hardware/Windows opt-in cases и 17 PowerShell cases
(PowerShell отсутствует на этом Mac). Эти portable updater cases исполняются Windows CI.
Результат GitHub Windows CI проверяется для опубликованного head PR отдельно.

`scripts/probe_multimodal_budget.py` измеряет реальное UTF-8 представление transport.
Компактная Qt- и Electron-поверхности сохраняют прежний предел регрессии 180 000 байт
для максимального SoM и изображения 48 KiB. Полный Qt-каталог с connectors проверяется
отдельно против неизменного предела запроса 262 144 байт. Удаляются повторяющиеся
schema titles/docstrings; свойства, типы, ограничения и описания аргументов сохраняются.

Проверки macOS и GitHub-hosted Windows CI не подтверждают пользовательские микрофон,
динамики, live cloud/mail/OAuth и полную приёмку Windows. Исторические acceptance-записи
из main сохраняют свою область проверки; новые возможности требуют отдельной приёмки.
