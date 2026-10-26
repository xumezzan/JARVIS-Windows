// Main-process window presentation only. Runtime owns tasks; no approval authority here.
class TaskHudWindow {
  constructor(window, screen, notify) {
    this.window = window;
    this.screen = screen;
    this.notify = notify;
    this.saved = null;
    this.restoredTask = "";
    this.state = null;
    this.reposition = () => { if (this.saved) this.position(); };
    for (const event of ["display-metrics-changed", "display-added", "display-removed"])
      screen.on(event, this.reposition);
  }
  get mode() { return this.saved ? "hud" : "normal"; }
  position() {
    const {x, y, width, height} = this.screen.getDisplayMatching(this.window.getBounds()).workArea;
    this.window.setBounds({x: Math.max(x, x + width - 376), y: y + 16,
      width: Math.min(360, width), height: Math.min(120, height)});
  }
  update(state) {
    this.state = state;
    const task = state?.task;
    const running = ["working", "running"].includes(task?.state);
    if (!running) this.restoredTask = "";
    const prompt = state?.prompt || ["approval", "clarification"].includes(task?.phase);
    const promptId = state?.prompt?.id || (prompt ? `${task.id}:${task.phase}` : "");
    const wasHud = !!this.saved;
    const eligible = running && !prompt && this.restoredTask !== task.id;
    if (eligible && !this.saved && !this.window.isDestroyed()) {
      const maximized = this.window.isMaximized();
      this.saved = {bounds: maximized ? this.window.getNormalBounds() : this.window.getBounds(), maximized,
        minimum: this.window.getMinimumSize(), maximum: this.window.getMaximumSize(),
        resizable: this.window.isResizable(), top: this.window.isAlwaysOnTop(),
        display: this.screen.getDisplayMatching(this.window.getBounds()).id};
      if (this.saved.maximized) this.window.unmaximize();
      this.window.setMinimumSize(0, 0);
      this.window.setMaximumSize(0, 0);
      this.window.setResizable(false);
      this.window.setAlwaysOnTop(true, "floating");
      this.notify("hud");
      this.position();
      this.window.showInactive();
    } else if (!eligible && this.saved) this.restore(!!prompt);
    if (prompt && !wasHud && promptId !== this.promptId && !this.window.isDestroyed()) {
      this.window.show();
      this.window.focus();
    }
    this.promptId = promptId;
  }
  restore(focus = false, user = false) {
    if (user) this.restoredTask = this.state?.task?.id || "";
    const saved = this.saved;
    this.saved = null;
    if (!saved || this.window.isDestroyed()) return;
    this.notify("normal");
    this.window.setAlwaysOnTop(saved.top);
    this.window.setResizable(saved.resizable);
    let bounds = saved.bounds;
    if (saved.display !== undefined && !this.screen.getAllDisplays().some(d => d.id === saved.display)) {
      const area = this.screen.getDisplayMatching(bounds).workArea;
      const width = Math.min(bounds.width, area.width), height = Math.min(bounds.height, area.height);
      bounds = {width, height, x: Math.max(area.x, Math.min(bounds.x, area.x + area.width - width)),
        y: Math.max(area.y, Math.min(bounds.y, area.y + area.height - height))};
    }
    this.window.setBounds(bounds);
    this.window.setMinimumSize(...saved.minimum);
    this.window.setMaximumSize(...saved.maximum);
    if (saved.maximized) this.window.maximize();
    if (focus) { this.window.show(); this.window.focus(); }
    else this.window.showInactive();
  }
  dispose() {
    for (const event of ["display-metrics-changed", "display-added", "display-removed"])
      this.screen.removeListener(event, this.reposition);
  }
}
module.exports = {TaskHudWindow};
