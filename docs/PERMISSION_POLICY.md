# Политика взаимодействий

Изменена по запросу пользователя 2026-10-24. Обычные действия получают SAFE по умолчанию;
отдельный Autonomous Mode не нужен. PermissionEngine и AuditLog сохраняются.

| Действие | Риск |
| --- | --- |
| Browser open / navigate / search / read / get_tabs / type | SAFE |
| Browser click: обычная ссылка или GET-кнопка поиска/навигации | SAFE |
| Browser click: оплата, удаление, отправка, покупка, закрытие или другие опасные подписи; POST | CONFIRM |
| Browser close: форма без изменений | SAFE |
| Browser close: изменённый input/textarea | CONFIRM |
| Windows open / list / focus / inspect / screenshot / type_text | SAFE |
| Windows clipboard_get / clipboard_set; Ctrl+C / Ctrl+V / Ctrl+A / Tab | SAFE |
| Windows click_element: обычный именованный UIA-элемент | SAFE |
| Windows click_element: опасная подпись/name/automation_id, закрытие, без имени или координаты | CONFIRM |
| Windows Enter / Alt+F4 | CONFIRM |
| Outlook account / list / read / local_draft / save_draft | SAFE |
| Outlook send | CONFIRM |

Системного инструмента удаления файлов нет. Его появление потребует отдельного контракта
и подтверждения; эта правка не добавляет файловые операции. CRITICAL/BLOCKED остаются
запрещены. Учебный local.append_message сохраняет CONFIRM для проверки протокола.

`ToolSpec.classify_risk` — чистая функция доверенного кода над нормализованными параметрами.
PermissionEngine записывает вычисленный риск в immutable Action до approval и аудита.
Модель не задаёт риск: лишние поля отклоняются схемой, изменение подготовленного Action
отклоняется engine. В discovery поле risk для динамических инструментов показывает
консервативный CONFIRM; окончательный риск определяется при prepare.

Перед действием адаптер повторно сверяет наблюдённый DOM/UIA target. Подмена имени
«Отправить» на «Поиск» не совпадёт с наблюдаемым элементом. Browser close сверяет также
has_unsaved_changes; его нельзя сбросить в запросе для закрытия изменённой формы.
Windows сохраняет process/window/editor/tab/content binding, clipboard digest и read-back.

CONFIRM требует точный одноразовый UI token со сроком до 60 секунд. SAFE не создаёт
запрос approval. Оба класса проходят preconditions, STARTED audit до adapter call,
верификацию результата, FINISHED audit, отмену и тайм-ауты. Simulation выполняет
схемы/политику без вызова адаптеров. Текст, буфер и почта не пишутся в аудит.

Подписи RU/EN — эвристика, а не универсальная проверка смысла кнопки. Необычные подписи,
другие языки и неожиданные эффекты приложений остаются ограничением. Неименованные и
координатные клики, Enter и закрытие окон требуют CONFIRM, поскольку текущий адаптер
не может доказать их безопасность. Для Strict Sandbox сохраняются anonymous/script-disabled
context, origin/DNS policy, запрет production POST и отсутствие профиля пользователя.
В явно включаемом Live Browser JS-кнопки и form buttons, включая GET, требуют CONFIRM;
наблюдённые POST-формы допускаются с CONFIRM. Скрипты сайта при этом могут выполнять
запросы сами: это расширенная граница, описанная в [BROWSER_MODES.md](BROWSER_MODES.md).

Изменения input/textarea определяются сравнением value с defaultValue в изолированном
браузере; это не определение dirty-state произвольных динамических приложений.
Политика не меняет просмотр голосовой расшифровки и отдельную отправку команды в planner.

Проверка на Windows 11 после установки зависимостей:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest tests/unit/test_permissions.py tests/unit/test_interaction_policy.py tests/unit/test_windows*.py tests/integration/test_windows_ui.py tests/integration/test_planner_ui.py -q
python -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
python -m jarvis
```

Для ручных проверок использовать одноразовые документы. Проверить ввод/буфер/переключение
без диалога, отмену Enter/Alt+F4/опасных кликов и отказ при изменении target. Проверки
на macOS и фикстуры не подтверждают native Windows, live Outlook или голосовое оборудование.

## Выполненная проверка, macOS, 2026-10-24

Изолированное окружение Python 3.12: `/tmp/jarvis-permissions-qa-20261024`;
установка `python -m pip install -e ".[dev,voice]" Pillow`.

- `python -m pytest -q`: **710 passed, 5 skipped**. Пропущены live model, voice и Windows.
- `python -m pytest tests/unit/test_interaction_policy.py -q`: **68 passed**, включая
  повторный запуск после усиления проверок отказа для неподтверждённого EXECUTE.
- С `QT_QPA_PLATFORM=cocoa` браузерные, Windows-probe, почтовые и planner GUI-тесты:
  **31 passed**. Chromium использует локальный fixture server, почта и Windows — fixtures.
- Ruff lint/format, mypy `--no-incremental`, `git diff --check`: пройдены.
- `QT_QPA_PLATFORM=cocoa python -m jarvis --smoke-test`: **success**.
- `python -m build`: wheel и sdist собраны.

Начальные прогоны выявили старые ожидания approval для поиска и ошибочные assertions
новых тестов; они исправлены. Исходный `.venv-reference` имел скрытые `.pth`/Qt plugins,
из-за чего collection и Qt startup падали; финальные проверки выполнены в новом окружении
в `/tmp`. Никакие live credentials, микрофон или внешние отправки для тестов не использовались.
