"""The storefront contract.

Everything the mini app renders for one tenant comes from a ``ServiceConfig``. The LLM
writes it, this module is the only thing that decides whether what it wrote is usable,
and the mini app never sees an unvalidated payload.

Keeping the whole storefront as validated data (instead of generated code) is what makes
"one prompt -> a working service" take seconds and survive a bad model day: a malformed
field is clamped or dropped here, not shipped to a customer.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

# Three storefront shapes cover the services micro-business actually asks for. They differ
# in wording and in whether a time slot is part of the request, not in structure.
SERVICE_KINDS = ("booking", "menu", "landing")
SERVICE_MOODS = ("calm", "warm", "bold", "minimal")
SERVICE_LAYOUTS = ("classic", "editorial", "cards", "poster")
SERVICE_COLOR_SCHEMES = ("light", "dark")
SERVICE_HEADING_STYLES = ("sans", "serif", "display")
SERVICE_NAV_STYLES = ("tabs", "pills", "rail", "none")
SERVICE_HERO_STYLES = ("split", "fullbleed", "editorial", "typographic", "collage")
SERVICE_CARD_STYLES = ("image-top", "horizontal", "overlay", "minimal", "numbered", "menu")
SERVICE_RADIUS_STYLES = ("sharp", "soft", "round")
SERVICE_DENSITIES = ("airy", "balanced", "compact")
# Cyrillic-capable families the mini app actually ships. A name outside this list would
# silently fall back to the system font, so it is dropped here instead.
SERVICE_FONTS = (
    "inter",
    "manrope",
    "rubik",
    "unbounded",
    "oswald",
    "playfair",
    "cormorant",
    "lora",
    "comfortaa",
)
SERVICE_PATTERNS = ("none", "grain", "dots", "grid", "rings", "stripes", "glow")
SERVICE_HERO_TONES = ("accent", "tint", "plain", "ink")
MAX_HIGHLIGHTS = 3

MAX_ITEMS = 24
MAX_SLOTS = 12

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Cyrillic -> latin, so a Russian business name still produces a readable deep link.
_TRANSLIT = {
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
    "й": "i",
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
    "ц": "c",
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


def slugify(value: str, *, fallback: str = "service") -> str:
    """A short, URL-safe, latin slug - it ends up in ``?startapp=<slug>``."""
    lowered = unicodedata.normalize("NFKC", value or "").strip().lower()
    latin = "".join(_TRANSLIT.get(char, char) for char in lowered)
    cleaned = _SLUG_RE.sub("-", latin).strip("-")
    return (cleaned or fallback)[:40].strip("-") or fallback


class ServiceItem(BaseModel):
    """One bookable service, menu position or offer."""

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=280)
    # Kept as an integer number of roubles: the storefronts here never need kopecks, and a
    # float would only invite rounding noise in the customer-visible price.
    price_rub: int | None = Field(default=None, ge=0, le=10_000_000)
    duration_min: int | None = Field(default=None, ge=5, le=600)
    category: str = Field(default="", max_length=60)
    image_url: str = Field(default="", max_length=500)
    badge: str = Field(default="", max_length=32)

    @field_validator("title", "description", "category", "badge", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("image_url", mode="before")
    @classmethod
    def _image_url(cls, value: object) -> str:
        url = str(value or "").strip()
        return url if re.match(r"^https://[^\s]+$", url) else ""

    @field_validator("duration_min", mode="before")
    @classmethod
    def _duration(cls, value: object) -> object:
        if value in (0, "0", "", None):
            return None
        return value


def _hex_rgb(value: str) -> tuple[float, float, float]:
    raw = value.lstrip("#")
    if len(raw) == 3:
        raw = "".join(part * 2 for part in raw)
    return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(value: str) -> float:
    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in _hex_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    a, b = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


class ServicePalette(BaseModel):
    """The storefront's own colours. Empty means "use the mood preset".

    The model is free to pick any colours - that freedom is most of what makes two
    storefronts look unrelated - but a palette that would make the text unreadable is
    dropped as a whole rather than half-applied.
    """

    bg: str = ""
    surface: str = ""
    ink: str = ""
    accent2: str = ""

    @field_validator("bg", "surface", "ink", "accent2", mode="before")
    @classmethod
    def _hex(cls, value: object) -> str:
        colour = str(value or "").strip()
        return colour if _HEX_COLOR_RE.match(colour) else ""

    @model_validator(mode="after")
    def _readable(self) -> ServicePalette:
        if not (self.bg and self.ink) or contrast_ratio(self.ink, self.bg) < 7:
            self.bg = self.surface = self.ink = ""
            return self
        if self.surface and contrast_ratio(self.ink, self.surface) < 6:
            self.surface = ""
        return self


class ServiceHighlight(BaseModel):
    """A short fact shown large in the hero: "45 мин" / "стрижка"."""

    value: str = Field(min_length=1, max_length=16)
    label: str = Field(default="", max_length=40)

    @field_validator("value", "label", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return str(value or "").strip()


class ServiceContacts(BaseModel):
    phone: str = Field(default="", max_length=32)
    address: str = Field(default="", max_length=200)
    hours: str = Field(default="", max_length=120)

    @field_validator("phone", "address", "hours", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return str(value or "").strip()


_TEXT_LIMITS = {
    "title": 120,
    "tagline": 160,
    "about": 600,
    "cta_label": 40,
    "success_message": 160,
    "comment_hint": 80,
    "design_concept": 100,
}


class ServiceConfig(BaseModel):
    """The full storefront. One row of ``max_services.config_json``."""

    kind: str = "booking"
    title: str = Field(min_length=1, max_length=120)
    tagline: str = Field(default="", max_length=160)
    about: str = Field(default="", max_length=600)
    accent: str = "#2E7CF6"
    # Visual register of the storefront. The mini app is one renderer, so mood is how a
    # tutor page stops looking like an auto shop: palette and hero, not a new template.
    mood: str = "bold"
    # These are deliberately bounded design choices, rather than arbitrary generated CSS.
    # They give the model several genuinely different compositions without allowing a
    # prompt (or a compromised model response) to inject markup into every customer's app.
    layout: str = "classic"
    color_scheme: str = "light"
    heading_style: str = "sans"
    # Owner-supplied image only. The generator never gets to invent or hot-link this URL.
    hero_image: str = Field(default="", max_length=1_400_000)
    # A broad, safe design language. The model can combine these into hundreds of distinct
    # compositions while checkout and MAX authentication remain platform-owned.
    design_concept: str = Field(default="", max_length=100)
    nav_style: str = "none"
    hero_style: str = "split"
    card_style: str = "image-top"
    radius_style: str = "soft"
    density: str = "balanced"
    section_order: list[str] = Field(default_factory=lambda: ["hero", "story", "catalog"])
    # Empty = derived from heading_style/mood on the client, so storefronts generated
    # before these fields existed keep rendering as they did.
    heading_font: str = ""
    palette: ServicePalette = Field(default_factory=ServicePalette)
    pattern: str = "none"
    hero_tone: str = ""
    # The line above the title ("Барбершоп с 2016"). Empty = the generic kind label.
    kicker: str = Field(default="", max_length=48)
    catalog_title: str = Field(default="", max_length=40)
    highlights: list[ServiceHighlight] = Field(default_factory=list)
    marquee: bool = False
    contacts: ServiceContacts = Field(default_factory=ServiceContacts)
    items: list[ServiceItem] = Field(default_factory=list)
    # Human-readable slots ("Вт 23 сен, 16:00"). The owner's calendar parses them.
    slots: list[str] = Field(default_factory=list)
    # Blank by default on purpose: normalise() fills the wording that fits the kind, and a
    # non-empty default here would silently win over it ("Записаться" on a menu).
    cta_label: str = Field(default="", max_length=40)
    success_message: str = Field(default="", max_length=160)
    # Placeholder on the comment field. Must match the business: "марка авто" on a tutor
    # page is how the form starts looking copied from the demo.
    comment_hint: str = Field(default="", max_length=80)
    ask_phone: bool = True
    ask_comment: bool = True
    # False keeps every existing storefront's behaviour. Owners can enable a real cart
    # for menus and combinable services from their cabinet.
    allow_multiple_items: bool = False

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: object) -> str:
        kind = str(value or "").strip().lower()
        return kind if kind in SERVICE_KINDS else "booking"

    @field_validator("mood", mode="before")
    @classmethod
    def _mood(cls, value: object) -> str:
        mood = str(value or "").strip().lower()
        return mood if mood in SERVICE_MOODS else "bold"

    @field_validator("layout", mode="before")
    @classmethod
    def _layout(cls, value: object) -> str:
        layout = str(value or "").strip().lower()
        return layout if layout in SERVICE_LAYOUTS else "classic"

    @field_validator("color_scheme", mode="before")
    @classmethod
    def _color_scheme(cls, value: object) -> str:
        scheme = str(value or "").strip().lower()
        return scheme if scheme in SERVICE_COLOR_SCHEMES else "light"

    @field_validator("heading_style", mode="before")
    @classmethod
    def _heading_style(cls, value: object) -> str:
        style = str(value or "").strip().lower()
        return style if style in SERVICE_HEADING_STYLES else "sans"

    @field_validator("hero_image", mode="before")
    @classmethod
    def _hero_image(cls, value: object) -> str:
        image = str(value or "").strip()
        # Only decoded owner uploads are persisted. No javascript:, SVG or remote tracking.
        allowed = re.match(r"^data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$", image)
        return image if allowed else ""

    @field_validator("nav_style", mode="before")
    @classmethod
    def _nav_style(cls, value: object) -> str:
        style = str(value or "").strip().lower()
        return style if style in SERVICE_NAV_STYLES else "none"

    @field_validator("hero_style", mode="before")
    @classmethod
    def _hero_style(cls, value: object) -> str:
        style = str(value or "").strip().lower()
        return style if style in SERVICE_HERO_STYLES else "split"

    @field_validator("card_style", mode="before")
    @classmethod
    def _card_style(cls, value: object) -> str:
        style = str(value or "").strip().lower()
        return style if style in SERVICE_CARD_STYLES else "image-top"

    @field_validator("radius_style", mode="before")
    @classmethod
    def _radius_style(cls, value: object) -> str:
        style = str(value or "").strip().lower()
        return style if style in SERVICE_RADIUS_STYLES else "soft"

    @field_validator("density", mode="before")
    @classmethod
    def _density(cls, value: object) -> str:
        density = str(value or "").strip().lower()
        return density if density in SERVICE_DENSITIES else "balanced"

    @field_validator("kicker", "catalog_title", mode="before")
    @classmethod
    def _short_label(cls, value: object) -> str:
        # Decorative labels: an over-long one is trimmed, not a reason to reject the reply.
        return str(value or "").strip()[:40]

    @field_validator("heading_font", mode="before")
    @classmethod
    def _heading_font(cls, value: object) -> str:
        font = str(value or "").strip().lower()
        return font if font in SERVICE_FONTS else ""

    @field_validator("pattern", mode="before")
    @classmethod
    def _pattern(cls, value: object) -> str:
        pattern = str(value or "").strip().lower()
        return pattern if pattern in SERVICE_PATTERNS else "none"

    @field_validator("hero_tone", mode="before")
    @classmethod
    def _hero_tone(cls, value: object) -> str:
        tone = str(value or "").strip().lower()
        return tone if tone in SERVICE_HERO_TONES else ""

    @field_validator("palette", mode="before")
    @classmethod
    def _palette(cls, value: object) -> object:
        return value if isinstance(value, dict | ServicePalette) else {}

    @field_validator("highlights", mode="before")
    @classmethod
    def _highlights(cls, value: object) -> list[object]:
        if not isinstance(value, list):
            return []
        # One malformed fact must not cost the whole storefront its validation.
        kept: list[object] = []
        for entry in value:
            try:
                kept.append(ServiceHighlight.model_validate(entry))
            except Exception:
                continue
        return kept[:MAX_HIGHLIGHTS]

    @field_validator("section_order", mode="before")
    @classmethod
    def _section_order(cls, value: object) -> list[str]:
        allowed = ("hero", "story", "catalog")
        if not isinstance(value, list):
            return list(allowed)
        result: list[str] = []
        for item in value:
            name = str(item)
            if name in allowed and name not in result:
                result.append(name)
        result.extend(item for item in allowed if item not in result)
        return result[:3]

    @field_validator(
        "title",
        "tagline",
        "about",
        "cta_label",
        "success_message",
        "comment_hint",
        "design_concept",
        mode="before",
    )
    @classmethod
    def _text(cls, value: object, info: ValidationInfo) -> str:
        text = str(value or "").strip()
        # A richer prompt means longer copy; one sentence over the limit is trimmed at a
        # word boundary instead of failing the whole storefront into a retry.
        limit = _TEXT_LIMITS.get(info.field_name or "", 0)
        if limit and len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-") or text[:limit]
        return text

    @field_validator("accent", mode="before")
    @classmethod
    def _accent(cls, value: object) -> str:
        colour = str(value or "").strip()
        return colour if _HEX_COLOR_RE.match(colour) else "#2E7CF6"

    @field_validator("items", mode="after")
    @classmethod
    def _cap_items(cls, value: list[ServiceItem]) -> list[ServiceItem]:
        return value[:MAX_ITEMS]

    @field_validator("slots", mode="before")
    @classmethod
    def _slots(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = [str(slot).strip()[:64] for slot in value if str(slot or "").strip()]
        return cleaned[:MAX_SLOTS]


DEFAULT_CTA_BY_KIND = {
    "booking": "Записаться",
    "menu": "Заказать",
    "landing": "Оставить заявку",
}

DEFAULT_SUCCESS_BY_KIND = {
    "booking": "Заявка на запись принята — свяжемся для подтверждения",
    "menu": "Заказ принят — свяжемся для подтверждения",
    "landing": "Заявка принята — свяжемся с вами",
}

DEFAULT_COMMENT_HINT_BY_KIND = {
    "booking": "Пожелания к записи",
    "menu": "Аллергии, пожелания к заказу",
    "landing": "Что нужно обсудить",
}


def normalise(config: ServiceConfig) -> ServiceConfig:
    """Fill the per-kind wording the model left blank.

    Doing it after validation rather than in the prompt keeps the storefront usable even
    when the model ignores half the instructions.
    """
    data = config.model_dump()
    if not data["cta_label"]:
        data["cta_label"] = DEFAULT_CTA_BY_KIND.get(config.kind, "Отправить")
    if not data["success_message"]:
        data["success_message"] = DEFAULT_SUCCESS_BY_KIND.get(config.kind, "Заявка принята")
    if not data["comment_hint"]:
        data["comment_hint"] = DEFAULT_COMMENT_HINT_BY_KIND.get(config.kind, "Пожелания или вопрос")
    if config.kind != "booking":
        # Only a booking asks "when" - a menu order or an enquiry has no slot.
        data["slots"] = []
    return ServiceConfig.model_validate(data)
