"""PostgreSQL persistence service connecting Bonus-1 VLM JSON to candidate profiles (Bonus-2).

Persists validated BacTranscriptExtractionResult payloads into:
  - core.document (RELEVE_BAC | RELEVE_UNIVERSITAIRE, ocr_json, min_confidence, verification_status)
  - core.candidate_profile (current_education_level, current_specialty, declared_overall_avg, facts)
  - core.declared_grades (diploma_stage, subject_name, grade_value, coefficient, confidence_score)
  - core.audit_log (PII-free immutable state-change trail)

Enforces:
  - "Give or Keep" authentication & strict anti-IDOR ownership for students.
  - Per-field confidence threshold checking (< VLM_CONFIDENCE_THRESHOLD) with explicit
    confirmation flags so low-confidence fields or average mismatches are never silently accepted.
  - HITL admission boundary: syncing a transcript never admits, waitlists, or rejects a candidate.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentActor
from app.models.users import (
    AuditLog,
    Candidate,
    CandidateDocument,
    CandidateProfile,
    DeclaredGrade,
)
from app.schemas.transcript import (
    BacTranscriptExtractionResult,
    CandidateTranscriptDocumentSummary,
    CandidateTranscriptProfileResponse,
    DocumentVerificationStatus,
    GradeEntrySource,
    LowConfidenceFlag,
    PersistedGradeSummary,
    TranscriptProfileSyncRequest,
    TranscriptProfileSyncResponse,
)

logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_ACTOR_TYPE_BY_ROLE: dict[str, str] = {
    "student": "STUDENT",
    "rh": "RH",
    "responsable": "RESPONSABLE",
    "direction": "DIRECTION",
    "admin": "ADMIN",
}

_EDUCATION_LEVEL_LABELS: dict[str, str] = {
    "BAC": "BAC",
    "L1": "L1",
    "L2": "L2",
    "L3": "L3",
    "PREPA_1": "PREPA_1",
    "PREPA_2": "PREPA_2",
    "M1": "M1",
    "OTHER": "OTHER",
}


def _to_decimal(value: float | None, places: int = 2) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{round(float(value), places):.{places}f}")


def _sanitize_document_uri(candidate_id: UUID, file_name: str, provided_uri: str | None) -> str:
    if provided_uri and provided_uri.strip():
        return provided_uri.strip()[:512]
    clean_name = _SAFE_FILENAME_RE.sub("_", (file_name or "transcript").strip())[:120]
    return f"vlm://transcripts/{candidate_id}/{clean_name or 'transcript'}"


def _build_low_confidence_flags(
    extraction: BacTranscriptExtractionResult,
    *,
    threshold: float,
) -> list[LowConfidenceFlag]:
    """Audit top-level metadata, subjects, and arithmetic checks for confirmation flags."""
    flags: list[LowConfidenceFlag] = []
    fc = extraction.field_confidences

    if extraction.document_type == "RELEVE_BAC":
        if fc.bac_section < threshold or extraction.bac_section in (None, "Autre"):
            flags.append(
                LowConfidenceFlag(
                    field="bac_section",
                    value=extraction.bac_section,
                    confidence=fc.bac_section,
                    threshold=threshold,
                    reason=(
                        "Section du Bac absente ou confiance inférieure au seuil requis "
                        "pour pré-remplissage automatique."
                    ),
                )
            )

    if fc.session_year < threshold or not extraction.session_year:
        flags.append(
            LowConfidenceFlag(
                field="session_year",
                value=extraction.session_year,
                confidence=fc.session_year,
                threshold=threshold,
                reason="Année de session illisible ou incertaine ; confirmation requise.",
            )
        )

    effective_avg = (
        extraction.general_average
        if extraction.general_average is not None
        else extraction.calculated_average
    )
    if fc.general_average < threshold or effective_avg is None:
        flags.append(
            LowConfidenceFlag(
                field="general_average",
                value=effective_avg,
                confidence=fc.general_average,
                threshold=threshold,
                reason="Moyenne générale absente ou confiance inférieure au seuil requis.",
            )
        )

    if extraction.discrepancy_detected:
        flags.append(
            LowConfidenceFlag(
                field="average_discrepancy",
                value=extraction.average_discrepancy,
                confidence=min(fc.general_average, extraction.min_confidence),
                threshold=threshold,
                reason=(
                    "Écart détecté entre la moyenne déclarée/imprimée et la moyenne recalculée "
                    "(vérification RH / candidat obligatoire)."
                ),
            )
        )

    if not extraction.subjects:
        flags.append(
            LowConfidenceFlag(
                field="subjects",
                value=None,
                confidence=0.0,
                threshold=threshold,
                reason="Aucune note de matière extraite ; saisie ou vérification manuelle requise.",
            )
        )

    for subj in extraction.subjects:
        if subj.confidence < threshold or subj.needs_confirmation:
            flags.append(
                LowConfidenceFlag(
                    field=f"subject:{subj.name}",
                    value=subj.grade,
                    confidence=subj.confidence,
                    threshold=threshold,
                    reason=(
                        f"Note '{subj.name}' ({subj.grade}/{subj.grade_scale}) sous le seuil "
                        f"de confiance ({subj.confidence:.2f} < {threshold:.2f})."
                    ),
                )
            )

    return flags


def _parse_actor_candidate_uuid(raw_id: str | UUID | None) -> UUID | None:
    if raw_id is None:
        return None
    if isinstance(raw_id, UUID):
        return raw_id
    try:
        return UUID(str(raw_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid candidate_id UUID in authentication token.",
        ) from exc


async def _resolve_target_candidate(
    db: AsyncSession,
    *,
    actor: CurrentActor,
    requested_candidate_id: UUID | None,
) -> Candidate:
    """Resolve and authorize target Candidate, preventing cross-candidate IDOR for students."""
    actor_cand_uuid = _parse_actor_candidate_uuid(actor.candidate_id)

    if actor.role == "student":
        if actor_cand_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated student token has no linked candidate profile.",
            )
        if requested_candidate_id is not None and requested_candidate_id != actor_cand_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students can only sync transcripts into their own candidate profile.",
            )
        target_id = actor_cand_uuid
    else:
        target_id = requested_candidate_id or actor_cand_uuid
        if target_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="candidate_id is required when staff sync a candidate transcript.",
            )

    candidate = await db.get(Candidate, target_id)
    if candidate is None:
        res = await db.execute(select(Candidate).where(Candidate.id == target_id))
        candidate = res.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{target_id}' not found.",
        )
    return candidate


def _resolve_verification_status(
    extraction: BacTranscriptExtractionResult,
    *,
    low_confidence_flags: list[LowConfidenceFlag],
    confirmed_by_student: bool,
) -> DocumentVerificationStatus:
    """Determine core.document.verification_status while preserving HITL & mismatch guards."""
    if extraction.discrepancy_detected or extraction.verification_status == "MISMATCH_RH":
        return "MISMATCH_RH"
    if confirmed_by_student and not low_confidence_flags:
        return "CONFIRMED_BY_STUDENT"
    return "PENDING"


def _resolve_specialty(extraction: BacTranscriptExtractionResult) -> str | None:
    if extraction.document_type == "RELEVE_UNIVERSITAIRE":
        candidate_spec = extraction.program_title or extraction.bac_section
    else:
        candidate_spec = (
            extraction.bac_section
            if extraction.bac_section and extraction.bac_section != "Autre"
            else extraction.program_title
        )
    if not candidate_spec:
        return None
    return candidate_spec.strip()[:128]


def _resolve_entry_source(
    *,
    actor_role: str,
    confirmed_by_student: bool,
    needs_confirmation: bool,
) -> GradeEntrySource:
    if actor_role in {"rh", "responsable", "admin"} and confirmed_by_student:
        return "RH_CORRECTED"
    if confirmed_by_student and not needs_confirmation:
        return "VLM_CONFIRMED"
    return "CANDIDATE_FORM"


async def sync_transcript_to_profile(
    db: AsyncSession,
    *,
    actor: CurrentActor,
    payload: TranscriptProfileSyncRequest,
) -> TranscriptProfileSyncResponse:
    """Validate and persist a Bonus-1 VLM extraction into PostgreSQL for the candidate."""
    candidate = await _resolve_target_candidate(
        db,
        actor=actor,
        requested_candidate_id=payload.candidate_id,
    )
    extraction = payload.extraction
    threshold = settings.VLM_CONFIDENCE_THRESHOLD
    now = datetime.now(UTC)

    low_confidence_flags = _build_low_confidence_flags(extraction, threshold=threshold)
    needs_manual_review = bool(
        extraction.needs_manual_review
        or extraction.discrepancy_detected
        or low_confidence_flags
    )
    verification_status = _resolve_verification_status(
        extraction,
        low_confidence_flags=low_confidence_flags,
        confirmed_by_student=payload.confirmed_by_student,
    )

    # 1. Persist core.document (private transcript record + full JSONB OCR output)
    doc_uri = _sanitize_document_uri(candidate.id, extraction.file_name, payload.document_uri)
    document = CandidateDocument(
        candidate_id=candidate.id,
        type=extraction.document_type,
        uri=doc_uri,
        ocr_json=extraction.model_dump(mode="json"),
        min_confidence=_to_decimal(extraction.min_confidence, places=3),
        verification_status=verification_status,
        uploaded_at=now,
    )
    db.add(document)
    await db.flush()

    # 2. Upsert core.candidate_profile (pre-fill academic facts without clobbering prior memory)
    profile_res = await db.execute(
        select(CandidateProfile).where(CandidateProfile.candidate_id == candidate.id)
    )
    profile = profile_res.scalar_one_or_none()
    if profile is None:
        profile = CandidateProfile(
            candidate_id=candidate.id,
            facts={},
            summary="",
            updated_at=now,
        )
        db.add(profile)

    education_level = _EDUCATION_LEVEL_LABELS.get(extraction.diploma_stage, "OTHER")
    specialty = _resolve_specialty(extraction)
    effective_avg = (
        extraction.general_average
        if extraction.general_average is not None
        else extraction.calculated_average
    )

    profile.current_education_level = education_level
    if specialty is not None:
        profile.current_specialty = specialty
    if effective_avg is not None:
        profile.declared_overall_avg = _to_decimal(effective_avg, places=2)

    existing_facts = dict(profile.facts or {})
    existing_facts["transcript_extraction"] = {
        "last_document_id": str(document.id),
        "document_type": extraction.document_type,
        "diploma_stage": extraction.diploma_stage,
        "bac_section": extraction.bac_section,
        "program_title": extraction.program_title,
        "session_year": extraction.session_year,
        "session_type": extraction.session_type,
        "institution": extraction.institution,
        "governorate": extraction.governorate,
        "general_average": extraction.general_average,
        "calculated_average": extraction.calculated_average,
        "average_discrepancy": extraction.average_discrepancy,
        "discrepancy_detected": extraction.discrepancy_detected,
        "credits_acquired": extraction.credits_acquired,
        "mention": extraction.mention,
        "jury_decision": extraction.jury_decision,
        "min_confidence": extraction.min_confidence,
        "overall_confidence": extraction.overall_confidence,
        "needs_manual_review": needs_manual_review,
        "low_confidence_fields": [f.field for f in low_confidence_flags],
        "verification_status": verification_status,
        "extraction_source": extraction.extraction_source,
        "model_used": extraction.model_used,
        "synced_at": now.isoformat(),
    }
    profile.facts = existing_facts
    profile.updated_at = now

    # 3. Sync core.declared_grades (idempotent replacement for VLM grades of same diploma_stage)
    if payload.replace_stage_grades:
        existing_grades_res = await db.execute(
            select(DeclaredGrade).where(
                DeclaredGrade.candidate_id == candidate.id,
                DeclaredGrade.diploma_stage == extraction.diploma_stage,
            )
        )
        for old_grade in existing_grades_res.scalars().all():
            if old_grade.source_document_id is not None:
                await db.delete(old_grade)

    academic_year = (extraction.session_year or "").strip()[:16] or None
    persisted_grades: list[PersistedGradeSummary] = []

    for subj in extraction.subjects:
        subj_needs_conf = bool(subj.needs_confirmation or subj.confidence < threshold)
        entry_source = _resolve_entry_source(
            actor_role=actor.role,
            confirmed_by_student=payload.confirmed_by_student,
            needs_confirmation=subj_needs_conf,
        )
        grade_row = DeclaredGrade(
            candidate_id=candidate.id,
            academic_year=academic_year,
            diploma_stage=extraction.diploma_stage,
            subject_name=subj.name.strip()[:128],
            grade_value=_to_decimal(subj.grade, places=2) or Decimal("0.00"),
            grade_scale=_to_decimal(subj.grade_scale, places=2) or Decimal("20.00"),
            coefficient=_to_decimal(subj.coefficient, places=2),
            entry_source=entry_source,
            source_document_id=document.id,
            confidence_score=_to_decimal(subj.confidence, places=3),
        )
        db.add(grade_row)
        await db.flush()
        persisted_grades.append(
            PersistedGradeSummary(
                id=grade_row.id,
                diploma_stage=extraction.diploma_stage,
                academic_year=grade_row.academic_year,
                subject_name=grade_row.subject_name,
                grade_value=float(grade_row.grade_value),
                grade_scale=float(grade_row.grade_scale),
                coefficient=(
                    float(grade_row.coefficient) if grade_row.coefficient is not None else None
                ),
                entry_source=entry_source,
                confidence_score=(
                    float(grade_row.confidence_score)
                    if grade_row.confidence_score is not None
                    else None
                ),
                needs_confirmation=subj_needs_conf,
            )
        )

    # 4. Append PII-free immutable AuditLog record
    audit = AuditLog(
        actor_type=_ACTOR_TYPE_BY_ROLE.get(actor.role, "SYSTEM"),
        actor_id=str(actor.account_id),
        action="TRANSCRIPT_PROFILE_SYNCED",
        entity_type="candidate_profile",
        entity_id=candidate.id,
        reason=(
            f"Synced {extraction.document_type} ({extraction.diploma_stage}) "
            f"with status={verification_status}"
        ),
        event_metadata={
            "document_id": str(document.id),
            "document_type": extraction.document_type,
            "diploma_stage": extraction.diploma_stage,
            "grades_persisted": len(persisted_grades),
            "verification_status": verification_status,
            "min_confidence": extraction.min_confidence,
            "low_confidence_count": len(low_confidence_flags),
            "discrepancy_detected": extraction.discrepancy_detected,
        },
        created_at=now,
    )
    db.add(audit)

    await db.commit()

    warnings = list(extraction.warnings)
    if low_confidence_flags and payload.confirmed_by_student:
        warnings.append(
            "Confirmation différée : des champs sous le seuil de confiance ou un écart "
            "de moyenne nécessitent encore une validation explicite."
        )

    logger.info(
        "Bonus-2 transcript synced: candidate=%s doc=%s stage=%s grades=%d status=%s flags=%d",
        candidate.id,
        document.id,
        extraction.diploma_stage,
        len(persisted_grades),
        verification_status,
        len(low_confidence_flags),
    )

    return TranscriptProfileSyncResponse(
        candidate_id=candidate.id,
        document_id=document.id,
        document_type=extraction.document_type,
        diploma_stage=extraction.diploma_stage,
        verification_status=verification_status,
        profile_updated=True,
        current_education_level=profile.current_education_level,
        current_specialty=profile.current_specialty,
        declared_overall_avg=(
            float(profile.declared_overall_avg)
            if profile.declared_overall_avg is not None
            else None
        ),
        grades_persisted_count=len(persisted_grades),
        grades=persisted_grades,
        overall_confidence=extraction.overall_confidence,
        min_confidence=extraction.min_confidence,
        discrepancy_detected=extraction.discrepancy_detected,
        needs_manual_review=needs_manual_review,
        low_confidence_flags=low_confidence_flags,
        warnings=warnings,
        synced_at=now,
    )


async def get_candidate_transcript_profile(
    db: AsyncSession,
    *,
    actor: CurrentActor,
    requested_candidate_id: UUID | None = None,
) -> CandidateTranscriptProfileResponse:
    """Read persisted academic profile, transcript documents, and grades for a candidate."""
    candidate = await _resolve_target_candidate(
        db,
        actor=actor,
        requested_candidate_id=requested_candidate_id,
    )
    threshold = settings.VLM_CONFIDENCE_THRESHOLD

    profile_res = await db.execute(
        select(CandidateProfile).where(CandidateProfile.candidate_id == candidate.id)
    )
    profile = profile_res.scalar_one_or_none()

    docs_res = await db.execute(
        select(CandidateDocument).where(CandidateDocument.candidate_id == candidate.id)
    )
    docs = list(docs_res.scalars().all())

    grades_res = await db.execute(
        select(DeclaredGrade).where(DeclaredGrade.candidate_id == candidate.id)
    )
    grade_rows = list(grades_res.scalars().all())

    extraction_meta = (
        (profile.facts or {}).get("transcript_extraction", {}) if profile is not None else {}
    )
    low_conf_fields = list(extraction_meta.get("low_confidence_fields", []))
    needs_manual_review = bool(extraction_meta.get("needs_manual_review", False))

    return CandidateTranscriptProfileResponse(
        candidate_id=candidate.id,
        current_education_level=profile.current_education_level if profile else None,
        current_specialty=profile.current_specialty if profile else None,
        declared_overall_avg=(
            float(profile.declared_overall_avg)
            if profile and profile.declared_overall_avg is not None
            else None
        ),
        needs_manual_review=needs_manual_review,
        low_confidence_fields=low_conf_fields,
        documents=[
            CandidateTranscriptDocumentSummary(
                id=d.id,
                type=d.type,
                uri=d.uri,
                min_confidence=float(d.min_confidence) if d.min_confidence is not None else None,
                verification_status=d.verification_status,
                uploaded_at=d.uploaded_at,
            )
            for d in docs
        ],
        grades=[
            PersistedGradeSummary(
                id=g.id,
                diploma_stage=g.diploma_stage,  # type: ignore[arg-type]
                academic_year=g.academic_year,
                subject_name=g.subject_name,
                grade_value=float(g.grade_value),
                grade_scale=float(g.grade_scale),
                coefficient=float(g.coefficient) if g.coefficient is not None else None,
                entry_source=g.entry_source,  # type: ignore[arg-type]
                confidence_score=(
                    float(g.confidence_score) if g.confidence_score is not None else None
                ),
                needs_confirmation=(
                    g.confidence_score is not None and float(g.confidence_score) < threshold
                ),
            )
            for g in grade_rows
        ],
        updated_at=profile.updated_at if profile else None,
    )

