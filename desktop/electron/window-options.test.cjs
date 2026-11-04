const { test } = require("node:test");
const assert = require("node:assert/strict");
const { mainWindowOptions, showMainWindow } = require("./window-options.cjs");

function options(workArea, platform = "darwin") {
  const cursor = { x: workArea.x + 100, y: workArea.y + 100 };
  return mainWindowOptions({
    getCursorScreenPoint: () => cursor,
    getDisplayNearestPoint: (point) => {
      assert.equal(point, cursor);
      return { workArea, scaleFactor: 2 };
    },
  }, platform);
}

test("MacBook startup fits the usable Retina work area in logical points", () => {
  const window = options({ x: 0, y: 33, width: 1512, height: 859 });
  assert.deepEqual([window.x, window.y, window.width, window.height], [16, 49, 1480, 827]);
  assert.equal(window.useContentSize, false);
  assert.equal(window.enableLargerThanScreen, false);
  assert.equal(window.frame, true);
  assert.equal(window.titleBarStyle, "hiddenInset");
  assert.equal(window.fullscreenable, false);
  assert.deepEqual(window.trafficLightPosition, { x: 16, y: 18 });
});

test("larger Windows displays retain the preferred size and custom controls", () => {
  const window = options({ x: 100, y: 40, width: 2560, height: 1360 }, "win32");
  assert.deepEqual([window.x, window.y, window.width, window.height], [612, 208, 1536, 1024]);
  assert.equal(window.frame, false);
  assert.equal(window.titleBarStyle, undefined);
  assert.equal(window.trafficLightPosition, undefined);
});

test("a secondary display with a negative origin keeps the whole window reachable", () => {
  const window = options({ x: -1920, y: 40, width: 1440, height: 860 });
  assert.deepEqual([window.x, window.y, window.width, window.height], [-1904, 56, 1408, 828]);
});

test("minimum size never forces a small desktop window beyond its work area", () => {
  const window = options({ x: 0, y: 20, width: 800, height: 580 }, "linux");
  assert.deepEqual([window.width, window.height, window.minWidth, window.minHeight], [768, 548, 768, 548]);
});

test("Dock activation restores a minimized window and explicitly restores its workbench", () => {
  const calls = [];
  showMainWindow({
    isDestroyed: () => false,
    isMinimized: () => true,
    restore: () => calls.push("restore"),
    show: () => calls.push("show"),
    focus: () => calls.push("focus"),
  }, {restore: (...args) => calls.push(args)});
  assert.deepEqual(calls, [[true, true], "restore", "show", "focus"]);
});

test("activating an existing normal window keeps its geometry", () => {
  const calls = [];
  showMainWindow({
    isDestroyed: () => false,
    isMinimized: () => false,
    restore: () => { throw new Error("normal window must not be restored"); },
    show: () => calls.push("show"),
    focus: () => calls.push("focus"),
  });
  assert.deepEqual(calls, ["show", "focus"]);
});

test("activation never creates or revives a missing window", () => {
  assert.doesNotThrow(() => showMainWindow(undefined));
  assert.doesNotThrow(() => showMainWindow({isDestroyed: () => true}));
});
