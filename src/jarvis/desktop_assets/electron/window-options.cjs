// Display geometry and native chrome are trusted main-process decisions.
function mainWindowOptions(screen, platform = process.platform) {
  const { workArea } = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  const width = Math.min(1536, Math.max(1, workArea.width - 32));
  const height = Math.min(1024, Math.max(1, workArea.height - 32));
  return {
    x: workArea.x + Math.floor((workArea.width - width) / 2),
    y: workArea.y + Math.floor((workArea.height - height) / 2),
    width,
    height,
    useContentSize: false,
    enableLargerThanScreen: false,
    minWidth: Math.min(1024, width),
    minHeight: Math.min(720, height),
    frame: platform === "darwin",
    ...(platform === "darwin" ? {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: { x: 16, y: 18 },
      // Native green button zooms the same window that becomes the task HUD.
      fullscreenable: false,
    } : {}),
  };
}

module.exports = { mainWindowOptions };
