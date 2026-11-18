"""QA automated tests for Authentication, JWT & Consent endpoints (SA-28 & SA-22).

Verifies OTP generation/verification, token refresh, /me endpoint, and INPDP consent.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, status

from app.core.database import get_db
from app.core.deps import create_access_token, create_refresh_token
from app.main import app
from app.schemas.auth import (
    AuthTokenResponse,
    AuthUserSummary,
    ConsentResponse,
    OtpSendResponse,
)

SAMPLE_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
SAMPLE_CANDIDATE_ID = UUID("66666666-7777-8888-9999-000000000000")

MOCK_USER_SUMMARY = AuthUserSummary(
    id=SAMPLE_USER_ID,
    email="etudiant.test@smartcampus.tn",
    full_name="Ahmed Ben Ahmed",
    fullName="Ahmed Ben Ahmed",
    role="student",
    school_scope="iit",
    school="iit",
    candidate_id=SAMPLE_CANDIDATE_ID,
    status="AUTHENTICATED",
    isAnonymous=False,
)

MOCK_TOKENS = AuthTokenResponse(
    access_token="mock.jwt.access",
    token="mock.jwt.access",
    refresh_token="mock.jwt.refresh",
    token_type="Bearer",
    expires_in=3600,
    user=MOCK_USER_SUMMARY,
)


@pytest.fixture(autouse=True)
def _mock_db_dependency():
    """Ensure auth test queries never touch external databases."""

    async def _dummy_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _dummy_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


def test_otp_send_valid_email(client):
    """POST /api/v1/auth/otp/send succeeds with a properly formatted email."""
    mock_resp = OtpSendResponse(
        success=True,
        message="Code OTP envoyé",
        expires_in_seconds=300,
        dev_otp_code="123456",
    )
    with patch(
        "app.services.auth_service.send_otp",
        new=AsyncMock(return_value=mock_resp),
    ):
        response = client.post(
            "/api/v1/auth/otp/send",
            json={"channel": "email", "email": "etudiant@gmail.com"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert "expires_in_seconds" in data


def test_otp_send_invalid_email_format(client):
    """POST /api/v1/auth/otp/send returns 422 when email format is invalid."""
    response = client.post(
        "/api/v1/auth/otp/send",
        json={"channel": "email", "email": "not-an-email"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_otp_send_missing_destination(client):
    """POST /api/v1/auth/otp/send returns 422 when destination and email are absent."""
    response = client.post(
        "/api/v1/auth/otp/send",
        json={"channel": "email"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_otp_verify_success(client):
    """POST /api/v1/auth/otp/verify issues JWT bearer tokens on valid OTP code."""
    with patch(
        "app.services.auth_service.verify_otp",
        new=AsyncMock(return_value=MOCK_TOKENS),
    ):
        response = client.post(
            "/api/v1/auth/otp/verify",
            json={"channel": "email", "email": "etudiant@gmail.com", "code": "123456"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"


def test_otp_verify_invalid_code_format(client):
    """POST /api/v1/auth/otp/verify rejects codes that are not exactly 6 digits."""
    # Less than 6 digits
    resp_short = client.post(
        "/api/v1/auth/otp/verify",
        json={"channel": "email", "email": "etudiant@gmail.com", "code": "123"},
    )
    assert resp_short.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Alpha characters
    resp_alpha = client.post(
        "/api/v1/auth/otp/verify",
        json={"channel": "email", "email": "etudiant@gmail.com", "code": "ABCDEF"},
    )
    assert resp_alpha.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_otp_verify_wrong_code_fails(client):
    """POST /api/v1/auth/otp/verify returns 401 when code is incorrect or expired."""
    with patch(
        "app.services.auth_service.verify_otp",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=401, detail="Code OTP invalide ou expiré"
            )
        ),
    ):
        response = client.post(
            "/api/v1/auth/otp/verify",
            json={"channel": "email", "email": "etudiant@gmail.com", "code": "000000"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_token_endpoint(client):
    """POST /api/v1/auth/refresh returns fresh tokens when provided a valid refresh token."""
    with patch(
        "app.services.auth_service.refresh_access_token",
        new=AsyncMock(return_value=MOCK_TOKENS),
    ):
        token = create_refresh_token(
            account_id="11111111-2222-3333-4444-555555555555",
            role="student",
        )
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data


def test_get_me_authenticated(client):
    """GET /api/v1/auth/me returns the authenticated actor profile."""
    valid_token = create_access_token(
        account_id="11111111-2222-3333-4444-555555555555",
        role="student",
        email="etudiant.test@smartcampus.tn",
    )
    with patch(
        "app.services.auth_service.get_current_user_summary",
        new=AsyncMock(return_value=MOCK_USER_SUMMARY),
    ):
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email"] == "etudiant.test@smartcampus.tn"
        assert data["role"] == "student"


def test_get_me_unauthenticated(client):
    """GET /api/v1/auth/me returns 401 when Authorization header is absent."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_consent_endpoint(client):
    """POST /api/v1/auth/consent records student consent for personal data processing."""
    valid_token = create_access_token(
        account_id="11111111-2222-3333-4444-555555555555",
        role="student",
        candidate_id="66666666-7777-8888-9999-000000000000",
    )
    mock_consent = ConsentResponse(
        consent_id=UUID("99999999-8888-7777-6666-555555555555"),
        logged_at=datetime.now(UTC),
        status="VALID",
        purposes_granted=["DOCUMENT_OCR"],
    )
    with patch(
        "app.services.auth_service.record_consent",
        new=AsyncMock(return_value=mock_consent),
    ):
        response = client.post(
            "/api/v1/auth/consent",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={
                "terms_accepted": True,
                "privacy_policy_accepted": True,
                "transcript_processing_accepted": True,
                "policy_version": "2026.1",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "VALID"
        assert "DOCUMENT_OCR" in data["purposes_granted"]
