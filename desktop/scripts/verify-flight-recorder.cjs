// Renderer-only acceptance of the Python event projection. No live Windows claims.
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const project = path.resolve(__dirname, "../..");
  const fixture = spawnSync(process.env.JARVIS_PYTHON, [path.join(project, "tests/desktop_flight_recorder_fixture.py")], {
    cwd: project, env: { ...process.env, PYTHONUTF8: "1" }, encoding: "utf8",
  });
  assert.equal(fixture.status, 0, fixture.stderr);
  const state = JSON.parse(fixture.stdout.trim());
  const dist = path.join(project, "desktop/dist");
  const server = http.createServer(async (req, res) => {
    const relative = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    const file = path.resolve(dist, "." + (relative === "/" ? "/index.html" : relative));
    if (!file.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await fs.readFile(file);
      const type = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2" }[path.extname(file)];
      res.writeHead(200, { "Content-Type": type || "application/octet-stream" }); res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    await page.addInitScript(state => {
      window.jarvis = {
        status: async () => ({ state, error: "" }),
        subscribe: () => () => {},
        open: async () => state,
      };
    }, state);
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const failures = page.locator(".step-failure");
    await failures.first().waitFor();
    assert.equal(await failures.count(), 3);
    assert.match(await failures.first().innerText(), /Цель не найдена/);
    await failures.first().getByText("Flight Recorder · детали отказа").click();
    assert.match(await failures.first().innerText(), /_observed_target/);
    assert.match(await failures.first().innerText(), /unobserved_target/);
    assert.match(await failures.first().innerText(), new RegExp(state.task.steps[0].request_id));
    assert.match(await failures.last().innerText(), /не повторяйте/);
    const destination = process.argv[2] || path.join(project, "desktop/qa");
    await fs.mkdir(destination, { recursive: true });
    for (const [width, height] of [[1440, 1000], [1024, 768]]) {
      await page.setViewportSize({ width, height });
      for (const failure of await failures.all()) {
        assert.ok(await failure.evaluate(el => el.scrollWidth <= el.clientWidth + 1));
      }
      await page.screenshot({ path: path.join(destination, `flight-recorder-${width}.png`) });
    }
    console.log("Flight Recorder: three Python-projected failures; reason, stage, code, request ID and uncertainty warning visible at desktop sizes.");
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
