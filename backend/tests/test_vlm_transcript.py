"""Automated tests for Bonus-1 / SA-42 VLM Tunisian Bac & Licence Transcript Extraction."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import create_access_token
from app.schemas.transcript import ExtractedSubjectGrade
from app.services.vlm_transcript_service import (
    compute_tunisian_bac_average,
    normalize_bac_section,
)

SAMPLE_DIR = Path(__file__).parent / "data" / "sample_bac"
REAL_BAC_JPG = SAMPLE_DIR / "releve_bac_math_sfax_anonymized.jpg"
REAL_BAC_PDF = SAMPLE_DIR / "releve_bac_math_sfax_anonymized.pdf"
REAL_LICENCE_ISIMS_PDF = SAMPLE_DIR / "releve_licence_l3_isims_sfax_anonymized.pdf"


def _student_headers() -> dict[str, str]:
    token = create_access_token(account_id="acc-stu-01", role="student", candidate_id="cand-01")
    return {"Authorization": f"Bearer {token}"}


def _build_in_memory_digital_pdf(lines: list[str]) -> bytes:
    """Build a minimal in-memory digital PDF with an embedded text layer for offline parser test."""
    cmds = ["BT", "/F1 11 Tf", "50 780 Td", "18 TL"]
    for idx, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        cmds.append(f"({esc}) Tj" if idx == 0 else f"T* ({esc}) Tj")
    cmds.append("ET")
    stream = "\n".join(cmds).encode("latin-1")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for o in objs:
        offsets.append(len(buf))
        buf.extend(o)
    xref = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(buf)


def test_vlm_health_endpoint(client: TestClient):
    """Verify GET /api/v1/documents/vlm/health exposes VLM model and upload constraints."""
    resp = client.get("/api/v1/documents/vlm/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == settings.VLM_MODEL
    assert data["confidence_threshold"] == pytest.approx(0.85)
    assert "application/pdf" in data["supported_mime_types"]
    assert "image/jpeg" in data["supported_mime_types"]
    assert data["status"] in {"ready", "local_pdf_fallback", "misconfigured"}


def test_extract_transcript_enforces_give_or_keep_rbac(client: TestClient):
    """Verify anonymous guests (401) and non-authorized roles (403) cannot upload transcripts."""
    pdf_bytes = REAL_BAC_PDF.read_bytes()

    r_anon = client.post(
        "/api/v1/documents/extract-bac-transcript",
        files={"file": (REAL_BAC_PDF.name, pdf_bytes, "application/pdf")},
    )
    assert r_anon.status_code == 401

    dir_jwt = create_access_token(account_id="acc-dir-01", role="direction")
    r_dir = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers={"Authorization": f"Bearer {dir_jwt}"},
        files={"file": (REAL_BAC_PDF.name, pdf_bytes, "application/pdf")},
    )
    assert r_dir.status_code == 403


def test_live_vlm_extraction_on_real_anonymized_tunisian_bac_jpg_and_pdf(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify Arabic Tunisian Bac extraction on real Sfax JPEG + scanned PDF (172.66/15 = 11.51)."""
    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    monkeypatch.setattr(settings, "VLM_MODEL", "qwen/qwen3.8-27b")

    # Mirrors exact official Arabic Sfax Bac (no printed coeffs, comma decimals, optional Spanish)
    vlm_json_payload = {
        "document_type": "RELEVE_BAC",
        "diploma_stage": "BAC",
        "bac_section": "الرياضيات",
        "session_year": "2021",
        "session_type": "Principale",
        "student_name": None,
        "institution": None,
        "governorate": "صفاقس",
        "general_average": "11,51",
        "mention": "متوسط",
        "jury_decision": "Admis",
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

    for fixture_path, mime in (
        (REAL_BAC_JPG, "image/jpeg"),
        (REAL_BAC_PDF, "application/pdf"),
    ):
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as m_post:
            resp = client.post(
                "/api/v1/documents/extract-bac-transcript",
                headers=_student_headers(),
                files={"file": (fixture_path.name, fixture_path.read_bytes(), mime)},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["document_type"] == "RELEVE_BAC"
        assert body["diploma_stage"] == "BAC"
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

        by_name = {s["name"]: s for s in body["subjects"]}
        assert by_name["Mathématiques"]["coefficient"] == pytest.approx(4.0)
        assert by_name["Sciences Physiques"]["coefficient"] == pytest.approx(4.0)
        assert by_name["Espagnol (Option)"]["is_optional"] is True
        assert by_name["Espagnol (Option)"]["weighted_points"] == pytest.approx(1.25)

        # Both raw JPEG and scanned PDF must send an image_url Base64 Data-URI to the Vision LLM
        sent_json = m_post.call_args.kwargs["json"]
        user_parts = sent_json["messages"][1]["content"]
        image_parts = [p for p in user_parts if p.get("type") == "image_url"]
        assert len(image_parts) == 1
        assert image_parts[0]["image_url"]["url"].startswith("data:image/")


def test_live_vlm_extraction_on_real_anonymized_isims_sfax_licence_l3_pdf(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify real scanned Université de Sfax (ISIMS) L3 PDF extraction (389.37/30 = 12.98)."""
    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    monkeypatch.setattr(settings, "VLM_MODEL", "qwen/qwen3.8-27b")

    isims_payload = {
        "document_type": "RELEVE_UNIVERSITAIRE",
        "diploma_stage": "L3",
        "bac_section": None,
        "program_title": (
            'Licence en Science de l\'Informatique "Computer Science" : '
            "Big Data et Analyse de Données"
        ),
        "session_year": "2023-2024",
        "session_type": "Rattrapage",
        "student_name": None,
        "institution": "Institut Supérieur d'Informatique et de Multimédia de Sfax (ISIMS)",
        "governorate": "Sfax",
        "general_average": 12.98,
        "credits_acquired": 53,
        "mention": "ASSEZ BIEN",
        "jury_decision": "Admis",
        "subjects": [
            {"name": "SI Décisionnels", "grade": 9.52, "coefficient": 3.0, "confidence": 0.95},
            {
                "name": "Technologies avancées Big Data",
                "grade": 10.19,
                "coefficient": 3.0,
                "confidence": 0.96,
            },
            {"name": "Unités transversales", "grade": 10.0, "coefficient": 3.0, "confidence": 0.97},
            {
                "name": "Techniques avancées Big Data",
                "grade": 11.85,
                "coefficient": 3.0,
                "confidence": 0.96,
            },
            {"name": "UOPS1-ADBD", "grade": 8.88, "coefficient": 3.0, "confidence": 0.95},
            {
                "name": "Stage de fin d'études",
                "grade": 15.87,
                "coefficient": 15.0,
                "credits_acquired": 30,
                "confidence": 0.98,
            },
        ],
        "field_confidences": {
            "bac_section": 0.95,
            "session_year": 0.99,
            "general_average": 0.99,
            "institution": 0.98,
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen/qwen3.8-27b",
        "choices": [
            {"message": {"role": "assistant", "content": json.dumps(isims_payload)}}
        ],
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as m_post:
        resp = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=_student_headers(),
            files={
                "file": (
                    REAL_LICENCE_ISIMS_PDF.name,
                    REAL_LICENCE_ISIMS_PDF.read_bytes(),
                    "application/pdf",
                )
            },
            data={"declared_average": "12.98"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_type"] == "RELEVE_UNIVERSITAIRE"
    assert body["diploma_stage"] == "L3"
    assert body["session_type"] == "Rattrapage"
    assert body["governorate"] == "Sfax"
    assert body["credits_acquired"] == pytest.approx(53.0)
    assert body["mention"] == "Assez Bien"
    assert body["jury_decision"] == "Admis"
    assert body["general_average"] == pytest.approx(12.98)
    assert body["calculated_average"] == pytest.approx(12.98)
    assert body["average_discrepancy"] == pytest.approx(0.0)
    assert body["discrepancy_detected"] is False
    assert body["needs_manual_review"] is False

    # Verify pypdf extracted the scanned image inside the ISIMS PDF for multimodal Vision API
    sent_json = m_post.call_args.kwargs["json"]
    user_parts = sent_json["messages"][1]["content"]
    assert any(p.get("type") == "image_url" for p in user_parts)


def test_offline_digital_pdf_parser_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify local pypdf text-layer parser works offline when a digital PDF is uploaded."""
    monkeypatch.setattr(settings, "VLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "VLM_FALLBACK_TO_LOCAL_PARSER", True)

    digital_pdf = _build_in_memory_digital_pdf(
        [
            "RELEVE DE NOTES - BACCALAUREAT",
            "Session Principale : 2026",
            "Gouvernorat : Sfax",
            "Section : Sciences de l'Informatique",
            "Algorithmique et Programmation | 3 | 17.00",
            "Mathematiques | 3 | 15.00",
            "Sciences de la Technologie | 3 | 16.00",
            "Sciences Physiques | 2 | 14.00",
            "Moyenne Generale : 15.64",
        ]
    )
    resp = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=_student_headers(),
        files={"file": ("digital_bac.pdf", digital_pdf, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_source"] == "pdf_text_parser"
    assert body["bac_section"] == "Sciences de l'Informatique"
    assert len(body["subjects"]) == 4
    assert body["general_average"] == pytest.approx(15.64)
    assert body["calculated_average"] == pytest.approx(15.64)
    assert body["discrepancy_detected"] is False


def test_low_confidence_and_arithmetic_discrepancy_trigger_hitl_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify low-confidence fields (<0.85) and average mismatches trigger HITL review."""
    monkeypatch.setattr(settings, "VLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")

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


def test_offline_raster_image_and_scanned_pdf_do_not_hallucinate_grades(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify offline raster image & scanned PDF without VLM_API_KEY never invent fake grades."""
    monkeypatch.setattr(settings, "VLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "VLM_FALLBACK_TO_LOCAL_PARSER", True)

    for fixture_path, mime in (
        (REAL_BAC_JPG, "image/jpeg"),
        (REAL_LICENCE_ISIMS_PDF, "application/pdf"),
    ):
        resp = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=_student_headers(),
            files={"file": (fixture_path.name, fixture_path.read_bytes(), mime)},
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

    r_empty = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r_empty.status_code == 400

    r_spoof = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("fake.pdf", b"not a real pdf document", "application/pdf")},
    )
    assert r_spoof.status_code == 415

    monkeypatch.setattr(settings, "VLM_MAX_FILE_SIZE_BYTES", 128)
    r_big = client.post(
        "/api/v1/documents/extract-bac-transcript",
        headers=headers,
        files={"file": ("big.jpg", REAL_BAC_JPG.read_bytes(), "image/jpeg")},
    )
    assert r_big.status_code == 413
    monkeypatch.setattr(settings, "VLM_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024)

    monkeypatch.setattr(settings, "VLM_API_KEY", "test-vlm-key")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
        r_to = client.post(
            "/api/v1/documents/extract-bac-transcript",
            headers=headers,
            files={"file": (REAL_BAC_JPG.name, REAL_BAC_JPG.read_bytes(), "image/jpeg")},
        )
    assert r_to.status_code == 504

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
    assert normalize_bac_section("رياضيات") == "Mathématiques"
    assert normalize_bac_section("Sciences de l'Informatique") == "Sciences de l'Informatique"
    assert normalize_bac_section("Economie et Gestion") == "Économie et Gestion"

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
