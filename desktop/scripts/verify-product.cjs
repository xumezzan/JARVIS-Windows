// Packaged native app + system Chrome. Synthetic settings only; no login, audio or LLM calls.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { _electron } = require("playwright");

(async () => {
  const repository = path.resolve(__dirname, "../..");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-product-e2e-"));
  const executable = process.env.JARVIS_PACKAGED_EXE || path.join(os.homedir(), "Applications/Jarvis.app/Contents/MacOS/Jarvis");
  const env = { ...process.env, JARVIS_DATA_DIR: data };
  for (const name of ["ELECTRON_RUN_AS_NODE", "JARVIS_VOSK_MODEL", "JARVIS_PLANNER_MODEL",
    "JARVIS_VOICE_WAKE_WORD", "JARVIS_VOICE_AUTO_STOP_ON_SILENCE", "JARVIS_BROWSER_MODE",
    "JARVIS_BROWSER_CDP_URL", "JARVIS_BROWSER_CDP_ENDPOINT"]) delete env[name];
  let running;
  const errors = [];
  async function waitForState(page, predicate) {
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      // Await IPC in Node: an async predicate passed to the renderer poller can
      // be treated as a truthy Promise before its status result has arrived.
      const state = (await page.evaluate(() => window.jarvis?.status()))?.state;
      if (state && predicate(state)) return state;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error("runtime_state_timeout");
  }
  async function launch() {
    const app = await _electron.launch({ executablePath: executable, args: [], env });
    running = app;
    const page = await app.firstWindow();
    page.on("pageerror", (e) => errors.push(e.message));
    await waitForState(page, state => state.can_run);
    return { app, page };
  }
  try {
    let { app, page } = await launch();
    assert.equal(await app.evaluate(({ app }) => app.isPackaged), true);
    await page.getByRole("button", { name: "Настройки", exact: true }).click();
    await page.getByLabel("Голосовой автозапуск", { exact: true }).check();
    await page.getByLabel("Завершать запись по тишине (Auto-stop on silence)").check();
    await page.getByLabel("Ожидать обращение «Джарвис» (Wake Word)").check();
    await page.getByLabel("Обработка команд").selectOption("openai");
    await page.getByLabel("Модель Responses API").fill("test-model");
    await page.getByRole("button", { name: "Сохранить модель", exact: true }).click();
    await page.getByRole("button", { name: "Пространство", exact: true }).click();
    await page.getByRole("textbox", { name: "Ваше имя", exact: true }).waitFor();
    await page.getByRole("button", { name: "Добавить 14 приложений Omadli Group" }).click();
    await page.waitForFunction(() => document.querySelectorAll(".workspace-app").length === 14);
    await page.getByRole("button", { name: "Открыть рабочий браузер", exact: true }).click();
    await waitForState(page, state => state.workspace_browser?.state === "connected");
    const receipt = JSON.parse(await fs.readFile(path.join(data, "workspace_browser.json"), "utf8"));
    const settings = JSON.parse(await fs.readFile(path.join(data, "ui_settings.json"), "utf8"));
    assert.equal(settings.planner_model, "test-model");
    assert.equal(settings.wake_word, true);
    assert.equal(settings.auto_stop_on_silence, true);
    assert.equal(settings.auto_start, true);
    assert.equal(settings.browser_mode, "authenticated_workspace");
    assert.equal(settings.managed_browser, true);
    assert.equal(settings.cloud_consent, undefined);
    await page.screenshot({ path: path.join(data, "product-workspace.png"), fullPage: true, animations: "disabled" });
    await app.close(); running = null;
    ({ app, page } = await launch());
    await waitForState(page, state => state.workspace_browser?.state === "connected");
    const restored = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(restored.model, "test-model");
    assert.equal(restored.settings.auto_start, true);
    assert.equal(restored.settings.wake_word, true);
    assert.equal(restored.settings.auto_stop_on_silence, true);
    assert.equal(restored.started_count, 0);
    assert.equal(JSON.parse(await fs.readFile(path.join(data, "workspace_browser.json"), "utf8")).pid, receipt.pid);
    assert.equal(restored.wake_listening, false); // no Vosk model in this isolated test
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ pass: true, packaged: true, system_browser_cdp: "127.0.0.1:9222",
      reused_browser_pid: receipt.pid, preferences_restored: true, evidence: data,
      limitations: "macOS only; no live accounts, 1Password, microphone, LLM or native Windows acceptance" }, null, 2));
  } finally {
    if (running) await running.close();
    // Close only the test-owned browser, after checking its exact isolated profile.
    execFileSync(path.join(repository, ".venv/bin/python"), ["-c", `
import json,sys
from pathlib import Path
import psutil
root=Path(sys.argv[1]); receipt=root/'workspace_browser.json'
if receipt.exists():
    value=json.loads(receipt.read_bytes())
    try:
        p=psutil.Process(value['pid'])
        if abs(p.create_time()-value['started'])<.01 and '--user-data-dir='+str(root/'workspace_chrome_profile') in p.cmdline():
            p.terminate(); p.wait(timeout=10)
    except psutil.NoSuchProcess: pass
`, data]);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
