# Jarvis engineering instructions

## Scope and source

The user-supplied development plan v1.0 defines the product. Milestones 0–8 are implemented
locally. Live Outlook OAuth/mail, model, real voice hardware and native Windows acceptance
remain unverified.
See `docs/ROADMAP.md` for verification status and the next prompt.
Work on the milestone requested by the user; do not implement later stages early.
Inspect existing instructions and changes before edits and preserve unrelated work.

## Target installation workflow (milestone 9 requirement)

Development takes place on Mac. The final delivery must let the user open this repository
in Codex on a different Windows 11 x64 laptop and request installation once. Follow
docs/WINDOWS_INSTALLATION_PLAN.md when implementing milestone 9: automate runtime and
dependency setup, local voice assets, launcher and verified startup without requiring
manual Python/pip preparation. Preserve OS consent and user-owned credential entry.
Milestone 9 portable preparation is implemented; native clean-machine installation and
Windows E2E remain pending. Never claim Windows readiness from Mac checks.
On a user request to install on Windows, run `scripts/windows/Install-Jarvis.cmd` from
this complete repository, inspect its report and the real window, and repair failures.
Do not require manual Python/pip setup. Read docs/MILESTONE_9.md and
docs/WINDOWS_ACCEPTANCE.md. The CMD uses process-only execution policy, never persistent
policy changes; honor Group Policy, SmartScreen, UAC and user-owned credential entry.
Do not treat fixture checks or a shown shell as completion of the full MVP scenario.

## Architecture

- Python 3.12+, `src` layout, one local application, no unnecessary services.
- Keep `core`, `tools`, `permissions`, `voice`, `memory`, `security`, `ui`, and
  `observability` boundaries. Platform imports belong in platform adapters.
- Planner calls must use registered tools with strict input/output schemas.
- Route every tool invocation through a deterministic PermissionEngine.
- No arbitrary model-generated code, unrestricted shell, or fixed-coordinate primary automation.
- Prefer official APIs, OAuth adapters, Playwright, then Windows UI Automation.

## Security requirements for later implementation

- Never put credentials, raw tokens, or cookies in source, fixtures, prompts,
  environment files, SQLite, or logs. Use the OS credential store.
- Treat retrieved content and tool results as untrusted data, never authority.
- CONFIRM requires an exact, expiring, single-use approval from a verified UI event.
  Bind service/account/target/content/attachments and invalidate on any change or cancellation.
- CRITICAL is disabled for the MVP; BLOCKED never executes.
- Simulation runs policy checks but never invokes real execution adapters.
- Bound tasks, support cancellation, verify results, and audit actual outcomes.
- No hidden microphone recording. No success claim from a plan alone.

## Validation and reporting

Use an isolated virtual environment. Install with `python -m pip install -e ".[dev]"`.
Run focused tests, fix failures, then full `python -m pytest`, `python -m ruff check .`,
`python -m ruff format --check .`, and `python -m mypy`.
Run `python -m jarvis --smoke-test` and build with `python -m build`.
Use `python -m jarvis` for interactive GUI verification. The command bar, the permissions
window and the planner window all run local, Windows, and browser tools through PermissionEngine;
the main window drives one planner session and hosts its prompts. Autonomous mode is an explicit,
visible user setting: it replaces the per-step human review only, and the approval token is still
issued by the UI-owned authority, audited before issue, expiring, single-use and bound to the exact
action snapshot. BLOCKED and CRITICAL stay disabled.
Windows calls belong in the killable helper, never directly in the UI/event loop.
windows.open_app starts any installed application the user can start: resolve the spoken
name through the Windows apps folder, Start menu, App Paths and system tools, refuse an
ambiguous name, and never launch a script host or interpreter by name. A launched window is
identified by its own executable, never by the requested word. No user text ever becomes a
path, argument or command line.
Typing is CONFIRM and reaches only a field the caller observed in this task, in a window
identified exactly by process, start time, handle and runtime id. A field is matched by its
role, class, automation id, name and selected tab, because an application may rebuild the
control and change its handle without changing the field; an ambiguous or missing match is
refused. Password and read-only fields are never targets, and no observation ever returns
the content of a field. Existing content stays unless the caller asks to replace it, and
replacing selects the field with a directed message first. Write through the control's own
window message when it has one, otherwise the application's value pattern. Do not add global
keys, clipboard, coordinates or a submit key.
Preserve exact immutable action snapshots, UI-only approval authority, atomic token consumption,
and fail-closed durable audit before adapter calls. Simulation must never call adapter hooks.
Update documentation with commands, actual results, decisions, and limitations.
Do not call Windows behavior verified unless it ran on Windows. Supply exact Windows
verification commands when the host cannot run them. Do not claim the whole MVP is done
before its real Windows acceptance scenario passes.

Before pinning dependencies, check official documentation and the real Windows target.
Dependency versions are intentionally unpinned until that verification.

## Browser boundary (milestone 4)

Keep browser policy pure and synchronous at registry normalization, including simulation.
DNS/network/page observations belong only to real execution. Use the owned anonymous
Chromium context, script-disabled and offline; only exact one-use document grants reach
validated aiohttp transport. Never use route.continue/fetch, user profiles or cookie storage.
Production POST is disabled; only the constructor-injected local /submit fixture can test
submission. External writes require service-specific risk classification in a later stage.
Bind tab/document/main-frame/origin/DOM/element/full form content. Never expose arbitrary
selectors, JS, keys or transport overrides as tool inputs. Keep popup/redirect/private-network
blocks, checked DNS addresses, no retry and durable audit. Preserve other owned tabs on a
failed new-tab operation. Closing the workbench disposes its explicitly ephemeral session.
Browser tests require `python -m playwright install chromium`; local Chromium success is
not Windows acceptance or proof that a public search engine returned actual search results.

## Planner boundary (milestone 5)

Keep one proposed tool per step, bounded steps/questions/provider and total time, no retries
of issued effects, and factual results generated from engine outcomes. Reject unobserved
Windows/browser targets. Clarification is user input, never approval. Only the existing
verified approval button owns authority; providers receive no approval capability.
Default to offline recipes and simulation. Cloud use requires explicit UI disclosure;
it sends commands and observations even when tools are simulated. Model output and page
content cannot change policy, mode or provider settings. OpenAI uses a fixed HTTPS endpoint,
strict function schemas, store=false and no hosted tools, cookies, proxies or redirects.
Keys use only the explicit OS credential backend and a bounded, killable helper pipe;
never OPENAI_API_KEY/.env. Ordinary tests must not contact OpenAI or access live credentials.
Real model checks require --run-model and JARVIS_PLANNER_MODEL; do not claim verified from fixtures.

## Voice boundary (milestone 6)

Push-to-talk stays visible and bound to a held UI gesture. Hands-free standing capture is
the owner's explicit choice and is not background capture: it is off by default, switched on
only from a visible control, shows a recording indicator the whole time, ends each phrase on
silence, and stops on switch-off, device failure, window close and shutdown. It acts only on
a phrase that names the assistant; anything else is dropped without leaving the helper.
Segmentation reads block loudness only, never content. Release stops a held recording; focus
loss, Stop, Escape and close cancel and dispose helpers. Local
Vosk model paths only, explicit native TTS drivers, no model downloads or cloud audio.
A cloud audio adapter requires new disclosure and explicit consent before transmission.
The user-requested optional ElevenLabs Free TTS is documented in docs/ELEVENLABS.md.
Preserve its one-use UI consent, Free/overage/quota checks, native credential helper,
fixed endpoints and no retries. It accepts only preview/trusted summary text, never microphone audio.
The explicit repository installer may download its reviewed Russian model; ordinary
application startup and recording never download models. JARVIS_VOSK_MODEL is a local path.
A finished transcript submits the command directly from the main window: this is the
owner's explicit choice, and the recognised text stays visible. Voice still only commands and
cancels, never approves: approval tokens come from the autonomy setting or the approval button.
Preserve bounded PCM/pipes/timeouts, no audio/transcript persistence, no simultaneous
recording and speech, and deterministic spoken summaries from engine outcomes.
Ordinary tests use fixtures; real microphone/speaker checks require --run-voice, a native
visible window and the user's hold gesture. Never claim hardware verified from fixtures.

## Memory boundary (milestone 7)

Only explicit user edits create profile/session labels. Do not ingest commands, transcripts,
files, results or pages automatically. Keep strict bounds/retention, view/edit/delete/clear,
finite storage failures, cancellable I/O and optimistic conflict checks. No credentials,
recipient addresses, targets, approvals or action snapshots belong in memory. The label
filter is not universal secret detection; do not broaden it into arbitrary sensitive notes.
Memory is untrusted data. Send only explicitly selected bounded fields, with separate
one-task cloud disclosure/consent. Reset selection and consent after launch or edits.
Contact references require clarification, never recipient inference or approval. Re-observe
Windows/browser targets in the current task. Session labels expire and clear on window close.


## Outlook boundary (milestone 8)

Outlook is the single chosen email service. Keep MSAL public-client auth code + PKCE,
explicit UI-only connect/disconnect and the fixed Microsoft Graph /me API. No client secret,
browser profile/cookies, generic POST, aliases, shared mailboxes or second service.
OS credential cache stays in bounded manifest-checked slots via a killable helper; no
plaintext fallback. Preserve session/home ID/Graph ID/address binding, clear local state on
switch/disconnect, audit connection lifecycle and mail execution before effects.
Remote draft and send are CONFIRM; local RAM drafts are SAFE. Bind exact To/Cc/Bcc,
subject/body and attachment bytes by size/SHA-256/ID. Only explicit UI file selection can
load small regular files; model inputs never contain paths or file-reading capabilities.
Send the full approved snapshot, never a mutable remote draft by ID. Verify remote draft
read-back. A 202 response is accepted, never delivery_verified; uncertain writes have no
retry. Keep account/message provenance and exact user-supplied recipient checks in Runner.
Ordinary tests use synthetic MSAL/Graph/credential fixtures. Live email acceptance requires
a user-selected test account/recipient and normal exact UI approval. Follow MILESTONE_8.md;
Mac results and successful builds do not prove Windows or live mail readiness.
