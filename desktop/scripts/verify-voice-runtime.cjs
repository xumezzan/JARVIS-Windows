const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { _electron } = require("playwright");
(async () => {
  const root = path.resolve(__dirname, "..");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-voice-ui-"));
  const python = process.env.JARVIS_PYTHON;
  if (!python)
    throw new Error("Set JARVIS_PYTHON to the test virtual environment.");
  if (process.platform === "win32")
    throw new Error(
      "This fixture launcher uses a POSIX wrapper; run Python tests on Windows.",
    );
  const quote = (value) => "'" + value.replaceAll("'", "'\\''") + "'";
  const launcher = path.join(data, "fixture-python");
  await fs.writeFile(
    launcher,
    `#!/bin/sh\nexec ${quote(python)} -u ${quote(path.join(root, "../tests/desktop_runtime_fixture.py"))} --stdio\n`,
    { mode: 0o700 },
  );
  const env = {
    ...process.env,
    JARVIS_PYTHON: launcher,
    JARVIS_DATA_DIR: data,
  };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.JARVIS_VOSK_MODEL;
  const application = await _electron.launch({
    executablePath: require("electron"),
    args: [root],
    env,
  });
  const checks = [];
  try {
    const page = await application.firstWindow();
    await page.emulateMedia({ reducedMotion: "reduce" });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const state = async () =>
      (await page.evaluate(() => window.jarvis.status())).state;
    async function until(predicate) {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        const value = await state();
        if (value && predicate(value)) return value;
        await page.waitForTimeout(30);
      }
      throw new Error(
        "fixture_state_timeout: " + JSON.stringify({ state: await state(), native: await application.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().map(w => ({url:w.webContents.getURL(), focused:w.isFocused(), visible:w.isVisible()}))), dom: await page.evaluate(() => ({focus:document.hasFocus(), active:document.activeElement?.outerHTML, errors:[...document.querySelectorAll("[role=alert]")].map(n=>n.textContent)})) }),
      );
    }
    await until((s) => s.can_run);
    await application.evaluate(({ app, BrowserWindow }) => {
      if (process.platform === "darwin") app.show();
      app.focus({ steal: true });
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.show();
      w.focus();
      w.webContents.focus();
    });
    await page.waitForTimeout(150);
    const mic = page.getByRole("button", {
      name: "Удерживайте для голосового ввода",
      exact: true,
    });
    await mic.evaluate((button) => {
      button.click();
      button.dispatchEvent(
        new PointerEvent("pointerdown", { bubbles: true, button: 0 }),
      );
    });
    assert.equal((await state()).voice_phase, "idle");
    const command = page.getByRole("textbox", { name: "Команда Jarvis" });
    await command.focus();
    const bounds = await mic.boundingBox();
    const x = bounds.x + bounds.width / 2,
      y = bounds.y + bounds.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await until((s) => s.voice_phase === "recording");
    await page.mouse.up();
    await until((s) => s.voice_phase === "idle");
    await page.waitForFunction(
      () => document.getElementById("command").value === "проверь систему",
    );
    assert.equal((await state()).started_count, 0);
    checks.push({
      check:
        "trusted hold records from a focused composer; release returns transcript for review; synthetic gestures ignored",
      pass: true,
    });
    await mic.focus();
    await page.keyboard.down("Space");
    await until((s) => s.voice_phase === "recording");
    await page.keyboard.up("Space");
    await until((s) => s.voice_phase === "idle");
    checks.push({
      check: "Space hold and release uses isolated gesture bridge",
      pass: true,
    });
    await page.getByRole("button", { name: "Настройки", exact: true }).click();
    await page.getByLabel("Голосовой автозапуск", { exact: true }).check();
    await until((s) => s.settings.auto_start);
    await page.screenshot({
      path: path.join(root, "qa/voice-settings.png"),
      fullPage: true,
      scale: "css",
      animations: "disabled",
    });
    await page.getByRole("button", { name: "Ассистент", exact: true }).click();
    await page.getByLabel("Режим", { exact: true }).selectOption("execute");
    await page.mouse.move(x, y);
    await page.mouse.down();
    await until((s) => s.voice_phase === "recording");
    await page.mouse.up();
    await until((s) => s.task.state === "finished" && s.voice_phase === "idle");
    assert.equal((await state()).task.steps[0].status, "SUCCESS");
    checks.push({
      check:
        "voice opt-in automatically starts the real Runner and acknowledges using fixture speaker",
      pass: true,
    });
    // Completion restores without stealing the target app focus. A new hold needs focus.
    await application.evaluate(({ app, BrowserWindow }) => {
      if (process.platform === "darwin") app.show();
      app.focus({ steal: true });
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.show();
      w.focus();
      w.webContents.focus();
    });
    // Focus loss must cancel, including when recording was started by keyboard.
    await mic.focus();
    await page.keyboard.down("Space");
    await until((s) => s.voice_phase === "recording");
    await page.getByRole("button", {name:"Ассистент",exact:true}).focus();
    await until((s) => s.voice_phase === "idle");
    await page.keyboard.up("Space");
    assert.equal((await state()).started_count, 1);
    checks.push({
      check: "focus loss cancels capture without submitting stale audio",
      pass: true,
    });
    await command.fill("confirm fixture");
    await page
      .getByRole("button", { name: "Выполнить команду", exact: true })
      .click();
    await until((s) => s.prompt?.kind === "approval");
    const approve = page.getByRole("button", {
      name: "Подтвердить показанное действие",
      exact: true,
    });
    await approve.waitFor({state: "visible", timeout: 5000}).catch(async error => {
      const current = await state();
      console.error("approval_render_diagnostic", JSON.stringify({
        backend: {state: current.task.state, phase: current.task.phase, prompt: current.prompt?.kind},
        renderer: await page.evaluate(() => ({
          mode: document.documentElement.dataset.windowMode,
          hud: !!document.querySelector('.task-hud'),
          dialogs: [...document.querySelectorAll('dialog')].map(d => ({open: d.open, text: d.querySelector('h2')?.textContent})),
          approvals: document.querySelectorAll('[data-jarvis-approval]').length,
        })), errors,
      }));
      throw error;
    });
    await approve.evaluate((button) => button.click());
    await assert.rejects(fs.access(path.join(data, "effect.json")));
    await page.screenshot({
      path: path.join(root, "qa/voice-approval.png"),
      scale: "css",
      animations: "disabled",
    });
    await application.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      window.setMinimumSize(360, 600);
      window.setContentSize(390, 844);
    });
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    const mobile = await page.getByRole("dialog").evaluate((dialog) => {
      const bounds = dialog.getBoundingClientRect();
      return {
        left: bounds.left,
        right: bounds.right,
        width: innerWidth,
        scroll: document.documentElement.scrollWidth,
      };
    });
    assert.ok(mobile.left >= 0 && mobile.right <= mobile.width);
    assert.equal(mobile.width, mobile.scroll);
    await page.screenshot({
      path: path.join(root, "qa/voice-approval-mobile.png"),
      scale: "css",
      animations: "disabled",
    });
    await application.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html")).setContentSize(1536, 1024),
    );
    checks.push({
      check:
        "approval dialog stays inside narrow renderer without horizontal overflow",
      ...mobile,
    });
    // Real mouse click issues one audited capability; same snapshot cannot be replayed.
    await application.evaluate(({ app, BrowserWindow }) => {
      if (process.platform === "darwin") app.show();
      app.focus({ steal: true });
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.show();
      w.focus();
      w.webContents.focus();
    });
    await page.waitForTimeout(150);
    await approve.click();
    await until((s) => s.task.state === "finished");
    assert.deepEqual(
      JSON.parse(await fs.readFile(path.join(data, "effect.json"), "utf8")),
      { performed: true },
    );
    checks.push({
      check:
        "synthetic approval never executes; trusted click executes exact snapshot through PermissionEngine",
      pass: true,
    });
    assert.deepEqual(errors, []);
    await fs.writeFile(
      path.join(root, "qa/voice-runtime-verification.json"),
      JSON.stringify(checks, null, 2) + "\n",
    );
    console.log(JSON.stringify(checks, null, 2));
  } finally {
    await application.close();
    await fs.rm(data, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
