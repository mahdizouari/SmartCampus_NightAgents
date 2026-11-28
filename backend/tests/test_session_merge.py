"""Unit and integration tests for SA-29 Associating Guest Sessions to Candidate Accounts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import create_access_token
from app.main import app
from app.models.users import (
    Account,
    AccountRole,
    AuditLog,
    Candidate,
    CandidateProfile,
    GuestSession,
    Role,
)
from app.services.guest_session_service import (
    clear_guest_session_store_for_tests,
    create_signed_guest_cookie_token,
)


class _FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return self._items


class _FakeResult:
    def __init__(self, items: list[Any]):
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)


class FakeMergeAsyncSession:
    """In-memory async session double supporting SA-29 session merge verification."""

    def __init__(self) -> None:
        self.accounts: dict[UUID, Account] = {}
        self.roles: dict[int, Role] = {}
        self.account_roles: list[AccountRole] = []
        self.candidates: dict[UUID, Candidate] = {}
        self.profiles: dict[UUID, CandidateProfile] = {}
        self.guest_sessions: dict[UUID, GuestSession] = {}
        self.audit_logs: list[AuditLog] = []

    async def get(self, model: type[Any], pk: Any) -> Any:
        if model is Account:
            return self.accounts.get(pk)
        if model is Candidate:
            return self.candidates.get(pk)
        if model is CandidateProfile:
            return self.profiles.get(pk)
        if model is GuestSession:
            return self.guest_sessions.get(pk)
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, Account):
            self.accounts[obj.id] = obj
        elif isinstance(obj, Candidate):
            self.candidates[obj.id] = obj
        elif isinstance(obj, CandidateProfile):
            self.profiles[obj.candidate_id] = obj
        elif isinstance(obj, GuestSession):
            self.guest_sessions[obj.id] = obj
        elif isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def execute(self, stmt: Any) -> _FakeResult:
        compiled = str(stmt).lower()
        if "from core.candidate" in compiled:
            params = stmt.compile().params
            acc_id = params.get("account_id_1")
            matches = [
                c
                for c in self.candidates.values()
                if acc_id and str(c.account_id) == str(acc_id)
            ]
            return _FakeResult(matches)
        return _FakeResult([])

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None


@pytest.fixture
def fake_merge_db() -> FakeMergeAsyncSession:
    clear_guest_session_store_for_tests()
    return FakeMergeAsyncSession()


@pytest.fixture
def merge_client(fake_merge_db: FakeMergeAsyncSession):
    async def _override_get_db():
        yield fake_merge_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    clear_guest_session_store_for_tests()


def _create_authenticated_student(
    db: FakeMergeAsyncSession,
    email: str = "candidat@nau.org",
) -> tuple[Account, Candidate, str]:
    """Helper to provision a candidate account and return an authorized Bearer JWT."""
    now = datetime.now(UTC)
    acc = Account(
        id=uuid4(),
        email=email,
        full_name="Youssef Test",
        auth_provider="EMAIL_OTP",
        is_active=True,
        email_verified_at=now,
    )
    cand = Candidate(
        id=uuid4(),
        account_id=acc.id,
        candidate_number="NAU-2026-TEST42",
        email=email,
        status="AUTHENTICATED",
        owner="JARVIS",
    )
    prof = CandidateProfile(
        candidate_id=cand.id,
        facts={},
        summary="",
    )
    db.add(acc)
    db.add(cand)
    db.add(prof)

    token = create_access_token(
        account_id=str(acc.id),
        role="student",
        email=email,
        candidate_id=str(cand.id),
    )
    return acc, cand, token


def test_merge_session_requires_authentication(merge_client: TestClient):
    """POST /api/v1/auth/merge-session requires a valid student Bearer token."""
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        json={"anonymous_session_id": str(uuid4()), "consent_explicit": True},
    )
    assert resp.status_code == 401


def test_merge_session_requires_explicit_consent(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """Merge must be explicitly consented to by the candidate (consent_explicit=True)."""
    _, _, token = _create_authenticated_student(fake_merge_db)
    sid = uuid4()
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"anonymous_session_id": str(sid), "consent_explicit": False},
    )
    assert resp.status_code == 400
    assert "explicit consent is required" in resp.json()["detail"].lower()


def test_merge_session_successful_transfer_profile_and_history(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """
    Complete nominal flow:
    1. Visitor initiates anonymous guest session.
    2. Enters declared profile facts and conversation turns.
    3. Authenticates and explicitly merges the guest session into their candidate profile.
    4. Verifies database profile sync, status, audit trail, and cookie cleanup.
    """
    # 1. Create guest session
    create_resp = merge_client.post(
        "/api/v1/auth/guest-session",
        json={"preferred_language": "ARABIZI"},
    )
    assert create_resp.status_code == 201
    sid_str = create_resp.json()["session_id"]
    sid = UUID(sid_str)
    signed_cookie = create_resp.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    assert signed_cookie is not None

    # 2. Patch guest session with profile facts
    patch_resp = merge_client.patch(
        "/api/v1/auth/guest-session",
        json={
            "step": "ORIENTED",
            "school_target": "iit",
            "declared_facts": {
                "bac_section": "Sciences Informatiques",
                "declared_overall_avg": 16.25,
                "current_specialty": "Génie Logiciel",
            },
        },
    )
    assert patch_resp.status_code == 200

    # 3. Create authenticated candidate
    acc, cand, token = _create_authenticated_student(fake_merge_db)

    # 4. Explicitly merge session
    merge_resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"anonymous_session_id": sid_str, "consent_explicit": True},
    )
    assert merge_resp.status_code == 200
    m_data = merge_resp.json()
    assert m_data["candidate_id"] == str(cand.id)
    assert m_data["account_id"] == str(acc.id)
    assert m_data["anonymous_session_id"] == sid_str
    assert m_data["declared_profile_synced"] is True
    assert m_data["current_status"] == "AUTHENTICATED"

    # 5. Verify PostgreSQL Candidate & CandidateProfile state
    profile = fake_merge_db.profiles[cand.id]
    assert profile.facts["bac_section"] == "Sciences Informatiques"
    assert profile.preferred_language == "ARABIZI"
    assert profile.current_specialty == "Génie Logiciel"
    assert float(profile.declared_overall_avg) == 16.25
    assert fake_merge_db.candidates[cand.id].school_target == "iit"

    # 6. Verify core.guest_sessions status changed to MERGED
    guest_rec = fake_merge_db.guest_sessions[sid]
    assert guest_rec.status == "MERGED"
    assert guest_rec.merged_candidate_id == cand.id
    assert guest_rec.merged_at is not None

    # 7. Verify AuditLog recorded
    audit_entries = [
        a for a in fake_merge_db.audit_logs if a.action == "GUEST_SESSION_MERGED"
    ]
    assert len(audit_entries) == 1
    assert audit_entries[0].actor_type == "CANDIDATE"
    assert audit_entries[0].entity_id == sid


def test_merge_session_ownership_verification_cookie_mismatch_rejected(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """
    Security check:
    If client presents a signed cookie for session A, but asks to merge session B,
    the request must be rejected with 403 Forbidden to prevent session hijacking.
    """
    _, _, token = _create_authenticated_student(fake_merge_db)

    # Issue cookie for Session A
    sess_a = uuid4()
    cookie_token_a, _, _ = create_signed_guest_cookie_token(sess_a)
    merge_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, cookie_token_a)

    # Attempt to merge Session B
    sess_b = uuid4()
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"anonymous_session_id": str(sess_b), "consent_explicit": True},
    )
    assert resp.status_code == 403
    assert "ownership verification failed" in resp.json()["detail"].lower()


def test_merge_session_ownership_verification_header_mismatch_rejected(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """
    Security check:
    If client presents an X-Session-ID header for session A, but asks to merge session B,
    the request must be rejected with 403 Forbidden.
    """
    _, _, token = _create_authenticated_student(fake_merge_db)

    sess_a = uuid4()
    sess_b = uuid4()
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Session-ID": str(sess_a),
        },
        json={"anonymous_session_id": str(sess_b), "consent_explicit": True},
    )
    assert resp.status_code == 403
    assert "ownership verification failed" in resp.json()["detail"].lower()


def test_merge_session_invalid_header_format_rejected(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """If client presents an invalid X-Session-ID header format, returns 401."""
    _, _, token = _create_authenticated_student(fake_merge_db)
    sess_b = uuid4()
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Session-ID": "not-a-valid-uuid",
        },
        json={"anonymous_session_id": str(sess_b), "consent_explicit": True},
    )
    assert resp.status_code == 401


def test_merge_session_double_merge_rejected_with_409(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """Idempotency / Anti-replay: A session already marked as MERGED cannot be merged again."""
    _, _, token = _create_authenticated_student(fake_merge_db)

    # Seed an already merged session
    sid = uuid4()
    now = datetime.now(UTC)
    already_merged = GuestSession(
        id=sid,
        status="MERGED",
        preferred_language="FR",
        merged_candidate_id=uuid4(),
        merged_at=now,
        last_activity_at=now,
        expires_at=now,
        created_at=now,
    )
    fake_merge_db.add(already_merged)

    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"anonymous_session_id": str(sid), "consent_explicit": True},
    )
    assert resp.status_code == 409
    assert "already been merged" in resp.json()["detail"].lower()


def test_merge_session_non_existent_rejected_with_404(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """Non-existent or purged session returns 404 Not Found."""
    _, _, token = _create_authenticated_student(fake_merge_db)
    random_sid = uuid4()
    resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"anonymous_session_id": str(random_sid), "consent_explicit": True},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_merge_session_auto_resolved_from_signed_cookie(
    merge_client: TestClient,
    fake_merge_db: FakeMergeAsyncSession,
):
    """When anonymous_session_id is omitted from body, it is auto-resolved from signed cookie."""
    create_resp = merge_client.post("/api/v1/auth/guest-session")
    sid_str = create_resp.json()["session_id"]
    sid = UUID(sid_str)

    _, cand, token = _create_authenticated_student(fake_merge_db)

    # Call merge without anonymous_session_id in body
    merge_resp = merge_client.post(
        "/api/v1/auth/merge-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"consent_explicit": True},
    )
    assert merge_resp.status_code == 200
    assert merge_resp.json()["anonymous_session_id"] == sid_str
    assert fake_merge_db.guest_sessions[sid].status == "MERGED"
