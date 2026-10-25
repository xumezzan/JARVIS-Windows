import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import electron from "electron";
import { createServer } from "vite";
import { ensurePython, abortSetup } from "./setup.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const built = process.argv.includes("--built");
let server;
let child;
let stopping = false;

async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  abortSetup();
  child?.kill();
  await server?.close();
  process.exitCode = code;
}

process.once("SIGINT", () => void stop());
process.once("SIGTERM", () => void stop());

try {
  const python = await ensurePython();
  if (stopping) process.exit(0);
  if (built) {
    await access(path.join(root, "dist", "index.html"));
  } else {
    server = await createServer({
      root,
      server: { host: "127.0.0.1", port: 5173, strictPort: true },
    });
    await server.listen();
    server.printUrls();
  }
  const environment = { ...process.env, JARVIS_PYTHON: python };
  delete environment.ELECTRON_RUN_AS_NODE;
  child = spawn(electron, [root, ...(built ? [] : ["--jarvis-dev"])], {
    cwd: root,
    stdio: "inherit",
    env: environment,
  });
  child.once("error", (error) => {
    console.error("Unable to launch Electron:", error.message);
    void stop(1);
  });
  child.once("exit", (code) => void stop(code ?? 0));
} catch (error) {
  console.error("Unable to start Jarvis desktop:", error.message);
  await stop(1);
}
