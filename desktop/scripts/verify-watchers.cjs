const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { _electron } = require('playwright');
(async () => {
  const root = path.resolve(__dirname, '..');
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-watchers-'));
  const python = process.env.JARVIS_PYTHON;
  if (!python || process.platform === 'win32') throw new Error('Set JARVIS_PYTHON to a POSIX test venv; this is fixture QA.');
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  const launcher = path.join(data, 'fixture-python');
  await fs.writeFile(launcher, `#!/bin/sh\nexec ${quote(python)} -u ${quote(path.join(root, '../tests/watcher_runtime_fixture.py'))} --stdio\n`, { mode: 0o700 });
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
        await page.waitForTimeout(30);
      }
      throw new Error('watcher_state_timeout');
    };
    await until(s => s.can_run);
    assert.equal((await state()).settings.watch_clipboard, false);
    await page.getByRole('button', {name: 'Настройки', exact: true}).click();
    await page.getByLabel('Подсказывать по ошибкам и иностранному тексту в буфере', {exact: true}).check();
    const offered = await until(s => s.watcher.notifications.length === 1);
    assert.equal(offered.task.state, 'idle');
    assert.equal(offered.voice_phase, 'idle');
    assert.ok(!JSON.stringify(offered).includes('synthetic clipboard failure'));
    await page.locator('.watcher-notice').waitFor();
    const hud = app.windows().find(p => p.url().endsWith('#hud'));
    await hud.getByText('Есть предложение', {exact: true}).waitFor();
    const windows = await app.evaluate(({BrowserWindow}) => Object.fromEntries(BrowserWindow.getAllWindows().map(w => [
      w.webContents.getURL().endsWith('#hud') ? 'hud' : w.webContents.getURL().endsWith('target.html') ? 'target' : 'main',
      {visible: w.isVisible(), focusable: w.isFocusable()},
    ])));
    assert.equal(windows.main.visible, true);
    assert.deepEqual(windows.hud, {visible: true, focusable: false});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await hud.emulateMedia({reducedMotion: 'reduce'});
    assert.equal(await hud.locator('.has-notification').evaluate(e => getComputedStyle(e).animationName), 'none');
    await page.screenshot({path: path.join(root, 'qa/watchers-settings.png'), scale: 'css'});
    await hud.screenshot({path: path.join(root, 'qa/watchers-orb.png'), scale: 'css'});
    const desktopGeometry = await page.locator('.watcher-notice').evaluate(e => ({width: e.clientWidth, content: e.scrollWidth}));
    assert.ok(desktopGeometry.content <= desktopGeometry.width);
    await page.getByRole('button', {name: 'Вставить в команду', exact: true}).click();
    await until(s => s.watcher.notifications.length === 0);
    assert.match(await page.getByRole('textbox', {name: 'Команда Jarvis', exact: true}).inputValue(), /synthetic clipboard failure/);
    assert.equal((await state()).started_count, 0);
    checks.push({check: 'synthetic clipboard baseline/detection, real HUD without focus steal, reduced motion, reviewable composer handoff without autostart', pass: true});
    await page.getByRole('button', {name: 'Настройки', exact: true}).click();
    await page.setViewportSize({width: 390, height: 844});
    const overflow = await page.evaluate(() => ({page: document.documentElement.scrollWidth > innerWidth,
      controls: [...document.querySelectorAll('.runtime-form .check-row')].some(e => e.scrollWidth > e.clientWidth + 1)}));
    assert.deepEqual(overflow, {page: false, controls: false});
    await page.screenshot({path: path.join(root, 'qa/watchers-mobile.png'), scale: 'css', fullPage: true});
    await page.getByRole('button', {name: 'Приостановить фоновые проверки', exact: true}).click();
    await until(s => s.watcher.status.includes('приостановлены'));
    assert.equal((await state()).settings.watch_clipboard, true);
    await page.getByLabel('Подсказывать по ошибкам и иностранному тексту в буфере', {exact: true}).uncheck();
    await until(s => !s.settings.watch_clipboard);
    assert.deepEqual(errors, []);
    checks.push({check: '390px settings without page/internal overflow; disable clears watcher', pass: true});
    await fs.writeFile(path.join(root, 'qa/watchers-verification.json'), JSON.stringify({platform: process.platform, checks,
      limitations: ['Synthetic clipboard only. Windows native clipboard, Outlook OAuth/live mail, and real speakers remain unverified.']}, null, 2) + '\n');
    console.log(JSON.stringify(checks, null, 2));
  } finally {
    await app.close();
    await fs.rm(data, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
