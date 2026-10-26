# Open a website in the owner's Chrome

The Electron command bar accepts exact local navigation recipes, including
`открой сайт apple.com` and `открой браузер и перейди на сайт apple.com`.
A bare domain becomes a canonical HTTPS URL. This single, literal command does not
need an OpenAI request or cloud consent. Other commands retain their existing
planner and consent rules; historical context cannot select a URL for this recipe.

`открой сайт instagram` (also `инстаграм`) resolves through the reviewed local alias
to `https://www.instagram.com/`. Unknown website names ask for an explicit domain;
the recipe does not append `.com` to an arbitrary brand. Tool input also accepts
an explicit bare domain and HTTPS URLs without a trailing slash, canonicalizing
them before creating the exact approval snapshot. URL scheme, public-address,
blocked-site and DNS checks still apply. The native helper receives only the
canonical URL. Denial text for external Chrome describes that operation rather
than suggesting that the Strict Sandbox allowlist governs the owner's Chrome.

`windows.open_chrome_url` is a separate CONFIRM capability. Its approval snapshot
binds the canonical URL, `external-chrome` service and `current-profile` account.
It validates the initial public HTTPS destination and existing blocked site tiers,
preflights DNS after approval, then hands that exact URL to the installed Chrome
executable with the fixed `--new-tab` switch and `shell=False` in the killable
Windows helper. It never accepts an executable, profile path or extra switches.
Existing windows are not closed or restarted, and cookies are not read or copied.
Chrome selects its existing profile and owns the navigation after this handoff.

This is an external-browser launch receipt, not a verified page-load or DOM
observation. The approval dialog and factual report say so. Redirects and later
requests in the owner's browser are managed by Chrome, without Jarvis request
interception. Strict Sandbox and its allowlist remain in force for `browser.*`;
this launch does not provide those tools access to the owner's tabs/accounts.
Simulation, denial and stale approvals cannot launch Chrome. No remote-debugging
port or separate Chrome profile is required for this limited operation.

Network policy rejections during tool preparation now retain `network_denied`
and DENIED, with the registered tool name in the payload-free audit, rather than
becoming `invalid_request`. Malformed input still yields INVALID.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chrome_command.py tests/unit/test_browser.py tests/unit/test_windows.py tests/integration/test_desktop_service.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
```

Live installation and Chrome checks are recorded after running these gates.
