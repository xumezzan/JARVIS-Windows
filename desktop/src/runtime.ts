export type VoiceSettings = {
  provider: "offline" | "openai";
  model: string;
  auto_start: boolean;
  speech_enabled: boolean;
  task_memory_enabled: boolean;
  watch_clipboard: boolean;
  watch_mail: boolean;
  watch_speech: boolean;
  wake_word: boolean;
  auto_stop_on_silence: boolean;
};
export type Prompt = {
  id: string;
  kind: "approval" | "clarification";
  signature: string;
  expires_at: number;
  question: string;
  action: { tool: string; risk: string; mode: Mode; arguments: unknown } | null;
};
export type MemoryHint = { kind: string; label: string; value: string };
export type MemoryEntry = MemoryHint & {
  id: string;
  updated: number;
  expires: number;
};
export type Attachment = {
  id: string;
  name: string;
  size: number;
  sha256: string;
};
export type RunOptions = {
  cloud_consent?: boolean;
  memory_ids?: string[];
  session_memory?: boolean;
  memory_consent?: boolean;
  conversation_consent?: boolean;
};
export type Mode = "simulation" | "execute";
export type Panel =
  "task" | "settings" | "memory" | "outlook" | "voice" | "tools" | "workspace";
export type WorkspaceProfile = {
  user: {
    name: string; organization: string; role: string; tasks_description: string;
    language: string; email_tone: string; social_comments_tone: string;
  };
  apps: WorkspaceApp[];
  browser_mode: "strict_sandbox" | "authenticated_workspace";
};
export type WorkspaceApp = {
  id: string; name: string;
  category: "productivity" | "communication" | "finance" | "cloud_admin" | "social" | "custom_erp" | "security";
  kind: "web" | "native" | "hybrid";
  launch_url: string | null; allowed_origins: string[]; native_exe_hint: string | null;
  mcp_server_id: string | null; workspace_notes: string;
  default_mutation_risk: "SAFE" | "CONFIRM"; enabled: boolean; allow_private_network: boolean;
};
export type WorkspaceState = {
  profile: WorkspaceProfile; revision: string; restart_required: boolean; active_browser_mode: string;
};
export type WorkspaceCommand =
  | { operation: "read" }
  | { operation: "save"; revision: string; profile: WorkspaceProfile }
  | { operation: "preset"; revision: string; preset: "omadli_group" };
export type Step = {
  tool: string;
  status: string;
  error: string;
  result: string;
  at: string;
  may_have_effects: boolean;
};
export type History = {
  id: string;
  command: string;
  state: string;
  mode: Mode;
  at: string;
  steps: number;
};
export type RuntimeState = {
  workspace?: WorkspaceState | null;
  workspace_browser?: { state: "disconnected" | "connecting" | "connected"; endpoint?: string };
  ready: boolean;
  can_run: boolean;
  started_count: number;
  completed_count: number;
  platform: string;
  provider: "offline" | "openai";
  model: string;
  task: {
    id: string;
    command: string;
    state: string;
    mode: Mode;
    phase: string;
    active_tool?: string;
    summary: string;
    spoken_summary: string;
    started_at: string;
    steps: Step[];
  };
  history: History[];
  outlook_connected: boolean;
  voice_phase: string;
  voice_status: string;
  voice_background?: boolean;
  wake_enabled?: boolean;
  wake_listening?: boolean;
  wake_suspended?: boolean;
  native_window_visible: boolean;
  settings: VoiceSettings;
  prompt: Prompt | null;
  memory_entries: MemoryEntry[];
  session_memory: MemoryHint[];
  recent_tasks?: {
    id: string;
    created: number;
    command: string;
    summary: string;
    scratchpad: { key: string; value: string }[];
    tools: string[];
    window_handles: number[];
    urls: string[];
    paths: string[];
  }[];
  playbooks?: { id: string; created: number; terms: string[]; route: string[] }[];
  account: { address: string; session: string } | null;
  mail_result: { state?: string; data?: string };
  attachments: Attachment[];
  drafts: { id: string; subject: string }[];
  eleven: {
    account?: string;
    remaining?: number;
    voices?: { id: string; name: string }[];
  };
  spoken_text: string;
  watcher?: {
    status: string;
    notifications: {
      id: string; kind: string; text: string; expires_at: number;
      recipient: string; subject: string;
    }[];
  };
  notice: string;
  tools: { name: string; description: string; risk: string }[];
};
export type RuntimeEvent =
  | { event: "state"; state: RuntimeState }
  | { event: "transcript"; text: string }
  | { event: "unavailable"; error: string }
  | { event: "ui_error"; error: string };
export interface JarvisApi {
  workspace(command: WorkspaceCommand): Promise<RuntimeState>;
  workspaceBrowser(): Promise<RuntimeState>;
  watcher(id: string, dismiss?: boolean): Promise<RuntimeState>;
  status(): Promise<{ state: RuntimeState | null; error: string }>;
  run(command: string, mode: Mode, options?: RunOptions): Promise<RuntimeState>;
  cancel(): Promise<RuntimeState>;
  open(panel: Panel): Promise<RuntimeState>;
  configure(settings: VoiceSettings): Promise<RuntimeState>;
  answer(id: string, answer: string): Promise<RuntimeState>;
  memory(edit: Record<string, unknown>): Promise<RuntimeState>;
  mail(command: Record<string, unknown>, mode: Mode): Promise<RuntimeState>;
  eleven(command: Record<string, unknown>): Promise<RuntimeState>;
  attachment(): Promise<RuntimeState>;
  model(): Promise<RuntimeState>;
  speak(): Promise<RuntimeState>;
  subscribe(listener: (event: RuntimeEvent) => void): () => void;
}
export const taskLabel = (state: string) =>
  ({
    idle: "Готов к работе",
    working: "Выполняется",
    finished: "Выполнено",
    simulated: "Симуляция завершена",
    no_action: "Без действий",
    error: "Ошибка",
    cancelled: "Отменено",
    timeout: "Время истекло",
    limit: "Достигнут лимит",
  })[state] || state;
export const toolLabel = (name: string) =>
  ({
    "fs.search": "Поиск файлов",
    "fs.read_document": "Чтение документа",
    "fs.write_file": "Запись файла",
    "python.sandbox_eval": "Вычисления и таблицы",
    "system.info": "Сведения о системе",
    "browser.open": "Открытие страницы",
    "browser.navigate": "Переход по адресу",
    "browser.search": "Поиск в интернете",
    "browser.read": "Чтение страницы",
    "browser.get_tabs": "Вкладки браузера",
    "browser.close": "Закрытие вкладки",
    "browser.click": "Нажатие элемента",
    "browser.type": "Ввод на странице",
    "browser.open_workspace_app": "Открытие рабочего приложения",
    "browser.scroll": "Прокрутка страницы",
    "browser.press_key": "Нажатие клавиши в браузере",
    "browser.trigger_1password_autofill": "Автозаполнение через 1Password",
    "calendar.list_events": "Встречи Outlook",
    "calendar.create_draft_event": "Черновик встречи",
    "calendar.create_event": "Создание встречи Outlook",
    "calendar.list": "Список встреч",
    "calendar.search": "Поиск встреч",
    "calendar.get": "Чтение встречи",
    "calendar.availability": "Свободное время участников",
    "calendar.create": "Создание встречи",
    "calendar.update": "Изменение встречи",
    "calendar.cancel": "Отмена встречи",
    "files.find": "Поиск в папках",
    "files.list_folder": "Содержимое папки",
    "files.read_text": "Чтение текстового файла",
    "files.write_text": "Запись текстового файла",
    "files.rename": "Переименование файла",
    "files.recycle": "Перемещение в корзину",
    "files.open": "Открытие файла",
    "local.check": "Локальная проверка",
    "local.critical_test": "Проверка критического действия",
    "local.blocked_test": "Проверка запрещённого действия",
    "local.append_message": "Сообщение в тестовый исходящий ящик",
    "fireflies.list": "Встречи Fireflies",
    "fireflies.search": "Поиск в Fireflies",
    "fireflies.get": "Сведения о встрече Fireflies",
    "fireflies.transcript": "Расшифровка встречи",
    "asana.workspaces": "Пространства Asana",
    "asana.projects": "Проекты Asana",
    "asana.tasks": "Задачи Asana",
    "asana.task": "Чтение задачи Asana",
    "asana.create_task": "Создание задачи Asana",
    "notion.search": "Поиск в Notion",
    "notion.page": "Страница Notion",
    "notion.read": "Чтение Notion",
    "notion.create_page": "Создание страницы Notion",
    "notion.append": "Дополнение страницы Notion",
    "teams.chats": "Чаты Teams",
    "teams.messages": "Сообщения Teams",
    "teams.draft": "Черновик Teams",
    "teams.send": "Отправка сообщения Teams",
    "onedrive.files": "Файлы OneDrive",
    "onedrive.sheets": "Листы Excel",
    "onedrive.read": "Чтение Excel",
    "onedrive.write": "Запись в Excel",
    "windows.get_open_windows": "Открытые окна Windows",
    "windows.open_app": "Запуск приложения",
    "windows.open_chrome_url": "Открытие адреса в вашем Chrome",
    "windows.get_text_fields": "Поля ввода окна",
    "windows.get_calculator_number": "Результат калькулятора",
    "windows.calculator_number": "Число в калькуляторе",
    "windows.focus_app": "Выбор окна",
    "windows.type_text": "Ввод текста в окно",
    "windows.inspect_active_window": "Анализ активного окна",
    "windows.clipboard_get": "Чтение буфера обмена",
    "windows.clipboard_set": "Запись в буфер обмена",
    "windows.send_hotkey": "Нажатие сочетания клавиш",
    "windows.click_element": "Нажатие в окне",
    "windows.screenshot": "Снимок экрана",
    "memory.scratchpad_write": "Запись рабочей заметки",
    "scratchpad.set": "Запись рабочей заметки",
    "outlook.account": "Аккаунт Outlook",
    "outlook.list": "Письма Outlook",
    "outlook.read": "Чтение письма",
    "outlook.local_draft": "Локальный черновик",
    "outlook.list_messages": "Письма Outlook",
    "outlook.read_message": "Чтение письма",
    "outlook.create_local_draft": "Локальный черновик",
    "outlook.save_draft": "Черновик Outlook",
    "outlook.send": "Отправка письма",
  })[name] || (name.startsWith("mcp.") || name.startsWith("mcp_")
    ? `Инструмент MCP: ${name.replace(/^mcp[._]/, "").replace("__", " / ")}` : name);
export const statusLabel = (name: string) =>
  ({
    SUCCESS: "Проверено",
    SIMULATED: "Симуляция",
    ERROR: "Ошибка",
    DENIED: "Отклонено",
    CANCELLED: "Отменено",
    TIMEOUT: "Время истекло",
    INVALID: "Некорректный запрос",
    VERIFICATION_FAILED: "Не подтверждено",
  })[name] || name;
export function readableError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  for (const [code, message] of Object.entries({
    workspace_browser_missing: "Установите Chrome или Edge и повторите подключение.",
    workspace_browser_port_busy: "Порт 9222 занят другим процессом. Освободите его и повторите подключение.",
    workspace_browser_untrusted: "Не удалось проверить принадлежность браузера. Закройте его рабочее окно и подключите заново.",
    workspace_browser_failed: "Рабочий браузер не подключился. Проверьте его окно и повторите подключение.",
    workspace_changed: "Профиль изменён другим окном. Загрузите его заново и повторите изменения.",
    workspace_storage: "Не удалось прочитать или сохранить профиль. Проверьте доступ к папке данных.",
    workspace_invalid: "Проверьте профиль: нужен точный HTTPS URL без пароля, фрагмента или шаблона домена.",
    workspace_limit: "Профиль слишком большой. Сократите описания или число приложений.",
    cloud_consent_required:
      "Для OpenAI отметьте согласие на передачу команды и наблюдений.",
    prompt_expired: "Запрос подтверждения истёк. Запустите задачу заново.",
    prompt_changed: "Данные подтверждения изменились. Запустите задачу заново.",
    memory_changed:
      "Выбранные метки изменились или истекли. Выберите их заново.",
    memory_consent_required:
      "Разрешите передачу выбранных меток в OpenAI или снимите выбор.",
    model: "Выберите локальную модель Vosk в настройках голоса.",
    watcher_expired: "Подсказка истекла или данные изменились. Дождитесь нового события.",
    busy: "Другая задача ещё выполняется. Остановите её или дождитесь результата.",
    runtime_not_ready: "Движок ещё запускается. Дождитесь подключения.",
    runtime_disconnected: "Связь с движком потеряна. Перезапустите приложение.",
    runtime_start_failed:
      "Не удалось запустить Python-движок. Повторите установку приложения.",
    runtime_start_timeout: "Движок не запустился за отведённое время.",
    runtime_response_timeout:
      "Движок не ответил вовремя. Проверьте окно выполнения.",
    task_not_started:
      "Задача не запустилась. Проверьте сообщение движка и настройки.",
    invalid_task: "Введите команду от 1 до 4 000 символов.",
  }))
    if (text.includes(code)) return message;
  return "Не удалось выполнить запрос. Откройте настройки или перезапустите приложение.";
}
