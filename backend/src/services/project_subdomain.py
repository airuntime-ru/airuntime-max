"""Project public subdomain helpers."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.project import Project

SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])?$")
RESERVED_SUBDOMAINS = frozenset(
    {
        "www",
        "api",
        "admin",
        "mail",
        "s3",
        "s3-console",
        "traefik",
        "frontend",
        "backend",
        "worker",
        "postgres",
        "redis",
        "minio",
    }
)

# Generic / placeholder project titles that should not become the public hostname.
_GENERIC_NAME_SLUGS = frozenset(
    {
        "project",
        "new",
        "new-project",
        "untitled",
        "novyy-proekt",
        "novyi-proekt",
        "test",
        "site",
        "website",
        "landing",
        "sait",
        "sayt",
        "proekt",
    }
)

_PROMPT_STOPWORDS_RAW = frozenset(
    {
        "a",
        "an",
        "and",
        "app",
        "bot",
        "create",
        "for",
        "from",
        "make",
        "my",
        "of",
        "please",
        "site",
        "the",
        "to",
        "with",
        "website",
        "а",
        "без",
        "бот",
        "в",
        "для",
        "и",
        "из",
        "как",
        "мне",
        "на",
        "нужен",
        "нужна",
        "нужно",
        "о",
        "об",
        "пожалуйста",
        "по",
        "про",
        "просто",
        "сайт",
        "сделай",
        "сделать",
        "с",
        "телеграм",
        "telegram",
        "хочу",
        "этот",
        "эта",
        "это",
    }
)

_CYRILLIC_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

_MAX_ALLOCATE_ATTEMPTS = 50


def transliterate(value: str) -> str:
    """Map Cyrillic letters to Latin; leave other characters as-is for slugify."""
    return "".join(_CYRILLIC_TRANSLIT.get(ch, ch) for ch in value.lower())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", transliterate(value)).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "project"


_PROMPT_STOPWORDS = frozenset(
    {w for raw in _PROMPT_STOPWORDS_RAW for w in (raw, slugify(raw)) if w}
)


def normalize_deploy_subdomain(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if not SUBDOMAIN_RE.fullmatch(cleaned):
        raise HTTPException(
            status_code=400,
            detail="Поддомен: только латиница, цифры и дефис, от 3 до 48 символов",
        )
    if cleaned in RESERVED_SUBDOMAINS:
        raise HTTPException(status_code=400, detail="Этот поддомен зарезервирован")
    return cleaned


def default_deploy_subdomain(project: Project) -> str:
    """Last-resort hostname when nothing was allocated yet (legacy / races)."""
    return f"{slugify(project.name)}-{str(project.id)[:8]}"


def resolve_deploy_subdomain(project: Project) -> str:
    return project.deploy_subdomain or default_deploy_subdomain(project)


def planned_public_url(project: Project) -> str | None:
    if project.type not in ("website", "mixed"):
        return None
    return settings.build_project_url(resolve_deploy_subdomain(project))


def assert_subdomain_available(
    db: Session, subdomain: str, *, exclude_project_id: UUID | str | None = None
) -> None:
    query = db.query(Project).filter(Project.deploy_subdomain == subdomain)
    if exclude_project_id:
        query = query.filter(Project.id != UUID(str(exclude_project_id)))
    if query.first():
        raise HTTPException(status_code=409, detail="Этот поддомен уже занят")


def is_subdomain_available(
    db: Session, subdomain: str, *, exclude_project_id: UUID | str | None = None
) -> bool:
    if subdomain in RESERVED_SUBDOMAINS or not SUBDOMAIN_RE.fullmatch(subdomain):
        return False
    query = db.query(Project).filter(Project.deploy_subdomain == subdomain)
    if exclude_project_id:
        query = query.filter(Project.id != UUID(str(exclude_project_id)))
    return query.first() is None


def _clip_slug(slug: str, *, max_len: int = 48) -> str:
    slug = slug.strip("-")[:max_len].rstrip("-")
    if len(slug) < 3:
        return "project"
    return slug


def _tokens_from_text(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text)
    tokens: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in _PROMPT_STOPWORDS:
            continue
        slug = slugify(word)
        if not slug or slug in _PROMPT_STOPWORDS or len(slug) < 2:
            continue
        if slug.isdigit():
            continue
        tokens.append(slug)
    return tokens


def suggest_deploy_base(*, project_name: str, prompt: str | None = None) -> str:
    """Pick a human-readable base slug from the project name or prompt essence."""
    name_slug = _clip_slug(slugify(project_name))
    if name_slug not in _GENERIC_NAME_SLUGS:
        return name_slug

    if prompt:
        # Prefer a quoted product name if the user named it explicitly.
        quoted = re.search(r"[«\"']([^«\"']{2,40})[»\"']", prompt)
        if quoted:
            quoted_slug = _clip_slug(slugify(quoted.group(1)))
            if quoted_slug not in _GENERIC_NAME_SLUGS:
                return quoted_slug

        tokens = _tokens_from_text(prompt)
        if tokens:
            # 1–3 meaningful tokens keep URLs short but recognizable.
            combined = _clip_slug("-".join(tokens[:3]))
            if combined not in _GENERIC_NAME_SLUGS:
                return combined

    return name_slug


def allocate_unique_subdomain(
    db: Session,
    base: str,
    *,
    exclude_project_id: UUID | str | None = None,
    fallback_suffix: str | None = None,
) -> str:
    """Try `base`, then `base-2`…; fall back to `base-<suffix>` if exhausted."""
    base = _clip_slug(slugify(base))
    candidates = [base]
    for n in range(2, _MAX_ALLOCATE_ATTEMPTS + 1):
        # Keep room for "-NN" within the 48-char limit.
        suffix = f"-{n}"
        candidates.append(_clip_slug(base[: 48 - len(suffix)] + suffix))

    for candidate in candidates:
        if is_subdomain_available(db, candidate, exclude_project_id=exclude_project_id):
            return candidate

    suffix = (fallback_suffix or "x")[:8]
    last_resort = _clip_slug(f"{base[: 48 - len(suffix) - 1]}-{suffix}")
    if is_subdomain_available(db, last_resort, exclude_project_id=exclude_project_id):
        return last_resort
    # Extremely unlikely: keep uniqueness with a longer hex-ish suffix from the hint.
    return _clip_slug(f"p-{suffix}-{base}")[:48].strip("-")


def ensure_deploy_subdomain(
    db: Session,
    project: Project,
    *,
    prompt: str | None = None,
) -> str | None:
    """Allocate and persist a friendly subdomain for website/mixed projects if unset.

    Returns the subdomain that will be used (existing or newly allocated), or None when
    the project type does not get a public HTTP hostname.
    """
    if project.type not in ("website", "mixed"):
        return None
    if project.deploy_subdomain:
        return project.deploy_subdomain

    base = suggest_deploy_base(project_name=project.name, prompt=prompt)
    allocated = allocate_unique_subdomain(
        db,
        base,
        exclude_project_id=str(project.id),
        fallback_suffix=str(project.id)[:8],
    )
    project.deploy_subdomain = allocated
    db.add(project)
    return allocated
