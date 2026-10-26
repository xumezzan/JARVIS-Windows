const test = require("node:test");
const assert = require("node:assert/strict");
const {EventEmitter} = require("node:events");
const {TaskHudWindow} = require("./hud-mode.cjs");
const {cleanEnvironment} = require("./environment.cjs");

class Window {
  constructor() {
    this.bounds = {x: -1500, y: 80, width: 1200, height: 800};
    this.minimum = [1024, 720]; this.maximum = [0, 0];
    this.resizable = true; this.top = false; this.maximized = false;
    this.focused = false; this.visible = true;
  }
  getBounds() { return {...this.bounds}; }
  getNormalBounds() { return {x:-1500, y:80, width:1200, height:800}; }
  getMinimumSize() { return [...this.minimum]; }
  getMaximumSize() { return [...this.maximum]; }
  isDestroyed() { return false; }
  isResizable() { return this.resizable; }
  isAlwaysOnTop() { return this.top; }
  isMaximized() { return this.maximized; }
  maximize() { this.maximized = true; }
  unmaximize() { this.maximized = false; }
  setBounds(bounds) { this.bounds = {...bounds}; }
  setMinimumSize(...size) { this.minimum = size; }
  setMaximumSize(...size) { this.maximum = size; }
  setResizable(value) { this.resizable = value; }
  setAlwaysOnTop(value) { this.top = value; }
  showInactive() { this.visible = true; }
  show() { this.visible = true; }
  focus() { this.focused = true; }
}
function setup() {
  const screen = new EventEmitter();
  screen.getDisplayMatching = () => ({workArea: {x:-1920, y:0, width:1920, height:1080}});
  const window = new Window();
  const modes = [];
  const hud = new TaskHudWindow(window, screen, mode => modes.push(mode));
  return {window, screen, hud, modes};
}
const active = {task: {id: "task", state: "working", phase: "executing"}, prompt: null};

test("HUD is exactly 360x120, top right in workArea, and restores every terminal outcome", () => {
  for (const state of ["finished", "error", "cancelled", "timeout", "limit", "simulated", "no_action"]) {
    const {window, hud, modes} = setup();
    const before = window.getBounds();
    hud.update(active);
    assert.equal(hud.mode, "hud");
    assert.deepEqual(window.bounds, {x:-376, y:16, width:360, height:120});
    assert.equal(window.top, true);
    assert.equal(window.resizable, false);
    hud.update({task: {...active.task, state}, prompt: null});
    assert.deepEqual(window.bounds, before);
    assert.deepEqual(window.minimum, [1024, 720]);
    assert.equal(window.top, false);
    assert.equal(window.resizable, true);
    assert.deepEqual(modes, ["hud", "normal"]);
    hud.dispose();
  }
});
test("approval and clarification restore/focus the workbench even after a manual restore", () => {
  for (const kind of ["approval", "clarification"]) {
    const {window, hud} = setup();
    window.maximized = true;
    hud.update(active);
    hud.update({...active, prompt: {id:"one", kind}});
    assert.equal(hud.mode, "normal");
    assert.equal(window.top, false);
    assert.equal(window.focused, true);
    assert.equal(window.maximized, true);
    hud.update(active);
    hud.restore(true, true);
    hud.update(active);
    assert.equal(hud.mode, "normal");
    window.visible = false; window.focused = false;
    hud.update({...active, prompt: {id:"two", kind}});
    assert.equal(window.visible, true);
    assert.equal(window.focused, true);
    hud.dispose();
  }
});
test("runtime loss restores the window and display changes retain the escape controls", () => {
  const {window, hud, screen} = setup();
  hud.update(active);
  screen.getDisplayMatching = () => ({workArea: {x:0, y:40, width:1920, height:1000}});
  screen.emit("display-removed");
  assert.deepEqual(window.bounds, {x:1544, y:56, width:360, height:120});
  hud.update(null);
  assert.equal(hud.mode, "normal");
  hud.dispose();
  assert.equal(screen.listenerCount("display-removed"), 0);
});
test("only trusted non-secret voice config survives runtime environment filtering", () => {
  const config = {JARVIS_VOICE_WAKE_WORD: "1", JARVIS_VOICE_RMS_THRESHOLD: "700",
    JARVIS_VOICE_RMS_CALIBRATE: "1", JARVIS_VOICE_AUTO_STOP_ON_SILENCE: "1"};
  assert.deepEqual(cleanEnvironment({...config, OPENAI_API_KEY:"fixture-secret", HTTPS_PROXY:"fixture-proxy"}), config);
});
test("the trusted workspace CDP alias survives runtime environment filtering", () => {
  const config = {JARVIS_BROWSER_MODE: "authenticated_workspace",
    JARVIS_BROWSER_CDP_URL: "http://127.0.0.1:9222"};
  assert.deepEqual(cleanEnvironment({...config, OPENAI_API_KEY:"fixture-secret"}), config);
});
test("an approval remains reachable when the original monitor is unplugged", () => {
  const {window, hud, screen} = setup();
  screen.getDisplayMatching = () => ({id:1, workArea:{x:-1920,y:0,width:1920,height:1080}});
  hud.update(active);
  screen.getAllDisplays = () => [{id:2}];
  screen.getDisplayMatching = () => ({id:2, workArea:{x:0,y:0,width:1920,height:1080}});
  hud.update({...active, prompt:{id:"prompt",kind:"approval"}});
  assert.equal(window.bounds.x, 0);
  assert.equal(window.visible, true);
  assert.equal(window.focused, true);
  hud.dispose();
});
