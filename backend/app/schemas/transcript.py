"""Pydantic v2 schemas for VLM Baccalaureate & University transcript extraction (Bonus-1 / SA-42).

Designed for direct handoff to Bonus-2 PostgreSQL persistence:
  - core.document (RELEVE_BAC | RELEVE_UNIVERSITAIRE, ocr_json, min_confidence, verification_status)
  - core.candidate_profile (current_education_level, current_specialty, declared_overall_avg, facts)
  - core.declared_grades (diploma_stage, subject_name, grade_value, coefficient, confidence_score)
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocumentType = Literal["RELEVE_BAC", "RELEVE_UNIVERSITAIRE"]

DiplomaStage = Literal["BAC", "L1", "L2", "L3", "PREPA_1", "PREPA_2", "M1", "OTHER"]

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

TranscriptSessionType = Literal["Principale", "Contrôle", "Rattrapage", "Inconnue"]

ExtractionSource = Literal["vlm_live", "pdf_text_parser", "image_fallback"]


class ExtractedSubjectGrade(BaseModel):
    """Single subject / UE grade row extracted from a Tunisian Bac or University transcript."""

    name: str = Field(..., min_length=1, max_length=128)
    grade: float = Field(..., ge=0.0, le=20.0)
    grade_scale: float = Field(default=20.0, gt=0.0)
    coefficient: float = Field(..., gt=0.0, le=30.0)
    weighted_points: float | None = Field(default=None, ge=0.0)
    credits_acquired: float | None = Field(default=None, ge=0.0, le=60.0)
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

    document_type: DocumentType = "RELEVE_BAC"
    diploma_stage: DiplomaStage = "BAC"
    bac_section: BacSection | None = "Autre"
    program_title: str | None = Field(default=None, max_length=256)
    session_year: str | None = Field(default=None, max_length=32)
    session_type: TranscriptSessionType = "Principale"
    student_name: str | None = Field(default=None, max_length=128)
    institution: str | None = Field(default=None, max_length=200)
    governorate: str | None = Field(default=None, max_length=64)
    general_average: float | None = Field(default=None, ge=0.0, le=20.0)
    calculated_average: float | None = Field(default=None, ge=0.0, le=20.0)
    declared_average: float | None = Field(default=None, ge=0.0, le=20.0)
    average_discrepancy: float | None = Field(default=None, ge=0.0)
    discrepancy_detected: bool = False
    credits_acquired: float | None = Field(default=None, ge=0.0, le=180.0)
    mention: str | None = Field(default=None, max_length=32)
    jury_decision: str | None = Field(default=None, max_length=64)
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


DocumentVerificationStatus = Literal[
    "PENDING",
    "CONFIRMED_BY_STUDENT",
    "MISMATCH_RH",
    "VERIFIED",
]

GradeEntrySource = Literal[
    "DECLARED_CHAT",
    "CANDIDATE_FORM",
    "VLM_CONFIRMED",
    "RH_CORRECTED",
]


class LowConfidenceFlag(BaseModel):
    """Explicit low-confidence or discrepancy flag surfaced for student/RH confirmation."""

    field: str = Field(..., min_length=1, max_length=128)
    value: str | float | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    threshold: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=256)


class TranscriptProfileSyncRequest(BaseModel):
    """Bonus-2 payload connecting Bonus-1 VLM extraction JSON to PostgreSQL profile."""

    extraction: BacTranscriptExtractionResult
    candidate_id: UUID | None = Field(
        default=None,
        description=(
            "Target candidate UUID. Ignored/checked against own ID for students; "
            "required when staff (rh/responsable/admin) sync on behalf of a candidate."
        ),
    )
    document_uri: str | None = Field(
        default=None,
        max_length=512,
        description="Optional private storage URI (e.g. minio://...); defaults to sanitized URI.",
    )
    confirmed_by_student: bool = Field(
        default=False,
        description=(
            "Set true after the student reviews and confirms extracted grades. "
            "Never overrides MISMATCH_RH or low-confidence manual review flags."
        ),
    )
    replace_stage_grades: bool = Field(
        default=True,
        description="Replace prior VLM grades for the same diploma_stage to stay idempotent.",
    )

    @model_validator(mode="after")
    def validate_subject_grades_within_scale(self) -> "TranscriptProfileSyncRequest":
        for subj in self.extraction.subjects:
            if subj.grade > subj.grade_scale:
                raise ValueError(
                    f"Subject '{subj.name}' grade ({subj.grade}) exceeds "
                    f"grade_scale ({subj.grade_scale})."
                )
        return self


class PersistedGradeSummary(BaseModel):
    """Persisted core.declared_grades row returned after syncing VLM extraction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    diploma_stage: DiplomaStage
    academic_year: str | None = None
    subject_name: str
    grade_value: float
    grade_scale: float = 20.0
    coefficient: float | None = None
    entry_source: GradeEntrySource
    confidence_score: float | None = None
    needs_confirmation: bool = False


class TranscriptProfileSyncResponse(BaseModel):
    """Result of persisting Bonus-1 VLM transcript JSON into PostgreSQL (Bonus-2)."""

    candidate_id: UUID
    document_id: UUID
    document_type: DocumentType
    diploma_stage: DiplomaStage
    verification_status: DocumentVerificationStatus
    profile_updated: bool = True
    current_education_level: str | None = None
    current_specialty: str | None = None
    declared_overall_avg: float | None = None
    grades_persisted_count: int = Field(..., ge=0)
    grades: list[PersistedGradeSummary] = Field(default_factory=list)
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    min_confidence: float = Field(..., ge=0.0, le=1.0)
    discrepancy_detected: bool = False
    needs_manual_review: bool = True
    low_confidence_flags: list[LowConfidenceFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    synced_at: datetime


class CandidateTranscriptDocumentSummary(BaseModel):
    """Compact summary of a persisted core.document transcript record."""

    id: UUID
    type: str
    uri: str
    min_confidence: float | None = None
    verification_status: str
    uploaded_at: datetime


class CandidateTranscriptProfileResponse(BaseModel):
    """Authenticated candidate's persisted academic profile, grades, and pending review flags."""

    candidate_id: UUID
    current_education_level: str | None = None
    current_specialty: str | None = None
    declared_overall_avg: float | None = None
    needs_manual_review: bool = False
    low_confidence_fields: list[str] = Field(default_factory=list)
    documents: list[CandidateTranscriptDocumentSummary] = Field(default_factory=list)
    grades: list[PersistedGradeSummary] = Field(default_factory=list)
    updated_at: datetime | None = None
