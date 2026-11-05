# Постоянная установка Jarvis — 4 ноября 2026

Запрос владельца: видеть Jarvis в «Программах» и получать текущую локальную сборку
без повторной ручной установки и запуска ассистентом.

## Доставка

- Канонический bundle: `/Applications/Jarvis.app`, зарегистрирован в LaunchServices.
- `~/Applications/Jarvis.app` — ссылка на этот же bundle; старая собственная копия
  сохранена в каталоге maintenance без расширения `.app`.
- Источник выбран явно: исходный проект `JARVIS for Asal` в Documents/ChatGPT.
  Уже установленный UI с исправлениями `0b65dc8` сохранён; отдельная ветка установщика
  создана от опубликованного main `6198d22`, без stacking на PR #77.
- Скопирован локальный helper и зарегистрирован пользовательский LaunchAgent
  `org.jarvis.desktop.sync`: RunAtLoad, WatchPaths manifest, StartInterval 30 секунд.
- Настройки, аккаунты и OS credentials находятся вне bundle и не мигрировались.

## Фактическая проверка

- В Finder → Программы обнаружена и выбрана иконка **Jarvis**.
- Двойной щелчок запустил `/Applications/Jarvis.app` и его Python runtime.
  В настоящем окне: «Локальный движок подключён», `READY`, «Готов к работе».
- `codesign --verify --deep --strict /Applications/Jarvis.app` — success.
- launchctl подтвердил загруженный job, наблюдение manifest, интервал 30 секунд,
  первый запуск с exit code 0 и состояние helper `current`.
- Для проверки очереди изменён только mtime служебного marker установленного bundle.
  Настоящий launchd-процесс обнаружил необходимость обновления и записал
  `waiting_for_close`, сохранив работающий процесс Jarvis.
  На время проверки владелец использовал окно настроек; повторное принудительное
  закрытие не выполнялось. Полная фоновая замена после его закрытия в этом прогоне
  отдельно не наблюдалась; staging/publish и late-launch deferral покрыты тестами,
  а первоначальная реальная установка выполнена тем же sync/publish путём.

## Проверки кода

- Целевые macOS sync + launcher: **12 passed**.
- Sync / launcher / config / browser settings: **49 passed**.
- Полный pytest: **2200 passed, 24 skipped**, 192,54 с.
- Ruff check, Ruff format-check, mypy (369 файлов), pip check — success.
- GUI smoke на изолированном профиле — success (simulated).
- Wheel и sdist собраны.
- Тесты config/default browser используют временные профили, сохраняя проверки
  origin, filesystem roots и отказов; личные настройки владельца не читаются.

Команды установки, поведение и отключение описаны в [MACOS.md](MACOS.md).
Hosted Windows/macOS gates проверяются в PR; эта macOS-доставка не объявляет
живую Windows-, voice- или cloud-приёмку.
