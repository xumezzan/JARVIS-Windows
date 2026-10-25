# Этап 9 — установка из репозитория и подготовка Windows E2E

Дата: 2026-09-15. Хост разработки: macOS 27.0 arm64, CPython 3.12.7.
**Переносимая подготовка реализована. Native Windows, чистая установка и полный MVP E2E
не выполнены; этап 9 ещё не закрыт.** Живые Outlook OAuth/доставка, OpenAI API и
микрофон/STT/TTS также остаются непроверенными.

## Реализация

- `scripts/windows/Install-Jarvis.cmd` — один вход для агента после получения репозитория.
  `Install-Jarvis.ps1` проверяет Windows 11 x64/desktop, готовит официальный Python для
  текущего пользователя, запускает дальнейшую установку, создаёт стабильный ярлык в «Пуск».
  Не нужны ручные команды Python/pip, PATH, React или инструменты дизайна.
- `%LOCALAPPDATA%\JarvisInstall`: exclusive lock, кэш проверенных архивов, runtime и два
  каталога `slots/a`, `slots/b`. В каждом — отдельный venv, wheel приложения с `[voice]`,
  matching Chromium и русская модель. Dev dependencies в рабочую установку не добавляются.
- При повторе проверяется SHA-256 установленного содержимого и fingerprint исходников.
  Если всё совпало, проверка не скачивает компоненты. При изменении/повреждении готовится
  неактивный слот; переключение `active.json` выполняется атомарно после проверки и запуска.
  Рабочий слот не удаляется при сетевом сбое или отмене подготовки. Неактивный слот
  удаляется только при наличии ownership marker; symlink/junction отклоняются.
- Пользовательские данные и OS credentials находятся вне компонентов установки.
  Относительный `JARVIS_DATA_DIR` и путь внутри `JarvisInstall` отклоняются. Никакой очистки памяти/настроек/ключей.
  Установщик не является tool планировщика; CRITICAL по-прежнему отключён.
- Проверки: `pip check`, импорты runtime-зависимостей, настоящий локальный Chromium с
  offline context и script-disabled документом, загрузка Vosk с явным `model_path`,
  перечисление доступных аудиоустройств и SAPI5-голосов без записи/озвучивания,
  native Qt smoke во временном каталоге данных, запуск обычного приложения.
- `--startup-report` пишет после показа окна только PID, Qt platform и visible.
  Установщик ждёт свежий PID и **windows**, ограничивает ожидание 30 секундами и не считает
  успешный `Start-Process` доказательством старта. Ярлык запускает стабильный `Launch-Jarvis.pyw`
  с аналогичной проверкой; при сбое показывает сообщение о восстановлении.
- `installation-report.json` содержит версии, хэши исходников, исходы проверок и отдельные
  pending-статусы MVP, модели, почты и оборудования. Отсутствующий голос/микрофон/динамик
  выдаёт предупреждение и `voice_setup=needs_user_action`; текстовый режим доступен.
- `JARVIS_VOSK_MODEL` автоматически заполняет поле локальной модели. Сам запуск приложения
  не скачивает файлы, не записывает звук и не включает автоматическую озвучку.
- `Test-Jarvis.ps1` готовит отдельное acceptance-окружение и запускает quality gates.
  Native apps, голос и live model требуют отдельных switches. Живой send не автоматизируется.

## Приёмочные команды

Офлайн-рецепты расширены под PRD: **Открой Chrome и найди OpenAI**,
**Открой Блокнот и напиши Jarvis test successful**. Текст в кавычках продолжает работать.
Ввод в Notepad требует нового наблюдённого пустого редактора и обычного точного approval.
Поиск проходит browser approval и работает в принадлежащем Jarvis анонимном Chromium;
пользовательский Chrome открывается отдельно. Результат public search не проверен.

Уточнение по письмам теперь указывает на существующую вкладку Outlook. Сохранённая роль
не превращается в recipient. Browser/permission/voice/memory/Outlook границы не ослаблены.

## Источники и целостность

Проверены официальные страницы 2026-09-15:

- [Python 3.13.15](https://www.python.org/downloads/release/python-31315/) и
  [Windows unattended install](https://docs.python.org/3.13/using/windows.html): per-user,
  TargetDir, passive, без PATH/launcher/associations; Windows Authenticode проверяет PSF.
- [Playwright browsers](https://playwright.dev/python/docs/browsers): браузер соответствует
  установленному Playwright, отдельный `PLAYWRIGHT_BROWSERS_PATH`, официальный CDN.
- [Vosk models](https://alphacephei.com/vosk/models): выбран
  `vosk-model-small-ru-0.22`, Apache 2.0. [Установка Vosk](https://alphacephei.com/vosk/install).
- [Microsoft execution policy](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies):
  CMD задаёт policy лишь своему процессу; постоянная policy и Group Policy не изменяются.

Python EXE и Vosk ZIP фактически скачаны **только в `/tmp` на Mac**, их размеры и SHA-256
записаны в `scripts/windows/assets.json`. Это проверенные байты с официальных HTTPS URL,
а не заявление о Windows-совместимости. Vosk извлечён безопасным extractor и загружен
локальным Vosk на Mac; речи/микрофона не было. Python EXE на Mac не исполнялся, Authenticode
здесь не проверен. Redirect, неправильный размер/хэш и опасные ZIP paths отклоняются.

Python 3.13.15 — **кандидат bootstrap runtime**, выбранный из актуального официального
списка; dependency compatibility lock отсутствует до реального Windows-прогона. Все
зависимости `pyproject.toml` остаются без pin. Ремонт/новая установка могут разрешить другие
версии. Другой зарегистрированный per-user Python 3.13 не перезаписывается; конфликт
останавливает setup до отдельного решения на целевом устройстве.

PyPI-пакеты разрешаются pip с официального индекса, без пользовательских index/proxy env
и config. Chromium загружается штатным Playwright по HTTPS: **предварительно закреплённого
независимого SHA-256 браузерного архива пока нет**. Хэши установленного дерева обнаруживают
последующие изменения; они не заменяют upstream provenance первого скачивания.
Воспроизводимые Windows constraints и архивные хэши Chromium — остающийся release gate
после проверки реального target; автоматической совместимости будущих версий не обещаем.

## Фактическая проверка на Mac

Изолированный `.venv`, установлен `python -m pip install -e ".[dev]"`. Выполнены focused
installation/permissions/mail/memory/voice checks, full pytest, Ruff lint/format, mypy,
статический mypy win32, pip check, Cocoa GUI smoke и wheel/sdist build.

| Проверка | Фактический результат |
| --- | --- |
| Focused installer pytest | 36 passed, 1.24 s |
| Full `python -m pytest` | **429 passed, 5 skipped**, 32.88 s |
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed, 134 файла |
| `python -m mypy` | Passed, 109 source files |
| `python -m mypy --platform win32` | Passed; только static analysis |
| Native Cocoa planner/voice/mail/memory Qt | **52 passed**, 17.94 s |
| `python -m pip check` | No broken requirements |
| Cocoa `python -m jarvis --smoke-test` | Success, exit 0 |
| `python -m build` | Wheel и sdist; состав проверен отдельно |
| PowerShell parser 7.6.6 на Mac | Оба `.ps1` разобраны без ошибок |
| `git diff --check` | Passed |

Пять skipped: три Windows app acceptance, один live OpenAI и один voice hardware.
Live Outlook остаётся отдельной ручной приёмкой. Финальный smoke повторён после правок.


Отдельно: интерактивный `python -m jarvis` на Cocoa с отдельным `/tmp/jarvis-milestone9-gui`.
Через native UI открыт планировщик, снята simulation, введено «проверь систему дважды»:
оба `local.check` вернули SUCCESS. Окна закрыты. Startup receipt: `platform=cocoa`,
`visible=true`, PID запущенного процесса. Это проверка Mac GUI, не Windows.

PowerShell 7.6.6 для macOS распакован из официального GitHub-релиза в `/tmp` после сверки
SHA-256 с release metadata. Parser успешно разобрал оба `.ps1`; никакие Windows-команды
не запускались. Это синтаксическая проверка, не запуск PowerShell 5.1/Win32/COM.

## Открытые ворота

### Дополнение 2026-10-14 — автоматическая диагностика и отчёты

В рамках этапа 9 установщик расширен по запросу пользователя: он проверяет безопасные
локальные функции и сохраняет текущие результаты, в том числе при сбое и обычной отмене.
Это переносимое дополнение, **не новая Windows-приёмка** и не реализация этапов 10–32.

- `installation-report.json` теперь schema 2; рядом создаётся читаемый русский
  `installation-report.md`. Каждая проверка имеет статус, фиксированную категорию,
  длительность, границу доказательства и следующий шаг. Отчёт обновляется до и после
  проверки; прежний успешный файл не заменяет текущий результат.
- Bootstrap создаёт свои `installation-bootstrap.json`/`.md` с общим UUID `run_id`:
  host, managed root/lock, runtime, application setup, launcher/shortcut. До безопасного
  корня и exclusive lock отчёт находится в новом временном каталоге, путь виден в консоли.
  PS source имеет UTF-8 BOM для русских строк Windows PowerShell 5.1.
- Вместо остановки на первом импорте независимые ограниченные процессы проверяют все
  компоненты, локальный Chromium/Vosk, устройства и SAPI. Обязательные ошибки или
  отсутствующие результаты запрещают запуск и активацию кандидата.
- Добавлены реальные локальные probes PermissionEngine/simulation/cancel/audit,
  OfflineProvider/Runner и synthetic memory roundtrip. Используются только временные
  данные. Native credential probe лишь строит backend, не читает/записывает secrets.
- Исправлен cleanup: timeout/ошибка Windows taskkill не пропускает kill/wait прямого
  дочернего процесса. Очистка полного Windows process tree по-прежнему требует native проверки.
- Live модель, почта, запись/звук, UIA, public search, запуск из «Пуск» и полная приёмка
  остаются `not_checked`/`pending`. `automatic_checks_passed` означает только описанную
  автоматическую диагностику. GUI smoke по-прежнему проверяет demo shell.

Охват, значения статусов, команды и невозможность гарантировать файл до старта скрипта
или после hard kill описаны в [INSTALLATION_DIAGNOSTICS.md](INSTALLATION_DIAGNOSTICS.md).

Проверка на Mac, Python 3.12.7, отдельный venv и `pip install -e ".[dev,voice]`:

| Проверка | Результат дополнения |
| --- | --- |
| Focused installer/report/offline probes | 103 passed |
| Full `QT_QPA_PLATFORM=offscreen python -m pytest` | 527 passed, 5 skipped |
| Ruff check / format --check | Passed, 152 файла |
| Mypy | Passed, 118 source files |
| `python -m pip check` | No broken requirements |
| Cocoa `python -m jarvis --smoke-test` | Success, exit 0 |
| Интерактивный Cocoa `python -m jarvis` с временными данными | Planner/offline/execute: два `local.check: SUCCESS`, окна закрыты |
| `python -m build` | Wheel и sdist собраны |
| PowerShell 7.6.7 parser на Mac | Оба `.ps1` синтаксически разобраны; PS5.1/COM/Windows не запускались |

Команды isolated probes `python -m jarvis.installation.probe --check permissions`,
`--check planner`, `--check memory`, `--check offline_chromium` выполнены на Mac;
это локальные исходы, не evidence Windows. Live credentials, model/mail API и аудио
не использовались. Версии зависимостей не закреплялись; native gates остаются ниже.

### Дополнение 2026-10-15 — один документ для отправки

По запросу пользователя установка теперь сохраняет **`Jarvis-Report.html`** и текстовую
копию **`Jarvis-Report.md`**. HTML самодостаточен: UTF-8, без JavaScript, внешних ресурсов,
аккаунтных данных и зависимости от соседних файлов. Путь печатается после завершения.
Основной каталог — `%LOCALAPPDATA%\JarvisInstall`; при раннем отказе используется
напечатанный временный каталог. Сохраняются прежние исходные JSON/Markdown-отчёты.

- PowerShell пишет консервативный документ даже без готового Python. В `finally`, если
  runtime проверен, ограниченный stdlib-only exporter объединяет текущий bootstrap и
  диагностику приложения. При сбое exporter остаётся консервативный документ.
- Объединяются только одинаковые UUID `run_id`. Отсутствующий, устаревший, повреждённый,
  чрезмерный или ссылающийся на symlink отчёт не используется как доказательство.
  Произвольные label/evidence/exception/stdout/пути из JSON не копируются: используются
  фиксированный каталог и разрешённые metadata версий/хэша исходников/Windows build.
- Матрица перечисляет текущие функции и отдельно показывает фактические локальные
  проверки, обнаружение компонентов и непроверенные реальные сценарии. Импорт Vosk,
  обнаружение аудиоустройства или `202` не превращаются в доказательство речи/доставки.
  Установщик не может отметить ручную приёмку успешной.
- Добавлены ограниченные `browser_policy` и `outlook_local`: реальный PermissionEngine
  проверяет browser schema/policy/simulation без backend, синтетический RAM-черновик,
  очистку и запрет удалённого draft/send без approval. OAuth/Graph/credentials/сеть/аудио
  не вызываются. Проверки не создают approval authority.
- Исправлен обнаруженный при проверке перезаписи отчёта вызов .NET `File.Replace`:
  вместо преобразуемого в пустую строку `$null` передаётся `[NullString]::Value`.
  Это сохраняет возможность обновлять текущий отчёт после первой записи.

Фактическая проверка на Mac, CPython 3.12.7, отдельный
`/private/tmp/jarvis-share-report-venv`, установка `python -m pip install -e ".[dev,voice]"`:

| Проверка | Результат |
| --- | --- |
| Exporter: matching/stale/malformed/secret exclusion, manual false-pass, CLI `-I -S` | 33 passed |
| Offline diagnostic probes, включая новые browser/Outlook проверки | 23 passed |
| Полный `QT_QPA_PLATFORM=offscreen python -m pytest -q` | **571 passed, 5 skipped**, 39.37 s |
| `python -m ruff check .` / `python -m ruff format --check .` | Passed, 156 файлов |
| `python -m mypy` / `python -m mypy --platform win32` | Passed, 120 source files; win32 — только статический анализ |
| `python -m pip check` | No broken requirements |
| `python -I -m jarvis.installation.probe --check browser_policy` / `--check outlook_local` | Оба `passed`; локальные синтетические данные |
| Cocoa `python -m jarvis --smoke-test` | Success, exit 0 |
| Интерактивный Cocoa `python -m jarvis`, временные данные | Через native UI: offline/execute, два `local.check: SUCCESS`; окно закрыто, exit 0 |
| PowerShell 7.6.7 parser и ранний Mac-отказ bootstrap | Синтаксис принят; итоговые HTML/MD обновлены и путь показан при отказе host; Windows setup не запускался |
| `python -m build` и проверка состава архивов | Wheel/sdist собраны; новые report/probe/exporter включены |
| `git diff --check` | Passed |

Первый полный прогон имел один timeout entrypoint при чтении старого сгенерированного
`__pycache__`; удалены только эти производные каталоги в `src`/`tests`, данные/исходники
не менялись. Повторный focused entrypoint: 4 passed; финальный полный прогон выше прошёл.
Визуальный просмотр HTML через встроенный браузер не выполнен: его политика запрещает
`file:` URL. Структура и самостоятельность документа проверены тестами, без обхода политики.
Зависимости не закреплялись, живые credentials/API и запись/озвучивание не использовались.
Доставка изменений через опубликованный `main` этой локальной задачей не выполнялась.

**Windows-установка, PS5.1/Win32/COM, полный MVP и живые сценарии остаются непроверенными.**
Этот документ можно будет отправить в чат после установки на целевом ноутбуке;
описание и точные Windows-команды — [INSTALLATION_DIAGNOSTICS.md](INSTALLATION_DIAGNOSTICS.md).

### Оставшаяся native-приёмка

1. Чистая Windows 11 x64: runtime 3.13.15, все wheels/native DLL, Authenticode, SAPI,
   permissions, кириллица/пробелы, первый/повторный запуск, ярлык, offline repeat и repair.
2. Реальный Windows MVP: Notepad UIA/read-back, Chrome/VS Code, действительная поисковая
   выдача, контекст, approvals, emergency stop, audit и все failure cases.
3. Настоящая речь и русский голос с явным удержанием пользователя; личные Outlook OAuth
   и тестовая доставка после нормального approval; opt-in OpenAI.
4. После native-совместимости — constraints/хэши зависимостей и повтор clean install.

Следующая задача: выполнить [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md) на целевом
ноутбуке и исправить выявленные Windows-ошибки. Не переходить к календарю или второму сервису.
