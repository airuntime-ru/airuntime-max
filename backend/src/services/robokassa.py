"""Robokassa payment links and callback signature checks.

Docs: https://docs.robokassa.ru/ru/pay-interface
  * password #1 — payment form + Success URL
  * password #2 — Result URL
  * password #3 — XML API (not used on the payment form)
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from src.core.config import settings
from src.db.models.credit_topup import CreditTopUp

PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
_HASH_ALGORITHMS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


def format_out_sum(amount_rub: int) -> str:
    return str(amount_rub)


def amounts_match(out_sum: str, amount_rub: int) -> bool:
    try:
        paid = Decimal(out_sum.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        return False
    return paid == Decimal(amount_rub)


def _hash_hex(value: str) -> str:
    alg = (settings.robokassa_hash_algorithm or "md5").strip().lower()
    factory = _HASH_ALGORITHMS.get(alg)
    if factory is None:
        raise ValueError(f"Unsupported Robokassa hash algorithm: {alg}")
    return factory(value.encode("utf-8")).hexdigest()


def signatures_match(expected: str, actual: str | None) -> bool:
    received = (actual or "").strip()
    if not expected or not received or len(expected) != len(received):
        return False
    return hmac.compare_digest(expected.lower(), received.lower())


def _shp_tail(shp: dict[str, str] | None) -> str:
    if not shp:
        return ""
    parts = [f"{key}={shp[key]}" for key in sorted(shp)]
    return ":" + ":".join(parts)


def payment_signature(*, out_sum: str, inv_id: int, shp: dict[str, str] | None = None) -> str:
    login = settings.robokassa_merchant_login or ""
    password = settings.robokassa_password1 or ""
    base = f"{login}:{out_sum}:{inv_id}:{password}"
    return _hash_hex(base + _shp_tail(shp))


def result_signature(*, out_sum: str, inv_id: int, shp: dict[str, str] | None = None) -> str:
    password = settings.robokassa_password2 or ""
    base = f"{out_sum}:{inv_id}:{password}"
    return _hash_hex(base + _shp_tail(shp))


def success_signature(*, out_sum: str, inv_id: int, shp: dict[str, str] | None = None) -> str:
    password = settings.robokassa_password1 or ""
    base = f"{out_sum}:{inv_id}:{password}"
    return _hash_hex(base + _shp_tail(shp))


def extract_shp(payload: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in payload.items() if key.lower().startswith("shp_")}


def payment_url_for_invoice(invoice: CreditTopUp, *, email: str | None = None) -> str | None:
    if not settings.robokassa_enabled or invoice.status != "pending":
        return None
    out_sum = format_out_sum(invoice.amount_rub)
    params = {
        "MerchantLogin": settings.robokassa_merchant_login,
        "OutSum": out_sum,
        "InvId": str(invoice.inv_id),
        "Description": f"Пополнение баланса AIRuntime, {invoice.credits} кредитов",
        "SignatureValue": payment_signature(out_sum=out_sum, inv_id=invoice.inv_id),
        "Culture": "ru",
        "Encoding": "utf-8",
    }
    if settings.robokassa_test_mode:
        params["IsTest"] = "1"
    if email:
        params["Email"] = email
    return f"{PAYMENT_URL}?{urlencode(params)}"


def parse_inv_id(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value
