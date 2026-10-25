const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("jarvisOverlay", {
  status: () => ipcRenderer.invoke("jarvis-overlay:status"),
  subscribe: (listener) => {
    const handler = (_event, state) => listener(state);
    ipcRenderer.on("jarvis-overlay:state", handler);
    return () => ipcRenderer.removeListener("jarvis-overlay:state", handler);
  },
});
ipcRenderer.on("jarvis-overlay:target", (_event, id) => {
  requestAnimationFrame(() => requestAnimationFrame(() =>
    ipcRenderer.send("jarvis-overlay:painted", id),
  ));
});
