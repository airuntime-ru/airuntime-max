import re
from pathlib import Path

from src.api.dto.project import ProjectType
from src.db.models.project import Project

_BOT_TERMS = (
    "telegram",
    "телеграм",
    "тг",
    "бот",
    "bot",
    "webhook",
    "polling",
)

# Strong site-product terms: almost never appear when the user only wants a bot.
_SITE_STRONG_TERMS = (
    "лендинг",
    "landing",
    "website",
    "личный кабинет",
)

# Weak terms: often incidental (парсинг сайта, дашборд в боте). Alone they must not flip
# a clear bot request to mixed.
_SITE_WEAK_TERMS = (
    "сайт",
    "дашборд",
)

_SITE_WHOLE_WORDS = (
    "web",
    "веб",
    "лк",
    # "интерфейс" is too ambiguous (Telegram button UIs, bot menus) to count as a site signal.
    # "страниц" / "страница" match Telegram menus ("страницы меню") — never count as site.
)

# Explicit "I want a hosted website" phrasing (RU/EN), including dual-product asks.
_SITE_PRODUCT_INTENT = re.compile(
    r"(?:"
    r"(?:сделай|создай|хочу|нужен|нужна|нужно|сделать|разработай|напиши|добавь)\s+"
    r"(?:(?:ещё|еще|также|тоже|отдельный|отдельную|простой|небольшой|новый|красивый)\s+)*"
    r"(?:сайт|лендинг|landing|website|веб(?:сайт)?|web(?:site)?|лк|личный\s+кабинет)"
    r"|"
    r"(?:сайт|лендинг|landing|website)\s+(?:для|и\b)"
    r"|"
    r"\bи\s+(?:сайт|лендинг|landing|website)\b"
    r"|"
    r"(?:telegram|телеграм|тг|бот|bot).{0,60}(?:и|,)\s*"
    r"(?:сайт|лендинг|landing|website)"
    r"|"
    # Reverse order: a site term mentioned first, then a bot term joined by "и"/",", even with
    # a noun in between ("Сайт студии и Telegram-бот") - mirrors the alternative above so word
    # order alone doesn't decide whether a dual-product ask gets recognised as one.
    r"(?:сайт|лендинг|landing|website).{0,60}(?:и|,)\s*"
    r"(?:telegram|телеграм|тг|бот|bot)"
    r")",
    re.IGNORECASE,
)

# User explicitly declined a hosted site.
_SITE_NEGATION = re.compile(
    r"(?:"
    r"(?:не\s+нужен|не\s+нужна|не\s+нужно|не\s+надо|не\s+требуется|без|только\s+бот)"
    r".{0,40}(?:сайт|лендинг|landing|website)"
    r"|"
    r"(?:сайт|лендинг|landing|website).{0,40}"
    r"(?:не\s+нужен|не\s+нужна|не\s+нужно|не\s+надо|не\s+требуется|не\s+делай)"
    r")",
    re.IGNORECASE,
)

# "сайт" as an external URL / scrape target, not a product to host on the platform.
_SITE_AS_EXTERNAL_OBJECT = re.compile(
    r"(?:"
    r"(?:парс(?:ит|инг|ить|ер)?|скрап|scrape|монитор(?:ит|инг)?|ходит\s+на|"
    r"открывает|читает|собирает|берёт|берет)\s+.{0,40}сайт"
    r"|"
    r"сайт(?:а|у|е|ом)?\s+.{0,40}(?:парс|конкурент|api|url|ссылк)"
    r"|"
    r"(?:с|со)\s+сайт(?:а|ов)\b"
    r"|"
    r"на\s+сайт(?:е|ах)?\b"
    r")",
    re.IGNORECASE,
)


def _count_word_matches(terms: tuple[str, ...], text: str) -> int:
    # Match at a word start (not anywhere mid-word - a plain "in" check would count ordinary
    # words like "работа"/"работало" as bot signals, since they contain "бот" as a substring),
    # but allow anything after the term so inflected forms still count ("бота", "боту", ...).
    return sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\w*", text))


def _count_whole_word_matches(terms: tuple[str, ...], text: str) -> int:
    return sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\b", text))


def detect_project_type_signals(text: str) -> tuple[int, int]:
    normalized = text.lower()
    bot_score = _count_word_matches(_BOT_TERMS, normalized)

    strong = _count_word_matches(_SITE_STRONG_TERMS, normalized)
    weak = _count_word_matches(_SITE_WEAK_TERMS, normalized)
    whole = _count_whole_word_matches(_SITE_WHOLE_WORDS, normalized)

    suppress_weak = bool(
        _SITE_NEGATION.search(normalized) or _SITE_AS_EXTERNAL_OBJECT.search(normalized)
    )
    if suppress_weak:
        weak = 0

    site_score = strong + weak + whole
    return bot_score, site_score


def infer_project_type(text: str) -> ProjectType:
    bot_score, site_score = detect_project_type_signals(text)
    wants_site_product = bool(_SITE_PRODUCT_INTENT.search(text))
    site_negated = bool(_SITE_NEGATION.search(text))

    if bot_score > 0 and site_score > 0:
        # Dual-product only when the user clearly asked to *host* a site, not merely mentioned
        # "сайт"/"дашборд" in a bot context (parsing, menus, admin screens inside Telegram).
        if site_negated:
            return ProjectType.telegram_bot
        strong = _count_word_matches(_SITE_STRONG_TERMS, text.lower())
        if strong > 0 or wants_site_product:
            return ProjectType.mixed
        return ProjectType.telegram_bot

    if bot_score > site_score:
        return ProjectType.telegram_bot
    if site_score > 0 or wants_site_product:
        return ProjectType.website
    # No signal yet - keep the historical create default so empty names still get a type.
    return ProjectType.website


def _workspace_has_site(root: Path) -> bool:
    return (root / "public" / "index.html").exists()


def _workspace_has_bot(root: Path) -> bool:
    return (root / "app.py").exists() or (root / "bot.py").exists()


def workspace_root_for(project: Project) -> Path:
    from src.services.workspace import project_dir_path

    # Resolve only - do not mkdir. Reconcile/read paths must not require a writable
    # generated_projects_dir (CI and hosts where /data is unavailable).
    return project_dir_path(project.id)


def can_update_project_type(project: Project) -> bool:
    """Allow reclassification until real bot/site files exist."""
    if project.status == "created":
        return True
    root = workspace_root_for(project)
    return not _workspace_has_site(root) and not _workspace_has_bot(root)


def update_project_type_from_prompt(project: Project, prompt: str) -> bool:
    bot_score, site_score = detect_project_type_signals(prompt)
    wants_site_product = bool(_SITE_PRODUCT_INTENT.search(prompt))
    if bot_score == 0 and site_score == 0 and not wants_site_product:
        # A follow-up without type signals must not overwrite a correct bot/site classification
        # with the ambiguous website default.
        return False
    inferred = infer_project_type(prompt)
    if project.type == inferred:
        return False
    project.type = inferred
    if inferred == ProjectType.telegram_bot:
        project.deploy_subdomain = None
    return True


def reconcile_type_with_workspace(
    project: Project,
    workspace: Path | None = None,
    *,
    has_bot_secret: bool = False,
) -> bool:
    """Fix type when workspace/secrets clearly indicate bot-only (no site).

    Covers false `mixed`/`website` classifications that still show the subdomain card
    for projects that never generated a website.
    """
    if project.type == ProjectType.telegram_bot:
        return False

    root = workspace if workspace is not None else workspace_root_for(project)
    has_site = _workspace_has_site(root)
    has_bot_files = _workspace_has_bot(root)

    if has_site:
        # A configured TELEGRAM_BOT_TOKEN is as strong a signal the project should stay `mixed`
        # as app.py/bot.py existing on disk (it's only ever requested for a bot/mixed project).
        # Without this, a `mixed` project whose bot code just hadn't landed on disk *yet* this
        # turn got silently demoted to `website` the moment its site files appeared, even with a
        # real token already sitting in its secrets - the settings page then had no way to know
        # the project was ever supposed to have a bot (reported 2026-07-23: "нет окна с
        # настройкой бота, хотя есть токен"). Scoped to *this* branch only - the "no site on
        # disk yet" branches below intentionally still require has_bot_files or wait for it (see
        # test_reconcile_keeps_mixed_with_only_bot_secret_before_files).
        if not has_bot_files and not has_bot_secret and project.type == ProjectType.mixed:
            project.type = ProjectType.website
            return True
        return False

    # No site entrypoint on disk.
    if has_bot_files and project.type in (ProjectType.mixed, ProjectType.website):
        project.type = ProjectType.telegram_bot
        project.deploy_subdomain = None
        return True

    # website + TELEGRAM_BOT_TOKEN and no site files is inconsistent (token is only
    # requested for bot/mixed). Do not auto-demote intentional mixed before generation.
    if has_bot_secret and project.type == ProjectType.website:
        project.type = ProjectType.telegram_bot
        project.deploy_subdomain = None
        return True

    return False


# Backwards-compatible alias used by older call sites / tests.
def reconcile_mixed_type_without_website(project: Project, workspace: Path | None = None) -> bool:
    return reconcile_type_with_workspace(project, workspace)
