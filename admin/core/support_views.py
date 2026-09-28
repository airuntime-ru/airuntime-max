from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from django.conf import settings as django_settings
from django.db import IntegrityError
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.utils import timezone
from django.views import View

from core.models import AppUser


def _django_staff_email(request) -> str:
    return (getattr(request.user, "email", None) or "").strip().lower()


def _provision_support_operator(*, email: str) -> AppUser | None:
    """Ensure a platform users row exists with role=admin for the Django staff operator."""
    now = timezone.now()
    row = AppUser.objects.filter(email__iexact=email).first()
    if row is None:
        try:
            return AppUser.objects.create(
                id=uuid.uuid4(),
                email=email,
                password_hash=None,
                is_verified=True,
                role="admin",
                credits_balance=0,
                onboarding_completed=True,
                is_banned=False,
                banned_reason=None,
                plan_id=None,
                billing_period_start=None,
                billing_period_end=None,
                low_credits_notified_at=None,
                period_ending_notified_at=None,
                created_at=now,
                updated_at=now,
            )
        except IntegrityError:
            row = AppUser.objects.filter(email__iexact=email).first()
            if row is None:
                return None

    update_fields: list[str] = []
    if row.role != "admin":
        row.role = "admin"
        update_fields.append("role")
    if not row.is_verified:
        row.is_verified = True
        update_fields.append("is_verified")
    if update_fields:
        row.updated_at = now
        update_fields.append("updated_at")
        row.save(update_fields=update_fields)
    return row


class SupportChatBridgeView(View):
    """Redirect staff to the frontend support inbox with a short-lived JWT."""

    def get(self, request, user_id: str | None = None):
        if not request.user.is_authenticated or not request.user.is_staff:
            return HttpResponseForbidden("Нужны права staff.")
        email = _django_staff_email(request)
        if not email:
            return HttpResponseForbidden(
                "У учётной записи Django не заполнен email — укажите его в разделе AUTH."
            )
        operator = _provision_support_operator(email=email)
        if operator is None:
            return HttpResponseForbidden("Не удалось создать профиль оператора в users.")

        jwt_secret = os.getenv("JWT_SECRET_KEY", django_settings.SECRET_KEY)
        exp = datetime.now(UTC) + timedelta(minutes=120)
        token = jwt.encode(
            {
                "sub": str(operator.id),
                "role": "admin",
                "type": "access",
                "exp": exp,
            },
            jwt_secret,
            algorithm="HS256",
        )
        if isinstance(token, bytes):
            token = token.decode("utf-8")

        frontend = os.getenv("FRONTEND_URL", "https://airuntime.ru").rstrip("/")
        url = f"{frontend}/app/support-chat?bridge_token={quote(token, safe='')}"
        if user_id:
            url += f"&customer_user_id={quote(str(user_id), safe='')}"
        return HttpResponseRedirect(url)
