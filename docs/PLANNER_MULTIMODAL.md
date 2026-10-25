# Мультимодальный planner и оперативные заметки

Изменение от 2026-10-24. Это расширение существующего function-calling planner;
все действия выполняются зарегистрированными инструментами через PermissionEngine.
Hosted computer tools и произвольный код модели не добавляются.

## SoM и исправление provenance (2026-10-25)

`windows.inspect_active_window` и active-window screenshot возвращают до 96 наиболее
значимых видимых/доступных интерактивных элементов, с именами до 120 символов.
`mark_id` — целое 1..N, локальное для конкретного snapshot. JPEG содержит рамки и
плашки `[1]`, `[2]`, … в координатах окна с учётом финального уменьшения кадра.
Screen screenshot остаётся без разметки. JPEG по-прежнему <=49152 bytes.

Click принимает полный `ElementTarget` либо `mark_id` вместе с точным `WindowTarget`.
Runner резолвит номер в полную структуру **до** PermissionEngine.prepare(), поэтому
нормализация, risk, immutable action, approval и native revalidation связаны с одним
точным элементом. Прямое исполнение неразрешённого mark_id адаптером запрещено.
Усечение длинной подписи отражается `label_truncated=true` и требует CONFIRM.
`name_sha256` связывает всю исходную подпись: изменение даже невидимого хвоста
обнаруживается native revalidation. Пустой digest не дублируется в provider context.
Номер не является глобальной или постоянной идентичностью элемента.

Image-only screenshot больше не скрывает предыдущее полное UIA-наблюдение.
Поиск прекращается на open_app/focus_app/click_element/type_text/send_hotkey
(включая неопределённые исходы), а также при новом наблюдении другого активного окна.
После таких действий необходимо свежее UIA-наблюдение/размеченный screenshot.

Помимо счётчиков, native сборка ограничивает все метаданные одним бюджетом
67000 bytes с учётом JSON escaping; при необходимости берёт меньше 96 элементов
и устанавливает truncated. Это защищает от длинных automation IDs и Unicode-полей.
Provider компактно сериализует текстовый JSON и сохраняет общий предел 262144 bytes.
Произвольная история/документы всё ещё могут исчерпать общий бюджет: fail-closed
context_limit остаётся обязательным, результаты не обрезаются до правдоподобного текста.

`python scripts/probe_multimodal_budget.py` без сети строит запросы настоящим кодом
provider для реальных Qt/Desktop catalogs, 96 имён по 120 четырёхбайтовых Unicode
символов и 65536 base64 bytes. Оба payload должны быть <180000 bytes.
Размеры, regression-тесты и проверка окружения записаны в `SOM_VAD_FIXES.md`.

## Контекст и границы

- `PlannerInput.screenshot_b64: str | None` — текущий JPEG в base64, без data URL prefix.
  Runner заполняет его только из последнего SUCCESS `windows.screenshot`.
  После верификации PermissionEngine отделяет base64 от `Outcome.result_json` во
  временное поле `Outcome.screenshot_b64`. Runner забирает кадр в отдельное RAM-поле
  и очищает поле Outcome до добавления шага в историю и уведомления UI. История,
  PlanResult и Electron JSONL содержат только метаданные, без байтов изображений.
  Следующий запрос содержит `input_text` с JSON-контекстом и один `input_image`
  с `data:image/jpeg;base64,...`. После другого шага старое изображение не пересылается.
  Ссылка Runner на кадр очищается после следующего результата и в `finally` при
  завершении, ошибке, отмене или timeout. Provider дополнительно удаляет
  `image_base64` из текстовых наблюдений для совместимости со старыми callers.
- В simulation изображение не передаётся, scratchpad не изменяется, adapter hooks
  не вызываются. Скриншот не заменяет наблюдённые target/element identities.
- Общий UTF-8 JSON запрос по-прежнему ограничен **262144 байтами** вместе с каталогом,
  текстом и base64. Слишком большой JPEG/контекст останавливает задачу с `context_limit`
  до transport/чтения ключа. Native screenshot кодируется в JPEG с **quality=65**,
  без увеличения, до **1280×800**. Если JPEG превышает **49152 байта (48 КиБ)**,
  тот же захват уменьшается по обеим сторонам до 75% с повторным кодированием.
  Это даёт максимум **65536 ASCII-байт base64 (64 КиБ)**; повторного захвата экрана,
  сетевых повторов и обрезки контекста нет. Для сложных кадров разрешение будет ниже.
  Registry и provider проверяют бюджет изображения; общий бюджет запроса остаётся
  независимым и может быть превышен большим текстовым контекстом.
- Лимит `PermissionEngine.prepare()` **65536 байт** относится к аргументам действия,
  а не к результатам. У screenshot аргументы содержат только `scope`.
  Текущие пределы Electron JSONL: **131072 байта на запрос**, **1048576 на ответ**;
  кадр через этот канал не передаётся. Лимиты не увеличивались.
- Defaults и верхние пределы Runner: **30 действий / 300 секунд**.
  Уточнений по-прежнему максимум 3; provider timeout по умолчанию 30 секунд.
  Запись scratchpad также расходует один шаг.

## Scratchpad

Инструмент `memory.scratchpad_write` имеет Risk.SAFE и strict параметры:

```json
{"key": "copied_text", "value": "Текст или промежуточный черновик"}
```

`key` соответствует `[A-Za-z][A-Za-z0-9_]{0,63}`, `value` — строка до 8192 символов.
Числа сохраняются строками. Произвольные вложенные JSON-объекты не принимаются.
Новая запись заменяет существующее значение ключа. Общий словарь ограничен
32 ключами и 32768 байтами UTF-8 JSON; превышение даёт `context_limit`.

Схема регистрируется в local и Electron desktop registries до их sealing.
Для собственного registry нужно вызвать `register_scratchpad(registry)` до создания engine.
Инструмент валидирует и верифицирует обновление через PermissionEngine, затем Runner
применяет успешный результат к собственному RAM-словарю. Между задачами общего состояния нет.
После завершения, ошибки, timeout или отмены словарь очищается в `finally`.
База profile/session memory не используется; результаты шагов остаются частью обычного
RAM-отчёта задачи, без сохранения содержимого в audit.

В каждом запросе JSON начинается с `untrusted_scratchpad`, даже если он пуст.
В `PlannerInput` передаётся отдельный неизменяемый snapshot. История записей содержит
ключи без повторения старых значений. Заметки и изображения — недоверенные данные:
они не дают approval, не определяют адресата Outlook и не обходят проверки provenance.
Облачное согласие в UI явно перечисляет скриншоты и scratchpad.

Формат `input_image` сверён с
[официальной документацией OpenAI](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).
Нужна модель с поддержкой image inputs и strict function calling; конкретная модель
этим изменением не выбирается.

## Проверка

Повторная проверка ограничения изображений 2026-10-24 использовала Python 3.12.7
из `/tmp/jarvis-windows-step1-venv` (editable checkout, `.[dev,voice]` и Pillow):

```sh
python -m pytest tests/unit/test_planner.py tests/unit/test_openai_provider.py tests/unit/test_windows_cross_app.py tests/unit/test_permissions.py tests/integration/test_desktop_service.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy --no-incremental
QT_QPA_PLATFORM=cocoa python -m jarvis --smoke-test
python -m build --outdir /tmp/jarvis-screenshot-budget-dist-20261024
git diff --check
```

Focused: **181 passed**. Полный набор: **807 passed, 5 skipped**, 58.69 s.
Ruff lint/format, mypy (136 файлов), native Mac GUI smoke, wheel/sdist и diff check
прошли. В тестовом transport исправлена mypy-аннотация словаря ответа; финальный
type check проходит. Пропущены live model, voice и native Windows acceptance.
Тест с 30 последовательными максимальными base64-кадрами использует настоящий
DesktopService → Runner → PermissionEngine → OpenAIProvider и полный каталог;
захват и сеть подменены fixtures. Все 31 запроса укладываются в 262144 байта,
JSONL — в MAX_RESPONSE; история и audit не содержат кадров. Отдельно проверены
шумный JPEG, quality=65, верхняя граница 48 КиБ / 64 КиБ base64, отказ при
превышении, передача кадра отдельно от result_json и очистка после следующего шага.
Формат `input_image` с base64 data URL повторно сверен с официальной документацией
OpenAI по ссылке выше. Реальные Windows capture/input и live vision API не запускались.

### Предыдущая проверка до отделения изображения от Outcome

Использовано изолированное `.venv-reference` с зависимостями `.[dev,voice]` и Pillow.
После сборки финальные тесты выполнялись из установленного wheel (`pip install
--force-reinstall --no-deps dist/jarvis_windows-0.0.1-py3-none-any.whl`).
Основной `.venv` указывает на отсутствующий cache; исходники ради окружения не менялись.
На этом Mac скрытые атрибуты `.pth` и Qt plugins мешали Python/Qt discovery;
атрибуты восстанавливались. Wheel устранил зависимость helper-процессов от `.pth`;
Qt plugins скопированы в `/tmp/jarvis-multimodal-qt-runtime/plugins`, со ссылкой `lib`
на библиотеки Qt исходного окружения. У копии снят hidden attribute. Это локальный
обход поведения окружения для проверки, не изменение packaging приложения.

```sh
export QT_QPA_PLATFORM=offscreen
export QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/jarvis-multimodal-qt-runtime/plugins/platforms
.venv-reference/bin/python -m pytest -q
.venv-reference/bin/python -m ruff check .
.venv-reference/bin/python -m ruff format --check .
.venv-reference/bin/python -m mypy --no-incremental
.venv-reference/bin/python -m jarvis --smoke-test
.venv-reference/bin/python -m build
```

Результаты на macOS, Python 3.12.7: **638 passed, 5 skipped**, 40.63 s, из установленного
wheel. Ruff check/format и mypy (`127 source files`) прошли; GUI smoke —
`Jarvis GUI smoke: success`; wheel и sdist собраны. `git diff --check` прошёл.
Пропуски: live model, real voice и три native Windows acceptance cases.
Первоначальные прогоны с ошибками import/Qt discovery относятся к описанному выше
окружению; финальный прогон использует wheel и внешнюю копию plugins.

Проверки transport используют fixtures без OpenAI, credentials и захвата реального экрана.
Покрыты текущий image block без старых base64, request budget до отправки,
невалидный base64, 30/31 действий, сохранение/перезапись/очистка заметок,
неизменяемые snapshots, simulation, строгая схема, общий размер и отсутствие
содержимого заметок в audit. Проверяется доступность scratchpad в desktop registry.

Live API, реальный Windows screenshot и многошаговый cross-app сценарий остаются
непроверенными. На Windows после установки из полного репозитория:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_planner.py tests/unit/test_openai_provider.py tests/unit/test_windows_cross_app.py tests/integration/test_desktop_service.py -q
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
# Только после выбора модели и настройки ключа через OS credential store:
$env:JARVIS_PLANNER_MODEL = Read-Host "Vision + strict function calling model ID"
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_model_acceptance.py --run-model -q
.\.venv\Scripts\python.exe -m jarvis
```

Существующий live model test проверяет текстовый function calling. Отдельно в видимом UI
нужно разрешить облачную передачу, проверить небольшой screenshot, запись/использование
заметки и точные CONFIRM approvals. Fixture и текстовый model test не доказывают
совместимость vision конкретной модели или выполнение сценария на Windows.
