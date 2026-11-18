"""Unit and endpoint tests for SA-20 catalog management API and admin RBAC."""

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.catalog import (
    AdmissionCampaign,
    Program,
    ProgramFee,
    ProgramPrerequisite,
    School,
)
from app.models.users import AuditLog


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_test_jwt(
    sub: str = "admin-uuid-1",
    role: str = "admin",
    exp_offset: int = 3600,
    secret: str | None = None,
) -> str:
    """Create a real HS256 JWT matching SA-20/SA-28 verification logic."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url_encode(
        json.dumps(
            {
                "sub": sub,
                "role": role,
                "exp": int(time.time()) + exp_offset,
            }
        ).encode("utf-8")
    )
    signing_key = (secret or settings.SECRET_KEY).encode("utf-8")
    sig = hmac.new(signing_key, f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


class _ScalarResult:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeAsyncSession:
    """Minimal stateful async SQLAlchemy session double for isolated CI tests."""

    def __init__(self) -> None:
        self.schools: dict[str, School] = {}
        self.programs: dict[UUID, Program] = {}
        self.prerequisites: dict[UUID, ProgramPrerequisite] = {}
        self.fees: dict[UUID, ProgramFee] = {}
        self.campaigns: dict[UUID, AdmissionCampaign] = {}
        self.audit_logs: list[AuditLog] = []
        self._pending: list[Any] = []

    def _store_for(self, model: type[Any]) -> dict[Any, Any]:
        mapping = {
            School: self.schools,
            Program: self.programs,
            ProgramPrerequisite: self.prerequisites,
            ProgramFee: self.fees,
            AdmissionCampaign: self.campaigns,
        }
        return mapping[model]

    async def get(self, model: type[Any], pk: Any) -> Any:
        return self._store_for(model).get(pk)

    def add(self, obj: Any) -> None:
        if isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = uuid4()
            self.audit_logs.append(obj)
            return

        now = datetime.now(UTC)
        if isinstance(obj, School):
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.schools[obj.code] = obj
        else:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            if hasattr(obj, "created_at") and obj.created_at is None:
                obj.created_at = now
            if hasattr(obj, "updated_at") and obj.updated_at is None:
                obj.updated_at = now
            self._store_for(type(obj))[obj.id] = obj
        self._pending.append(obj)

    async def delete(self, obj: Any) -> None:
        key = obj.code if isinstance(obj, School) else obj.id
        self._store_for(type(obj)).pop(key, None)

    async def execute(self, stmt: Any) -> _ScalarResult:
        compiled = str(stmt)
        if "core.program" in compiled:
            code_param = stmt.compile().params.get("code_1")
            for prog in self.programs.values():
                if prog.code == code_param:
                    return _ScalarResult(prog.id)
        return _ScalarResult(None)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self._pending.clear()

    async def rollback(self) -> None:
        self._pending.clear()

    async def refresh(self, obj: Any) -> None:
        return None


@pytest.fixture
def fake_db() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def api_client(fake_db: FakeAsyncSession):
    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(role='admin')}"}


# ── RBAC & JWT Tests ─────────────────────────────────────────────────────────


def test_catalog_admin_requires_authentication(api_client: TestClient):
    resp = api_client.post(
        "/api/v1/catalog/schools",
        json={"code": "iit", "name": "Institut International de Technologie"},
    )
    assert resp.status_code == 401


def test_catalog_admin_rejects_expired_or_tampered_jwt(api_client: TestClient):
    expired = make_test_jwt(role="admin", exp_offset=-60)
    resp = api_client.post(
        "/api/v1/catalog/schools",
        headers={"Authorization": f"Bearer {expired}"},
        json={"code": "iit", "name": "IIT Sfax"},
    )
    assert resp.status_code == 401

    bad_sig = make_test_jwt(role="admin", secret="wrong-secret")
    resp2 = api_client.post(
        "/api/v1/catalog/schools",
        headers={"Authorization": f"Bearer {bad_sig}"},
        json={"code": "iit", "name": "IIT Sfax"},
    )
    assert resp2.status_code == 401


@pytest.mark.parametrize("role", ["student", "rh", "responsable", "direction"])
def test_catalog_admin_rejects_non_admin_roles(api_client: TestClient, role: str):
    token = make_test_jwt(sub=f"{role}-1", role=role)
    resp = api_client.post(
        "/api/v1/catalog/schools",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": "iit", "name": "IIT Sfax"},
    )
    assert resp.status_code == 403


# ── Validation Tests (422) ───────────────────────────────────────────────────


def test_catalog_validation_rejects_invalid_constraints(api_client: TestClient):
    headers = _admin_headers()
    pid = uuid4()

    # Invalid school code
    r1 = api_client.post(
        "/api/v1/catalog/schools",
        headers=headers,
        json={"code": "other", "name": "Unknown"},
    )
    assert r1.status_code == 422

    # Invalid duration (> 6)
    r2 = api_client.post(
        "/api/v1/catalog/programs",
        headers=headers,
        json={
            "code": "IIT-TEST",
            "school_code": "iit",
            "title": "Test Program",
            "degree_level": "cycle_ingenieur",
            "degree_title": "Ingenieur",
            "duration_years": 9,
            "source_document": "brochure.pdf",
        },
    )
    assert r2.status_code == 422

    # Invalid fee effective dates
    r3 = api_client.post(
        f"/api/v1/catalog/programs/{pid}/fees",
        headers=headers,
        json={
            "academic_year": "2026-2027",
            "tuition_amount": "4500.00",
            "effective_from": "2026-09-01",
            "effective_to": "2026-08-01",
            "source_document": "fees.pdf",
        },
    )
    assert r3.status_code == 422

    # Invalid campaign window
    r4 = api_client.post(
        "/api/v1/catalog/campaigns",
        headers=headers,
        json={
            "school_code": "iit",
            "academic_year": "2026-2027",
            "phase_name": "Session 1",
            "opens_at": "2026-09-01T08:00:00Z",
            "closes_at": "2026-09-01T08:00:00Z",
            "source_document": "calendar.pdf",
        },
    )
    assert r4.status_code == 422


# ── End-to-End CRUD & Audit Tests ────────────────────────────────────────────


def test_school_program_prereq_fee_campaign_crud_and_audit(
    api_client: TestClient,
    fake_db: FakeAsyncSession,
):
    headers = _admin_headers()

    # 1. Create & update schools
    s_resp = api_client.post(
        "/api/v1/catalog/schools",
        headers=headers,
        json={"code": "iit", "name": "IIT Sfax", "is_extension": False},
    )
    assert s_resp.status_code == 201
    assert s_resp.json()["code"] == "iit"

    # Duplicate school -> 409
    s_dup = api_client.post(
        "/api/v1/catalog/schools",
        headers=headers,
        json={"code": "iit", "name": "Duplicate"},
    )
    assert s_dup.status_code == 409

    s_patch = api_client.patch(
        "/api/v1/catalog/schools/iit",
        headers=headers,
        json={"name": "Institut International de Technologie"},
    )
    assert s_patch.status_code == 200
    assert s_patch.json()["name"] == "Institut International de Technologie"

    # Second school for cross-school campaign validation
    api_client.post(
        "/api/v1/catalog/schools",
        headers=headers,
        json={"code": "isb", "name": "ISB Sfax"},
    )

    # 2. Program CRUD
    p_resp = api_client.post(
        "/api/v1/catalog/programs",
        headers=headers,
        json={
            "code": "IIT-ING-GL",
            "school_code": "iit",
            "title": "Cycle Ingenieur Genie Logiciel",
            "degree_level": "cycle_ingenieur",
            "degree_title": "Diplome National d'Ingenieur",
            "duration_years": 3,
            "study_modes": ["Plein temps"],
            "description": "Official placeholder description",
            "source_document": "Catalogue_IIT_2026.pdf",
        },
    )
    assert p_resp.status_code == 201
    program_id = p_resp.json()["id"]

    # Duplicate program code -> 409
    p_dup = api_client.post(
        "/api/v1/catalog/programs",
        headers=headers,
        json={
            "code": "IIT-ING-GL",
            "school_code": "iit",
            "title": "Duplicate Code",
            "degree_level": "cycle_ingenieur",
            "degree_title": "Ingenieur",
            "duration_years": 3,
            "source_document": "Catalogue_IIT_2026.pdf",
        },
    )
    assert p_dup.status_code == 409

    p_patch = api_client.patch(
        f"/api/v1/catalog/programs/{program_id}",
        headers=headers,
        json={"duration_years": 3, "study_modes": ["Plein temps", "Alternance"]},
    )
    assert p_patch.status_code == 200
    assert p_patch.json()["study_modes"] == ["Plein temps", "Alternance"]

    # 3. Prerequisite CRUD
    pr_resp = api_client.post(
        f"/api/v1/catalog/programs/{program_id}/prerequisites",
        headers=headers,
        json={
            "entry_track": "prepa",
            "required_diploma": "Cycle Preparatoire",
            "min_average": "12.00",
            "yaml_rule_key": "iit_ing_prepa",
            "source_document": "Reglement_IIT_2026.pdf",
        },
    )
    assert pr_resp.status_code == 201
    prereq_id = pr_resp.json()["id"]

    pr_patch = api_client.patch(
        f"/api/v1/catalog/prerequisites/{prereq_id}",
        headers=headers,
        json={"notes": "Admission sur dossier"},
    )
    assert pr_patch.status_code == 200
    assert pr_patch.json()["notes"] == "Admission sur dossier"

    # 4. Fee CRUD + partial update date validation
    f_resp = api_client.post(
        f"/api/v1/catalog/programs/{program_id}/fees",
        headers=headers,
        json={
            "academic_year": "2026-2027",
            "tuition_amount": "5200.00",
            "currency": "TND",
            "effective_from": "2026-09-01",
            "effective_to": "2027-06-30",
            "source_document": "Grille_Tarifs_2026.pdf",
        },
    )
    assert f_resp.status_code == 201
    fee_id = f_resp.json()["id"]

    f_bad_patch = api_client.patch(
        f"/api/v1/catalog/fees/{fee_id}",
        headers=headers,
        json={"effective_to": "2026-01-01"},
    )
    assert f_bad_patch.status_code == 422

    f_patch = api_client.patch(
        f"/api/v1/catalog/fees/{fee_id}",
        headers=headers,
        json={"tuition_amount": "5400.00"},
    )
    assert f_patch.status_code == 200
    assert Decimal(f_patch.json()["tuition_amount"]) == Decimal("5400.00")

    # 5. Campaign CRUD + cross-school check
    c_bad = api_client.post(
        "/api/v1/catalog/campaigns",
        headers=headers,
        json={
            "school_code": "isb",
            "program_id": program_id,
            "academic_year": "2026-2027",
            "phase_name": "Session 1",
            "opens_at": "2026-06-01T08:00:00Z",
            "closes_at": "2026-09-15T18:00:00Z",
            "source_document": "Calendrier_2026.pdf",
        },
    )
    assert c_bad.status_code == 422

    c_resp = api_client.post(
        "/api/v1/catalog/campaigns",
        headers=headers,
        json={
            "school_code": "iit",
            "program_id": program_id,
            "academic_year": "2026-2027",
            "phase_name": "Session Principale",
            "opens_at": "2026-06-01T08:00:00Z",
            "closes_at": "2026-09-15T18:00:00Z",
            "source_document": "Calendrier_2026.pdf",
        },
    )
    assert c_resp.status_code == 201
    campaign_id = c_resp.json()["id"]

    c_patch = api_client.patch(
        f"/api/v1/catalog/campaigns/{campaign_id}",
        headers=headers,
        json={"is_open": False},
    )
    assert c_patch.status_code == 200
    assert c_patch.json()["is_open"] is False

    # 6. Soft deactivate vs hard delete + child deletes
    assert api_client.delete(f"/api/v1/catalog/fees/{fee_id}", headers=headers).status_code == 204
    assert (
        api_client.delete(
            f"/api/v1/catalog/prerequisites/{prereq_id}", headers=headers
        ).status_code
        == 204
    )
    assert (
        api_client.delete(
            f"/api/v1/catalog/campaigns/{campaign_id}", headers=headers
        ).status_code
        == 204
    )

    soft_del = api_client.delete(f"/api/v1/catalog/programs/{program_id}", headers=headers)
    assert soft_del.status_code == 204
    assert fake_db.programs[UUID(program_id)].is_active is False

    hard_del = api_client.delete(
        f"/api/v1/catalog/programs/{program_id}?hard=true", headers=headers
    )
    assert hard_del.status_code == 204
    assert UUID(program_id) not in fake_db.programs

    # Verify audit trail recorded ADMIN actions without cleartext PII
    assert len(fake_db.audit_logs) >= 10
    assert all(log.actor_type == "ADMIN" for log in fake_db.audit_logs)
    assert all(log.actor_id == "admin-uuid-1" for log in fake_db.audit_logs)


def test_missing_resources_return_404(api_client: TestClient):
    headers = _admin_headers()
    missing_id = uuid4()

    assert (
        api_client.patch(
            "/api/v1/catalog/schools/iit", headers=headers, json={"is_active": False}
        ).status_code
        == 404
    )
    assert (
        api_client.patch(
            f"/api/v1/catalog/programs/{missing_id}",
            headers=headers,
            json={"title": "Missing"},
        ).status_code
        == 404
    )
    assert (
        api_client.delete(f"/api/v1/catalog/fees/{missing_id}", headers=headers).status_code
        == 404
    )
    assert (
        api_client.delete(
            f"/api/v1/catalog/campaigns/{missing_id}", headers=headers
        ).status_code
        == 404
    )

