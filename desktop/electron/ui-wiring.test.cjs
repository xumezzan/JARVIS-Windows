const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("notice dismissal uses one fixed IPC channel without exposing gesture authority", () => {
  const exposed = {}, calls = [];
  vm.runInNewContext(fs.readFileSync(require.resolve("./preload.cjs"), "utf8"), {
    require: () => ({
      contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
      ipcRenderer: { on() {}, invoke: (...args) => { calls.push(args); } },
    }),
    window: { addEventListener() {} }, process: { platform: "darwin" },
  });
  exposed.jarvis.dismissNotice("Observed notice");
  exposed.jarvis.configure({ theme: "Graphite" });
  assert.deepEqual(calls, [
    ["jarvis-app:dismiss-notice", "Observed notice"],
    ["jarvis-app:configure", { theme: "Graphite" }],
  ]);
  for (const name of ["approve", "voiceStart", "request", "invoke"])
    assert.equal(exposed.jarvis[name], undefined);
});
