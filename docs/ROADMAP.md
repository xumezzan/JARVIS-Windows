# Roadmap

Работа идёт по этапам из исходного плана v1.0. Новый этап запускается отдельной задачей.
Текущий scope — этап 4. Browser adapter реализован локально; native Windows-приёмка ожидается.
Результаты: `MILESTONE_0.md`, `MILESTONE_1.md`, `MILESTONE_2.md`, `MILESTONE_3.md`, `MILESTONE_4.md`.

| Этап | Результат и критерий выхода | Статус |
| --- | --- | --- |
| 0 Foundation | Документы, package, установка, тесты, lint, types, команды для Windows | Реализован; native Windows не проверен |
| 1 Desktop shell | PySide6, состояния, текст, fake worker без блокировки, activity, config/logging, smoke | Реализован локально; native Windows не проверен |
| 2 Tools / permissions | Typed registry, risk, exact expiring single-use approvals, simulation, audit | Реализован локально; native Windows не проверен |
| 3 Windows | open/focus/list/type, Notepad read-back, Chrome/VS Code, UIA без координат | Реализован; portable tests пройдены, native Windows не проверен |
| 4 Browser | open/navigate/search/click/type/read/tabs/close, Playwright, domain policy, timeouts | Реализован локально: статический анонимный Chromium; Windows не проверена |
| 5 Planner | Strict calls, только registry, bounded plan, cancellation, уточнения, проверенные ответы | Следующий; не начат |
| 6 Voice | Push-to-talk, transcript, STT/TTS, русские команды, voice/button cancel | Не начат |
| 7 Memory | Профиль и короткий контекст, view/edit/delete, уточнение контактов, без secrets | Не начат |
| 8 External service | Gmail или Outlook OAuth/API, drafts, полный preview, send через permissions | Не начат |
| 9 E2E / packaging | Реальный Windows-сценарий, failure tests, PyInstaller, чистая установка | Не начат |

## Обязательные ворота качества

Каждый этап: focused tests → исправления → full suite → Ruff lint/format → mypy →
применимый реальный запуск → обновление docs и отчёт. Mock-тест не доказывает работу
Windows. Перед подключением Gmail/Outlook должны пройти негативные тесты PermissionEngine.

Этап 2 обязательно проверяет SAFE без approval, CONFIRM без/с token, подмену аргументов,
expiry, reuse, cancellation, BLOCKED, CRITICAL, concurrent consumption и отсутствие
реального side effect в simulation. Этапы 3–4 проверяют наблюдаемый результат, а не только
возврат вызова API. Этап 9 закрывает сетевые сбои, отсутствие приложения, ошибочную речь,
prompt injection и emergency stop.

## После MVP

0.2 — память, configurable permissions, Gmail/Calendar, routines. 0.3 — дополнительные
интеграции и skills. 0.4 — офисные приложения и профессиональные профили. 1.0 — стабильный
installer, подписанные обновления, административные политики и поддержка.

## Точный следующий prompt

```text
Implement Milestone 5: LLM Planner and Orchestrator.

Read AGENTS.md, the user-supplied development plan v1.0, docs/SECURITY.md,
docs/MILESTONE_4.md and the current registry, PermissionEngine, approval UI and workers.
Preserve existing work, including the dashboard changes. Milestones 3–4 native Windows
acceptance remains unverified; do not mark it complete from portable tests.

Scope:
- Add a replaceable model provider and a bounded multi-step orchestrator for text commands.
  Start with deterministic offline provider fixtures; connect a real provider through
  documented strict structured tool calls. Check official provider docs before adding SDKs.
- Expose only ToolRegistry discovery and strict Pydantic inputs/results. No generated code,
  shell, JS, arbitrary selectors, private backend access, direct adapter calls or approval tool.
- Route every tool through PermissionEngine, including reads and discovery actions.
  Model output, plans, page text and tool results are untrusted data, never permission.
- Enforce maximum steps, per-call/overall timeout, cancellation and bounded provider output.
  Do not retry issued effects; distinguish pre-execution failures from possible effects.
- Ask for missing/ambiguous targets or content. Show exact proposed actions in the existing
  UI and pause CONFIRM execution for its verified button event. Never auto-approve a plan.
  Bind approval to the immutable exact action; reject changes, expiry and reuse.
- Keep simulation as the default and invoke no real tool adapter hooks in simulation.
  Present plans and simulated results honestly; do not invent observed targets or success.
- Keep browser restrictions: dedicated anonymous script-disabled context, exact-origin/DNS
  checks, no redirects/popups/production POST/profile credentials, one-use document requests.
  No general-purpose external write tool; CRITICAL disabled and BLOCKED never executes.
- Add a manual text-command path with progress, pending approval, stop and factual final
  outcome. Keep the existing shell demo clearly identifiable and preserve manual tools.
- Secrets must use the OS credential store. Never use .env, logs, SQLite, fixtures or model
  messages for raw credentials. Real-provider tests opt in; ordinary tests are offline.
- Do not implement voice, memory, Gmail/Outlook or packaging from later milestones.

Test multi-step success, clarification, unknown tool/invalid args, injection in observations,
no approval/changed approval/replay, provider failure/timeout, stop during planning/tool/
approval, failed verification, no success from plan alone, bounded loops and zero adapter
calls in simulation. Use controlled browser fixtures, not public sites, in the default suite.
Run focused/full tests, Ruff lint/format, mypy, native GUI smoke and build. Update commands,
actual results, limitations and the exact next prompt for Milestone 6. Whole MVP remains
incomplete until real Windows acceptance and packaging pass.
```
