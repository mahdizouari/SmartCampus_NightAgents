"""QA automated tests for API Security, RBAC, and JWT attack vectors (SA-20, SA-28, SA-22).

Verifies role-based access control, forbidden mutations for non-admins, token tampering,
expired credentials, and input validation boundaries.
"""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status

from app.core.database import get_db
from app.core.deps import (
    _b64url_encode,
    create_access_token,
    create_refresh_token,
    encode_hs256_jwt,
)
from app.main import app
from app.schemas.catalog_management import SchoolResponse


@pytest.fixture(autouse=True)
def _mock_db_dependency():
    """Ensure security test mutations never touch external databases."""

    async def _dummy_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _dummy_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


def test_catalog_admin_mutation_unauthenticated_fails(client):
    """POST /api/v1/catalog/schools without Authorization header must return 401."""
    payload = {
        "code": "iit",
        "name": "New School Test",
        "is_extension": False,
        "is_active": True,
    }
    response = client.post("/api/v1/catalog/schools", json=payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_catalog_admin_mutation_student_role_forbidden(client):
    """POST /api/v1/catalog/schools with 'student' role must be rejected with 403."""
    student_token = create_access_token(
        account_id="00000000-0000-0000-0000-000000000001",
        role="student",
        email="etudiant@smartcampus.tn",
    )
    payload = {
        "code": "iit",
        "name": "Unauthorized School Mutation",
        "is_extension": False,
        "is_active": True,
    }
    response = client.post(
        "/api/v1/catalog/schools",
        headers={"Authorization": f"Bearer {student_token}"},
        json=payload,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "PLATFORM_ADMIN" in response.json()["detail"]


def test_catalog_admin_mutation_responsable_role_forbidden(client):
    """POST /api/v1/catalog/schools with 'responsable' role must be rejected with 403."""
    responsable_token = create_access_token(
        account_id="00000000-0000-0000-0000-000000000002",
        role="responsable",
        school_scope="iit",
        email="responsable.iit@smartcampus.tn",
    )
    payload = {
        "code": "iit",
        "name": "Unauthorized School Mutation",
        "is_extension": False,
        "is_active": True,
    }
    response = client.post(
        "/api/v1/catalog/schools",
        headers={"Authorization": f"Bearer {responsable_token}"},
        json=payload,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_catalog_admin_mutation_admin_role_allowed(client):
    """POST /api/v1/catalog/schools with 'admin' role passes RBAC gate."""
    admin_token = create_access_token(
        account_id="00000000-0000-0000-0000-000000000099",
        role="admin",
        email="admin@smartcampus.tn",
    )
    payload = {
        "code": "iit",
        "name": "Institut International de Technologie",
        "is_extension": False,
        "is_active": True,
    }
    mock_school_resp = SchoolResponse(
        code="iit",
        name="Institut International de Technologie",
        is_extension=False,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with patch(
        "app.services.catalog_management_service.create_school",
        new=AsyncMock(return_value=mock_school_resp),
    ):
        response = client.post(
            "/api/v1/catalog/schools",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert response.status_code == status.HTTP_201_CREATED


def test_program_creation_student_forbidden(client):
    """POST /api/v1/catalog/programs by a student returns 403 Forbidden."""
    student_token = create_access_token(
        account_id="00000000-0000-0000-0000-000000000001",
        role="student",
    )
    program_payload = {
        "code": "IIT-HACKED",
        "school_code": "iit",
        "title": "Malicious Program Creation",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Diplôme",
        "duration_years": 3,
        "source_document": "fake.pdf",
    }
    response = client.post(
        "/api/v1/catalog/programs",
        headers={"Authorization": f"Bearer {student_token}"},
        json=program_payload,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_jwt_tampered_signature_rejected(client):
    """Token with altered payload or signature returns 401 Unauthorized."""
    valid_token = create_access_token(
        account_id="11111111-2222-3333-4444-555555555555",
        role="admin",
    )
    header, payload, sig = valid_token.split(".")
    # Tamper signature by changing the last characters
    corrupted_sig = sig[:-4] + "AAAA"
    tampered_token = f"{header}.{payload}.{corrupted_sig}"

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Invalid or malformed" in response.json()["detail"]


def test_jwt_expired_token_rejected(client):
    """Token with exp timestamp in the past returns 401 Unauthorized."""
    expired_token = create_access_token(
        account_id="11111111-2222-3333-4444-555555555555",
        role="admin",
        expires_in_seconds=-3600,  # Expired 1 hour ago
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in response.json()["detail"].lower()


def test_jwt_alg_none_attack_rejected(client):
    """Token attempting 'alg': 'none' security bypass must be rejected with 401."""
    header_none = _b64url_encode(b'{"alg":"none","typ":"JWT"}')
    payload = _b64url_encode(b'{"sub":"admin-id","role":"admin","type":"access"}')
    fake_token = f"{header_none}.{payload}."

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {fake_token}"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_jwt_refresh_token_used_on_access_endpoint_rejected(client):
    """Passing a refresh token to an access-token guarded endpoint must return 401."""
    refresh_token = create_refresh_token(
        account_id="11111111-2222-3333-4444-555555555555",
        role="student",
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Expected 'access' token" in response.json()["detail"]


def test_jwt_missing_required_claims_rejected(client):
    """Token signed properly but lacking 'role' or 'sub' claim must return 401."""
    # Craft token with missing 'role'
    now = int(time.time())
    token_missing_role = encode_hs256_jwt(
        {"sub": "account-123", "type": "access", "iat": now, "exp": now + 3600}
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_missing_role}"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "missing required" in response.json()["detail"].lower()


def test_malformed_auth_header_rejected(client):
    """Authorization header with wrong scheme (Basic, Token, etc.) returns 401."""
    # Basic scheme
    resp_basic = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp_basic.status_code == status.HTTP_401_UNAUTHORIZED

    # Empty bearer
    resp_empty = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer "},
    )
    assert resp_empty.status_code == status.HTTP_401_UNAUTHORIZED
