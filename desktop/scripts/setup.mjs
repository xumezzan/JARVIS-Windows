import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import runtimeEnvironment from "../electron/environment.cjs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const repository = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const timeoutMs = 600000;
let activeChild;
let aborted = false;
export function abortSetup() {
  aborted = true;
  activeChild?.kill();
}
function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    if (aborted) return reject(new Error("Подготовка отменена."));
    const child = spawn(command, args, {
      cwd: repository,
      env: {
        ...runtimeEnvironment.cleanEnvironment(process.env),
        PYTHONPATH: path.join(repository, "src"),
        PYTHONNOUSERSITE: "1",
        PIP_CONFIG_FILE: os.devNull,
      },
      stdio: options.quiet ? "ignore" : "inherit",
      windowsHide: true,
      shell: false,
    });
    activeChild = child;
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Время подготовки Python истекло."));
    }, options.timeout || timeoutMs);
    child.once("error", () => {
      clearTimeout(timer);
      reject(new Error("Python недоступен."));
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      if (activeChild === child) activeChild = undefined;
      code === 0
        ? resolve()
        : reject(new Error("Не удалось подготовить зависимости Python."));
    });
  });
}
async function usable(python) {
  try {
    await run(
      python,
      [
        "-c",
        "import sys; assert sys.version_info >= (3,12); import jarvis.desktop.service; import pypdf, docx, psutil",
      ],
      { quiet: true, timeout: 20000 },
    );
    return true;
  } catch {
    return false;
  }
}
export async function ensurePython() {
  if (process.env.JARVIS_PYTHON) {
    if (await usable(process.env.JARVIS_PYTHON))
      return process.env.JARVIS_PYTHON;
    throw new Error(
      "JARVIS_PYTHON не содержит подходящий установленный движок.",
    );
  }
  const data =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local")
      : process.platform === "darwin"
        ? path.join(os.homedir(), "Library", "Application Support")
        : process.env.XDG_DATA_HOME ||
          path.join(os.homedir(), ".local", "share");
  const runtime = path.join(data, "JarvisDesktop", "runtime");
  const executable = (folder) =>
    path.join(
      folder,
      ...(process.platform === "win32"
        ? ["Scripts", "python.exe"]
        : ["bin", "python"]),
    );
  const target = executable(runtime);
  if (existsSync(target) && (await usable(target))) return target;
  const candidatePaths = [
    executable(path.join(repository, ".venv-desktop")),
    executable(path.join(repository, ".venv")),
  ];
  for (const candidate of candidatePaths) {
    if (existsSync(candidate) && (await usable(candidate))) return candidate;
  }
  let base =
    process.platform === "win32"
      ? path.join(data, "JarvisInstall", "runtime", "python.exe")
      : "python3";
  try {
    await run(base, ["-c", "import sys; assert sys.version_info >= (3,12)"], {
      quiet: true,
      timeout: 10000,
    });
  } catch {
    if (process.platform === "win32")
      throw new Error(
        "Запустите scripts/windows/Install-Jarvis.cmd: он установит Python автоматически. После этого повторите запуск Jarvis.",
      );
    throw new Error("Для подготовки приложения нужен Python 3.12 или новее.");
  }
  console.log("Jarvis: подготовка изолированного локального движка…");
  await mkdir(path.dirname(runtime), { recursive: true });
  await run(base, ["-m", "venv", runtime]);
  await run(target, [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--index-url",
    "https://pypi.org/simple",
    "-e",
    `${repository}[voice]`,
  ]);
  if (!(await usable(target)))
    throw new Error("Движок установлен, но проверка импорта не пройдена.");
  return target;
}
if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  process.once("SIGINT", abortSetup);
  process.once("SIGTERM", abortSetup);
  try {
    await ensurePython();
    console.log("Jarvis: локальный движок готов.");
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
