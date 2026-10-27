const path = require("node:path");

// Owned display-only windows. No task/approval/voice APIs are exposed to them.
class Overlay {
  constructor({ BrowserWindow, screen, ipcMain, globalShortcut }, main, url, {taskHud = false} = {}) {
    this.main = main;
    this.screen = screen;
    this.state = null;
    this.taskHud = taskHud;
    this.active = false;
    this.hintActive = false;
    this.restoredTask = "";
    this.pendingPaint = null;
    const options = {
      frame: false, transparent: true, backgroundColor: "#00000000",
      show: false, alwaysOnTop: true, focusable: false, skipTaskbar: true,
      resizable: false, maximizable: false, minimizable: false, hasShadow: false,
      webPreferences: {
        preload: path.join(__dirname, "overlay-preload.cjs"),
        partition: "jarvis-desktop", contextIsolation: true, nodeIntegration: false,
        sandbox: true, webSecurity: true, backgroundThrottling: false,
      },
    };
    this.hud = new BrowserWindow({ ...options, width: 320, height: 110, title: "Jarvis HUD" });
    this.target = new BrowserWindow({ ...options, width: 1, height: 1, title: "Jarvis Target" });
    for (const window of [this.hud, this.target]) {
      window.setIgnoreMouseEvents(true, { forward: true });
      window.setAlwaysOnTop(true, "screen-saver");
      window.setMenu(null);
      window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      for (const name of ["will-navigate", "will-redirect", "will-attach-webview"])
        window.webContents.on(name, (event) => event.preventDefault());
      window.webContents.on("render-process-gone", () => this.restore());
    }
    ipcMain.handle("jarvis-overlay:status", (event) => {
      if (event.sender !== this.hud.webContents || event.senderFrame !== this.hud.webContents.mainFrame)
        throw new Error("overlay_request_rejected");
      return this.presentation();
    });
    ipcMain.on("jarvis-overlay:painted", (event, id) => {
      if (event.sender === this.target.webContents &&
          event.senderFrame === this.target.webContents.mainFrame &&
          id === this.pendingPaint?.id) this.pendingPaint.resolve();
    });
    this.ready = Promise.all([
      this.hud.loadURL(`${url}#hud`),
      this.target.loadFile(path.join(__dirname, "target.html")),
    ]);
    // Hiding the workbench is safe only when both escape routes are registered.
    const restore = globalShortcut.register("CommandOrControl+Alt+J", () => this.restore(true));
    const cancel = globalShortcut.register("CommandOrControl+Alt+Escape", () => {
      this.onCancel?.();
      this.restore(true);
    });
    this.shortcuts = restore && cancel;
    this.reposition = () => { if (this.active || this.hintActive) this.position(); };
    for (const name of ["display-metrics-changed", "display-added", "display-removed"])
      screen.on(name, this.reposition);
    main.on("show", () => { if (this.active) this.restore(); });
    main.on("closed", () => this.dispose());
  }
  presentation() {
    const task = this.state?.task;
    if (task?.state !== "working" && (this.state?.wake_listening || this.state?.voice_background))
      return {command: this.state.voice_status, phase: this.state.voice_phase,
        mode: "wake", step: 0, tool: ""};
    const notice = this.state?.watcher?.notifications?.find((n) => n.expires_at > Date.now() / 1000);
    if (notice && task?.state !== "working") return {
      command: notice.text, phase: "attention", mode: "notification",
      step: 0, tool: "", notification: notice.id,
    };
    return task ? {
      command: task.command, phase: task.phase, mode: task.mode,
      step: task.steps.length + 1, tool: task.active_tool || "",
    } : null;
  }
  position() {
    const { x, y, width, height } = this.screen.getDisplayMatching(this.main.getBounds()).workArea;
    this.hud.setBounds({ x: x + width - 336, y: y + height - 126, width: 320, height: 110 });
  }
  update(state) {
    if (this.disposed) return;
    this.state = state;
    const task = state?.task;
    if (task?.state !== "working") this.restoredTask = "";
    const executing = task?.state === "working" && task.mode === "execute";
    const eligible = !this.taskHud && executing && !state.prompt && !state.cloud_review && this.shortcuts && task.id !== this.restoredTask;
    if (eligible && !this.active && !this.disposed) {
      this.active = true;
      this.position();
      this.hud.showInactive();
      this.main.hide();
    } else if (!eligible && this.active) {
      this.active = false;
      this.hud.hide();
      this.target.hide();
      if (!this.main.isDestroyed()) {
        if (state?.prompt || state?.cloud_review) this.main.show();
        else this.main.showInactive();
      }
    }
    const hint = task?.state !== "working" && !state?.prompt && !state?.cloud_review && ["notification", "wake"].includes(this.presentation()?.mode);
    if (!this.active) {
      this.hintActive = !!hint;
      if (hint) {
        this.position();
        this.hud.showInactive();
      } else this.hud.hide();
    }
    if (!executing || state?.prompt || state?.cloud_review) this.target.hide();
    if (!this.hud.isDestroyed()) this.hud.webContents.send("jarvis-overlay:state", this.presentation());
  }
  restore(focus = false) {
    this.onRestore?.();
    this.hintActive = false;
    this.restoredTask = this.state?.task?.id || "";
    this.active = false;
    if (!this.hud.isDestroyed()) this.hud.hide();
    if (!this.target.isDestroyed()) this.target.hide();
    if (!this.main.isDestroyed()) {
      if (focus) this.main.show();
      else this.main.showInactive();
    }
  }
  async highlight(message) {
    await this.ready;
    if (this.disposed || this.state?.task?.state !== "working" || this.state?.prompt || this.state?.cloud_review)
      throw new Error("target_cancelled");
    const taskId = this.state.task.id;
    if (this.pendingPaint) this.pendingPaint.reject(new Error("target_superseded"));
    const paint = { id: message.id };
    const { left, top, right, bottom } = message.rectangle;
    const physical = { x: left, y: top, width: right - left, height: bottom - top };
    const rect = process.platform === "win32" ? this.screen.screenToDipRect(null, physical) : physical;
    const display = this.screen.getDisplayMatching(rect).bounds;
    const x = Math.max(rect.x, display.x), y = Math.max(rect.y, display.y);
    const rightEdge = Math.min(rect.x + rect.width, display.x + display.width);
    const bottomEdge = Math.min(rect.y + rect.height, display.y + display.height);
    if (rightEdge <= x || bottomEdge <= y) throw new Error("target_offscreen");
    this.target.setBounds({ x, y, width: rightEdge - x, height: bottomEdge - y });
    this.target.showInactive();
    let timer;
    try {
      await new Promise((resolve, reject) => {
        Object.assign(paint, { resolve, reject });
        this.pendingPaint = paint;
        timer = setTimeout(() => reject(new Error("target_paint_timeout")), 1500);
        this.target.webContents.send("jarvis-overlay:target", message.id);
      });
      await new Promise((resolve) => setTimeout(resolve, 300));
      if (this.disposed || this.state?.task?.id !== taskId ||
          this.state?.task?.state !== "working" || this.state?.prompt || this.state?.cloud_review)
        throw new Error("target_cancelled");
    } finally {
      clearTimeout(timer);
      if (this.pendingPaint === paint) {
        this.pendingPaint = null;
        if (!this.target.isDestroyed()) this.target.hide();
      }
    }
  }
  dispose() {
    this.disposed = true;
    for (const name of ["display-metrics-changed", "display-added", "display-removed"])
      this.screen.removeListener(name, this.reposition);
    for (const window of [this.hud, this.target]) if (!window.isDestroyed()) window.destroy();
  }
}
module.exports = { Overlay };
