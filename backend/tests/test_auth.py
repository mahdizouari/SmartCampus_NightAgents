"""Unit and integration tests for SA-28 Authentication (OTP, Google OAuth, JWT, INPDP Consent)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.users import (
    Account,
    AccountRole,
    AuditLog,
    Candidate,
    CandidateProfile,
    Consent,
    Role,
)
from app.services.auth_service import clear_otp_store_for_tests


class _FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeResult:
    def __init__(self, items: list[Any]):
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)


class FakeAuthAsyncSession:
    """In-memory async session double supporting SA-28 Account, Role, Candidate, and Consent."""

    def __init__(self) -> None:
        self.accounts: dict[UUID, Account] = {}
        self.roles: dict[int, Role] = {}
        self.account_roles: list[AccountRole] = []
        self.candidates: dict[UUID, Candidate] = {}
        self.profiles: dict[UUID, CandidateProfile] = {}
        self.consents: list[Consent] = []
        self.audit_logs: list[AuditLog] = []
        self._role_seq = 1

    async def get(self, model: type[Any], pk: Any) -> Any:
        if model is Account:
            return self.accounts.get(pk)
        if model is Role:
            return self.roles.get(pk)
        if model is Candidate:
            return self.candidates.get(pk)
        return None

    def add(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, Account):
            if obj.id is None:
                obj.id = uuid4()
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.accounts[obj.id] = obj
        elif isinstance(obj, Role):
            if obj.id is None:
                obj.id = self._role_seq
                self._role_seq += 1
            self.roles[obj.id] = obj
        elif isinstance(obj, AccountRole):
            if obj.id is None:
                obj.id = uuid4()
            self.account_roles.append(obj)
        elif isinstance(obj, Candidate):
            if obj.id is None:
                obj.id = uuid4()
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.candidates[obj.id] = obj
        elif isinstance(obj, CandidateProfile):
            self.profiles[obj.candidate_id] = obj
        elif isinstance(obj, Consent):
            if obj.id is None:
                obj.id = uuid4()
            self.consents.append(obj)
        elif isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = uuid4()
            self.audit_logs.append(obj)

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        params = stmt.compile().params
        if "core.roles" in sql:
            code = params.get("code_1")
            matches = [r for r in self.roles.values() if r.code == code]
            return _FakeResult(matches)
        if "core.accounts" in sql:
            email = params.get("email_1")
            matches = [a for a in self.accounts.values() if a.email == email]
            return _FakeResult(matches)
        if "core.account_roles" in sql:
            acc_id = params.get("account_id_1")
            matches = [ar for ar in self.account_roles if ar.account_id == acc_id]
            return _FakeResult(matches)
        if "core.candidate" in sql:
            acc_id = params.get("account_id_1")
            matches = [c for c in self.candidates.values() if c.account_id == acc_id]
            return _FakeResult(matches)
        return _FakeResult([])

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_otp():
    clear_otp_store_for_tests()
    yield
    clear_otp_store_for_tests()


@pytest.fixture
def fake_auth_db() -> FakeAuthAsyncSession:
    return FakeAuthAsyncSession()


@pytest.fixture
def auth_client(fake_auth_db: FakeAuthAsyncSession):
    async def _override_get_db():
        yield fake_auth_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def test_otp_send_cooldown_and_verify_flow(
    auth_client: TestClient,
    fake_auth_db: FakeAuthAsyncSession,
):
    # 1. Request OTP via api_contract.md format
    send_resp = auth_client.post(
        "/api/v1/auth/otp/send",
        json={"channel": "email", "destination": "Etudiant@Example.tn"},
    )
    assert send_resp.status_code == 200
    data = send_resp.json()
    assert data["expires_in_seconds"] == 300
    otp_code = data["dev_otp_code"]
    assert otp_code and len(otp_code) == 6

    # 2. Immediate resend hits 60s rate-limit cooldown (429)
    cooldown_resp = auth_client.post(
        "/api/v1/auth/otp/request",
        json={"email": "etudiant@example.tn"},
    )
    assert cooldown_resp.status_code == 429

    # 3. Wrong OTP code -> 401
    bad_verify = auth_client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "etudiant@example.tn", "code": "000000"},
    )
    assert bad_verify.status_code == 401

    # 4. Valid OTP code -> 200 + JWT tokens + Candidate provisioned
    verify_resp = auth_client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "etudiant@example.tn", "code": otp_code},
    )
    assert verify_resp.status_code == 200
    tokens = verify_resp.json()
    assert tokens["access_token"] == tokens["token"]
    assert tokens["token_type"] == "Bearer"
    assert tokens["user"]["email"] == "etudiant@example.tn"
    assert tokens["user"]["role"] == "student"
    assert tokens["user"]["status"] == "AUTHENTICATED"
    assert tokens["user"]["candidate_id"] is not None

    # 5. GET /me with access token
    me_resp = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "etudiant@example.tn"

    # 6. Refresh token flow + token type enforcement
    bad_me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert bad_me.status_code == 401

    bad_refresh = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["access_token"]},
    )
    assert bad_refresh.status_code == 401

    ref_resp = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert ref_resp.status_code == 200
    assert ref_resp.json()["access_token"]

    # 7. Explicit INPDP Consent recording (Law 2004-63)
    bad_consent = auth_client.post(
        "/api/v1/auth/consent",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={
            "terms_accepted": False,
            "privacy_policy_accepted": True,
            "transcript_processing_accepted": True,
            "policy_version": "2026.1",
        },
    )
    assert bad_consent.status_code == 422

    consent_resp = auth_client.post(
        "/api/v1/auth/consent",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={
            "terms_accepted": True,
            "privacy_policy_accepted": True,
            "transcript_processing_accepted": True,
            "policy_version": "2026.1",
        },
    )
    assert consent_resp.status_code == 200
    c_data = consent_resp.json()
    assert c_data["status"] == "VALID"
    assert set(c_data["purposes_granted"]) == {
        "ORIENTATION_PROFILE",
        "ADMISSION_FOLLOWUP",
        "DOCUMENT_OCR",
    }
    assert len(fake_auth_db.consents) == 3
    assert all(c.ip_hash is not None for c in fake_auth_db.consents)


def test_otp_lockout_after_five_failed_attempts(auth_client: TestClient):
    send_resp = auth_client.post(
        "/api/v1/auth/otp/send",
        json={"email": "lockout@example.tn"},
    )
    code = send_resp.json()["dev_otp_code"]

    for _ in range(5):
        r = auth_client.post(
            "/api/v1/auth/otp/verify",
            json={"email": "lockout@example.tn", "code": "999999"},
        )
        assert r.status_code == 401

    # 6th attempt even with the right code must fail (challenge invalidated)
    locked = auth_client.post(
        "/api/v1/auth/otp/verify",
        json={"email": "lockout@example.tn", "code": code},
    )
    assert locked.status_code == 401


def test_google_oauth_login_and_staff_role_preservation(
    auth_client: TestClient,
    fake_auth_db: FakeAuthAsyncSession,
):
    # Pre-seed a responsable account for IIT
    resp_role = Role(code="responsable", label="Responsable", description="NAU Responsable")
    fake_auth_db.add(resp_role)
    staff_acc = Account(
        email="responsable.iit@smartcampus.tn",
        full_name="Dr. Responsable IIT",
        auth_provider="GOOGLE_OAUTH",
        is_active=True,
    )
    fake_auth_db.add(staff_acc)
    fake_auth_db.add(
        AccountRole(
            account_id=staff_acc.id,
            role_id=resp_role.id,
            school_scope="iit",
            responsibility_scope="ALL",
        )
    )

    mock_claims = {
        "sub": "google-sub-999",
        "email": "responsable.iit@smartcampus.tn",
        "email_verified": "true",
        "name": "Dr. Responsable IIT",
    }
    with patch(
        "app.services.auth_service.verify_google_id_token",
        new=AsyncMock(return_value=mock_claims),
    ):
        oauth_resp = auth_client.post(
            "/api/v1/auth/oauth/google",
            json={"id_token": "valid-google-id-token-placeholder"},
        )
    assert oauth_resp.status_code == 200
    body = oauth_resp.json()
    assert body["user"]["role"] == "responsable"
    assert body["user"]["school_scope"] == "iit"
    assert body["user"]["school"] == "IIT"
    assert body["user"]["candidate_id"] is None

