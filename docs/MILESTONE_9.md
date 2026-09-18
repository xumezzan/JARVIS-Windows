# Этап 9 — установка из репозитория и подготовка Windows E2E

Дата: 2026-09-15. Хост разработки: macOS 27.0 arm64, CPython 3.12.7.
Обновлено 2026-09-16 после первого запуска на целевой Windows 11 x64.
**Часть native-ворот пройдена (см. «Фактическая проверка на Windows»), но чистая
установка и полный MVP E2E не выполнены; этап 9 ещё не закрыт.** Живые Outlook
OAuth/доставка, OpenAI API и настоящий микрофон остаются непроверенными.

## Реализация

- `scripts/windows/Install-Jarvis.cmd` — один вход для агента после получения репозитория.
  `Install-Jarvis.ps1` проверяет Windows 11 x64/desktop, готовит официальный Python для
  текущего пользователя, запускает дальнейшую установку, создаёт стабильные ярлыки на
  рабочем столе и в «Пуск».
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

## Фактическая проверка на Windows

Дата: 2026-09-16. Windows 11 Pro 10.0.26200 x64, CPython 3.13.15 из подготовленного runtime.
Установщик отработал ранее в тот же день: `dependencies=passed`, `offline_chromium=153.0.8010.12`,
`local_vosk_load=passed`, `warnings=[]`, `gui_smoke=passed`, `native_window=observed`.

Полный набор тестов на Windows сначала дал 5 падений и 9 ошибок, невоспроизводимых на Mac.
Семь дефектов переносимых тестов исправлены отдельным коммитом: лимит переменной окружения
32767 для node id, дочерний процесс venv-редиректора в startup receipt, незакрытое
sqlite-соединение фикстуры (ResourceWarning всплывал в постороннем тесте), нормализация
`os.sep` в `zipfile` при записи и чтении, кодировка/перевод строки дочернего процесса,
привилегия на symlink, лимит командной строки 32767. После правок: **462 passed, 6 skipped**,
стабильно в трёх прогонах. Ruff lint/format, `mypy --strict` (114 файлов), `pip check`,
`python -m build`, `jarvis.exe --version`, `--smoke-test` (success и timeout) — пройдены.
Native Qt: `QT_QPA_PLATFORM=windows`, `tests/integration` — 105 passed.

Пройдено на реальном рабочем столе:

- `windows.open_app` + `windows.focus_app` для Chrome и VS Code. На холодном старте VS Code
  первая попытка вернула ERROR: окно не появляется за отведённое адаптеру ожидание.
- `windows.type_text` в пустую вкладку Notepad: точный текст `Jarvis test successful`
  записан, UIA read-back совпал, текст независимо прочитан сторонним UIA-обходом.
- Windows 11 восстанавливает сессию Notepad, поэтому `test_notepad_open_focus_literal_text_and_readback`
  на такой машине недостижим: приложение корректно отказывается писать в восстановленную
  вкладку. Для этого ворота нужен Notepad, настроенный на пустой старт.
- Локальный русский Vosk: синтезированная SAPI фраза «проверь систему дважды» (16 kHz mono)
  распознана дословно через штатный helper. Это проверка модели и протокола, не микрофона.
- Локальный TTS: русский системный голос присутствует, `LocalSpeaker.speak` отработал.
- Публичная выдача: `html.duckduckgo.com` отвечает 403 неназванному клиенту и 202
  (страница-challenge) названному. Транспорт теперь называет клиента и принимает только 200,
  а поиск использует `lite.duckduckgo.com`; наблюдены настоящие результаты по запросу OpenAI.
  Ссылки выдачи ведут на другие origin и потому не становятся кликабельными элементами.

Не проверено: живой OpenAI, живой Outlook OAuth и доставка, настоящий микрофон, а также
матрица чистой/повторной установки, кириллических путей и прерываний.

## Открытые ворота

Обновлено после прогона 17.09.2026; что именно проверено — в
[WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md), раздел «Результаты прогона 17.09.2026».

1. Чистая Windows 11 x64: runtime 3.13.15, все wheels/native DLL, Authenticode, SAPI,
   permissions, кириллица/пробелы, первый/повторный запуск, ярлык, offline repeat и repair.
   **Открыто:** требует отдельного профиля или VM.
2. Реальный Windows MVP. **Закрыто 17.09:** Chrome и VS Code открываются и получают фокус,
   полный набор тестов зелёный на Windows, живой DeepSeek отвечает по схеме.
   **Открыто:** ввод в Блокнот с read-back (Windows восстанавливает вкладку — нужен Блокнот,
   настроенный на новую сессию) и **действительная поисковая выдача**: источник отвечает
   страницей-проверкой честно представившемуся клиенту после первых обращений, приложение
   правильно отказывается выдавать её за результат. Это продуктовое ограничение единственного
   источника, закрывается фазой 5 [плана агента](AGENT_PLAN.md).
3. Настоящая речь и русский голос с явным удержанием пользователя; личные Outlook OAuth
   и тестовая доставка после нормального approval. **Частично:** слово активации прошло
   opt-in проверку без микрофона; живой микрофон и Outlook остаются за владельцем.
4. После native-совместимости — constraints/хэши зависимостей и повтор clean install.

Следующая задача: выполнить [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md) на целевом
ноутбуке и исправить выявленные Windows-ошибки. Не переходить к календарю или второму сервису.
