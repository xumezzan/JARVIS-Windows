// Real Electron/JSONL/service with explicit model/microphone fixtures, no live recording.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const {_electron} = require("playwright");
(async () => {
  const root = path.resolve(__dirname, "..");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-wake-ui-"));
  const python = process.env.JARVIS_PYTHON;
  if (!python || process.platform === "win32") throw new Error("Use a POSIX fixture venv; this is not hardware acceptance.");
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  const launcher = path.join(data, "fixture-python");
  await fs.writeFile(launcher, `#!/bin/sh\nexec ${quote(python)} -u ${quote(path.join(root, "../tests/desktop_runtime_fixture.py"))} --stdio\n`, {mode:0o700});
  const env = {...process.env, JARVIS_PYTHON:launcher, JARVIS_DATA_DIR:data, JARVIS_VOICE_WAKE_WORD:"1"};
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.JARVIS_VOSK_MODEL;
  const app = await _electron.launch({executablePath:require("electron"), args:[root], env});
  const checks = [];
  try {
    const page = await app.firstWindow();
    const state = async () => (await page.evaluate(() => window.jarvis.status())).state;
    async function until(predicate) {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        const current = await state();
        if (current && predicate(current)) return current;
        await page.waitForTimeout(30);
      }
      throw new Error("wake_fixture_state_timeout");
    }
    const focus = () => app.evaluate(({app, BrowserWindow}) => {
      if (process.platform === "darwin") app.show();
      app.focus({steal:true});
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.show(); w.focus(); w.webContents.focus();
    });
    await until(s => s.voice_phase === "wake_listening");
    await page.locator('.wake-indicator').waitFor();
    const corner = app.windows().find(w => w.url().endsWith("#hud"));
    await corner.getByText("Микрофон включён", {exact:true}).waitFor();
    await focus();
    await page.getByRole("button", {name:"Выключить фоновый микрофон", exact:true}).click();
    await until(s => !s.wake_listening && s.wake_suspended);
    const resume = page.locator('[data-jarvis-wake]');
    await resume.evaluate(button => button.click());
    await page.waitForTimeout(100);
    assert.equal((await state()).wake_listening, false);
    await focus();
    await resume.click();
    await until(s => s.voice_phase === "wake_listening");
    checks.push({check:"opt-in visible microphone indicator; Stop suspends; synthetic resume rejected; trusted resume works",pass:true});
    await fs.writeFile(path.join(data,"activate-wake"), "fixture");
    await until(s => s.voice_background && s.voice_phase === "recording");
    await app.evaluate(({BrowserWindow}) => {
      BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html")).blur();
    });
    await page.waitForTimeout(100);
    assert.equal((await state()).voice_phase, "recording");
    await fs.writeFile(path.join(data,"finish-command"), "fixture");
    await until(s => s.voice_phase === "idle");
    await page.waitForFunction(() => document.getElementById('command')?.value === "проверь систему");
    const end = await state();
    assert.equal(end.started_count, 0);
    assert.equal(end.wake_listening, false);
    checks.push({check:"wake starts endpoint recording without a hold; allowed background capture survives blur; transcript waits for review without task/cloud consent",pass:true});
    await fs.writeFile(path.join(root,"qa/wake-runtime-verification.json"), JSON.stringify({platform:process.platform, checks, limitations:["Model and microphone are fixtures; real keyword recognition and Windows hardware remain unverified."]}, null, 2)+"\n");
    console.log(JSON.stringify(checks,null,2));
  } finally {
    await app.close();
    await fs.rm(data,{recursive:true,force:true});
  }
})().catch(error => {console.error(error); process.exitCode=1;});
