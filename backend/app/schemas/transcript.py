"""Pydantic v2 schemas for VLM Baccalaureate transcript extraction (Bonus-1 / SA-42).

Designed for direct handoff to Bonus-2 PostgreSQL persistence:
  - core.document (RELEVE_BAC, ocr_json, min_confidence, verification_status)
  - core.candidate_profile (current_specialty, declared_overall_avg, facts)
  - core.declared_grades (diploma_stage='BAC', subject_name, grade_value, coefficient, confidence)
"""

from typing import Literal

from pydantic import BaseModel, Field

BacSection = Literal[
    "Mathématiques",
    "Sciences Expérimentales",
    "Sciences Techniques",
    "Sciences de l'Informatique",
    "Économie et Gestion",
    "Lettres",
    "Sport",
    "Autre",
]

BacSessionType = Literal["Principale", "Contrôle", "Inconnue"]

ExtractionSource = Literal["vlm_live", "pdf_text_parser", "image_fallback"]


class ExtractedSubjectGrade(BaseModel):
    """Single subject grade row extracted from a Tunisian Baccalaureate transcript."""

    name: str = Field(..., min_length=1, max_length=128)
    grade: float = Field(..., ge=0.0, le=20.0)
    grade_scale: float = Field(default=20.0, gt=0.0)
    coefficient: float = Field(..., gt=0.0, le=10.0)
    weighted_points: float | None = Field(default=None, ge=0.0)
    is_optional: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_confirmation: bool = False


class TranscriptFieldConfidence(BaseModel):
    """Per-field OCR/VLM confidence scores for top-level transcript metadata."""

    bac_section: float = Field(..., ge=0.0, le=1.0)
    session_year: float = Field(..., ge=0.0, le=1.0)
    general_average: float = Field(..., ge=0.0, le=1.0)
    student_name: float | None = Field(default=None, ge=0.0, le=1.0)
    institution: float | None = Field(default=None, ge=0.0, le=1.0)


class BacTranscriptExtractionResult(BaseModel):
    """Structured JSON output of VISION 1.4_Reader for Bonus-1 -> Bonus-2 integration."""

    document_type: Literal["RELEVE_BAC"] = "RELEVE_BAC"
    diploma_stage: Literal["BAC"] = "BAC"
    bac_section: BacSection = "Autre"
    session_year: str | None = Field(default=None, max_length=16)
    session_type: BacSessionType = "Principale"
    student_name: str | None = Field(default=None, max_length=128)
    institution: str | None = Field(default=None, max_length=160)
    governorate: str | None = Field(default=None, max_length=64)
    general_average: float | None = Field(default=None, ge=0.0, le=20.0)
    calculated_average: float | None = Field(default=None, ge=0.0, le=20.0)
    declared_average: float | None = Field(default=None, ge=0.0, le=20.0)
    average_discrepancy: float | None = Field(default=None, ge=0.0)
    discrepancy_detected: bool = False
    mention: str | None = Field(default=None, max_length=32)
    subjects: list[ExtractedSubjectGrade] = Field(default_factory=list)
    field_confidences: TranscriptFieldConfidence
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    min_confidence: float = Field(..., ge=0.0, le=1.0)
    low_confidence_fields: list[str] = Field(default_factory=list)
    needs_manual_review: bool = True
    verification_status: Literal["PENDING", "MISMATCH_RH"] = "PENDING"
    extraction_source: ExtractionSource
    model_used: str
    file_name: str
    content_type: str
    file_size_bytes: int = Field(..., gt=0)
    latency_ms: int = Field(..., ge=1)
    warnings: list[str] = Field(default_factory=list)


class VlmHealthResponse(BaseModel):
    """Readiness status of the configured Vision LLM (Bonus-1 / SA-42)."""

    status: Literal["ready", "local_pdf_fallback", "misconfigured"]
    provider: str
    model: str
    base_url: str
    api_key_configured: bool
    confidence_threshold: float
    max_file_size_bytes: int
    supported_mime_types: list[str]
    local_pdf_fallback_enabled: bool
