# Wake word, RMS и HUD — 2026-10-25

Три изменения для подготовки Windows-поставки. Нативная приёмка Windows,
реальный микрофон/ASR/TTS и cloud vision не подтверждены проверками на Mac.

## Production dependencies

Pillow теперь без платформенного marker в project.dependencies; из dev удалён.
Обычная установка приложения получает PIL.ImageGrab/ImageDraw без dev extras.
Editable metadata обновлена. Wheel/sdist собраны; METADATA проверена:
`Requires-Dist: pillow` без условия extra/dev. Новых голосовых зависимостей нет.

## Настройки и запуск

Значения берутся только из доверенного окружения при запуске. .env.example — справка,
приложение не загружает .env. Node cleanEnvironment явно пропускает только эти
несекретные voice-настройки, provider keys/proxy variables по-прежнему не передаются.

| Настройка | Default | Поведение |
|---|---|---|
| JARVIS_VOICE_WAKE_WORD | 0 | 1 разрешает Electron фоновое локальное ожидание «Джарвис» |
| JARVIS_VOICE_RMS_THRESHOLD | 500 | Конечное число 1..32768, амплитуда PCM16 |
| JARVIS_VOICE_RMS_CALIBRATE | 0 | 1 калибрует порог по первым двум тихим 100 ms кадрам |
| JARVIS_VOICE_AUTO_STOP_ON_SILENCE | 0 | Опциональная автоостановка для ручной записи; wake-команда использует её всегда |

На установленном Windows перед штатным Electron launcher можно задать:

```powershell
$env:JARVIS_VOICE_WAKE_WORD = "1"
$env:JARVIS_VOICE_RMS_THRESHOLD = "500"
# По желанию, начинайте запись в тишине:
$env:JARVIS_VOICE_RMS_CALIBRATE = "1"
```

Требуется выбранная локальная модель Vosk. Нормальный старт не скачивает модели.
Для development checkout используется `npm --prefix desktop start`; обычная установка
Windows по-прежнему выполняется Install-Jarvis.cmd/Update-Jarvis.cmd без ручного pip.
Qt сохраняет ручной push-to-talk/VAD; фоновый wake UI реализован в Electron.

## Голосовой путь и безопасность

AudioHost остаётся одним прогретым процессом с одной загруженной моделью. Приватная
операция wake использует `KaldiRecognizer(model, 16000, '["джарвис", "слушай джарвис", "[unk]"]')`.
Сначала проверяется наличие «джарвис» в словаре и совместимость грамматики: иначе
конечная wake_model ошибка до открытия микрофона. Никакого автоматического скачивания
или подстановки другой модели. API сверено с [официальным Vosk Python source](https://github.com/alphacep/vosk-api/blob/master/python/vosk/__init__.py).

RawInputStream использует 1600 mono int16 samples/кадр. Bounded очередь — 8 кадров;
grammar decoder работает вне audio callback. Проверяются Result/PartialResult и
точное слово «джарвис», а не подстрока «антиджарвис». Родителю передаётся только
boolean wake: фоновая речь/PCM не покидают helper, не пишутся в audit, файлы или memory.
Один период ожидания <=30 s; при обычном истечении срока следующий использует тот же
загруженный Model и свежий decoder. Stop/EOF/cancel закрывают stream; timeout/нарушение
протокола убивают/reap-ят helper. stop_record связан с точным capture_id.

После wake stream ожидания закрывается и тот же AudioHost начинает штатную command
capture с EnergyEndpoint. Скажите команду после перехода индикатора в запись. Три
громких кадра отмечают начало речи; 12 тихих (1.2 s) заканчивают. Настраиваемый RMS
используется и при ручном capture. Калибровка использует два последовательных тихих
кадра до начала фразы, задаёт 3×средний RMS шума с минимумом 50; голосовые кадры
сбрасывают незавершённую калибровку. Начальная речь не используется как шумовой baseline.

DesktopService сначала публикует opt-in статус, затем разрешает ожидание. Основное
окно и отдельный click-through corner HUD явно показывают микрофон. Только wake-режим
имеет background permission и не отменяется при blur; ручной hold сохраняет focus-loss
cancellation. Перед задачами, auxiliary работой, approvals, ручным capture и TTS ожидание
приостанавливается/drain-ится; одновременно запись и TTS не работают.

Stop/Escape приостанавливают wake до «Возобновить» в текущем сеансе. Этот путь проходит
через event.isTrusted в isolated preload и проверку sender/frame/URL/focus в main;
renderer не получает generic wake/record/approval API. Синтетический click не возобновляет
микрофон. «Возобновить» берёт явно выбранный UI режим; начальный wake_mode=simulation.

Wake запускает запись, а не действие. Autostart по-прежнему отдельный default-off toggle
и confidence >=0.75. При выключенном autostart/неуверенном результате текст ждёт review,
а wake не перезаписывает composer новой речью. Запуск задачи потребляет review и может
возобновить ожидание после завершения. Cloud/history consent и exact one-use UI approval
не возникают из wake/autostart. Любое признание «да» голосом не подтверждает действие.

## HUD окна

TaskHudWindow меняет главное окно, не создаёт скрытый workbench для исполнения:
360×120 DIP, отступ 16 DIP справа/сверху workArea, alwaysOnTop=true. JarvisOrb,
текущий шаг/инструмент, Stop и «Открыть» доступны на той же защищённой странице.
Две кнопки >=44px; overflow проверен в native renderer. Длинное имя инструмента
занимает до двух строк. Команда, адреса и action contents в HUD не выводятся.

Main сохраняет/восстанавливает bounds, min/max size, resizable, maximized и top flag.
Любой терминальный статус или потеря runtime возвращают обычное окно. Prompt/phase
approval/clarification возвращают полный интерфейс с фокусом, включая случай ручного
выхода из HUD или отключения исходного монитора. «Открыть» оставляет обычный режим до
следующей задачи. Generic resize/OS APIs renderer не получает. Старый corner HUD нужен
для display-only подсказок/микрофона, а target outline остаётся click-through.

Реализация следует [BrowserWindow API](https://www.electronjs.org/docs/latest/api/browser-window)
и [screen workArea/DIP API](https://www.electronjs.org/docs/latest/api/screen).

## Проверки

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
JARVIS_PYTHON="$PWD/.venv/bin/python" node desktop/scripts/verify-overlay.cjs
JARVIS_PYTHON="$PWD/.venv/bin/python" node desktop/scripts/verify-voice-runtime.cjs
JARVIS_PYTHON="$PWD/.venv/bin/python" node desktop/scripts/verify-wake-runtime.cjs
```

Python fixtures проверяют grammar/on-off/OOV, RMS/config/calibration, wake→record→transcribe,
одну модель/один helper, late wake после Stop, review и отсутствие cloud consent.
Node tests проверяют HUD/terminal/prompt/display transitions и allowlisted config.
Native Electron QA (на Mac, с model/microphone fixtures) проверяет:

- 360×120/top flag/Stop/исходные bounds/target-paint receipt;
- полный approval/clarify, synthetic approval rejection и trusted approval через engine;
- microphone indicator, Stop/resume, synthetic resume rejection;
- wake без hold и разрешённую background запись после blur, review без autostart/cloud.

Один focused 30-screenshot тест упёрся в прежний общий deadline 5 s при параллельной
UI-проверке; отдельный запуск занял 4.63 s и прошёл. Только этому long-flow fixture
дано 15 s; остальные ожидания остаются 5 s, runtime timeouts не менялись. Также один
voice UI QA остановился на поиске approval button; диагностический повтор прошёл
все проверки. К нему добавлена диагностика backend/DOM без action contents на случай
повторения. Это ограничение UI QA учитывается, а не объявляется Windows acceptance.

Mechanical design detector выдал только warning о существующем Inter. Он сохранён по
пользовательскому reference/design brief; контраст новой Stop-кнопки исправлен до 5.13:1.
Снимок: desktop/qa/task-hud.png. Машиночитаемые результаты — task-hud-verification.json,
voice-runtime-verification.json и wake-runtime-verification.json в desktop/qa/.

Финальный контрольный прогон на Mac, из корневой .venv:

| Проверка | Результат |
|---|---|
| python -m ruff check . | All checks passed, exit 0 |
| python -m ruff format --check . | 224 files already formatted, exit 0 |
| python -m mypy | 165 source files, ошибок нет, exit 0 |
| python -m pytest -q | 992 passed, 5 skipped in 78.23s, exit 0 |
| npm --prefix desktop run build | 2338 modules, 297 ms, exit 0 |
| Node unit tests | 10 passed, exit 0 |
| Native Electron HUD QA | 5 checks passed, exit 0 |
| Native Electron PTT/approval QA | 6 checks passed на повторном прогоне, exit 0 |
| Native Electron wake QA | 2 checks passed, exit 0 |
| pip check / git diff --check | Без замечаний, exit 0 |
| Python wheel/sdist | Собраны, Requires-Dist: pillow без dev/platform marker |

Пять skips: live model (1), voice hardware (1), Windows acceptance (3).
Логи сохранены в /tmp/jarvis-wake-hud-final-pytest.log,
/tmp/jarvis-wake-hud-final-build.log, /tmp/jarvis-wake-hud-node-final.log,
/tmp/jarvis-hud-native-final.log, /tmp/jarvis-wake-hud-voice-diagnostic.log,
/tmp/jarvis-wake-native-qa.log. Wheel/sdist — /tmp/jarvis-wake-hud-build/.

На реальном Windows 11 проверить выбранную локальную модель/словарь «джарвис»,
OS microphone permission, wake→команда→тишина, Stop/resume, PTT focus loss,
HUD/approval/clarify/terminal restore и текущие mixed-DPI displays. Fixture-тесты
не заменяют эту приёмку. Из полного Windows checkout:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_windows_acceptance.py --run-windows -q
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_voice_acceptance.py --run-voice -q
npm --prefix desktop run build
```
