# First-run onboarding и Modules & Connections

Проект **этапа 12**, не реализован. Зависит от завершённой Windows-установки (9), EN/RU
локализации (10) и общего integration/setup contract (11). Текущие диалоги Planner/Outlook/
voice не являются этим мастером. Родительские требования: [PRD](PRD.md),
[roadmap](ROADMAP.md), [security](SECURITY.md), [acceptance](PRODUCT_ACCEPTANCE.md).

Цель: несколько понятных экранов, техническая подготовка по возможности автоматическая,
системное разрешение и личный вход — у пользователя. Пользователь видит только возможности
установленной версии; будущие подписаны Planned / Not yet supported.

## 1. Welcome

Первый экран на English, краткое объяснение JARVIS и сразу видимый English / Русский.
Start setup и Skip for now доступны с клавиатуры. Язык меняет весь оставшийся маршрут
немедленно, включая help/errors/confirmations. При skip остаются рабочий shell, Settings →
Language и возможность Resume setup; никаких доступов или фоновых routines не включается.

## 2. How JARVIS works

Короткий пример request → plan → action → verified result. Объяснить text, held voice,
разрешённый screen context, явную memory, точные approvals и Stop. Показать доступные
сейчас задачи на основе принятого каталога. Planned cards не имеют работающего Connect.
Успех, accepted, simulation и unfinished различимы текстом, а не только цветом.

## 3. Find my apps

До кнопки объяснить область: поддержанные installed apps, browsers, monitors и audio
input/output devices через разрешённые OS mechanisms. Нажатие запускает bounded
зарегистрированную discovery operation с progress/cancel. Никакого чтения документов,
почты, history, cookies или vaults; никаких screenshots/audio capture для поиска приложений.
Результат показывает найденное и неподдержанное; отдельный manual cloud service selection.
Detected означает только локальную программу, не account connection и не разрешение доступа.
Повторное обнаружение доступно позже; не активирует модуль автоматически.

## 4. Choose modules and applications

| Группа | Обязательные элементы каталога |
| --- | --- |
| Email and calendar | Microsoft Outlook mail; Microsoft / Outlook Calendar |
| Tasks and knowledge | Asana; Notion; personal memory; company knowledge |
| Communication and social media | Microsoft Teams; Instagram; Facebook |
| Finance | QuickBooks |
| Meetings and video | Fireflies.ai; Loom |
| Windows, browser and screen | Windows allowlisted tools; owned browser; будущие authenticated browser и scoped screen/visual modules |
| Infrastructure and administration | AWS; Microsoft admin office (предварительно Microsoft 365 Admin Center, требует уточнения) |
| AI models | Offline recipes; OpenAI; Claude; voice settings с local STT/TTS и optional ElevenLabs Free |
| Company's internal system | Omadli Group, адрес/способ доступа требуют настройки |
| Secret management | OS credential backend; optional 1Password |

Каждый элемент: plain-language description; status **Detected, Available to connect,
Connected, Setup required, Error, Not yet supported**; permissions/доступные операции;
Connect, Configure или Skip по реальному состоянию. Expired authorization отображается
как Error / Reconnect required. Локальное обнаружение и connection — независимые признаки.
Рекомендованные найденные приложения не выбраны как доступные для чтения автоматически.
Каталог берётся из trusted manifest: newly registered modules появляются без нового экрана.

При выпуске этапа 12 сервисы этапов 13–32 показаны Not yet supported. Это удовлетворяет
полноте каталога, но не считается их реализацией или успешным подключением.

## 5. Connect and prepare

После выбора подготовить разрешённые configuration values и показать конкретные scopes,
account/organization selector, тип отправляемых данных и возможную цену. Официальный
OAuth/authorization flow; sign-in, MFA, consent делает пользователь. Никаких secrets в чате,
встроенного общего client secret, подключения через чужой browser profile или обхода политики.

Health check — минимальное безопасное чтение с time limit и понятным результатом; не
создавать тестовые письма/tasks/meetings. Для paid read заранее показать расход и budget.
Omadli требует пользовательский адрес и оценку API/UI метода, а не выдуманный endpoint.
Отсутствующий компонент: точное имя/source/version/size/назначение и отдельное install
consent через принятый setup contract. Без такой capability — Setup required и понятный
поддержанный путь. Не менять CRITICAL policy, не включать subscriptions/overages/новые scopes.

Ошибка одного connection не блокирует остальные. Offline/rate limit/denied/admin-required
получают разные объяснения и действия; uncertain external operation не повторяется.

## 6. Personal settings

Минимально необходимые настройки: выбранные UI и conversation/voice languages, time zone
с предложением OS default и возможностью поправить. English остаётся default языка,
даже если ОС русская, до явного выбора пользователя. Name, role, company, working hours,
primary apps/accounts необязательны или заполняются позднее. Явные сохранённые поля —
user-owned settings; не извлекать их из документов или account contents без отдельного scope.

Microphone access, screen access, memory, launch at startup и background checks выключены
до отдельного enablement каждого. Доступ микрофона/экрана не равен началу capture.
Microphone test запускается пользователем, с held gesture/indicator и Stop, затем review.
Отсутствующее устройство/EN или RU voice asset отмечено Setup required; text остаётся доступен.
Local/cloud voice и model data disclosures раздельны. ElevenLabs сохраняет Free-only и
one-use consent; wizard не делает его постоянным голосовым согласием.

## 7. Readiness and first task

Показать Connected / Skipped / Needs attention, language/zone, доступные задачи и условия.
Не выводить Everything ready, если существенная проверка не пройдена. Предложить safe task
на доступных модулях: например показать количество встреч выбранного дня после принятия
Calendar, либо текущий local.check/минимальное Outlook read. Результат должен быть проверен;
при отказе честно показать его и независимый local first task, не выдавая connection за success.

## Общие взаимодействия и сохранение

- Back, skip optional, cancel, restart/resume. Сохранять только успешно выбранные настройки
  и secure references; не требовать повторного ввода завершённых шагов. Partial connection
  снова проверяется минимальным read, а не автоматически авторизуется.
- Не сохранять MFA codes, callback payload, raw tokens в wizard-state, pending approval
  или одноразовое cloud/voice consent. После restart capture не возобновляется автоматически:
  microphone требует нового held gesture, screen/demo — нового explicit Start с indicator и scope. Только ранее явно
  включённые routines могут продолжить свой schedule по действующей policy.
- Modules & Connections доступны после wizard: add, disconnect, select account, rediscover,
  configure и resume setup. Отключение не стирает чужие remote records и не отменяет уже
  выданный effect; текущие задачи/approvals прежнего account прекращаются.
- Settings → Language меняет UI без перевода user data. Рабочий approval preview сохраняет
  точные source strings/bytes; даты дополнительно показывают time zone/offset для действия.
- Native keyboard/focus/screen-reader labels, читабельность EN/RU длинных строк, 100/150/200%
  DPI и минимум размеров окна входят в QA. Перевод не должен скрывать кнопку Stop или payload.

## Проверки

E01/E02/E03/E09/E10/E11: clean Windows, English welcome, immediate RU, back/skip, offline,
permission denial, несколько accounts, restart посреди каждого шага, no repeated entry,
независимые errors, добавление нового модуля, microphone user gesture и exact approval.
Установка и discovery доказываются реальными observations; fixture не доказывает native
готовность. Для каждого сценария записать version/platform, ожидаемое/фактическое и
passed/failed/pending; текущий статус всех сценариев мастера — **planned / not run**.
