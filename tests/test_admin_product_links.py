"""Unit tests for admin/domain/product_links.py (no Django/DB)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ADMIN_DIR = Path(__file__).resolve().parents[1] / "admin"
if str(ADMIN_DIR) not in sys.path:
    sys.path.insert(0, str(ADMIN_DIR))

from domain.product_links import collect_product_links, link_display_text  # noqa: E402


def test_website_uses_deployment_url() -> None:
    obj = SimpleNamespace(
        type="website",
        deployment_url="https://bakery.airuntime.ru",
        deploy_subdomain="bakery",
        custom_domain=None,
        custom_domain_status="none",
        logs="",
    )
    assert collect_product_links(obj) == [("Сайт", "https://bakery.airuntime.ru")]


def test_bot_uses_tme_deployment_url() -> None:
    obj = SimpleNamespace(
        type="telegram_bot",
        deployment_url="https://t.me/airuntime_demo_bot",
        deploy_subdomain=None,
        logs="",
    )
    assert collect_product_links(obj) == [("Бот", "https://t.me/airuntime_demo_bot")]


def test_website_falls_back_to_subdomain() -> None:
    obj = SimpleNamespace(
        type="website",
        deployment_url=None,
        deploy_subdomain="vozduh",
        logs="",
    )
    assert collect_product_links(obj, domain="airuntime.ru") == [
        ("Сайт", "https://vozduh.airuntime.ru")
    ]


def test_mixed_adds_bot_from_logs() -> None:
    obj = SimpleNamespace(
        type="mixed",
        deployment_url="https://siz.airuntime.ru",
        deploy_subdomain="siz",
        logs="ready\nTelegram-бот доступен: https://t.me/siz_tracker_bot\n",
    )
    assert collect_product_links(obj) == [
        ("Сайт", "https://siz.airuntime.ru"),
        ("Бот", "https://t.me/siz_tracker_bot"),
    ]


def test_verified_custom_domain_is_listed() -> None:
    obj = SimpleNamespace(
        type="website",
        deployment_url="https://shop.airuntime.ru",
        deploy_subdomain="shop",
        custom_domain="example.com",
        custom_domain_status="verified",
        logs="",
    )
    links = collect_product_links(obj)
    assert ("Сайт", "https://shop.airuntime.ru") in links
    assert ("Домен", "https://example.com") in links


def test_link_display_text_strips_scheme() -> None:
    assert link_display_text("https://t.me/demo_bot") == "t.me/demo_bot"
