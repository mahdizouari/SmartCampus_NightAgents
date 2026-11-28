"""VISION 1.4_Reader — Transcript VLM Extraction & PostgreSQL Profile Sync (Bonus-1 & Bonus-2)."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentActor, require_roles
from app.schemas.transcript import (
    BacTranscriptExtractionResult,
    CandidateTranscriptProfileResponse,
    TranscriptProfileSyncRequest,
    TranscriptProfileSyncResponse,
    VlmHealthResponse,
)
from app.services import transcript_profile_service as profile_svc
from app.services import vlm_transcript_service as svc

router = APIRouter()


@router.get(
    "/vlm/health",
    response_model=VlmHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Report local Vision LLM (Ollama qwen2.5vl:7b) readiness and upload constraints",
)
async def get_vlm_extraction_health() -> VlmHealthResponse:
    return svc.get_vlm_health()


@router.post(
    "/extract-bac-transcript",
    response_model=BacTranscriptExtractionResult,
    status_code=status.HTTP_200_OK,
    summary="Extract Tunisian Bac or Licence grades & confidence scores from image/PDF",
)
async def extract_bac_transcript_endpoint(
    file: UploadFile = File(
        ...,
        description="Tunisian Bac or Licence transcript (PNG, JPEG, WEBP, or PDF, max 10 MB)",
    ),
    declared_average: float | None = Form(
        default=None,
        ge=0.0,
        le=20.0,
        description="Optional candidate declared average to cross-check for discrepancies",
    ),
    _actor: CurrentActor = Depends(require_roles("student", "rh", "responsable", "admin")),
) -> BacTranscriptExtractionResult:
    raw_bytes = await file.read(settings.VLM_MAX_FILE_SIZE_BYTES + 1)
    return await svc.extract_bac_transcript(
        raw_bytes,
        file_name=file.filename or "transcript",
        declared_content_type=file.content_type,
        declared_average=declared_average,
    )


@router.post(
    "/sync-transcript-profile",
    response_model=TranscriptProfileSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Persist Bonus-1 VLM extraction JSON into authenticated candidate's PostgreSQL profile",
)
async def sync_transcript_profile_endpoint(
    payload: TranscriptProfileSyncRequest,
    actor: CurrentActor = Depends(require_roles("student", "rh", "responsable", "admin")),
    db: AsyncSession = Depends(get_db),
) -> TranscriptProfileSyncResponse:
    return await profile_svc.sync_transcript_to_profile(db, actor=actor, payload=payload)


@router.post(
    "/extract-and-sync-transcript",
    response_model=TranscriptProfileSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="One-shot Bonus-1 + Bonus-2: extract transcript via VLM and pre-fill profile",
)
async def extract_and_sync_transcript_endpoint(
    file: UploadFile = File(
        ...,
        description="Tunisian Bac or Licence transcript (PNG, JPEG, WEBP, or PDF, max 10 MB)",
    ),
    declared_average: float | None = Form(
        default=None,
        ge=0.0,
        le=20.0,
        description="Optional candidate declared average to cross-check for discrepancies",
    ),
    candidate_id: UUID | None = Form(
        default=None,
        description="Optional target candidate UUID (staff only; students always sync own profile)",
    ),
    confirmed_by_student: bool = Form(
        default=False,
        description="Whether extracted values are already confirmed by the candidate",
    ),
    actor: CurrentActor = Depends(require_roles("student", "rh", "responsable", "admin")),
    db: AsyncSession = Depends(get_db),
) -> TranscriptProfileSyncResponse:
    raw_bytes = await file.read(settings.VLM_MAX_FILE_SIZE_BYTES + 1)
    extraction = await svc.extract_bac_transcript(
        raw_bytes,
        file_name=file.filename or "transcript",
        declared_content_type=file.content_type,
        declared_average=declared_average,
    )
    sync_request = TranscriptProfileSyncRequest(
        extraction=extraction,
        candidate_id=candidate_id,
        confirmed_by_student=confirmed_by_student,
    )
    return await profile_svc.sync_transcript_to_profile(db, actor=actor, payload=sync_request)


@router.get(
    "/my-transcript-profile",
    response_model=CandidateTranscriptProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve persisted candidate profile, VLM documents, grades & confirmation flags",
)
async def get_my_transcript_profile_endpoint(
    candidate_id: UUID | None = Query(
        default=None,
        description="Optional candidate UUID (staff only; students always view own profile)",
    ),
    actor: CurrentActor = Depends(require_roles("student", "rh", "responsable", "admin")),
    db: AsyncSession = Depends(get_db),
) -> CandidateTranscriptProfileResponse:
    return await profile_svc.get_candidate_transcript_profile(
        db,
        actor=actor,
        requested_candidate_id=candidate_id,
    )
