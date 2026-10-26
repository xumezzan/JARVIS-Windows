const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const {cleanEnvironment} = require("./environment.cjs");
const MAX_LINE = 1048576;
const MAX_REQUESTS = 8;

class Runtime {
  constructor(root, python, notify) {
    this.pending = new Map();
    this.state = null;
    this.buffer = Buffer.alloc(0);
    this.closed = false;
    this.failure = "";
    this.notify = notify;
    const environment = {
      ...cleanEnvironment(process.env),
      PYTHONNOUSERSITE: "1",
      PYTHONUTF8: "1",
      PYTHONPATH: path.join(root, "src"),
      PYTHONUNBUFFERED: "1",
    };
    this.child = spawn(
      python,
      ["-u", "-m", "jarvis.desktop.service", "--stdio"],
      {
        cwd: root,
        env: environment,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    this.child.stdout.on("data", (chunk) => this.read(chunk));
    // Native diagnostics may contain local paths. Do not forward or persist them in renderer/logs.
    this.child.stderr.on("data", () => {});
    this.child.stdin.on("error", () => {
      if (!this.closed) this.fail("runtime_disconnected");
    });
    this.child.once("error", () => this.fail("runtime_start_failed"));
    this.exited = new Promise((resolve) =>
      this.child.once("close", () => {
        if (!this.closed) this.fail("runtime_disconnected");
        resolve();
      }),
    );
    this.startupTimer = setTimeout(() => {
      if (!this.state) {
        this.fail("runtime_start_timeout");
        this.child.kill();
      }
    }, 15000);
  }
  fail(code) {
    clearTimeout(this.startupTimer);
    this.failure = code;
    this.state = null;
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error(code));
    }
    this.pending.clear();
    this.notify({ event: "unavailable", error: code });
  }
  read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      const index = this.buffer.indexOf(10);
      if (index < 0) break;
      if (index > MAX_LINE) return this.protocolFailure();
      const line = this.buffer.subarray(0, index);
      this.buffer = this.buffer.subarray(index + 1);
      let message;
      try {
        message = JSON.parse(line.toString("utf8"));
      } catch {
        return this.protocolFailure();
      }
      if (!message || typeof message !== "object" || Array.isArray(message))
        return this.protocolFailure();
      if (message.event === "fatal") {
        this.fail("runtime_initialization_failed");
        return;
      }
      if (message.event === "state") {
        if (!message.state || typeof message.state.ready !== "boolean")
          return this.protocolFailure();
        clearTimeout(this.startupTimer);
        this.failure = "";
        this.state = message.state;
        this.notify({ event: "state", state: this.state });
      } else if (message.event === "clipboard_poll") {
        if (!/^[a-f0-9]{32}$/.test(message.id)) return this.protocolFailure();
        this.notify(message);
      } else if (message.event === "target") {
        const rect = message.rectangle;
        if (!/^[a-f0-9]{32}$/.test(message.id) || !rect ||
            ![rect.left, rect.top, rect.right, rect.bottom].every(Number.isSafeInteger) ||
            Object.values(rect).some((value) => Math.abs(value) > 100000) ||
            rect.right <= rect.left || rect.bottom <= rect.top)
          return this.protocolFailure();
        this.notify(message);
      } else if (message.event === "transcript") {
        if (typeof message.text !== "string" || message.text.length > 4000)
          return this.protocolFailure();
        this.notify({ event: "transcript", text: message.text });
      } else if (
        typeof message.id === "string" &&
        typeof message.ok === "boolean"
      ) {
        const pending = this.pending.get(message.id);
        if (!pending) continue;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.ok) {
          this.state = message.state;
          pending.resolve(message.state);
        } else
          pending.reject(
            new Error(
              typeof message.error === "string"
                ? message.error
                : "runtime_operation_failed",
            ),
          );
      } else return this.protocolFailure();
    }
    if (this.buffer.length > MAX_LINE) this.protocolFailure();
  }
  protocolFailure() {
    this.fail("runtime_protocol_failed");
    this.child.stdin.end();
  }
  request(operation, fields = {}) {
    if (this.closed || this.failure || !this.state)
      return Promise.reject(new Error(this.failure || "runtime_not_ready"));
    if (this.pending.size >= MAX_REQUESTS)
      return Promise.reject(new Error("runtime_busy"));
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("runtime_response_timeout"));
      }, operation === "workspace_browser" ? 45000 : 10000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(
        JSON.stringify({ id, operation, ...fields }) + "\n",
        (error) => {
          if (error) {
            this.pending.delete(id);
            clearTimeout(timer);
            reject(new Error("runtime_disconnected"));
          }
        },
      );
    });
  }
  async stop() {
    if (this.closed) return;
    this.closed = true;
    clearTimeout(this.startupTimer);
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error("runtime_closed"));
    }
    this.pending.clear();
    this.child.stdin.end();
    const timeout = setTimeout(() => this.child.kill(), 12000);
    const kill = setTimeout(() => this.child.kill("SIGKILL"), 15000);
    await this.exited;
    clearTimeout(timeout);
    clearTimeout(kill);
  }
}
function pythonExecutable(root) {
  if (process.env.JARVIS_PYTHON) return process.env.JARVIS_PYTHON;
  const data = process.platform === "win32"
    ? process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local")
    : process.platform === "darwin"
      ? path.join(os.homedir(), "Library", "Application Support")
      : process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  const cached = path.join(data, "JarvisDesktop", "runtime",
    ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
  if (fs.existsSync(cached)) return cached;
  const candidates =
    process.platform === "win32"
      ? [
          path.join(root, ".venv-desktop", "Scripts", "python.exe"),
          path.join(root, ".venv", "Scripts", "python.exe"),
        ]
      : [
          path.join(root, ".venv-desktop", "bin", "python"),
          path.join(root, ".venv", "bin", "python"),
        ];
  return (
    candidates.find((candidate) => fs.existsSync(candidate)) ||
    (process.platform === "win32" ? "python" : "python3")
  );
}
module.exports = { Runtime, pythonExecutable };
