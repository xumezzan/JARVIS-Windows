// Renderer-only fixture QA. No model, account, device or real action is used.
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const project = path.resolve(__dirname, "../..");
  const python = process.env.JARVIS_PYTHON;
  assert.ok(python, "Set JARVIS_PYTHON to the project test environment");
  const fixture = spawnSync(python, [path.join(project, "tests/desktop_cloud_review_fixture.py")], {
    cwd: project, env: { ...process.env, PYTHONUTF8: "1" }, encoding: "utf8",
  });
  assert.equal(fixture.status, 0, fixture.stderr);
  const states = JSON.parse(fixture.stdout.trim());
  const dist = path.join(project, "desktop/dist");
  const server = http.createServer(async (req, res) => {
    const relative = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    const file = path.resolve(dist, "." + (relative === "/" ? "/index.html" : relative));
    if (!file.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await fs.readFile(file);
      const type = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2" }[path.extname(file)];
      res.writeHead(200, { "Content-Type": type || "application/octet-stream" });
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.addInitScript(({ ordinary, review }) => {
      let state = ordinary;
      const listeners = new Set();
      const publish = () => listeners.forEach(fn => fn({ event: "state", state }));
      window.__showCloudReviewFixture = () => { state = review; publish(); };
      window.__cloudFixtureReply = null;
      window.jarvis = {
        status: async () => ({ state, error: "" }),
        subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
        cloudReview: async reply => {
          window.__cloudFixtureReply = reply;
          state = { ...ordinary, cloud_review: null }; publish(); return state;
        },
        open: async () => state,
      };
    }, states);
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator(".app-shell").waitFor();
    assert.equal(await page.locator('input[type="checkbox"]').count(), 0);
    await page.evaluate(() => window.__showCloudReviewFixture());
    const card = page.getByRole("region", { name: "Вопрос о данных задачи" });
    await card.waitFor();
    assert.equal(await page.getByLabel("Режим", { exact: true }).isDisabled(), true);
    assert.ok(await card.evaluate(el => el.scrollHeight <= el.clientHeight + 1));
    assert.equal(await page.locator("dialog[open]").count(), 0);
    assert.equal(await card.locator('input[type="checkbox"]').count(), 2);
    assert.equal(await card.getByRole("button", { name: "Продолжить задачу" }).isDisabled(), true);
    const destination = process.argv[2] || path.join(project, "desktop/qa");
    await fs.mkdir(destination, { recursive: true });
    await card.screenshot({ path: path.join(destination, "cloud-review-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await card.scrollIntoViewIfNeeded();
    assert.ok(await card.evaluate(el => el.scrollWidth <= el.clientWidth + 1));
    assert.ok(await card.evaluate(el => el.scrollHeight <= el.clientHeight + 1));
    await card.screenshot({ path: path.join(destination, "cloud-review-mobile.png") });
    await card.locator('input[type="checkbox"]').first().check();
    await card.getByRole("button", { name: "Продолжить задачу" }).click();
    assert.deepEqual(await page.evaluate(() => window.__cloudFixtureReply), {
      id: states.review.cloud_review.id, allow_current: true, allow_history: false,
    });
    console.log("Cloud review fixture: no permanent checkboxes; inline serious-task card; optional history; desktop/mobile layout passed.");
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
