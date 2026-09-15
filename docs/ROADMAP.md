# Roadmap

Работа идёт по этапам из исходного плана v1.0. Новый этап запускается отдельной задачей.
Текущий scope — этап 6. Локальный голос реализован; настоящий микрофон/STT/TTS, живой OpenAI API
и native Windows-приёмка ожидаются.
Результаты: `MILESTONE_0.md`, `MILESTONE_1.md`, `MILESTONE_2.md`, `MILESTONE_3.md`, `MILESTONE_4.md`, `MILESTONE_5.md`, `MILESTONE_6.md`.

| Этап | Результат и критерий выхода | Статус |
| --- | --- | --- |
| 0 Foundation | Документы, package, установка, тесты, lint, types, команды для Windows | Реализован; native Windows не проверен |
| 1 Desktop shell | PySide6, состояния, текст, fake worker без блокировки, activity, config/logging, smoke | Реализован локально; native Windows не проверен |
| 2 Tools / permissions | Typed registry, risk, exact expiring single-use approvals, simulation, audit | Реализован локально; native Windows не проверен |
| 3 Windows | open/focus/list/type, Notepad read-back, Chrome/VS Code, UIA без координат | Реализован; portable tests пройдены, native Windows не проверен |
| 4 Browser | open/navigate/search/click/type/read/tabs/close, Playwright, domain policy, timeouts | Реализован локально: статический анонимный Chromium; Windows не проверена |
| 5 Planner | Strict calls, только registry, bounded plan, cancellation, уточнения, проверенные ответы | Реализован локально; offline/протокол/Qt/Chromium проверены, live API и Windows не проверены |
| 6 Voice | Push-to-talk, transcript, STT/TTS, русские команды, voice/button cancel | Реализован локально; fixtures/Qt/helper tests, реальное оборудование и Windows не проверены |
| 7 Memory | Профиль и короткий контекст, view/edit/delete, уточнение контактов, без secrets | Следующий; не начат |
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
Implement Milestone 7: Memory — user-managed profile and bounded short context.

Read AGENTS.md, the user-supplied development plan v1.0, docs/PRD.md, docs/SECURITY.md,
docs/MILESTONE_6.md and the existing planner, voice lifecycle and PermissionEngine.
Preserve all dashboard/manual tools, offline recipes, optional OpenAI and local voice.
Real Windows, live LLM and real voice hardware remain unverified.

Scope:
- Add a local, strictly typed memory store within the existing application: profession,
  preferred applications, project labels, contact roles and ordinary preferences.
- Make saving explicit and provide visible view/edit/delete/clear controls. Do not
  silently mine transcripts, tool output or files. Never store credentials/tokens/cookies,
  approval tokens, audio, whole pages or raw sensitive content. Bound size and retention.
- Keep a short bounded conversation context with explicit lifecycle and reset. Stored
  labels/context are untrusted data, never system policy or approval authority.
- Resolve ambiguity by clarification, especially names/contacts. Do not infer an exact
  recipient or authorize writes from remembered preferences. No OAuth/email integration.
- Re-observe all Windows/browser targets in the current task; never reuse stored HWND,
  PID, DOM identities, approvals or an obsolete action snapshot as execution authority.
- Keep provider, simulation, cloud disclosure and permission settings under UI control.
  Disclose any remembered context sent to an opted-in cloud planner; send only necessary
  bounded fields. Memory must not silently opt the user into cloud transmission.
- Use cancellable operations, explicit failure handling and safe metadata-only logging.
  Voice transcript review, no background recording and exact per-action approval remain.
- Do not implement external service sending, installer or later milestones.

Test persistence/restart, editing/deletion/clear, bounds/retention, invalid or corrupted
storage, ambiguous references, prompt injection in memory, cloud disclosure, no secrets,
no stale targets/approvals and unchanged simulation/voice cancellation. Run focused/full
tests, Ruff lint/format, mypy, GUI smoke, interactive GUI and build. Update docs with
actual results and exact Windows acceptance commands; add the next prompt for Milestone 8.
Whole MVP remains incomplete until real Windows E2E and packaging pass.
```
