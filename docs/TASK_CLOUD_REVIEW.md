# Ordinary tasks and cloud review in Electron

The owner chose this policy on 2026-10-26: ordinary tasks use the configured OpenAI
provider without repeated consent questions. Their enabled recent-task history and
explicitly selected memory are included under that standing authorization. The
non-secret `routine_cloud_consent` preference in `cloud_task_policy.json` records
it for this profile and survives restart. This separate file keeps older slots'
strict `ui_settings.json` schema compatible with rollback. It defaults to false
for other/fresh profiles; per-task choices
and pending review IDs are never persisted as approvals.

The permanent command/history/selected-memory consent controls were removed from
the home screen. A serious task instead creates an inline chat card with two
unchecked checkboxes: current-task data (required to continue) and prior history
(optional). The card states its command, reasons, recipient and one-task scope.
There is no modal data-consent window. Existing tool-effect approvals are separate.

The deterministic local policy checks money/purchases, sending/publishing,
deletion/overwrite, account/access/settings changes and long or explicitly complex
commands before any model call. A task that grows to four executed steps or needs
to transmit a screenshot pauses before the next provider call and asks in chat.
The provider cannot classify or approve its own access to new context.

Each pending question owns an immutable original request, random one-use ID,
expiry, and provider/settings snapshot. Refusal, cancellation, timeout, replay and
settings changes prevent further transmission. The history choice applies only
to that task; excluded historical, derived and knowledge context is stripped from
outbound input. Selected-memory IDs are resolved again after acceptance to detect
changes. The ordinary standing grant does not disable task-memory preferences.

Website-opening commands still use the local Chrome recipe and require neither
OpenAI nor a cloud review. Voice transcripts use the same gate and cannot approve
a serious task by themselves. An ordinary API request with a legacy explicit
one-task grant remains compatible, while serious tasks still require the pending
chat review.

Validation uses no live credentials or model service:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cloud_task_review.py tests/integration/test_conversation_memory.py tests/integration/test_desktop_service.py -q
$env:JARVIS_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
# Point PLAYWRIGHT_BROWSERS_PATH to the existing installed Chromium cache.
node desktop/scripts/verify-cloud-review.cjs <screenshot-output-directory>
```

The renderer fixture verifies the clean ordinary-task screen, the inline serious
review, unchecked defaults, optional history and desktop/mobile layout. It cannot
execute a model request or grant a real tool action.
