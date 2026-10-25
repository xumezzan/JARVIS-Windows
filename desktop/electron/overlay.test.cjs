const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Overlay } = require('./overlay.cjs');

class Window extends EventEmitter {
  constructor(options = {}) {
    super(); this.options = options; this.visible = false; this.destroyed = false;
    this.webContents = new EventEmitter();
    this.webContents.mainFrame = {};
    this.webContents.setWindowOpenHandler = () => {};
    this.webContents.send = () => {};
  }
  setIgnoreMouseEvents(...args) { this.ignore = args; }
  setAlwaysOnTop(...args) { this.top = args; }
  setMenu() {}
  loadURL() { return Promise.resolve(); }
  loadFile() { return Promise.resolve(); }
  isDestroyed() { return this.destroyed; }
  getBounds() { return { x: 0, y: 0, width: 1536, height: 1024 }; }
  setBounds(value) { this.bounds = value; }
  showInactive() { this.visible = true; }
  show() { this.visible = true; this.emit('show'); }
  hide() { this.visible = false; }
  destroy() { this.destroyed = true; }
}
function setup(shortcuts = true) {
  const screen = new EventEmitter();
  screen.getDisplayMatching = () => ({ workArea: {x: -1920, y: 0, width: 1920, height: 1080}, bounds: {x: -1920, y: 0, width: 1920, height: 1080} });
  const handlers = new Map();
  const ipcMain = new EventEmitter();
  ipcMain.handle = (name, callback) => handlers.set(name, callback);
  const keys = new Map();
  const globalShortcut = { register: (key, callback) => { keys.set(key, callback); return shortcuts; } };
  const main = new Window(); main.visible = true;
  const overlay = new Overlay({ BrowserWindow: Window, screen, ipcMain, globalShortcut }, main, 'file:///index.html');
  return { overlay, main, keys, handlers };
}
const working = (id = 'one') => ({ task: { id, state: 'working', mode: 'execute', phase: 'executing', steps: [], command: 'Check system' }, prompt: null });
test('task/prompt/error transitions preserve an interactive escape route', async () => {
  const { overlay, main, keys, handlers } = setup();
  await overlay.ready;
  overlay.update(working());
  assert.equal(main.visible, false);
  assert.equal(overlay.hud.visible, true);
  assert.deepEqual(overlay.hud.ignore, [true, {forward: true}]);
  assert.equal(overlay.hud.options.focusable, false);
  assert.equal(overlay.hud.options.transparent, true);
  assert.deepEqual(overlay.hud.top, [true, 'screen-saver']);
  assert.equal(overlay.hud.bounds.x, -336);
  assert.throws(() => handlers.get('jarvis-overlay:status')({ sender: main.webContents }));
  overlay.update({...working(), prompt: {kind: 'approval'}});
  assert.equal(main.visible, true);
  assert.equal(overlay.hud.visible, false);
  overlay.update(working());
  keys.get('CommandOrControl+Alt+J')();
  overlay.update(working());
  assert.equal(main.visible, true);
  overlay.update(working('two'));
  let cancelled = 0; overlay.onCancel = () => cancelled++;
  keys.get('CommandOrControl+Alt+Escape')();
  assert.equal(cancelled, 1);
  assert.equal(main.visible, true);
  overlay.update(null);
  overlay.dispose();
  assert.doesNotThrow(() => overlay.update(working()));
});
test('unregistered global escape keys keep the workbench reachable', async () => {
  const {overlay, main} = setup(false);
  await overlay.ready;
  overlay.update(working());
  assert.equal(main.visible, true);
  assert.equal(overlay.hud.visible, false);
  overlay.dispose();
});
test('watcher hints show the corner orb without hiding or focusing the main window', async () => {
  const {overlay, main} = setup();
  await overlay.ready;
  const state = {task: {state: 'idle', steps: []}, watcher: {notifications: [{
    id: 'hint', text: 'Fixed local offer', expires_at: Date.now()/1000 + 300,
  }]}};
  overlay.update(state);
  assert.equal(main.visible, true);
  assert.equal(overlay.hud.visible, true);
  assert.equal(overlay.presentation().mode, 'notification');
  overlay.update({...state, watcher: {notifications: []}});
  assert.equal(overlay.hud.visible, false);
  assert.equal(main.visible, true);
  overlay.dispose();
});
