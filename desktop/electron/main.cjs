const { app, BrowserWindow, ipcMain, session, dialog, screen, globalShortcut, clipboard } = require("electron");
const { Overlay } = require("./overlay.cjs");
const { TaskHudWindow } = require("./hud-mode.cjs");
const { clipboardSample } = require("./clipboard.cjs");
const path = require("node:path");
const fs = require("node:fs/promises");
const { constants } = require("node:fs");
const { pathToFileURL } = require("node:url");
const { Runtime, runtimeConfiguration } = require("./runtime.cjs");
const repository = path.resolve(__dirname, "../..");
const startupArgument = process.argv.find((value) => value.startsWith("--jarvis-startup-report="));
const startupReport = startupArgument?.slice("--jarvis-startup-report=".length);
let startupWritten = false;
let installedRoot;
async function writeStartupReport(state) {
  if (!startupReport || startupWritten || !state.ready || !mainWindow?.isVisible()) return;
  const allowedRoot = installedRoot || path.resolve(process.env.LOCALAPPDATA || "", "JarvisInstall");
  if (process.platform !== "win32" || !path.isAbsolute(startupReport) ||
      path.dirname(startupReport) !== allowedRoot ||
      !/^startup(?:-[a-f0-9]{32})?\.json$/.test(path.basename(startupReport)))
    throw new Error("invalid_startup_report");
  startupWritten = true;
  const temporary = startupReport + ".tmp";
  await fs.writeFile(temporary, JSON.stringify({
    pid: process.pid, ppid: process.ppid, platform: "windows", visible: true,
    surface: "electron", backend_ready: true,
  }), { flag: "wx" });
  await fs.rename(temporary, startupReport);
}
let runtime;
let quitting = false;
app.setName("Jarvis");
app.setPath("userData", path.join(app.getPath("appData"), "JarvisDesktop"));

const development = process.argv.includes("--jarvis-dev");
const rendererFile = path.join(__dirname, "..", "dist", "index.html");
const rendererUrl = development
  ? "http://127.0.0.1:5173/"
  : pathToFileURL(rendererFile).href;
let mainWindow;
let overlay;
let taskHud;

function verifiedWindow(event, args, count = 0) {
  if (
    args.length !== count ||
    !mainWindow ||
    mainWindow.isDestroyed() ||
    event.sender !== mainWindow.webContents ||
    !event.senderFrame ||
    event.senderFrame !== mainWindow.webContents.mainFrame ||
    event.senderFrame.url !== rendererUrl
  ) {
    throw new Error("Window control request rejected.");
  }
  return mainWindow;
}

ipcMain.handle("jarvis-window:minimize", (event, ...args) => {
  verifiedWindow(event, args).minimize();
});
ipcMain.handle("jarvis-window:maximize", (event, ...args) => {
  const window = verifiedWindow(event, args);
  if (window.isMaximized()) window.unmaximize();
  else window.maximize();
  return window.isMaximized();
});
ipcMain.handle("jarvis-window:close", (event, ...args) => {
  verifiedWindow(event, args).close();
});
ipcMain.handle("jarvis-window:is-maximized", (event, ...args) =>
  verifiedWindow(event, args).isMaximized(),
);
ipcMain.handle("jarvis-window:mode", (event, ...args) => {
  verifiedWindow(event, args);
  return taskHud?.mode || "normal";
});
ipcMain.handle("jarvis-window:restore", (event, ...args) => {
  verifiedWindow(event, args);
  taskHud?.restore(true, true);
});
ipcMain.handle("jarvis-ui:wake", (event, ...args) => {
  const window = verifiedWindow(event, args, 1);
  if (!window.isVisible() || !window.isFocused() || !runtime?.state?.wake_enabled ||
      !["simulation", "execute"].includes(args[0]))
    throw new Error("invalid_request");
  return runtime.request("voice_wake_resume", {mode: args[0]});
});

function protectedRequest(event, args, count, operation, fields = {}) {
  verifiedWindow(event, args, count);
  if (!runtime) throw new Error("runtime_not_ready");
  return runtime.request(operation, fields);
}
ipcMain.handle("jarvis-app:status", (event, ...args) => {
  verifiedWindow(event, args);
  return { state: runtime?.state || null, error: runtime?.failure || "" };
});
ipcMain.handle("jarvis-app:run", (event, ...args) => {
  verifiedWindow(event, args, 3);
  const [command, mode, options] = args;
  if (
    typeof command !== "string" ||
    !command.trim() ||
    command.length > 4000 ||
    !["simulation", "execute"].includes(mode)
  )
    throw new Error("invalid_task");
  if (!options || typeof options !== "object" || Array.isArray(options))
    throw new Error("invalid_task");
  const allowed = [
    "cloud_consent",
    "memory_ids",
    "session_memory",
    "memory_consent",
    "conversation_consent",
  ];
  if (Object.keys(options).some((key) => !allowed.includes(key)))
    throw new Error("invalid_task");
  return protectedRequest(event, args, 3, "run", {
    command,
    mode,
    cloud_consent: options.cloud_consent ?? false,
    memory_ids: options.memory_ids ?? [],
    session_memory: options.session_memory ?? false,
    memory_consent: options.memory_consent ?? false,
    conversation_consent: options.conversation_consent ?? false,
  });
});
ipcMain.handle("jarvis-app:cancel", (event, ...args) =>
  protectedRequest(event, args, 0, "cancel"),
);
ipcMain.handle("jarvis-app:workspace-browser", (event, ...args) =>
  protectedRequest(event, args, 0, "workspace_browser"),
);
ipcMain.handle("jarvis-app:open", (event, ...args) => {
  verifiedWindow(event, args, 1);
  const panel = args[0];
  if (
    !["task", "settings", "memory", "outlook", "voice", "tools", "workspace"].includes(panel)
  )
    throw new Error("invalid_panel");
  return protectedRequest(event, args, 1, "open", { panel });
});

// Fixed UI operations, all revalidated by the Python schema. No generic tool, shell or path API.
for (const [channel, operation, field] of [
  ["configure", "configure", "settings"],
  ["memory", "memory", "memory"],
  ["eleven", "eleven", "eleven"],
  ["workspace", "workspace", "workspace"],
  ["cloud-review", "cloud_review", "cloud_review"],
]) {
  ipcMain.handle(`jarvis-app:${channel}`, (event, ...args) => {
    verifiedWindow(event, args, 1);
    // Keep a large settings form inside Python's bounded JSONL request reader.
    if (channel === "workspace" && Buffer.byteLength(JSON.stringify(args[0]) || "") > 130000)
      throw new Error("workspace_limit");
    return protectedRequest(event, args, 1, operation, { [field]: args[0] });
  });
}
ipcMain.handle("jarvis-app:watcher", (event, ...args) => {
  verifiedWindow(event, args, 2);
  const [id, dismiss] = args;
  if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id) || typeof dismiss !== "boolean")
    throw new Error("invalid_request");
  return protectedRequest(event, args, 2, "watcher_action", { notification_id: id, dismiss });
});
ipcMain.handle("jarvis-app:answer", (event, ...args) => {
  verifiedWindow(event, args, 2);
  const prompt = runtime?.state?.prompt;
  const [id, answer] = args;
  if (
    !prompt ||
    prompt.kind !== "clarification" ||
    prompt.id !== id ||
    typeof answer !== "string" ||
    answer.length > 4000
  )
    throw new Error("prompt_expired");
  return protectedRequest(event, args, 2, "respond", {
    reply: { prompt_id: id, answer },
  });
});
// Only the isolated preload's trusted DOM gesture can invoke this channel. It is not exposed.
ipcMain.handle("jarvis-ui:approval", (event, ...args) => {
  const window = verifiedWindow(event, args, 2);
  const [id, approved] = args;
  const prompt = runtime?.state?.prompt;
  if (
    !window.isFocused() ||
    !window.isVisible() ||
    !prompt ||
    prompt.kind !== "approval" ||
    prompt.id !== id ||
    typeof approved !== "boolean" ||
    Date.now() / 1000 >= prompt.expires_at
  )
    throw new Error("prompt_expired");
  return runtime.request("respond", {
    reply: { prompt_id: id, signature: prompt.signature, approved },
  });
});
let held = false;
ipcMain.handle("jarvis-ui:hold", (event, ...args) => {
  const window = verifiedWindow(event, args, 2);
  const [phase, mode] = args;
  if (
    !["start", "release", "cancel"].includes(phase) ||
    !["simulation", "execute"].includes(mode)
  )
    throw new Error("invalid_gesture");
  if (phase === "start") {
    if (held || !window.isFocused() || !window.isVisible())
      throw new Error("invalid_gesture");
    held = true;
    return runtime.request("voice_start", { mode });
  }
  if (!held) return runtime.state;
  held = false;
  return runtime.request(
    phase === "release" ? "voice_release" : "voice_cancel",
  );
});
ipcMain.handle("jarvis-app:speak", (event, ...args) =>
  protectedRequest(event, args, 0, "speak_result"),
);
ipcMain.handle("jarvis-app:mail", (event, ...args) => {
  verifiedWindow(event, args, 2);
  const [mail, mode] = args;
  if (
    !mail ||
    ![
      "connect",
      "disconnect",
      "list",
      "read",
      "local_draft",
      "save_draft",
      "send",
      "clear_attachments",
      "load_draft",
    ].includes(mail.operation)
  )
    throw new Error("invalid_mail");
  return protectedRequest(event, args, 2, "mail", { mail, mode });
});
ipcMain.handle("jarvis-app:attachment", async (event, ...args) => {
  const window = verifiedWindow(event, args);
  const result = await dialog.showOpenDialog(window, {
    title: "Выберите вложение до 32 KiB",
    properties: ["openFile"],
  });
  if (result.canceled || result.filePaths.length !== 1) return runtime.state;
  const filePath = result.filePaths[0];
  const before = await fs.lstat(filePath);
  if (!before.isFile() || before.isSymbolicLink() || before.size > 32768)
    throw new Error("attachment_limit");
  const file = await fs.open(
    filePath,
    constants.O_RDONLY | (constants.O_NOFOLLOW || 0),
  );
  try {
    const stat = await file.stat();
    if (
      !stat.isFile() ||
      stat.ino !== before.ino ||
      stat.dev !== before.dev ||
      stat.size > 32768
    )
      throw new Error("attachment_changed");
    const bytes = Buffer.alloc(32769);
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
    const after = await file.stat();
    if (
      bytesRead !== stat.size ||
      bytesRead > 32768 ||
      after.size !== stat.size ||
      after.mtimeMs !== stat.mtimeMs
    )
      throw new Error("attachment_changed");
    return runtime.request("mail", {
      mail: {
        operation: "attach",
        name: path.basename(filePath),
        content: bytes.subarray(0, bytesRead).toString("base64"),
      },
    });
  } finally {
    await file.close();
  }
});
ipcMain.handle("jarvis-app:model", async (event, ...args) => {
  const window = verifiedWindow(event, args);
  const result = await dialog.showOpenDialog(window, {
    title: "Локальная модель Vosk",
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length !== 1) return runtime.state;
  return runtime.request("voice_model", { model_path: result.filePaths[0] });
});

async function createWindow() {
  const rendererSession = session.fromPartition("jarvis-desktop");
  rendererSession.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  rendererSession.setPermissionCheckHandler(() => false);
  rendererSession.webRequest.onHeadersReceived((details, callback) => {
    const scriptPolicy = development ? "'self' 'unsafe-inline'" : "'self'";
    const connectionPolicy = development
      ? "http://127.0.0.1:5173 ws://127.0.0.1:5173"
      : "'none'";
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [
          `default-src 'self'; script-src ${scriptPolicy}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src ${connectionPolicy}; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none';`,
        ],
      },
    });
  });

  mainWindow = new BrowserWindow({
    width: 1536,
    height: 1024,
    useContentSize: true,
    enableLargerThanScreen: true,
    minWidth: 1024,
    minHeight: 720,
    frame: false,
    show: false,
    backgroundColor: "#060c12",
    title: "Jarvis",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      partition: "jarvis-desktop",
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      devTools: development,
      spellcheck: false,
    },
  });
  mainWindow.on("blur", () => {
    held = false;
    if (!runtime?.state?.voice_background && ["recording", "transcribing"].includes(runtime?.state?.voice_phase))
      void runtime.request("voice_cancel").catch(() => {});
  });
  mainWindow.webContents.on("render-process-gone", () => {
    held = false;
    if (runtime?.state) void runtime.request("cancel").catch(() => {});
  });
  mainWindow.setMenu(null);
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event) => event.preventDefault());
  mainWindow.webContents.on("will-redirect", (event) => event.preventDefault());
  mainWindow.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    taskHud?.dispose();
    mainWindow = undefined;
  });

  if (development) await mainWindow.loadURL(rendererUrl);
  else await mainWindow.loadFile(rendererFile);
  taskHud = new TaskHudWindow(mainWindow, screen,
    (mode) => mainWindow?.webContents.send("jarvis-window:mode", mode));
  overlay = new Overlay({ BrowserWindow, screen, ipcMain, globalShortcut }, mainWindow, rendererUrl, {taskHud: true});
  await overlay.ready;
  overlay.onCancel = () => { void runtime?.request("cancel").catch(() => {}); };
  overlay.onRestore = () => taskHud?.restore(true, true);
  const { root: backendRoot, python: backendPython, installed } =
    runtimeConfiguration(repository, app.getAppPath(), app.isPackaged);
  if (installed) installedRoot = path.resolve(backendRoot, "../..");
  runtime = new Runtime(backendRoot, backendPython, (message) => {
    if (message.event === "clipboard_poll") {
      // Main-only sampling: never expose clipboard content to a renderer automatically.
      void (async () => {
        const text = await clipboardSample(clipboard,
          runtime?.state?.settings?.watch_clipboard && !quitting);
        if (runtime?.state && !quitting)
          await runtime.request("clipboard_sample", {
            sample_id: message.id,
            clipboard_text: runtime.state.settings?.watch_clipboard ? text : "",
          });
      })().catch(() => {});
      return;
    }
    if (message.event === "target") {
      const taskId = runtime.state?.task?.id;
      void overlay.highlight(message)
        .then(() => runtime.request("target_presented", { target_id: message.id }))
        .catch(() => {
          if (runtime?.state?.task?.id === taskId)
            void runtime.request("cancel").catch(() => {});
        });
      return;
    }
    if (message.event === "state") {
      taskHud.update(message.state);
      overlay.update(message.state);
      void writeStartupReport(message.state).catch(() => {
        dialog.showErrorBox("Jarvis", "Не удалось записать отчёт запуска. Проверьте установку.");
      });
    } else if (message.event === "unavailable") {
      taskHud.update(null);
      overlay.update(null);
    }
    if (mainWindow && !mainWindow.isDestroyed())
      mainWindow.webContents.send("jarvis-app:event", message);
  });

}

app
  .whenReady()
  .then(createWindow)
  .catch((error) => {
    console.error("Jarvis desktop failed to start:", error.message);
    app.exit(1);
  });
app.on("before-quit", (event) => {
  if (quitting || !runtime) return;
  event.preventDefault();
  quitting = true;
  globalShortcut.unregisterAll();
  overlay?.dispose();
  taskHud?.dispose();
  runtime.stop().finally(() => app.quit());
});
app.on("window-all-closed", () => app.quit());
