"""REST endpoints for SmartCampus voice note transcription and review pipeline."""

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.deps import decode_hs256_jwt
from app.core.rate_limit import enforce_guest_chat_rate_limit
from app.schemas.chat import ChatMessageRequest
from app.schemas.voice_note import (
    CampusScope,
    VoiceNoteMode,
    VoiceNoteProcessRequest,
    VoiceNoteProcessResponse,
    VoiceNoteTranscribeResponse,
)
from app.services import llm_service as chat_svc
from app.services import whisper_service

logger = logging.getLogger(__name__)

router = APIRouter()
_optional_bearer = HTTPBearer(auto_error=False)


@router.post(
    "/transcribe",
    response_model=VoiceNoteTranscribeResponse,
    status_code=status.HTTP_200_OK,
    summary="Transcribe student voice note into text for review (Whisper STT)",
    description=(
        "Accepts a voice recording (webm, mp4, m4a, mp3, wav, ogg up to 25MB). "
        "Transcribes it with OpenAI / Groq Whisper using domain context for IIT, ISB, and ISA "
        "(French, Derja, and Modern Standard Arabic). "
        "Returns the transcript for user review/correction without invoking the LLM."
    ),
)
async def post_transcribe_voice_note(
    audio: Annotated[UploadFile, File(description="Voice recording audio file")],
    request: Request,
    response: Response,
    mode: Annotated[VoiceNoteMode, Form(description="Language mode")] = "auto",
    campus_scope: Annotated[CampusScope, Form(description="Target school filter")] = "all",
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> VoiceNoteTranscribeResponse:
    """Step 1: Transcribe the audio note with Whisper, returning editable transcript."""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        decode_hs256_jwt(credentials.credentials, expected_type="access")
    else:
        await enforce_guest_chat_rate_limit(request, response)

    return await whisper_service.transcribe_audio(
        file=audio,
        mode=mode,
        campus_scope=campus_scope,
    )


@router.post(
    "/process",
    response_model=VoiceNoteProcessResponse,
    status_code=status.HTTP_200_OK,
    summary="Process user-confirmed transcript through SmartCampus agent/LLM pipeline",
    description=(
        "Receives the reviewed/corrected transcript and target campus_scope. "
        "Routes it to the VISION conversational agent and RAG pipeline with multilingual "
        "support (French, Arabic script, Tunisian Arabizi) and school scope filtering."
    ),
)
async def post_process_voice_note(
    payload: VoiceNoteProcessRequest,
    request: Request,
    response: Response,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> VoiceNoteProcessResponse:
    """Step 2: Feed the confirmed transcript into the SmartCampus LLM pipeline."""
    is_authenticated = False
    actor_session_id: str | None = None

    if credentials is not None and credentials.scheme.lower() == "bearer":
        claims = decode_hs256_jwt(credentials.credentials, expected_type="access")
        is_authenticated = True
        actor_session_id = str(claims.get("candidate_id") or claims.get("sub"))
    else:
        await enforce_guest_chat_rate_limit(request, response)

    resolved_session_id = payload.session_id or x_session_id or actor_session_id

    # Construct chat request routed to VISION agent
    chat_payload = ChatMessageRequest(
        message=payload.final_transcript,
        language_hint=payload.mode if payload.mode in {"fr", "derja"} else "auto",
        session_id=resolved_session_id,
        isVoice=True,
        campus_scope=payload.campus_scope,
    )

    chat_reply = await chat_svc.generate_chat_reply(
        chat_payload,
        session_id=resolved_session_id,
        is_authenticated=is_authenticated,
    )

    return VoiceNoteProcessResponse(
        transcript_id=payload.transcript_id,
        campus_scope=payload.campus_scope,
        mode=payload.mode,
        final_transcript=payload.final_transcript,
        session_id=chat_reply.session_id,
        sessionId=chat_reply.sessionId,
        reply_text=chat_reply.reply_text,
        reply=chat_reply.reply,
        sub_agent=chat_reply.sub_agent,
        subAgent=chat_reply.subAgent,
        detected_language=chat_reply.detected_language,
        detectedDialect=chat_reply.detectedDialect,
        intent=chat_reply.intent,
        citations=[c.model_dump() for c in chat_reply.citations],
        suggested_actions=chat_reply.suggested_actions,
        actionPrompt=chat_reply.actionPrompt,
        provider=chat_reply.provider,
        model_used=chat_reply.model_used,
        latency_ms=chat_reply.latency_ms,
        used_fallback=chat_reply.used_fallback,
        comprehension_confidence=chat_reply.comprehension_confidence,
        needs_reformulation=chat_reply.needs_reformulation,
        reformulation_prompt=chat_reply.reformulation_prompt,
    )
