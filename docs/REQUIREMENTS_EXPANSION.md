# Отчёт: расширение требований и roadmap

Дата: 2026-09-15. Задача выполнена как **анализ и документация**. Код приложения,
зависимости, assets, scripts и тесты не изменялись; новый milestone не запускался,
аккаунты не подключались. Рабочее дерево до начала было чистым.

## Изменённые документы

| Документ | Изменение |
| --- | --- |
| [PRD.md](PRD.md) | Фактический MVP отдельно от полного видения; English-default/RU, сценарии и ограничения |
| [ROADMAP.md](ROADMAP.md) | Сохранены 0–9, незавершённая native-приёмка; новые bounded 10–32, dependencies/exclusions/exits и точный следующий prompt |
| [CAPABILITY_MAP.md](CAPABILITY_MAP.md) | 50 строк capability → actual state → milestone → dependencies → readiness |
| [INTEGRATIONS.md](INTEGRATIONS.md) | 15 сервисов, documented vs unverified, auth/scopes/risks/limitations, dated official sources и acceptance |
| [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) | Проект manifest/registration/setup, общего budget, verification и duplicate handling |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Исправлены устаревшие текущие сведения; отдельно предложены adapters/roles/KB/routines/demo architecture |
| [SECURITY.md](SECURITY.md) | Сохранены текущие restrictions; будущие threat boundaries и связи с acceptance |
| [ONBOARDING.md](ONBOARDING.md) | Семь экранов, все группы сервисов, consent/discovery/resume/readiness и EN/RU |
| [PRODUCT_ACCEPTANCE.md](PRODUCT_ACCEPTANCE.md) | 12 групп сквозных сценариев и отказов; все новые прогоны planned / not run |
| [OPEN_DECISIONS.md](OPEN_DECISIONS.md) | 21 открытое решение с временным допущением и доказательством закрытия |
| [UI_DESIGN.md](UI_DESIGN.md) | Ранний UI-отчёт помечен историческим, ссылки на актуальное состояние |
| [UI_REFINEMENT.md](UI_REFINEMENT.md) | Разделены исторический UI, текущие ограничения и будущий wizard |
| [AGENTS.md](../AGENTS.md) | Только согласование источников, границы 0–9/10–32 и языковых требований |
| [README.md](../README.md) | Навигация к новым документам и явный статус предложенного расширения |
| [Этот отчёт](REQUIREMENTS_EXPANSION.md) | Выполненный scope, результаты документационных проверок и ограничения |

## Источники и решения

Прочитаны AGENTS.md, прежние PRD/ROADMAP/ARCHITECTURE/SECURITY/UI документы, отчёты
M8/M9 и последнего локального дополнения ElevenLabs, Windows acceptance/installation plan,
релевантный исходный код и git state. Отдельный оригинал плана v1.0 не найден в репозитории;
это явно отмечено, доступны ссылки на него в M0 и сохранённые требования 0–9.

Официальные источники интеграций проверены 2026-09-15 и перечислены рядом с ограничениями.
Недоступные direct Meta/часть Intuit pages не представлены как прочитанные: использованы
официальные collections/SDK references, а недостающие endpoint/scopes оставлены investigation.
Существование API не подтверждает account entitlement, production review или JARVIS adapter.

Рекомендуемый путь: завершить M9 → EN/RU (10) → общий контракт (11) → onboarding (12) →
поэтапные integrations (13–21) → browser/Windows/visual расширения (22–24) → memory/KB (25)
→ workflows/roles (26) → background (27) → demonstrations (28). Финансовые, инфраструктурные,
административные, social DM/publishing и video effects отделены в 29–32 и не разрешены этим
документом автоматически. Точные минимальные dependencies указаны у каждого этапа.

Завершение текущего MVP даст ограниченный Windows-помощник с отдельным Planner, текстом,
локальным голосом, проверяемыми allowlisted Windows/static browser operations, явными
memory labels и Outlook. Оно не означает полную компанию в памяти, все приложения,
English onboarding, фоновые отправки или visual automation.

Сейчас нужен реальный Windows target для M9. Для расширений решения пользователя:
приоритеты, точный Microsoft admin product, purpose/URL/roles/workflows Omadli, AWS
services/accounts/regions, типы аккаунтов/admin consent, corporate storage/processing,
spending и границы background autonomy. Они не блокировали эту документационную задачу.

## Фактические проверки этой задачи

- git status перед работой: чистое дерево; проверены имеющиеся файлы и история.
- Read-only inline Python 3 document check: local links/heading anchors, table columns,
  closed code fences, последовательность milestone 0–32, E01–E12/D01–D21 references и
  scope только Markdown. Пройдено после финальных правок: 15 Markdown файлов,
  204 локальные ссылки/якоря, 23 таблицы, 50 capabilities и 15 сервисов.
  Внешние source references проверялись содержательно при исследовании; локальный
  link checker не является проверкой доступности всех внешних URL.
- git diff --check: пройдено. Whitespace новых Markdown также проверен отдельно.
- Сверка требований с кодом: dashboard input остаётся demo; month calendar не подключён;
  RU voice/recipes не являются EN/RU localization; Outlook incoming attachment fetching,
  first-run wizard, manifest catalog, company KB и shared monetary budgets не реализованы.
- Исправлены найденные cross-document расхождения: будущие mail extensions M13, decision
  IDs, capture dependency M32, отсутствие автоматического capture resume, отдельный
  harness/catalog gate M11 и UI gate M12, real-only preconditions вне pure normalization.

**Не запускались:** pytest, Ruff, mypy, application smoke, build, GUI, Windows installer,
реальное audio/model/mail/API acceptance. Изменения только документационные; старые
числа тестов сохранены как исторические результаты, не как новые проверки.
Windows/live gates остаются pending. Полный MVP не объявляется завершённым.

## Продолжение

Копируемый точный prompt: [ROADMAP.md — Точный следующий prompt](ROADMAP.md#точный-следующий-prompt).
Он продолжает M9 на целевой Windows, сохраняет native approvals и не начинает этапы 10–32.
