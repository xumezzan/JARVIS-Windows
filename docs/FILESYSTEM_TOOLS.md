# Прямая работа с файлами и данными

Расширение по запросу пользователя от 24 октября 2026. Четыре инструмента доступны
в headless Electron runtime и Qt planner через существующие Runner и PermissionEngine.
Это не означает завершение будущих этапов roadmap или Windows acceptance.

| Инструмент | Поведение | Риск |
| --- | --- | --- |
| `fs.search(query, directory)` | Имя без учёта регистра, расширение `.pdf`, glob `*.csv`; ограниченный рекурсивный обход | SAFE |
| `fs.read_document(path)` | Текст TXT, MD, JSON, CSV, PY, PDF и DOCX в RAM; абсолютный путь и версия файла | SAFE |
| `fs.write_file(path, content, expected_version=null)` | Новый UTF-8 файл только в `workspace`; существующий файл не заменяется | SAFE |
| `fs.write_file(..., expected_version=version)` | Перезапись прочитанной в этой задаче версии, после точного UI approval | CONFIRM |
| `python.sandbox_eval(code, data="")` | Ограниченное подмножество Python для вычислений, CSV/JSON и форматирования | SAFE |

## Папки и запись

Доверенные настройки запуска задают roots. По умолчанию доступны `Desktop`, `Downloads`,
`Documents` и `workspace`. В Windows пути рабочих папок получают через Known Folders API,
в том числе для локального OneDrive redirect; UNC/network roots не поддерживаются.
Вне Windows используются соответствующие папки домашнего каталога.

`workspace` находится в `<JARVIS_DATA_DIR>/workspace`, обычно
`%LOCALAPPDATA%\Jarvis\workspace` на Windows. Можно использовать alias `workspace/report.csv`
или абсолютный путь из результата инструмента. Дополнительные папки проектов задаются
только до запуска в `JARVIS_FS_PROJECT_ROOTS` — JSON-массив абсолютных локальных путей:

```powershell
$env:JARVIS_FS_PROJECT_ROOTS='["D:\\Projects", "D:\\Work"]'
```

Модель не может изменить roots. Отклоняются traversal, UNC/device paths, NTFS streams,
скрытые компоненты, известные служебные/credential names, symlinks, junctions/reparse points
и hard links. Служебный каталог Jarvis защищён, кроме выделенной workspace. Проверка имён
не является универсальным обнаружением секретов внутри обычных пользовательских документов.

Для перезаписи:

1. `fs.read_document` возвращает абсолютный `path`, `version` (device, file ID, mtime, size,
   SHA-256), `text`, `truncated` и `notice`.
2. Планировщик передаёт весь `version` как `expected_version` вместе с точным новым content.
   Runner принимает только успешное, не усечённое чтение в текущей задаче.
3. PermissionEngine связывает path/version/content в immutable action; только прежний
   доверенный UI может выдать expiring single-use approval.
4. Воркер повторно открывает и проверяет файл, пишет временный соседний файл, проверяет
   исходную версию ещё раз, выполняет commit и читает результат обратно.

На POSIX используются directory FD / `O_NOFOLLOW`, на Windows — открытые ancestor handles
и `FILE_FLAG_OPEN_REPARSE_POINT`. Создание публикуется через exclusive hard link: конкурентно
появившийся destination не перезаписывается. Старые файлы с несколькими hard links не читаются.
Jarvis сериализует свои записи через служебный `.filesystem.lock` в каталоге данных.

Windows overwrite использует `ReplaceFileW` без игнорирования ACL errors, с временным backup.
Backup удаляется только после успешной проверки результата. При неопределённом результате
сохраняются `.jarvis-backup-*` / `.jarvis-part-*` в целевой папке для ручного восстановления;
автоматической повторной записи нет. На POSIX сохраняются mode bits владельца; полное
сохранение extended metadata/ACL не заявлено. Не заявляется транзакция с внешними редакторами:
их изменения не участвуют в Jarvis lock, а между последней проверкой и OS commit остаётся
окно гонки. Отмена после commit может иметь эффект; возврат назад не обещается.

## Вычисления

`code` разбирается собственным ограниченным AST-интерпретатором: Python `exec`, `eval`
и `compile` не используются. Доступны присваивания, `for`/`if`, comprehensions, индексы,
арифметика, f-strings, ограниченные методы строк/словарей и фиксированные функции.
`json.loads/dumps`, `csv.parse/format`, `math.sqrt/floor/ceil` доступны и без import;
`import` разрешает только эти три фасада. Это не полный Python и не OS sandbox для
произвольного CPython-кода. Нет file/network/OS API, отражения, классов, функций,
произвольных импортов, `while` или загрузки пакетов.

Данные документа передаются отдельным инертным полем `data`, не подставляются в исходный код:

```json
{
  "code": "rows = csv.parse(data)\nresult = csv.format([{'total': round(sum(float(r['amount']) for r in rows), 2)}])",
  "data": "amount\n12.50\n5.00\n"
}
```

Результат — `value_json` (JSON-строка значения последнего выражения или переменной `result`)
и bounded `stdout`. Для сохранения полученной CSV-строки нужен отдельный `fs.write_file`.
Файлы с формулами для Excel не исполняются; CSV остаётся буквальным текстом, который Excel
может интерпретировать при последующем открытии пользователем. XLSX-редактирование не добавлено.

## Ограничения и приватность

- Поиск: до 20 000 entries, глубина 8, 100 совпадений, около 3 секунд и бюджет ответа.
  `truncated=true` означает неполный поиск; нет обещания мгновенного индексного поиска.
- Входной документ: до 16 MiB, вывод текста до 16 000 символов. PDF — до 100 страниц;
  encrypted PDF не читается, image-only PDF возвращает `no_text_layer`; OCR отсутствует.
- DOCX: основные абзацы и таблицы, ZIP budgets до распаковки; headers/footers, notes,
  tracked revisions и вложенные структуры могут быть опущены с `partial_document`.
- Текст: UTF-8 / UTF-8 BOM, UTF-16 с BOM. Legacy-encoding не угадывается.
- Запись: только TXT/MD/JSON/CSV/PY, до 16 000 символов, общий action snapshot до 64 KiB.
- Вычисления: code до 8 000 символов, data до 16 000; AST до 500 nodes / глубина 35,
  до 20 000 interpreter steps, bounded значения/контейнеры, stdout до 4 000 символов.
- Каждый вызов — отдельный `python -I -B -m ...worker` с private JSONL, очищенным окружением,
  временным cwd, bounded pipes, wall timeout 6 секунд и OS CPU budget 4 секунды.
  Windows — Job Object 512 MiB, Linux — `RLIMIT_AS`; macOS — более слабый parent RSS watchdog
  с порогом 512 MiB и интервалом 20 мс. RSS может временно превысить порог; это не hard cap.
- Cancel/timeout убивает и reaps воркер. После прерывания записи возможны временные остатки;
  `may_have_effects` сохраняется. В simulation адаптер вообще не вызывается.
- Текст, код и результаты не пишутся в audit/profile/session memory; audit сохраняет только
  конечные статусы и конечные error codes. Результаты доступны в RAM текущей задачи.
- В режиме OpenAI общее одноразовое согласие задачи теперь прямо включает пути и прочитанный
  текст файлов. Без него запроса модели нет. Документы остаются untrusted data; не могут
  подтвердить действие, изменить policy или разрешить отправку почты.

Офлайн-провайдер сохраняет прежние ограниченные рецепты: универсальный сценарий на естественном
языке требует настроенного провайдера. Облачная модель не вызывалась при этих проверках.

## Проверки

```sh
python -m pip install -e '.[dev,voice]'
python -m pytest tests/unit/test_filesystem.py tests/integration/test_filesystem.py
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m jarvis --smoke-test
python -m build
npm --prefix desktop run qa:electron
```

Для Qt на headless Mac при необходимости задать `QT_QPA_PLATFORM=offscreen`.
Интеграционный сценарий создаёт synthetic PDF: поиск → чтение реальным pypdf → извлечение
суммы AST-интерпретатором → CSV → workspace через настоящий Runner/PermissionEngine и
убиваемые воркеры. DOCX-проверка читает реальную таблицу. Нет GUI-эмуляции, пользовательских
файлов, Outlook, credentials, микрофона или live модели.

На Windows 11 нужно выполнить те же file tests и отдельно проверить Known Folders/OneDrive,
Job Object quotas, кириллицу, длинные пути, junctions, NTFS ACL, занятую/readonly цель,
отмену и backup recovery. Mac-результаты не подтверждают эти Windows-механизмы.

Источники API: [pypdf](https://pypdf.readthedocs.io/en/latest/user/extract-text.html),
[python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html),
[psutil](https://psutil.readthedocs.io/stable/index.html),
[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[ReplaceFileW](https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-replacefilew).

## Регрессия Qt lifecycle

Общий прогон выявил существовавший PySide6 segfault: автоматический cyclic GC стартовал
из потока планировщика при генерации JSON-схем и удалял Qt wrappers в чужом потоке.
`GuiGarbageCollector` отключает automatic cycle collection на срок жизни GUI, собирает
циклы GUI-таймером и восстанавливает прежний режим после `window.shutdown()`/worker drain.
Тестовый QApplication использует ту же lifecycle-границу. Регрессии проверяют настоящее
удаление циклического QWidget в GUI-потоке и отказ вызова collector из другого потока.
Headless Electron runtime Qt не импортирует.

Числовые OS identifiers / mtime nanoseconds в FileVersion передаются как opaque decimal
strings: JSON-превью Electron не округляет 64-bit значения перед точным approval.

## Фактический результат проверки, 24 октября 2026

На macOS в изолированном venv `/tmp/jarvis-hud-qa-20261024`: **885 passed, 7 skipped**,
65.28 с. Пропущены 5 live model/voice/Windows acceptance tests и 2 optional Pillow checks.
Прошли Ruff lint/format, mypy (153 source files), GUI smoke, sdist/wheel build,
проверка готовности Electron Python runtime и настоящий Electron `qa:electron` с наличием
всех четырёх новых инструментов. Отдельно: 69 filesystem/computation tests; подтверждена
цепочка synthetic PDF → сумма → CSV → созданный файл, overwrite approval/version checks,
создание при гонке, readonly, ZIP bomb, ограничения вычислений и kill/reap.

Предшествующий общий прогон падал в Qt cyclic GC; после введения GUI-thread collector
два полных прогона прошли. Живой OpenAI, реальные пользовательские документы и native
Windows file/ACL/Job Object acceptance не выполнялись. Это локальная проверка, не заявление
о production readiness или завершении Windows MVP.
