# Jarvis — Technical Architecture Dump

Дата анализа: 2026-10-24. Аудит текущего рабочего дерева, включая tracked modifications и untracked source files. Git HEAD: `477500917101cd12faad25540e7eccf6541a1f63`; этот commit сам по себе не воспроизводит описанное рабочее дерево.

Корень: `/Users/whomaun/Documents/ChatGPT/JARVIS for Asal`. Пути ниже заданы относительно этого корня для передачи документа вместе с репозиторием. Основание утверждений — исходники, manifests и выполненные проверки. Roadmap используется только для обозначения планов, не как доказательство реализации. Зависимости и внешние API не обновлялись в рамках аудита.

## 1. Стек и окружение

### 1.1. Платформы и зависимости

Целевая установка — Windows 11 x64: `pyproject.toml` содержит Windows 11 classifier; `src/jarvis/installation/setup.py:supported_host()` требует `win32`, `amd64`/`x86_64`, Windows build >= 22000. PowerShell bootstrap дополнительно проверяет интерактивную 64-bit Windows-сессию. Текущий анализ выполнен на Darwin arm64.

macOS используется для разработки и переносимых тестов. `config.load_config()` имеет Windows/macOS/Linux ветви путей. `ProcessBackend.call()` исполняет Windows-действия только на `win32`. `src/jarvis/platforms/credentials.py:native_store()` поддерживает только Windows Credential Locker и macOS Keychain; на Linux выбрасывает исключение. Поэтому наличие Linux paths и espeak-ветки не означает полной поддержки Linux.

| Слой | Manifest / реализация | Содержимое |
| --- | --- | --- |
| Python package | `pyproject.toml` | `jarvis-windows`, version `0.0.1`, Python `>=3.12`, `src` layout, Hatchling wheel build |
| Основные Python dependencies | `project.dependencies` | `PySide6-Essentials`, `pydantic`, `playwright`, `aiohttp`, `certifi`, `keyring`, `msal` |
| Windows dependencies | platform markers в `pyproject.toml` | `pywinauto`, `psutil` только при `sys_platform == 'win32'` |
| Voice extra | `project.optional-dependencies.voice` | `sounddevice`, `vosk`, `pyttsx3` |
| Dev extra | `project.optional-dependencies.dev` | `pytest`, `pytest-asyncio`, `pytest-qt`, `ruff`, `mypy`, `build` |
| Frontend package | `desktop/package.json` | `jarvis-desktop`, version `0.2.0`, ESM frontend; Electron main/preload — CommonJS `.cjs` |
| Frontend runtime dependencies | `desktop/package.json` | React/React DOM `19.2`, Framer Motion `^14.2.0`, Lucide React `^1.46.0`, Inter variable font `^5.3.0` |
| Frontend dev dependencies | `desktop/package.json` | Electron `^44.7.2`, Playwright `^1.64.1`, TypeScript `^7.0.2`, Vite `^8.3.0`, `@vitejs/plugin-react ^6.1.1`, Tailwind/`@tailwindcss/vite ^4.3.3`, Node/React type packages |
| Стандартная библиотека | Python modules | `asyncio`, `threading`, `sqlite3`, `subprocess`, `ctypes`, `urllib.request`, `hashlib`, `ssl`, `zipfile` |
| OAuth HTTP | `src/jarvis/mail/oauth_helper.py:OAuthHTTP` | `requests.Session`, импортируемый через транзитивное окружение MSAL; отдельной direct dependency `requests` в manifest нет |

Python dependencies не ограничены версиями в `pyproject.toml`. Python lockfile и `requirements.txt` в проекте не обнаружены. Для npm имеется `desktop/package-lock.json`; строки версий выше — декларации `package.json`, не результат проверки установленных npm-пакетов. `desktop/tsconfig.json` включает strict TypeScript, ES2022 и `noEmit`.

Нет FastAPI/HTTP backend, PostgreSQL, Redis, Docker deployment, vector database или внешней очереди заданий в текущем runtime. Python service связывается с Electron через pipes.

### 1.2. Структура проекта

```text
JARVIS for Asal/
├── AGENTS.md                      инженерные и security boundaries
├── README.md                      команды запуска и текущие ограничения
├── pyproject.toml                 Python package, dependencies, test/lint/type settings
├── src/jarvis/
│   ├── __main__.py                python -m jarvis → app.main
│   ├── app.py                     argparse + QApplication + MainWindow
│   ├── config.py                  AppConfig, load_config, non-secret environment settings
│   ├── core/planner/              Provider protocol, offline/OpenAI, serial Runner
│   ├── tools/                     ToolSpec, registry, local/windows/browser/outlook schemas
│   ├── permissions/               immutable Action, approval capabilities, PermissionEngine
│   ├── browser/                   Playwright owner, DOM inspection, approved HTTP transport
│   ├── platforms/                 native audio, ElevenLabs, native credential stores
│   │   └── windows/               UIA/Win32 adapter + disposable worker + pipe transport
│   ├── voice/                     audio contracts, local adapters, cloud speaker consent
│   ├── memory/                    SQLite profile labels and in-memory session labels
│   ├── mail/                      Outlook OAuth, Graph, RAM drafts, attachment loader
│   ├── observability/             SQLite action audit and rotating shell JSONL metadata
│   ├── ui/                        PySide6 shell, planner, panels, dialogs, QThread workers
│   │   └── assets/                Inter.ttf and font license
│   ├── desktop/                   Electron-facing Python service, protocol, system.info
│   └── installation/              bootstrap, asset integrity, probes and shareable reports
├── desktop/
│   ├── package.json               Node scripts and frontend dependencies
│   ├── package-lock.json          npm lock
│   ├── index.html                 renderer document
│   ├── tsconfig.json              strict TypeScript configuration
│   ├── vite.config.ts             React/Tailwind, loopback dev server :5173, relative base
│   ├── electron/
│   │   ├── main.cjs               frameless BrowserWindow and guarded IPC
│   │   ├── preload.cjs            narrow contextBridge API
│   │   ├── runtime.cjs            owned Python subprocess, JSONL protocol, lifecycle
│   │   └── environment.cjs        allowlist of inherited Python environment variables
│   ├── src/
│   │   ├── main.tsx               App, navigation, command submission, runtime subscription
│   │   ├── runtime.ts             wire types, labels, error mapping
│   │   ├── tokens.css             design tokens
│   │   ├── index.css              layout, components and responsive rules
│   │   └── components/
│   │       ├── JarvisOrb.tsx      procedural Canvas 2D orb
│   │       └── JarvisOrb.css      orb styling
│   ├── scripts/
│   │   ├── setup.mjs              ensurePython / isolated editable backend setup
│   │   ├── desktop.mjs            launcher: ensurePython → Vite/built files → Electron
│   │   └── verify-desktop.cjs     Playwright Electron smoke and renderer checks
│   └── qa/                        saved screenshots and historical verification reports
├── scripts/windows/
│   ├── Install-Jarvis.cmd/.ps1    per-user Python/bootstrap/shortcut installation
│   ├── install.py                 stdlib entry into installation.setup
│   ├── Update-Jarvis.cmd/.ps1     fetch published main, preserve dirty checkout, run installer
│   ├── Launch-Jarvis.pyw          stable launcher → active slot → pythonw -I -m jarvis
│   ├── Test-Jarvis.ps1            isolated dev/native acceptance commands
│   ├── export_report.py           standalone bootstrap into report exporter
│   └── assets.json                Python/Vosk URLs, hashes, sizes
├── tests/
│   ├── conftest.py                offscreen default, opt-in Windows/voice/model switches
│   ├── unit/                     schemas, policy, planner, memory, adapters, diagnostics
│   ├── integration/              Qt panels, browser, private desktop service
│   ├── e2e/                      offline flow and explicitly enabled native/model/voice flows
│   └── *_support.py              synthetic browser/Windows/mail/voice fixtures
├── docs/                         architecture, milestones, acceptance, future product specs
└── reference/                    UI reference assets; not an execution module
```

В `src/jarvis` найдено 86 Python files, 12 399 строк, включая `src/jarvis/__init__.py`; в `tests` — 33 файла `test_*.py`. Vendored dependencies, caches и generated build files не входят в эти числа. `design-preview/` отмечен Git как удалённый; актуальный React source находится в `desktop/`.

### 1.3. Точки входа и composition roots

**PySide6:** console script `jarvis = jarvis.app:main`; `src/jarvis/__main__.py` также вызывает `main()`. `app.main()` обрабатывает `--version`, `--smoke-test`, `--startup-report`, читает конфигурацию, создаёт `ShellLog`, `QApplication`, `MainWindow`, затем вызывает `application.exec()`.

`src/jarvis/ui/main_window.py:MainWindow` — launcher с разделами ассистента, истории, инструментов и настроек. `open_planner()` создаёт `PlannerWindow`; `open_permissions()` — `PermissionWorkbench`. Отдельный `submit()` запускает `DemoWorker`, который только ждёт и выдаёт состояния, без выполнения пользовательского текста. `--smoke-test` проверяет именно этот demo flow.

**Electron:** `npm --prefix desktop start` запускает `tsc -b && vite build`, затем `node scripts/desktop.mjs --built`. `npm --prefix desktop run desktop` запускает launcher с dev Vite. Самостоятельный `npm run dev` — только frontend web server; без Electron preload `window.jarvis` отсутствует и команды не выполняются.

`electron/main.cjs:createWindow()` создаёт окно 1536×1024, min 1024×720, `frame:false`. `runtime.cjs:Runtime` запускает `python -u -m jarvis.desktop.service --stdio`, с `PYTHONPATH=<root>/src`, приватными stdin/stdout и отдельным stderr, который не пересылается renderer.

`src/jarvis/desktop/service.py:main()` создаёт второй `QApplication`, `DesktopService`, скрытый `NativePlanner(PlannerWindow)`, `InputBus` и reader thread. `NativePlanner` переиспользует нативные окна approvals, голоса, памяти, Outlook и настроек. Это единое локальное приложение с несколькими процессами, без сетевого backend server.

**Installer:** Windows shortcut по-прежнему запускает Python/PySide6 `jarvis`, а не Electron. В `desktop/package.json` нет electron-builder/electron-forge packaging command. Два способа запуска имеют разные deployment paths.

### 1.4. Конфигурация и установка

`src/jarvis/config.py:AppConfig` — frozen dataclass: `data_dir`, `demo_duration_ms=2400`, `task_timeout_ms=10000`, `activity_limit=200`, `browser_origins`, `planner_model=''`. `load_config()` читает `JARVIS_DATA_DIR`, `JARVIS_DEMO_DURATION_MS`, `JARVIS_TASK_TIMEOUT_MS`, `JARVIS_ACTIVITY_LIMIT`, `JARVIS_BROWSER_ORIGINS`, `JARVIS_PLANNER_MODEL`. `.env` автоматически не загружается.

Дополнительные deployment settings: `JARVIS_VOSK_MODEL` — локальный Vosk path; `PLAYWRIGHT_BROWSERS_PATH` — browser assets; `JARVIS_PYTHON` — выбранный Python в Node launcher; `QT_QPA_PLATFORM` — Qt platform plugin.

Default app data: `%LOCALAPPDATA%/Jarvis` на Windows, `~/Library/Application Support/Jarvis` на macOS, `$XDG_DATA_HOME/Jarvis` либо `~/.local/share/Jarvis` на Linux.

`scripts/windows/assets.json` содержит Python `3.13.15` и `vosk-model-small-ru-0.22` с фиксированными official URLs, SHA-256 и размером. Это зафиксированные значения manifest, не подтверждение актуальности/доступности загрузок.

`src/jarvis/installation/setup.py:install()` готовит inactive `slots/a` или `slots/b`, устанавливает wheel с `[voice]`, Chromium и проверенную Vosk-модель, выполняет probes, проверяет source/file inventory, вызывает `launch()` и атомарно меняет `active.json` после PID-bound visible-window receipt. App data хранится вне install slots. `src/jarvis/installation/assets.py:download()/extract_model()` ограничивают host, bytes, duration, SHA-256 и zip contents. `src/jarvis/installation/report.py:InstallationReport` сохраняет JSON/Markdown; `share.py:render_report()/export_report()` формируют standalone HTML/Markdown только по соответствующему `run_id`.

`desktop/scripts/setup.mjs:ensurePython()` имеет отдельный путь: проверяет `JARVIS_PYTHON`, cached `JarvisDesktop/runtime`, repository `.venv-desktop`/`.venv`; иначе создаёт cached venv и делает editable install `[voice]`. Этот путь сам не устанавливает Chromium или Vosk-модель. На Windows без подготовленного base Python он предлагает repository installer.

## 2. Голосовой контур — Audio / STT / TTS / VAD

### 2.1. Запись и жизненный цикл

Основные контракты: `src/jarvis/voice/contracts.py:AudioClip`, `Transcript`, `Recorder`, `Recognizer`, `Speaker`. Реализации: `src/jarvis/voice/local.py:LocalRecorder`, `VoskRecognizer`, `LocalSpeaker`; native code — `src/jarvis/platforms/audio.py`.

`src/jarvis/ui/voice_panel.py:HoldButton` обрабатывает реальное удержание мыши или Space, release, focus loss и hide. `VoicePanel._record()` запускает `VoiceWorker` только при `button.holding`; `VoiceWorker._work()` вызывает `LocalRecorder.record()`. Helper `python -I -m jarvis.platforms.audio` получает fixed JSON operation через pipe. Запись начинается внутри `src/jarvis/platforms/audio.py:capture()`.

`sounddevice.RawInputStream` использует default input device ОС, 16 000 Hz, mono, signed int16, blocksize 1600 frames (100 ms). Callback добавляет bytes в `bytearray`; верхняя граница — 30 секунд / 960 000 PCM bytes. `release()` через pipe посылает `stop`, helper thread завершает capture. Audio status/overflow прерывает запись. Менее 3200 bytes трактуется как `silence`; это проверка длины, не полноценный VAD.

Wake Word engine и отдельный VAD module не реализованы. Постоянного microphone stream во время ожидания команды нет. Vosk использует `AcceptWaveform()` для сегментации уже записанной речи, но не управляет окончанием live capture.

### 2.2. STT

`VoskRecognizer.transcribe(AudioClip)` передаёт весь PCM как base64 в отдельный helper. `src/jarvis/platforms/audio.py:recognize()` проверяет локальный путь и `am/final.mdl`, создаёт `vosk.Model(model_path=...)`, затем `KaldiRecognizer(model, SAMPLE_RATE)` и `SetWords(True)`.

Запись разбирается кусками по 8000 bytes через `AcceptWaveform()`, `Result()`, `FinalResult()`. Текст объединяется в одну строку; confidence — минимум `conf` всех распознанных слов. Модель загружается заново в каждом STT helper; постоянного прогретого recognizer process нет.

STT работает батчами после release, без partial transcript events в UI. Whisper, OpenAI audio, Azure Speech, Gemini audio и другие STT providers не подключены. Обычный app start не скачивает модель.

`Transcript` ограничивает текст 4000 символами и confidence диапазоном 0..1; `<0.75` считается uncertain. `VoicePanel._finished()` помещает результат в редактируемую команду. Пользователь должен отдельно запустить задачу; transcript не становится approval.

### 2.3. TTS

**Локально:** `LocalSpeaker.speak()` запускает тот же audio helper с operation `speak`. `src/jarvis/platforms/audio.py:speak()` использует `pyttsx3` с явно выбранным native driver: `sapi5` Windows, `nsss` macOS, `espeak` остальные ОС. Выбирается первый найденный русский voice, rate=175; без русского voice операция завершается ошибкой. Текст 1..500 символов, `engine.say()` + блокирующий `runAndWait()` внутри helper. Token/audio streaming с LLM нет.

`src/jarvis/voice/contracts.py:spoken_result()` формирует русский текст детерминированно из `PlanResult.status`, числа `SUCCESS` steps и `may_have_effects`. Произвольные page/model/tool results не озвучиваются этим путём.

**ElevenLabs opt-in:** `src/jarvis/voice/elevenlabs.py:ElevenLabsSpeaker` содержит `voice_id`, fingerprint аккаунта, `authorized_text`, single-use `used`. `src/jarvis/ui/elevenlabs_dialog.py:ElevenLabsDialog` получает key через native credential helper, список голосов и одноразовое согласие. `src/jarvis/platforms/elevenlabs.py:Client` обращается только к `https://api.elevenlabs.io`:

- GET `/v1/user/subscription` — проверка Free, отключённого overage и оставшейся квоты;
- GET `/v2/voices?page_size=100&category=premade` — допустимые premade voices;
- POST `/v1/text-to-speech/{voice_id}?output_format=pcm_16000` — `model_id='eleven_flash_v2_5'`, `language_code='ru'`.

`Client.synthesize()` читает весь bounded response; `playback()` затем пишет PCM в `sounddevice.RawOutputStream` кусками по 3200 bytes. Это batch synthesis followed by playback, без streaming endpoint. Микрофон в ElevenLabs не передаётся; поддерживаются preview и trusted spoken summary. Автоматических retries нет.

Одновременная запись/распознавание/озвучка в `VoicePanel` блокируется наличием одного `worker`. VoiceWorker имеет общий timeout 80 s; local capture helper 35 s, STT 45 s, local TTS 25 s, ElevenLabs helper 55 s. Отдельный `VoicePanel.deadline` — 5 минут для voice task flow.

## 3. «Мозг» и оркестрация — LLM / VLM / Agents

### 3.1. Провайдеры и модели

`src/jarvis/core/planner/contracts.py:Provider` определяет единственный async метод `propose(PlannerInput) -> Proposal`. Реализации — `OfflineProvider` и `OpenAIProvider`; для Electron есть wrapper `DesktopOfflineProvider`.

`OfflineProvider.propose()` — regex/exact-string recipes, без нейросети. Поддерживает «проверь систему [дважды]», открытие Notepad/Chrome/VS Code, ввод в Notepad по точной форме команды, `открой https://...`, `найди ...`, комбинированное открытие Chrome + поиск, выбранное application label. Неизвестная команда приводит к уточнению. Почтовые запросы направляются к уточнению/ручной панели; полноценного offline mail planner нет.

`DesktopOfflineProvider.propose()` заменяет recipe tool `local.check` на `system.info`, затем оставляет остальные recipes прежними.

`OpenAIProvider` использует прямой `aiohttp` POST на фиксированный `https://api.openai.com/v1/responses`. Python OpenAI SDK не используется. Model ID передаётся в constructor, из UI либо `JARVIS_PLANNER_MODEL`; default model не задан. В коде нет фиксированного GPT model ID.

Нет Anthropic/Gemini/Ollama providers, локальной LLM, LangChain/LangGraph/CrewAI/AutoGen и VLM image input. В OpenAI передаются JSON text command, clarifications, observations и selected memory labels; screenshots/images/audio не включаются.

`OpenAIProvider.propose()` строит function schemas из registry metadata, пропускает только `SAFE`/`CONFIRM`, заменяет `.` в tool names на `__`, выставляет strict JSON Schema через `strict_schema()`. Control output — JSON `clarify`/`finish`. Параметры запроса: `store=false`, `parallel_tool_calls=false`, `max_output_tokens=4096`. Нет server conversation ID/previous response ID, hosted tools, streaming, proxies, cookies, redirects или retry loop. Каждый шаг заново передаёт весь bounded task context. JSON request/response ограничены 262 144 bytes.

API key загружается `src/jarvis/security/credentials.py:load_api_key()` через 8-second killable subprocess из `Jarvis/OpenAI`, account `default`. CLI записи — `python -m jarvis.security.credentials set`, hidden TTY input. `OPENAI_API_KEY` не читается.

### 3.2. Цикл принятия решений

Это один последовательный planner/runner. `Runner` — детерминированный orchestrator, а не отдельный LLM agent. `QThread` workers и native helper processes — исполнительные адаптеры; они не планируют независимо и не являются multi-agent swarm.

```text
Reviewed text command + selected MemoryContext
  → PlannerWindow.start()
  → PlannerWorker (asyncio loop inside QThread)
  → Runner.run() / Runner._run()
  → Provider.propose(PlannerInput)
      ├─ clarify → UI prompt → user answer → next proposal
      ├─ finish  → PlanResult derived from actual steps
      └─ call    → PermissionEngine.prepare()
                  → Runner._observed_target()
                  → exact native approval if CONFIRM
                  → PermissionEngine.execute()
                  → adapter preconditions → execute → verify
                  → Outcome → Step → next proposal
```

`Proposal` допускает ровно `call`, `clarify`, `finish`; tool arguments представлены JSON string. `PlannerInput` — command, tuple answers, tuple steps, Mode, catalog JSON, `MemoryContext`. `Step` содержит tool name + immutable `Outcome` с status/error/effect flag/result JSON.

Default `Limits`: 8 actions, 3 clarification answers, provider 30 s, вся задача 180 s, включая пользовательские ожидания. Runner одноразовый (`_used`), actions выполняются serial. Ошибка/denial/timeout/verification failure останавливают план; automatic retries и rollback отсутствуют.

`Runner._observed_target()` требует Windows/browser identities из `SUCCESS` результатов этой же задачи; браузерный элемент должен принадлежать наблюдаемой странице. Для Outlook требуется ранее наблюдённый account, а `outlook.read` принимает message ID из текущего listing. Получатели draft/save/send должны буквально встречаться в command/answers; адрес из письма или памяти не считается user-supplied recipient. Contact labels вызывают предварительное уточнение.

`PlanResult.summary` формируется локально. `finish` означает завершение planner loop, а не независимую проверку всех пользовательских postconditions. Каждый `SUCCESS` означает успешную tool-specific verification; общего semantic goal verifier нет.

### 3.3. Полный каталог registered Tools/Actions

Каталог проверен созданием реестров без запуска UI/браузера/облака. Объединение имён — 23 tools. `PlannerWindow`: 22 = 4 local + 4 Windows + 8 browser + 6 Outlook. `NativePlanner`: 19 = 1 system + 4 Windows + 8 browser + 6 Outlook. OpenAI скрывает CRITICAL/BLOCKED local tests, поэтому видит 20 functions в обычном планировщике и 19 в Electron.

| Tool | Schema / return | Risk | Timeout | Назначение |
| --- | --- | --- | --- | --- |
| `system.info` | `SystemInput` → `SystemInfo` | SAFE | 5 s | OS/release/architecture/Python/CPU count/disk bytes/current UTC; только desktop registry |
| `local.check` | `CheckInput` → `CheckResult` | SAFE | 2 s | синтетическая delay/fail проверка, не системная диагностика |
| `local.append_message` | `MessageInput` → `MessageReceipt` | CONFIRM | 5 s | запись в RAM `LocalOutbox`, не реальная отправка |
| `local.critical_test` | `CheckInput` → `CheckResult` | CRITICAL | 2 s | policy-denied тест |
| `local.blocked_test` | `CheckInput` → `CheckResult` | BLOCKED | 2 s | policy-denied тест |
| `windows.get_open_windows` | `ListWindows` → `WindowsResult` | SAFE | 30 s | до 100 окон разрешённых приложений |
| `windows.open_app` | `OpenApp` → `WindowsResult` | SAFE | 30 s | fixed allowlist app; повторно использует первое найденное окно |
| `windows.focus_app` | `FocusApp` → `WindowsResult` | SAFE | 30 s | restore + foreground точного observed HWND |
| `windows.type_text` | `TypeText` → `WindowsResult` | CONFIRM | 30 s | 1..4000 символов только в observed empty Notepad editor |
| `browser.open` | `OpenPage` → `BrowserResult` | CONFIRM | 45 s | новая принадлежащая Jarvis вкладка, allowlisted URL |
| `browser.navigate` | `NavigatePage` → `BrowserResult` | CONFIRM | 45 s | переход в exact observed page |
| `browser.search` | `SearchWeb` → `BrowserResult` | CONFIRM | 45 s | фиксированный DuckDuckGo HTML search URL |
| `browser.click` | `ClickElement` → `BrowserResult` | CONFIRM | 45 s | observed link или поддерживаемый submit |
| `browser.type` | `TypeElement` → `BrowserResult` | CONFIRM | 45 s | fill observed plain textbox |
| `browser.read` | `ReadPage` → `BrowserResult` | SAFE | 45 s | bounded DOM text/elements observation |
| `browser.get_tabs` | `BrowserArgs` → `BrowserResult` | SAFE | 45 s | только собственные вкладки |
| `browser.close` | `ReadPage` → `BrowserResult` | CONFIRM | 45 s | закрытие exact observed tab |
| `outlook.account` | `Empty` → `MailResult` | SAFE | 5 s | явно подключённый account snapshot |
| `outlook.list` | `ListInput` → `MailResult` | SAFE | 45 s | 1..10 писем inbox/sentitems/drafts |
| `outlook.read` | `ReadInput` → `MailResult` | SAFE | 45 s | выбранное message как untrusted text fields |
| `outlook.local_draft` | `DraftInput` → `MailResult` | SAFE | 5 s | полный Message в RAM текущего окна |
| `outlook.save_draft` | `SaveInput` → `MailResult` | CONFIRM | 60 s | POST draft + read-back полей и вложений |
| `outlook.send` | `SendInput` → `MailResult` | CONFIRM | 45 s | POST exact full message; 202 = accepted |

Определения: `src/jarvis/desktop/system_tools.py:desktop_registry()`, `src/jarvis/tools/local.py:local_registry()`, `src/jarvis/tools/windows.py:register_windows()`, `src/jarvis/tools/browser.py:register_browser()`, `src/jarvis/tools/outlook.py:register_outlook()`.

`PermissionWorkbench` имеет отдельный registry из 16 local/windows/browser tools, без Outlook. В Electron `DesktopService.open_panel('tools')` убирает local items из UI списка этого workbench, но не удаляет их из уже созданного registry. Workbench не передаётся LLM.

Memory CRUD, microphone capture, credential setup, OAuth connect/disconnect, ElevenLabs consent и installer не registered model tools. Доступны только через соответствующий trusted UI/CLI/bootstrap.

### 3.4. Permission boundary

`src/jarvis/tools/base.py:ToolModel` — strict Pydantic model, `extra='forbid'`, `frozen=True`, revalidation. `ToolSpec` задаёт input/result models, risk, pure policy, async preconditions/execute/verify и timeout. `ToolRegistry.register()` создаёт schema-bound wrappers; `PermissionEngine.__init__()` вызывает `registry.seal()`.

`PermissionEngine.prepare()` нормализует и канонизирует arguments, проверяет pure policy даже в simulation, ограничивает snapshot 65 536 bytes, создаёт `Action` и пишет PREPARED audit. `Risk.SAFE` разрешён; CONFIRM требует approval; CRITICAL/BLOCKED всегда denied.

`Action.signature` — SHA-256 от request ID, tool, canonical payload, risk, mode. `ApprovalStore` выдаёт единственную `ApprovalAuthority` в trusted composition root; grant живёт максимум 60 секунд от подготовки действия, одноразово потребляется под lock, инвалидируется при отмене/изменении. `ApprovalDialog._approve()` — UI issuance path; ни Provider, ни Electron IPC не получают authority.

`execute()` проверяет exact prepared action, replay, grant и preconditions; перед execution требует durable STARTED audit, затем вызывает adapter и verifier. Simulation не вызывает preconditions/execute/verify adapters и не создаёт observations, но сохраняет policy/approval requirements. `Outcome.may_have_effects` выставляется консервативно перед adapter run, включая read-only tools; это не доказательство фактической записи.

## 4. Управление компьютером — Actuation & Computer Use

### 4.1. Windows UIA и Win32

`src/jarvis/platforms/windows/transport.py:ProcessBackend` запускает `python -I -m jarvis.platforms.windows.worker`. Fixed `NativeRequest` передаётся stdin, result — bounded JSON stdout; timeout по умолчанию 8 s на helper call, на stop/timeout процесс убивается и собирается. Native imports не происходят в simulation/macOS/Linux.

`src/jarvis/platforms/windows/native.py:NativeDesktop` создаёт `pywinauto.Desktop(backend='uia', allow_magic_lookup=False)`. `psutil.Process` проверяет executable, PID и process start time. `ctypes.WinDLL('user32')` используется для Win32 calls.

Разрешены только `AppId = Literal['notepad','chrome','vscode']`. `paths()/executable()` используют фиксированные System32/Program Files/LocalAppData paths; поддержан packaged Windows 11 Notepad path. Запуск через `subprocess.Popen([exact_executable], shell=False)`, без произвольных argv/shell scripts. Если app уже имеет разрешённое окно, `open` возвращает первое существующее; это не гарантия нового окна или нового документа.

`WindowTarget` связывает app, HWND, PID, process_started, executable, UIA runtime_id, title, editor и empty flag. `EditorTarget` дополнительно связывает native handle, class, automation ID, control type, runtime ID и selected tab runtime IDs. `resolve()/resolve_editor()` повторно наблюдают identity перед действием.

Переключение окна: dispatch `focus` → `ShowWindowAsync(handle, 9)` (`SW_RESTORE`) → `SetForegroundWindow(handle)` → сравнение с `GetForegroundWindow()`, затем повторная verification в `src/jarvis/tools/windows.py`.

Ввод: `TypeText.only_empty_notepad()` запрещает другие приложения и nonempty editor. `editor()` требует единственный visible/enabled Edit/Document с разрешённым native edit class, точным PID/child HWND. `SendMessageTimeoutW(..., EM_REPLACESEL=0x00C2, undo=1, SMTO_ABORTIFHUNG=0x0002, timeout=1000)` вставляет буквальный текст прямо в этот control. Read-back — UIA TextPattern либо ValuePattern; проверка — SHA-256 нормализованного текста. Enter/submit и глобальные keys не отправляются.

Clipboard API, clipboard fallback, Alt-Tab automation, `pyautogui`, `pynput`, mouse coordinate actions, OCR и screenshot-to-action loop в production adapters отсутствуют. Нативная macOS Accessibility automation не реализована. OS calls ограничены описанными Windows operations и безопасной `system.info`.

### 4.2. Browser

`src/jarvis/browser/host.py:BrowserHost` держит долгоживущий asyncio loop в отдельном daemon Thread, лениво создаёт `BrowserSession`, сериализует calls через `asyncio.Lock`. По умолчанию browser headless. `BrowserSession.start()` запускает owned Playwright Chromium, отдельный anonymous context: JavaScript disabled, offline, service workers blocked, downloads disabled, no permissions, TLS verification enabled. Не подключается к пользовательскому Chrome/CDP profile.

`BrowserSession.route()` не пропускает обычную browser network. Только main-frame navigation, совпавшая с одноразовым `Grant`, доходит до `src/jarvis/browser/network.py:fetch()`; HTTP выполняет `aiohttp`, затем HTML возвращается через `route.fulfill()`. Subresources, redirects и popups блокируются. Доверенный fixed DOM inspection script `src/jarvis/browser/inspection.py:INSPECT` вызывается через `page.evaluate()`; arbitrary model JS/selectors не принимаются. Поэтому «JavaScript disabled» относится к scripts страницы, а не отсутствию собственного read-only inspection JS.

`NetworkPolicy` по умолчанию разрешает exact origins `https://example.com`, `https://www.python.org`, `https://docs.python.org`, `https://html.duckduckgo.com`. Config может заменить allowlist. URL validation запрещает userinfo, fragments, invalid aliases и private/local destinations. `PolicyResolver` проверяет именно DNS addresses, переданные connector; HTTP не использует cookies/proxies, redirect follow или automatic retry. Только `text/html`, без encoded content; response <= 1 000 000 bytes, timeout 8 s.

Production generic POST запрещён. Единственное исключение — constructor-injected loopback `fixture_origin` и path `/submit` в tests. `browser.search` жёстко строит DuckDuckGo HTML URL; реальная поисковая выдача не подтверждается самим фактом навигации.

Targets: `PageTarget` связывает tab/document ID, exact URL/origin, main frame, DOM fingerprint; `ElementTarget` — role/name/HTML fingerprint/value/request intent. INSPECT включает состояния form controls в page material. Action paths используют `get_by_role(..., exact=True).click()` или `.fill()` и повторное наблюдение. Elements без однозначного name/locator, plain text edit или разрешённого request intent не предлагаются.

Лимиты: 8 tabs, 50 elements/page, page text 12 000 chars, input 4000 chars; browser phase 15 s, registered tool 45 s. Stop отменяет HTTP/Playwright; fixed CDP `Page.stopLoading` используется только для остановки загрузки. Hard-kill containment зависшего Chromium в этом browser owner не реализован.

### 4.3. Outlook API

Это отдельный service adapter, без управления веб-интерфейсом Outlook. `src/jarvis/mail/oauth_helper.py:authenticate()` использует MSAL `PublicClientApplication`, authority `https://login.microsoftonline.com/common`, interactive auth code + PKCE через библиотеку; scopes `User.Read`, `Mail.ReadWrite`, `Mail.Send`. Client ID вводится пользователем, client secret нет. `ProcessCredentials` изолирует MSAL/credential access в bounded helper; UI connect/disconnect явно отделены от model tools.

`GraphTransport.request()` допускает fixed `https://graph.microsoft.com/v1.0/me` paths и только разрешённые GET/POST; no arbitrary service URL. `MailSession` связывает home account ID, Graph user ID, exact address и UUID session; перед mail operation заново проверяет `/me`.

`MailSession.write()` отправляет полный approved `Message`, а не изменяемый remote draft ID. Remote draft проверяется повторным GET полей и attachment bytes. Send допускает 202 и возвращает `state='accepted'`; `MailResult.delivery_verified` всегда `False`.

Вложения загружает только UI-selected `src/jarvis/mail/attachments.py:read_attachment()` через helper; regular file, без symlink, <=32 KiB, до 3 attachment identities, bytes в RAM. Модель не получает file paths или универсальный read-file tool.

## 5. Контекст, память и хранение состояния

### 5.1. Что где хранится

| Состояние | Владелец / представление | Persistence / предел |
| --- | --- | --- |
| Текущий запрос и clarifications | `Runner._run()`, `PlannerInput` | RAM одного task; command/answer <=4000 chars |
| Scratchpad | `Runner.steps: list[Step]`, `Outcome.result_json` | RAM, до 8 actions; results передаются next proposal как untrusted data |
| Текущая задача Electron | `src/jarvis/desktop/service.py:Task` | RAM Python service; command/phase/steps/summary |
| История Electron | `DesktopService.history` | `deque(maxlen=20)`, текущий session; command excerpt <=120 chars, timestamps/status/count |
| React view state | `main.tsx:App` hooks | RAM renderer, включая theme/motion/mode; localStorage persistence отсутствует |
| PySide6 shell activity | `src/jarvis/ui/activity_log.py:ActivityLog` | bounded QListWidget, `activity_limit=200` default |
| Browser state | `BrowserSession.pages/documents`, Playwright context | ephemeral RAM/session; не browser profile storage |
| Windows observations | `WindowTarget`, `EditorTarget`, current outcomes | task/workbench RAM; новые tasks должны наблюдать targets заново |
| Profile memory | `src/jarvis/memory/store.py:MemoryStore` | `<data_dir>/memory.sqlite3` |
| Session labels | `src/jarvis/memory/session.py:SessionContext` | RAM отдельного окна, TTL 30 min, clear при reset/shutdown |
| Tool audit | `src/jarvis/observability/audit.py:AuditLog` | `<data_dir>/audit.sqlite3`, table `events(id,record)` |
| Shell log | `src/jarvis/observability/logging.py:ShellLog` | `<data_dir>/logs/shell.jsonl`, 1 MB + 2 backups, metadata only |
| Mail drafts/attachments | `MailSession._drafts/_attachments` | RAM; <=10 local drafts, <=3 attachments |
| API keys / OAuth cache | explicit `keyring` native backend | OS credential store, не SQLite/.env |
| Install state | `active.json`, slot receipt, JSON/MD/HTML reports | `%LOCALAPPDATA%/JarvisInstall`, отдельный install tree |

Постоянной истории диалога с role-based messages в БД нет. Многотуровый контекст ограничен command + clarification answers + tool outcomes одной задачи. Нет отдельного free-form chain-of-thought scratchpad, summarization pipeline, embeddings или RAG retrieval. OpenAI output reasoning items не используются как сохранённая память; следующему запросу передаются фактические step outcomes.

### 5.2. Profile/session memory

`src/jarvis/memory/models.py:Kind` разрешает `profession`, `application`, `project`, `contact`, `preference`. `Entry` содержит id/kind/label/value/updated/expires; label/value — нормализованные NFKC обычные строки 1..80 chars с ограниченным алфавитом. `application` value только `notepad`, `chrome`, `vscode`. Фильтр отклоняет некоторые секретоподобные слова/IDs, но не является общей secret detector системой.

`MemoryStore` создаёт table `profile(id TEXT PRIMARY KEY,payload TEXT)`; payload — validated Entry JSON. `BEGIN IMMEDIATE`, `secure_delete=ON`, DELETE journal; SQLite timeout 1 s, cancellation progress handler. Максимум 32 profile entries, file size guard 1 MiB, TTL <=30 дней; expired entries удаляются при чтении. Optimistic concurrency сравнивает `previous` со свежей записью перед save/delete. App-level encryption отсутствует.

`SessionContext` хранит максимум 6 `Hint` с monotonic TTL 1800 s. `MemoryContext` включает максимум 8 selected profile hints + 6 session hints. `MemoryPanel.snapshot()`/`consume_selection()` обеспечивают явный выбор на задачу; отдельное cloud consent нужно для передачи labels в OpenAI. Нет автоматической записи commands/transcripts/files/pages/results в profile или session memory. Модель не может делать memory CRUD.

### 5.3. Аудит и секреты

`AuditLog` использует SQLite с `check_same_thread=False`, lock и `PRAGMA synchronous=FULL`. Сохраняются UTC timestamp, generated session/request IDs, registered tool name, risk/mode/decision/status/duration/error/effect flag и finite actor. Arguments заменяются `[REDACTED]`; body, recipient, account и raw results не сохраняются. Отсутствие durable audit запрещает выдачу approval/adapter execution. Retention/rotation `audit.sqlite3` не реализованы.

`ShellLog` принимает только enum `ShellEvent`, generated IDs и demo mode, без пользовательского текста/исключений. Это отдельный log от tool audit.

`native_store()` выбирает конкретный `WinVaultKeyring` либо macOS `Keyring` без dynamic backend discovery/plaintext fallback. OpenAI service — `Jarvis/OpenAI`; ElevenLabs — `Jarvis/ElevenLabs`; Outlook — `Jarvis/Outlook`. Outlook `CacheStore` разбивает ASCII serialized cache на 64 fixed slots по 1000 chars, использует count/SHA-256 manifest и invalidates manifest до записи. OAuth token временно проходит private pipe и RAM adapter, но не planner input/log/audit.

## 6. Runtime и асинхронность

### 6.1. Процессы и циклы

```text
Node launcher (setup.mjs / desktop.mjs)
└─ Electron main process: app event loop, BrowserWindow, guarded IPC, Runtime
   ├─ Chromium renderer: React + Canvas 2D; нет Node/OS/approval authority
   └─ Python desktop service: QApplication.exec() на main thread
      ├─ daemon Thread read_pipe → Qt InputBus signals
      ├─ PlannerWorker QThread → asyncio.run(Runner.run(...))
      ├─ VoiceWorker QThread → asyncio audio transport
      ├─ MailWorker / MemoryWorker / ToolWorker QThreads
      ├─ BrowserHost daemon Thread → persistent asyncio loop → Chromium/driver
      └─ disposable Python -I helpers:
         Windows UIA, audio record/STT/TTS, credentials, OAuth, attachment read
```

При `python -m jarvis` Electron/stdio слои отсутствуют; `MainWindow` создаёт те же native planner/workbench boundaries напрямую. `DemoWorker` — отдельная bounded QThread pause/progress simulation.

`PlannerWorker` передаёт UI `Prompt(id,kind,value)` через Qt signals и ждёт asyncio Future. `respond()` проверяет prompt ID и вызывает `loop.call_soon_threadsafe`; поздний ответ не применяется к другому prompt. Approval authority остаётся у UI.

`PermissionEngine` использует `RLock`, pending map и cancellation `threading.Event`; action coroutine соревнуется с watcher через `asyncio.wait(FIRST_COMPLETED)` и timeout. Runner имеет собственный watcher. BrowserHost сериализует operations в своём loop через lock; event loop сохраняется между одноразовыми ToolWorker loops.

Native helpers запускаются через `asyncio.create_subprocess_exec`, аргументы пользователя передаются stdin, а не shell/argv. Creation shield предотвращает orphan process при cancellation во время launch. Windows/audio/credential helpers завершаются и reap на отмене/timeout.

### 6.2. Electron boundary и ограничения протокола

`preload.cjs` экспортирует `window.jarvis.status/run/cancel/open/subscribe` и argument-free window controls. Generic IPC, tool name invocation, approve, credentials, raw recording operation или paths не экспортируются. `main.cjs:verifiedWindow()` проверяет webContents, exact mainFrame URL и argument count. `BrowserWindow` использует `contextIsolation=true`, `nodeIntegration=false`, `sandbox=true`, `webSecurity=true`; навигация, popups и webviews запрещены, renderer permissions denied. Production CSP запрещает network connections.

Python `src/jarvis/desktop/protocol.py:Request` — strict Pydantic schema: UUID id, operation `status|run|cancel|open`, bounded command, mode, fixed panel enum. Max request 32 768 bytes, response 262 144 bytes. Невалидная/слишком длинная JSONL line закрывает input reader и вызывает shutdown.

Node Runtime держит максимум 8 pending requests, startup timer 15 s, request response timer 10 s. Это timeout на подтверждение команды/получение state, не общий task deadline. `run` запускает задачу асинхронно; дальнейшие изменения приходят event `state`. При OpenAI режиме desktop service сбрасывает cloud consent и открывает native UI для явного запуска пользователем.

### 6.3. Остановка и голосовое прерывание

Пользователь может прервать Runner кнопкой Stop или Escape; `PlannerWindow.stop()` отменяет voice/mail/runner и закрывает pending dialogs. Electron вызывает тот же native stop через `jarvis.cancel`.

Голосовая отмена доступна во время planning/execution и в approval/clarification dialogs через видимую hold-to-talk кнопку. После release и STT `Transcript.is_cancel` требует confidence >=0.75 и одну из точных русских фраз: `стоп`, `отмена`, `отмени`, `остановись`, `остановить`, `отмени задачу`. В режиме `cancel_only` остальная речь не превращается в новую команду. Voice не может подтвердить действие.

Это не hands-free barge-in: нет постоянно активного VAD/wake word, слова не распознаются до завершения batch capture/STT, запись и TTS не работают одновременно. При уже активном speech worker `_record()` откажется начать capture; для немедленной остановки доступна кнопка/клавиша.

Stop не отзывает отправленный Graph POST, Win32 message, запущенный app или уже выданный HTTP request. Engine возвращает cancellation/timeout/error и `may_have_effects`; rollback/exactly-once для внешних сервисов не обещается. Closing выполняет shutdown/drain workers/browser/audit. Некоторые UI shutdown paths вызывают `QThread.wait()` без собственного timeout; это зависит от завершения внутренних bounded operations и не даёт абсолютной гарантии отсутствия UI freeze при сбое native cleanup.

## 7. Текущий статус и узкие места — What Works vs. TODO

### 7.1. Реализованные execution paths

В исходниках есть исполняемые реализации strict tool registry, deterministic permission policies, single-use approvals, durable audit, simulation без adapter hooks, serial Runner, offline recipes, OpenAI Responses adapter, owned static browser, Windows UIA/Win32 adapter, local voice, optional ElevenLabs TTS, explicit SQLite labels, Outlook OAuth/Graph/drafts/send, Windows installer/update/reports и Electron bridge.

Только синтетическими/демонстрационными по назначению являются `DemoWorker`, `local.check`, `LocalOutbox` и local risk tests. Они не должны интерпретироваться как системная диагностика, отправка почты или LLM inference. В production source scan `TODO`, `FIXME`, `NotImplementedError` как маркеры незавершённых модулей не обнаружены; отсутствие таких markers не подтверждает полноту продукта.

### 7.2. Проверки текущего аудита

| Проверка | Фактический результат |
| --- | --- |
| AST inventory текущего `src/jarvis` | все 86 Python modules разобраны; inventory классов/функций собран |
| Создание обоих tool registries без UI/облака | подтверждены 22 и 19 tools, risks и timeouts |
| `python -m ruff check .` | `All checks passed!` |
| `python -m ruff format --check .` | `169 files already formatted` |
| `python -m mypy` | `Success: no issues found in 125 source files` |
| `python -m pytest tests/unit --ignore=tests/unit/test_app.py -q --tb=short` | 459 passed, 11.12 s |
| `python -m pytest tests/unit/test_app.py -q --tb=short` | 5 passed, 0.50 s; суммарно 464 unit tests |
| `python -m pytest --collect-only -q` | 590 tests collected, 0.57 s; collection не подтверждает выполнение |
| Полный `python -m pytest -q --tb=short` | не завершён: process abort при создании `QApplication` в pytest-qt; unit success не подменяет GUI/integration/E2E |
| Изолированная проверка QApplication с `QT_QPA_PLATFORM=offscreen` | exit 134; `Could not find the Qt platform plugin "offscreen" in ""`; явный plugin path также не устранил abort |
| `npm --prefix desktop run build` | exit 127, `sh: tsc: command not found`; TypeScript/Vite build не подтверждён |
| Python wheel packaging | `pip install --no-deps .` успешно собрал и установил current wheel в existing `.venv-reference` |
| Native GUI smoke / Electron `qa:electron` | не выполнены успешно в этом аудите; frontend build и Qt startup блокируют проверку |
| Windows, real mic/speaker, live OpenAI/Outlook/ElevenLabs, public search | не проверялись; Mac unit results их не подтверждают |

Использован existing `.venv-reference` с Python 3.12.7, PySide6-Essentials 6.12.0, pytest 9.1.1. `.venv/bin/python` отсутствует. Первый pytest завершился 32 collection errors `ModuleNotFoundError: jarvis`; editable reinstall формально прошёл, но import path не заработал. Для unit verification установлен обычный wheel в этом же venv. Причина editable-path failure не установлена и не заявляется дефектом приложения. Product source files при подготовке тестов не изменялись.

`desktop/node_modules` — существующий symlink на `/tmp/jarvis-ui-runtime-20261015/node_modules`; `typescript` и `vite` там не обнаружены. Наличие сохранённых `desktop/qa/*.json` не использовано как подтверждение текущего запуска. npm dependency tree в рамках аудита не переустанавливался.

### 7.3. Конкретные ограничения и несоответствия

1. **Разные runtime/deployment пути.** `Launch-Jarvis.pyw`/Windows installer запускают `python -I -m jarvis`; Electron запускается из repository через Node scripts и требует Node/npm artifacts. Windows one-command installer не является installer Electron UI. У Python package version `0.0.1`, у desktop `0.2.0`.

2. **Две UI реализации и связность service с widgets.** `src/jarvis/desktop/service.py:NativePlanner` наследуется от `PlannerWindow`; `DesktopService` читает/меняет `provider_choice`, `command`, `simulation`, `tabs`, `worker`, `mail`, `memory`, `voice`. Renderer зависит от hidden Qt runtime даже для `system.info`. Ошибка Qt initialization блокирует весь desktop Python service, хотя safe system tool сам не требует GUI.

3. **Срез JSON result в renderer.** `NativePlanner._event()` сохраняет `(outcome.result_json or '')[:2000]`, summary — до 3000 chars. Большой browser/mail result может стать невалидным обрезанным JSON; `main.tsx:formatResult()` тогда показывает raw string. Это UI truncation; Runner получает полный `Outcome.result_json`.

4. **Несовпадение frontend mail labels и registry.** `desktop/src/runtime.ts:toolLabel()` содержит `outlook.list_messages`, `outlook.read_message`, `outlook.create_local_draft`; реальные registered names — `outlook.list`, `outlook.read`, `outlook.local_draft`. Для этих трёх tools frontend показывает fallback raw name. Execution path при этом использует правильный backend catalog.

5. **Offline жёстко ограничен русскими recipes.** `OfflineProvider` не понимает произвольные команды; browser search всегда DuckDuckGo HTML. Команда «открой chrome и найди ...» открывает системный Chrome, а поиск выполняет в отдельном anonymous headless Chromium, не в открытом Chrome profile.

6. **Browser не предназначен для современных authenticated SPA.** JavaScript, subresources, cookies, user profile, external POST, redirects и downloads заблокированы сознательной policy. Многие реальные сайты вне текущего supported scope; allowlisted navigation не доказывает пригодность страницы или успешный web search.

7. **Windows action surface узок.** Только три приложения и ввод только в пустой поддерживаемый Notepad editor. Нет generic UI tree manipulation, arbitrary keys, clipboard, file manager, Word/Excel automation, screenshots/OCR. Fixed executable paths и native edit class allowlist могут отклонять другие installation layouts/Notepad versions; native Windows compatibility не проверена здесь.

8. **Голос имеет batch latency и cold start.** Полная запись собирается и base64-копируется между helpers; Vosk Model создаётся на каждую транскрипцию. Нет streaming STT/TTS, warm recognition worker, VAD endpointing или hands-free interruption. Невозможность capture во время TTS следует из single voice worker guard. Системный TTS жёстко требует русский voice.

9. **Нет durable task recovery/dialogue history.** После restart теряются current task, scratchpad, history, drafts/attachments, browser targets и UI settings. Durable audit содержит только metadata и не позволяет восстановить payload или продолжить task. Profile labels — единственная предметная пользовательская память, не knowledge base.

10. **Нет общего cost/token budget и semantic goal verification.** Ограничены steps/time/context bytes/output tokens, но нет денежного бюджета, usage aggregation, долговечного effect deduplication ledger или task-level semantic verifier. `finished` выводится после provider `finish` и успешных отдельных tools; это не независимое доказательство полного бизнес-результата.

11. **Аудит растёт без retention.** `AuditLog` не очищает/ротирует SQLite events; `synchronous=FULL` commit выполняется синхронно для каждого события. Это потенциальная disk/latency точка, не измеренный performance defect. UI approval/prepare также выполняют синхронную audit запись.

12. **Cleanup не является абсолютной process sandbox.** Python helper transport killable, но browser владеет in-process loop/thread и ждёт Playwright cleanup. `PlannerWindow.shutdown()` и voice/mail memory shutdown могут ждать QThread. Уже выданные внешние effects не откатываются и не повторяются автоматически.

13. **Workbenches имеют самостоятельные sessions/policy composition.** В основном planner BrowserHost создаётся с `NetworkPolicy(config.browser_origins)`; `PermissionWorkbench` без injected host создаёт `BrowserHost()` с default origins. `DesktopService.open_panel('tools')` использует именно такой default workbench. Поэтому configured origin set planner не обязательно совпадает с ручным tool window. Это разные owned browser sessions и registries, общий только audit file.

14. **Установщик проверяет компоненты отдельно от live функций.** `src/jarvis/installation/probe.py` умеет импортировать modules, открыть offline local page, загрузить Vosk model, обнаружить audio devices/native voices; `offline_checks.py` использует synthetic data/temp storage. Ни обнаружение устройства, ни visible-window receipt не доказывают mic/STT/TTS или полный MVP. `MailResult.accepted` не означает delivery.

### 7.4. Планируемое и команды native verification

`docs/ROADMAP.md` явно относит milestones 10–32 к предлагаемым расширениям. EN/RU resource localization, first-run wizard, расширяемый integration manifest, новые сервисы/календарь/соцсети/finance, authenticated browser, visual computer use, company KB, specialized agents, background routines и learning from demonstrations не представлены соответствующими runtime adapters в текущем коде. Их нельзя приписывать системе по PRD/архитектурным схемам. Наличие `MonthCalendar` — local calendar widget, не Calendar service integration.

Native acceptance switches реализованы в `tests/conftest.py`; test names и side effects надо учитывать перед запуском. На целевой Windows после repository installer developer verification запускается:

```powershell
& '.\scripts\windows\Test-Jarvis.ps1' -NativeApps -Voice -LiveModel
```

Скрипт создаёт `.venv-acceptance`, ставит `[dev,voice]` и Chromium, выполняет portable checks/native GUI, а с указанными switches — opt-in Windows apps, voice и model tests. Для LiveModel нужен `JARVIS_PLANNER_MODEL` и OS-stored key. Voice test ждёт явного удержания кнопки, проверки текста и ручного запуска. Windows app acceptance может оставить тестовый текст в Notepad; existing documents не закрываются автоматически. Live Outlook требует отдельного test account/recipient и exact UI approval по `docs/MILESTONE_8.md`/`docs/WINDOWS_ACCEPTANCE.md`; автоматической отправки этим скриптом нет.

Для восстановления frontend dependencies предусмотрено `npm --prefix desktop ci`, затем `npm --prefix desktop run build` и `npm --prefix desktop run qa:electron`; эти команды не выполнялись успешно в данном аудите. Python full quality gates и `python -m jarvis --smoke-test` следует повторить после исправления Qt environment. Полная production/native готовность этим документом не установлена.
