import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RotateCw, Save } from "lucide-react";
import { readableError, type RuntimeState, type WorkspaceApp, type WorkspaceCommand,
  type WorkspaceProfile, type WorkspaceState } from "../runtime";

export function WorkspacePanel({ state, active, apply }: {
  state: RuntimeState | null; active: boolean; apply: (next: RuntimeState) => void;
}) {
  const [saved, setSaved] = useState<WorkspaceState | null>(null);
  const [draft, setDraft] = useState<WorkspaceProfile | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const loaded = useRef(false);
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved.profile);
  const disabled = pending || !state?.ready || !state.can_run;
  const request = useCallback(async (command: WorkspaceCommand) => {
    if (!window.jarvis) return;
    setPending(true);
    setError("");
    setMessage("");
    try {
      const next = await window.jarvis.workspace(command);
      if (!next.workspace) throw new Error("workspace_storage");
      apply(next);
      setSaved(next.workspace);
      setDraft(structuredClone(next.workspace.profile));
      setMessage(command.operation === "read" ? "" : "Сохранено. Перезапустите Jarvis для применения.");
    } catch (e) {
      setError(readableError(e));
    } finally { setPending(false); }
  }, [apply]);
  useEffect(() => {
    if (active && state?.ready && state.can_run && !loaded.current) {
      loaded.current = true;
      void request({ operation: "read" });
    }
  }, [active, state?.ready, state?.can_run, request]);
  function editApp(id: string, patch: Partial<WorkspaceApp>) {
    setDraft((current) => current && ({ ...current,
      apps: current.apps.map((app) => app.id === id ? { ...app, ...patch } : app),
    }));
    setMessage("");
  }
  function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft || !saved) return;
    // Keep secondary origins (e.g. Facebook) while replacing the edited launch origin.
    try {
      const apps = draft.apps.map((app) => {
        const old = saved.profile.apps.find((item) => item.id === app.id);
        if (app.launch_url === old?.launch_url) return app;
        const oldOrigin = old?.launch_url ? new URL(old.launch_url).origin : "";
        const origins = app.allowed_origins.filter((value) => value !== oldOrigin);
        if (app.launch_url) origins.push(new URL(app.launch_url).origin);
        return { ...app, allowed_origins: [...new Set(origins)] };
      });
      void request({ operation: "save", revision: saved.revision, profile: { ...draft, apps } });
    } catch { setError("Введите полный URL приложения, например https://portal.company.uz."); }
  }
  function addApp() {
    if (!draft) return;
    const id = "app_" + crypto.randomUUID().replaceAll("-", "");
    setDraft({ ...draft, apps: [...draft.apps, {
      id, name: "Новое приложение", category: "productivity", kind: "web", launch_url: null,
      allowed_origins: [], native_exe_hint: null, mcp_server_id: null, workspace_notes: "",
      default_mutation_risk: "CONFIRM", enabled: false, allow_private_network: false,
    }] });
    requestAnimationFrame(() => document.getElementById(`app-name-${id}`)?.focus());
  }
  return <section className="workspace-setup" hidden={!active} aria-label="Настройка рабочего пространства">
    <div className="page-heading">
      <div><h1>Рабочее пространство</h1><p>Ваш контекст, рабочие приложения и режим браузера.</p></div>
      <button className="outline-button" disabled={disabled} onClick={() => void request({ operation: "read" })}>
        <RotateCw aria-hidden="true" /> Загрузить сохранённое
      </button>
    </div>
    {error && <p className="workspace-feedback is-error" role="alert">{error}</p>}
    {message && <p className="workspace-feedback" role="status">{message}</p>}
    {!draft || !saved ? <p role="status">{pending ? "Загрузка профиля…" : "Загрузите сохранённый профиль, чтобы начать настройку."}</p> :
      <form className="runtime-form workspace-form" onSubmit={save}>
        <fieldset disabled={disabled}>
          <section className="glass-card workspace-section">
            <h2>Профиль и контекст работы</h2>
            <div className="workspace-identity">
              {([
                ["name", "Ваше имя"], ["organization", "Компания"], ["role", "Должность"],
              ] as const).map(([key, label]) => <label key={key}>{label}
                <input autoComplete={key === "name" ? "name" : key === "organization" ? "organization" : "organization-title"}
                  maxLength={200} value={draft.user[key]}
                  onChange={(e) => setDraft({ ...draft, user: { ...draft.user, [key]: e.target.value } })} />
              </label>)}
            </div>
            <label>Правила работы и типичные задачи
              <textarea maxLength={2048} value={draft.user.tasks_description}
                placeholder="Какие задачи вы решаете и какие правила нужно учитывать?"
                onChange={(e) => setDraft({ ...draft, user: { ...draft.user, tasks_description: e.target.value } })} />
            </label>
            <small>Контекст передаётся модели вместе с задачей только при согласии на облачный режим. Он не даёт разрешений на действия.</small>
          </section>
          <section className="glass-card workspace-section">
            <div className="card-heading"><h2>Мои приложения <span className="workspace-count">{draft.apps.length}</span></h2>
              <button type="button" className="outline-button" disabled={dirty}
                onClick={() => void request({ operation: "preset", revision: saved.revision, preset: "omadli_group" })}>
                Добавить 14 приложений Omadli Group
              </button>
            </div>
            <p className="workspace-hint">Пресет сохраняет ваши приложения и изменённые URL. Уровень рядом с приложением относится к изменениям; окончательное решение принимает движок разрешений.</p>
            {dirty && <small>Сохраните изменения перед выбором пресета.</small>}
            {!draft.apps.length && <p className="workspace-empty">Добавьте свои приложения или выберите пресет Omadli Group.</p>}
            <div className="workspace-apps">
              {draft.apps.map((app) => <article className="workspace-app" key={app.id}>
                <div className="workspace-app-heading">
                  <label className="workspace-app-name" htmlFor={`app-name-${app.id}`}>Название приложения
                    <input id={`app-name-${app.id}`} required maxLength={200} value={app.name}
                      onChange={(e) => editApp(app.id, { name: e.target.value })} />
                  </label>
                  <button type="button" role="switch" aria-checked={app.enabled}
                    aria-label={`Включить ${app.name}`} className="toggle"
                    onClick={() => editApp(app.id, { enabled: !app.enabled })}><span /></button>
                </div>
                <div className="workspace-app-meta"><span>{app.enabled ? "Включено" : "Выключено"}</span>
                  <span className="tool-risk">Изменения: {app.default_mutation_risk}</span></div>
                {app.kind === "native" ? <small>{app.id === "onepassword"
                  ? "Локальное приложение. Доступ к хранилищу 1Password запрещён; автозаполнение подтверждается отдельно."
                  : "Локальное приложение. URL не требуется."}</small> :
                  <label>URL приложения
                    <input type="url" inputMode="url" spellCheck={false} autoComplete="off" maxLength={2048}
                      required={app.enabled} placeholder="https://portal.company.uz" value={app.launch_url || ""}
                      onChange={(e) => editApp(app.id, { launch_url: e.target.value || null })} />
                  </label>}
                {app.category === "custom_erp" && <label className="check-row">
                  <input type="checkbox" checked={app.allow_private_network}
                    onChange={(e) => editApp(app.id, { allow_private_network: e.target.checked })} />
                  Разрешить частную сеть для точного корпоративного домена
                </label>}
              </article>)}
            </div>
            <button type="button" className="outline-button" disabled={draft.apps.length >= 128} onClick={addApp}>
              <Plus aria-hidden="true" /> Добавить приложение
            </button>
          </section>
          <section className="glass-card workspace-section">
            <h2>Режим браузера</h2>
            <div className="control-actions">
              <button type="button" className="outline-button" disabled={dirty} onClick={async () => {
                setPending(true); setError("");
                try {
                  const next = await window.jarvis!.workspaceBrowser(); apply(next);
                  if (next.workspace) { setSaved(next.workspace); setDraft(structuredClone(next.workspace.profile)); }
                }
                catch (error) { setError(readableError(error)); }
                finally { setPending(false); }
              }}>Открыть рабочий браузер</button>
              <span role="status">{state?.workspace_browser?.state === "connected"
                ? "Браузер подключён" : state?.workspace_browser?.state === "connecting"
                  ? "Подключение…" : "Рабочий браузер не подключён"}</span>
            </div>
            <small>Откроется отдельный профиль Chrome или Edge. Войдите в сервисы самостоятельно;
              сессии сохранятся. После подключения браузер будет восстанавливаться при запуске
              Jarvis в режиме Authenticated Workspace.</small>
            <label>Выбранный режим
              <select value={draft.browser_mode} onChange={(e) => setDraft({ ...draft,
                browser_mode: e.target.value as WorkspaceProfile["browser_mode"] })}>
                <option value="strict_sandbox">Strict Sandbox</option>
                <option value="authenticated_workspace">Authenticated Workspace (Live/CDP)</option>
              </select>
            </label>
            <p className="workspace-hint">Strict Sandbox открывает изолированные страницы. Authenticated Workspace использует локальный профиль браузера; вход и разблокировку выполняете вы. CDP доступен при доверенной настройке запуска.</p>
            <small>Сейчас в движке: {saved.active_browser_mode === "authenticated_workspace" ? "Authenticated Workspace" : saved.active_browser_mode === "live" ? "Live/CDP" : "Strict Sandbox"}.
              {saved.restart_required ? " Есть сохранённые настройки, ожидающие перезапуска." : " Настройки применены."}
              {" "}Параметры запуска JARVIS_BROWSER_MODE/CDP имеют приоритет.</small>
          </section>
          <div className="workspace-save">
            <span role="status">{pending ? "Сохранение…" : dirty ? "Есть несохранённые изменения" : "Изменений нет"}</span>
            <button type="submit" className="primary-button" disabled={!dirty}><Save aria-hidden="true" /> Сохранить пространство</button>
          </div>
        </fieldset>
      </form>}
  </section>;
}
