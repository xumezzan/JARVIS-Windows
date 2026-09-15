# Dashboard refinement — 15 September 2026

## Delivered design

The user's reference sets the visual direction: a midnight workspace around one blue
light sphere. The native PySide6 application now has Inter typography, semantic Midnight
and Graphite themes, aligned panels and controls, a real local-month calendar, registered
application entry points, an animation pause control, and a compact layout without
horizontal scrolling. Planner, voice entry and permissions retain their existing behavior.
Opening the shell does not start microphone capture.

`design-preview/` is a separate React prototype using actual shadcn components. It supports
local demo input, cancellation, state changes, calendar selection, navigation and themes.
It does not execute Python tools or connect to the native backend.

## Design system

- Font: bundled Inter, with Helvetica Neue / Segoe UI fallbacks.
- Body 13–14 px; support copy 11–12 px; panel headings 17–18 px; hero 48–58 px.
- Canvas `#090d12`, surface `#111820`, raised surface `#1a2430`, text `#edf2f8`.
- Muted text `#9cabbc`; blue interactive emphasis; teal for actual successful demo state.
- Spacing: 4 / 8 / 12 / 16 / 24 / 32. Controls 8–12 px corners; panels 16 px.
- Wide layout: navigation, history/tools, sphere/input, calendar/task.
- Compact native layout: icon navigation, sphere/input above two support columns.
- Mobile preview: horizontal icon navigation and one content column.
- Focus rings, descriptive control names, disabled state, input validation, cancellation,
  empty states and theme switching are retained. Animation stops when the native widget
  is hidden; web animation pauses when the document is hidden or reduced motion is requested.

## Requested toolchain actually used

| Tool | Use and status |
| --- | --- |
| Frontend Design | Official Anthropic skill installed into `.agents/skills/frontend-design`; used to define the reference-led direction. |
| UI/UX Pro Max | Installed with its CLI for Codex. Design-system searches informed Inter, spacing, states and layout. A generic handwritten-font suggestion was rejected against the explicit reference. |
| 21st | Installed/configured; CLI browser login succeeded. Searched `orb` and retrieved ElevenLabs Orb, id 8602, before implementation. CLI used for authenticated search; HTTP MCP still requires `API_KEY_21ST`. No raw credential was copied into the project. |
| shadcn MCP | Registered with Codex. Called the component-install command tool and audit checklist through the MCP SDK. CLI installed Button, Textarea, Badge, Calendar, Dialog, Select and Tooltip. |
| Chrome DevTools MCP | Registered with Codex and actually used through the MCP SDK for page opening, viewport emulation, screenshots, accessibility snapshots, input, keyboard checks and console inspection. |
| Impeccable | Installed for project Codex without hooks. Read context, craft floor, audit/native audit and polish instructions; performed audit, batch fixes and a confirming visual pass. Detector ran once and returned `[]`; screenshots and interactions were reviewed separately. |
| Context7 / Figma | Not used: Context7 connection was unconfirmed and no Figma source was provided. |

Local design skills are ignored by Git. MCP registrations are in the user's Codex config.
A subsequent Codex session can load those tools/skills normally. They are not runtime
dependencies of the Python application.

Sources: [Frontend Design](https://github.com/anthropics/skills/tree/main/skills/frontend-design),
[shadcn theming](https://ui.shadcn.com/docs/theming),
[21st Orb](https://21st.dev/@ElevenLabs-crawled/components/orb),
[Qt grid layout](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QGridLayout.html).

## Impeccable audit and polish

Audit target: the dashboard and its command-entry path, native and browser preview.
No claim of a complete accessibility conformance audit is made.

| Priority / category | Evidence and impact | Applied correction |
| --- | --- | --- |
| P1 Implementation | Retrieved WebGL Orb left a blank canvas and caused screenshot stalls. | Reused the native sphere artwork; removed WebGL and its 61 transitive packages. |
| P1 Responsive | Native fixed minimum content width caused horizontal scrolling on laptops. | Reflow at 1300 px; icon navigation and stacked support layout. Verified at 860×640. |
| P2 Integrity | Inactive service logos and motivational filler implied unavailable functionality. | Three real tool entry points, current-month calendar and actual session events. |
| P2 Theming | Surface/text colors and dialogs needed one shared theme. | Semantic QSS/CSS palettes; native theme changes also reach open/new planner and permissions dialogs. |
| P2 Layout | Browser calendar formed a heavy inner rectangle with oversized rows. | Transparent shared surface and consistent 36 px rows. |
| P2 Accessibility | Motion lacked a pause control; icon and calendar labels needed localization. | Pause controls, reduced-motion handling, Russian labels, Radix focus behavior and visible focus rings. |
| P3 Polish | Typography, input emphasis and spacing drifted across sections. | Bundled Inter, common panel primitives, a visible command label and consistent borders/spacing. |

Preserved strengths: one clear sphere focal point, readable dark contrast, distinct task
state, explicit demo boundary and the existing permission flow. Recommended audit actions
(`impeccable adapt`, `impeccable optimize`, `impeccable polish`) were completed in this pass.

## Verification

Native Cocoa screenshots: 1480×940 Midnight and Graphite, 860×640 compact.
Horizontal scrollbar maximum was zero in all three. The compact view scrolls vertically;
keyboard navigation to Chat brings the command input into view.

Chrome DevTools MCP: 1480×960, 1024×900, 390×844. Each reported document width equal to
viewport width, with no overflowing elements. Visually inspected all layouts and both themes.
Verified Ctrl+Enter, Escape cancellation, success count, animation pause, keyboard theme
selection, modal opening and Escape dismissal. Final console check: no errors or warnings.
The first WebGL browser process was replaced by an isolated headless Chrome session for QA;
the final preview has no WebGL dependency. No user browser tabs were modified.

Build: `npm run build` passed. Final JS 430.53 kB (136.12 kB gzip), down from the
initial 1.34 MB WebGL bundle. `npm audit` reported zero vulnerabilities at installation.

Python validation results are recorded below.
Windows execution and real microphone/model acceptance remain outside this macOS design pass.
For Windows acceptance, follow `docs/TESTING.md`; do not infer it from these screenshots.

### Python results

- Isolated virtual environment: `python -m pip install -e ".[dev]"` succeeded.
- Final full run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`:
  **292 passed, 5 skipped** (26.04 seconds).
- Latest focused dashboard run, including theme propagation and motion/reflow checks:
  **16 passed**.
- `python -m ruff check .`: passed; `python -m ruff format --check .`: 103 files formatted.
- `python -m mypy`: success, 85 source files.
- `python -m jarvis --smoke-test`: success.
- `python -m build`: wheel and sdist built; bundled Inter verified inside the wheel.
- Initial parallel QA run encountered `browser_cleanup` timeouts in the existing browser
  adapter. After closing the isolated DevTools session, the complete suite passed without
  adapter changes. This transient failure is retained here instead of omitted.
- Skips are explicit opt-in Windows (3), live model (1) and real voice (1) acceptance.

Windows verification (PowerShell, an interactive user session):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\python -m jarvis
.\.venv\Scripts\python -m pytest --run-windows tests/e2e/test_windows_acceptance.py
```

Inspect both themes and compact sizing interactively before calling Windows UI verified.
See the acceptance prerequisites in `docs/TESTING.md` before running real actions.

Screenshots: [native](images/dashboard-refined.png), [Graphite](images/dashboard-graphite.png),
[compact native](images/dashboard-compact.png), [web desktop](images/web-preview-wide.png),
[web mobile](images/web-preview-mobile.png).
