"""Screens for the product deck, rendered by the real frontend with a mocked API.

    cd frontend && npm run dev          # in another terminal
    python docs/product-presentation/capture-screens.py

Every /api/v1 call is answered in the browser (page.route), so no backend, database or
account is needed and nothing temporary goes into the product. Paths the mock does not
know are printed - a new page that calls a new endpoint shows up here first. All data is
demo data.
"""

import asyncio
import datetime as dt
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
BASE = "http://localhost:3000"
NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


def ago(**kw):
    return (NOW - dt.timedelta(**kw)).isoformat()


def project(pid, name, desc, status, sub=None, ptype="website"):
    url = f"https://{sub}.airuntime.ru" if sub else None
    return {
        "id": pid,
        "type": ptype,
        "name": name,
        "description": desc,
        "status": status,
        "logs": "",
        "deployment_url": url if status in ("deployed", "ready", "running") else None,
        "deploy_subdomain": sub,
        "blocked_reason": None,
        "planned_site_url": url,
        "git_history": "[]",
    }


P = "11111111-1111-4111-8111-111111111111"
PROJECTS = [
    project(P, "Кофейня «Зерно»", "Сайт кофейни с меню и предзаказом навынос", "deployed", "zerno"),
    project(
        "22222222-2222-4222-8222-222222222222",
        "Бот записи в барбершоп",
        "Telegram-бот: услуги, слоты, напоминания",
        "deployed",
        "blade-bot",
        "telegram_bot",
    ),
    project(
        "33333333-3333-4333-8333-333333333333",
        "Лендинг курса по Python",
        "Промо-страница с программой и формой заявки",
        "deployed",
        "python-course",
    ),
    project(
        "44444444-4444-4444-8444-444444444444",
        "CRM для студии йоги",
        "Абонементы, расписание, заявки — сайт и бот",
        "building",
        "yoga-crm",
    ),
    project(
        "55555555-5555-4555-8555-555555555555",
        "Портфолио фотографа",
        "Галерея, отзывы и запись на съёмку",
        "deployed",
        "lena-photo",
    ),
    project(
        "66666666-6666-4666-8666-666666666666",
        "Бот поддержки магазина",
        "Отвечает на вопросы и принимает заказы",
        "needs_configuration",
        None,
        "telegram_bot",
    ),
]

MESSAGES = [
    {
        "role": "user",
        "t": ago(minutes=42),
        "md": "Сделай сайт кофейни «Зерно»: меню с ценами, фото, адрес на Петроградке и предзаказ навынос. Стиль — тёплый, крафтовый.",
    },
    {
        "role": "assistant",
        "t": ago(minutes=41),
        "md": "Принял. План:\n\n1. **Структура** — главная, меню по категориям, контакты с картой\n2. **Предзаказ** — корзина, время готовности, заявка в Telegram владельцу\n3. **База** — PostgreSQL для заказов\n4. **Сборка и запуск** — Docker, HTTPS-домен\n\nНачинаю с каркаса и дизайн-системы.",
    },
    {
        "role": "assistant",
        "t": ago(minutes=31),
        "md": "- ✓ Каркас и меню готовы\n- ✓ Подключил PostgreSQL — креды созданы платформой, в код не попали\n- ✓ Docker build прошёл\n\nДля уведомлений о заказах нужен токен бота — **добавьте `TELEGRAM_BOT_TOKEN` в Секреты проекта**, значение мне не передаётся.",
    },
    {
        "role": "user",
        "t": ago(minutes=24),
        "md": "Добавил токен. И сделай кнопку «Позвонить» заметнее.",
    },
    {
        "role": "assistant",
        "t": ago(minutes=18),
        "md": "Готово. Кнопка «Позвонить» теперь в шапке и закреплена снизу на телефоне.\n\n- ✓ Build прошёл, проверил страницу в браузере\n- ✓ Версия v5 запущена: **https://zerno.airuntime.ru**\n\nМожно открыть и проверить предзаказ.",
    },
]

LOG = """#1 [build 1/6] FROM node:22-alpine
#2 [build 2/6] COPY package*.json ./
#3 [build 3/6] RUN npm ci                              12.4s
#4 [build 4/6] COPY . .
#5 [build 5/6] RUN npm run build                        21.7s
   ✓ Compiled successfully
   ✓ Generating static pages (7/7)
#6 [run] exporting to image                              1.1s
✓ image built    airuntime/zerno:v5
✓ container started   zerno-v5
✓ health check   200 OK  /  (142 ms)
✓ HTTPS connected   https://zerno.airuntime.ru"""


def routes(path, method):
    if path.startswith("/auth/me"):
        return {
            "id": "u1",
            "email": "demo@airuntime.ru",
            "is_verified": True,
            "credits_balance": 612000,
            "onboarding_completed": True,
        }
    if path.startswith("/projects/runtime-limits"):
        return {"running": 4, "max_running": 5, "total": 6, "max_total": 10}
    if path.startswith("/projects?") or path == "/projects":
        return {"items": PROJECTS, "total": len(PROJECTS), "deployed_total": 4}
    if path.startswith(f"/projects/{P}/chats/chat-1/messages"):
        return [
            {
                "id": f"m{i}",
                "role": m["role"],
                "content_markdown": m["md"],
                "created_at": m["t"],
                "attachments": [],
            }
            for i, m in enumerate(MESSAGES)
        ]
    if path.startswith(f"/projects/{P}/chats"):
        return [
            {
                "id": "chat-1",
                "project_id": P,
                "title": "Сайт кофейни",
                "created_at": ago(minutes=42),
                "updated_at": ago(minutes=18),
            }
        ]
    if path.startswith(f"/projects/{P}/deployments"):
        return {
            "total": 5,
            "items": [
                {
                    "id": f"d{v}",
                    "project_id": P,
                    "status": "completed" if v != 3 else "failed",
                    "image_ref": f"airuntime/zerno:v{v}",
                    "container_id": f"zerno-v{v}",
                    "logs_ref": None,
                    "error_text": "npm run build: Module not found — исправлено агентом в v4"
                    if v == 3
                    else None,
                    "log_text": LOG if v == 5 else "",
                    "started_at": ago(minutes=60 - v * 8),
                    "finished_at": ago(minutes=59 - v * 8),
                }
                for v in (5, 4, 3, 2, 1)
            ],
        }
    if path.startswith(f"/projects/{P}/versions"):
        msgs = [
            "Кнопка «Позвонить» в шапке и снизу на телефоне",
            "Уведомления о заказах в Telegram",
            "Предзаказ: корзина и время готовности",
            "Меню по категориям с фото",
            "Каркас сайта и дизайн-система",
        ]
        return [
            {
                "commit_hash": f"{'a3f9c1e7b2d4'[i:]}{i}e81c0d9f7",
                "created_at": ago(minutes=18 + i * 7),
                "message": m,
            }
            for i, m in enumerate(msgs)
        ]
    if path.startswith(f"/projects/{P}/secrets"):
        return [
            {
                "id": "s1",
                "key": "TELEGRAM_BOT_TOKEN",
                "reason": "Уведомления владельцу о новых заказах",
                "has_value": True,
                "created_at": ago(minutes=30),
            },
            {
                "id": "s2",
                "key": "OWNER_CHAT_ID",
                "reason": "Куда присылать заказы",
                "has_value": True,
                "created_at": ago(minutes=29),
            },
            {
                "id": "s3",
                "key": "YANDEX_MAPS_KEY",
                "reason": "Карта на странице контактов",
                "has_value": False,
                "created_at": ago(minutes=12),
            },
        ]
    if path.startswith(f"/projects/{P}/generation-usage"):
        return {
            "credits_spent": 88000,
            "cost_rub": 88,
            "input_tokens": 910000,
            "cached_input_tokens": 640000,
            "cache_write_input_tokens": 0,
            "output_tokens": 61000,
            "total_tokens": 971000,
            "generation_seconds": 1260,
            "runs_count": 5,
            "charge_events": 5,
        }
    if path.startswith(f"/projects/{P}/logs"):
        return {
            "project_logs": "",
            "deployment_logs": LOG,
            "runtime_logs": "GET / 200 142ms",
            "runtime_error": None,
            "deployment_status": "completed",
            "container_id": "zerno-v5",
            "logs_ref": None,
        }
    if path.startswith(f"/projects/{P}/domain"):
        return {"domain": None, "status": "none"}
    if path.startswith(f"/projects/{P}/telegram"):
        return {}
    if path.startswith(f"/projects/{P}"):
        return PROJECTS[0]
    if path.startswith("/billing/me"):
        return {
            "credits_balance": 612000,
            "balance_rub": 612,
            "billing_period_start": ago(days=6),
            "billing_period_end": ago(days=-24),
            "plan": None,
            "pending_plan_request": None,
            "robokassa_enabled": False,
        }
    if path.startswith("/billing/plans"):
        return []
    if path.startswith("/support/unread-count"):
        return {"unread": 0}
    if path.startswith("/providers"):
        return {"providers": [], "default_provider": "openai", "default_model": ""}
    return None


async def open_app(browser, width, height):
    ctx = await browser.new_context(
        viewport={"width": width, "height": height}, device_scale_factor=2
    )
    await ctx.add_init_script(
        "localStorage.setItem('airuntime_access_token','demo');"
        "localStorage.setItem('airuntime_refresh_token','demo');"
        "localStorage.setItem('airuntime-consent-analytics','denied');"
    )

    async def api(route):
        req = route.request
        path = req.url.split("/api/v1", 1)[-1]
        body = routes(path, req.method)
        if body is None:
            print("  unmocked:", req.method, path)
            return await route.fulfill(status=404, json={"detail": "not mocked"})
        return await route.fulfill(json=body)

    await ctx.route("**/api/v1/**", api)
    await ctx.route("**/mc.yandex.ru/**", lambda r: r.abort())
    return ctx


def shrink(path: Path) -> None:
    """The repository refuses files over 1000 KB; a palette PNG keeps UI shots crisp and small."""
    if path.stat().st_size > 900_000:
        im = Image.open(path).convert("RGB")
        im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(
            path, optimize=True
        )


HIDE = "nextjs-portal, [data-nextjs-toast] { display: none !important; }"


async def snap(page, url, name, wait="main", full=False, pause=1800):
    await page.goto(BASE + url)
    await page.wait_for_selector(wait, timeout=120_000)
    await page.add_style_tag(content=HIDE)
    await page.wait_for_timeout(pause)
    await page.screenshot(path=str(HERE / name), full_page=full)
    shrink(HERE / name)
    print("saved", name)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await open_app(browser, 1440, 900)
        page = await ctx.new_page()
        await snap(page, "/app", "shot-dashboard.png", wait="[data-tour='project-create-trigger']")
        await snap(page, f"/app/projects/{P}/chat", "shot-chat.png", wait="body", pause=6000)
        await page.goto(BASE + f"/app/projects/{P}/deployments")
        await page.wait_for_selector("text=zerno:v5", timeout=60_000)
        await page.add_style_tag(content=HIDE)
        await page.get_by_text("Развернуть лог").first.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(HERE / "shot-deploys.png"))
        print("saved shot-deploys.png")
        await snap(
            page, f"/app/projects/{P}/versions", "shot-versions.png", wait="body", pause=4000
        )
        await snap(page, f"/app/projects/{P}/secrets", "shot-secrets.png", wait="body", pause=4000)
        await ctx.close()

        ctx = await open_app(browser, 390, 844)
        page = await ctx.new_page()
        await snap(
            page, "/app", "shot-dashboard-mobile.png", wait="[data-tour='project-create-trigger']"
        )
        await snap(page, f"/app/projects/{P}/chat", "shot-chat-mobile.png", wait="body", pause=6000)
        await ctx.close()
        await browser.close()


asyncio.run(main())
