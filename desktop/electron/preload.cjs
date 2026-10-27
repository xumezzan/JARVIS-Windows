const { contextBridge, ipcRenderer } = require("electron");

// Expose only fixed, argument-free window controls; no generic IPC or OS access.
contextBridge.exposeInMainWorld("jarvisWindow", {
  minimize: () => ipcRenderer.invoke("jarvis-window:minimize"),
  maximize: () => ipcRenderer.invoke("jarvis-window:maximize"),
  close: () => ipcRenderer.invoke("jarvis-window:close"),
  isMaximized: () => ipcRenderer.invoke("jarvis-window:is-maximized"),
  mode: () => ipcRenderer.invoke("jarvis-window:mode"),
  restore: () => ipcRenderer.invoke("jarvis-window:restore"),
  subscribeMode: (listener) => {
    if (typeof listener !== "function") throw new Error("invalid_listener");
    const handler = (_event, mode) => listener(mode);
    ipcRenderer.on("jarvis-window:mode", handler);
    return () => ipcRenderer.removeListener("jarvis-window:mode", handler);
  },
});

const listeners = new Set();
ipcRenderer.on("jarvis-app:event", (_event, message) => {
  for (const listener of listeners) listener(message);
});
contextBridge.exposeInMainWorld("jarvis", {
  status: () => ipcRenderer.invoke("jarvis-app:status"),
  run: (command, mode, options = {}) =>
    ipcRenderer.invoke("jarvis-app:run", command, mode, options),
  cancel: () => ipcRenderer.invoke("jarvis-app:cancel"),
  cloudReview: (reply) => ipcRenderer.invoke("jarvis-app:cloud-review", reply),
  open: (panel) => ipcRenderer.invoke("jarvis-app:open", panel),
  configure: (settings) => ipcRenderer.invoke("jarvis-app:configure", settings),
  workspace: (command) => ipcRenderer.invoke("jarvis-app:workspace", command),
  workspaceBrowser: () => ipcRenderer.invoke("jarvis-app:workspace-browser"),
  answer: (id, answer) => ipcRenderer.invoke("jarvis-app:answer", id, answer),
  memory: (edit) => ipcRenderer.invoke("jarvis-app:memory", edit),
  mail: (command, mode) => ipcRenderer.invoke("jarvis-app:mail", command, mode),
  eleven: (command) => ipcRenderer.invoke("jarvis-app:eleven", command),
  attachment: () => ipcRenderer.invoke("jarvis-app:attachment"),
  model: () => ipcRenderer.invoke("jarvis-app:model"),
  speak: () => ipcRenderer.invoke("jarvis-app:speak"),
  watcher: (id, dismiss = false) => ipcRenderer.invoke("jarvis-app:watcher", id, dismiss),
  subscribe: (listener) => {
    if (typeof listener !== "function") throw new Error("invalid_listener");
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
});

// Observe trusted events in the isolated world. Synthetic clicks/dispatchEvent are ignored.
window.addEventListener("DOMContentLoaded", () => {
  let holding = false;
  let heldButton = null;
  const mode = () =>
    document.getElementById("mode")?.value === "execute"
      ? "execute"
      : "simulation";
  const send = (channel, ...args) =>
    ipcRenderer.invoke(channel, ...args).catch((error) => {
      for (const listener of listeners)
        listener({ event: "ui_error", error: error.message });
    });
  const start = () => {
    if (holding) return;
    holding = true;
    send("jarvis-ui:hold", "start", mode());
  };
  const end = (phase) => {
    if (!holding) return;
    holding = false;
    send("jarvis-ui:hold", phase, mode());
  };
  document.addEventListener(
    "pointerdown",
    (event) => {
      const button = event.target.closest?.("[data-jarvis-hold]");
      if (event.isTrusted && event.button === 0 && button && !button.disabled) {
        heldButton = button;
        button.setPointerCapture(event.pointerId);
        start();
      }
    },
    true,
  );
  document.addEventListener(
    "pointerup",
    (event) => {
      if (event.isTrusted) end("release");
    },
    true,
  );
  document.addEventListener(
    "pointercancel",
    (event) => {
      if (event.isTrusted) end("cancel");
    },
    true,
  );
  document.addEventListener(
    "lostpointercapture",
    (event) => {
      if (event.isTrusted) end("cancel");
    },
    true,
  );
  document.addEventListener(
    "keydown",
    (event) => {
      if (!event.isTrusted) return;
      if (event.key === "Escape") end("cancel");
      if (
        event.code === "Space" &&
        !event.repeat &&
        event.target.matches?.("[data-jarvis-hold]") &&
        !event.target.disabled
      ) {
        event.preventDefault();
        heldButton = event.target;
        start();
      }
    },
    true,
  );
  document.addEventListener(
    "keyup",
    (event) => {
      if (event.isTrusted && event.code === "Space") {
        event.preventDefault();
        end("release");
      }
    },
    true,
  );
  document.addEventListener(
    "focusout",
    (event) => {
      if (event.target === heldButton) end("cancel");
    },
    true,
  );
  window.addEventListener("blur", () => end("cancel"));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) end("cancel");
  });
  document.addEventListener(
    "click",
    (event) => {
      const wake = event.target.closest?.("[data-jarvis-wake]");
      if (event.isTrusted && wake && !wake.disabled) send("jarvis-ui:wake", mode());
      const button = event.target.closest?.("[data-jarvis-approval]");
      if (!event.isTrusted || !button || button.disabled) return;
      const id = button.dataset.jarvisApproval;
      button.disabled = true;
      send("jarvis-ui:approval", id, button.dataset.approved === "true");
    },
    true,
  );
});
