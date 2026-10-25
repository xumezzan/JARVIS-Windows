# Настраиваемое рабочее пространство — этап 1

`src/jarvis/workspace/` хранит явные настройки владельца, каталог его приложений и
описания рабочих сценариев. Профиль не создаётся из команд, истории или ответов сайтов.
При отсутствии `<data_dir>/workspace_profile.json` используется пустой профиль;
Omadli Group не выбирается автоматически.

## Формат и хранение

Все модели наследуют `ToolModel`: `strict=True`, `extra='forbid'`, `frozen=True`,
повторная валидация экземпляров включена.

- `UserIdentity`: `name`, `organization`, `role`, `tasks_description`, `language`,
  `email_tone`, `social_comments_tone`. Поля по умолчанию пустые, язык — `ru`.
- `AppEntry`: произвольный slug `id` (включая `_` и `-`), название, категория, вид,
  `launch_url`, точные `allowed_origins`, необязательные `native_exe_hint` и
  `mcp_server_id`, `workspace_notes`, `default_mutation_risk`, `enabled`.
  URL может быть `null` для native или выключенного ненастроенного приложения.
- `WorkflowPlaybook`: `id`, `title`, `trigger_hint`, непустые `steps_description`,
  `apps_involved`. Ссылки должны указывать на существующие приложения профиля.
- `WorkspaceProfile`: `user`, `apps`, `playbooks`, `browser_mode`.

ID приложений и playbooks уникальны. Лимиты: 128 приложений, 64 playbooks,
32 origins на приложение, 32 шага на playbook, 2048 символов на описание/заметку,
200 на короткое поле, 64 на slug. Файл ограничен **262 144 UTF-8 байтами** при
чтении и записи, независимо от количества символов.

`WorkspaceStore(data_dir).load()` читает ограниченный объём из обычного файла;
symlink и специальные файлы отвергаются. Отсутствующий файл возвращает пустой
профиль без записи на диск. Повреждённый или слишком большой файл вызывает конечную
ошибку `WorkspaceFailure` (`invalid`, `limit`, `storage`), без содержимого в сообщении.
`save(profile)` повторно проверяет модель, записывает уникальный временный файл
в том же каталоге, делает flush/fsync и `os.replace`. Предыдущий профиль остаётся
целым при отказе до замены. Параллельные записи видны целиком, последняя успешная
замена побеждает; объединения конкурентных правок нет.

## Выбор пресета и добавление своего приложения

Пример явной настройки из Python в виртуальном окружении проекта:

```python
from jarvis.config import load_config
from jarvis.workspace.models import AppEntry, UserIdentity, WorkspaceProfile
from jarvis.workspace.presets import empty_profile, omadli_group_preset
from jarvis.workspace.store import WorkspaceStore

store = WorkspaceStore(load_config().data_dir)

# Выберите один исходный профиль. Пресет заменяет текущую конфигурацию при save.
profile = store.load()  # сохранить уже настроенные приложения
# profile = empty_profile(UserIdentity(name="Имя", organization="Компания"))
# profile = omadli_group_preset(omadli_group_url="https://erp.example.com")

app = AppEntry(
    id="sales_erp",
    name="Sales ERP",
    category="custom_erp",
    kind="web",
    launch_url="https://sales.example.com/dashboard",
    allowed_origins=("https://sales.example.com",),
    workspace_notes="Подготовка отчётов по продажам.",
    default_mutation_risk="CONFIRM",
)
store.save(
    WorkspaceProfile(
        user=profile.user,
        apps=(*profile.apps, app),
        playbooks=profile.playbooks,
        browser_mode=profile.browser_mode,
    )
)
```

Настройки загружаются `load_config()` при старте. После ручного сохранения
перезапустите Jarvis. Новый редактор профиля и новые коннекторы в этап 1 не входят.
Старые три названия в редакторе меток памяти остаются подсказками: можно ввести
другое обычное название. Ограничения содержимого меток сохранены. Windows launcher
уже разрешает установленные приложения по каталогу ОС; профиль не превращает hint
в путь, аргументы запуска или наблюдавшийся WindowTarget.

## Пресет Omadli Group

Пресет содержит ровно 14 записей: `asana`, `claude`, `ms_mail`, `ms_calendar`,
`social_comments`, `omadli_group`, `notion`, `quickbooks`, `ms_teams`, `aws_console`,
`fireflies`, `onepassword`, `ms_admin`, `loom`. Включены описания работы и два
playbooks: подготовка к встрече и ответ на комментарий.

Без `omadli_group_url` запись ERP выключена и не имеет URL/origins. Домен компании
не угадывается. Для 1Password предусмотрено описание native-приложения без веб-доступа:
пароли и содержимое хранилища никогда не должны попадать в LLM, scratchpad или логи.
Ручная разблокировка/автозаполнение выполняются владельцем; автоматический адаптер
автозаполнения этим этапом не добавлен.

## Планировщик и браузер

`Runner` создаёт неизменяемый `WorkspaceContext` и передаёт его в `PlannerInput.workspace`.
Сводка включает `user`, включённые приложения (`id`, `name`, `launch_url`,
`workspace_notes`, `default_mutation_risk`) и playbooks, все приложения которых включены.
Native hints, MCP IDs и network grants провайдер не получает.
Qt, compact Qt и Electron передают один активный профиль из `AppConfig`.

OpenAIProvider сериализует сводку в пользовательском JSON-поле `untrusted_workspace`,
отдельно от trusted instructions. То же поле получает DeepSeek через общий `user_content`.
Это соответствует разделению инструкций приложения и входных данных в
[официальной документации OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering).
Передача профиля перечислена в существующем cloud disclosure; локальная работа
не вызывает API. Общий предел **всего** запроса OpenAI остаётся 262 144 байтами:
профиль не обрезается, превышение даёт `context_limit` до transport/credentials.
Существующее вытеснение старых записей task memory сохраняется.

`NetworkPolicy.from_workspace(profile)` собирает и дедуплицирует точные origins
включённых приложений. `AppConfig.browser_policy` объединяет их с настроенными
базовыми origins. URL требуют публичного HTTPS, без userinfo, wildcard и fragments;
origins не содержат путей или query. Проверка DNS остаётся на стороне runtime.
Отключение одного приложения не отзывает общий origin другого включённого приложения.

`strict_sandbox` соответствует Strict Browser, `authenticated_workspace` — существующему
Live Browser. При старте явно заданный `JARVIS_BROWSER_MODE` имеет приоритет.
CDP endpoint по-прежнему приходит только из trusted startup configuration; профиль
не содержит cookies, паролей, профиля Chrome или endpoint. При явном создании
`AppConfig` из Python его `browser_mode` также имеет приоритет.

Наличие origin в allowlist не меняет site tiers: менеджеры паролей всегда BLOCKED,
QuickBooks/AWS/Microsoft Admin остаются read-only. `default_mutation_risk` — metadata,
не override PermissionEngine. Публикация комментария/отправка/админ-операция требуют
как минимум CONFIRM, а более строгие действующие ограничения сохраняются.
Playbooks не исполняются автоматически, не дают approvals и не заменяют свежие
наблюдения целей, аккаунтов и элементов текущей задачи.

## Проверка

```text
.venv/bin/python -m pytest -q tests/unit/test_workspace.py tests/integration/test_workspace.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build
```

Windows: те же команды с `.venv\Scripts\python.exe`; CI выполняет их на Windows
при pull request. Fixture-тесты не проверяют реальные аккаунты 14 сервисов,
Windows UIA, вход пользователя, облачные запросы или автозаполнение 1Password.

Локальная особенность macOS 2026-10-25: platform plugins в существующей `.venv`
имеют hidden attributes, и исходный запуск Qt завершился с кодом 134 до тестов.
Для pytest и smoke использованы `QT_QPA_PLATFORM=offscreen` и
`QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/jarvis-multimodal-qt-runtime/plugins/platforms`
(плагины той же версии Qt 6.12.0). Настройки production и проверки CI не изменялись.
Отдельный интерактивный `python -m jarvis` с `QT_QPA_PLATFORM=cocoa`, тем же путём
plugins и временным data_dir открыл настоящее окно; оно закрыто после осмотра.

Финальный локальный прогон: **1731 passed, 23 skipped**, Ruff lint/format — код 0,
mypy — 322 файла без ошибок, GUI smoke — `success (simulated)`, wheel/sdist и
`desktop/npm run build` собраны. Пропуски относятся к явным live model/voice/Windows
проверкам и отсутствующему локальному PowerShell. Ранее выявленная регрессия сообщения
при недоступном data_dir исправлена без ослабления существующего теста.
