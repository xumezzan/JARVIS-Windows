# Roadmap

Работа идёт по этапам из исходного плана v1.0. Новый этап запускается отдельной задачей.
Текущий scope — этап 2. Tools/permissions реализованы локально; Windows-проверка ожидается.
Результаты: `MILESTONE_0.md`, `MILESTONE_1.md`, `MILESTONE_2.md`.

| Этап | Результат и критерий выхода | Статус |
| --- | --- | --- |
| 0 Foundation | Документы, package, установка, тесты, lint, types, команды для Windows | Реализован; native Windows не проверен |
| 1 Desktop shell | PySide6, состояния, текст, fake worker без блокировки, activity, config/logging, smoke | Реализован локально; native Windows не проверен |
| 2 Tools / permissions | Typed registry, risk, exact expiring single-use approvals, simulation, audit | Реализован локально; native Windows не проверен |
| 3 Windows | open/focus/list/type, Notepad read-back, Chrome/VS Code, UIA без координат | Следующий |
| 4 Browser | open/navigate/search/click/type/read/tabs/close, Playwright, domain policy, timeouts | Не начат |
| 5 Planner | Strict calls, только registry, bounded plan, cancellation, уточнения, проверенные ответы | Не начат |
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
Implement Milestone 3: Windows Automation.

Inspect AGENTS.md and the implemented ToolRegistry, PermissionEngine, approval UI,
audit, cancellation, and Simulation Mode. Preserve unrelated changes and existing tests.

Scope:
- Implement windows.open_app, windows.focus_app, windows.get_open_windows, windows.type_text.
- Support an explicit application allowlist including Notepad, Chrome, and VS Code.
- Use Windows UI Automation/pywinauto and semantic controls; no fixed-coordinate primary path.
- Keep Windows dependencies and imports platform-specific and lazy.
- Route every tool through PermissionEngine with strict schemas and verified results.
- Bind typing to the exact intended app/window/control. Do not let arbitrary keypresses,
  forms, or Enter/submit bypass confirmation for external effects.
- Define bounded timeouts and cancellation for native calls; do not pretend that cancelling
  an async wrapper stops a blocking native operation or rolls back a completed effect.
- Keep Simulation Mode free from all real adapter calls.

Required scenario on real Windows:
- Open Notepad, locate its editor, write "Jarvis integration test", and read back the text.
- Never automatically discard unsaved content.
- Verify opening/focusing supported applications and reporting missing applications.
- Test wrong target, precondition failure, timeout, stop, invalid schemas, and permissions.

Do not implement browser automation, LLM planning, voice, or external services yet.
Check current official dependency docs and Windows compatibility before fixing versions.
Run focused/full tests, Ruff lint/format, mypy, build, and relevant native integration tests.
If Windows is unavailable, implement and test portable boundaries, supply exact Windows
verification commands, and explicitly leave native acceptance unverified.
Update setup, architecture, security, testing and roadmap. Return changed files,
commands/results, decisions, remaining risks, and the exact prompt for Milestone 4.
```
