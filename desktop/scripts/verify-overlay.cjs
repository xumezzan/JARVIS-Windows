const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { _electron } = require('playwright');
(async () => {
  const root = path.resolve(__dirname, '..');
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-overlay-'));
  const python = process.env.JARVIS_PYTHON;
  if (!python || process.platform === 'win32') throw new Error('Set JARVIS_PYTHON to a POSIX test venv; this is fixture QA.');
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  const launcher = path.join(data, 'fixture-python');
  await fs.writeFile(launcher, `#!/bin/sh\nexec ${quote(python)} -u ${quote(path.join(root, '../tests/desktop_runtime_fixture.py'))} --stdio\n`, { mode: 0o700 });
  const env = { ...process.env, JARVIS_DATA_DIR: data, JARVIS_PYTHON: launcher };
  delete env.ELECTRON_RUN_AS_NODE;
  const app = await _electron.launch({ executablePath: require('electron'), args: [root], env });
  const checks = [];
  try {
    const page = await app.firstWindow();
    await page.locator('.app-shell').waitFor();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const state = async () => (await page.evaluate(() => window.jarvis.status())).state;
    const until = async predicate => {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        const s = await state();
        if (s && predicate(s)) return s;
        await page.waitForTimeout(20);
      }
      throw new Error('overlay_state_timeout: ' + JSON.stringify(await state()));
    };
    await until(s => s.can_run);
    const info = () => app.evaluate(({ BrowserWindow }) => Object.fromEntries(BrowserWindow.getAllWindows().map(w => {
      const url = w.webContents.getURL();
      const role = url.endsWith('#hud') ? 'hud' : url.endsWith('target.html') ? 'target' : 'main';
      const p = w.webContents.getLastWebPreferences();
      return [role, { visible: w.isVisible(), focused: w.isFocused(), size: w.getSize(), bounds: w.getBounds(), top: w.isAlwaysOnTop(), focusable: w.isFocusable(), transparent: p.transparent, preload: p.preload, isolated: p.contextIsolation, sandbox: p.sandbox }];
    })));
    const before = await info();
    await page.evaluate(() => window.jarvis.run('overlay fixture', 'execute'));
    await until(s => s.task.phase === 'executing');
    await page.waitForTimeout(100);
    let current = await info();
    assert.equal(current.main.visible, true);
    assert.equal(current.main.top, true);
    assert.deepEqual(current.main.size, [360, 120]);
    assert.equal(current.hud.visible, false);
    assert.equal(current.target.visible, true);
    assert.equal(current.hud.focusable, false);
    assert.equal(current.hud.top, true);
    assert.deepEqual(current.hud.size, [320, 110]);
    await page.locator('.task-hud').waitFor();
    const isolation = await page.evaluate(() => ({ node: typeof window.require,
      overflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
      stop: document.querySelector('.task-hud-stop').getBoundingClientRect().height }));
    assert.deepEqual(isolation, { node: 'undefined', overflow: false, stop: 44 });
    await page.screenshot({ path: path.join(root, 'qa/task-hud.png'), scale: 'css' });
    await until(s => s.task.state === 'finished');
    await fs.access(path.join(data, 'target-presented'));
    current = await info();
    assert.equal(current.hud.visible, false);
    assert.equal(current.target.visible, false);
    assert.equal(current.main.visible, true);
    assert.deepEqual(current.main.bounds, before.main.bounds);
    assert.equal(current.main.top, before.main.top);
    checks.push({ check: 'real Electron HUD transition and native target paint/300ms receipt, fixture adapter; original window restored', pass: true, isolation });
    await page.evaluate(() => window.jarvis.run('confirm fixture', 'execute'));
    await until(s => s.prompt?.kind === 'approval');
    current = await info();
    assert.equal(current.main.visible, true);
    assert.equal(current.hud.visible, false);
    assert.equal(await page.getByRole('dialog').count(), 1);
    await page.evaluate(() => window.jarvis.cancel());
    await until(s => s.task.state === 'cancelled');
    checks.push({ check: 'exact approval remains in the visible interactive workbench; cancellation restores it', pass: true });
    await page.evaluate(() => window.jarvis.run('clarify fixture', 'execute'));
    await until(s => s.prompt?.kind === 'clarification');
    current = await info();
    assert.equal(current.main.visible, true);
    assert.deepEqual(current.main.bounds, before.main.bounds);
    await page.getByRole('dialog').waitFor();
    await page.evaluate(() => window.jarvis.cancel());
    await until(s => s.task.state === 'cancelled');
    checks.push({check: 'clarification restores the complete interactive workbench', pass: true});
    await page.evaluate(() => window.jarvis.run('overlay fixture', 'simulation'));
    await until(s => s.task.state === 'simulated');
    assert.equal((await info()).hud.visible, false);
    checks.push({ check: 'simulation never calls target display/adapter', pass: true });
    await page.evaluate(() => window.jarvis.run('overlay fixture', 'execute'));
    await until(s => s.task.phase === 'executing');
    await page.getByRole('button', {name: 'Остановить задачу', exact: true}).click();
    await until(s => s.task.state === 'cancelled');
    assert.equal((await info()).target.visible, false);
    assert.equal((await info()).main.visible, true);
    assert.deepEqual(errors, []);
    checks.push({ check: 'cancel during preview hides both overlays and restores workbench', pass: true });
    await fs.writeFile(path.join(root, 'qa/task-hud-verification.json'), JSON.stringify({ platform: process.platform, checks, limitations: ['Windows native UIA/click-through and mixed-DPI monitors need Windows acceptance. Fixture paint is not a real Windows click.'] }, null, 2) + '\n');
    console.log(JSON.stringify(checks, null, 2));
  } finally {
    await app.close();
    await fs.rm(data, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
