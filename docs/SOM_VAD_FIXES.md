# SoM, provenance, VAD и локальное окружение — 2026-10-25

Исправления по результатам [аудита](AUDIT_2026_10_25.md). Существующие незакоммиченные
изменения сохранены. PermissionEngine, ApprovalStore, UI-only approvals, audit и
Strict Sandbox не обходятся; Windows/hardware/live model acceptance не заявляется.

## Изменения

- Runner ищет полное UIA-наблюдение за image-only screenshot того же окна.
  Любое промежуточное open/focus/click/type/hotkey останавливает этот поиск, включая
  неопределённые исходы. Наблюдение другого активного окна также инвалидирует маркеры.
- ElementTarget содержит mark_id 1..96, имя <=120 символов, label_truncated и digest
  полной усечённой подписи. До 96 видимых интерактивных элементов ранжируются:
  именованные редакторы/кнопки/вкладки перед повторяющимся содержимым документов/списков.
  Совокупный бюджет метаданных <=67000 bytes учитывает escaping provider JSON.
  Если длинные automation IDs/другие поля расходуют бюджет, элементов будет меньше,
  а truncated=true. Native revalidation сравнивает в том числе полный name_sha256:
  замена скрытого за 120 символами хвоста не проходит проверку.
- Active-window screenshot собирает актуальные elements/window_rectangle и рисует
  контрастные рамки/номера через PIL.ImageDraw. Учитываются отрицательные screen
  coordinates, смещение окна и финальный resize; метки рисуются заново при уменьшении.
  Захват ОС один; JPEG quality=65, <=49152 bytes, base64 <=65536 bytes.
- Click с mark_id разрешается Runner в полный ElementTarget до engine.prepare().
  Риск и approval связываются с полной immutable action. Неизвестные/устаревшие номера
  отклоняются до адаптера; прямое выполнение неразрешённого номера запрещено.
  Координатные/неизвестные/усечённые/чувствительные clicks остаются CONFIRM.
- OpenAIProvider строит payload отдельным чистым методом, используемым также probe.
  Текстовый контекст сериализуется компактно. Общий guard 262144 bytes перед transport
  и чтением ключа сохранён; изображения не попадают в audit/history/JSONL.
- EnergyEndpoint считает RMS PCM16 для кадров по 1600 samples (100 мс). Начало речи:
  три последовательных кадра RMS >=500. Остановка: 12 тихих кадров (1.2 секунды)
  после начала речи. Начальная тишина и одиночные всплески не запускают endpoint.
- Опциональный LocalRecorder использует существующий AudioHost с одной загруженной
  моделью. KaldiRecognizer.AcceptWaveform обрабатывает каждый кадр в streaming,
  вне audio callback, через bounded очередь из восьми кадров. Переполнение — ошибка.
  У capture/transcribe отдельные decoder states. Управляющий helper loop принимает
  ручную остановку только для точного capture_id. Stop/cancel/timeout убивают и reap-ят
  helper; release во время warmup не допускает запоздалого record/microphone startup.
- VAD включается startup-параметром JARVIS_VOICE_AUTO_STOP_ON_SILENCE=1 (default 0)
  в Qt/Electron или LocalRecorder(host=recognizer.host, auto_stop_on_silence=True).
  Запуск записи по-прежнему требует явного жеста. VAD не даёт approvals/cloud consent.
- Создан настоящий корневой .venv с editable `.[dev,voice]`. Pillow добавлен в dev,
  чтобы тесты отрисовки выполнялись и на Mac. Старый сломанный symlink сохранён как
  /tmp/jarvis-old-venv-link-20261025; другие окружения и пользовательские данные сохранены.
  Hidden flags editable .pth и Qt platform plugins очищены. Python -I импортирует
  текущий src/jarvis; pip check подтверждает целостность зависимостей.
  Из-за повторного появления UF_HIDDEN на Mac установлен локальный sitecustomize
  только внутри .venv: scripts/repair_local_venv_flags.py. Он снимает только этот
  флаг с editable .pth и Qt platform dylibs, сохраняя остальные атрибуты. Повторно
  обрабатывается только доверенный Jarvis editable install; cwd/PYTHONPATH/user-site
  не добавляются. Глобальный Python, Gatekeeper, OS consent и production code не меняются.
  Принудительное выставление hidden на .pth/Qt воспроизведено: следующий обычный
  python -I восстановил import Jarvis и QApplication без отдельного chflags.

## Верификация

Все финальные Python-команды выполняются непосредственно из `.venv/bin/python`,
без PYTHONPATH или внешнего QA-окружения. Исходные схемы в старой audit fixture
были обновлены с 256/1000 до 96/120 с сохранением проверок metadata-only durability,
отсутствия секретов и commit-before-effect. Сначала это был единственный упавший
тест полного прогона (968 passed). При диагностике Qt также обнаруживались hidden
platform plugins: они очищены, повторный запуск стандартного pytest прошёл.

Финальные результаты:

| Проверка | Результат |
|---|---|
| Ruff check | All checks passed, exit 0 |
| Ruff format --check | 222 files already formatted, exit 0 |
| mypy | Success: no issues found in 164 source files, exit 0 |
| pytest -q | 971 passed, 5 skipped in 78.79s, exit 0 |
| Focused regression suite | 119 passed in 3.10s, exit 0 |
| pip check | No broken requirements found, exit 0 |
| git diff --check | Без замечаний, exit 0 |
| GUI smoke | Jarvis GUI smoke: success (Mac offscreen) |
| Python build | wheel и sdist успешно собраны в /tmp/jarvis-som-vad-build-20261025 |
| Desktop build | TypeScript/Vite успешно, 2336 modules, 351 ms |

Пропуски: live model (1), voice hardware (1), Windows acceptance (3).
Визуально проверена реальная функция PIL-разметки на синтетическом UI с отрицательным
смещением окна: /tmp/jarvis-som-preview-20261025.jpg. Это не Windows screenshot.
Логи: /tmp/jarvis-fixes-final-accepted-pytest.log,
/tmp/jarvis-fixes-final-accepted-probe.log, /tmp/jarvis-fixes-regression.log,
/tmp/jarvis-fixes-complete-build.log, /tmp/jarvis-fixes-smoke.log,
/tmp/jarvis-fixes-frontend.log. В исходном audit-каталоге сохранены предыдущие результаты;
старый probe.py от аудита рассчитан на прежние схемы, актуальный probe — в scripts/.

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/python scripts/probe_multimodal_budget.py
.venv/bin/python -m pip check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m jarvis --smoke-test
.venv/bin/python -m build --outdir /tmp/jarvis-som-vad-build-20261025
npm --prefix desktop run build
```

No-network probe строит настоящие каталоги PlannerWindow/DesktopService и payload
настоящим кодом OpenAIProvider. Синтетические данные: 96 имён по 120 четырёхбайтовых
Unicode символов, полное UIA-наблюдение и максимальное поле изображения 65536 bytes.
Это проверка сериализации и схем, не реальный Windows capture или вызов OpenAI.

| Каталог | Полный JSON payload |
|---|---:|
| PlannerWindow | 178704 bytes <180000 |
| DesktopService | 177287 bytes <180000 |

Guard не обещает такой размер для произвольной истории, команд, документов или
дополнительно настроенных roots. Если весь контекст всё же >262144 bytes, задача
останавливается с context_limit до сетевого запроса, без обрезки существенных результатов.

Regression-покрытие: inspect→screenshot→click; stale/unknown/new marker mappings;
точный approved payload после mark resolution; all five mutation barriers;
изменившийся хвост усечённой подписи; JSON Unicode byte cap; реальная PIL отрисовка/
JPEG; RMS onset/pause/reset; ручной release; auto endpoint; cancel/reap; повторное
использование model host; запрет записи после release во время warmup.

Live Windows UIA/SoM/clipboard/keyboard, качество микрофона и подбор RMS-порога,
системный TTS, live vision API и Outlook остаются непроверенными. Для Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
# После настройки локальной модели и только по явному выбору пользователя:
$env:JARVIS_VOICE_AUTO_STOP_ON_SILENCE = "1"
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_voice_acceptance.py --run-voice -q
```

Использованные native API сверены с [Pillow ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html),
[sounddevice Raw Streams](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html)
и [официальным примером Vosk](https://github.com/alphacep/vosk-api/blob/master/python/example/test_microphone.py).
