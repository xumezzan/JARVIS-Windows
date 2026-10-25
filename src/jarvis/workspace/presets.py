"""Optional owner-selected presets; startup never installs a business profile implicitly."""

from jarvis.security.browser_policy import origin
from jarvis.workspace.models import (
    AppEntry,
    Category,
    UserIdentity,
    WorkflowPlaybook,
    WorkspaceProfile,
)


def empty_profile(user: UserIdentity | None = None) -> WorkspaceProfile:
    return WorkspaceProfile(user=user or UserIdentity())


def _web(id: str, name: str, category: Category, url: str, notes: str) -> AppEntry:
    return AppEntry(
        id=id,
        name=name,
        category=category,
        kind="web",
        launch_url=url,
        allowed_origins=(origin(url),),
        workspace_notes=notes,
    )


def omadli_group_preset(
    *, omadli_group_url: str | None = None, user: UserIdentity | None = None
) -> WorkspaceProfile:
    """Fourteen app descriptions, without inferred owner identity, credentials or tools."""
    apps = (
        _web(
            "asana",
            "Asana",
            "productivity",
            "https://app.asana.com",
            "Ведение проектов, задач, сроков и ответственных Omadli Group.",
        ),
        _web(
            "claude",
            "Claude",
            "productivity",
            "https://claude.ai",
            "Подготовка текстов и анализ рабочих материалов. Передача данных требует согласия.",
        ),
        _web(
            "ms_mail",
            "Microsoft Email",
            "communication",
            "https://outlook.office.com/mail",
            "Чтение деловой почты и подготовка ответов в стиле пользователя. Отправка = CONFIRM.",
        ),
        _web(
            "ms_calendar",
            "Microsoft Calendar",
            "productivity",
            "https://outlook.office.com/calendar",
            "Рабочие встречи и расписание. Приглашения другим участникам требуют CONFIRM.",
        ),
        AppEntry(
            id="social_comments",
            name="Instagram & Facebook",
            category="social",
            kind="web",
            launch_url="https://www.instagram.com",
            allowed_origins=("https://www.instagram.com", "https://www.facebook.com"),
            workspace_notes=(
                "Написание и ответы на комментарии в Instagram и Facebook в стиле пользователя. "
                "Подготовить текст можно заранее; финальная публикация всегда CONFIRM."
            ),
        ),
        AppEntry(
            id="omadli_group",
            name="Omadli Group Website",
            category="custom_erp",
            kind="web",
            launch_url=omadli_group_url,
            allowed_origins=(origin(omadli_group_url),) if omadli_group_url else (),
            workspace_notes=(
                "Внутренняя веб-система компании: бизнес-процессы и операционные данные. "
                "URL и домен задаёт владелец; не угадывать адрес или полномочия."
            ),
            enabled=omadli_group_url is not None,
        ),
        _web(
            "notion",
            "Notion",
            "productivity",
            "https://www.notion.so",
            "База знаний, документы и рабочие заметки компании.",
        ),
        _web(
            "quickbooks",
            "QuickBooks",
            "finance",
            "https://app.qbo.intuit.com",
            "Учёт и финансовые отчёты. Финансовые операции = CONFIRM; "
            "более строгая read-only политика браузера имеет приоритет.",
        ),
        _web(
            "ms_teams",
            "Microsoft Teams",
            "communication",
            "https://teams.microsoft.com",
            "Командные обсуждения и встречи. Отправка сообщения или публикация = CONFIRM.",
        ),
        _web(
            "aws_console",
            "AWS Console",
            "cloud_admin",
            "https://console.aws.amazon.com",
            "Обзор облачных ресурсов. Модификация ресурсов = CONFIRM; "
            "более строгая read-only политика браузера имеет приоритет.",
        ),
        _web(
            "fireflies",
            "Fireflies.ai",
            "productivity",
            "https://app.fireflies.ai",
            "Транскрипты встреч, итоги обсуждений и принятые решения.",
        ),
        AppEntry(
            id="onepassword",
            name="1Password",
            category="security",
            kind="native",
            native_exe_hint="1Password.exe",
            workspace_notes=(
                "Менеджер паролей. Никогда не читать и не передавать пароли, секреты, "
                "содержимое хранилища в LLM, scratchpad или логи. Только ручная разблокировка "
                "и автозаполнение владельцем; браузерный доступ к менеджерам паролей запрещён. "
                "Автоматический вызов автозаполнения этим пресетом не реализован."
            ),
        ),
        _web(
            "ms_admin",
            "Microsoft 365 Admin",
            "cloud_admin",
            "https://admin.microsoft.com",
            "Обзор Microsoft 365. Админ-действия = CONFIRM; "
            "более строгая read-only политика браузера имеет приоритет.",
        ),
        _web(
            "loom",
            "Loom",
            "communication",
            "https://www.loom.com",
            "Рабочие видеосообщения и инструкции. Публикация и отправка ссылки = CONFIRM.",
        ),
    )
    return WorkspaceProfile(
        user=user or UserIdentity(organization="Omadli Group"),
        apps=apps,
        playbooks=(
            WorkflowPlaybook(
                id="meeting_preparation",
                title="Подготовка к встрече",
                trigger_hint="Подготовь материалы к рабочей встрече",
                steps_description=(
                    "Уточнить встречу по календарю и наблюдать её участников в текущей задаче.",
                    "Найти связанные задачи, документы и доступные транскрипты прошлых встреч.",
                    "Подготовить краткую повестку со ссылками на наблюдавшиеся источники.",
                ),
                apps_involved=("ms_calendar", "asana", "notion", "fireflies"),
            ),
            WorkflowPlaybook(
                id="social_comment_reply",
                title="Ответ на комментарий",
                trigger_hint="Подготовь ответ на комментарий в соцсетях",
                steps_description=(
                    "Наблюдать выбранный комментарий и контекст в текущей задаче.",
                    "Подготовить ответ на языке и в тоне пользователя.",
                    "Перед финальной публикацией запросить точное UI-подтверждение.",
                ),
                apps_involved=("social_comments",),
            ),
        ),
    )
