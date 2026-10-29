# Ghost Cursor and Human Takeover

Ghost Cursor is enabled automatically in Electron, including the installed Windows
slot. The transparent, non-focusable window covers the virtual desktop, ignores
mouse events and exposes only display/paint IPC. It has no approval or tool API.
Physical pixel rectangles become Electron DIP coordinates at the main-process
boundary; the React cursor moves for 280 ms, paints a target frame and click ring,
and acknowledges its paint before the adapter continues. Idle hides the surface.
Yield uses an amber cursor displaced 40 px with an explicit pause badge.

Windows clicks use Invoke, Toggle, SelectionItem or messages to the exact observed
control HWND. Typing retains directed edit messages and ValuePattern. Both typing
and clicks revalidate the control after the display receipt. Failure after a UIA
pattern is issued never retries a different pattern. A custom control without a
usable pattern/HWND requires the existing explicitly enabled, observed, confirmed
coordinate fallback. That fallback saves GetCursorPos and submits move/down/up/
restore as one SendInput batch, with emergency restoration after partial failure.
There are no sleeps. Windows cannot guarantee a hard scheduling deadline below
2 ms; this is not a real-time operating system. Semantic and browser input never
move the physical cursor at all.

Browser click/fill/wheel stay inside Playwright/CDP. Headed Windows previews use
the actual visible Chromium render-widget HWND client rectangle, not an estimate
of browser borders, toolbars or devicePixelRatio. CSS viewport coordinates scale
into that physical rectangle, including zoom and negative monitor origins. The
bounded helper is terminated on cancellation/timeout. A missing viewport refuses
the action rather than displaying guessed coordinates. Anonymous headless browser
contexts have no visible screen rectangle and do not fabricate a ghost position.

During Runner execution a lightweight Windows poller detects physical clicks,
typing, and physical movement within 80 px of the ghost target in its foreground
window. Low-level hooks filter injected events and retain counters for short taps
between GetAsyncKeyState polls. Only transient button/key state and counters are
held; no text or input history is recorded. Hooks are unregistered when the run
ends. Simulation never starts the monitor.

An intervention latches Yield until two seconds without clicks, typing or continued
mouse motion. Runner cancels an unissued prepared action and invalidates its
approval; a proposal computed during the interruption is discarded. Old UIA/DOM
results and screenshots are removed from the next provider context and cannot
ground another action. Runner observes the current active window and, when a
browser target was in use, obtains fresh tabs and reads its current document.
Those reads go through PermissionEngine and must still be SAFE. Cancellation,
total task time, step budgets, durable audit and uncertain-effect guards remain.

## Verification

Run from the repository with its prepared virtual environment:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
node desktop/scripts/verify-ghost.cjs
```

On this transferred checkout the development venv is stored above the repository
to avoid MAX_PATH and exposed locally as `.venv`. Tests use the existing local
Chromium cache and a short temporary directory (see `DEVELOPMENT.md` above the
repository). Windows subprocess/Qt tests need a normal desktop execution context.

The actual Electron fixture writes `desktop/qa/ghost-verification.json`,
`ghost-acting.png` and `ghost-yielding.png`. It checks real React paint, animation
timing, pointer transparency, amber state and hiding on idle, and records physical
cursor positions before/after. Unit tests cover geometry/DPI, short physical taps,
quiet-period reset, cancellation, stale UIA/DOM grounding and native cursor restore.
Local Chromium tests exercise click/fill/wheel without any live service/provider.

`Install-Jarvis.ps1` installs source-fingerprinted prebuilt resources, checks that
the real slot's startup receipt includes `ghost_cursor: true`, and refreshes the
stable `Jarvis.lnk`. This is separate from opt-in live account/microphone and full
Windows MVP acceptance. Do not infer those results from fixture checks.
