# Product launcher — этап 5, текущий статус

Этапы 1–4 включены в main: WorkspaceProfile, 14 приложений Omadli Group,
SPA/contenteditable/tabs, Workspace MCP, Calendar и React Workspace Setup.
Этап 4 прошёл hosted Windows CI и слит PR #63.

## Реализованные изменения

- `ui_settings.json` хранит несекретные provider/model, голосовой auto-start,
  Wake Word, auto-stop on silence, озвучивание, task-memory toggle, локальный путь
  Vosk и режим браузера. Cloud consent, approvals, credentials и фоновые watchers
  не сохраняются. По умолчанию Wake Word выключен; Stop приостанавливает его.
- Кнопка **Открыть рабочий браузер** запускает установленный Chrome/Edge с
  `<data_dir>/workspace_chrome_profile` и loopback CDP `127.0.0.1:9222`.
  Проверяются executable, PID/start time, точные аргументы и OS-owned listener.
  Посторонний занятый порт не используется и не освобождается автоматически.
  Профиль не копирует обычный браузер, пароли или cookies. Вход выполняет владелец.
- Idle runtime пересобирает каталог для CDP, сохраняет существующий ApprovalStore
  и проверяет `browser.get_tabs` через PermissionEngine. Подключённый managed
  browser восстанавливается при следующем запуске в Workspace mode.
- `npm --prefix desktop run build` синхронизирует предсобранный UI в
  `src/jarvis/desktop_assets/` с SHA-256 manifest исходников и файлов.
  Установщику Windows не нужны npm, Node или сборка на целевой машине.
- Windows installer подготавливает Electron 44.7.2, соответствующий уже выбранной
  npm-зависимости, из официального release по проверенным размеру/SHA-256.
  Распаковка ограничена и отказывается от traversal, ссылок и Windows aliases.
  Старый активный slot остаётся до успешного запуска кандидата.
- `Launch-Jarvis.pyw` запускает Electron из активного slot и ждёт PID-bound receipt
  видимого окна с готовым backend. `python -m jarvis` сохраняет Qt-вход.
- `scripts/package_desktop.py` создаёт локальное `~/Applications/Jarvis.app`
  с предсобранным UI и ссылкой на проектную `.venv`. Оно не требует npm при запуске;
  это локальная сборка, привязанная к checkout, а не самостоятельный Python runtime.

Данные браузера и UI settings исключены из agent filesystem allowlists.
Настройки среды запуска имеют приоритет над сохранёнными defaults.

## Проверено и что осталось

Ruff, format-check, mypy, Electron build и 13 Node tests прошли. Focused Python
проверки новых настроек/browser provenance и установки: **44 passed**; ранее
workspace/service/browser regression: **124 passed**.

Полный каталог с 14 включёнными приложениями, reviewed MCP, максимальными UIA names
и base64 screenshot 65536 байт: DesktopService **148879**, PlannerWindow **175254**,
compact **149940** байт. Каждый `<200000<262144`; произвольные расширенные профили
ограничиваются существующим `context_limit` до отправки провайдеру.

На macOS системный Chrome проверен с отдельным временным профилем, CDP и OS-owned
loopback listener. Локальная `.app` создана вне синхронизируемого Documents, где
Finder metadata мешала ad-hoc signing. Quarantine и OS consent не обходятся.

**Полный native-product E2E пока не прошёл:** `verify-product.cjs` остановился
на проверке `app.isPackaged` (получено false вместо true). Поэтому восстановление
системного браузера через packaged UI и работа конечного Windows ярлыка не объявлены
проверенными. Нужна диагностика packaged/defaultApp detection, повторный native E2E
и целевая Windows installation/shortcut acceptance. Live accounts, 1Password,
микрофон и облачная модель в этой проверке не использовались.

Проверки:

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
.venv/bin/python scripts/probe_multimodal_budget.py
.venv/bin/python scripts/package_desktop.py
node desktop/scripts/verify-product.cjs
```
