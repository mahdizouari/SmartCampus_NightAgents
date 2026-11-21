"""Automated unit and integration tests for Bonus-1 / SA-42 VLM Bac Transcript Extraction."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import create_access_token
from app.services.vlm_transcript_service import (
    compute_tunisian_bac_average,
    normalize_bac_section,
)

SAMPLE_BAC_DIR = Path(__file__).parent / "data" / "sample_bac"
MATH_PDF = SAMPLE_BAC_DIR / "releve_bac_math_sfax_2026.pdf"
INFO_PDF = SAMPLE_BAC_DIR / "releve_bac_info_sfax_2026.pdf"
REAL_BAC_JPG = SAMPLE_BAC_DIR / "releve_bac_math_sfax_anonymized.jpg"


def _student_headers() -> dict[str, str]:
    token = create_access_token(account_id="acc-stu-01", role="student", candidate_id="cand-01")
    return {"Authorization": f"Bearer {token}"}


def test_vlm_health_endpoint(client: TestClient):
    """Verify GET /api/v1/documents/vlm/health exposes VLM model and upload constraints."""
    resp = client.get("/api/v1/documents/vlm/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == settings.VLM_MODEL
    assert data["confidence_threshold"] == pytest.approx(0.85)
    assert "application/pdf" in data["supported_mime_types"]
    assert "image/png" in data["supported_mime_types"]
    assert data["status"] in {"ready", "local_pdf_fallback", "misconfigured"}


def test_extract_transcript_enforces_give_or_keep_rbac(client: TestClient):
    """Verify anonymous guests (401) and non-authorized roles (403) cannot upload transcripts."""
    pdf_bytes = MATH_PDF.read_bytes()

    # 1. Anonymous guest -> 401 Unauthorized (Give or Keep rule)
    r_anon = client.post(
        "/api/v1/documents/extract-bac-transcript",
        files={"file": ("releve.pdf", pdf_bytes, "application/pdf")},
    )
    assert r_anon.status_code == 401

    # 2. Direction role (read-only KPI role) -> 403 Forbidden
    dir_jwt = create_access_token(account_id="acc-dir-01", role="direction")
    r_dir = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers={"Authorization": f"Bearer {dir_jwt}"},
        files={"file": ("releve.pdf", pdf_bytes, "application/pdf")},
    )
    assert r_dir.status_code == 403


def test_extract_real_sfax_bac_math_and_info_pdfs(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify real pypdf extraction on anonymized Sfax Bac Math & Info PDF fixtures."""
    monkeypatch.setattr(settings, "VLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "VLM_FALLBACK_TO_LOCAL_PARSER", True)

    # 1. Lycee Pilote de Sfax — Bac Mathematiques (16.80)
    r_math = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=_student_headers(),
        files={"file": (MATH_PDF.name, MATH_PDF.read_bytes(), "application/pdf")},
        data={"declared_average": "16.80"},
    )
    assert r_math.status_code == 200
    bm = r_math.json()
    assert bm["document_type"] == "RELEVE_BAC"
    assert bm["diploma_stage"] == "BAC"
    assert bm["bac_section"] == "Mathématiques"
    assert bm["session_year"] == "2026"
    assert bm["governorate"] == "Sfax"
    assert bm["institution"] == "Lycee Pilote de Sfax"
    assert len(bm["subjects"]) == 9
    assert bm["general_average"] == pytest.approx(16.80)
    assert bm["calculated_average"] == pytest.approx(16.80)
    assert bm["average_discrepancy"] == pytest.approx(0.0)
    assert bm["discrepancy_detected"] is False
    assert bm["min_confidence"] >= 0.95
    assert bm["low_confidence_fields"] == []
    assert bm["needs_manual_review"] is False
    assert bm["verification_status"] == "PENDING"
    assert bm["extraction_source"] == "pdf_text_parser"

    # 2. Lycee Hedi Chaker Sfax — Bac Sciences de l'Informatique (15.45)
    r_info = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=_student_headers(),
        files={"file": (INFO_PDF.name, INFO_PDF.read_bytes(), "application/pdf")},
    )
    assert r_info.status_code == 200
    bi = r_info.json()
    assert bi["bac_section"] == "Sciences de l'Informatique"
    assert bi["institution"] == "Lycee Hedi Chaker Sfax"
    assert len(bi["subjects"]) == 9
    assert bi["general_average"] == pytest.approx(15.45)
    assert bi["calculated_average"] == pytest.approx(15.45)
    assert bi["discrepancy_detected"] is False
    assert bi["needs_manual_review"] is False


def test_live_vlm_image_extraction_on_real_anonymized_tunisian_bac_jpg(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify Arabic Tunisian Bac extraction without printed coeffs (exact 172.66/15 = 11.51)."""
    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    monkeypatch.setattr(settings, "VLM_MODEL", "qwen/qwen3.8-27b")

    # Mirrors exact official Arabic transcript (no coeff column, comma decimals, optional Spanish)
    vlm_json_payload = {
        "bac_section": "الرياضيات",
        "session_year": "2021",
        "session_type": "Principale",
        "student_name": None,
        "institution": None,
        "governorate": "صفاقس",
        "general_average": "11,51",
        "mention": "متوسط",
        "subjects": [
            {"name": "رياضيات", "grade": "09,25", "confidence": 0.97},
            {"name": "علوم فيزيائية", "grade": "11,50", "confidence": 0.96},
            {"name": "علوم الحياة والأرض", "grade": "12,50", "confidence": 0.96},
            {"name": "انقليزية", "grade": "13,50", "confidence": 0.97},
            {"name": "فرنسية", "grade": "08,50", "confidence": 0.96},
            {"name": "عربية", "grade": "10,75", "confidence": 0.96},
            {"name": "فلسفة", "grade": "07,50", "confidence": 0.95},
            {"name": "إعلامية", "grade": "17,00", "confidence": 0.98},
            {"name": "تربية بدنية", "grade": "18,66", "confidence": 0.97},
            {"name": "إسبانية", "grade": "11,25", "confidence": 0.95},
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
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"```json\n{json.dumps(vlm_json_payload, ensure_ascii=False)}\n```",
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as m_post:
        resp = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=_student_headers(),
            files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_source"] == "vlm_live"
    assert body["model_used"] == "qwen/qwen3.8-27b"
    assert body["bac_section"] == "Mathématiques"
    assert body["mention"] == "Passable"
    assert len(body["subjects"]) == 10
    assert body["general_average"] == pytest.approx(11.51)
    assert body["calculated_average"] == pytest.approx(11.51)
    assert body["average_discrepancy"] == pytest.approx(0.0)
    assert body["discrepancy_detected"] is False
    assert body["min_confidence"] == pytest.approx(0.95)
    assert body["needs_manual_review"] is False

    # Verify auto-inferred official coefficients and optional Spanish flag
    by_name = {s["name"]: s for s in body["subjects"]}
    assert by_name["Mathématiques"]["coefficient"] == pytest.approx(4.0)
    assert by_name["Sciences Physiques"]["coefficient"] == pytest.approx(4.0)
    assert by_name["Espagnol (Option)"]["is_optional"] is True
    assert by_name["Espagnol (Option)"]["weighted_points"] == pytest.approx(1.25)

    # Verify multimodal base64 JPEG data URI was sent to VLM
    sent_json = m_post.call_args.kwargs["json"]
    assert sent_json["model"] == "qwen/qwen3.8-27b"
    user_parts = sent_json["messages"][1]["content"]
    image_parts = [p for p in user_parts if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_low_confidence_and_arithmetic_discrepancy_trigger_hitl_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify low-confidence fields (<0.85) and average mismatches trigger HITL review."""
    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")

    # Printed average (18.50) disagrees with weighted average of grades (12.00) + blurry Physics
    noisy_payload = {
        "bac_section": "Sciences Expérimentales",
        "session_year": "2026",
        "general_average": 18.50,
        "subjects": [
            {"name": "SVT", "grade": 12.0, "coefficient": 4.0, "confidence": 0.92},
            {"name": "Sciences Physiques", "grade": 12.0, "coefficient": 4.0, "confidence": 0.62},
        ],
        "field_confidences": {
            "bac_section": 0.91,
            "session_year": 0.95,
            "general_average": 0.72,
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "gpt-4o-mini",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(noisy_payload)}}],
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        resp = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=_student_headers(),
            files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["discrepancy_detected"] is True
    assert body["average_discrepancy"] == pytest.approx(6.50)
    assert body["verification_status"] == "MISMATCH_RH"
    assert body["needs_manual_review"] is True
    assert body["min_confidence"] == pytest.approx(0.62)
    assert "subject:Sciences Physiques" in body["low_confidence_fields"]
    assert "general_average" in body["low_confidence_fields"]
    assert any(s["needs_confirmation"] for s in body["subjects"])


def test_offline_raster_image_does_not_hallucinate_grades(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify offline raster image without VLM_API_KEY flags manual review with zero fake grades."""
    monkeypatch.setattr(settings, "VLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "VLM_FALLBACK_TO_LOCAL_PARSER", True)

    resp = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=_student_headers(),
        files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_source"] == "image_fallback"
    assert body["subjects"] == []
    assert body["needs_manual_review"] is True
    assert body["min_confidence"] == 0.0
    assert any("VLM_API_KEY" in w for w in body["warnings"])


def test_upload_validation_and_vlm_upstream_error_handling(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify magic-byte spoofing (415), empty (400), oversize (413), and VLM errors (504/502)."""
    headers = _student_headers()

    # 1. Empty file -> 400
    r_empty = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r_empty.status_code == 400

    # 2. Spoofed extension (plain text pretending to be PDF) -> 415
    r_spoof = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("fake.pdf", b"not a real pdf document", "application/pdf")},
    )
    assert r_spoof.status_code == 415

    # 3. Oversized file -> 413
    monkeypatch.setattr(settings, "VLM_MAX_FILE_SIZE_BYTES", 128)
    r_big = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("big.jpg", REAL_BAC_JPG.read_bytes(), "image/jpeg")},
    )
    assert r_big.status_code == 413
    monkeypatch.setattr(settings, "VLM_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024)

    # 4. VLM timeout -> 504 Gateway Timeout
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
        r_to = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=headers,
            files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
        )
    assert r_to.status_code == 504

    # 5. Malformed non-JSON VLM output -> 502 Bad Gateway
    bad_vlm = MagicMock()
    bad_vlm.status_code = 200
    bad_vlm.json.return_value = {"choices": [{"message": {"content": "not valid json"}}]}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=bad_vlm)):
        r_bad = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=headers,
            files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
        )
    assert r_bad.status_code == 502


def test_tunisian_bac_optional_subject_bonus_and_section_normalization():
    """Verify Tunisian Bac optional-subject (>10 bonus) rule and Arabic/French section mapping."""
    from app.schemas.transcript import ExtractedSubjectGrade

    assert normalize_bac_section("رياضيات") == "Mathématiques"
    assert normalize_bac_section("Sciences de l'Informatique") == "Sciences de l'Informatique"
    assert normalize_bac_section("Economie et Gestion") == "Économie et Gestion"

    # Mandatory: Math 14.0 (coeff 4) + Phys 16.0 (coeff 4) = 120 / 8 = 15.00
    # Optional Allemand: 14.0 (coeff 1) -> +4.0 bonus points -> 124 / 8 = 15.50
    subjects = [
        ExtractedSubjectGrade(name="Mathématiques", grade=14.0, coefficient=4.0, confidence=0.98),
        ExtractedSubjectGrade(
            name="Sciences Physiques", grade=16.0, coefficient=4.0, confidence=0.98
        ),
        ExtractedSubjectGrade(
            name="Option Allemand",
            grade=14.0,
            coefficient=1.0,
            is_optional=True,
            confidence=0.95,
        ),
    ]
    assert compute_tunisian_bac_average(subjects) == pytest.approx(15.50)
