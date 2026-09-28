"""Transactional email templates for AIRuntime.

Public builders return EmailContent(subject, plain, html). HTML uses Jinja2
table layouts with autoescape; plain-text counterparts are hand-written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.core.config import settings
from src.services.email_templates.render import render_email

__all__ = [
    "EmailContent",
    "LOGO_CID",
    "login_code_email",
    "verify_email",
    "password_reset_email",
    "low_credits_email",
    "credits_exhausted_email",
    "period_ending_email",
    "period_renewed_email",
    "invoice_created_email",
    "invoice_paid_email",
    "credits_topup_paid_email",
    "plan_request_created_email",
    "plan_request_approved_email",
    "plan_request_rejected_email",
    "project_deployed_email",
    "deploy_failed_email",
    "list_template_previews",
]

LOGO_CID = "airuntime-logo"


@dataclass(frozen=True)
class EmailContent:
    subject: str
    plain: str
    html: str


def _content(name: str, *, subject: str, use_cid: bool = True, **context: object) -> EmailContent:
    plain, html = render_email(name, subject=subject, use_cid=use_cid, **context)
    return EmailContent(subject=subject, plain=plain, html=html)


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _fmt_rub(credits: int) -> str:
    """Rubles are the unit the cabinet shows, so balance emails lead with them.

    Mirrors ``billing.usage_credits_to_rub`` without importing it - ``services.billing``
    imports this module, so the dependency only goes one way.
    """
    value = Decimal(abs(credits)) / Decimal(settings.billing_credits_per_rub)
    quantized = value.quantize(Decimal("0.01"))
    whole = quantized.to_integral_value()
    text = _fmt_int(int(whole)) if quantized == whole else f"{quantized:.2f}".replace(".", ",")
    return f"{text} ₽"


def _fmt_balance(credits: int) -> str:
    """`12 ₽ (1 200 кредитов)` - the money first, the internal unit in brackets."""
    return f"{_fmt_rub(credits)} ({_fmt_int(credits)} кредитов)"


def login_code_email(*, code: str, minutes: int) -> EmailContent:
    return _content(
        "login_code",
        subject="Код входа в AIRuntime",
        preheader=f"Код для входа в AIRuntime: {code}",
        title="Код для входа",
        code=code,
        minutes=minutes,
        reason="Вы получили это письмо, потому что был запрошен вход в AIRuntime.",
    )


def verify_email(*, verify_url: str) -> EmailContent:
    return _content(
        "verify_email",
        subject="Подтвердите почту в AIRuntime",
        preheader="Подтвердите адрес почты, чтобы завершить регистрацию.",
        title="Подтверждение почты",
        verify_url=verify_url,
        link_ttl="24 часа",
        reason="Вы получили это письмо, потому что зарегистрировали аккаунт в AIRuntime.",
    )


def password_reset_email(*, reset_url: str) -> EmailContent:
    return _content(
        "password_reset",
        subject="Сброс пароля AIRuntime",
        preheader="Ссылка для безопасного сброса пароля в AIRuntime.",
        title="Сброс пароля",
        reset_url=reset_url,
        link_ttl="15 минут",
        reason="Вы получили это письмо, потому что был запрошен сброс пароля.",
    )


def low_credits_email(*, credits_balance: int, billing_url: str | None = None) -> EmailContent:
    label = _fmt_int(credits_balance)
    rub_label = _fmt_rub(credits_balance)
    return _content(
        "low_credits",
        subject="Баланс почти закончился",
        preheader=f"На балансе осталось {rub_label}.",
        title="Низкий баланс",
        credits_balance=credits_balance,
        credits_balance_label=label,
        balance_label=_fmt_balance(credits_balance),
        rub_balance_label=rub_label,
        summary_rows=[{"label": "Текущий остаток", "value": _fmt_balance(credits_balance)}],
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что баланс кредитов опустился ниже порога.",
    )


def credits_exhausted_email(*, billing_url: str | None = None) -> EmailContent:
    return _content(
        "credits_exhausted",
        subject="Баланс закончился",
        preheader="Баланс исчерпан — пополните его, чтобы продолжить работу с агентом.",
        title="Баланс закончился",
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что баланс достиг нуля.",
    )


def period_ending_email(
    *,
    period_end: datetime,
    plan_name: str | None = None,
    credits_balance: int | None = None,
    billing_url: str | None = None,
) -> EmailContent:
    date_str = period_end.strftime("%d.%m.%Y")
    rows = [{"label": "Дата окончания", "value": date_str}]
    if plan_name:
        rows.append({"label": "Тариф", "value": plan_name})
    if credits_balance is not None:
        rows.append({"label": "Текущий остаток", "value": _fmt_balance(credits_balance)})
    return _content(
        "period_ending",
        subject="Тарифный период скоро закончится",
        preheader=f"Тарифный период заканчивается {date_str}.",
        title="Скоро конец периода",
        period_end_label=date_str,
        plan_name=plan_name,
        credits_balance=credits_balance,
        balance_label=_fmt_balance(credits_balance) if credits_balance is not None else None,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что у вашего тарифа скоро закончится период.",
    )


def period_renewed_email(
    *,
    plan_name: str,
    credits: int,
    period_end: datetime | None = None,
) -> EmailContent:
    credits_label = _fmt_int(credits)
    period_end_label = period_end.strftime("%d.%m.%Y") if period_end else None
    rows = [
        {"label": "Тариф", "value": plan_name},
        {"label": "Начислено", "value": _fmt_balance(credits)},
    ]
    if period_end_label:
        rows.append({"label": "Новый период до", "value": period_end_label})
    return _content(
        "period_renewed",
        subject="Тарифный период обновлён",
        preheader=f"Тариф «{plan_name}» продлён. Начислено {credits_label} кредитов.",
        title="Тариф продлён",
        plan_name=plan_name,
        credits=credits,
        credits_label=credits_label,
        granted_label=_fmt_balance(credits),
        period_end_label=period_end_label,
        summary_rows=rows,
        reason="Вы получили это письмо, потому что тарифный период был автоматически обновлён.",
    )


def invoice_created_email(
    *,
    invoice_id: str,
    credits: int,
    amount_rub: int,
    created_at: datetime | None = None,
    billing_url: str | None = None,
) -> EmailContent:
    short_id = invoice_id.replace("-", "")[:8].upper()
    credits_label = _fmt_int(credits)
    created_label = created_at.strftime("%d.%m.%Y %H:%M") if created_at else None
    rows = [
        {"label": "Номер счёта", "value": short_id},
        {"label": "Сумма", "value": f"{amount_rub} ₽"},
        {"label": "К зачислению", "value": f"{credits_label} кредитов"},
    ]
    if created_label:
        rows.append({"label": "Создан", "value": created_label})
    return _content(
        "invoice_created",
        subject=f"Счёт {short_id} создан",
        preheader=f"Счёт на {amount_rub} ₽ создан. Ожидает оплаты.",
        title="Счёт создан",
        invoice_number=short_id,
        credits=credits,
        credits_label=credits_label,
        amount_rub=amount_rub,
        created_label=created_label,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что создали счёт на пополнение кредитов.",
    )


def invoice_paid_email(
    *,
    credits: int,
    amount_rub: int,
    new_balance: int | None = None,
    billing_url: str | None = None,
) -> EmailContent:
    credits_label = _fmt_int(credits)
    new_balance_label = _fmt_balance(new_balance) if new_balance is not None else None
    rows = [
        {"label": "Оплачено", "value": f"{amount_rub} ₽"},
        {"label": "Зачислено на баланс", "value": _fmt_balance(credits)},
    ]
    if new_balance_label:
        rows.append({"label": "Новый баланс", "value": new_balance_label})
    return _content(
        "invoice_paid",
        subject="Счёт оплачен — баланс пополнен",
        preheader=f"Платёж на {amount_rub} ₽ подтверждён. Начислено {credits_label} кредитов.",
        title="Счёт оплачен",
        credits=credits,
        credits_label=credits_label,
        granted_label=_fmt_balance(credits),
        amount_rub=amount_rub,
        new_balance=new_balance,
        new_balance_label=new_balance_label,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что оплата счёта на пополнение подтверждена.",
    )


# Backward-compatible alias used by billing maintenance.
def credits_topup_paid_email(
    *, credits: int, amount_rub: int, new_balance: int | None = None
) -> EmailContent:
    return invoice_paid_email(credits=credits, amount_rub=amount_rub, new_balance=new_balance)


def plan_request_created_email(
    *,
    plan_name: str,
    price_rub: int,
    budget_rub: int,
    billing_url: str | None = None,
) -> EmailContent:
    rows = [
        {"label": "Тариф", "value": plan_name},
        {"label": "Стоимость", "value": f"{price_rub} ₽/мес"},
        {"label": "Бюджет в тарифе", "value": f"{budget_rub} ₽/мес"},
        {"label": "Статус", "value": "На рассмотрении"},
    ]
    return _content(
        "plan_request_created",
        subject="Заявка на смену тарифа принята",
        preheader=f"Заявка на тариф «{plan_name}» принята и ожидает подтверждения.",
        title="Заявка принята",
        plan_name=plan_name,
        price_rub=price_rub,
        budget_rub=budget_rub,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что подали заявку на смену тарифа.",
    )


def plan_request_approved_email(
    *,
    plan_name: str,
    budget_rub: int,
    credits: int,
    period_end: datetime | None = None,
    billing_url: str | None = None,
) -> EmailContent:
    credits_label = _fmt_int(credits)
    period_end_label = period_end.strftime("%d.%m.%Y") if period_end else None
    rows = [
        {"label": "Тариф", "value": plan_name},
        {"label": "Начислено", "value": f"{budget_rub} ₽ ({credits_label} кредитов)"},
    ]
    if period_end_label:
        rows.append({"label": "Период до", "value": period_end_label})
    return _content(
        "plan_request_approved",
        subject=f"Тариф «{plan_name}» подключён",
        preheader=f"Тариф «{plan_name}» подключён. Бюджет {budget_rub} ₽ уже на балансе.",
        title="Тариф подключён",
        plan_name=plan_name,
        budget_rub=budget_rub,
        credits=credits,
        credits_label=credits_label,
        period_end_label=period_end_label,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что ваша заявка на смену тарифа подтверждена.",
    )


def plan_request_rejected_email(
    *,
    plan_name: str,
    admin_note: str | None = None,
    billing_url: str | None = None,
) -> EmailContent:
    rows = [
        {"label": "Тариф", "value": plan_name},
        {"label": "Статус", "value": "Отклонена"},
    ]
    if admin_note:
        rows.append({"label": "Причина", "value": admin_note, "block": True})
    return _content(
        "plan_request_rejected",
        subject="Заявка на смену тарифа отклонена",
        preheader=f"Заявка на тариф «{plan_name}» отклонена.",
        title="Заявка отклонена",
        plan_name=plan_name,
        admin_note=admin_note,
        summary_rows=rows,
        billing_url=billing_url,
        reason="Вы получили это письмо, потому что подавали заявку на смену тарифа.",
    )


def project_deployed_email(
    *,
    project_name: str,
    project_url: str | None = None,
    open_url: str | None = None,
) -> EmailContent:
    return _content(
        "project_deployed",
        subject=f"Проект «{project_name}» развёрнут",
        preheader=f"Проект «{project_name}» успешно развёрнут.",
        title="Проект развёрнут",
        project_name=project_name,
        project_url=project_url,
        open_url=open_url or project_url,
        reason="Вы получили это письмо, потому что деплой вашего проекта завершился успешно.",
    )


def deploy_failed_email(
    *,
    project_name: str,
    failed_at: datetime | None = None,
    check_url: str | None = None,
    summary: str | None = None,
) -> EmailContent:
    failed_label = (failed_at or datetime.now()).strftime("%d.%m.%Y %H:%M")
    safe_summary = (
        summary or "Деплой завершился с ошибкой. Откройте проект, чтобы посмотреть детали."
    ).strip()
    if len(safe_summary) > 280:
        safe_summary = safe_summary[:277] + "…"
    return _content(
        "deploy_failed",
        subject=f"Ошибка деплоя: {project_name}",
        preheader=f"Деплой проекта «{project_name}» завершился с ошибкой.",
        title="Деплой не удался",
        project_name=project_name,
        failed_label=failed_label,
        check_url=check_url,
        summary=safe_summary,
        summary_rows=[
            {"label": "Время", "value": failed_label},
            {"label": "Что произошло", "value": safe_summary, "block": True},
        ],
        reason="Вы получили это письмо, потому что деплой вашего проекта завершился с ошибкой.",
    )


def list_template_previews(*, use_cid: bool = False) -> list[dict[str, object]]:
    """Sample payloads for local preview / snapshot tests (no real send)."""
    from src.services.email_templates.render import logo_url

    now = datetime(2026, 7, 24, 12, 30)
    samples: list[tuple[str, EmailContent]] = [
        ("login_code", login_code_email(code="359547", minutes=10)),
        (
            "verify_email",
            verify_email(verify_url="https://airuntime.ru/auth/verify?token=preview-token"),
        ),
        (
            "password_reset",
            password_reset_email(reset_url="https://airuntime.ru/auth/reset?token=preview-token"),
        ),
        (
            "low_credits",
            low_credits_email(
                credits_balance=120,
                billing_url="https://airuntime.ru/app/settings",
            ),
        ),
        (
            "credits_exhausted",
            credits_exhausted_email(billing_url="https://airuntime.ru/app/settings"),
        ),
        (
            "period_ending",
            period_ending_email(
                period_end=now,
                plan_name="Starter",
                credits_balance=840,
                billing_url="https://airuntime.ru/app/settings",
            ),
        ),
        (
            "period_renewed",
            period_renewed_email(plan_name="Starter", credits=2000, period_end=now),
        ),
        (
            "plan_request_created",
            plan_request_created_email(
                plan_name="Про",
                price_rub=990,
                budget_rub=700,
                billing_url="https://airuntime.ru/app/profile",
            ),
        ),
        (
            "plan_request_approved",
            plan_request_approved_email(
                plan_name="Про",
                budget_rub=700,
                credits=70_000,
                period_end=now,
                billing_url="https://airuntime.ru/app/profile",
            ),
        ),
        (
            "plan_request_rejected",
            plan_request_rejected_email(
                plan_name="Про",
                admin_note="Оплата не поступила",
                billing_url="https://airuntime.ru/app/profile",
            ),
        ),
        (
            "invoice_created",
            invoice_created_email(
                invoice_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                credits=5000,
                amount_rub=50,
                created_at=now,
                billing_url="https://airuntime.ru/app/settings",
            ),
        ),
        (
            "invoice_paid",
            invoice_paid_email(
                credits=5000,
                amount_rub=50,
                new_balance=6120,
                billing_url="https://airuntime.ru/app/settings",
            ),
        ),
        (
            "project_deployed",
            project_deployed_email(
                project_name="Landing для студии",
                project_url="https://studio-landing.airuntime.ru",
                open_url="https://airuntime.ru/app/projects/preview-id",
            ),
        ),
        (
            "deploy_failed",
            deploy_failed_email(
                project_name="Landing для студии",
                failed_at=now,
                check_url="https://airuntime.ru/app/projects/preview-id/deployments",
                summary="Контейнер не прошёл проверку после старта. Откройте историю деплоев.",
            ),
        ),
    ]
    result: list[dict[str, object]] = []
    for template_id, content in samples:
        html = content.html
        if not use_cid:
            html = html.replace("cid:airuntime-logo", logo_url())
        result.append(
            {
                "id": template_id,
                "subject": content.subject,
                "plain": content.plain,
                "html": html,
            }
        )
    return result
