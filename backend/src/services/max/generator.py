"""One prompt in, a validated storefront out.

The owner types a sentence about their business into the MAX bot; this turns it into a
``ServiceConfig``. Two deliberate choices:

- **the model never writes code here.** It fills a schema. A storefront is then rendered by
  one multi-tenant mini app, so "describe it -> a customer can book" takes seconds and
  cannot be broken by a model that had a bad day. Code generation (the regular AIRuntime
  pipeline) stays available for the public site, where minutes are acceptable.
- **it always returns something.** If the provider is down or answers with noise, the
  keyword fallback still produces a usable draft the owner can edit in the bot. A wizard
  that dead-ends on a provider hiccup is worse than a rough first draft.
"""

from __future__ import annotations

import json
import logging
import random
import re

from src.core.config import settings
from src.services.agent.events import TextDelta, TurnFinished
from src.services.agent.providers import get_agent_provider
from src.services.file_context import ImageAttachment
from src.services.max.schema import ServiceConfig, normalise
from src.services.max.slots import fallback_slots, now_msk, russian_today
from src.services.provider.factory import resolve_provider_and_model
from src.services.system_settings import resolve_platform_api_key

logger = logging.getLogger(__name__)

GENERATION_TIMEOUT_HINT = "Собираю сервис…"

# Three because the owner is watching a chat: at roughly five seconds a turn, two retries
# stay inside the patience the "Собираю сервис…" line buys, and a fourth would not.
_ATTEMPTS = 3


class LlmUnavailable(RuntimeError):
    """No model can be called at all. Distinct from a turn that went wrong: the caller must
    not burn the retry budget on a deployment that simply has no key."""


_SYSTEM_PROMPT = """Ты — арт-директор и редактор AIRuntime. Ты делаешь витрину бизнеса, \
которую клиенты открывают как мини-приложение в мессенджере MAX — только на телефоне, \
в экране шириной 360–430 px.

Владелец описывает бизнес в мини-приложении. К описанию могут быть приложены сайт \
(текст страницы, цвета, позиции с фото) и файлы: логотип, фото, прайс, референс дизайна. \
Ты превращаешь это в JSON-конфигурацию. Её рендерит готовый движок, поэтому ты не пишешь \
код — ты принимаешь дизайнерские решения, и от них целиком зависит, будет ли витрина \
выглядеть как дорогой брендовый сайт или как шаблон.

Верни СТРОГО один JSON-объект без markdown, без пояснений, без ```-ограждений.

Схема:
{
  "kind": "booking" | "menu" | "landing",
  "title": "название бизнеса, до 120 символов",
  "kicker": "строка над названием, до 40 символов: «Барбершоп · Лиговский, 50»",
  "tagline": "подзаголовок-обещание, до 160 символов",
  "about": "1-3 конкретных предложения, до 600 символов",
  "catalog_title": "заголовок каталога по-человечески, до 40 символов: «Прайс», «Что заказать»",
  "highlights": [{"value": "45 мин", "label": "длится стрижка"}],
  "design_concept": "короткое имя концепции",
  "accent": "#RRGGBB",
  "palette": {"bg": "#RRGGBB", "surface": "#RRGGBB", "ink": "#RRGGBB", "accent2": "#RRGGBB"},
  "heading_font": "manrope" | "rubik" | "unbounded" | "oswald" | "playfair" | "cormorant" | "lora" | "comfortaa",
  "hero_style": "split" | "fullbleed" | "editorial" | "typographic" | "collage",
  "hero_tone": "accent" | "tint" | "plain" | "ink",
  "pattern": "none" | "grain" | "dots" | "grid" | "rings" | "stripes" | "glow",
  "card_style": "image-top" | "horizontal" | "overlay" | "minimal" | "numbered" | "menu",
  "nav_style": "tabs" | "pills" | "rail" | "none",
  "radius_style": "sharp" | "soft" | "round",
  "density": "airy" | "balanced" | "compact",
  "marquee": false,
  "section_order": ["hero", "story", "catalog"],
  "mood": "calm" | "warm" | "bold" | "minimal",
  "color_scheme": "light" | "dark",
  "contacts": {"phone": "", "address": "", "hours": ""},
  "items": [
    {"title": "название позиции", "description": "короткое пояснение или пустая строка",
     "price_rub": 1500, "duration_min": 60, "category": "категория или пустая строка",
     "image_url": "точный https URL фото из источника или пустая строка", "badge": "хит или пусто"}
  ],
  "slots": ["Вт 23 сен, 16:00", "Ср 24 сен, 18:00"],
  "cta_label": "текст кнопки действия",
  "success_message": "что увидит клиент после отправки",
  "comment_hint": "плейсхолдер поля комментария, по делу этого бизнеса",
  "ask_phone": true,
  "ask_comment": true,
  "allow_multiple_items": true
}

ДИЗАЙН. Порядок работы: сначала пойми характер бренда и клиента, придумай design_concept \
(«Утренняя пекарня», «Неон и сталь», «Тетрадь в клетку»), затем выведи из неё ВСЕ \
остальные решения как одну композицию.

Палитра (palette + accent) — главное, что отличает одну витрину от другой:
- Составь собственную палитру под концепцию, а не бери дефолт. bg — фон страницы, \
surface — карточки (чуть светлее или темнее bg), ink — текст, accent — кнопки и акценты, \
accent2 — второй цвет для градиентов и фактур.
- Контраст обязателен: ink на bg не ниже 7:1, ink на surface не ниже 6:1. Иначе палитра \
будет отброшена целиком. Светлый bg — почти чёрный ink; тёмный bg — почти белый ink.
- Избегай безликого: чисто белый #FFFFFF фон, серо-голубой #F2F4F7 и синий #2E7CF6 — \
признаки шаблона. Тёплые кремовые, глубокие тёмные, насыщенные, пастельные — всё можно, \
если служит концепции. Цвета сайта и фото владельца важнее твоего вкуса.
- accent и accent2 должны быть одной светлоты (оба тёмные или оба светлые): на их \
градиенте лежит текст.
- Тёмный bg — только если его просили, референс тёмный или концепция однозначно ночная \
(бар, барбер, клуб, неон).

Типографика (heading_font — шрифт заголовков, все с кириллицей):
- manrope — чистый современный гротеск; rubik — дружелюбный плотный; unbounded — широкий \
технологичный, для молодых и дерзких брендов; oswald — узкий плакатный капсом, для спорта, \
барберов, мастерских; playfair — контрастная антиква, мода, рестораны, юристы; \
cormorant — изящная антиква, красота, кофе, ювелирка, флористика; lora — книжная \
антиква, образование, психология; comfortaa — мягкая округлая, дети, уход, кондитерская.

Hero (первый экран — решает всё):
- fullbleed — от края до края, лучше всего с сильным фото; без фото это большой цветной \
блок с фактурой.
- typographic — огромное название как главный образ; идеально для сильного имени без фото.
- editorial — без плашки, журнальный заголовок на фоне страницы и фото в рамке под ним; \
для авторских, спокойных и премиальных.
- split — компактная карточка, фото сверху; утилитарно и быстро.
- collage — мозаика из 2-3 фото каталога под текстом; только если фото минимум 2.
- hero_tone: accent — градиент accent→accent2 (ярко); tint — лёгкий оттенок акцента; \
plain — цвет карточек; ink — инверсия (цвет текста как фон, очень контрастно).
- pattern — фактура hero без фото: grain (зерно, тактильно), dots (точки), grid (сетка, \
технично), rings (кольца), stripes (диагональ, энергично), glow (свечение accent2).

Каталог:
- card_style: image-top — сетка 2 колонки с фото, для еды и товаров; overlay — высокие \
плитки с текстом на картинке, для визуальных услуг; horizontal — фото слева, текст справа; \
numbered — открытый список с крупными номерами 01, 02 (программы, курсы, этапы); menu — \
название, отточие, цена, как в печатном меню (кафе, бар, прайс барбера); minimal — \
аккуратные строки. Фото нет — не выбирай image-top: движок покажет цветные плитки \
с буквой, это хуже, чем numbered/menu/minimal.
- nav_style: если категорий 2 и больше — обязательно pills, tabs или rail. none — только \
для одной категории.
- highlights: 2-3 коротких факта крупными цифрами (время, опыт, цена «от», режим). ТОЛЬКО \
то, что прямо есть в материалах владельца. Нет фактов — пустой массив.
- marquee: бегущая строка из категорий под hero — для ярких, уличных, food и спортивных \
брендов; для спокойных и премиальных false.
- radius_style и density — продолжение концепции: sharp+compact — строго и плотно, \
round+airy — мягко и воздушно.

Витрины одной отрасли не должны совпадать. Если ниже дано «Арт-направление этой \
генерации», используй его как стартовую идею и адаптируй под бренд; отбрось, только если \
оно прямо противоречит сайту, фото или просьбе владельца.

КОНТЕНТ:
- kind: "booking" — если клиент записывается на время (услуги, мастера, репетитор, приём); \
"menu" — если выбирает позицию из каталога или меню (еда, доставка, товары); \
"landing" — если просто оставляет заявку (консультация, презентация, сбор контактов).
- items: только то, что владелец реально назвал. Если назвал одну услугу — верни одну. \
Не размножай шаблонными «Услуга 2», «Пакет 3», «Консультация VIP». 3-10 позиций — только \
когда в описании, на сайте или в прайсе действительно столько пунктов.
- Описание владельца задаёт фокус, даже если сайт шире. Для запроса «кофейня» выбирай из \
большого ресторанного меню 6-10 релевантных кофе, напитков, выпечки и десертов; не тащи \
горячие блюда только потому, что они встретились на сайте. Аналогично для любой отдельной \
точки или категории. Разнообразие создавай дизайном и подачей, а не нерелевантными позициями.
- Если есть сайт или прайс — услуги и цены бери оттуда, ничего не выдумывай.
- Если источник дал `Фото: https://...` под позицией, скопируй точный URL в image_url этой \
позиции. Никогда не придумывай URL. Сохрани категории из источника в category.
- duration_min заполняй только для kind = "booking" и только если это осмысленно.
- allow_multiple_items: true, если позиции естественно складываются в один заказ (еда, \
товары, допуслуги автосервиса или салона); false, если клиент выбирает один взаимоисключающий \
вариант записи. Владелец сможет поменять это в кабинете.
- slots: 4-6 ближайших слотов, только для kind = "booking". Формат строго \
«Вт 23 сен, 16:00» — конкретный день, без слов «сегодня» и «завтра». \
Для остальных типов — пустой массив.
- contacts: только то, что владелец реально написал. Не выдумывай телефон, адрес, часы.
- comment_hint: подсказка ИМЕННО для этого бизнеса. Репетитор — «Класс, тема занятия, \
онлайн или очно». Автосервис — «Марка, год, что случилось». Кафе — «Аллергии, пожелания».
- mood — общий регистр (calm — обучение, медицина; warm — еда, дети, уют; bold — авто, \
барбер, спорт; minimal — консультации, B2B); color_scheme — light или dark по palette.bg.
- title — название вывески (бренд или как владелец назвал точку). Никогда не копируй \
служебные подписи вроде «Описание владельца», «Сайт владельца», «Название страницы».
- Тексты — как у хорошего бренда, не канцелярия. Никаких «Качественные услуги», \
«Индивидуальный подход», «Профессионализм». tagline — конкретное обещание, about — \
для кого, в каком формате, что получает клиент. cta_label — живой глагол по делу: \
«Записаться на стрижку», «Заказать к выходу», «Разобрать мою ситуацию».
- Весь текст — на русском, коротко. Без восклицательных знаков и эмодзи.
- Никаких обещаний, гарантий, лицензий, цифр и цен, которых нет в материалах владельца.
- Фото смотри как дизайнер: характер, палитра, свет. Поле hero_image не добавляй: \
приложение само безопасно подставит загруженное владельцем фото."""

# One of these is suggested per generation. Without it the model converges on the same
# "safe" answer for every coffee shop; with it, two coffee shops start from different
# ideas and still adapt them to their own brand.
_ART_DIRECTIONS = (
    "журнальная вёрстка: крупная антиква, много воздуха, тонкие линейки, фото в рамке",
    "швейцарский модернизм: строгий гротеск, сетка, один сочный акцент на нейтральном фоне",
    "ретро-плакат 70-х: тёплые насыщенные цвета, узкий капс, диагональные полосы",
    "ночной неон: глубокий тёмный фон, светящийся акцент, свечение в hero",
    "скандинавский минимализм: светлый тёплый фон, мягкие скругления, приглушённые цвета",
    "бумага и крафт: кремовый фон, зерно, книжная антиква, землистые оттенки",
    "дерзкий стрит: огромная типографика, бегущая строка, контрастная пара цветов",
    "пастельный уход: нежная пастель, округлый шрифт, мягкие плитки",
    "премиум-бутик: инверсия (тёмный hero), золото или бронза, изящная антиква",
    "свежий маркет: яркий природный акцент, фото крупно, плотная сетка карточек",
    "технологичный: широкий гротеск, сетка-фактура, холодная палитра с одним тёплым акцентом",
    "домашний и тёплый: терракота и сливки, мягкий гротеск, дружелюбные формулировки",
)

_EDIT_SYSTEM_PROMPT = """Ты редактируешь JSON-конфигурацию сервиса AIRuntime в мессенджере MAX.

Тебе дают текущую конфигурацию и просьбу пользователя её изменить. Верни СТРОГО один \
JSON-объект той же схемы — полную обновлённую конфигурацию, без markdown и пояснений.

Меняй только то, о чём попросил пользователь. Всё остальное оставь ровно как было, \
включая формулировки, цены, дизайн (palette, accent, heading_font, hero_style, hero_tone, \
pattern, card_style, nav_style, radius_style, density, marquee, highlights), comment_hint \
и порядок позиций. Если просят поменять стиль, цвета или шрифт — меняй дизайн-поля \
смело и согласованно, как арт-директор; контраст ink на bg в palette не ниже 7:1. \
Слоты, если их трогают, пиши в формате «Вт 23 сен, 16:00»."""


def _resolve_llm() -> tuple[str, str, str]:
    """Pick the provider/model/key for a short interactive call.

    The coding agent routes "openai" through the Codex CLI container, which is the right
    trade-off for writing a repository and the wrong one for a chat wizard that has to
    answer in seconds. Here we always take the plain HTTP path, mapping "openai" onto the
    OpenAI-compatible proxy when one is configured so the key matches the endpoint.

    Swapping the endpoint means swapping the model name with it: the two speak different
    catalogues, and a Codex model id sent to the proxy comes back as HTTP 400 `Model not
    found`. That failure used to be invisible - see ``_complete``.
    """
    provider, model = resolve_provider_and_model()
    api_key = resolve_platform_api_key(provider) or ""
    wire_provider = provider
    if provider == "openai" and (settings.openai_base_url or "").strip():
        wire_provider = "routerai"
        model = (settings.max_wizard_model or "").strip() or model
    return wire_provider, model, api_key


async def _complete(
    system_prompt: str, user_text: str, *, images: list[ImageAttachment] | None = None
) -> str:
    wire_provider, model, api_key = _resolve_llm()
    if not api_key:
        raise LlmUnavailable("No LLM provider is configured")

    provider = get_agent_provider(wire_provider)
    # Generous on purpose: a site brief with categories and a photo URL per position is
    # several thousand characters, and cutting it drops the owner's own notes at the end.
    messages = provider.build_messages([], user_text[:16000], images=images or ())
    collected = ""
    failure = ""
    async for event in provider.stream_turn(
        system_prompt=system_prompt,
        messages=messages,
        tools=[],
        model=model,
        api_key=api_key,
    ):
        if isinstance(event, TextDelta):
            collected += event.text
        elif isinstance(event, TurnFinished) and event.stop_reason == "error":
            failure = event.error or "provider reported an error"
    # A provider adapter reports a failed turn as an event, not an exception, so collecting
    # only TextDelta turns "the model is misconfigured" into "the model said nothing" - and
    # the caller's fallback then quietly serves "Услуга 1, Услуга 2" as if that were the
    # answer. This is the one place that can tell the difference, so it raises.
    if failure:
        raise RuntimeError(f"{wire_provider}/{model}: {failure}")
    return collected


def _extract_json(raw: str) -> dict:
    """Pull the JSON object out of a reply that may still be wrapped in prose or fences."""
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object in model reply")
        text = match.group(0)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Model reply is not a JSON object")
    return payload


_MENU_TERMS = (
    "меню",
    "кафе",
    "ресторан",
    "кофейн",
    "доставк",
    "пицц",
    "суши",
    # With the space: a bare "бар" also matches "барбершоп" and turned barbers into menus.
    "бар ",
    "коктейл",
    "пекарн",
    "кофемани",
)
_LANDING_TERMS = ("лендинг", "landing", "заявк", "консультац", "презентац", "курс", "вебинар")
_TUTOR_TERMS = ("репетитор", "урок", "егэ", "огэ", "занят", "математик", "английск", "физик")

_SKIP_TITLE_PREFIXES = (
    "описание владельца",
    "сайт владельца",
    "текст страницы",
    "цвета с сайта",
    "позиции с сайта",
    "что написал",
    "описание:",
)

_GRAY_ACCENTS = {
    "#fff",
    "#ffffff",
    "#000",
    "#000000",
    "#212121",
    "#a0a1a4",
    "#f5f5f5",
    "#f3f4f5",
    "#b0b3b6",
    "#2e7cf6",
}

_PRICED_LINE = re.compile(
    r"^\s*[-•*]?\s*(.+?)\s*[—–\-:]\s*(\d{2,7})\s*(?:₽|руб(?:\.|лей|ля)?)?\s*$",
    re.M,
)
_PRICED_INLINE = re.compile(
    r"([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9&«»\"'(). -]{1,40}?)\s+(\d{2,6})(?:\s*(?:₽|руб))?"
)
_SOURCE_ITEM = re.compile(r"^\s*-\s*\[([^]]+)]\s*(.+?)\s*[—–-]\s*\d[\d ]*\s*₽\s*$")
_SOURCE_PHOTO = re.compile(r"^\s*Фото:\s*(https://\S+)\s*$")


def _fallback_mood(prompt: str, kind: str) -> str:
    lowered = (prompt or "").lower()
    if any(term in lowered for term in _TUTOR_TERMS):
        return "calm"
    if any(term in lowered for term in _MENU_TERMS):
        return "warm"
    if kind == "landing":
        return "minimal"
    return "bold"


def _fallback_design(prompt: str, kind: str, mood: str) -> tuple[str, str, str]:
    lowered = (prompt or "").lower()
    scheme = (
        "dark" if re.search(r"т[её]мн(?:ая|ый|ое|ую)|dark\s*(?:mode|theme)", lowered) else "light"
    )
    if kind == "menu":
        return "cards", scheme, "serif" if mood == "warm" else "sans"
    if any(word in lowered for word in ("премиум", "люкс", "бутик", "авторск", "галере")):
        return "editorial", scheme, "serif"
    if any(word in lowered for word in ("ярк", "дерзк", "фестиваль", "концерт", "спорт", "барбер")):
        return "poster", scheme, "display"
    if mood in {"calm", "minimal"}:
        return "editorial", scheme, "serif" if mood == "calm" else "sans"
    return "classic", scheme, "sans"


def _fallback_title(prompt: str) -> str:
    brand = ""
    owner_line = ""
    page_name = ""
    for raw in (prompt or "").splitlines():
        stripped = raw.strip().strip(":")
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("бренд:"):
            brand = stripped.split(":", 1)[-1].strip()
            continue
        if lower.startswith("название страницы"):
            page_name = stripped.split(":", 1)[-1].split("—")[0].split(" - ")[0].strip()
            continue
        if any(lower.startswith(prefix) for prefix in _SKIP_TITLE_PREFIXES):
            continue
        if not owner_line:
            owner_line = stripped.split(".")[0].strip()
    title = owner_line or brand or page_name or "Мой сервис"
    if title.lower() in _SKIP_TITLE_PREFIXES or title.endswith(":"):
        title = brand or page_name or "Мой сервис"
    return title[:60]


def _fallback_accent(prompt: str) -> str | None:
    for color in re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", prompt or ""):
        if color.lower() in _GRAY_ACCENTS:
            continue
        return color
    return None


def _parse_priced_items(prompt: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(name: str, price: int) -> None:
        title = re.sub(r"\s+", " ", name).strip(" .,;:—-–")
        if len(title) < 2 or title.lower() in seen:
            return
        if title.lower() in {"заказ", "меню", "сайт", "с", "до", "позиции с сайта"}:
            return
        if price < 40 or price > 500_000:
            return
        seen.add(title.lower())
        found.append({"title": title[:120], "price_rub": price})

    for match in _PRICED_LINE.finditer(prompt or ""):
        add(match.group(1), int(match.group(2)))
    if found:
        return found[:8]

    for match in _PRICED_INLINE.finditer(prompt or ""):
        preceding = (prompt or "")[max(0, match.start() - 3) : match.start()].lower()
        if preceding.endswith("с ") or preceding.endswith("до "):
            continue
        add(match.group(1), int(match.group(2)))
    return found[:8]


# Without a model there is no art direction, but a keyword-drafted storefront still should
# not look like a form. One considered composition per register.
_FALLBACK_LOOK: dict[str, dict[str, object]] = {
    "menu": {
        "heading_font": "cormorant",
        "hero_style": "typographic",
        "hero_tone": "accent",
        "pattern": "grain",
        "card_style": "menu",
        "nav_style": "pills",
    },
    "calm": {
        "heading_font": "lora",
        "hero_style": "editorial",
        "hero_tone": "tint",
        "card_style": "numbered",
        "density": "airy",
    },
    "minimal": {
        "heading_font": "playfair",
        "hero_style": "typographic",
        "hero_tone": "ink",
        "pattern": "rings",
        "card_style": "minimal",
    },
    "bold": {
        "heading_font": "oswald",
        "hero_style": "typographic",
        "hero_tone": "accent",
        "pattern": "stripes",
        "card_style": "menu",
        "radius_style": "sharp",
    },
    "warm": {
        "heading_font": "comfortaa",
        "hero_style": "split",
        "hero_tone": "accent",
        "pattern": "glow",
        "card_style": "minimal",
        "radius_style": "round",
    },
}


def _fallback_config(prompt: str) -> ServiceConfig:
    """A usable draft when the model is unavailable - the wizard must never dead-end."""
    lowered = (prompt or "").lower()
    priced = _parse_priced_items(prompt)
    if any(term in lowered for term in _MENU_TERMS):
        kind = "menu"
        items = priced or [{"title": "Кофе"}, {"title": "Выпечка"}]
    elif any(term in lowered for term in _LANDING_TERMS):
        kind = "landing"
        items = priced or [{"title": "Консультация"}]
    elif any(term in lowered for term in _TUTOR_TERMS):
        kind = "booking"
        items = priced or [{"title": "Занятие"}]
    else:
        kind = "booking"
        items = priced or [{"title": "Услуга"}]

    title = _fallback_title(prompt)
    tagline = ""
    for raw in (prompt or "").splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("описание:") and "заполните" not in stripped.lower():
            tagline = stripped.split(":", 1)[-1].strip()[:160]
            break
    mood = _fallback_mood(prompt, kind)
    layout, color_scheme, heading_style = _fallback_design(prompt, kind, mood)
    payload: dict[str, object] = {
        "kind": kind,
        "title": title,
        "tagline": tagline,
        "about": "",
        "mood": mood,
        "layout": layout,
        "color_scheme": color_scheme,
        "heading_style": heading_style,
        **_FALLBACK_LOOK.get(kind if kind == "menu" else mood, {}),
        "items": items,
        "allow_multiple_items": kind == "menu",
        "slots": fallback_slots() if kind == "booking" else [],
    }
    accent = _fallback_accent(prompt)
    if accent:
        payload["accent"] = accent
    return normalise(ServiceConfig.model_validate(payload))


async def _config_from_model(
    system_prompt: str,
    user_text: str,
    *,
    what: str,
    images: list[ImageAttachment] | None = None,
) -> ServiceConfig | None:
    """One storefront out of the model, or None once the attempts are used up.

    The retry is not politeness about a flaky network. The proxy in front of the model
    offers it a tool set we never asked for (we send no ``tools`` at all), and the model
    answers a measured one turn in three by calling ``bash`` instead of writing anything -
    a 200 with no text in it. ``tool_choice: "none"`` reduces that and does not remove it,
    so the only thing between an owner's real prices and a stub reading "Услуга 1" is
    asking again.
    """
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            raw = await _complete(system_prompt, user_text, images=images)
            return normalise(ServiceConfig.model_validate(_extract_json(raw)))
        except LlmUnavailable:
            logger.warning("%s: no model configured, not retrying", what, exc_info=True)
            return None
        except Exception:
            logger.warning("%s attempt %s/%s failed", what, attempt, _ATTEMPTS, exc_info=True)
    return None


def _dated_prompt(prompt: str, *, direction: str = "") -> str:
    art = f"Арт-направление этой генерации: {direction}.\n" if direction else ""
    return (
        f"Сегодня: {russian_today(now_msk())} (Москва). "
        "Слоты пиши только конкретными днями в формате «Вт 23 сен, 16:00».\n"
        f"{art}\n"
        f"{prompt}"
    )


def _catalog_key(title: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", title.casefold()).strip()


def _source_catalog(prompt: str) -> dict[str, tuple[str, str]]:
    """Read the exact category/photo pairs extracted from the owner's site.

    The model decides composition and copy. Asset provenance is deterministic: it cannot
    invent a tracking URL, and it cannot accidentally lose the photos we already found.
    """
    result: dict[str, tuple[str, str]] = {}
    pending: tuple[str, str] | None = None
    for line in (prompt or "").splitlines():
        item_match = _SOURCE_ITEM.match(line)
        if item_match:
            pending = (item_match.group(2).strip(), item_match.group(1).strip())
            result[_catalog_key(pending[0])] = (pending[1], "")
            continue
        photo_match = _SOURCE_PHOTO.match(line)
        if photo_match and pending:
            title, category = pending
            result[_catalog_key(title)] = (category, photo_match.group(1))
    return result


def _enrich_from_source(config: ServiceConfig, prompt: str) -> ServiceConfig:
    source = _source_catalog(prompt)
    if not source:
        # With no verified source list, remote assets from a model reply are untrusted.
        items = [item.model_copy(update={"image_url": ""}) for item in config.items]
        return config.model_copy(update={"items": items})
    items = []
    for item in config.items:
        match = source.get(_catalog_key(item.title))
        if match:
            category, image_url = match
            items.append(
                item.model_copy(
                    update={
                        "category": item.category or category,
                        "image_url": image_url,
                    }
                )
            )
        else:
            items.append(item.model_copy(update={"image_url": ""}))
    return config.model_copy(update={"items": items})


async def generate_config(
    prompt: str, *, images: list[ImageAttachment] | None = None
) -> tuple[ServiceConfig, bool]:
    """Return ``(config, used_llm)`` for a fresh storefront."""
    direction = random.choice(_ART_DIRECTIONS)
    config = await _config_from_model(
        _SYSTEM_PROMPT,
        _dated_prompt(prompt, direction=direction),
        what="max_generate_config",
        images=images,
    )
    if config is None:
        return _fallback_config(prompt), False
    return _enrich_from_source(config, prompt), True


async def apply_edit(config: ServiceConfig, instruction: str) -> tuple[ServiceConfig, bool]:
    """Return ``(config, changed)`` after applying a free-form edit request."""
    # Inline images can be close to a megabyte. Sending one back to the text model would
    # consume the whole prompt window and truncate the owner's actual edit instruction.
    # It is immutable application data, so keep it out of the turn and restore it after.
    current = json.dumps(config.model_dump(exclude={"hero_image"}), ensure_ascii=False)
    user_text = f"Текущая конфигурация:\n{current}\n\nПросьба пользователя:\n{instruction}"
    updated = await _config_from_model(_EDIT_SYSTEM_PROMPT, user_text, what="max_apply_edit")
    if updated is None:
        return config, False
    trusted_images = {item.image_url for item in config.items if item.image_url}
    items = [
        item if item.image_url in trusted_images else item.model_copy(update={"image_url": ""})
        for item in updated.items
    ]
    return updated.model_copy(
        update={
            "hero_image": config.hero_image,
            "items": items,
            # A visual/content edit must not silently change checkout semantics selected
            # explicitly by the owner in the cabinet.
            "allow_multiple_items": config.allow_multiple_items,
        }
    ), True
