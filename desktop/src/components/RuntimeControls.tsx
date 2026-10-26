import { useEffect, useRef, useState } from "react";
import { Mic, ShieldCheck, X } from "lucide-react";
import { toolLabel } from "../runtime";
import type {
  MemoryEntry,
  Mode,
  RuntimeState,
  VoiceSettings,
} from "../runtime";

export type Perform = (work: Promise<RuntimeState>) => Promise<void>;

export function PromptDialog({
  state,
  perform,
}: {
  state: RuntimeState;
  perform: Perform;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [answer, setAnswer] = useState("");
  const [now, setNow] = useState(Date.now());
  const prompt = state.prompt;
  useEffect(() => {
    if (!prompt) return;
    setAnswer("");
    ref.current?.showModal();
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      ref.current?.close();
    };
  }, [prompt?.id]);
  if (!prompt) return null;
  const remaining = Math.max(0, Math.ceil(prompt.expires_at - now / 1000));
  const approval = prompt.kind === "approval";
  return (
    <dialog
      ref={ref}
      className="runtime-dialog"
      aria-labelledby="prompt-title"
      onCancel={(event) => {
        event.preventDefault();
        void perform(window.jarvis!.cancel());
      }}
    >
      <h2 id="prompt-title">
        {approval ? "Подтвердите точное действие" : "Уточните команду"}
      </h2>
      {approval ? (
        <>
          <p>
            <ShieldCheck size={18} /> {prompt.action?.tool} ·{" "}
            {prompt.action?.mode === "simulation" ? "Симуляция" : "Выполнение"}
          </p>
          <p>
            Проверьте цель, аккаунт и полное содержание. Подтверждение разрешает
            только показанное действие.
          </p>
          {prompt.action?.tool === "windows.open_chrome_url" && (
            <p>
              Адрес откроется в новой вкладке вашего Chrome с текущими аккаунтами.
              Вкладки и загрузкой страницы управляет Chrome. Jarvis передаст адрес,
              но не проверит загрузку и не прочитает страницу.
            </p>
          )}
          <pre tabIndex={0} aria-label="Точные данные действия">
            {JSON.stringify(prompt.action, null, 2)}
          </pre>
        </>
      ) : (
        <>
          <p>{prompt.question}</p>
          <label htmlFor="clarification">Ваш ответ</label>
          <textarea
            id="clarification"
            value={answer}
            maxLength={4000}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <small>Ответ уточняет задачу и не подтверждает действия.</small>
        </>
      )}
      <p role="status">
        {remaining
          ? `Осталось ${remaining} сек.`
          : "Запрос истёк. Запустите задачу заново."}
      </p>
      <div className="control-actions">
        {approval ? (
          <>
            <button
              className="outline-button"
              data-jarvis-approval={prompt.id}
              data-approved="false"
              disabled={!remaining}
            >
              Отклонить действие
            </button>
            <button
              className="primary-button"
              data-jarvis-approval={prompt.id}
              data-approved="true"
              disabled={!remaining}
            >
              Подтвердить показанное действие
            </button>
          </>
        ) : (
          <button
            className="primary-button"
            disabled={!remaining || !answer.trim()}
            onClick={() =>
              void perform(window.jarvis!.answer(prompt.id, answer))
            }
          >
            Ответить
          </button>
        )}
        <button
          className="outline-button"
          onClick={() => void perform(window.jarvis!.cancel())}
        >
          <X size={16} /> Остановить задачу
        </button>
      </div>
      <button
        className="outline-button dialog-mic"
        data-jarvis-hold
        disabled={state.voice_phase !== "idle"}
      >
        <Mic size={18} /> Удерживайте и скажите «стоп»
      </button>
      <small role="status">{state.voice_status}</small>
    </dialog>
  );
}

export function VoiceSettingsPanel({
  state,
  perform,
}: {
  state: RuntimeState;
  perform: Perform;
}) {
  const [settings, setSettings] = useState<VoiceSettings>(state.settings);
  const [key, setKey] = useState("");
  const [voice, setVoice] = useState("");
  const [consent, setConsent] = useState(false);
  useEffect(
    () => setSettings(state.settings),
    [
      state.settings.provider,
      state.settings.model,
      state.settings.auto_start,
      state.settings.speech_enabled,
      state.settings.task_memory_enabled,
      state.settings.watch_clipboard,
      state.settings.watch_mail,
      state.settings.watch_speech,
      state.settings.wake_word,
      state.settings.auto_stop_on_silence,
    ],
  );
  const busy = !state.can_run;
  async function configure(next: VoiceSettings) {
    setSettings(next);
    await perform(window.jarvis!.configure(next));
  }
  return (
    <section className="glass-card settings-card runtime-form">
      <h2>Модель и голос</h2>
      <label htmlFor="provider">Обработка команд</label>
      <select
        id="provider"
        disabled={busy}
        value={settings.provider}
        onChange={(e) =>
          void configure({
            ...settings,
            provider: e.target.value as VoiceSettings["provider"],
          })
        }
      >
        <option value="offline">Офлайн</option>
        <option value="openai">OpenAI</option>
      </select>
      {settings.provider === "openai" && (
        <>
          <label htmlFor="planner-model">Модель Responses API</label>
          <input
            id="planner-model"
            value={settings.model}
            maxLength={100}
            disabled={busy}
            onChange={(e) =>
              setSettings({ ...settings, model: e.target.value })
            }
          />
          <button
            className="outline-button"
            disabled={busy}
            onClick={() => void configure(settings)}
          >
            Сохранить модель
          </button>
          <p>
            Ключ хранится в системном хранилище. Для ввода: python -m
            jarvis.security.credentials set. Команда, наблюдения, скриншоты и
            scratchpad, профиль пользователя, приложения и playbooks отправляются
            в OpenAI после согласия на одну задачу. API
            оплачивается отдельно; store=false не исключает хранение у
            провайдера.
          </p>
        </>
      )}
      <h3>Голосовой ввод</h3>
      <p>
        Модель Vosk загружается заранее и остаётся в RAM. Микрофон включается
        при удержании кнопки или при явно включённом ожидании «Джарвис».
      </p>
      <button
        className="outline-button"
        disabled={busy}
        onClick={() => void perform(window.jarvis!.model())}
      >
        Выбрать локальную модель Vosk
      </button>
      <label className="check-row">
        <input
          type="checkbox"
          checked={settings.auto_start}
          disabled={busy}
          onChange={(e) =>
            void configure({ ...settings, auto_start: e.target.checked })
          }
        />
        Голосовой автозапуск
      </label>
      <small>
        При уверенности от 0,75 команда запускается сразу. Для OpenAI сначала
        проверьте текст и дайте согласие. Подтверждения действий запрашиваются
        отдельно.
      </small>
      {([
        ["wake_word", "Ожидать обращение «Джарвис» (Wake Word)"],
        ["auto_stop_on_silence", "Завершать запись по тишине (Auto-stop on silence)"],
      ] as const).map(([key, label]) => <label className="check-row" key={key}>
        <input type="checkbox" disabled={busy} checked={settings[key] ?? false}
          onChange={(e) => void configure({ ...settings, [key]: e.target.checked })} />
        {label}
      </label>)}
      <small>Эти настройки сохраняются. При включённом Wake Word индикатор микрофона виден;
        Stop приостанавливает ожидание. Оно не даёт согласия на облако и подтверждений действий.</small>
      <label className="check-row">
        <input
          type="checkbox"
          checked={settings.speech_enabled}
          disabled={busy}
          onChange={(e) =>
            void configure({ ...settings, speech_enabled: e.target.checked })
          }
        />
        Озвучивать итог системным голосом
      </label>
      <h3>Фоновые подсказки</h3>
      <p>
        Включаются на текущий сеанс. Буфер проверяется локально раз в 2 секунды;
        текст до 16 000 символов не сохраняется и не отправляется модели.
        Нажатие на подсказку вставляет до 3500 символов в команду для проверки.
        Почта проверяется каждые 5 минут через подключённый Outlook — это
        реальные чтения, независимо от режима задач. Важные письма: новые
        непрочитанные с высокой важностью; проверяются последние 10 входящих.
      </p>
      {([
        ["watch_clipboard", "Подсказывать по ошибкам и иностранному тексту в буфере"],
        ["watch_mail", "Проверять важные письма Outlook каждые 5 минут"],
        ["watch_speech", "Коротко озвучивать фоновые подсказки системным голосом"],
      ] as const).map(([key, label]) => (
        <label className="check-row" key={key}>
          <input type="checkbox" checked={settings[key] ?? false} disabled={busy}
            onChange={(e) => void configure({ ...settings, [key]: e.target.checked })} />
          {label}
        </label>
      ))}
      <button className="outline-button"
        disabled={busy || !(settings.watch_clipboard || settings.watch_mail)}
        onClick={() => void perform(window.jarvis!.cancel())}>
        Приостановить фоновые проверки
      </button>
      <small>Без автоматического запуска задач и отправки писем. Старые данные при включении пропускаются. Stop приостанавливает проверки до следующей операции или изменения настроек.</small>
      {state.watcher?.status && <p role="status">{state.watcher.status}</p>}
      <h3>ElevenLabs Free</h3>
      <p>
        Только текст пробы или показанного итога передаётся провайдеру. Он может
        хранить текст и аудио. Один запрос расходует Free-лимит; платные тарифы
        и overage блокируются. Запись микрофона не передаётся.
      </p>
      <label htmlFor="eleven-key">API-ключ ElevenLabs</label>
      <input
        id="eleven-key"
        type="password"
        autoComplete="off"
        maxLength={4096}
        value={key}
        disabled={busy}
        onChange={(e) => {
          setKey(e.target.value);
          setConsent(false);
        }}
      />
      <div className="control-actions">
        <button
          className="outline-button"
          disabled={busy || !key}
          onClick={() => {
            const value = key;
            setKey("");
            setConsent(false);
            void perform(
              window.jarvis!.eleven({ operation: "set", key: value }),
            );
          }}
        >
          Сохранить ключ
        </button>
        <button
          className="outline-button"
          disabled={busy}
          onClick={() => {
            setKey("");
            setConsent(false);
            void perform(window.jarvis!.eleven({ operation: "delete" }));
          }}
        >
          Удалить ключ
        </button>
        <button
          className="outline-button"
          disabled={busy}
          onClick={() => {
            setConsent(false);
            void perform(window.jarvis!.eleven({ operation: "voices" }));
          }}
        >
          Загрузить голоса и Free-лимит
        </button>
      </div>
      {state.eleven.voices && (
        <>
          <label htmlFor="eleven-voice">
            Голос · осталось {state.eleven.remaining} кредитов
          </label>
          <select
            id="eleven-voice"
            value={voice}
            disabled={busy}
            onChange={(e) => {
              setVoice(e.target.value);
              setConsent(false);
            }}
          >
            <option value="">Выберите голос</option>
            {state.eleven.voices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <p>Проба: «Здравствуйте. Я Джарвис, ваш помощник. Готов к работе.»</p>
          {state.spoken_text && <blockquote>{state.spoken_text}</blockquote>}
          <label className="check-row">
            <input
              type="checkbox"
              checked={consent}
              disabled={busy}
              onChange={(e) => setConsent(e.target.checked)}
            />
            Разрешаю одну озвучку показанного текста через ElevenLabs
          </label>
          <div className="control-actions">
            <button
              className="outline-button"
              disabled={busy || !consent || !voice}
              onClick={() => {
                setConsent(false);
                void perform(
                  window.jarvis!.eleven({
                    operation: "speak",
                    voice_id: voice,
                    consent: true,
                  }),
                );
              }}
            >
              Прослушать пробу
            </button>
            <button
              className="primary-button"
              disabled={busy || !consent || !voice || !state.spoken_text}
              onClick={async () => {
                setConsent(false);
                try {
                  const next = await window.jarvis!.eleven({
                    operation: "select",
                    voice_id: voice,
                    consent: true,
                  });
                  await perform(Promise.resolve(next));
                } catch {
                  return;
                }
              }}
            >
              Разрешить озвучку итога
            </button>
            <button
              className="outline-button"
              disabled={busy || !state.spoken_text}
              onClick={() => void perform(window.jarvis!.speak())}
            >
              Озвучить итог
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export function MemoryPanel({
  state,
  perform,
  selected,
  select,
  session,
  setSession,
}: {
  state: RuntimeState;
  perform: Perform;
  selected: string[];
  select: (ids: string[]) => void;
  session: boolean;
  setSession: (value: boolean) => void;
}) {
  const [kind, setKind] = useState("preference");
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [previous, setPrevious] = useState<MemoryEntry | null>(null);
  const [temporary, setTemporary] = useState(false);
  const busy = !state.can_run;
  const reset = () => {
    select([]);
    setSession(false);
  };
  const edit = (entry: MemoryEntry) => {
    setPrevious(entry);
    setKind(entry.kind);
    setLabel(entry.label);
    setValue(entry.value);
    setTemporary(false);
  };
  return (
    <section className="glass-card settings-card runtime-form">
      <h2>Память и контекст</h2>
      <p>
        Метки профиля добавляются вами. Выберите записи для следующей
        задачи. Профиль хранится до 30 дней, контекст сеанса — до 30 минут и
        закрытия приложения.
      </p>
      <button
        className="outline-button"
        disabled={busy}
        onClick={() => {
          reset();
          void perform(window.jarvis!.memory({ operation: "read" }));
        }}
      >
        Обновить записи
      </button>
      {state.memory_entries.map((entry) => (
        <div key={entry.id} className="memory-row">
          <label className="check-row">
            <input
              type="checkbox"
              disabled={
                busy || (!selected.includes(entry.id) && selected.length >= 8)
              }
              checked={selected.includes(entry.id)}
              onChange={(e) =>
                select(
                  e.target.checked
                    ? [...selected, entry.id]
                    : selected.filter((id) => id !== entry.id),
                )
              }
            />
            {entry.label}: {entry.value}
          </label>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => edit(entry)}
          >
            Изменить
          </button>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => {
              reset();
              void perform(
                window.jarvis!.memory({ operation: "delete", previous: entry }),
              );
            }}
          >
            Удалить
          </button>
        </div>
      ))}
      {!state.memory_entries.length && <p>Сохранённых меток пока нет.</p>}
      <label htmlFor="memory-kind">Тип метки</label>
      <select
        id="memory-kind"
        value={kind}
        disabled={busy}
        onChange={(e) => setKind(e.target.value)}
      >
        {[
          ["profession", "Профессия"],
          ["application", "Приложение"],
          ["project", "Проект"],
          ["contact", "Контакт / роль"],
          ["preference", "Предпочтение"],
        ].map(([key, name]) => (
          <option key={key} value={key}>
            {name}
          </option>
        ))}
      </select>
      <label htmlFor="memory-label">Название</label>
      <input
        id="memory-label"
        maxLength={80}
        value={label}
        disabled={busy}
        onChange={(e) => setLabel(e.target.value)}
      />
      <label htmlFor="memory-value">
        Значение {kind === "application" && "(notepad, chrome или vscode)"}
      </label>
      <input
        id="memory-value"
        maxLength={80}
        value={value}
        disabled={busy}
        onChange={(e) => setValue(e.target.value)}
      />
      <label className="check-row">
        <input
          type="checkbox"
          disabled={busy || !!previous}
          checked={temporary}
          onChange={(e) => setTemporary(e.target.checked)}
        />
        Только на текущий сеанс
      </label>
      <div className="control-actions">
        <button
          className="primary-button"
          disabled={busy || !label.trim() || !value.trim()}
          onClick={() => {
            reset();
            const hint = { kind, label, value };
            const updated = Math.floor(Date.now() / 1000);
            void perform(
              window.jarvis!.memory(
                temporary
                  ? { operation: "session_add", hint }
                  : {
                      operation: "save",
                      entry: {
                        ...hint,
                        id:
                          previous?.id ||
                          crypto.randomUUID().replaceAll("-", ""),
                        updated,
                        expires: updated + 30 * 86400,
                      },
                      previous,
                    },
              ),
            );
            setPrevious(null);
            setLabel("");
            setValue("");
          }}
        >
          {previous ? "Сохранить изменения" : "Добавить метку"}
        </button>
        {previous && (
          <button
            className="outline-button"
            onClick={() => {
              setPrevious(null);
              setLabel("");
              setValue("");
            }}
          >
            Отменить редактирование
          </button>
        )}
        <button
          className="outline-button"
          disabled={busy}
          onClick={() => {
            reset();
            void perform(window.jarvis!.memory({ operation: "clear" }));
          }}
        >
          Очистить профиль
        </button>
      </div>
      <h3>Контекст сеанса</h3>
      {state.session_memory.map((hint, i) => (
        <div key={i} className="memory-row">
          <span>
            {hint.label}: {hint.value}
          </span>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => {
              reset();
              void perform(
                window.jarvis!.memory({ operation: "session_delete", hint }),
              );
            }}
          >
            Удалить
          </button>
        </div>
      ))}
      <label className="check-row">
        <input
          type="checkbox"
          checked={session}
          disabled={busy || !state.session_memory.length}
          onChange={(e) => setSession(e.target.checked)}
        />
        Передать контекст сеанса в следующую задачу
      </label>
      <button
        className="outline-button"
        disabled={busy || !state.session_memory.length}
        onClick={() => {
          reset();
          void perform(window.jarvis!.memory({ operation: "session_clear" }));
        }}
      >
        Очистить контекст сеанса
      </button>
      <h3>Недавние задачи и рецепты</h3>
      <label className="check-row">
        <input
          type="checkbox"
          checked={state.settings.task_memory_enabled ?? true}
          disabled={busy}
          onChange={(e) => void perform(window.jarvis!.configure({
            ...state.settings, task_memory_enabled: e.target.checked,
          }))}
        />
        Запоминать успешные задачи и использовать их контекст
      </label>
      <p>
        Последние 5 успешных задач хранятся локально до 30 минут,
        до 32 рецептов — до 30 дней, включая перезапуск приложения.
        Команды и рабочие заметки могут содержать личный текст. Отключите
        запоминание перед чувствительной задачей. Выключение сохраняет записи;
        удалить их можно ниже. В OpenAI контекст уходит только после отдельного
        согласия на одну задачу.
      </p>
      {!(state.recent_tasks?.length || state.playbooks?.length) && (
        <p>Сохранённых задач и рецептов пока нет.</p>
      )}
      {state.recent_tasks?.map((item) => (
        <div className="experience-record" key={item.id}>
          <details>
            <summary>{item.command}</summary>
            <p>{item.summary}</p>
            {item.scratchpad.map((note) => (
              <p className="experience-text" key={note.key}><strong>{note.key}: </strong>{note.value}</p>
            ))}
            <p>HWND: {item.window_handles.join(", ") || "нет"}</p>
            {item.urls.map((url) => <p key={url}>{url}</p>)}
            {item.paths.map((path) => <p key={path}>{path}</p>)}
            <p>{item.tools.map(toolLabel).join(" → ")}</p>
          </details>
          <button
            className="text-button"
            disabled={busy}
            aria-label={`Удалить задачу: ${item.command}`}
            onClick={() => void perform(window.jarvis!.memory({
              operation: "experience_delete", record_id: item.id,
            }))}
          >Удалить задачу</button>
        </div>
      ))}
      {state.playbooks?.map((item) => (
        <div className="experience-record" key={item.id}>
          <details>
            <summary>Рецепт: {item.terms.join(" ")}</summary>
            <ol>{item.route.map((tool, i) => <li key={i}>{toolLabel(tool)}</li>)}</ol>
          </details>
          <button
            className="text-button"
            disabled={busy}
            aria-label={`Удалить рецепт: ${item.terms.join(" ")}`}
            onClick={() => void perform(window.jarvis!.memory({
              operation: "experience_delete", record_id: item.id,
            }))}
          >Удалить рецепт</button>
        </div>
      ))}
      <button
        className="outline-button"
        disabled={busy || !(state.recent_tasks?.length || state.playbooks?.length)}
        onClick={() => void perform(window.jarvis!.memory({ operation: "experience_clear" }))}
      >Очистить задачи и рецепты</button>
    </section>
  );
}

export function OutlookPanel({
  state,
  perform,
  mode,
  setMode,
}: {
  state: RuntimeState;
  perform: Perform;
  mode: Mode;
  setMode: (mode: Mode) => void;
}) {
  const [client, setClient] = useState("");
  const [consent, setConsent] = useState(false);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draftAttachments, setDraftAttachments] = useState<
    RuntimeState["attachments"] | null
  >(null);
  useEffect(() => {
    if (state.mail_result.state !== "loaded_draft") return;
    try {
      const message = JSON.parse(state.mail_result.data || "{}");
      setTo(message.to.join(", "));
      setCc(message.cc.join(", "));
      setBcc(message.bcc.join(", "));
      setSubject(message.subject);
      setBody(message.body);
      setDraftAttachments(message.attachments);
    } catch {
      /* The backend produces a validated Message; display remains empty on malformed data. */
    }
  }, [state.mail_result.state, state.mail_result.data]);
  useEffect(
    () => setDraftAttachments(null),
    [JSON.stringify(state.attachments)],
  );
  useEffect(() => {
    setTo("");
    setCc("");
    setBcc("");
    setSubject("");
    setBody("");
    setDraftAttachments(null);
  }, [state.account?.session]);
  const busy = !state.can_run;
  const mail = (operation: string, extra: Record<string, unknown> = {}) =>
    perform(window.jarvis!.mail({ operation, ...extra }, mode));
  const addresses = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  let rows: { id: string; subject: string }[] = [];
  if (state.mail_result.state === "listed") {
    try {
      rows = JSON.parse(state.mail_result.data || "[]");
    } catch {
      /* Finite display fallback. */
    }
  }
  return (
    <section className="glass-card settings-card runtime-form">
      <h2>Outlook</h2>
      <label htmlFor="outlook-mode">Режим почтовых действий</label>
      <select
        id="outlook-mode"
        disabled={busy}
        value={mode}
        onChange={(e) => setMode(e.target.value as Mode)}
      >
        <option value="simulation">Симуляция</option>
        <option value="execute">Выполнение</option>
      </select>
      <p>
        {state.account
          ? `Подключён: ${state.account.address}`
          : "Аккаунт не подключён."}
      </p>
      <p>
        OAuth открывает системный браузер. Токены остаются в системном
        хранилище. Отправка требует отдельного подтверждения полного письма; 202
        означает принятие запроса, доставка не подтверждена.
      </p>
      <label htmlFor="outlook-client">Microsoft Application (client) ID</label>
      <input
        id="outlook-client"
        maxLength={36}
        value={client}
        disabled={busy}
        onChange={(e) => {
          setClient(e.target.value);
          setConsent(false);
        }}
      />
      <label className="check-row">
        <input
          type="checkbox"
          checked={consent}
          disabled={busy}
          onChange={(e) => setConsent(e.target.checked)}
        />
        Разрешаю подключить выбранный аккаунт Outlook
      </label>
      <div className="control-actions">
        <button
          className="primary-button"
          disabled={busy || !consent || !client}
          onClick={() => {
            setConsent(false);
            void mail("connect", { client_id: client, consent: true });
          }}
        >
          Подключить аккаунт
        </button>
        <button
          className="outline-button"
          disabled={busy || !state.account}
          onClick={() => void mail("disconnect")}
        >
          Отключить аккаунт
        </button>
        <button
          className="outline-button"
          disabled={busy || !state.account}
          onClick={() => void mail("list")}
        >
          Показать входящие
        </button>
      </div>
      {!!state.drafts.length && (
        <>
          <label htmlFor="local-drafts">Черновики в RAM текущего сеанса</label>
          <select
            id="local-drafts"
            defaultValue=""
            disabled={busy}
            onChange={(e) => {
              if (e.target.value)
                void mail("load_draft", { draft_id: e.target.value });
              e.target.value = "";
            }}
          >
            <option value="">Загрузить черновик</option>
            {state.drafts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.subject || "Без темы"}
              </option>
            ))}
          </select>
        </>
      )}
      {rows.map((row) => (
        <button
          className="outline-button"
          disabled={busy}
          key={row.id}
          onClick={() => void mail("read", { message_id: row.id })}
        >
          {row.subject || "Без темы"}
        </button>
      ))}
      {state.mail_result.data && (
        <pre tabIndex={0}>{state.mail_result.data}</pre>
      )}
      <h3>Письмо · {mode === "execute" ? "Выполнение" : "Симуляция"}</h3>
      {[
        ["mail-to", "Кому (точные email через запятую)", to, setTo],
        ["mail-cc", "Копия", cc, setCc],
        ["mail-bcc", "Скрытая копия", bcc, setBcc],
        ["mail-subject", "Тема", subject, setSubject],
      ].map(([id, label, value, set]) => (
        <label key={id as string} htmlFor={id as string}>
          {label as string}
          <input
            id={id as string}
            maxLength={id === "mail-subject" ? 500 : 2540}
            value={value as string}
            disabled={busy}
            onChange={(e) => (set as (v: string) => void)(e.target.value)}
          />
        </label>
      ))}
      <label htmlFor="mail-body">Текст письма</label>
      <textarea
        id="mail-body"
        value={body}
        maxLength={16000}
        disabled={busy}
        onChange={(e) => setBody(e.target.value)}
      />
      {(draftAttachments || state.attachments).map((file) => (
        <small key={file.id}>
          {file.name} · {file.size} байт · SHA-256: {file.sha256}
        </small>
      ))}
      <div className="control-actions">
        <button
          className="outline-button"
          disabled={busy || !state.account}
          onClick={() => void perform(window.jarvis!.attachment())}
        >
          Выбрать вложение до 32 KiB
        </button>
        <button
          className="outline-button"
          disabled={busy || !state.attachments.length}
          onClick={() => void mail("clear_attachments")}
        >
          Убрать вложения
        </button>
        {[
          ["local_draft", "Черновик в RAM"],
          ["save_draft", "Сохранить в Outlook"],
          ["send", "Подготовить отправку"],
        ].map(([op, title]) => (
          <button
            className="primary-button"
            key={op}
            disabled={busy || !state.account || !to || !body}
            onClick={() =>
              void mail(op, {
                message: {
                  to: addresses(to),
                  cc: addresses(cc),
                  bcc: addresses(bcc),
                  subject,
                  body,
                  attachments: draftAttachments || state.attachments,
                },
              })
            }
          >
            {title}
          </button>
        ))}
      </div>
      {busy && (
        <button
          className="outline-button"
          onClick={() => void perform(window.jarvis!.cancel())}
        >
          Остановить операцию
        </button>
      )}
    </section>
  );
}
