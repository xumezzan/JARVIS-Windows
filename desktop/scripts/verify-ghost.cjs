// Actual Electron/React paint QA. Local display-only fixture; no provider or user task.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { _electron } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-ghost-'));
  const harness = path.join(scratch, 'harness.cjs');
  const renderer = require('node:url').pathToFileURL(path.join(root, 'dist/index.html')).href;
  await fs.writeFile(harness, `
    const {app, BrowserWindow, screen, ipcMain, globalShortcut} = require('electron');
    const {Overlay} = require(${JSON.stringify(path.join(root, 'electron/overlay.cjs'))});
    app.setPath('userData', ${JSON.stringify(path.join(scratch, 'profile'))});
    app.whenReady().then(async () => {
      const main = new BrowserWindow({show: false});
      global.fixtureOverlay = new Overlay({BrowserWindow, screen, ipcMain, globalShortcut},
        main, ${JSON.stringify(renderer)}, {taskHud: true});
      await global.fixtureOverlay.ready;
      global.fixtureOverlay.update({task: {id:'fixture', state:'working', mode:'execute', steps:[]}});
    });
  `);
  const env = {...process.env};
  delete env.ELECTRON_RUN_AS_NODE;
  console.log('Launching isolated Electron ghost fixture');
  const app = await _electron.launch({args: [harness], env, timeout:15000});
  console.log('Electron fixture connected');
  try {
    const deadline = Date.now() + 15000;
    let ghost;
    while (!ghost && Date.now() < deadline) {
      ghost = app.windows().find(w => w.url().endsWith('#ghost'));
      if (!ghost) await new Promise(r => setTimeout(r, 30));
    }
    assert.ok(ghost, 'Ghost renderer loaded');
    await ghost.waitForSelector('html.ghost-surface', {state: 'attached'});
    console.log('React ghost renderer ready');
    const physical = await app.evaluate(({screen}) => {
      const b = screen.getPrimaryDisplay().bounds;
      const rect = {x: b.x+Math.round(b.width/2), y: b.y+Math.round(b.height/2), width: 160, height: 40};
      const p = process.platform === 'win32' ? screen.dipToScreenRect(null, rect) : rect;
      return {left:p.x, top:p.y, right:p.x+p.width, bottom:p.y+p.height};
    });
    const cursorBefore = await app.evaluate(({screen}) => screen.getCursorScreenPoint());
    const started = Date.now();
    await app.evaluate(async (_electron, rectangle) => {
      await global.fixtureOverlay.highlight({id:'first', rectangle, action:'click', label:'Отправить'});
    }, physical);
    console.log('Animation paint receipt received');
    assert.ok(Date.now() - started >= 270, 'Adapter receipt waits for the 280ms animation');
    assert.equal(await ghost.locator('.ghost-badge').innerText(), 'JARVIS\nКлик: «Отправить»');
    assert.equal(await ghost.locator('.ghost-box').count(), 1);
    assert.equal(await ghost.locator('.ghost-ripple').count(), 1);
    const styles = await ghost.locator('.ghost-cursor').evaluate(el => ({
      transition: getComputedStyle(el).transitionDuration,
      pointerEvents: getComputedStyle(el).pointerEvents,
    }));
    assert.equal(styles.transition, '0.28s, 0.2s');
    assert.equal(styles.pointerEvents, 'none');
    await fs.mkdir(path.join(root, 'qa'), {recursive: true});
    await ghost.screenshot({path:path.join(root, 'qa/ghost-acting.png'), omitBackground:true});
    await app.evaluate(() => global.fixtureOverlay.cursorState('yielding'));
    await ghost.locator('.is-yielding').waitFor();
    await ghost.waitForTimeout(320);
    assert.match(await ghost.locator('.ghost-badge').innerText(), /Уступаю мышь/);
    assert.match(await ghost.locator('.ghost-badge').innerText(), /Пауза: жду ваших действий/);
    assert.equal(await ghost.locator('.ghost-cursor').evaluate(el => getComputedStyle(el).opacity), '0.55');
    assert.equal(await ghost.locator('.ghost-ripple').count(), 0);
    await ghost.screenshot({path:path.join(root, 'qa/ghost-yielding.png'), omitBackground:true});
    const cursorAfter = await app.evaluate(({screen}) => screen.getCursorScreenPoint());
    await app.evaluate(() => global.fixtureOverlay.update(null));
    await ghost.locator('.ghost-layer').waitFor({state:'detached'});
    const report = {pass:true, renderer:'actual Electron + React', animation_ms:280,
      click_through:true, yielding:true, hidden_on_idle:true,
      cursor_before:cursorBefore, cursor_after:cursorAfter,
      cursor_unchanged:JSON.stringify(cursorBefore) === JSON.stringify(cursorAfter)};
    await fs.writeFile(path.join(root, 'qa/ghost-verification.json'), JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report));
  } finally {
    await app.close();
    // Preserve the tiny fixture/profile for diagnosis; never remove user app data.
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
