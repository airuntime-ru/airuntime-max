from __future__ import annotations

import re
from datetime import datetime

import pytest

from src.services.email import _safe_header
from src.services.email_templates import (
    LOGO_CID,
    credits_exhausted_email,
    deploy_failed_email,
    invoice_created_email,
    invoice_paid_email,
    list_template_previews,
    login_code_email,
    low_credits_email,
    password_reset_email,
    period_ending_email,
    period_renewed_email,
    project_deployed_email,
    verify_email,
)


def test_login_code_email_contains_branding():
    content = login_code_email(code="482913", minutes=10)
    assert content.subject == "Код входа в AIRuntime"
    assert "482913" in content.plain
    assert "Код для входа: 482913" in content.plain
    assert f"cid:{LOGO_CID}" in content.html
    assert 'alt="AIRuntime"' in content.html
    assert 'content="light only"' in content.html
    assert "color-scheme: light only" in content.html
    assert 'class="email-masthead"' in content.html
    assert 'bgcolor="#FFFFFF"' in content.html
    # Deep-space masthead: the gradient is progressive enhancement, the bgcolor is
    # what Outlook actually paints, so the solid twin has to be there.
    assert 'bgcolor="#070C17"' in content.html
    assert "Код для входа" in content.html
    assert "10 минут" in content.html
    assert "preheader" not in content.html.lower() or "482913" in content.html
    assert "letter-spacing:10px" in content.html
    assert "Если вы не запрашивали вход" in content.html


def test_login_code_escapes_user_input():
    content = login_code_email(code="<b>hack</b>", minutes=10)
    assert "<b>hack</b>" not in content.html
    assert "&lt;b&gt;hack&lt;/b&gt;" in content.html


def test_verify_email_template():
    content = verify_email(verify_url="https://airuntime.ru/auth/verify?token=abc")
    assert "Подтвердите почту" in content.subject
    assert "https://airuntime.ru/auth/verify?token=abc" in content.plain
    assert "Подтвердить почту" in content.html
    assert f"cid:{LOGO_CID}" in content.html
    assert "24 часа" in content.html


def test_password_reset_template():
    content = password_reset_email(reset_url="https://airuntime.ru/auth/reset?token=xyz")
    assert "Сброс пароля" in content.subject
    assert "Сбросить пароль" in content.html
    assert "15 минут" in content.html
    assert f"cid:{LOGO_CID}" in content.html


def test_billing_and_deploy_templates_render():
    now = datetime(2026, 7, 24, 12, 0)
    templates = [
        low_credits_email(credits_balance=42, billing_url="https://airuntime.ru/app/settings"),
        credits_exhausted_email(billing_url="https://airuntime.ru/app/settings"),
        period_ending_email(period_end=now, plan_name="Starter", credits_balance=100),
        period_renewed_email(plan_name="Starter", credits=2000, period_end=now),
        invoice_created_email(
            invoice_id="11111111-2222-3333-4444-555555555555",
            credits=1000,
            amount_rub=10,
            created_at=now,
        ),
        invoice_paid_email(credits=1000, amount_rub=10, new_balance=1500),
        project_deployed_email(
            project_name="Очень длинное название проекта " * 3,
            project_url="https://example.airuntime.ru/" + ("path/" * 20),
        ),
        deploy_failed_email(
            project_name="Bot",
            failed_at=now,
            summary="<script>alert(1)</script> boom",
        ),
    ]
    for content in templates:
        assert content.subject
        assert content.plain.strip()
        assert "AIRuntime" in content.html
        assert "<script>" not in content.html
        assert "table" in content.html


def test_balance_emails_lead_with_rubles():
    """The cabinet shows rubles, so the letters must not speak only in credits."""
    low = low_credits_email(credits_balance=120, billing_url="https://airuntime.ru/app/profile")
    assert "1,20 ₽ (120 кредитов)" in low.html
    assert "1,20 ₽ (120 кредитов)" in low.plain

    paid = invoice_paid_email(credits=1000, amount_rub=10, new_balance=1500)
    assert "10 ₽ (1 000 кредитов)" in paid.html
    assert "15 ₽ (1 500 кредитов)" in paid.html
    assert "15 ₽ (1 500 кредитов)" in paid.plain

    renewed = period_renewed_email(plan_name="Про", credits=70_000)
    assert "700 ₽ (70 000 кредитов)" in renewed.html

    # An optional balance stays optional - no "None ₽" leaking into the letter.
    ending = period_ending_email(period_end=datetime(2026, 7, 24, 12, 0))
    assert "None" not in ending.plain
    assert "₽" not in ending.plain


def test_all_preview_templates_have_html_and_text():
    previews = list_template_previews(use_cid=True)
    assert len(previews) >= 11
    ids = {item["id"] for item in previews}
    assert "login_code" in ids
    assert "deploy_failed" in ids
    for item in previews:
        assert item["subject"]
        assert "AIRuntime" in item["plain"]
        assert "cid:airuntime-logo" in item["html"]
        assert re.search(r"max-width:\s*600px", item["html"])


def test_safe_header_blocks_injection():
    with pytest.raises(ValueError):
        _safe_header("Subject\r\nBcc: attacker@evil.test")
