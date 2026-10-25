# Windows cross-app tools — step 1

Implemented at the user's request on 2026-10-24. This expands the earlier
empty-Notepad-only MVP boundary. It does not establish native Windows acceptance
or completion of later roadmap milestones.

All operations remain registered tools routed through PermissionEngine and the
killable `jarvis.platforms.windows.worker` subprocess. Native libraries are loaded
only inside the helper. Simulation runs schema/policy validation without native
calls. The UI remains the sole source of expiring, single-use CONFIRM approvals.
Existing `windows.open_app` still launches only reviewed fixed executable paths
for Notepad, Chrome and VS Code; this change adds no shell or command arguments.

## Contracts

| Tool | Risk | Parameters and behavior |
| --- | --- | --- |
| `windows.get_open_windows` | SAFE | `{}` lists visible titled windows across applications. Optional `app` filters the executable stem; established IDs `notepad`, `chrome`, `vscode` remain compatible. Shell desktop/taskbar/tooltips and inaccessible/closing windows are omitted. Limit: 1000; `truncated` signals reaching it. |
| `windows.focus_app` | SAFE | Exactly one of `target`, `handle` (HWND), `title` (case-insensitive substring). Multiple title matches fail. Returns the resolved full `target`. |
| `windows.inspect_active_window` | SAFE | `max_depth`: 1–5, default 5. Returns the active `target`, `window_rectangle` and `elements` for visible enabled Button/Edit/Document/TabItem/ListItem controls. Each element includes mark_id (1..N), runtime ID, name (up to 120 characters), label_truncated, control type, automation ID and screen rectangle. Password controls are omitted. Traversal: at most 2048 nodes, output: 96 significant interactive elements, further bounded to 67000 escaped-JSON metadata bytes, with `truncated` when bounded. |
| `windows.type_text` | SAFE | Full observed `target` and literal `text`, max 4000 characters. Replaces the contents of the single supported editor, including a nonempty editor. A nonempty editor requires its observed content digest. Multiple candidate editors, password fields, content changes and tab changes fail. Native Edit/RichEdit or writable UIA ValuePattern only; independent text read-back verifies the result. |
| `windows.clipboard_get` | SAFE | `{}` returns Unicode `text` and `clipboard_sha256`. Text only; maximum 4000 Unicode characters, bounded native allocation before text conversion. No persistence in audit. |
| `windows.clipboard_set` | SAFE | `text`, including empty string, max 4000 characters. Replaces existing clipboard formats with Unicode text, with a helper-owned hidden HWND; returns read-back text and digest. Clipboard ownership is released on success/failure. |
| `windows.send_hotkey` | SAFE / CONFIRM | Full observed `target`, one `hotkey`: Ctrl+C / Ctrl+V / Ctrl+A / Enter / Tab / Alt+F4. Ctrl+V also requires the most recently observed `clipboard_sha256`; the worker checks text and rejects rich/non-text formats. Use `clipboard_set` to establish plain text. |
| `windows.click_element` | SAFE / CONFIRM | Full observed `target` plus either exact inspected `element`, or `x`, `y`, `window_rectangle`. UIA InvokePattern is preferred; click_input is used only when that pattern is absent, never after a failed/uncertain invocation. Coordinates must be inside unchanged observed window bounds; hit testing rejects another window covering the point. |
| `windows.screenshot` | SAFE | `scope`: `active_window` (default) or `screen` (all monitors). Native result contains `image_base64`, `image_format="JPEG"`, `width`, `height`. Fits inside 1280×800 without upscaling, quality 65; further reduces resolution until JPEG fits 48 KiB (base64 ≤64 KiB). Does not write files. PermissionEngine separates the image from textual Outcome metadata; Runner retains only the current frame outside history/JSONL. Active-window capture includes a fresh UIA observation and numbered Set-of-Marks boxes/badges, scaled relative to the window and final image size. It can include overlapping windows. Full-screen capture has no marks. |

The current action-specific risk table is in [PERMISSION_POLICY.md](PERMISSION_POLICY.md).
Ctrl+C/Ctrl+V/Ctrl+A/Tab and ordinary named UIA clicks are SAFE. Enter/Alt+F4,
sensitive/truncated labels, unnamed elements and coordinate clicks remain CONFIRM. A click may supply mark_id instead of the full element; Runner resolves it before prepare/approval. Marks expire after window mutations or a newer active-window observation. Unresolved marks never reach the native adapter.

Clicks and hotkeys reactivate the exact observed window, then check its foreground
HWND immediately before issuing input. This is necessary because the approval UI
itself takes focus. Process start time, PID, executable path, window runtime ID,
HWND and title are rechecked. UIA elements are resolved again within that window.
A successful click/hotkey result (`performed=true`) means input was issued. It does
not prove that a document was saved, a message was sent or an application closed;
inspect again to determine the application outcome. There is no retry or rollback.
Desktop input is not atomic with foreground/hit testing; user/OS interference can
still race with input. Elevated apps and secure desktops may be inaccessible.

Runner requires current-task window observations, inspector provenance for elements
or coordinate bounds, and the latest observed clipboard digest before pasting.
A title/handle focus selector must match an observed window before a model can use
it. Direct native resolution also rejects ambiguous title searches.

`Runner._observed_target()` already permits actions without a `target` field.
Therefore `clipboard_get`, `clipboard_set` and both screenshot scopes can run as
the first step, without a previous SUCCESS observation. No tool-name exemption
is needed. `send_hotkey` and coordinate `click_element` are window-bound actions:
they retain full target matching, and coordinate clicks additionally require
matching inspected bounds. They must not bypass provenance as global tools.
Rejection returns `PlanResult(error="unobserved_target")` before approval or
adapter execution. Regression coverage is in `tests/unit/test_planner_windows_targets.py`;
run it with `python -m pytest tests/unit/test_planner_windows_targets.py -q`.

Runner provenance audit on 2026-10-24 added 24 regression cases. Together with
`tests/unit/test_planner.py` and `tests/unit/test_windows_cross_app.py`, the focused
run passed **93 tests** on macOS using mock adapters. It covers first-step global
operations, simulation without adapter calls, all allowed hotkeys and coordinate
clicks after inspection, approval classification, and rejection of missing,
failed, simulated or mismatched observations before approval/adapter execution.
The production Runner required no change. Native Windows execution remains unverified.
The full macOS suite passed **802 tests, 5 skipped** (live model, voice and Windows
acceptance). Ruff lint/format, mypy (136 source files), GUI smoke, wheel/sdist build
and `git diff --check` passed. The first focused attempt had seven test assertion
failures because the scripted provider consumes its proposal list; the assertion
now uses the independent expected reply count, and the focused rerun passed.

The private pipe response budget is now 2,500,000 bytes, with an explicit asyncio
stream limit and helper output bound. The existing 65,536-byte action/input limit,
helper timeout, cancellation/kill/reap behavior and metadata-only audit remain.
Pillow and pywin32 are Windows-only, unpinned direct dependencies.

The tools are exposed through the existing planner registry. This step does not
add offline natural-language recipes, a manual form for every new tool, a screenshot
viewer, or vision input to the model provider. Its existing text-context budget can
reject large image observations; JPEG generation/transport is independent of vision
planning. Screenshots and clipboard content are untrusted observations, and may
contain personal data; existing cloud disclosure applies when using a cloud planner.

## Verification

On macOS, use an isolated Python 3.12 environment:

```sh
python -m pip install -e ".[dev,voice]" Pillow
python -m pytest tests/unit/test_windows*.py tests/unit/test_planner.py tests/integration/test_windows_ui.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy --no-incremental
python -m jarvis --smoke-test
python -m build
```

macOS verification on 2026-10-24: full suite **616 passed, 5 skipped**; Ruff lint
and formatting passed; mypy (`--no-incremental`, after installing voice extras)
passed for 126 source files. GUI smoke passed; wheel and sdist built successfully.
The initial mypy run lacked optional voice packages; the isolated environment was
completed and the check rerun successfully. Native/live opt-in tests were skipped.

Tests use synthetic UIA/Win32 objects and real disposable subprocesses. They cover
arbitrary app snapshots, selectors, traversal/output bounds, password exclusion,
content/tab/identity changes, approval and simulation, clipboard ownership/read-back,
rich/oversized clipboard rejection, stale paste content, coordinate hit testing,
no retry after uncertain invocation, JPEG bytes/dimensions and responses above the
old 60KB limit. They do not access the live clipboard, desktop or credentials.

On the actual Windows 11 target, after installing dependencies:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest tests/unit/test_planner_windows_targets.py -q
python -m pytest tests/unit/test_windows*.py tests/integration/test_windows_ui.py -q
python -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
python -m jarvis
```

Open the planner from the main window. Use disposable documents to manually check:

1. List and focus a non-allowlisted application by HWND and by a unique title fragment;
   duplicate title fragments must fail.
2. Inspect controls in that application and another app; compare names and rectangles
   with the visible UI. Switch tabs or move the window before approval to check rejection.
3. Read/set plain clipboard text, copy it from a disposable editor and paste into a
   second editor without approval. Change clipboard content before dispatch: reject.
4. Invoke a button, select a tab/list item, and check a coordinate fallback in a custom
   app. An overlapping window must prevent coordinate/fallback input.
5. Check every hotkey on disposable content, including Enter and Alt+F4 with exact
   approval. Verify the application outcome separately after input.
6. Decode active-window and screen JPEGs; confirm dimensions and multi-monitor/DPI behavior.
7. Cancel/timeout a slow UIA provider. Confirm the helper exits and no automatic retry occurs.

Native Windows UIA, clipboard ownership, focus/input delivery, actual screenshots,
high-DPI/multi-monitor behavior and clean Windows installation remain unverified here.
