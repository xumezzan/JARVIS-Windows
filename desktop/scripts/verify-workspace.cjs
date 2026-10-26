// Real Electron -> preload -> main -> JSONL -> WorkspaceStore. No network, audio or account.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { _electron } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-workspace-e2e-"));
  const env = { ...process.env, JARVIS_DATA_DIR: data, JARVIS_VOICE_WAKE_WORD: "0",
    JARVIS_PYTHON: path.join(root, "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python") };
  for (const key of ["ELECTRON_RUN_AS_NODE", "JARVIS_VOSK_MODEL", "JARVIS_BROWSER_MODE",
    "JARVIS_BROWSER_CDP_URL", "JARVIS_BROWSER_CDP_ENDPOINT"]) delete env[key];
  const errors = [];
  async function launch() {
    const app = await _electron.launch({ executablePath: require("electron"), args: [root], env });
    const page = await app.firstWindow();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.waitForFunction(async () => (await window.jarvis?.status())?.state?.can_run);
    await page.getByRole("button", { name: "Пространство", exact: true }).click();
    try {
      await page.getByRole("textbox", { name: "Ваше имя", exact: true }).waitFor({ timeout: 10000 });
    } catch (error) {
      console.error(await page.locator(".workspace-setup").innerText());
      console.error(errors);
      console.error(await page.evaluate(() => window.jarvis.status()));
      await app.close();
      throw error;
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    return { app, page };
  }
  let running;
  try {
    running = await launch();
    let { app, page } = running;
    assert.equal(await page.locator(".workspace-app").count(), 0);
    await page.getByRole("button", { name: "Добавить 14 приложений Omadli Group" }).click();
    await page.waitForFunction(() => document.querySelectorAll(".workspace-app").length === 14);
    await page.getByRole("textbox", { name: "Ваше имя", exact: true }).fill("Тестовый пользователь");
    await page.getByRole("textbox", { name: "Должность", exact: true }).fill("Менеджер проектов");
    await page.getByRole("textbox", { name: "Правила работы и типичные задачи" }).fill("Готовить отчёты; отправку подтверждать.");
    const omadli = page.locator(".workspace-app:has(#app-name-omadli_group)");
    await omadli.getByRole("textbox", { name: "URL приложения" }).fill("http://127.0.0.1/private-fixture");
    await omadli.getByRole("switch").click();
    await page.getByRole("button", { name: "Сохранить пространство" }).click();
    await page.locator(".workspace-feedback.is-error").waitFor();
    // A semantic URL error must leave the private runtime alive and the draft editable.
    assert.equal((await page.evaluate(() => window.jarvis.status())).state.ready, true);
    await omadli.getByRole("textbox", { name: "URL приложения" }).fill("https://portal.example.test/operations");
    await page.getByLabel("Выбранный режим").selectOption("authenticated_workspace");
    await page.getByRole("button", { name: "Добавить приложение", exact: true }).click();
    const custom = page.locator(".workspace-app").last();
    await custom.getByRole("textbox", { name: "Название приложения" }).fill("Склад компании");
    await custom.getByRole("textbox", { name: "URL приложения" }).fill("https://warehouse.example.test/work");
    await custom.getByRole("switch").click();
    // Navigation retains unsaved form data.
    await page.getByRole("button", { name: "Ассистент", exact: true }).click();
    await page.getByRole("button", { name: "Пространство", exact: true }).click();
    assert.equal(await page.locator(".workspace-app").count(), 15);
    await page.getByRole("button", { name: "Сохранить пространство" }).click();
    await page.waitForFunction(() => document.querySelector(".workspace-feedback:not(.is-error)")?.textContent.includes("Сохранено"));
    const stored = JSON.parse(await fs.readFile(path.join(data, "workspace_profile.json"), "utf8"));
    assert.equal(stored.apps.length, 15);
    assert.equal(stored.user.name, "Тестовый пользователь");
    assert.equal(stored.apps.find((a) => a.id === "omadli_group").launch_url, "https://portal.example.test/operations");
    assert.equal(stored.browser_mode, "authenticated_workspace");
    const current = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(current.workspace.active_browser_mode, "strict");
    assert.equal(current.workspace.restart_required, true);
    assert.equal(current.started_count, 0);
    // Batched layout evidence at the shipped desktop window sizes.
    for (const width of [1536, 1024]) {
      await app.evaluate(({ BrowserWindow }, width) => {
        BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().endsWith("/index.html")).setContentSize(width, 1024);
      }, width);
      await page.waitForFunction((width) => window.innerWidth === width, width);
      const layout = await page.evaluate(() => ({
        width: innerWidth, scroll: document.documentElement.scrollWidth,
        overflowing: [...document.querySelectorAll(".workspace-setup input, .workspace-app, .workspace-section")]
          .filter((el) => el.scrollWidth > el.clientWidth + 2).map((el) => el.className || el.tagName),
      }));
      assert.ok(layout.scroll <= layout.width, JSON.stringify(layout));
      assert.deepEqual(layout.overflowing, []);
      await page.screenshot({ path: path.join(data, `workspace-${width}.png`), fullPage: true, animations: "disabled" });
    }
    await app.close();
    running = null;
    running = await launch();
    ({ page } = running);
    const restored = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(restored.workspace.active_browser_mode, "authenticated_workspace");
    assert.equal(restored.workspace.restart_required, false);
    assert.equal(restored.workspace.profile.apps.length, 15);
    assert.ok(restored.tools.some((t) => t.name === "browser.open_workspace_app"));
    await page.getByRole("button", { name: "Инструменты", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll(".tool-list strong").length > 0);
    const labels = await page.locator(".tool-list strong").allTextContents();
    assert.ok(labels.every((label) => /[А-Яа-я]/.test(label)));
    assert.equal(labels.length, restored.tools.length);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ pass: true, applications: 15, catalog_label_mismatches: 0,
      checks: ["preset", "identity", "URL rejection and recovery", "custom app", "navigation retains edits",
        "atomic persistence", "restart applies browser mode", "1536/1024 layout", "catalog labels"], evidence: data }, null, 2));
  } finally { if (running) await running.app.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
