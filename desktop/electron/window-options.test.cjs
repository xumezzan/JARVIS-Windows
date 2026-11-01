const { test } = require("node:test");
const assert = require("node:assert/strict");
const { mainWindowOptions } = require("./window-options.cjs");

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
  assert.deepEqual([window.x, window.y, window.width, window.height], [0, 33, 1512, 859]);
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
  assert.deepEqual([window.x, window.y, window.width, window.height], [-1920, 40, 1440, 860]);
});

test("minimum size never forces a small desktop window beyond its work area", () => {
  const window = options({ x: 0, y: 20, width: 800, height: 580 }, "linux");
  assert.deepEqual([window.width, window.height, window.minWidth, window.minHeight], [800, 580, 800, 580]);
});
