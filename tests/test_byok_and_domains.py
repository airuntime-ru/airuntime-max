"""Epic C (BYOK, zero-credit UX) and Epic D (custom domains).

The security-critical invariants here are that a user key is never echoed back or logged, and
that a user paying their own provider is never also charged platform credits.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.conftest import auth_tokens

from src.db.models.credit_ledger import CreditLedgerEntry
from src.db.models.plan import Plan
from src.db.models.project import Project
from src.db.models.user import User
from src.db.models.user_provider_credential import UserProviderCredential
from src.services import byok
from src.services import custom_domain as cd
from src.services.credit_gate import out_of_credits_detail
from src.services.model_access import resolve_model_for_user
from src.services.orchestration.budget import charge_credits_for_run
from src.services.secrets import decrypt_secret

REAL_KEY = "sk-test-abcdefghijklmnop1234"


@pytest.fixture()
def user(db: Session) -> User:
    row = User(email="byok@airuntime.dev", is_verified=True, credits_balance=10_000)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def project(db: Session, user: User) -> Project:
    row = Project(user_id=user.id, type="website", name="Домен", description="", status="created")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(byok, "validate_key", lambda *a, **k: (True, None))


# --------------------------------------------------------------------------- C1


def test_routerai_is_a_supported_byok_provider() -> None:
    assert "routerai" in byok.SUPPORTED_PROVIDERS
    assert "openai" in byok.SUPPORTED_PROVIDERS


def test_key_is_stored_encrypted_never_in_plaintext(db: Session, user: User, valid_key: None):
    byok.upsert_credential(db, user, "openai", REAL_KEY)

    row = db.query(UserProviderCredential).filter_by(user_id=user.id).one()
    assert row.encrypted_key != REAL_KEY
    assert REAL_KEY not in row.encrypted_key
    assert decrypt_secret(row.encrypted_key) == REAL_KEY


def test_api_never_returns_the_key(client: TestClient, db: Session, valid_key: None):
    headers = auth_tokens(client, "byok-api@airuntime.dev")

    saved = client.put(
        "/api/v1/byok", json={"provider": "openai", "api_key": REAL_KEY}, headers=headers
    )
    listed = client.get("/api/v1/byok", headers=headers)

    assert saved.status_code == 200
    assert REAL_KEY not in saved.text
    assert REAL_KEY not in listed.text
    assert saved.json()["last4"] == REAL_KEY[-4:]
    assert saved.json()["is_valid"] is True


def test_an_invalid_key_is_stored_but_not_used(db: Session, user: User, monkeypatch):
    monkeypatch.setattr(byok, "validate_key", lambda *a, **k: (False, "Ключ отклонён провайдером"))

    row = byok.upsert_credential(db, user, "openai", REAL_KEY)

    assert row.is_valid is False
    assert row.last_error == "Ключ отклонён провайдером"
    assert byok.resolve_user_api_key(db, user, "openai") is None


def test_valid_key_is_used_for_the_turn(db: Session, user: User, valid_key: None):
    byok.upsert_credential(db, user, "openai", REAL_KEY)

    assert byok.resolve_user_api_key(db, user, "openai") == REAL_KEY
    assert byok.resolve_user_api_key(db, user, "anthropic") is None


def test_byok_usage_does_not_touch_the_balance(
    db: Session, user: User, project: Project, valid_key: None
):
    byok.upsert_credential(db, user, "openai", REAL_KEY)
    balance_before = user.credits_balance

    charge_credits_for_run(
        db,
        user,
        project_id=project.id,
        amount=5_000,
        provider_name="openai",
        model="gpt-5.6-sol",
    )
    db.commit()
    db.refresh(user)

    assert user.credits_balance == balance_before
    entry = db.query(CreditLedgerEntry).filter_by(user_id=user.id, reason="byok_usage").one()
    assert entry.amount == 0
    assert entry.provider == "openai"


def test_platform_key_usage_still_charges(db: Session, user: User, project: Project):
    balance_before = user.credits_balance

    charge_credits_for_run(
        db, user, project_id=project.id, amount=1_500, provider_name="openai", model="gpt-5.6-sol"
    )
    db.commit()
    db.refresh(user)

    assert user.credits_balance == balance_before - 1_500


def test_byok_unlocks_the_whole_catalog_on_free(db: Session, user: User, valid_key: None):
    """Own key, own money - the plan's platform allowlist must not apply."""
    plan = db.query(Plan).filter(Plan.is_default.is_(True)).one()
    plan.allowed_models = ["gpt-5.6-luna"]
    user.plan_id = plan.id
    db.add_all([plan, user])
    db.commit()
    byok.upsert_credential(db, user, "openai", REAL_KEY)

    provider, model = resolve_model_for_user(
        db, user, model_override="gpt-5.6-sol", has_own_key=True
    )

    assert (provider, model) == ("openai", "gpt-5.6-sol")


def test_deleting_a_key_falls_back_to_the_platform(db: Session, user: User, valid_key: None):
    byok.upsert_credential(db, user, "openai", REAL_KEY)

    assert byok.delete_credential(db, user, "openai") is True
    assert byok.resolve_user_api_key(db, user, "openai") is None


# --------------------------------------------------------------------------- C2


def test_zero_credit_error_offers_three_ways_out(db: Session, user: User):
    user.credits_balance = 0
    db.add(user)
    db.commit()

    detail = out_of_credits_detail(db, user)

    assert detail["code"] == "out_of_credits"
    assert {action["id"] for action in detail["actions"]} == {"topup", "byok", "plan"}
    assert all(action["href"].startswith("/app/profile") for action in detail["actions"])


def test_byok_option_disappears_once_a_key_exists(db: Session, user: User, valid_key: None):
    byok.upsert_credential(db, user, "openai", REAL_KEY)

    detail = out_of_credits_detail(db, user)

    assert "byok" not in {action["id"] for action in detail["actions"]}
    assert detail["has_byok"] is True


def test_chat_returns_the_structured_402(client: TestClient, db: Session):
    headers = auth_tokens(client, "broke@airuntime.dev")
    row = db.query(User).filter_by(email="broke@airuntime.dev").one()
    project = client.post("/api/v1/projects", json={"name": "Пустой"}, headers=headers).json()
    chats = client.get(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()
    row.credits_balance = 0
    db.add(row)
    db.commit()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chats[0]['id']}/stream",
        json={"content": "Привет", "attachment_ids": []},
        headers=headers,
    )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "out_of_credits"


# --------------------------------------------------------------------------- D1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("shop.example.com", "shop.example.com"),
        ("  SHOP.Example.COM  ", "shop.example.com"),
        ("https://shop.example.com/path?x=1", "shop.example.com"),
        ("shop.example.com.", "shop.example.com"),
        ("shop.example.com:8443", "shop.example.com"),
    ],
)
def test_domain_normalization_accepts_what_users_paste(raw: str, expected: str):
    assert cd.normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["not a domain", "-bad.example.com", "example", "a..b.com"])
def test_domain_normalization_rejects_junk(raw: str):
    with pytest.raises(cd.CustomDomainError):
        cd.normalize_domain(raw)


def test_platform_domain_is_rejected():
    with pytest.raises(cd.CustomDomainError):
        cd.normalize_domain("mysite.airuntime.ru")


def test_setting_a_domain_starts_pending(db: Session, project: Project):
    updated = cd.set_custom_domain(db, project, "shop.example.com")

    assert updated.custom_domain == "shop.example.com"
    assert updated.custom_domain_status == cd.STATUS_PENDING
    assert updated.custom_domain_verified_at is None


def test_a_domain_cannot_be_claimed_twice(db: Session, user: User, project: Project):
    other = Project(
        user_id=user.id, type="website", name="Второй", description="", status="created"
    )
    db.add(other)
    db.commit()
    cd.set_custom_domain(db, project, "shop.example.com")

    with pytest.raises(cd.CustomDomainError):
        cd.set_custom_domain(db, other, "shop.example.com")


def test_verification_marks_verified_when_dns_points_at_us(
    db: Session, project: Project, monkeypatch
):
    cd.set_custom_domain(db, project, "shop.example.com")
    monkeypatch.setattr(cd, "check_domain_dns", lambda host: (True, None))

    updated = cd.verify_custom_domain(db, project)

    assert updated.custom_domain_status == cd.STATUS_VERIFIED
    assert updated.custom_domain_verified_at is not None
    assert updated.custom_domain_error is None


def test_wrong_dns_stays_pending_with_an_explanation(db: Session, project: Project, monkeypatch):
    """Propagation is normal - an alarming 'error' status would just create support noise."""
    cd.set_custom_domain(db, project, "shop.example.com")
    monkeypatch.setattr(cd, "check_domain_dns", lambda host: (False, "Домен ведёт на другой адрес"))

    updated = cd.verify_custom_domain(db, project)

    assert updated.custom_domain_status == cd.STATUS_PENDING
    assert updated.custom_domain_error == "Домен ведёт на другой адрес"


def test_clearing_the_domain_resets_status(db: Session, project: Project):
    cd.set_custom_domain(db, project, "shop.example.com")

    updated = cd.set_custom_domain(db, project, None)

    assert updated.custom_domain is None
    assert updated.custom_domain_status == cd.STATUS_NONE


def test_custom_domain_is_available_on_the_free_plan(client: TestClient, db: Session):
    """Explicitly not gated by plan - the brief says free keeps this feature."""
    headers = auth_tokens(client, "freedomain@airuntime.dev")
    plan = db.query(Plan).filter(Plan.is_default.is_(True)).one()
    plan.monthly_budget_rub = 100
    db.add(plan)
    db.commit()
    created = client.post("/api/v1/projects", json={"name": "Свой домен"}, headers=headers).json()

    response = client.put(
        f"/api/v1/projects/{created['id']}/domain",
        json={"domain": "shop.example.com"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_dns"
    assert response.json()["target"]["cname_target"]
