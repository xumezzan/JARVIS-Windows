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
  setVisibleOnAllWorkspaces(...args) { this.workspaces = args; }
  setHiddenInMissionControl(value) { this.hiddenInMissionControl = value; }
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
function setup(shortcuts = true, platform = 'win32') {
  const screen = new EventEmitter();
  screen.screenToDipRect = (_window, rect) => rect;
  screen.getDisplayMatching = () => ({ workArea: {x: -1920, y: 0, width: 1920, height: 1080}, bounds: {x: -1920, y: 0, width: 1920, height: 1080} });
  const handlers = new Map();
  const ipcMain = new EventEmitter();
  ipcMain.handle = (name, callback) => handlers.set(name, callback);
  const keys = new Map();
  const globalShortcut = { register: (key, callback) => { keys.set(key, callback); return shortcuts; } };
  const main = new Window(); main.visible = true;
  const overlay = new Overlay({ BrowserWindow: Window, screen, ipcMain, globalShortcut }, main,
    'file:///index.html', {platform});
  return { overlay, main, keys, handlers, ipcMain, screen };
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

test('ghost covers the desktop, paints exact target, yields and disappears at idle', async () => {
  const {overlay, ipcMain, screen} = setup();
  screen.getAllDisplays = () => [
    {bounds: {x: -1920, y: 0, width: 1920, height: 1080}},
    {bounds: {x: 0, y: 0, width: 1536, height: 1024}},
  ];
  await overlay.ready;
  overlay.update(working());
  assert.deepEqual(overlay.target.ignore, [true, {forward: true}]);
  assert.equal(overlay.target.options.focusable, false);
  assert.equal(overlay.target.options.transparent, true);
  assert.equal(overlay.target.options.webPreferences.sandbox, true);
  let rendered;
  overlay.target.webContents.send = (channel, value) => {
    if (channel !== 'jarvis-overlay:cursor' || !value) return;
    rendered = value;
    if (value.cursor_state === 'moving') {
      // A workbench renderer cannot forge the display-only paint receipt.
      ipcMain.emit('jarvis-overlay:painted', {sender: overlay.main.webContents,
        senderFrame: overlay.main.webContents.mainFrame}, value.id);
      assert.equal(overlay.pendingPaint.id, value.id);
      ipcMain.emit('jarvis-overlay:painted', {sender: overlay.target.webContents,
        senderFrame: overlay.target.webContents.mainFrame}, value.id);
    }
  };
  await overlay.highlight({id: 'ghost-one', action: 'click', label: 'Отправить',
    rectangle: {left: -1800, top: 200, right: -1600, bottom: 240}});
  assert.deepEqual(overlay.target.bounds, {x: -1920, y: 0, width: 3456, height: 1080});
  assert.equal(rendered.target_x, 220);
  assert.equal(rendered.target_y, 220);
  assert.deepEqual(rendered.bounding_box, {left: 120, top: 200, width: 200, height: 40});
  overlay.cursorState('yielding');
  assert.equal(rendered.cursor_state, 'yielding');
  assert.equal(overlay.target.visible, true);
  overlay.update({task: {state: 'finished', steps: []}});
  assert.equal(overlay.target.visible, false);
  assert.equal(overlay.ghost, null);
  overlay.dispose();
});

test('ghost transforms native pixels into Electron DIP and refuses invalid targets', async () => {
  const {overlay, ipcMain, screen} = setup();
  screen.screenToDipRect = (_window, r) => ({x: r.x/2, y: r.y/2,
    width: r.width/2, height: r.height/2});
  screen.getDisplayMatching = () => ({bounds: {x: 0, y: 0, width: 1920, height: 1080},
    workArea: {x: 0, y: 0, width: 1920, height: 1080}});
  await overlay.ready;
  overlay.update(working());
  overlay.target.webContents.send = (_channel, value) => {
    if (value) ipcMain.emit('jarvis-overlay:painted', {sender: overlay.target.webContents,
      senderFrame: overlay.target.webContents.mainFrame}, value.id);
  };
  await overlay.highlight({id: 'dpi', rectangle: {left: 200, top: 400, right: 400, bottom: 600}});
  assert.equal(overlay.ghost.target_x, 150);
  assert.equal(overlay.ghost.target_y, 250);
  await assert.rejects(overlay.highlight({id: 'bad', rectangle: {left: 1, top: 1, right: -1, bottom: 5}}));
  overlay.dispose();
});
test('unregistered global escape keys keep the workbench reachable', async () => {
  const {overlay, main} = setup(false);
  await overlay.ready;
  overlay.update(working());
  assert.equal(main.visible, true);
  assert.equal(overlay.hud.visible, false);
  overlay.dispose();
});
test('macOS panels follow Spaces/fullscreen and preserve logical Retina coordinates', async () => {
  const {overlay, screen, ipcMain} = setup(true, 'darwin');
  screen.screenToDipRect = () => { throw new Error('macOS coordinates are already points'); };
  await overlay.ready;
  for (const window of [overlay.hud, overlay.target]) {
    assert.equal(window.options.type, 'panel');
    assert.deepEqual(window.workspaces, [true, {visibleOnFullScreen: true}]);
    assert.equal(window.hiddenInMissionControl, true);
    assert.equal(window.options.focusable, false);
  }
  overlay.update(working());
  overlay.target.webContents.send = (_channel, value) => {
    if (value) ipcMain.emit('jarvis-overlay:painted', {sender: overlay.target.webContents,
      senderFrame: overlay.target.webContents.mainFrame}, value.id);
  };
  await overlay.highlight({id: 'retina', rectangle: {left: -1800, top: 200, right: -1600, bottom: 240}});
  assert.equal(overlay.ghost.target_x, 220);
  assert.equal(overlay.ghost.target_y, 220);
  assert.deepEqual(overlay.target.ignore, [true, {forward: true}]);
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
