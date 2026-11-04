# Reachable window controls on macOS

The fixed 1536×1024 frameless window was larger than the MacBook's 1512×859 usable
desktop (Retina logical points, excluding the menu bar and Dock). Its right-hand
HTML controls could be outside the screen, and native macOS controls were disabled.

The main process now chooses the display under the pointer and fits the initial
outer window to its work area with a 16-point edge inset, including displays with
negative origins. The inset also tolerates small Dock-area changes during startup. Minimum
sizes shrink when necessary. macOS uses native close/minimize/zoom buttons at the
top left with space above the sidebar wordmark. Persistent high-contrast HTML
controls at the top right remain available when macOS shows its window-sharing
indicator in place of traffic lights. Windows keeps its existing HTML controls.
The title bar stays at the top when a short window or a long panel is scrolled.
Activating the app through the Dock restores a minimized window and its workbench.
The green Mac button zooms the window instead of entering a separate fullscreen
Space, so the same window can still become the task HUD. Native buttons hide only
in the existing 360×120 HUD and return when it restores for a result or prompt.

Window metadata is a fixed boolean in the isolated preload. No generic IPC,
approval authority, model-selected geometry or new OS permission is exposed.
The new main-process module is included in the fingerprinted prebuilt assets.

## Verification

```sh
node --test desktop/electron/*.test.cjs
npm --prefix desktop run build
node desktop/scripts/verify-desktop.cjs
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/unit/test_installation.py tests/integration/test_desktop_service.py \
  tests/integration/test_desktop_workspace.py
```

On the MacBook, 30 Node tests and 84 focused Python tests passed. Real Electron E2E
observed the window inside the work area with its edge inset,
native button position `{x:16,y:18}`, a clear wordmark safe area and all three HTML
controls inside the viewport. Minimize/restore, zoom/restore, close, owned runtime shutdown,
simulation, cancellation and renderer isolation passed. Ruff, format, win32 mypy,
Qt simulated startup and Python source/wheel builds passed. The sanitized existing
`desktop/qa/runtime-verification.json` records this run.

CI builds and checks the desktop contracts on Windows and macOS, and retains the
full Windows Python gates. Final hosted and installed-bundle results are recorded
in the pull request. Native Mac adapters are the separate PR #71; this fix is based
directly on main and does not merge those adapters. Live Windows GUI/hardware,
accounts and microphone acceptance are not established by these checks.

For native Windows UI verification after preparing its development runtime, run:

```powershell
npm --prefix desktop run build
node desktop/scripts/verify-desktop.cjs
```

API references: [Electron title-bar customization](https://www.electronjs.org/docs/latest/tutorial/custom-title-bar),
[window geometry and zoom options](https://www.electronjs.org/docs/latest/api/structures/base-window-options).
