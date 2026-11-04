// Real React→preload→main→JSONL→DesktopService checks. Fixtures replace only
// audio, Graph and credentials in the second session; no paid/live calls.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { _electron } = require("playwright");
const root = path.resolve(__dirname, "..");
const checks = [];
const python = process.env.JARVIS_PYTHON || path.join(root, "..", ".venv", "bin/python");
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";

async function launch(data, interpreter = python) {
  const env = { ...process.env, JARVIS_DATA_DIR: data, JARVIS_PYTHON: interpreter };
  for (const key of ["ELECTRON_RUN_AS_NODE", "JARVIS_PLANNER_MODEL", "JARVIS_VOSK_MODEL",
    "JARVIS_BROWSER_MODE", "JARVIS_BROWSER_CDP_URL", "JARVIS_BROWSER_CDP_ENDPOINT",
    "JARVIS_VOICE_WAKE_WORD", "JARVIS_VOICE_AUTO_STOP_ON_SILENCE"]) delete env[key];
  const app = await _electron.launch({ executablePath: require("electron"), args: [root], env });
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: "reduce" });
  const state = async () => {
    const result = await page.evaluate(() => window.jarvis.status());
    if (result.error) throw new Error(result.error);
    return result.state;
  };
  async function until(predicate) {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const value = await state();
      if (value && predicate(value)) return value;
      await page.waitForTimeout(40);
    }
    throw new Error("ui_wiring_state_timeout: " + JSON.stringify(await state()));
  }
  await until(s => s.ready && s.can_run);
  const nav = name => page.getByRole("button", { name, exact: true }).click();
  return { app, page, state, until, nav, errors };
}

(async () => {
  if (process.platform === "win32") throw new Error("Use Python integration tests on Windows; this fixture launcher is POSIX.");
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-ui-wiring-"));
  let session;
  try {
    session = await launch(data);
    let { page, state, until, nav } = session;
    for (const name of ["Действия", "Инструменты", "Подключения", "Память", "Пространство", "Настройки", "Ассистент"])
      await nav(name);
    await until(s => s.can_run);
    await nav("Настройки");
    await page.getByLabel("Цветовая тема").selectOption("Graphite");
    await until(s => s.settings.theme === "Graphite");
    await page.getByRole("switch", { name: "Анимация сферы" }).click();
    await until(s => !s.settings.motion_enabled);
    const stored = () => fs.readFile(path.join(data, "ui_settings.json"), "utf8").then(JSON.parse);
    assert.equal((await stored()).theme, "Graphite");
    assert.equal((await stored()).motion_enabled, false);
    await page.reload();
    await until(s => s.ready);
    assert.equal(await page.locator("html").getAttribute("data-theme"), "Graphite");
    await session.app.close();
    session = await launch(data);
    ({ page, state, until, nav } = session);
    await nav("Настройки");
    assert.equal(await page.getByLabel("Цветовая тема").inputValue(), "Graphite");
    assert.equal(await page.getByRole("switch", { name: "Анимация сферы" }).getAttribute("aria-checked"), "false");
    checks.push("appearance reaches Python, atomic JSON, renderer reload and full process restart");

    const settingsFile = path.join(data, "ui_settings.json");
    await fs.rename(settingsFile, settingsFile + ".saved");
    await fs.mkdir(settingsFile); // Controlled storage failure, isolated profile only.
    try {
      await page.getByLabel("Обработка команд", { exact: true }).selectOption("openai");
      await page.getByRole("alert").filter({ hasText: "Не удалось прочитать или сохранить профиль" }).waitFor();
      assert.equal(await page.getByLabel("Обработка команд", { exact: true }).inputValue(), "offline");
      assert.equal((await state()).provider, "offline");
    } finally {
      await fs.rmdir(settingsFile);
      await fs.rename(settingsFile + ".saved", settingsFile);
    }
    checks.push("a rejected provider write leaves both UI and Python offline, with a visible error");

    await page.getByLabel("Обработка команд", { exact: true }).selectOption("openai");
    await until(s => s.provider === "openai");
    await page.getByLabel("Модель Responses API").fill("fixture-model");
    await page.getByLabel("Завершать запись по тишине (Auto-stop on silence)").check();
    await until(s => s.settings.auto_stop_on_silence);
    assert.equal((await stored()).planner_model, "");
    assert.equal(await page.getByLabel("Модель Responses API").inputValue(), "fixture-model");
    await page.getByRole("button", { name: "Сохранить модель", exact: true }).click();
    await until(s => s.model === "fixture-model");
    await page.getByText("Модель сохранена на этом компьютере.", { exact: true }).waitFor();
    assert.equal((await stored()).provider, "openai");
    assert.equal((await stored()).planner_model, "fixture-model");
    await page.screenshot({ path: path.join(root, "qa/ui-wiring-settings.png"), fullPage: true, animations: "disabled" });
    await session.app.close();
    session = await launch(data);
    ({ page, state, until, nav } = session);
    await nav("Настройки");
    assert.equal(await page.getByLabel("Обработка команд", { exact: true }).inputValue(), "openai");
    assert.equal(await page.getByLabel("Модель Responses API").inputValue(), "fixture-model");
    await page.getByLabel("Модель Responses API").fill("invalid model name");
    await page.getByRole("button", { name: "Сохранить модель", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "Движок отклонил параметры" }).waitFor();
    assert.equal((await state()).model, "fixture-model");
    assert.equal((await stored()).planner_model, "fixture-model");
    checks.push("provider/model persist across restart; unsaved model stays a draft; invalid save cannot alter Python");
    await page.getByLabel("Обработка команд", { exact: true }).selectOption("offline");
    await until(s => s.provider === "offline");

    await nav("Пространство");
    await page.getByLabel("Ваше имя", { exact: true }).fill("UI trace fixture");
    await page.getByRole("button", { name: "Сохранить пространство", exact: true }).click();
    await until(s => !!s.notice);
    await page.getByRole("button", { name: "Закрыть уведомление", exact: true }).click();
    await until(s => s.notice === "");
    await nav("Настройки");
    await page.getByLabel("Цветовая тема").selectOption("Midnight");
    await until(s => s.settings.theme === "Midnight");
    assert.equal((await state()).notice, "");
    assert.equal(await page.locator(".notice-banner").count(), 0);
    checks.push("closing a Python notice clears it in the service and later publishes cannot resurrect it");
    assert.deepEqual(session.errors, []);
    await session.app.close();
    session = null;

    const mailData = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-ui-mail-fixture-"));
    const interpreter = path.join(mailData, "fixture-python");
    await fs.writeFile(interpreter,
      `#!/bin/sh\nexec ${quote(python)} -u -m tests.desktop_ui_wiring_fixture --stdio\n`, { mode: 0o700 });
    session = await launch(mailData, interpreter);
    ({ page, state, until, nav } = session);
    await nav("Подключения");
    await page.getByLabel("Microsoft Application (client) ID").fill("00000000-0000-0000-0000-000000000001");
    await page.getByLabel("Разрешаю подключить выбранный аккаунт Outlook").check();
    await page.getByRole("button", { name: "Подключить аккаунт", exact: true }).click();
    await until(s => s.account && s.can_run);
    await page.getByLabel("Кому (точные email через запятую)").fill("recipient@example.test");
    await page.getByLabel("Тема", { exact: true }).fill("Fixture attachment draft");
    await page.getByLabel("Текст письма").fill("Fixture body");
    const attachment = path.join(mailData, "fixture.txt");
    await fs.writeFile(attachment, "Fixture attachment bytes");
    await session.app.evaluate(({ dialog }, file) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] });
    }, attachment);
    await page.getByRole("button", { name: "Выбрать вложение до 32 KiB", exact: true }).click();
    await until(s => s.attachments.length === 1 && s.can_run);
    await page.getByLabel("Режим почтовых действий").selectOption("execute");
    await page.getByRole("button", { name: "Черновик в RAM", exact: true }).click();
    const saved = await until(s => s.drafts.length === 1 && s.can_run);
    const draftId = saved.drafts[0].id;
    await page.getByRole("button", { name: "Убрать вложения", exact: true }).click();
    await until(s => s.attachments.length === 0 && s.can_run);
    await page.getByLabel("Черновики в RAM текущего сеанса").selectOption(draftId);
    await until(s => s.mail_result.state === "loaded_draft" && s.can_run);
    await page.getByText(/fixture.txt · .*SHA-256/).waitFor();
    await page.getByRole("button", { name: "Убрать вложения", exact: true }).click();
    await until(s => s.can_run);
    await page.getByRole("button", { name: "Черновик в RAM", exact: true }).click();
    const updated = await until(s => s.drafts.length === 2 && s.can_run);
    const newId = updated.drafts.find(d => d.id !== draftId).id;
    await page.getByLabel("Черновики в RAM текущего сеанса").selectOption(newId);
    const loaded = await until(s => s.mail_result.state === "loaded_draft" &&
      JSON.parse(s.mail_result.data).attachments.length === 0 && s.can_run);
    assert.deepEqual(JSON.parse(loaded.mail_result.data).attachments, []);
    checks.push("native file picker→bounded bytes→Python RAM draft→reload→remove attachment→new Message has no attachments (fixture account)");

    await page.getByRole("button", { name: "Подготовить отправку", exact: true }).click();
    await until(s => s.prompt?.kind === "approval");
    await page.getByRole("dialog").waitFor();
    await session.app.evaluate(({ app, BrowserWindow }) => {
      if (process.platform === "darwin") app.show();
      app.focus({ steal: true });
      const w = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith("/index.html"));
      w.show(); w.focus(); w.webContents.focus();
    });
    const mic = page.getByRole("dialog").getByRole("button", { name: "Удерживайте и скажите «стоп»", exact: true });
    await mic.focus();
    await page.keyboard.down("Space");
    await until(s => s.voice_phase === "recording");
    assert.equal(await mic.isEnabled(), true);
    await page.keyboard.up("Space");
    await until(s => s.voice_phase === "idle");
    assert.equal((await state()).prompt.kind, "approval");
    await page.screenshot({ path: path.join(root, "qa/ui-wiring-mail-prompt.png"), animations: "disabled" });
    await fs.writeFile(path.join(mailData, "voice-stop"), "");
    const box = await mic.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await until(s => s.voice_phase === "recording");
    await page.mouse.up();
    await until(s => !s.prompt && s.can_run && s.voice_phase === "idle");
    assert.notEqual((await state()).mail_result.state, "accepted");
    assert.equal((await state()).started_count, 0);
    checks.push("mail auxiliary prompt accepts trusted Space/pointer hold and release; arbitrary speech cannot approve, stop cancels (synthetic audio)");
    assert.deepEqual(session.errors, []);
    const report = { platform: process.platform, liveServices: false, realMicrophone: false, checks };
    await fs.writeFile(path.join(root, "qa/ui-wiring-verification.json"), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report, null, 2));
  } finally { if (session) await session.app.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
