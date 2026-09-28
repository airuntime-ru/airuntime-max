"""Screens for the deck, rendered by the real mini-app code with a mocked API and bridge.

    cd frontend && npm run dev          # in another terminal
    python docs/max-presentation/capture-screens.py
    python docs/max-presentation/make-assets.py

The API and MAX Bridge are faked inside the browser (page.route + an init script), so
nothing temporary goes into the product code. Data is demo data (see slide 16).
"""

import asyncio
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from playwright.async_api import async_playwright  # noqa: E402

from src.services.max.schema import ServiceConfig, normalise  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE
BASE = "http://localhost:3000/max"

sys.path.insert(0, str(HERE))
from demo_storefronts import RAW  # noqa: E402

AUTO = {
    "kind": "booking",
    "title": "Автосервис на Лесной",
    "kicker": "Автосервис · Лесной пр., 12",
    "tagline": "Диагностика, масло и шиномонтаж — запишитесь на удобное время",
    "about": "Небольшой сервис у дома: посмотрим подвеску, поменяем масло и переобуем колёса.",
    "accent": "#FF6A1A",
    "palette": {"bg": "#101214", "surface": "#1A1D21", "ink": "#F2F3F5", "accent2": "#C2410C"},
    "heading_font": "unbounded",
    "hero_style": "typographic",
    "hero_tone": "accent",
    "pattern": "grid",
    "card_style": "numbered",
    "radius_style": "soft",
    "density": "balanced",
    "catalog_title": "Услуги и цены",
    "highlights": [
        {"value": "9–20", "label": "каждый день"},
        {"value": "от 900 ₽", "label": "замена масла"},
    ],
    "contacts": {"address": "Лесной пр., 12", "hours": "9:00–20:00", "phone": ""},
    "items": [
        {"title": "Диагностика подвески", "price_rub": 1500, "duration_min": 60},
        {"title": "Замена масла", "price_rub": 900, "duration_min": 30},
        {"title": "Шиномонтаж", "price_rub": 2400, "duration_min": 45},
    ],
    "slots": ["Чт 25 сен, 10:00", "Чт 25 сен, 14:00", "Пт 26 сен, 11:30", "Сб 27 сен, 12:00"],
    "cta_label": "Записаться в сервис",
    "comment_hint": "Марка, год, что случилось",
}


def cfg(raw):
    return normalise(ServiceConfig.model_validate(raw)).model_dump()


CONFIGS = {"auto": cfg(AUTO), **{k: cfg(v) for k, v in RAW.items()}}


def service(slug, new=0, total=0):
    return {
        "slug": slug,
        "status": "live",
        "config": CONFIGS[slug],
        "link": f"https://max.ru/t403_hakaton_max_bot?startapp={slug}",
        "new_leads": new,
        "lead_count": total,
    }


NOW = datetime.datetime(2026, 9, 24, 9, 40)
LEADS = [
    {
        "id": "l1",
        "service_title": "Автосервис на Лесной",
        "service_slug": "auto",
        "customer_name": "Пётр",
        "phone": "+7 999 000-11-22",
        "item_title": "Диагностика подвески",
        "slot_label": "Чт 25 сен, 14:00",
        "comment": "Mazda 6, стучит подвеска",
        "status": "new",
        "created_at": NOW.isoformat(),
        "scheduled_at": "2026-09-25T14:00:00",
        "chat_url": "https://max.ru/u/x",
    },
    {
        "id": "l2",
        "service_title": "Автосервис на Лесной",
        "service_slug": "auto",
        "customer_name": "Анна",
        "phone": "+7 911 222-33-44",
        "item_title": "Шиномонтаж",
        "slot_label": "Сб 27 сен, 12:00",
        "comment": "",
        "status": "confirmed",
        "created_at": NOW.isoformat(),
        "scheduled_at": "2026-09-27T12:00:00",
        "chat_url": "https://max.ru/u/y",
    },
]

BRIDGE = """
window.WebApp = {
  initData: "demo", platform: "ios",
  initDataUnsafe: { user: { id: 1, first_name: "Пётр", last_name: "Клиент" }, start_param: window.__slug || "" },
  requestContact: async () => ({ phone: "+79990001122", authDate: "1", hash: "x" }),
  HapticFeedback: {}, BackButton: {},
};
"""
HIDE_DEV = (
    "nextjs-portal, [data-nextjs-toast], [data-next-badge-root] { display: none !important; }"
)


async def open_page(browser, slug, state):
    ctx = await browser.new_context(viewport={"width": 390, "height": 780}, device_scale_factor=2)
    page = await ctx.new_page()
    await page.add_init_script(f"window.__slug = {json.dumps(slug)};" + BRIDGE)
    await page.route(
        "https://st.max.ru/**", lambda r: r.fulfill(body="", content_type="text/javascript")
    )

    async def api(route):
        url, method = route.request.url, route.request.method
        path = url.split("/api/v1", 1)[-1]
        if path.startswith("/max/miniapp/service/"):
            s = path.rsplit("/", 1)[-1]
            return await route.fulfill(
                json={"slug": s, "status": "live", "config": CONFIGS[s], "my_leads": []}
            )
        if path.startswith("/max/miniapp/owner/overview"):
            return await route.fulfill(
                json={"owner": {"name": "Дмитрий", "username": ""}, "services": state["services"]}
            )
        if path.startswith("/max/miniapp/owner/leads"):
            return await route.fulfill(json={"leads": state["leads"]})
        if path == "/max/miniapp/owner/services" and method == "POST":
            if state.get("hang"):
                await asyncio.sleep(3600)
            return await route.fulfill(status=201, json={**service("auto"), "used_llm": True})
        if path == "/max/miniapp/lead":
            return await route.fulfill(
                status=201, json={"id": "x", "status": "new", "success_message": ""}
            )
        return await route.fulfill(status=404, json={"detail": "nf"})

    await page.route("**/api/v1/**", api)
    await page.goto(BASE)
    await page.add_style_tag(content=HIDE_DEV)
    # The first load of a dev server compiles for a while; wait for content, not a clock.
    await page.wait_for_selector(".max-welcome, .max-cover, .max-hero", timeout=120_000)
    await page.wait_for_timeout(800)
    await page.add_style_tag(content=HIDE_DEV)
    return ctx, page


async def shot(page, name):
    await page.add_style_tag(content=HIDE_DEV)
    await page.wait_for_timeout(600)
    await page.screenshot(path=str(OUT / name))
    print("saved", name)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Owner: compose, building, ready.
        ctx, page = await open_page(browser, "", {"services": [], "leads": []})
        await page.get_by_role("button", name="Автосервис").click()
        await shot(page, "shot-owner-compose.png")
        await ctx.close()

        ctx, page = await open_page(browser, "", {"services": [], "leads": [], "hang": True})
        await page.get_by_role("button", name="Автосервис").click()
        await page.get_by_role("button", name="Собрать витрину").click()
        await page.wait_for_timeout(800)
        await page.evaluate("window.scrollTo(0, 0)")
        await shot(page, "shot-owner-building.png")
        await ctx.close()

        state = {"services": [], "leads": []}
        ctx, page = await open_page(browser, "", state)
        await page.get_by_role("button", name="Автосервис").click()
        state["services"] = [service("auto")]
        await page.get_by_role("button", name="Собрать витрину").click()
        await page.wait_for_timeout(1200)
        await page.evaluate("window.scrollTo(0, 0)")
        await shot(page, "shot-owner-ready.png")
        await ctx.close()

        # Owner: leads, and the lead card crop for the feedback slide.
        ctx, page = await open_page(
            browser, "", {"services": [service("auto", 1, 2)], "leads": LEADS}
        )
        await shot(page, "shot-owner-leads.png")
        card = page.locator(".max-lead-card").first
        await card.scroll_into_view_if_needed()
        await page.wait_for_timeout(300)
        await card.screenshot(path=str(OUT / "shot-owner-lead-card.png"))
        print("saved shot-owner-lead-card.png")
        await ctx.close()

        # Customer: storefront, booking, done.
        ctx, page = await open_page(browser, "auto", {"services": [], "leads": []})
        await shot(page, "shot-storefront.png")
        await page.locator(".max-option").nth(0).click()
        await page.locator(".max-chip").nth(1).click()
        await page.evaluate(
            "document.querySelector('.max-chips').scrollIntoView({block: 'start'}); window.scrollBy(0, -50)"
        )
        await shot(page, "shot-booking.png")
        await page.get_by_role("button", name="Взять номер из MAX").click()
        await page.wait_for_timeout(300)
        await page.locator(".max-submit-bar .max-button").click()
        await page.wait_for_timeout(900)
        await page.evaluate("window.scrollTo(0, 0)")
        await shot(page, "shot-customer-done.png")
        await ctx.close()

        # Gallery: five businesses, five looks.
        for slug in ("coffee", "barber", "tutor", "nails", "lawyer"):
            ctx, page = await open_page(browser, slug, {"services": [], "leads": []})
            await page.wait_for_timeout(1500)
            await shot(page, f"shot-gallery-{slug}.png")
            await ctx.close()

        await browser.close()


asyncio.run(main())
