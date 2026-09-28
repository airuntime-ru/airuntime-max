"""Epic A + B: budget-in-rubles plans, approval-gated plan changes, markup, plan limits.

Covers the money-critical invariants:
  * no user reaches a paid plan without an admin approve,
  * the free grant is one-time,
  * platform-key usage is charged with the markup on top of provider cost,
  * project limits and the model allowlist come from the plan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.conftest import auth_tokens

from src.core.config import settings
from src.db.models.credit_ledger import CreditLedgerEntry
from src.db.models.plan import Plan
from src.db.models.plan_change_request import PlanChangeRequest
from src.db.models.user import User
from src.services import plan_requests
from src.services.billing import (
    assign_default_plan,
    plan_grant_credits,
    run_billing_maintenance,
    total_project_limit,
)
from src.services.model_access import (
    ModelNotAllowedError,
    reasoning_effort_for_user,
    resolve_model_for_user,
    visible_models_for_user,
)
from src.services.model_pricing import estimate_model_usage_cost


def _free_plan(db: Session) -> Plan:
    """Tighten conftest's permissive default plan into the real free offer.

    Updating rather than inserting keeps exactly one `is_default` row, so `get_default_plan()`
    is deterministic.
    """
    plan = db.query(Plan).filter(Plan.is_default.is_(True)).one()
    plan.monthly_budget_rub = 100
    plan.max_projects = 1
    plan.max_concurrent_projects = 1
    plan.grant_renews = False
    plan.allowed_models = ["gpt-5.6-luna"]
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _pro_plan(db: Session) -> Plan:
    plan = Plan(
        key="pro",
        name="Про",
        monthly_budget_rub=700,
        max_concurrent_projects=5,
        max_projects=10,
        price_rub=990,
        is_default=False,
        is_active=True,
        grant_renews=True,
        allowed_models=None,
        sort_order=1,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _user(db: Session, email: str = "plans@airuntime.dev") -> User:
    user = User(email=email, is_verified=True, credits_balance=0)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------- A2 / A5


def test_signup_grant_comes_from_plan_budget_in_rubles(db: Session):
    plan = _free_plan(db)
    user = _user(db)

    assign_default_plan(db, user)
    db.commit()

    assert user.credits_balance == 100 * settings.billing_credits_per_rub
    assert user.credits_balance == plan_grant_credits(plan)
    reasons = [row.reason for row in db.query(CreditLedgerEntry).filter_by(user_id=user.id).all()]
    assert "signup_grant" in reasons


def test_new_user_does_not_receive_a_billion_credits(db: Session):
    user = _user(db, "nobillion@airuntime.dev")
    assert user.credits_balance == 0


def test_free_grant_is_one_time_and_does_not_renew(db: Session):
    _free_plan(db)
    user = _user(db, "free-renew@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()

    # Spend it all, then let the period lapse.
    user.credits_balance = 0
    user.billing_period_end = datetime.now(UTC) - timedelta(days=1)
    db.add(user)
    db.commit()

    run_billing_maintenance(db)
    db.refresh(user)

    assert user.credits_balance == 0, "free grant must not be re-issued"
    assert user.billing_period_end > datetime.now(UTC), "period should still roll forward"


def test_paid_plan_still_renews(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "pro-renew@airuntime.dev")
    user.plan_id = pro.id
    user.credits_balance = 0
    user.billing_period_start = datetime.now(UTC) - timedelta(days=31)
    user.billing_period_end = datetime.now(UTC) - timedelta(days=1)
    db.add(user)
    db.commit()

    run_billing_maintenance(db)
    db.refresh(user)

    assert user.credits_balance == plan_grant_credits(pro)


# --------------------------------------------------------------------------- A1


def test_public_api_cannot_switch_plan_instantly(client: TestClient, db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    headers = auth_tokens(client, "switch@airuntime.dev")

    response = client.post("/api/v1/billing/plan", json={"plan_id": str(pro.id)}, headers=headers)

    assert response.status_code in (404, 405), "instant self-service switch must be gone"


def test_plan_request_does_not_grant_anything_until_approved(client: TestClient, db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    headers = auth_tokens(client, "request@airuntime.dev")
    user = db.query(User).filter_by(email="request@airuntime.dev").one()
    balance_before = user.credits_balance
    plan_before = user.plan_id

    created = client.post(
        "/api/v1/billing/plan-requests",
        json={"plan_id": str(pro.id), "note": "Оплатил вчера"},
        headers=headers,
    )

    assert created.status_code == 200
    assert created.json()["status"] == "pending"
    db.refresh(user)
    assert user.credits_balance == balance_before
    assert user.plan_id == plan_before


def test_only_one_pending_request_at_a_time(client: TestClient, db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    headers = auth_tokens(client, "double@airuntime.dev")

    first = client.post(
        "/api/v1/billing/plan-requests", json={"plan_id": str(pro.id)}, headers=headers
    )
    second = client.post(
        "/api/v1/billing/plan-requests", json={"plan_id": str(pro.id)}, headers=headers
    )

    assert first.status_code == 200
    assert second.status_code == 400


def test_cancelling_frees_the_slot(client: TestClient, db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    headers = auth_tokens(client, "cancel@airuntime.dev")
    created = client.post(
        "/api/v1/billing/plan-requests", json={"plan_id": str(pro.id)}, headers=headers
    ).json()

    cancelled = client.post(
        f"/api/v1/billing/plan-requests/{created['id']}/cancel", headers=headers
    )
    again = client.post(
        "/api/v1/billing/plan-requests", json={"plan_id": str(pro.id)}, headers=headers
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert again.status_code == 200


def test_approve_grants_budget_and_records_ledger(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "approve@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()

    request = plan_requests.create_request(db, user, pro)
    plan_requests.approve_request(db, request, resolved_by="admin@airuntime.ru")
    db.refresh(user)

    assert user.plan_id == pro.id
    assert user.credits_balance == plan_grant_credits(pro)
    reasons = [row.reason for row in db.query(CreditLedgerEntry).filter_by(user_id=user.id).all()]
    assert "plan_change" in reasons


def test_admin_marks_approved_and_the_sweep_grants_it(db: Session):
    """Django admin can only flip the status; run_billing_maintenance does the money part."""
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "sweep@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()
    request = plan_requests.create_request(db, user, pro)

    # Exactly what admin/domain/admin.py writes.
    request.status = "approved"
    request.resolved_at = datetime.now(UTC)
    request.resolved_by = "admin@airuntime.ru"
    db.add(request)
    db.commit()

    run_billing_maintenance(db)
    db.refresh(user)
    db.refresh(request)

    assert user.plan_id == pro.id
    assert user.credits_balance == plan_grant_credits(pro)
    assert request.applied_at is not None


def test_sweep_is_idempotent(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "idem@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()
    request = plan_requests.create_request(db, user, pro)
    plan_requests.approve_request(db, request)
    db.refresh(user)
    user.credits_balance = 5
    db.add(user)
    db.commit()

    run_billing_maintenance(db)
    db.refresh(user)

    assert user.credits_balance == 5, "an applied request must not be granted twice"


def test_reject_leaves_plan_and_balance_untouched(db: Session):
    free = _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "reject@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()
    balance_before = user.credits_balance

    request = plan_requests.create_request(db, user, pro)
    plan_requests.reject_request(db, request, admin_note="Оплата не поступила")
    db.refresh(user)

    assert user.plan_id == free.id
    assert user.credits_balance == balance_before
    assert db.get(PlanChangeRequest, request.id).status == "rejected"


# --------------------------------------------------------------------------- A3


def _usage() -> dict:
    return {"input_tokens": 1_000_000, "output_tokens": 0}


def test_markup_increases_charge_above_raw_provider_cost():
    raw = estimate_model_usage_cost(
        provider="openai", model="gpt-5.6-luna", usage=_usage(), markup_percent=0
    )
    marked = estimate_model_usage_cost(
        provider="openai", model="gpt-5.6-luna", usage=_usage(), markup_percent=10
    )

    assert raw is not None and marked is not None
    assert marked.credits == pytest.approx(raw.credits * 1.10, rel=0.001)
    assert marked.markup_percent == 10


def test_provider_cost_snapshot_excludes_markup():
    """Margin analytics needs provider cost and charged credits to stay separable."""
    raw = estimate_model_usage_cost(
        provider="openai", model="gpt-5.6-luna", usage=_usage(), markup_percent=0
    )
    marked = estimate_model_usage_cost(
        provider="openai", model="gpt-5.6-luna", usage=_usage(), markup_percent=25
    )

    assert raw is not None and marked is not None
    assert marked.provider_cost_usd_micros == raw.provider_cost_usd_micros
    assert marked.credits > raw.credits


# --------------------------------------------------------------------------- A4


def test_free_plan_allows_only_one_project(client: TestClient, db: Session):
    _free_plan(db)
    headers = auth_tokens(client, "onelimit@airuntime.dev")

    first = client.post("/api/v1/projects", json={"name": "Первый"}, headers=headers)
    second = client.post("/api/v1/projects", json={"name": "Второй"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409


def test_project_limit_follows_the_plan_not_a_global_setting(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "limits@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()

    assert total_project_limit(db, user) == 1

    user.plan_id = pro.id
    db.add(user)
    db.commit()

    assert total_project_limit(db, user) == 10


def test_runtime_limits_exposes_both_limits(client: TestClient, db: Session):
    _free_plan(db)
    headers = auth_tokens(client, "rtlimits@airuntime.dev")
    client.post("/api/v1/projects", json={"name": "Единственный"}, headers=headers)

    body = client.get("/api/v1/projects/runtime-limits", headers=headers).json()

    assert body["max_total"] == 1
    assert body["total"] == 1
    assert body["max_running"] == 1


# --------------------------------------------------------------------------- B1


def test_free_auto_selects_the_economy_model(db: Session):
    _free_plan(db)
    user = _user(db, "luna@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()

    provider, model = resolve_model_for_user(db, user)

    assert provider == "openai"
    assert model == "gpt-5.6-luna"


def test_free_cannot_select_the_frontier_model(db: Session):
    _free_plan(db)
    user = _user(db, "sol@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()

    with pytest.raises(ModelNotAllowedError):
        resolve_model_for_user(db, user, model_override="gpt-5.6-sol")


def test_paid_plan_can_select_any_catalog_model(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    user = _user(db, "paidmodel@airuntime.dev")
    user.plan_id = pro.id
    db.add(user)
    db.commit()

    provider, model = resolve_model_for_user(db, user, model_override="gpt-5.6-sol")

    assert (provider, model) == ("openai", "gpt-5.6-sol")


def test_free_model_catalog_is_filtered(db: Session):
    _free_plan(db)
    user = _user(db, "catalog@airuntime.dev")
    assign_default_plan(db, user)
    db.commit()
    options = [{"id": "gpt-5.6-sol"}, {"id": "gpt-5.6-luna"}, {"id": "gpt-5.6-terra"}]

    visible = visible_models_for_user(db, user, options, provider="openai")

    assert [row["id"] for row in visible] == ["gpt-5.6-luna"]


def test_free_reasoning_effort_is_reduced(db: Session):
    _free_plan(db)
    pro = _pro_plan(db)
    free_user = _user(db, "effort-free@airuntime.dev")
    assign_default_plan(db, free_user)
    paid_user = _user(db, "effort-paid@airuntime.dev")
    paid_user.plan_id = pro.id
    db.add(paid_user)
    db.commit()

    assert reasoning_effort_for_user(db, free_user) == "medium"
    assert reasoning_effort_for_user(db, paid_user) == settings.codex_reasoning_effort


def test_chat_stream_rejects_a_disallowed_model_with_403(client: TestClient, db: Session):
    """Bypassing the filtered UI select must fail at the API, not silently run the frontier."""
    _free_plan(db)
    headers = auth_tokens(client, "chatgate@airuntime.dev")
    project = client.post("/api/v1/projects", json={"name": "Гейт"}, headers=headers).json()
    chats = client.get(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chats[0]['id']}/stream",
        json={"content": "Привет", "model": "gpt-5.6-sol", "attachment_ids": []},
        headers=headers,
    )

    assert response.status_code == 403
