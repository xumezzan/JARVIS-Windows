# HUD / target highlight verification — 2026-10-24

Scope: separate transparent 320×110 Electron HUD, display-only preload, 300 ms pre-click
Windows target receipt, browser viewport highlight, exact target revalidation and cancellation.
Existing PermissionEngine and UI-only approval authority retained.

Passed on macOS:

- Full Python suite in new `/tmp/jarvis-hud-qa-20261024` venv: **813 passed, 7 skipped**.
  Five skipped tests need live model/voice/Windows opt-in; two need optional Pillow.
- Ruff check / format, mypy (139 source files), GUI smoke (`QT_QPA_PLATFORM=offscreen`), sdist/wheel build.
- React TypeScript/Vite build and actual Electron `qa:electron`: clean startup, real system.info,
  simulation, cancellation/clarification, responsive renderer, sandbox and owned shutdown.
- `node --test desktop/electron/overlay.test.cjs`: **2 passed**, including restore/cancel keys,
  unknown sender rejection, unavailable shortcuts fallback, prompt transition and shutdown.
- `qa:overlay`: actual Electron/Python fixture preview receipt after two frames + 300 ms,
  display-only HUD isolation/no overflow, original bounds restored, exact prompt stays interactive,
  simulation has no preview, cancellation hides both overlays. `overlay-hud.png` uses fixture
  command/tool labels; it is a UI test capture, not proof of a native Windows click.
- Browser tests run real local Chromium: pointer-events none, 300 ms duration, cleanup on cancel,
  changed target rejected. Windows helper tests use a real disposable fixture subprocess:
  no effect before receipt, cancellation kills/reaps helper; stale UIA geometry is never invoked.

Not passed/verified:

- `qa:voice` cannot obtain native focus here (`BrowserWindow.isFocused() == false`), so the
  trusted gesture check rejects recording. DOM focus alone is not enough. The check was not
  weakened. Existing Python voice tests passed; microphone/speaker hardware is not verified.
- Native Windows UIA/mouse execution, actual OS click-through, mixed-DPI/multi-monitor behavior,
  elevated apps and secure desktop require Windows acceptance. No privilege escalation added.

The old repository venv had hidden editable .pth metadata and unavailable Qt platform plugins;
initial checks failed in that environment, then were rerun in a fresh external venv installed
with `pip install -e '.[dev,voice]'`. No model, OAuth, mail or real microphone was used.
