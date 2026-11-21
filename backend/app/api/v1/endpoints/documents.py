"""VISION 1.4_Reader — Baccalaureate Transcript VLM Extraction REST Endpoints (Bonus-1 / SA-42)."""

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.core.config import settings
from app.core.deps import CurrentActor, require_roles
from app.schemas.transcript import BacTranscriptExtractionResult, VlmHealthResponse
from app.services import vlm_transcript_service as svc

router = APIRouter()


@router.get(
    "/vlm/health",
    response_model=VlmHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Report configured Vision LLM (Qwen-VL / GPT-4o-mini) readiness and upload constraints",
)
async def get_vlm_extraction_health() -> VlmHealthResponse:
    return svc.get_vlm_health()


@router.post(
    "/extract-bac-transcript",
    response_model=BacTranscriptExtractionResult,
    status_code=status.HTTP_200_OK,
    summary="Extract Tunisian Baccalaureate grades, average & confidence scores from image/PDF",
)
async def extract_bac_transcript_endpoint(
    file: UploadFile = File(
        ...,
        description="Tunisian Baccalaureate transcript (PNG, JPEG, WEBP, or PDF, max 10 MB)",
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
