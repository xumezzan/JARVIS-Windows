// Main-process adapter. Size is checked after Electron's native read and before private IPC.
async function clipboardSample(clipboard, enabled) {
  if (!enabled) return "";
  try {
    const text = await clipboard.readText();
    return typeof text === "string" && text.length <= 16000 ? text : "";
  } catch {
    return "";
  }
}
module.exports = { clipboardSample };
