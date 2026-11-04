# Electron → Python: аудит элементов интерфейса

Дата: 2026-11-04. Исходная версия: `6198d22e6b55075cfd5db509c6cabce45c6c2e3f`.
Проверены все TS/TSX в `desktop/src/`, основной и overlay preload, Electron main,
JSONL runtime, протокол, DesktopService, DesktopVoice, настройки и тесты.
Это аудит исходного кода; живые сервисы и оборудование отдельно от fixture-проверок.

## Как читать таблицу

- **OK** = `[OK — Полная связь]`: запрос доходит до Python и результат возвращается.
- **FAKE** = `[FAKE — Только визуально в React]`: параметр обещает действовать,
  но Python о нём не знает или UI может показывать неприменённое значение.
- **DEAD** = `[DEAD — Мёртвая кнопка / Иконка без действия]`: обещанное действие недоступно.
- **UI**: намеренно локальная навигация, фокус, раскрытие деталей либо черновик,
  который передаётся при Submit/Save. Это не сохранённая настройка и не FAKE.
- **Electron**: рабочее управление нативным окном, Python для этого не нужен.
- **Декор**: подпись/индикатор, а не управляющий элемент. Отсутствие onClick у SVG
  не делает его кнопкой. Ему не следует придумывать произвольный эффект.

Три заданных вердикта применяются к управляющим элементам, которым нужен backend.
Дополнительные обозначения позволяют не выдавать навигацию за вызов Python и не
отправлять черновики, пароли и согласия при каждом нажатии клавиши.
Повторяющиеся элементы `.map()` представлены шаблоном с точными названиями.

## Полная таблица исходного поведения

| Элемент / Кнопка / Иконка на экране | Что происходит в React | Запрос в preload / protocol? | Что реально делает Python; хранение / Runner | Вердикт до исправления |
|---|---|---|---|---|
| К содержимому | `preventDefault`, `focus(main-content)` | Нет | Не требуется: доступность клавиатуры | UI |
| Ассистент (House) | `setPage("home")` | Нет | Нет; подписка состояния уже активна | UI |
| Действия (Activity) | `setPage("history")` | Нет | История уже получена от `snapshot`, RAM последних 20 задач | UI |
| Инструменты (LayoutGrid) | `setPage("tools")` | Нет | Каталог уже получен от `registry.discover()` | UI |
| Подключения (Plug) | `setPage("services")` | Нет | Отображается `MailSession` из snapshot | UI |
| Память (ShieldCheck) | `setPage("memory")`, эффект `memory(read)` | Да: `memory` | `_auxiliary` → `_memory`, чтение SQLite и RAM | OK |
| Пространство (Globe) | `setPage("workspace")`; `WorkspacePanel.request(read)` | Да: `workspace` | `_workspace` → `WorkspaceStore.load`, JSON | OK |
| Настройки (Settings) | `setPage("settings")` | Нет | Отображается `state.settings` Python; настройки ниже отправляются отдельно | UI |
| Свернуть окно (Minus) | `jarvisWindow.minimize()` | Да до main; нет protocol | `BrowserWindow.minimize`, Python не требуется | Electron |
| Развернуть окно (Square) | `jarvisWindow.maximize()` | Да до main; нет protocol | `maximize/unmaximize`, Python не требуется | Electron |
| Закрыть окно (X) | `jarvisWindow.close()` | Да до main; shutdown по EOF | Electron закрывает окно, Runtime.stop → `DesktopService.shutdown` отменяет работу | Electron |
| Обработка команд: Офлайн / OpenAI, ChevronRight | `open("settings")` | Да: `open` | `handle(open)` очищает notice; переход в React | OK (навигация) |
| Последние задачи → Все, ChevronRight | `setPage("history")` | Нет | Уже полученные фактические записи | UI |
| Подключения → Открыть, ChevronRight | `setPage("services")` | Нет | Уже полученное состояние аккаунта | UI |
| Настроить Outlook, ChevronRight | `open("outlook")` | Да: `open` | Очищает notice; OAuth сам не запускается | OK (навигация) |
| Wake: Стоп / Выключить фоновый микрофон | `cancel()` | Да: `cancel` | `cancel`, `voice.cancel`, `runner.cancel`, остановка watchers; RAM | OK |
| Wake: Возобновить | `data-jarvis-wake`, обработчик isolated preload | Да: `voice_wake_resume` | Simulation проходит; execute отвергается scoped_fields и закрывает JSONL reader | DEAD в execute; OK в simulation |
| Ввести команду с клавиатуры (Keyboard) | `input.focus()` | Нет | Не требуется | UI |
| Удерживайте для голосового ввода (Mic) | `data-jarvis-hold`, trusted pointer/Space | Да: `voice_start/release/cancel` | `voice.record`; обычный transcript или `cancel_only`, когда есть worker; аудио не на диск | OK |
| Остановить задачу (X, рядом с микрофоном) | `cancel()` | Да: `cancel` | Отмена Runner/голоса/auxiliary | OK |
| Команда Jarvis / Напишите задачу… | `setCommand`; Ctrl/Cmd+Enter → `submit` | Да при Submit: `run` | `_launch_request` → `start` → Runner, PermissionEngine, audit; не демонстрация | UI → OK при отправке |
| Выполнить команду (ArrowRight), form Submit | `submit()` → `jarvis.run(command, mode, context)` | Да: `run` | Запускает настоящий Runner; результаты в snapshot, audit; история успешных задач по настройке | OK |
| Отменить задачу (Square в composer) | `cancel()` | Да: `cancel` | Отмена, не имитация завершения | OK |
| Режим: Симуляция / Выполнение | `setMode` | Да при run/mail/voice gesture | `Mode` применяется PermissionEngine; simulation не вызывает адаптеры. Сам выбор не сохраняется | UI → OK при запуске |
| Flight Recorder · детали отказа (`summary`) | Нативное раскрытие details | Нет нового запроса | Данные отказа уже пришли из Outcome.diagnostic | UI |
| Ответить на уточнение, ChevronRight | `open("task")` | Да: `open` | Диалог уже определяется `state.prompt` | OK (навигация) |
| Результат → название инструмента, ChevronRight (`summary`) | Нативное раскрытие details | Нет нового запроса | Проверенный `step.result`, не новый запуск | UI |
| Разрешения под контролем → Открыть / Показать | `open("tools"/"task")` | Да: `open` | Snapshot каталога / показ текущего точного prompt | OK (просмотр, не изменение policy) |
| К ассистенту, ChevronRight | `setPage("home")` | Нет | Не требуется | UI |
| Доступные операции → Открыть инструменты, ChevronRight | `open("tools")` на уже открытой странице | Да: `open` | Возвращает текущий каталог, только убирает notice; название вводит в заблуждение | DEAD (самопереход; нужно назвать обновлением) |
| Цветовая тема: Midnight / Graphite | `setTheme` → `html.dataset.theme` | Нет | Backend не знает, после запуска Midnight | FAKE |
| Анимация сферы | `setMoving` | Нет | Backend не знает, после запуска true | FAKE |
| Подсказка: Вставить в команду | `perform(watcher(id))` | Да: `watcher_action` | Проверяет clipboard fingerprint, выдаёт transcript для проверки; не запускает задачу | OK |
| Подсказка: Создать черновик этому адресату | `setPage(services)`, `watcher(id)` | Да: `watcher_action` | Повторно читает письмо, проверяет account/id/from/subject, RAM draft через engine | OK |
| Подсказка: Скрыть | `watcher(id, true)` | Да: `watcher_action` | `watchers.dismiss`, RAM | OK |
| Закрыть уведомление (X) | `setError("")`, локальная копия `state.notice = ""` | Нет | `service.notice` остаётся; следующий publish возвращает баннер | FAKE для notice Python; локальная ошибка закрывается корректно |
| Можно передать данные…: Разрешаю передать эту команду… | `CloudConsentCard.setCurrent` | Только при Продолжить | Неотмеченный по умолчанию одноразовый выбор; `cloud_review` привязан к id/TTL/settings | UI → OK при отправке |
| Также разрешаю передать историю прошлых задач… | `setHistory` | Только при Продолжить | Отдельное разрешение прошлого контекста; не action approval | UI → OK при отправке |
| Cloud consent: Отмена | `decideCloud(false, false)` | Да: `cloud_review` | `_respond_cloud_review` потребляет pending и отменяет задачу | OK |
| Cloud consent: Продолжить задачу | `decideCloud(current, history)` | Да: `cloud_review` | Проверяет id/TTL/settings, запускает / продолжает Runner; согласие не сохраняется | OK |
| Диалог: Ваш ответ (`textarea`) | `setAnswer` | Только при Ответить | Черновик уточнения, не approval | UI → OK при отправке |
| Диалог: Ответить | `jarvis.answer(prompt.id, answer)` | Да: `respond` | `_respond` проверяет pending clarification, разрешает его future | OK |
| Диалог: Отклонить действие | `data-jarvis-approval`, trusted click в preload | Да: `respond` | `_respond` проверяет immutable snapshot, завершает future без токена | OK |
| Диалог: Подтвердить показанное действие | `data-jarvis-approval`, trusted click | Да: `respond` | UI authority → exact single-use token после audit → PermissionEngine | OK |
| Диалог: Остановить задачу (X), Escape / onCancel | `jarvis.cancel()` | Да: `cancel` | Отменяет pending/Runner/auxiliary/voice | OK |
| Диалог: Удерживайте и скажите «стоп» (Mic) | `data-jarvis-hold` | Да: `voice_start` | При worker: запись `cancel_only`; любое другое слово не ответ/approval. При mail auxiliary: `busy`, запись не начинается | DEAD для диалога Outlook; OK для Runner, только отмена |
| Обработка команд: Офлайн / OpenAI в «Модель и голос» | `configure({...settings, provider})`, оптимистичный `setSettings` | Да: `configure` | Атомарно пишет `UiSettingsStore`, меняет `self.settings`, следующие Runner выбирают provider | OK по цепочке; FAKE-отображение при отказе записи: perform проглатывает ошибку |
| Модель Responses API | `setSettings({...settings, model})` | Только при Save; также непреднамеренно при другом configure | Черновик до сохранения. Полный snapshot настроек может унести несохранённое поле | UI; дефект изоляции изменений |
| Сохранить модель | `configure(settings)` | Да: `configure` | Валидация OpenAIProvider, `planner_model` в ui_settings.json, следующий Runner использует модель | OK; тот же дефект отображения ошибки |
| Выбрать локальную модель Vosk | `jarvis.model()` | Да: main dialog → `voice_model` | `VoskRecognizer(Path)`, warmup, путь атомарно на диск; real capture только по жесту | OK |
| Голосовой автозапуск | `configure(auto_start)` | Да: `configure` | На диск + `voice.auto_start`; transcript с достаточной уверенностью запускает Runner, без выдачи approval | OK |
| Ожидать обращение «Джарвис» (Wake Word) | `configure(wake_word)` | Да: `configure` | На диск + `voice.wake_enabled`; явно включённый локальный listener | OK |
| Завершать запись по тишине (Auto-stop on silence) | `configure(auto_stop_on_silence)` | Да: `configure` | На диск + настройка LocalRecorder | OK |
| Озвучивать итог системным голосом | `configure(speech_enabled)` | Да: `configure` | На диск + `voice.speech_enabled`, LocalSpeaker | OK |
| Подсказывать по ошибкам и иностранному тексту в буфере | `configure(watch_clipboard)` | Да: `configure` | `watchers.configure`; намеренно текущий сеанс, не на диск | OK (сеансовая настройка) |
| Проверять важные письма Outlook каждые 5 минут | `configure(watch_mail)` | Да: `configure` | SAFE outlook reads через engine; текущий сеанс | OK (сеансовая настройка) |
| Коротко озвучивать фоновые подсказки системным голосом | `configure(watch_speech)` | Да: `configure` | `watchers.configure`; текущий сеанс | OK (сеансовая настройка) |
| Приостановить фоновые проверки | `jarvis.cancel()` | Да: `cancel` | `watchers.pause`, отмена текущих операций; следующая операция может возобновить | OK |
| API-ключ ElevenLabs | `setKey`, снимает consent | Только при Сохранить ключ | Секрет в RAM формы, затем credential helper; не файл/лог | UI → OK при сохранении |
| ElevenLabs: Сохранить ключ | `eleven(set, key)`; очищает поле | Да: `eleven` | `_eleven` → bounded helper → OS credential store | OK |
| ElevenLabs: Удалить ключ | `eleven(delete)` | Да: `eleven` | Удаляет только отдельный ключ через credential helper | OK |
| Загрузить голоса и Free-лимит | `eleven(voices)` | Да: `eleven` | helper → fixed API, проверка Free/quota, возвращает voices/account | OK |
| Голос · осталось … кредитов | `setVoice`, снимает consent | При select/speak | Выбранный voice id проверяется по загруженному списку | UI → OK при отправке |
| Разрешаю одну озвучку показанного текста через ElevenLabs | `setConsent` | При select/speak | Одноразовый consent, не постоянная настройка | UI → OK при отправке |
| Прослушать пробу | `eleven(speak, voice_id, consent)` | Да: `eleven` | `_eleven` → ElevenLabsSpeaker(PREVIEW); реальные quota/сеть | OK |
| Разрешить озвучку итога | `eleven(select)`; catch возвращает молча | Да: `eleven` | Связывает voice/account/текущий итог в `cloud_voice`, RAM, одно использование | OK; UI теряет ошибку IPC |
| Озвучить итог | `jarvis.speak()` | Да: `speak_result` | Потребляет `cloud_voice` либо LocalSpeaker; не запускает запись | OK |
| Память: Обновить записи | `memory(read)`, очищает selection | Да: `memory` | `_memory` читает SQLite/ExperienceStore и session RAM | OK |
| Флажок «label: value» каждой метки | `select(ids)`, максимум 8 | При run: memory_ids | Проверяет актуальные id, отправляет только с согласием | UI → OK при запуске |
| Метка: Изменить | `edit(entry)`, заполняет форму | При Сохранить изменения | В исходном виде только черновик редактирования | UI → OK при сохранении |
| Метка: Удалить | `memory(delete, previous)` | Да: `memory` | `MemoryStore.delete`, SQLite, optimistic conflict check | OK |
| Тип метки: Профессия / Приложение / Проект / Контакт / Предпочтение | `setKind` | При Добавить / Сохранить | Валидация Hint/Entry в Python | UI → OK при сохранении |
| Название | `setLabel` | При Добавить / Сохранить | Ограниченная метка, SQLite или session RAM | UI → OK при сохранении |
| Значение | `setValue` | При Добавить / Сохранить | То же; не произвольная долговременная заметка | UI → OK при сохранении |
| Только на текущий сеанс | `setTemporary` | При Добавить | Выбор `session_add` вместо `save`; намеренно RAM | UI → OK при сохранении |
| Добавить метку / Сохранить изменения | `memory(session_add / save)` | Да: `memory` | `_memory`, SessionContext либо MemoryStore.save; SQLite по TTL | OK |
| Отменить редактирование | Сбрасывает previous/label/value | Нет | Черновик не был сохранён | UI |
| Очистить профиль | `memory(clear)` | Да: `memory` | `MemoryStore.clear`, SQLite | OK |
| Контекст сеанса → Удалить | `memory(session_delete, hint)` | Да: `memory` | `SessionContext.delete`, RAM | OK |
| Передать контекст сеанса в следующую задачу | `setSessionMemory` | При run: session_memory | `_memory_context`, выбранный текущий контекст и consent | UI → OK при запуске |
| Очистить контекст сеанса | `memory(session_clear)` | Да: `memory` | `SessionContext.clear`, RAM | OK |
| Запоминать успешные задачи и использовать их контекст | `configure(task_memory_enabled)` | Да: `configure` | На диск; меняет загрузку/включение контекста/сохранение успешных задач Runner | OK |
| Недавняя задача / Рецепт (`summary`) | Нативное details | Нет нового запроса | Данные SQLite уже пришли от `_load_experience` | UI |
| Удалить задачу / Удалить рецепт | `memory(experience_delete, record_id)` | Да: `memory` | ExperienceStore.forget, SQLite, перечитывает состояние | OK |
| Очистить задачи и рецепты | `memory(experience_clear)` | Да: `memory` | ExperienceStore.forget без id, SQLite | OK |
| Режим почтовых действий | `setMode` | В каждом `mail` | `Mode` применяется к соответствующему инструменту, не к OAuth | UI → OK при отправке |
| Microsoft Application (client) ID | `setClient`, снимает consent | При Подключить | UUID для MSAL public-client, не секрет | UI → OK при подключении |
| Разрешаю подключить выбранный аккаунт Outlook | `setConsent` | При Подключить | Явный connect consent, не разрешение отправки | UI → OK при подключении |
| Подключить аккаунт | `mail(connect, client_id, consent)` | Да: `mail` | `_connection`, MSAL+PKCE, OS credential cache, durable audit | OK |
| Отключить аккаунт | `mail(disconnect)` | Да: `mail` | Очищает account/cache/session/drafts/attachments, audit | OK |
| Показать входящие | `mail(list)` | Да: `mail` | `outlook.list` через engine, Graph в execute / simulation | OK |
| Черновики в RAM → Загрузить черновик | `mail(load_draft, id)` | Да: `mail` | Берёт текущий Message из MailSession RAM, возвращает в форму | OK |
| Тема входящего письма / Без темы | `mail(read, message_id)` | Да: `mail` | Проверка id из listing именно этого аккаунта/сеанса; `outlook.read` | OK |
| Кому (точные email через запятую) | `setTo` | При draft/save/send | Точные адреса в Message; никаких догадок по имени | UI → OK при отправке |
| Копия | `setCc` | При draft/save/send | Точные адреса Cc | UI → OK при отправке |
| Скрытая копия | `setBcc` | При draft/save/send | Точные адреса Bcc | UI → OK при отправке |
| Тема | `setSubject` | При draft/save/send | Ограниченная тема полного snapshot | UI → OK при отправке |
| Текст письма | `setBody` | При draft/save/send | Ограниченный body полного snapshot | UI → OK при отправке |
| Выбрать вложение до 32 KiB | `jarvis.attachment()` | Да: main file dialog → `mail(attach)` | Main проверяет regular file/размер/изменения; Python хранит bytes/id/hash в RAM | OK |
| Убрать вложения | `mail(clear_attachments)` | Да: `mail` | Очищает attachment store/list в RAM | OK для новых вложений; у загруженного draftAttachments UI остаётся старый список |
| Черновик в RAM | `mail(local_draft, message)` | Да: `mail` | `outlook.local_draft` через engine; только RAM, это явно указано | OK |
| Сохранить в Outlook | `mail(save_draft, message)` | Да: `mail` | `outlook.save_draft` через engine, read-back; remote storage при execute | OK |
| Подготовить отправку | `mail(send, message)` | Да: `mail` | CONFIRM exact snapshot → Graph; 202 = accepted, не delivery_verified | OK |
| Остановить операцию | `jarvis.cancel()` | Да: `cancel` | Отменяет auxiliary; выданный эффект не повторяется | OK |
| Загрузить сохранённое (RotateCw) | `WorkspacePanel.request(read)` | Да: `workspace` | WorkspaceStore.load, версия/revision возвращается | OK |
| Ваше имя | `setDraft(user.name)` | При Сохранить пространство | WorkspaceProfile JSON после валидации | UI → OK при сохранении |
| Компания | `setDraft(user.organization)` | При Сохранить пространство | То же | UI → OK при сохранении |
| Должность | `setDraft(user.role)` | При Сохранить пространство | То же | UI → OK при сохранении |
| Правила работы и типичные задачи | `setDraft(user.tasks_description)` | При Сохранить пространство | То же; контекст не становится approval | UI → OK при сохранении |
| Добавить 14 приложений Omadli Group | `request(preset, revision)` | Да: `workspace` | configure_workspace, bounded preset merge, JSON; применение после restart | OK |
| Название приложения (каждая карточка) | `editApp(name)` | При Сохранить пространство | AppEntry в workspace_profile.json | UI → OK при сохранении |
| Включить {app.name} (switch) | `editApp(enabled)` | При Сохранить пространство | Сохраняет enabled, после restart меняет доступный workspace | UI → OK при сохранении |
| URL приложения (каждая web/hybrid карточка) | `editApp(launch_url)` | При Сохранить пространство | Пересчитывает exact origin, Python валидирует policy | UI → OK при сохранении |
| Разрешить частную сеть для точного корпоративного домена | `editApp(allow_private_network)` | При Сохранить пространство | AppEntry, действует только в разрешённом workspace/policy | UI → OK при сохранении |
| Добавить приложение (Plus) | `addApp()` создаёт draft, focus имени | При Сохранить пространство | Пока не сохранено, приложения в backend нет; индикатор dirty это сообщает | UI → OK при сохранении |
| Открыть рабочий браузер | `workspaceBrowser(mode)` | Да: `workspace_browser` | Запускает managed Chrome/Edge, проверяет get_tabs через engine, пересобирает registry, сохраняет профиль | OK |
| Выбранный режим: Strict Sandbox / Live/CDP / Authenticated Workspace | `setDraft(browser_mode)` | При Сохранить пространство | Сохраняет профиль/UiSettings; restart, env имеет приоритет | UI → OK при сохранении |
| Сохранить пространство (Save), form Submit | `save` → request(save, revision, profile) | Да: `workspace` | Валидация, optimistic revision, atomic JSON; Runner после restart | OK |
| HUD: Stop | `cancel` callback | Да: `cancel` | Отменяет настоящий Runner/голос | OK |
| HUD: Открыть / Развернуть Jarvis | `jarvisWindow.restore()` | До main, нет protocol | Восстанавливает BrowserWindow; не выполнение команды | Electron |

## Все иконки и неинтерактивные компоненты

| Иконки / компонент | Где и зачем | Обработчик / связь | Классификация |
|---|---|---|---|
| House, Activity, LayoutGrid, Plug, ShieldCheck, Globe, Settings в nav | Названия семи переходов | onClick родительской кнопки, см. таблицу | UI, не мёртвые кнопки |
| Minus, Square, X в titlebar | Управление окном | onClick родительской кнопки → main | Electron |
| ChevronRight внутри кнопок | Указатель перехода | Действие у button; отдельный onClick SVG не нужен | UI / OK |
| Mic (основной и диалоговый) | Удержание | Isolated preload → main → voice_start | См. найденный разрыв Outlook |
| Keyboard, X у голосового ввода, ArrowRight / Square composer | Фокус / отмена / запуск | Действие у button/form | UI / OK |
| X в диалоге, баннере; Plus, RotateCw, Save в workspace | Подпись действия кнопки | Родительские обработчики, см. таблицу | См. notice-дефект; остальные работают |
| ShieldCheck у «Личный сеанс», заметки сеанса и карточки разрешений | Объяснение/индикатор | Не кнопка; состояние / PermissionEngine показаны текстом | Декор |
| Check / Clock3 в строках истории | Статус задачи | Данные history из Python; SVG не действие | Декор данных Python |
| Clock3 в «История пока пуста» | Empty state | Пустая history из Python | Декор данных Python |
| Mail в карточке Outlook | Идентификация сервиса | Подключение отдельной кнопкой; статус из Python | Декор данных Python |
| AudioLines у статуса голоса | Индикатор, не переключатель | voice_phase / task.phase из Python | Декор данных Python |
| Waves в composer | Маркер поля команды | Не кнопка; поле и Submit рядом | Декор |
| Check / Activity в шагах | Фактический статус Outcome | step.status из Python | Декор данных Python |
| Activity в «Нет активной задачи» | Empty state | task/steps из Python | Декор данных Python |
| ChevronRight в summary результата | Маркер раскрытия | Нативный details/summary | UI |
| CircleHelp в «Результатов ещё нет» | Empty state, не справка | Контейнер .empty без affordance кнопки | Декор |
| Globe / Mail / Monitor в каталоге инструментов | Категория инструмента | Карточка read-only; каталог из registry, не запуск по иконке | Декор данных Python |
| Unplug в empty tools / browser-notice | Недоступность runtime | Не кнопка, состояние явно подписано | Декор |
| ShieldCheck внутри PromptDialog | Указатель точного action | Не approval; работают отдельные trusted кнопки | Декор данных Python |
| Wrench | Импортирован в main.tsx, JSX не использует | На экране отсутствует; не мёртвая кнопка | Не элемент UI |
| JarvisOrb (canvas) | Процедурная сфера | Состояние от Python, анимация в renderer; управляющих элементов нет | Индикатор |
| CompactHud | Display-only corner overlay | overlay-preload status/subscribe; управление по fixed global shortcuts в main | Не содержит button/input/select/textarea |
| GhostCursor | Display-only click-through overlay | overlay preload, painted receipt → target_presented; это не approval | Не содержит управляющих элементов |

Ни одного кликабельного div в `desktop/src/` нет. `onClick` есть у ссылок/кнопок,
`onSubmit` — у форм, `onChange` — у полей. Повторяющиеся `.map()` раскрыты выше.
`DemoWorker` находится в отдельном Qt compact demo (`ui/compact_window.py`),
не импортируется Electron service и не достижим из перечисленных кнопок.

## Цепочка IPC

1. `main.tsx` / компоненты вызывают только фиксированные методы `window.jarvis`.
   Approval, hold-to-talk и wake-resume намеренно не доступны как renderer API:
   isolated preload проверяет `event.isTrusted` и `data-jarvis-*`.
2. `preload.cjs` вызывает конкретный `jarvis-app:*` / `jarvis-ui:*` канал.
3. `main.cjs` проверяет sender/mainFrame/URL, число аргументов и для защищённых
   жестов фокус/видимость/pending prompt. File dialogs остаются в main.
4. `runtime.cjs` добавляет UUID и передаёт ограниченную JSONL строку в дочерний
   `python -u -m jarvis.desktop.service --stdio`; нет общего shell/tool API.
5. `protocol.Request` проверяет strict типы, scope полей, длины и operation.
6. `DesktopService.handle` маршрутизирует запрос; `publish` / reply возвращают
   snapshot через JSONL → main event / invoke Promise → React `apply`.

`status()` в Electron читает кеш последнего Python snapshot; он не выполняет
ещё один JSONL status-запрос. `open()` — уведомление о переходе, не Qt окно.

## Критические узлы

**Модель.** `configure` уже пишет provider и planner_model атомарно через
`UiSettingsStore.save`. Следующий `start` создаёт OpenAIProvider(model), если
provider=openai. Автоматического fallback с OpenAI на OfflineProvider нет.
Открытие буквального URL — отдельный ChromeCommandProvider без облака.
При чтении действующего локального профиля найдены `provider=offline` и
`planner_model=gpt-4o`; установленный manifest совпадал с текущими assets.
Это объясняет OfflineProvider в текущем состоянии, но не доказывает историю
предыдущего клика. Возможный механизм рассинхронизации: optimistic setSettings
при проглоченной perform-ошибке; полный snapshot также мог перезаписать provider.
При запуске `JARVIS_PLANNER_MODEL` имеет приоритет над сохранённой моделью.
Ключ не проверяется/не отправляется при Save; ошибка ключа/сети у OpenAI не
превращает задачу в офлайн-команду.

**Микрофоны.** Оба действительно запускают одну функцию Python `voice.record`,
если жест доверенный и пройдены проверки. На idle основная кнопка возвращает
transcript и при opt-in может начать задачу. Во время worker основная кнопка и
кнопка диалога — только `cancel_only`: «стоп» отменяет; другие слова ничего не
подтверждают и не отвечают на уточнение. Для почтового auxiliary диалоговая
кнопка была заблокирована до voice.record. Внешний corner HUD — только индикатор.

## Исправления и проверка

Все выявленные backend-разрывы выше исправлены; исходные вердикты сохранены для
проверяемого before/after. Декор и UI-черновики не превращались в произвольные команды.

| Найденный дефект | Итоговое поведение | Проверка |
|---|---|---|
| Тема / анимация только в React | `configure({theme / motion_enabled})` → strict Settings → atomic UiSettingsStore; чтение в snapshot при запуске | Реальный Electron, reload и два полных перезапуска |
| Provider/модель могли расходиться с Python | Provider берётся только из Python; модель редактируется отдельно; частичные updates не затирают соседние поля; ошибки видны, флажки откатываются при отказе | Принудительный отказ записи в изолированном профиле; некорректная модель; restart; provider-fixture в Runner |
| OpenAI без модели | Понятная `planner_model_required`, Runner не стартует и не переключается на OfflineProvider | Python integration |
| Закрытие notice только в React | Fixed `dismissNotice` → main → runtime.request → `Request(dismiss_notice)` → `handle`; сверка точного текущего текста | Реальный IPC + защита от закрытия нового notice устаревшим запросом |
| Микрофон Outlook заблокирован auxiliary | При pending prompt разрешена запись `cancel_only`; кнопка остаётся доступна до отпускания | Реальный IPC, trusted Space/pointer; синтетические аудио/Graph/credential |
| Execute wake-resume ломал протокол | `mode=execute` разрешён ровно для `voice_wake_resume`, как для voice_start | JSONL-модель Request + DesktopService |
| Вложения загруженного draft оставались в редакторе | Удаляются и выбранные новые, и загруженные вложения; новый Message содержит `attachments=[]` | Реальный native file picker с тестовым выбором → bounded bytes → Python RAM draft → reload → remove → reload |
| Открыть инструменты внутри каталога | Названо «Обновить инструменты», получает свежий snapshot реестра | Исходный IPC open/tools сохранён |
| Молчаливый catch ElevenLabs select | Ошибка проходит общий perform и показывается пользователю | Проверка кода; сеть ElevenLabs не вызывалась |
| Открыть рабочий браузер: живой CDP отказ при обычном запуске | При подключении внешнего Chrome используется `no_defaults=True`, существующие настройки контекста не переписываются; sandbox/policy не изменены | Регрессия Live и Workspace; проверка в установленном приложении |

`runtime.cjs` уже пересылает строго названные операции с UUID по ограниченному
каналу; новый универсальный IPC не нужен. Для notice добавлены только fixed методы
в renderer/preload/main и scoped payload в Request. Approval API не расширялся.
Старые полные configure-снимки и явный reset `settings={}` совместимы с прежним
контрактом. Новые UI-переключатели отправляют непустые patch-объекты.

Машинная [опись JSX](electron-ui-inventory.json) содержит хеш каждого файла,
точную строку, tag, текст/выражения и обработчики. TypeScript AST охватывает
**10 TS/TSX файлов, 106 мест управляющих элементов и 43 места иконок**.
Шаблон `.map()` — одно место в AST, несколько элементов в интерфейсе; поэтому
это не число DOM-кнопок на одном экране. Также просмотрены CSS и неинтерактивные canvas/overlays.

Воспроизведение:

```sh
node desktop/scripts/audit-ui.mjs docs/electron-ui-inventory.json
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
node desktop/scripts/verify-ui-wiring.cjs
.venv/bin/python -m pytest -q tests/integration/test_desktop_ui_wiring.py tests/integration/test_product_setup.py tests/integration/test_desktop_service.py
```

На Windows native-gesture сценарий проверяется Python integration-командами
выше и `node --test desktop/electron/*.test.cjs`; POSIX wrapper в verify-ui-wiring
не объявляется Windows-приёмкой. Для полного набора:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m jarvis --smoke-test
.venv\Scripts\python.exe -m build
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
```

Отчёт сквозной проверки: `desktop/qa/ui-wiring-verification.json`.
Снимки: `desktop/qa/ui-wiring-settings.png`, `desktop/qa/ui-wiring-mail-prompt.png`.
Mechanical UI detector: `[]` для изменённых renderer-файлов.
Результаты проверки установленного приложения записаны ниже; hosted Windows/macOS
checks остаются отдельным обязательным gate в PR.

При обычном запуске выявлен настоящий отказ Chrome CDP:
`Browser.setDownloadBehavior: Browser context management is not supported`.
Порт/процесс/receipt были проверены и доступны; ошибка возникала при применении
Playwright default-context overrides. Для внешнего CDP включён документированный
[`no_defaults`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp-option-no-defaults).
Это сохраняет существующие настройки браузера, не меняет NetworkPolicy, consent
или PermissionEngine и не влияет на запуск собственного sandbox/persistent context.
Обхода проверки процесса или подключения к неизвестному listener нет.

Целевые проверки: 54 passed для desktop service/product setup/UI wiring;
51 passed для UI wiring/watchers/browser mode после проверки совместимости reset.
Electron window/IPC contracts: 34 passed. `verify-ui-wiring.cjs`,
`verify-voice-runtime.cjs` и `verify-wake-runtime.cjs` прошли на macOS.
Последний использует синтетический wake/audio, не реальный микрофон.
В voice QA исправлено повторное использование старых координат после перехода
в настройки: перед удержанием снова наблюдается текущая кнопка.
Один прежний browser-mode тест изолирован от реально сохранённого профиля владельца;
проверки Strict/Live, запрета неявного CDP и сокрытия ошибочного значения сохранены.

Полный локальный прогон окончательной версии: **2206 passed, 24 skipped** за 202,17 с.
Отдельно прошли **43 browser/product setup проверки**, включая CDP-регрессию.
`ruff check .`, `ruff format --check .` и `mypy` — success;
`pip check` — no broken requirements; `jarvis --smoke-test` — success (simulated);
`python -m build` — wheel и sdist собраны.
`verify-desktop.cjs` также прошёл; его проверки темы ждут Python acknowledgement,
вместо предположения о мгновенном локальном setState.

## Установленный экземпляр на этом Mac

Обновлён `/Users/whomaun/Applications/Jarvis.app` штатным
`.venv/bin/python scripts/package_desktop.py`. Установка Windows с A/B слотами
на этом хосте отсутствует; её active-slot pointer не изменялся.
`codesign --verify --deep --strict` — success.
Manifest совпал с поставкой; SHA-256 проверен для всех **22 файлов**.
SHA-256 manifest: `3c5ce438f2ccf37314383be86714fc8a3f2c1dddda9cc594d07ecbdff00cc093`.
`python-runtime.json` выбирает текущий checkout и его `.venv/bin/python`.

Обычное установленное окно проверено через native Accessibility: «Локальный движок
подключён», «READY», «Готов к работе». На странице Пространство после исправления
CDP виден статус **«Браузер подключён»**. Приложение оставлено на экране Ассистент,
без активной задачи, в симуляции, микрофон выключен. Сохранённый выбор владельца
«Офлайн» оставлен прежним; имя модели само по себе не переключает provider.
Проверка подключения Chrome не подтверждает авторизацию или работу всех сайтов.

OpenAI/Outlook/ElevenLabs cloud acceptance, реальный STT/TTS и full Windows E2E
не объявляются пройденными: для них нужны отдельно выбранные аккаунты и живое оборудование.
