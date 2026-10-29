const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("workspace preload exposes one fixed settings channel without approval authority", () => {
  const exposed = {}, calls = [];
  vm.runInNewContext(fs.readFileSync(require.resolve("./preload.cjs"), "utf8"), {
    require: () => ({
      contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
      ipcRenderer: { on() {}, invoke: (...args) => { calls.push(args); } },
    }),
    window: { addEventListener() {} },
  });
  const command = { operation: "read" };
  exposed.jarvis.workspace(command);
  assert.deepEqual(calls, [["jarvis-app:workspace", command]]);
  assert.equal(exposed.jarvis.approve, undefined);
  assert.equal(exposed.jarvis.request, undefined);
  exposed.jarvis.workspaceBrowser();
  exposed.jarvis.workspaceBrowser("live");
  assert.deepEqual(calls.slice(1), [
    ["jarvis-app:workspace-browser", "authenticated_workspace"],
    ["jarvis-app:workspace-browser", "live"],
  ]);
});

test("workspace browser, calendar and dynamic MCP labels are readable", async () => {
  const { toolLabel } = await import("../src/runtime.ts");
  for (const name of ["browser.open_workspace_app", "browser.scroll", "browser.press_key",
    "browser.trigger_1password_autofill", "calendar.list_events", "calendar.create_draft_event",
    "calendar.create_event", "mcp.crm__find_client", "mcp_crm.find_client"]) {
    assert.notEqual(toolLabel(name), name);
    assert.match(toolLabel(name), /[А-Яа-я]/);
  }
  assert.equal(toolLabel("unknown.tool"), "unknown.tool");
});
