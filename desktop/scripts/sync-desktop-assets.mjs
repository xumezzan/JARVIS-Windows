// Commit the prebuilt, source-fingerprinted UI so target installation needs no Node/npm.
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = path.join(root, "../src/jarvis/desktop_assets");
const files = ["package.json", "electron/main.cjs", "electron/preload.cjs", "electron/runtime.cjs",
  "electron/environment.cjs", "electron/overlay.cjs", "electron/overlay-preload.cjs",
  "electron/hud-mode.cjs", "electron/clipboard.cjs", "electron/target.html"];
async function tree(directory) {
  const result = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const name = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error("linked_desktop_source");
    if (entry.isDirectory()) result.push(...await tree(name));
    else result.push(name);
  }
  return result.sort();
}
const hash = async (file) => crypto.createHash("sha256").update(await fs.readFile(file)).digest("hex");
await fs.mkdir(target, { recursive: true });
// Only this explicitly generated dist directory is replaced; source files stay in desktop/.
await fs.rm(path.join(target, "dist"), { recursive: true, force: true });
await fs.cp(path.join(root, "dist"), path.join(target, "dist"), { recursive: true });
for (const file of files) {
  await fs.mkdir(path.dirname(path.join(target, file)), { recursive: true });
  await fs.copyFile(path.join(root, file), path.join(target, file));
}
await fs.copyFile(path.join(root, "node_modules/@fontsource-variable/inter/LICENSE"), path.join(target, "LICENSE-Inter.txt"));
const sources = [...await tree(path.join(root, "src")), ...await tree(path.join(root, "electron")),
  path.join(root, "index.html"), path.join(root, "package.json"), path.join(root, "vite.config.ts")];
const manifest = { schema: 1, sources: {}, files: {} };
for (const file of sources.sort()) manifest.sources[path.relative(root, file).split(path.sep).join("/")] = await hash(file);
for (const file of await tree(target)) {
  const name = path.relative(target, file).split(path.sep).join("/");
  if (name !== "manifest.json") manifest.files[name] = await hash(file);
}
await fs.writeFile(path.join(target, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log("Prebuilt Electron UI synchronized with its source fingerprint.");
