"""Robokassa payment form signatures and Result/Success/Fail callbacks."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.conftest import auth_tokens

from src.core.config import settings
from src.db.models.credit_ledger import CreditLedgerEntry
from src.db.models.credit_topup import CreditTopUp
from src.db.models.user import User
from src.services.robokassa import (
    payment_signature,
    payment_url_for_invoice,
    result_signature,
    success_signature,
)


def _enable_robokassa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "robokassa_merchant_login", "airuntime")
    monkeypatch.setattr(settings, "robokassa_password1", "pass1")
    monkeypatch.setattr(settings, "robokassa_password2", "pass2")
    monkeypatch.setattr(settings, "robokassa_password3", "pass3")
    monkeypatch.setattr(settings, "robokassa_test_mode", True)
    monkeypatch.setattr(settings, "robokassa_hash_algorithm", "md5")


def test_payment_and_result_signatures_use_the_documented_colon_format(
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_robokassa(monkeypatch)

    assert (
        payment_signature(out_sum="10", inv_id=5)
        == hashlib.md5(b"airuntime:10:5:pass1").hexdigest()
    )
    assert result_signature(out_sum="10", inv_id=5) == hashlib.md5(b"10:5:pass2").hexdigest()
    assert success_signature(out_sum="10", inv_id=5) == hashlib.md5(b"10:5:pass1").hexdigest()


def test_create_topup_without_robokassa_has_no_payment_url(client: TestClient):
    headers = auth_tokens(client, "manual-topup@airuntime.dev")
    created = client.post("/api/v1/billing/topups", json={"credits": 1000}, headers=headers)

    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "pending"
    assert body["amount_rub"] == 10
    assert body["payment_url"] is None


def test_create_topup_returns_robokassa_url(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_robokassa(monkeypatch)
    headers = auth_tokens(client, "pay-url@airuntime.dev")
    created = client.post("/api/v1/billing/topups", json={"credits": 1000}, headers=headers)

    assert created.status_code == 200
    url = created.json()["payment_url"]
    assert url.startswith("https://auth.robokassa.ru/Merchant/Index.aspx?")
    assert "MerchantLogin=airuntime" in url
    assert "OutSum=10" in url
    assert "IsTest=1" in url
    assert "SignatureValue=" in url


def _pending_invoice(
    client: TestClient, db: Session, email: str, credits: int = 1000
) -> CreditTopUp:
    headers = auth_tokens(client, email)
    created = client.post("/api/v1/billing/topups", json={"credits": credits}, headers=headers)
    assert created.status_code == 200
    invoice = db.get(CreditTopUp, uuid.UUID(created.json()["id"]))
    assert invoice is not None
    assert invoice.inv_id > 0
    return invoice


def test_result_url_credits_the_user_and_is_idempotent(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    _enable_robokassa(monkeypatch)
    invoice = _pending_invoice(client, db, "result-ok@airuntime.dev")
    user = db.get(User, invoice.user_id)
    assert user is not None
    balance_before = user.credits_balance
    signature = result_signature(out_sum="10", inv_id=invoice.inv_id)

    first = client.post(
        "/api/v1/billing/robokassa/result",
        data={"OutSum": "10", "InvId": str(invoice.inv_id), "SignatureValue": signature},
    )
    second = client.post(
        "/api/v1/billing/robokassa/result",
        data={"OutSum": "10", "InvId": str(invoice.inv_id), "SignatureValue": signature},
    )

    assert first.status_code == 200
    assert first.text == f"OK{invoice.inv_id}"
    assert second.status_code == 200
    assert second.text == f"OK{invoice.inv_id}"
    db.refresh(invoice)
    db.refresh(user)
    assert invoice.status == "paid"
    assert invoice.paid_at is not None
    assert invoice.credited_at is not None
    assert user.credits_balance == balance_before + invoice.credits
    topup_rows = db.query(CreditLedgerEntry).filter_by(user_id=user.id, reason="topup").all()
    assert len(topup_rows) == 1
    assert topup_rows[0].amount == invoice.credits


def test_result_url_rejects_bad_signature(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    _enable_robokassa(monkeypatch)
    invoice = _pending_invoice(client, db, "result-bad@airuntime.dev")
    user = db.get(User, invoice.user_id)
    assert user is not None
    balance_before = user.credits_balance

    response = client.post(
        "/api/v1/billing/robokassa/result",
        data={"OutSum": "10", "InvId": str(invoice.inv_id), "SignatureValue": "deadbeef"},
    )

    assert response.status_code == 400
    db.refresh(invoice)
    db.refresh(user)
    assert invoice.status == "pending"
    assert user.credits_balance == balance_before


def test_result_url_rejects_amount_mismatch(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    _enable_robokassa(monkeypatch)
    invoice = _pending_invoice(client, db, "result-sum@airuntime.dev")
    signature = result_signature(out_sum="999", inv_id=invoice.inv_id)

    response = client.post(
        "/api/v1/billing/robokassa/result",
        data={"OutSum": "999", "InvId": str(invoice.inv_id), "SignatureValue": signature},
    )

    assert response.status_code == 400
    db.refresh(invoice)
    assert invoice.status == "pending"


def test_success_and_fail_redirect_to_profile(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
):
    _enable_robokassa(monkeypatch)
    invoice = _pending_invoice(client, db, "return-url@airuntime.dev")
    signature = success_signature(out_sum="10", inv_id=invoice.inv_id)

    success = client.get(
        "/api/v1/billing/robokassa/success",
        params={
            "OutSum": "10",
            "InvId": str(invoice.inv_id),
            "SignatureValue": signature,
        },
        follow_redirects=False,
    )
    fail = client.get("/api/v1/billing/robokassa/fail", follow_redirects=False)

    assert success.status_code in (302, 303)
    assert success.headers["location"].endswith("/app/profile?payment=success")
    assert fail.status_code in (302, 303)
    assert fail.headers["location"].endswith("/app/profile?payment=fail")


def test_payment_url_for_cancelled_invoice_is_none(monkeypatch: pytest.MonkeyPatch, db: Session):
    _enable_robokassa(monkeypatch)
    user = User(email="cancelled-url@airuntime.dev", is_verified=True, credits_balance=0)
    db.add(user)
    db.commit()
    db.refresh(user)
    invoice = CreditTopUp(
        user_id=user.id,
        credits=1000,
        amount_rub=10,
        status="cancelled",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    assert payment_url_for_invoice(invoice) is None
