const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { _electron } = require("playwright");
(async () => {
  const root = path.resolve(__dirname, "..");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-desktop-e2e-"));
  const environment = { ...process.env, JARVIS_DATA_DIR: data };
  delete environment.ELECTRON_RUN_AS_NODE;
  const application = await _electron.launch({
    executablePath: require("electron"),
    args: [root],
    env: environment,
  });
  const checks = [];
  try {
    const page = await application.firstWindow();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.locator(".app-shell").waitFor();
    await page.emulateMedia({ reducedMotion: "reduce" });
    async function until(predicate) {
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        const reply = await page.evaluate(() => window.jarvis.status());
        if (reply.error) throw new Error(reply.error);
        if (reply.state && predicate(reply.state)) return reply.state;
        await page.waitForTimeout(50);
      }
      throw new Error("runtime_state_timeout");
    }
    const initial = await until((state) => state.ready && state.can_run);
    if (!initial)
      throw new Error(
        "Runtime lost after readiness: " +
          JSON.stringify(await page.evaluate(() => window.jarvis.status())),
      );
    assert.deepEqual(initial.history, []);
    assert.deepEqual(initial.task.steps, []);
    assert.equal(initial.task.state, "idle");
    assert.equal(initial.outlook_connected, false);
    assert.equal(initial.voice_phase, "idle");
    assert.ok(initial.tools.some((t) => t.name === "system.info"));
    for (const name of ["fs.search", "fs.read_document", "fs.write_file", "python.sandbox_eval"])
      assert.ok(initial.tools.some((tool) => tool.name === name));
    assert.ok(initial.tools.every((t) => !t.name.startsWith("local.")));
    assert.equal(
      await page.getByText("История пока пуста", { exact: true }).count(),
      1,
    );
    assert.equal(
      await page.getByText("Нет активной задачи", { exact: true }).count(),
      1,
    );
    checks.push({
      check:
        "clean session is real backend state; no seeded data or test tools",
      pass: true,
    });
    const security = await application.evaluate(({ BrowserWindow }) => {
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html")),
        p = w.webContents.getLastWebPreferences();
      return {
        size: w.getContentSize(),
        contextIsolation: p.contextIsolation,
        nodeIntegration: p.nodeIntegration,
        sandbox: p.sandbox,
      };
    });
    assert.deepEqual(security.size, [1536, 1024]);
    assert.equal(security.contextIsolation, true);
    assert.equal(security.nodeIntegration, false);
    assert.equal(security.sandbox, true);
    assert.equal(
      await page.evaluate(() => typeof window.jarvis.approve),
      "undefined",
    );
    assert.equal(await page.evaluate(() => typeof window.require), "undefined");
    checks.push({
      check: "isolated renderer, fixed window and no exposed approval API",
      ...security,
    });
    await page.screenshot({
      path: path.join(root, "qa/desktop-clean.png"),
      scale: "css",
      animations: "disabled",
    });
    await page.getByLabel("Режим", { exact: true }).selectOption("execute");
    await page
      .getByRole("textbox", { name: "Команда Jarvis" })
      .fill("проверь систему");
    await page
      .getByRole("button", { name: "Выполнить команду", exact: true })
      .click();
    await until((state) => state.task.state === "finished");
    const actual = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(actual.task.steps[0].tool, "system.info");
    assert.equal(actual.task.steps[0].status, "SUCCESS");
    const observation = JSON.parse(actual.task.steps[0].result);
    assert.equal(observation.system, os.type());
    assert.equal(
      observation.architecture,
      os.arch() === "arm64" ? "arm64" : observation.architecture,
    );
    assert.ok(observation.disk_free_bytes > 0);
    assert.equal(actual.history.length, 1);
    await page.locator(".result-content details summary").click();
    await page.screenshot({
      path: path.join(root, "qa/desktop-system-result.png"),
      scale: "css",
      animations: "disabled",
    });
    checks.push({
      check:
        "actual Electron→private pipe→Python Runner→PermissionEngine→OS tool→verified result",
      system: observation.system,
      cpu_count: observation.cpu_count,
      status: actual.task.steps[0].status,
    });
    await page.getByLabel("Режим", { exact: true }).selectOption("simulation");
    await page
      .getByRole("textbox", { name: "Команда Jarvis" })
      .fill("проверь систему");
    await page
      .getByRole("button", { name: "Выполнить команду", exact: true })
      .click();
    await until((state) => state.task.state === "simulated");
    const simulated = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(simulated.task.steps[0].status, "SIMULATED");
    assert.equal(simulated.task.steps[0].result, "");
    checks.push({
      check: "simulation is engine policy, not an invented outcome",
      pass: true,
    });
    await page
      .getByRole("textbox", { name: "Команда Jarvis" })
      .fill("Неподдерживаемая офлайн-команда");
    await page
      .getByRole("button", { name: "Выполнить команду", exact: true })
      .click();
    await until((state) => state.task.phase === "clarification");
    assert.equal(
      (await page.evaluate(() => window.jarvis.status())).state
        .native_window_visible,
      false,
    );
    assert.equal(await page.getByRole("dialog").count(), 1);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Остановить задачу", exact: true })
      .click();
    await until((state) => state.task.state === "cancelled");
    checks.push({
      check:
        "clarification appears in React; Electron Stop cancels headless runner",
      pass: true,
    });
    await page.getByRole("button", { name: "Действия", exact: true }).click();
    assert.equal(await page.locator(".history-row").count(), 3);
    await page
      .getByRole("button", { name: "Инструменты", exact: true })
      .click();
    assert.equal(
      await page.locator(".tool-list>div").count(),
      initial.tools.length,
    );
    await page
      .getByRole("button", { name: "Подключения", exact: true })
      .click();
    assert.equal(
      await page
        .getByRole("button", { name: "Подключить аккаунт", exact: true })
        .count(),
      1,
    );
    await page.getByRole("button", { name: "Настройки", exact: true }).click();
    await page.getByLabel("Цветовая тема").selectOption("Graphite");
    assert.equal(
      await page.locator("html").getAttribute("data-theme"),
      "Graphite",
    );
    await page.getByLabel("Цветовая тема").selectOption("Midnight");
    await page.getByRole("switch", { name: "Анимация сферы" }).click();
    await page.getByRole("button", { name: "Ассистент", exact: true }).click();
    const mic = page.getByRole("button", {
      name: "Удерживайте для голосового ввода",
      exact: true,
    });
    await mic.evaluate((button) => button.click());
    const voice = (await page.evaluate(() => window.jarvis.status())).state;
    assert.equal(voice.voice_phase, "idle");
    assert.equal(voice.native_window_visible, false);
    assert.equal(
      await page.evaluate(() => typeof window.jarvis.voiceStart),
      "undefined",
    );
    checks.push({
      check:
        "synthetic clicks do not record; gesture channels stay inside isolated preload",
      pass: true,
    });
    await page.getByRole("button", { name: "Настройки", exact: true }).click();
    await page.getByLabel("Голосовой автозапуск", { exact: true }).check();
    await until((state) => state.settings.auto_start === true);
    await page.getByLabel("Голосовой автозапуск", { exact: true }).uncheck();
    await until((state) => state.settings.auto_start === false);
    assert.equal(
      (await page.evaluate(() => window.jarvis.status())).state.voice_phase,
      "idle",
    );
    checks.push({
      check: "voice settings change headless state without starting microphone",
      pass: true,
    });
    await page.getByRole("button", { name: "Ассистент", exact: true }).click();
    for (const [name, width, height] of [
      ["compact", 1280, 900],
      ["mobile", 390, 844],
    ]) {
      await application.evaluate(
        ({ BrowserWindow }, { width, height }) => {
          const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
          w.setMinimumSize(360, 600);
          w.setContentSize(width, height);
        },
        { width, height },
      );
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      const size = await page.evaluate(() => ({
        width: innerWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      assert.equal(size.width, size.scroll);
      const contained = await page
        .locator(".session-card")
        .evaluate(
          (card) =>
            card.querySelector(".session-note").getBoundingClientRect()
              .bottom <=
            card.getBoundingClientRect().bottom - 10,
        );
      assert.equal(contained, true);
      await page.screenshot({
        path: path.join(root, `qa/${name}-runtime.png`),
        scale: "css",
        fullPage: true,
        animations: "disabled",
      });
      checks.push({
        check: `${name} actual Electron renderer has no horizontal overflow; session note is contained`,
        ...size,
      });
    }
    await page
      .getByRole("textbox", { name: "Команда Jarvis" })
      .fill(
        "НАЧАЛО " + "длинный запрос ".repeat(300).slice(0, 3987) + " КОНЕЦ",
      );
    await page
      .getByRole("button", { name: "Выполнить команду", exact: true })
      .click();
    await until((state) => state.task.phase === "clarification");
    for (const [width, height] of [
      [1536, 1024],
      [1280, 900],
      [390, 844],
    ]) {
      await application.evaluate(
        ({ BrowserWindow }, { width, height }) =>
          BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html")).setContentSize(width, height),
        { width, height },
      );
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      const reach = await page.locator(".command-context").evaluate((box) => {
        const text = box.querySelector("p").firstChild;
        const range = document.createRange();
        const bounds = box.getBoundingClientRect();
        box.scrollTop = 0;
        range.setStart(text, 0);
        range.setEnd(text, 6);
        const first = range.getBoundingClientRect();
        const firstVisible =
          first.top >= bounds.top && first.bottom <= bounds.bottom;
        box.scrollTop = box.scrollHeight;
        range.setStart(text, text.length - 5);
        range.setEnd(text, text.length);
        const last = range.getBoundingClientRect();
        return {
          firstVisible,
          lastVisible: last.top >= bounds.top && last.bottom <= bounds.bottom,
          length: text.length,
        };
      });
      assert.equal(reach.length, 4000);
      assert.equal(reach.firstVisible, true);
      assert.equal(reach.lastVisible, true);
      checks.push({
        check: "4000-character command start and end reachable",
        width,
        ...reach,
      });
    }
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Остановить задачу", exact: true })
      .click();
    await until((state) => state.task.state === "cancelled");
    assert.deepEqual(errors, []);
    checks.push({ check: "renderer errors", errors });
    await application.evaluate(({ BrowserWindow }) => {
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.setContentSize(1536, 1024);
    });
    await page.getByRole("button", { name: "Развернуть окно" }).click();
    assert.equal(
      await page.evaluate(() => window.jarvisWindow.isMaximized()),
      true,
    );
    await page.getByRole("button", { name: "Развернуть окно" }).click();
    const closing = page.waitForEvent("close");
    await page.getByRole("button", { name: "Закрыть окно" }).click();
    await closing;
    checks.push({
      check: "native custom window controls and owned runtime shutdown",
      pass: true,
    });
    await fs.writeFile(
      path.join(root, "qa/runtime-verification.json"),
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
