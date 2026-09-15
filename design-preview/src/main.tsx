import React, { useState, useRef, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  Home,
  MessageSquare,
  CheckCheck,
  CalendarDays,
  Grid2X2,
  Settings,
  ShieldCheck,
  ArrowUp,
  Keyboard,
  Mic,
  X,
  Globe,
  FileText,
  Code2,
  ChevronRight,
  Pause,
  Play,
  Check,
  Clock3,
  Command,
} from "lucide-react";
import { ru } from "date-fns/locale";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import "@fontsource-variable/inter";
import "./index.css";

type EventItem = {
  title: string;
  detail: string;
  time: string;
  kind: "home" | "chat" | "done";
};
const now = () =>
  new Date().toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
const navigation = [
  ["home", "Главная", Home],
  ["chat", "Чат", MessageSquare],
  ["tasks", "Задачи", CheckCheck],
  ["calendar", "Календарь", CalendarDays],
  ["apps", "Инструменты", Grid2X2],
  ["settings", "Настройки", Settings],
] as const;
function App() {
  const [active, setActive] = useState("home");
  const [theme, setTheme] = useState("Midnight");
  const [moving, setMoving] = useState(
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const [date, setDate] = useState<Date | undefined>(new Date());
  const [command, setCommand] = useState("");
  const [transcript, setTranscript] = useState("");
  const [state, setState] = useState<
    "idle" | "working" | "success" | "cancelled"
  >("idle");
  const [completed, setCompleted] = useState(0);
  const [dialog, setDialog] = useState("");
  const [error, setError] = useState("");
  const [events, setEvents] = useState<EventItem[]>([
    {
      title: "Jarvis готов",
      detail: "Начат новый сеанс",
      time: now(),
      kind: "home",
    },
  ]);
  const input = useRef<HTMLTextAreaElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  function record(title: string, detail: string, kind: EventItem["kind"]) {
    setEvents((e) => [{ title, detail, time: now(), kind }, ...e].slice(0, 30));
  }
  function submit() {
    if (state === "working") return;
    if (!command.trim() || command.length > 4000) {
      setError("Введите от 1 до 4 000 символов.");
      input.current?.focus();
      return;
    }
    setError("");
    setTranscript(command);
    setState("working");
    record("Команда принята", "Локальная демонстрация", "chat");
    timer.current = setTimeout(() => {
      setState("success");
      setCompleted((n) => n + 1);
      record("Демонстрация завершена", "Команда не исполнялась", "done");
    }, 1200);
  }
  function stop() {
    if (state !== "working") return;
    clearTimeout(timer.current);
    setState("cancelled");
    record("Демонстрация отменена", "Остановлено пользователем", "chat");
  }
  const status = {
    idle: "Готов к команде",
    working: "Выполняю демонстрацию",
    success: "Демонстрация завершена",
    cancelled: "Демонстрация отменена",
  }[state];
  function navigate(key: string) {
    setActive(key);
    if (key === "settings") {
      setDialog("Настройки и разрешения");
      return;
    }
    const target =
      key === "chat" ? input.current : document.getElementById(key);
    target?.scrollIntoView({
      behavior: moving ? "smooth" : "instant",
      block: "nearest",
    });
    if (key === "chat") input.current?.focus();
  }
  function iconAction(
    label: string,
    icon: React.ReactNode,
    action: () => void,
    disabled = false,
    cls = "",
  ) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            className={"circle-control " + cls}
            aria-label={label}
            disabled={disabled}
            onClick={action}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    );
  }
  return (
    <TooltipProvider>
      <div
        className="app-shell"
        onKeyDown={(e) => {
          if (e.key === "Escape") stop();
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
      >
        <a className="skip-link" href="#command">
          К вводу команды
        </a>
        <header className="app-header">
          <div className="wordmark">
            <span className="brand-orbit" aria-hidden="true" />
            Jarvis
          </div>
          <span className="preview-label">Дизайн-превью</span>
          <div className="header-actions">
            <Button
              variant="ghost"
              size="icon"
              aria-label={moving ? "Остановить анимацию" : "Включить анимацию"}
              onClick={() => setMoving((v) => !v)}
            >
              {moving ? <Pause /> : <Play />}
            </Button>
            <Select value={theme} onValueChange={setTheme}>
              <SelectTrigger
                aria-label="Цветовая тема"
                className="theme-picker"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Midnight">Midnight</SelectItem>
                <SelectItem value="Graphite">Graphite</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </header>
        <div className="workspace">
          <aside className="sidebar">
            <nav aria-label="Основная навигация">
              {navigation.map(([key, title, Icon]) => (
                <Button
                  key={key}
                  variant="ghost"
                  className={"nav-item " + (active === key ? "active" : "")}
                  aria-current={active === key ? "page" : undefined}
                  aria-label={title}
                  onClick={() => navigate(key)}
                >
                  <Icon aria-hidden="true" />
                  <span>{title}</span>
                </Button>
              ))}
            </nav>
            <div className="sidebar-footer">
              <span className="avatar">J</span>
              <div>
                <strong>Ваше пространство</strong>
                <p>
                  <ShieldCheck size={13} aria-hidden="true" />
                  На этом устройстве
                </p>
              </div>
            </div>
          </aside>
          <main className="dashboard" id="home">
            <div className="left-column">
              <section
                className="activity-panel"
                aria-labelledby="activity-title"
              >
                <div className="panel-heading">
                  <h2 id="activity-title">История действий</h2>
                  <span className="subtle">Этот сеанс</span>
                </div>
                <div
                  className="activity-list"
                  aria-live="polite"
                  aria-relevant="additions"
                >
                  {events.map((item, i) => {
                    const Icon =
                      item.kind === "done"
                        ? Check
                        : item.kind === "home"
                          ? Home
                          : MessageSquare;
                    return (
                      <article
                        className="activity-row"
                        key={`${item.time}-${i}`}
                      >
                        <span className={"activity-icon " + item.kind}>
                          <Icon size={20} aria-hidden="true" />
                        </span>
                        <div className="event-copy">
                          <div>
                            <h3>{item.title}</h3>
                            <time>{item.time}</time>
                          </div>
                          <p>{item.detail}</p>
                        </div>
                      </article>
                    );
                  })}
                </div>
                {events.length === 1 && (
                  <div className="history-empty">
                    <Clock3 size={20} aria-hidden="true" />
                    <p>Здесь будет история ваших команд и результатов.</p>
                  </div>
                )}
                <p className="panel-footnote">Только события текущего сеанса</p>
              </section>
              <section
                className="tools-panel"
                id="apps"
                aria-labelledby="tools-title"
              >
                <div className="panel-heading">
                  <h2 id="tools-title">Инструменты</h2>
                  <Grid2X2 size={18} className="subtle" aria-hidden="true" />
                </div>
                <p className="section-description">
                  Приложения под вашим контролем
                </p>
                <div className="tool-list">
                  {[
                    [Globe, "Браузер", "Сайты и вкладки"],
                    [FileText, "Блокнот", "Редактор Windows"],
                    [Code2, "VS Code", "Редактор кода"],
                  ].map(([Icon, title, detail]) => {
                    const Glyph = Icon as typeof Globe;
                    return (
                      <Button
                        variant="ghost"
                        className="tool-row"
                        key={title as string}
                        onClick={() => setDialog(title as string)}
                      >
                        <span className="tool-icon">
                          <Glyph aria-hidden="true" />
                        </span>
                        <span>
                          <strong>{title as string}</strong>
                          <small>{detail as string}</small>
                        </span>
                        <ChevronRight size={16} aria-hidden="true" />
                      </Button>
                    );
                  })}
                </div>
                <p className="panel-footnote">
                  <ShieldCheck size={14} aria-hidden="true" />
                  Действия проходят проверку разрешений
                </p>
              </section>
            </div>
            <section className="assistant-stage" aria-labelledby="hero-title">
              <div className="assistant-heading">
                <h1 id="hero-title">Jarvis</h1>
                <p className={"assistant-status " + state} role="status">
                  <span aria-hidden="true" />
                  {status}
                </p>
              </div>
              <div
                className={"orb-stage " + (!moving || !visible ? "paused" : "")}
                aria-hidden="true"
              >
                <img
                  className="orb-artwork"
                  src="/jarvis-orb.png"
                  alt=""
                  width="720"
                  height="720"
                />
              </div>
              <div className="voice-controls">
                {iconAction("Ввести команду", <Keyboard />, () =>
                  input.current?.focus(),
                )}
                {iconAction(
                  "Открыть голосовой ввод",
                  <Mic />,
                  () => setDialog("Голосовой ввод"),
                  false,
                  "microphone",
                )}
                {iconAction(
                  "Остановить демонстрацию",
                  <X />,
                  stop,
                  state !== "working",
                )}
              </div>
              <p className="voice-hint">
                Запись только по удержанию в приложении
              </p>
              <form
                className="composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit();
                }}
              >
                <label htmlFor="command">
                  Ваша команда <kbd>Ctrl ↵</kbd>
                </label>
                <div className="input-bar">
                  <Command size={20} aria-hidden="true" />
                  <Textarea
                    id="command"
                    ref={input}
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    placeholder="Что нужно сделать?"
                    maxLength={4001}
                    readOnly={state === "working"}
                    aria-describedby="command-hint"
                  />
                  <Button
                    type="submit"
                    size="icon"
                    disabled={state === "working"}
                    aria-label="Запустить демонстрацию"
                  >
                    <ArrowUp />
                  </Button>
                </div>
                <p id="command-hint" className={error ? "input-error" : ""}>
                  {error ||
                    "Локальная демонстрация. Приложения не запускаются."}
                </p>
              </form>
            </section>
            <div className="right-column">
              <section
                className="calendar-panel"
                id="calendar"
                aria-labelledby="calendar-title"
              >
                <div className="panel-heading">
                  <h2 id="calendar-title">Сегодня</h2>
                  <span className="subtle">
                    {new Date().toLocaleDateString("ru-RU", {
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                </div>
                <Calendar
                  mode="single"
                  selected={date}
                  onSelect={setDate}
                  locale={ru}
                  labels={{
                    labelNav: () => "Навигация календаря",
                    labelPrevious: () => "Предыдущий месяц",
                    labelNext: () => "Следующий месяц",
                    labelDayButton: (date) =>
                      date.toLocaleDateString("ru-RU", {
                        weekday: "long",
                        day: "numeric",
                        month: "long",
                        year: "numeric",
                      }),
                  }}
                  className="month-calendar"
                />
                <div className="calendar-note">
                  <CalendarDays size={18} aria-hidden="true" />
                  <p>
                    Календарь не подключён
                    <span>События появятся после подключения.</span>
                  </p>
                </div>
              </section>
              <section
                className="task-panel"
                id="tasks"
                aria-labelledby="task-title"
              >
                <div className="panel-heading">
                  <h2 id="task-title">Текущая задача</h2>
                  <Badge variant="outline">
                    {state === "working"
                      ? "В работе"
                      : state === "success"
                        ? "Готово"
                        : state === "cancelled"
                          ? "Отменено"
                          : "Нет задачи"}
                  </Badge>
                </div>
                <div className="task-content">
                  {transcript ? (
                    <>
                      <p className="transcript">{transcript}</p>
                      <p className="subtle">
                        {state === "success"
                          ? "Демонстрация завершена. Команда не исполнялась."
                          : state === "cancelled"
                            ? "Задача остановлена. Можно начать заново."
                            : "Подготавливаю демонстрацию…"}
                      </p>
                    </>
                  ) : (
                    <>
                      <span className="task-empty-icon">
                        <CheckCheck size={22} />
                      </span>
                      <p>Начните с одной команды</p>
                      <p className="subtle">
                        Её текст и результат появятся здесь.
                      </p>
                    </>
                  )}
                </div>
                <div className="session-summary">
                  <Check size={17} aria-hidden="true" />
                  Завершено демонстраций<span>{completed}</span>
                </div>
              </section>
              <section className="next-action">
                <h2>От команды к действию</h2>
                <p>
                  Планируйте задачи и проверяйте каждое действие перед
                  выполнением.
                </p>
                <Button onClick={() => setDialog("Планировщик команд")}>
                  <MessageSquare aria-hidden="true" />
                  Открыть планировщик
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setDialog("Инструменты и разрешения")}
                >
                  <ShieldCheck aria-hidden="true" />
                  Инструменты и разрешения
                </Button>
              </section>
            </div>
          </main>
        </div>
        <Dialog open={!!dialog} onOpenChange={(open) => !open && setDialog("")}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{dialog}</DialogTitle>
              <DialogDescription>
                Это браузерный макет дизайна. Эта функция доступна в настольном
                Jarvis через существующие окна планировщика и разрешений.
              </DialogDescription>
            </DialogHeader>
            <p className="dialog-copy">
              Запустите приложение командой <code>python -m jarvis</code>. Здесь
              можно проверить ввод демо-команды, остановку, календарь, навигацию
              и темы.
            </p>
            <Button onClick={() => setDialog("")}>Понятно</Button>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
