"""Unit and integration tests for SA-25 Signed Anonymous Guest Sessions & Rate Limiting."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import create_access_token
from app.core.rate_limit import enforce_guest_chat_rate_limit
from app.main import app
from app.models.users import GuestSession
from app.services.guest_session_service import (
    create_signed_guest_cookie_token,
    verify_signed_guest_cookie_token,
)


class FakeGuestDbSession:
    """In-memory async DB double tracking PII-free core.guest_sessions rows."""

    def __init__(self) -> None:
        self.guest_sessions: dict[UUID, GuestSession] = {}

    async def get(self, model: type[Any], pk: Any) -> Any:
        if model is GuestSession:
            return self.guest_sessions.get(pk)
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, GuestSession):
            self.guest_sessions[obj.id] = obj

    async def commit(self) -> None:
        return None


@pytest.fixture
def fake_guest_db() -> FakeGuestDbSession:
    return FakeGuestDbSession()


@pytest.fixture
def guest_client(fake_guest_db: FakeGuestDbSession):
    async def _override_get_db():
        yield fake_guest_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def test_create_guest_session_issues_signed_cookie_and_7_day_ttl(
    guest_client: TestClient,
    fake_guest_db: FakeGuestDbSession,
):
    """Verify POST /api/v1/auth/guest-session issues a signed 7-day cookie and DB tracker."""
    resp = guest_client.post(
        "/api/v1/auth/guest-session",
        json={"preferred_language": "DERJA"},
    )
    assert resp.status_code == 201
    data = resp.json()
    session_uuid = UUID(data["session_id"])
    assert data["status"] == "ANONYMOUS"
    assert data["ttl_seconds"] == 604800  # 7 days
    assert data["preferred_language"] == "DERJA"
    assert data["step"] == "ANONYMOUS"
    assert data["signed_cookie_issued"] is True
    assert resp.headers.get("X-Session-ID") == str(session_uuid)

    cookie_val = resp.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    assert cookie_val is not None
    verified_uuid = verify_signed_guest_cookie_token(cookie_val)
    assert verified_uuid == session_uuid

    # Verify PII-free tracker row in core.guest_sessions
    assert session_uuid in fake_guest_db.guest_sessions
    tracker = fake_guest_db.guest_sessions[session_uuid]
    assert tracker.status == "ACTIVE"
    assert tracker.preferred_language == "DERJA"


def test_auto_create_guest_session_when_visitor_starts_conversation(
    guest_client: TestClient,
    fake_guest_db: FakeGuestDbSession,
):
    """Verify automatic temporary session creation when visitor has no cookie yet."""
    resp = guest_client.get("/api/v1/auth/guest-session?auto_create=true")
    assert resp.status_code == 200
    data = resp.json()
    sid = UUID(data["session_id"])
    assert data["status"] == "ANONYMOUS"
    assert data["ttl_seconds"] == 604800
    assert resp.cookies.get(settings.GUEST_SESSION_COOKIE_NAME) is not None
    assert sid in fake_guest_db.guest_sessions


def test_get_and_patch_guest_session_sliding_7_day_ttl_refresh(
    guest_client: TestClient,
):
    """Verify partial updates and sliding 7-day TTL refresh via signed cookie."""
    create_resp = guest_client.post("/api/v1/auth/guest-session")
    assert create_resp.status_code == 201
    sid = create_resp.json()["session_id"]

    patch_resp = guest_client.patch(
        "/api/v1/auth/guest-session",
        json={
            "preferred_language": "ARABIZI",
            "step": "PROFILING",
            "school_target": "iit",
            "declared_facts": {"bac_section": "Mathématiques", "average": 15.4},
        },
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["session_id"] == sid
    assert patched["preferred_language"] == "ARABIZI"
    assert patched["step"] == "PROFILING"
    assert patched["school_target"] == "iit"
    assert patched["session_data"]["declared_facts"]["average"] == 15.4

    # Subsequent partial update merges declared_facts without losing previous keys
    patch_resp_2 = guest_client.patch(
        "/api/v1/auth/guest-session",
        json={"step": "ELIGIBLE", "declared_facts": {"city": "Sfax"}},
    )
    assert patch_resp_2.status_code == 200
    patched_2 = patch_resp_2.json()
    assert patched_2["step"] == "ELIGIBLE"
    assert patched_2["session_data"]["declared_facts"] == {
        "bac_section": "Mathématiques",
        "average": 15.4,
        "city": "Sfax",
    }

    get_resp = guest_client.get("/api/v1/auth/guest-session")
    assert get_resp.status_code == 200
    assert get_resp.json()["session_id"] == sid
    assert get_resp.json()["ttl_seconds"] == 604800


def test_tampered_expired_or_mismatched_cookie_rejected_with_401(
    guest_client: TestClient,
):
    """Verify cryptographic validation rejects forged, expired, or wrong-type tokens."""
    create_resp = guest_client.post("/api/v1/auth/guest-session")
    valid_cookie = create_resp.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    assert valid_cookie is not None

    # 1. Tampered signature
    tampered = valid_cookie[:-4] + ("AAAA" if not valid_cookie.endswith("AAAA") else "BBBB")
    guest_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, tampered)
    r_tampered = guest_client.get("/api/v1/auth/guest-session")
    assert r_tampered.status_code == 401

    # 2. Expired cookie (negative TTL)
    expired_token, _, _ = create_signed_guest_cookie_token(
        uuid4(),
        ttl_seconds=-60,
        now_ts=int(datetime.now(UTC).timestamp()) - 120,
    )
    guest_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, expired_token)
    r_expired = guest_client.get("/api/v1/auth/guest-session")
    assert r_expired.status_code == 401

    # 3. Standard user access token mistakenly sent as guest session cookie
    access_jwt = create_access_token(account_id=str(uuid4()), role="student")
    guest_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, access_jwt)
    r_wrong_type = guest_client.get("/api/v1/auth/guest-session")
    assert r_wrong_type.status_code == 401

    # 4. Valid cookie but conflicting X-Session-ID header
    guest_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, valid_cookie)
    r_mismatch = guest_client.get(
        "/api/v1/auth/guest-session",
        headers={"X-Session-ID": str(uuid4())},
    )
    assert r_mismatch.status_code == 401


def test_terminate_guest_session_expires_and_blocks_reuse(
    guest_client: TestClient,
    fake_guest_db: FakeGuestDbSession,
):
    """Verify DELETE /api/v1/auth/guest-session purges state and blocks replay."""
    create_resp = guest_client.post("/api/v1/auth/guest-session")
    sid = UUID(create_resp.json()["session_id"])
    saved_cookie = create_resp.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    assert saved_cookie is not None

    del_resp = guest_client.delete("/api/v1/auth/guest-session")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "EXPIRED"
    assert del_resp.json()["expired"] is True
    assert fake_guest_db.guest_sessions[sid].status == "EXPIRED"

    # Replaying the old signed cookie after termination must fail with 401
    guest_client.cookies.set(settings.GUEST_SESSION_COOKIE_NAME, saved_cookie)
    replay_resp = guest_client.get("/api/v1/auth/guest-session")
    assert replay_resp.status_code == 401


def test_per_ip_rate_limiting_and_ietf_headers(
    guest_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify IETF RateLimit-* headers and 429 Too Many Requests when IP quota is exceeded."""
    monkeypatch.setattr(settings, "RATE_LIMIT_GUEST_IP_PER_MINUTE", 3)

    for expected_remaining in (2, 1, 0):
        r = guest_client.post(
            "/api/v1/auth/guest-session",
            headers={"X-Forwarded-For": "196.203.12.45"},
        )
        assert r.status_code == 201
        assert r.headers.get("RateLimit-Limit") == "3"
        assert r.headers.get("RateLimit-Remaining") == str(expected_remaining)
        assert int(r.headers.get("RateLimit-Reset", "0")) >= 1

    # 4th request from the same IP in the window must be throttled with 429
    blocked = guest_client.post(
        "/api/v1/auth/guest-session",
        headers={"X-Forwarded-For": "196.203.12.45"},
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") is not None
    assert blocked.headers.get("RateLimit-Remaining") == "0"
    body = blocked.json()
    assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert body["status"] == 429

    # Health endpoints must remain exempt from rate limiting
    health_resp = guest_client.get(
        "/health",
        headers={"X-Forwarded-For": "196.203.12.45"},
    )
    assert health_resp.status_code == 200


def test_per_session_chat_rate_limiting(
    guest_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify per-session chat/orientation rate limiting (15 req/min default)."""
    monkeypatch.setattr(settings, "RATE_LIMIT_GUEST_IP_PER_MINUTE", 50)
    monkeypatch.setattr(settings, "RATE_LIMIT_GUEST_CHAT_PER_MINUTE", 2)

    create_resp = guest_client.post("/api/v1/auth/guest-session")
    assert create_resp.status_code == 201

    # Register a temporary chat probe route using enforce_guest_chat_rate_limit
    from fastapi import Depends

    @app.get("/api/v1/chat/_rate_limit_probe")
    async def _chat_probe(_rl=Depends(enforce_guest_chat_rate_limit)):
        return {"ok": True}

    try:
        r1 = guest_client.get("/api/v1/chat/_rate_limit_probe")
        assert r1.status_code == 200
        r2 = guest_client.get("/api/v1/chat/_rate_limit_probe")
        assert r2.status_code == 200
        # 3rd chat request on the same guest session exceeds the 2 req/min chat limit
        r3 = guest_client.get("/api/v1/chat/_rate_limit_probe")
        assert r3.status_code == 429
        assert r3.headers.get("Retry-After") is not None
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/api/v1/chat/_rate_limit_probe"
        ]
