const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("jarvisOverlay", {
  status: () => ipcRenderer.invoke("jarvis-overlay:status"),
  subscribe: (listener) => {
    const handler = (_event, state) => listener(state);
    ipcRenderer.on("jarvis-overlay:state", handler);
    return () => ipcRenderer.removeListener("jarvis-overlay:state", handler);
  },
});
contextBridge.exposeInMainWorld("jarvisGhost", {
  subscribe: (listener) => {
    const handler = (_event, state) => listener(state);
    ipcRenderer.on("jarvis-overlay:cursor", handler);
    return () => ipcRenderer.removeListener("jarvis-overlay:cursor", handler);
  },
  painted: (id) => ipcRenderer.send("jarvis-overlay:painted", id),
});
