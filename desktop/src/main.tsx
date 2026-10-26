import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { motion, MotionConfig } from "framer-motion";
import {
  Activity,
  ArrowRight,
  AudioLines,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Globe,
  House,
  Keyboard,
  LayoutGrid,
  Mail,
  Mic,
  Minus,
  Monitor,
  Plug,
  Settings,
  ShieldCheck,
  Square,
  Unplug,
  Waves,
  Wrench,
  X,
} from "lucide-react";
import { JarvisOrb } from "./components/JarvisOrb";
import { CompactHud } from "./components/CompactHud";
import { TaskHud } from "./components/TaskHud";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { CloudConsentCard } from "./components/CloudConsentCard";
import {
  PromptDialog,
  VoiceSettingsPanel,
  MemoryPanel,
  OutlookPanel,
} from "./components/RuntimeControls";
import {
  readableError,
  statusLabel,
  taskLabel,
  toolLabel,
  type Mode,
  type Panel,
  type RuntimeState,
} from "./runtime";
import "@fontsource-variable/inter";
import "./index.css";

const nav = [
  ["home", "Ассистент", House],
  ["history", "Действия", Activity],
  ["tools", "Инструменты", LayoutGrid],
  ["services", "Подключения", Plug],
  ["memory", "Память", ShieldCheck],
  ["workspace", "Пространство", Globe],
  ["settings", "Настройки", Settings],
] as const;
type Page = (typeof nav)[number][0];
const time = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
function App() {
  const [state, setState] = useState<RuntimeState | null>(null);
  const [page, setPage] = useState<Page>("home");
  const [command, setCommand] = useState("");
  const [mode, setMode] = useState<Mode>("simulation");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [offline, setOffline] = useState(!window.jarvis);
  const [moving, setMoving] = useState(true);
  const [theme, setTheme] = useState("Midnight");
  const [windowMode, setWindowMode] = useState<"normal" | "hud">("normal");
  const [memoryIds, setMemoryIds] = useState<string[]>([]);
  const [sessionMemory, setSessionMemory] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const task = state?.task;
  const working = task?.state === "working";
  const ready = !!state?.ready && !offline;
  const voiceActive = state?.voice_phase === "recording";
  useEffect(() => {
    const api = window.jarvisWindow;
    if (!api) return;
    let disposed = false;
    const unsubscribe = api.subscribeMode(setWindowMode);
    void api.mode().then((mode) => { if (!disposed) setWindowMode(mode); });
    return () => { disposed = true; unsubscribe(); };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.windowMode = windowMode;
  }, [windowMode]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const apply = useCallback((next: RuntimeState) => {
    setState(next);
    setOffline(false);
  }, []);
  const perform = useCallback(
    async (work: Promise<RuntimeState>) => {
      setError("");
      try {
        apply(await work);
      } catch (e) {
        setError(readableError(e));
      }
    },
    [apply],
  );
  useEffect(() => {
    setMemoryIds([]);
    setSessionMemory(false);
  }, [
    JSON.stringify(state?.memory_entries),
    JSON.stringify(state?.session_memory),
  ]);
  useEffect(() => {
    if (state?.cloud_review) setPage("home");
  }, [state?.cloud_review?.id]);
  useEffect(() => {
    if (page === "memory" && ready && window.jarvis)
      void perform(window.jarvis.memory({ operation: "read" }));
  }, [page, ready, perform]);
  useEffect(() => {
    const api = window.jarvis;
    if (!api) return;
    let disposed = false;
    const unsubscribe = api.subscribe((event) => {
      if (disposed) return;
      if (event.event === "state") apply(event.state);
      else if (event.event === "transcript") {
        setCommand(event.text);
        setPage("home");
        setError("");
        requestAnimationFrame(() => input.current?.focus());
      } else if (event.event === "ui_error") {
        setError(readableError(event.error));
      } else {
        setOffline(true);
        setError(readableError(event.error));
      }
    });
    api
      .status()
      .then((result) => {
        if (disposed) return;
        if (result.state) apply(result.state);
        if (result.error) {
          setOffline(true);
          setError(readableError(result.error));
        }
      })
      .catch((e) => {
        if (!disposed) {
          setOffline(true);
          setError(readableError(e));
        }
      });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [apply]);
  const open = useCallback(
    async (panel: Panel) => {
      if (!window.jarvis) return;
      setError("");
      setPage(
        (
          {
            settings: "settings",
            memory: "memory",
            outlook: "services",
            tools: "tools",
            voice: "home",
            task: "home",
            workspace: "workspace",
          } as const
        )[panel],
      );
      try {
        apply(await window.jarvis.open(panel));
      } catch (e) {
        setError(readableError(e));
      }
    },
    [apply],
  );
  const cancel = useCallback(async () => {
    if (!window.jarvis) return;
    try {
      apply(await window.jarvis.cancel());
    } catch (e) {
      setError(readableError(e));
    }
  }, [apply]);
  useEffect(() => {
    const stop = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (working || state?.voice_phase !== "idle"))
        void cancel();
    };
    window.addEventListener("keydown", stop);
    return () => window.removeEventListener("keydown", stop);
  }, [working, state?.voice_phase, cancel]);
  async function submit() {
    if (pending || working || !ready || !state?.can_run) return;
    if (!command.trim()) {
      setError("Введите команду.");
      input.current?.focus();
      return;
    }
    setError("");
    setPending(true);
    try {
      const next = await window.jarvis!.run(command.trim(), mode, {
        memory_ids: memoryIds,
        session_memory: sessionMemory,
      });
      apply(next);
      setMemoryIds([]);
      setSessionMemory(false);
      if (next.task.state === "working") setCommand("");
    } catch (e) {
      if (!String(e).includes("cloud_consent_required")) setError(readableError(e));
    } finally {
      setPending(false);
    }
  }
  async function decideCloud(current: boolean, history: boolean) {
    const review = state?.cloud_review;
    if (!review || !window.jarvis) return;
    setError("");
    setPending(true);
    try {
      const next = await window.jarvis.cloudReview({
        id: review.id, allow_current: current, allow_history: history,
      });
      apply(next);
      if (next.task.state === "working") {
        setCommand(""); setMemoryIds([]); setSessionMemory(false);
      }
    } catch (e) { setError(readableError(e)); }
    finally { setPending(false); }
  }
  const total = state?.started_count || 0;
  const successful = state?.completed_count || 0;
  const activeLabel = !ready
    ? offline
      ? "Движок недоступен"
      : "Подключение к движку…"
    : voiceActive
      ? "Идёт запись"
      : task?.phase === "approval"
        ? "Требуется подтверждение"
        : task?.phase === "clarification"
          ? "Нужно уточнение"
          : working
            ? "Выполняю задачу"
            : "Готов к работе";
  const recent = (
    <>
      {state?.history.length ? (
        <div className="history-list">
          {state.history.slice(0, page === "history" ? 20 : 4).map((item) => (
            <div key={item.id} className="history-row">
              <span
                className={`history-icon ${item.state === "finished" ? "success" : ""}`}
              >
                {item.state === "finished" ? <Check /> : <Clock3 />}
              </span>
              <span className="row-text">
                <strong title={item.command}>{item.command}</strong>
                <small>
                  {taskLabel(item.state)} ·{" "}
                  {item.mode === "execute" ? "Выполнение" : "Симуляция"}
                </small>
              </span>
              <time>{time(item.at)}</time>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty">
          <Clock3 />
          <p>История пока пуста</p>
          <small>Здесь будут фактические результаты ваших задач.</small>
        </div>
      )}
    </>
  );
  if (windowMode === "hud" && state && !state.prompt && !state.cloud_review) return (
    <MotionConfig reducedMotion="user">
      <TaskHud state={state} moving={moving} cancel={() => void cancel()}
        restore={() => void window.jarvisWindow?.restore()} />
    </MotionConfig>
  );
  return (
    <MotionConfig reducedMotion="user">
      <div className="app-shell">
        <a
          className="skip-link"
          href="#main-content"
          onClick={(event) => {
            event.preventDefault();
            document.getElementById("main-content")?.focus();
          }}
        >
          К содержимому
        </a>
        <aside className="sidebar">
          <div className="wordmark">
            <span className="brand-orbit" />
            Jarvis
          </div>
          <nav aria-label="Основная навигация">
            {nav.map(([key, label, Icon]) => (
              <motion.button
                key={key}
                title={label}
                aria-label={label}
                aria-current={page === key ? "page" : undefined}
                className={`nav-item ${page === key ? "active" : ""}`}
                whileTap={{ scale: 0.98 }}
                onClick={() => setPage(key)}
              >
                <Icon aria-hidden="true" />
                <span>{label}</span>
              </motion.button>
            ))}
          </nav>
          <div className="session-identity">
            <ShieldCheck />
            <span>
              Личный сеанс<small>На этом компьютере</small>
            </span>
          </div>
        </aside>
        <div className="workspace">
          <header className="titlebar">
            <span className="runtime-indicator">
              <i className={`dot ${ready ? "connected" : ""}`} />
              {ready
                ? "Локальный движок подключён"
                : offline
                  ? "Нет подключения к движку"
                  : "Запуск движка"}
            </span>
            <div className="window-controls">
              <button
                disabled={!window.jarvisWindow}
                aria-label="Свернуть окно"
                onClick={() => window.jarvisWindow?.minimize()}
              >
                <Minus />
              </button>
              <button
                disabled={!window.jarvisWindow}
                aria-label="Развернуть окно"
                onClick={() => window.jarvisWindow?.maximize()}
              >
                <Square />
              </button>
              <button
                disabled={!window.jarvisWindow}
                className="window-close"
                aria-label="Закрыть окно"
                onClick={() => window.jarvisWindow?.close()}
              >
                <X />
              </button>
            </div>
          </header>
          <main
            id="main-content"
            tabIndex={-1}
            className={page === "home" ? "dashboard" : "section-page"}
          >
            <WorkspacePanel state={state} active={page === "workspace"} apply={apply} />
            {page === "workspace" ? null : page === "home" ? (
              <>
                <div className="column left-column">
                  <section className="glass-card session-card">
                    <div className="card-heading">
                      <h2>Этот сеанс</h2>
                      <span>
                        {new Intl.DateTimeFormat("ru-RU", {
                          day: "numeric",
                          month: "long",
                        }).format(new Date())}
                      </span>
                    </div>
                    <div className="session-stat">
                      <span>Запущено задач</span>
                      <strong>{total}</strong>
                    </div>
                    <div className="session-stat">
                      <span>Выполнено и проверено</span>
                      <strong>{successful}</strong>
                    </div>
                    <div className="session-stat">
                      <span>Обработка команд</span>
                      <button
                        className="text-button"
                        disabled={!ready}
                        onClick={() => void open("settings")}
                      >
                        {state?.provider === "openai" ? "OpenAI" : "Офлайн"}
                        <ChevronRight />
                      </button>
                    </div>
                    <div className="session-note">
                      <ShieldCheck />
                      <p>Каждое действие проходит проверку разрешений.</p>
                    </div>
                  </section>
                  <section className="glass-card activity-card">
                    <div className="card-heading">
                      <h2>Последние задачи</h2>
                      <button
                        className="text-button"
                        onClick={() => setPage("history")}
                      >
                        Все
                        <ChevronRight />
                      </button>
                    </div>
                    {recent}
                  </section>
                  <section className="glass-card services-card">
                    <div className="card-heading">
                      <h2>Подключения</h2>
                      <button
                        className="text-button"
                        onClick={() => setPage("services")}
                      >
                        Открыть
                        <ChevronRight />
                      </button>
                    </div>
                    <div className="service-row">
                      <span className="service-icon">
                        <Mail />
                      </span>
                      <div className="row-text">
                        <strong>Outlook</strong>
                        <small>
                          {state?.outlook_connected
                            ? "Аккаунт подключён"
                            : "Аккаунт не подключён"}
                        </small>
                      </div>
                      <button
                        className="icon-button"
                        disabled={!ready}
                        aria-label="Настроить Outlook"
                        onClick={() => void open("outlook")}
                      >
                        <ChevronRight />
                      </button>
                    </div>
                  </section>
                </div>
                <section
                  className="column center-column"
                  aria-label="Ассистент"
                >
                  <div className="assistant-heading">
                    <h1>Jarvis</h1>
                    <p>
                      {voiceActive
                        ? "LISTENING..."
                        : working
                          ? "WORKING..."
                          : ready
                            ? "READY"
                            : "CONNECTING..."}
                    </p>
                  </div>
                  {state?.wake_enabled && (
                    <div className="wake-indicator" role="status">
                      <span>{state.wake_listening ? "● Микрофон включён · Жду «Джарвис»" : state.voice_background ? "● Записываю команду" : "Ожидание «Джарвис» приостановлено"}</span>
                      {state.wake_listening || state.voice_background ? (
                        <button onClick={() => void cancel()} aria-label="Выключить фоновый микрофон">Стоп</button>
                      ) : (
                        <button data-jarvis-wake disabled={!state.can_run}>Возобновить</button>
                      )}
                    </div>
                  )}
                  <div className="orb-slot">
                    <JarvisOrb
                      active={working || state?.voice_phase !== "idle"}
                      phase={state?.voice_phase || "idle"}
                      paused={!moving}
                    />
                  </div>
                  <div
                    className={`voice-state ${voiceActive ? "recording" : ""}`}
                    role="status"
                  >
                    <AudioLines />
                    <span>{activeLabel}</span>
                  </div>
                  <div className="glass-card command-context">
                    {task?.command ? (
                      <>
                        <p>{task.command}</p>
                        <small>
                          {taskLabel(task.state)} ·{" "}
                          {task.mode === "execute" ? "Выполнение" : "Симуляция"}
                        </small>
                      </>
                    ) : (
                      <>
                        <p>С чего начнём?</p>
                        <small>
                          {ready
                            ? "Введите задачу или откройте голосовой ввод."
                            : "Для выполнения команд нужен запущенный локальный движок."}
                        </small>
                      </>
                    )}
                  </div>
                  {state?.cloud_review && <CloudConsentCard
                    key={state.cloud_review.id} review={state.cloud_review}
                    busy={pending} onDecision={decideCloud} />}
                  <div className="voice-console">
                    <div className="voice-controls">
                      <button
                        className="round-button"
                        aria-label="Ввести команду с клавиатуры"
                        onClick={() => input.current?.focus()}
                      >
                        <Keyboard />
                      </button>
                      <button
                        className="microphone"
                        aria-label="Удерживайте для голосового ввода"
                        title="Удерживайте кнопку или пробел на ней. Отпустите для распознавания."
                        data-jarvis-hold
                        disabled={
                          !ready ||
                          ["speaking", "transcribing"].includes(
                            state?.voice_phase || "idle",
                          )
                        }
                      >
                        <Mic />
                      </button>
                      <button
                        className="round-button"
                        aria-label="Остановить задачу"
                        disabled={
                          !ready || (!working && state?.voice_phase === "idle")
                        }
                        onClick={() => void cancel()}
                      >
                        <X />
                      </button>
                    </div>
                    <p>Голосовой ввод по удержанию</p>
                    <small>{state?.voice_status || "Микрофон выключен."}</small>
                  </div>
                  <form
                    className="composer"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submit();
                    }}
                  >
                    <Waves aria-hidden="true" />
                    <label htmlFor="command" className="sr-only">
                      Команда Jarvis
                    </label>
                    <input
                      ref={input}
                      id="command"
                      value={command}
                      maxLength={4000}
                      onChange={(event) => {
                        setCommand(event.target.value);
                        setError("");
                      }}
                      onKeyDown={(event) => {
                        if (
                          (event.ctrlKey || event.metaKey) &&
                          event.key === "Enter"
                        ) {
                          event.preventDefault();
                          void submit();
                        }
                      }}
                      disabled={!ready || working || pending || !state?.can_run}
                      aria-invalid={!!error}
                      placeholder="Напишите задачу…"
                    />
                    <button
                      type={working ? "button" : "submit"}
                      aria-label={
                        working ? "Отменить задачу" : "Выполнить команду"
                      }
                      disabled={
                        !ready || pending || (!working && !state?.can_run)
                      }
                      onClick={working ? () => void cancel() : undefined}
                    >
                      {working ? <Square /> : <ArrowRight />}
                    </button>
                  </form>
                  {(memoryIds.length > 0 || sessionMemory) && (
                    <div className="memory-selection">
                      <p>
                        Контекст: меток профиля {memoryIds.length}
                        {sessionMemory ? ", контекст сеанса" : ""}.
                      </p>
                    </div>
                  )}
                  <div className="mode-row">
                    <label htmlFor="mode">Режим</label>
                    <select
                      id="mode"
                      value={mode}
                      disabled={
                        working || pending || state?.voice_phase !== "idle"
                      }
                      onChange={(event) => setMode(event.target.value as Mode)}
                    >
                      <option value="simulation">Симуляция</option>
                      <option value="execute">Выполнение</option>
                    </select>
                  </div>
                </section>
                <div className="column right-column">
                  <section className="glass-card task-card">
                    <div className="card-heading">
                      <h2>Ход работы</h2>
                      {working && (
                        <span className="busy-label">
                          <i className="dot connected" />
                          Выполняю
                        </span>
                      )}
                    </div>
                    {task?.steps.length ? (
                      <ol className="step-list">
                        {task.steps.map((step, i) => (
                          <li key={i}>
                            <span
                              className={`step-icon ${step.status === "SUCCESS" ? "success" : ""}`}
                            >
                              {step.status === "SUCCESS" ? (
                                <Check />
                              ) : (
                                <Activity />
                              )}
                            </span>
                            <div className="row-text">
                              <strong>{toolLabel(step.tool)}</strong>
                              <small>
                                {statusLabel(step.status)}
                                {step.error !== "none" &&
                                step.error !== "NONE" &&
                                step.error
                                  ? ` · ${step.error}`
                                  : ""}
                              </small>
                            </div>
                            <time>{time(step.at)}</time>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <div className="empty">
                        <Activity />
                        <p>
                          {working
                            ? "Планирую следующий шаг"
                            : "Нет активной задачи"}
                        </p>
                        <small>
                          {working
                            ? "Шаги появятся по событиям движка."
                            : "Действия появятся после запуска команды."}
                        </small>
                      </div>
                    )}
                    {task?.phase === "clarification" && (
                      <button
                        className="primary-button"
                        onClick={() => void open("task")}
                      >
                        Ответить на уточнение
                        <ChevronRight />
                      </button>
                    )}
                  </section>
                  <section className="glass-card result-card">
                    <div className="card-heading">
                      <h2>Результат</h2>
                      {task?.id && <span>{taskLabel(task.state)}</span>}
                    </div>
                    {task?.summary ? (
                      <div className="result-content">
                        {task.state === "finished" && task.spoken_summary && (
                          <p>{task.spoken_summary}</p>
                        )}
                        <p>{task.summary}</p>
                        {task.steps
                          .filter((step) => step.result)
                          .map((step, i) => (
                            <details key={i}>
                              <summary>
                                {toolLabel(step.tool)}
                                <ChevronRight />
                              </summary>
                              <pre>{formatResult(step.result)}</pre>
                            </details>
                          ))}
                      </div>
                    ) : (
                      <div className="empty">
                        <CircleHelp />
                        <p>Результатов ещё нет</p>
                        <small>
                          Здесь будут подтверждённые наблюдения и ошибки
                          выполнения.
                        </small>
                      </div>
                    )}
                  </section>
                  <section className="glass-card permission-card">
                    <ShieldCheck />
                    <div className="row-text">
                      <strong>
                        {task?.phase === "approval"
                          ? "Ожидается подтверждение"
                          : "Разрешения под контролем"}
                      </strong>
                      <small>
                        {task?.phase === "approval"
                          ? "Проверьте точные данные действия."
                          : "Открытие сайтов — без подтверждения."}
                      </small>
                    </div>
                    <button
                      className="outline-button"
                      disabled={!ready}
                      onClick={() =>
                        void open(task?.phase === "approval" ? "task" : "tools")
                      }
                    >
                      {task?.phase === "approval" ? "Показать" : "Открыть"}
                      <ChevronRight />
                    </button>
                  </section>
                </div>
              </>
            ) : (
              <section className="page-content">
                <div className="page-heading">
                  <div>
                    <h1>{nav.find((item) => item[0] === page)?.[1]}</h1>
                    <p>
                      {page === "history"
                        ? "Только фактические задачи текущего сеанса."
                        : page === "tools"
                          ? "Инструменты текущего движка и их ограничения."
                          : page === "services"
                            ? "Аккаунты подключаются вашим явным действием."
                            : page === "memory"
                              ? "Метки профиля, недавние задачи и сохранённые рецепты."
                              : "Оформление и параметры обработки команд."}
                    </p>
                  </div>
                  <button
                    className="outline-button"
                    onClick={() => setPage("home")}
                  >
                    К ассистенту
                    <ChevronRight />
                  </button>
                </div>
                {page === "history" && (
                  <section className="glass-card history-page-card">
                    {recent}
                  </section>
                )}
                {page === "tools" && (
                  <section className="glass-card">
                    <div className="card-heading">
                      <h2>Доступные операции</h2>
                      <button
                        className="outline-button"
                        disabled={!ready}
                        onClick={() => void open("tools")}
                      >
                        Открыть инструменты
                        <ChevronRight />
                      </button>
                    </div>
                    {state?.tools.length ? (
                      <div className="tool-list">
                        {state.tools.map((tool) => (
                          <div key={tool.name}>
                            <span className="service-icon">
                              {tool.name.startsWith("browser.") ? (
                                <Globe />
                              ) : tool.name.startsWith("outlook.") ? (
                                <Mail />
                              ) : (
                                <Monitor />
                              )}
                            </span>
                            <span className="row-text">
                              <strong>{toolLabel(tool.name)}</strong>
                              <small>{tool.description}</small>
                            </span>
                            <span className="tool-risk">
                              {tool.name.startsWith("windows.") &&
                              state.platform !== "win32"
                                ? "Требуется Windows"
                                : tool.risk === "CONFIRM"
                                  ? "С подтверждением"
                                  : tool.risk === "SAFE"
                                    ? "Без отдельного подтверждения"
                                    : tool.risk}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="empty">
                        <Unplug />
                        <p>Движок не подключён</p>
                      </div>
                    )}
                  </section>
                )}
                {page === "services" && state && (
                  <OutlookPanel
                    state={state}
                    perform={perform}
                    mode={mode}
                    setMode={setMode}
                  />
                )}
                {page === "memory" && state && (
                  <MemoryPanel
                    state={state}
                    perform={perform}
                    selected={memoryIds}
                    select={setMemoryIds}
                    session={sessionMemory}
                    setSession={setSessionMemory}
                  />
                )}
                {page === "settings" && (
                  <>
                    <section className="glass-card settings-card">
                      <h2>Оформление</h2>
                      <div className="setting-row">
                        <label htmlFor="theme">Цветовая тема</label>
                        <select
                          id="theme"
                          value={theme}
                          onChange={(event) => setTheme(event.target.value)}
                        >
                          <option value="Midnight">Midnight</option>
                          <option value="Graphite">Graphite</option>
                        </select>
                      </div>
                      <div className="setting-row">
                        <span id="motion-label">Анимация сферы</span>
                        <button
                          role="switch"
                          aria-checked={moving}
                          aria-labelledby="motion-label"
                          className="toggle"
                          onClick={() => setMoving((value) => !value)}
                        >
                          <span />
                        </button>
                      </div>
                    </section>
                    {state && (
                      <VoiceSettingsPanel state={state} perform={perform} />
                    )}
                  </>
                )}
              </section>
            )}
          </main>
          {!!state?.watcher?.notifications.length && (
            <aside className={`watcher-notices ${error || state.notice ? "with-notice" : ""}`} aria-label="Подсказки Jarvis" aria-live="polite">
              {state.watcher.notifications.map((item) => (
                <div className="watcher-notice" key={item.id}>
                  <p>{item.text}</p>
                  {item.kind === "mail" && <small>{item.subject || "Без темы"}<br />Ответить: {item.recipient}</small>}
                  <div className="control-actions">
                    <button className="outline-button" disabled={!state.can_run}
                      onClick={() => {
                        if (item.kind === "mail") setPage("services");
                        void perform(window.jarvis!.watcher(item.id));
                      }}>
                      {item.kind === "mail" ? "Создать черновик этому адресату" : "Вставить в команду"}
                    </button>
                    <button className="text-button" onClick={() => void perform(window.jarvis!.watcher(item.id, true))}>Скрыть</button>
                  </div>
                </div>
              ))}
            </aside>
          )}
          {state?.prompt && <PromptDialog state={state} perform={perform} />}
          {(error || state?.notice) && (
            <div
              className={`notice-banner ${error ? "is-error" : ""}`}
              role={error ? "alert" : "status"}
            >
              <span>{error || state?.notice}</span>
              <button
                aria-label="Закрыть уведомление"
                onClick={() => {
                  setError("");
                  if (state) setState({ ...state, notice: "" });
                }}
              >
                <X />
              </button>
            </div>
          )}
          {!window.jarvis && (
            <div className="browser-notice">
              <Unplug />
              <span>Откройте настольный Jarvis для выполнения команд.</span>
            </div>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
function formatResult(value: string) {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {window.jarvisOverlay ? <CompactHud /> : <App />}
  </React.StrictMode>,
);
