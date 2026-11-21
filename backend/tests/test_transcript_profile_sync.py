"""Unit and endpoint tests for Bonus-2: VLM Transcript JSON -> PostgreSQL Profile Sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import create_access_token
from app.main import app
from app.models.users import (
    AuditLog,
    Candidate,
    CandidateDocument,
    CandidateProfile,
    DeclaredGrade,
)

SAMPLE_BAC_JPG = (
    Path(__file__).parent / "data" / "sample_bac" / "releve_bac_math_sfax_anonymized.jpg"
)


class _FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeResult:
    def __init__(self, items: list[Any]):
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)


class FakeTranscriptProfileAsyncSession:
    """In-memory AsyncSession double for Candidate, Profile, Document, DeclaredGrade & AuditLog."""

    def __init__(self) -> None:
        self.candidates: dict[UUID, Candidate] = {}
        self.profiles: dict[UUID, CandidateProfile] = {}
        self.documents: dict[UUID, CandidateDocument] = {}
        self.grades: dict[UUID, DeclaredGrade] = {}
        self.audit_logs: list[AuditLog] = []
        self.committed = False

    async def get(self, model: type[Any], pk: Any) -> Any:
        if model is Candidate:
            return self.candidates.get(pk)
        if model is CandidateProfile:
            return self.profiles.get(pk)
        if model is CandidateDocument:
            return self.documents.get(pk)
        return None

    def add(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, Candidate):
            if obj.id is None:
                obj.id = uuid4()
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.candidates[obj.id] = obj
        elif isinstance(obj, CandidateProfile):
            obj.updated_at = obj.updated_at or now
            self.profiles[obj.candidate_id] = obj
        elif isinstance(obj, CandidateDocument):
            if obj.id is None:
                obj.id = uuid4()
            obj.uploaded_at = obj.uploaded_at or now
            self.documents[obj.id] = obj
        elif isinstance(obj, DeclaredGrade):
            if obj.id is None:
                obj.id = uuid4()
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.grades[obj.id] = obj
        elif isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = uuid4()
            obj.created_at = obj.created_at or now
            self.audit_logs.append(obj)

    async def delete(self, obj: Any) -> None:
        if isinstance(obj, DeclaredGrade) and obj.id in self.grades:
            del self.grades[obj.id]

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None

    async def execute(self, statement: Any) -> _FakeResult:
        compiled = statement.compile()
        params = compiled.params
        sql_str = str(compiled)

        if "core.candidate_profile" in sql_str:
            cid = next((v for k, v in params.items() if "candidate_id" in k), None)
            item = self.profiles.get(cid)
            return _FakeResult([item] if item is not None else [])

        if "core.document" in sql_str:
            cid = next((v for k, v in params.items() if "candidate_id" in k), None)
            items = [d for d in self.documents.values() if d.candidate_id == cid]
            return _FakeResult(items)

        if "core.declared_grades" in sql_str:
            cid = next((v for k, v in params.items() if "candidate_id" in k), None)
            stage = next((v for k, v in params.items() if "diploma_stage" in k), None)
            items = [
                g
                for g in self.grades.values()
                if g.candidate_id == cid and (stage is None or g.diploma_stage == stage)
            ]
            return _FakeResult(items)

        if "core.candidate" in sql_str:
            cid = next((v for k, v in params.items() if "id" in k), None)
            item = self.candidates.get(cid)
            return _FakeResult([item] if item is not None else [])

        return _FakeResult([])


def _sample_bac_extraction_dict(
    *,
    math_conf: float = 0.97,
    avg_conf: float = 0.98,
    discrepancy_detected: bool = False,
    verification_status: str = "PENDING",
) -> dict[str, Any]:
    return {
        "document_type": "RELEVE_BAC",
        "diploma_stage": "BAC",
        "bac_section": "Mathématiques",
        "program_title": None,
        "session_year": "2021",
        "session_type": "Principale",
        "student_name": None,
        "institution": None,
        "governorate": "Sfax",
        "general_average": 11.51,
        "calculated_average": 11.51,
        "declared_average": 15.0 if discrepancy_detected else None,
        "average_discrepancy": 3.49 if discrepancy_detected else 0.0,
        "discrepancy_detected": discrepancy_detected,
        "credits_acquired": None,
        "mention": "Passable",
        "jury_decision": "Admis",
        "subjects": [
            {
                "name": "Mathématiques",
                "grade": 9.25,
                "grade_scale": 20.0,
                "coefficient": 4.0,
                "is_optional": False,
                "confidence": math_conf,
                "needs_confirmation": math_conf < 0.85,
            },
            {
                "name": "Sciences Physiques",
                "grade": 11.5,
                "grade_scale": 20.0,
                "coefficient": 4.0,
                "is_optional": False,
                "confidence": 0.96,
                "needs_confirmation": False,
            },
            {
                "name": "Informatique",
                "grade": 17.0,
                "grade_scale": 20.0,
                "coefficient": 1.0,
                "is_optional": False,
                "confidence": 0.98,
                "needs_confirmation": False,
            },
        ],
        "field_confidences": {
            "bac_section": 0.99,
            "session_year": 0.99,
            "general_average": avg_conf,
        },
        "overall_confidence": 0.97,
        "min_confidence": min(math_conf, avg_conf, 0.96),
        "low_confidence_fields": (
            ["subject:Mathématiques"] if math_conf < 0.85 else []
        ),
        "needs_manual_review": discrepancy_detected or math_conf < 0.85 or avg_conf < 0.85,
        "verification_status": verification_status,
        "extraction_source": "vlm_live",
        "model_used": "qwen/qwen3.8-27b",
        "file_name": "releve_bac_math_sfax_anonymized.jpg",
        "content_type": "image/jpeg",
        "file_size_bytes": 203644,
        "latency_ms": 1420,
        "warnings": [],
    }


def _seed_candidate(fake_db: FakeTranscriptProfileAsyncSession) -> tuple[UUID, UUID]:
    acc_id = uuid4()
    cand_id = uuid4()
    fake_db.add(
        Candidate(
            id=cand_id,
            account_id=acc_id,
            candidate_number=f"CAND-2026-{str(cand_id)[:6].upper()}",
            email="student.bonus2@smartcampus.tn",
            status="AUTHENTICATED",
            owner="JARVIS",
        )
    )
    fake_db.add(
        CandidateProfile(
            candidate_id=cand_id,
            facts={"preferred_tracks": ["IIT-ING-GLID"]},
            preferred_language="FR",
            summary="Orientation chat initialized.",
        )
    )
    return acc_id, cand_id


@pytest.fixture
def fake_db():
    db = FakeTranscriptProfileAsyncSession()

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    yield db
    app.dependency_overrides.pop(get_db, None)


def test_sync_transcript_enforces_rbac_and_prevents_student_idor(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
):
    """Anonymous (401), direction (403), and cross-candidate student IDOR (403) are blocked."""
    acc_id, cand_id = _seed_candidate(fake_db)
    other_cand_id = uuid4()
    payload = {"extraction": _sample_bac_extraction_dict()}

    # 1. Anonymous visitor -> 401 Unauthorized (Give or Keep rule)
    r_anon = client.post("/api/v1/documents/sync-transcript-profile", json=payload)
    assert r_anon.status_code == 401

    # 2. Direction role -> 403 Forbidden
    dir_token = create_access_token(account_id=str(uuid4()), role="direction")
    r_dir = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {dir_token}"},
        json=payload,
    )
    assert r_dir.status_code == 403

    # 3. Student attempting to overwrite another candidate's profile -> 403 Forbidden
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )
    r_idor = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={**payload, "candidate_id": str(other_cand_id)},
    )
    assert r_idor.status_code == 403


def test_sync_transcript_pydantic_validation_rejects_invalid_payload(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
):
    """Pydantic rejects out-of-range grades (>20) or invalid confidence (>1.0) with 422."""
    acc_id, cand_id = _seed_candidate(fake_db)
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )

    bad_extraction = _sample_bac_extraction_dict()
    bad_extraction["subjects"][0]["grade"] = 24.5  # Exceeds 20.0 scale

    resp = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={"extraction": bad_extraction},
    )
    assert resp.status_code == 422


def test_sync_high_confidence_bac_updates_profile_grades_and_audit(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
):
    """High-confidence Bac extraction persists document, profile, grades, and PII-free audit."""
    acc_id, cand_id = _seed_candidate(fake_db)
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )

    resp = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={
            "extraction": _sample_bac_extraction_dict(),
            "confirmed_by_student": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(cand_id)
    assert body["document_type"] == "RELEVE_BAC"
    assert body["diploma_stage"] == "BAC"
    assert body["verification_status"] == "CONFIRMED_BY_STUDENT"
    assert body["current_education_level"] == "BAC"
    assert body["current_specialty"] == "Mathématiques"
    assert body["declared_overall_avg"] == pytest.approx(11.51)
    assert body["grades_persisted_count"] == 3
    assert body["needs_manual_review"] is False
    assert body["low_confidence_flags"] == []
    assert all(g["entry_source"] == "VLM_CONFIRMED" for g in body["grades"])

    # Verify PostgreSQL models were updated and prior facts preserved
    assert fake_db.committed is True
    profile = fake_db.profiles[cand_id]
    assert profile.facts["preferred_tracks"] == ["IIT-ING-GLID"]
    assert profile.facts["transcript_extraction"]["bac_section"] == "Mathématiques"
    assert len(fake_db.documents) == 1
    assert len(fake_db.grades) == 3
    assert len(fake_db.audit_logs) == 1
    assert fake_db.audit_logs[0].action == "TRANSCRIPT_PROFILE_SYNCED"

    # Verify GET /my-transcript-profile reads persisted data cleanly
    r_get = client.get(
        "/api/v1/documents/my-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert r_get.status_code == 200
    prof_body = r_get.json()
    assert prof_body["current_specialty"] == "Mathématiques"
    assert len(prof_body["documents"]) == 1
    assert len(prof_body["grades"]) == 3


def test_sync_flags_low_confidence_fields_and_average_mismatch_for_confirmation(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
):
    """Low-confidence subjects (<0.85) and MISMATCH_RH are flagged even on student confirm."""
    acc_id, cand_id = _seed_candidate(fake_db)
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )

    resp = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={
            "extraction": _sample_bac_extraction_dict(
                math_conf=0.62,
                avg_conf=0.71,
                discrepancy_detected=True,
                verification_status="MISMATCH_RH",
            ),
            "confirmed_by_student": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_status"] == "MISMATCH_RH"
    assert body["discrepancy_detected"] is True
    assert body["needs_manual_review"] is True

    flagged_fields = {f["field"] for f in body["low_confidence_flags"]}
    assert "general_average" in flagged_fields
    assert "average_discrepancy" in flagged_fields
    assert "subject:Mathématiques" in flagged_fields

    # Low-confidence Math grade must remain unconfirmed (CANDIDATE_FORM) with needs_confirmation
    math_grade = next(g for g in body["grades"] if g["subject_name"] == "Mathématiques")
    assert math_grade["needs_confirmation"] is True
    assert math_grade["entry_source"] == "CANDIDATE_FORM"


def test_sync_is_idempotent_on_stage_reupload_and_supports_university_l3(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
):
    """Re-syncing the same stage replaces prior VLM grades without creating duplicates."""
    acc_id, cand_id = _seed_candidate(fake_db)
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )

    # First sync: Bac Math (3 grades)
    r1 = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={"extraction": _sample_bac_extraction_dict()},
    )
    assert r1.status_code == 200
    assert len(fake_db.grades) == 3

    # Second sync: re-upload Bac Math -> still 3 grades (old VLM BAC grades replaced)
    r2 = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={"extraction": _sample_bac_extraction_dict()},
    )
    assert r2.status_code == 200
    assert len(fake_db.grades) == 3

    # Third sync: University L3 transcript (different diploma_stage -> adds alongside BAC)
    l3_payload = _sample_bac_extraction_dict()
    l3_payload["document_type"] = "RELEVE_UNIVERSITAIRE"
    l3_payload["diploma_stage"] = "L3"
    l3_payload["bac_section"] = None
    l3_payload["program_title"] = "Licence Informatique : Big Data et Analyse de Données"
    l3_payload["general_average"] = 12.98
    l3_payload["calculated_average"] = 12.98
    l3_payload["subjects"] = [
        {
            "name": "Stage de fin d'études",
            "grade": 15.87,
            "grade_scale": 20.0,
            "coefficient": 15.0,
            "is_optional": False,
            "confidence": 0.97,
            "needs_confirmation": False,
        }
    ]
    r3 = client.post(
        "/api/v1/documents/sync-transcript-profile",
        headers={"Authorization": f"Bearer {stu_token}"},
        json={"extraction": l3_payload},
    )
    assert r3.status_code == 200
    b3 = r3.json()
    assert b3["current_education_level"] == "L3"
    assert "Big Data" in (b3["current_specialty"] or "")
    assert len(fake_db.grades) == 4  # 3 BAC + 1 L3


def test_one_shot_extract_and_sync_endpoint_with_real_anonymized_bac_fixture(
    client: TestClient,
    fake_db: FakeTranscriptProfileAsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end Bonus-1 + Bonus-2: upload anonymized Sfax Bac JPG -> VLM -> PostgreSQL sync."""
    acc_id, cand_id = _seed_candidate(fake_db)
    stu_token = create_access_token(
        account_id=str(acc_id),
        role="student",
        candidate_id=str(cand_id),
    )

    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    monkeypatch.setattr(settings, "VLM_MODEL", "qwen/qwen3.8-27b")

    vlm_json = {
        "document_type": "RELEVE_BAC",
        "diploma_stage": "BAC",
        "bac_section": "الرياضيات",
        "session_year": "2021",
        "session_type": "Principale",
        "student_name": None,
        "governorate": "صفاقس",
        "general_average": "11,51",
        "mention": "متوسط",
        "jury_decision": "Admis",
        "subjects": [
            {"name": "رياضيات", "grade": "09,25", "confidence": 0.97},
            {"name": "علوم فيزيائية", "grade": "11,50", "confidence": 0.72},
        ],
        "field_confidences": {
            "bac_section": 0.99,
            "session_year": 0.99,
            "general_average": 0.98,
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen/qwen3.8-27b",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(vlm_json)}}],
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        resp = client.post(
            "/api/v1/documents/extract-and-sync-transcript",
            headers={"Authorization": f"Bearer {stu_token}"},
            files={
                "file": (
                    SAMPLE_BAC_JPG.name,
                    SAMPLE_BAC_JPG.read_bytes(),
                    "image/jpeg",
                )
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_id"] == str(cand_id)
    assert body["current_specialty"] == "Mathématiques"
    assert body["grades_persisted_count"] == 2
    assert body["needs_manual_review"] is True
    assert any(f["field"] == "subject:Sciences Physiques" for f in body["low_confidence_flags"])
    assert len(fake_db.documents) == 1
    assert len(fake_db.grades) == 2
