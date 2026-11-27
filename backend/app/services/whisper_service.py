"""OpenAI Whisper speech-to-text integration service for SmartCampus voice notes.

Supports trilingual student orientation covering IIT, ISB, and ISA in Tunisian Derja,
Modern Standard Arabic (Fusha), and French with code-switching and domain terminology.
"""

import logging
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.schemas.voice_note import (
    CampusScope,
    VoiceNoteMode,
    VoiceNoteTranscribeResponse,
)

logger = logging.getLogger(__name__)

# Max allowed audio size: 25 MB (OpenAI Whisper limit)
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024

ALLOWED_AUDIO_EXTENSIONS = {"webm", "mp4", "m4a", "mp3", "wav", "ogg"}

ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "video/webm",
    "audio/mp4",
    "video/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/aac",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/ogg",
    "application/ogg",
    "audio/vorbis",
    "audio/opus",
}

# Domain-specific prompts guiding Whisper on academic terminology and Tunisian dialect
WHISPER_PROMPTS: dict[VoiceNoteMode, str] = {
    "derja": (
        "SmartCampus orientation NAU Sfax: IIT, ISB, ISA. Admissions, inscription, filières, "
        "licence, mastère, cycle d'ingénierie, architecture, business school, stages, PFE. "
        "فما، برشا، كيفاش، قداش، نحب نسجل، concours, dossier, relevé de notes, bac."
    ),
    "ar": (
        "منصة SmartCampus للتوجيه والتسجيل في مؤسسات IIT و ISB و ISA بمجمع NAU الأكاديمي في صفاقس. "
        "شروط القبول، التخصصات، الإجازة، الماجستير، الهندسة، الهندسة المعمارية، إدارة الأعمال، "
        "فترات التدريب ومشاريع التخرج PFE."
    ),
    "fr": (
        "SmartCampus orientation universitaire NAU Sfax pour les écoles IIT, ISB et ISA. "
        "Admissions, inscription, filières, licence, mastère, cycle ingénieur, architecture, "
        "business school, stages et projets de fin d'études PFE."
    ),
    "auto": (
        "SmartCampus orientation NAU Sfax (IIT, ISB, ISA). Inscription, licence, mastère, "
        "ingénierie, architecture, business, stages/PFE. "
        "فما، برشا، كيفاش، شنوة، قداش، نحب نسجل."
    ),
}

# Mock transcripts when OPENAI_API_KEY is not configured (offline / testing fallback)
MOCK_VOICE_TRANSCRIPTS: dict[VoiceNoteMode, str] = {
    "derja": (
        "عسلامة، نحب نسجل في مرحلة تكوين المهندسين في IIT وفما برشا اختصاصات "
        "كيفاش نختار وبقداش التسجيل؟"
    ),
    "ar": (
        "مرحبا، أود الاستفسار عن شروط القبول والتسجيل في ماجستير إدارة الأعمال "
        "في ISB ومشاريع التخرج PFE."
    ),
    "fr": (
        "Bonjour, je souhaite me renseigner sur les filières d'ingénierie à l'IIT "
        "et d'architecture à l'ISA, ainsi que les stages et modalités d'admission."
    ),
    "auto": (
        "3aslema! Nheb na3ref kifech nsajel fi l'IIT w l'ISB w chnowa les filières "
        "d'ingénierie w architecture disponibles."
    ),
}


def validate_audio_upload(
    filename: str | None,
    content_type: str | None,
    file_bytes: bytes,
) -> None:
    """Validate file presence, format/MIME type, and size constraints."""
    if not file_bytes or len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier audio manquant ou vide. Veuillez enregistrer un message vocal.",
        )

    if len(file_bytes) > MAX_AUDIO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Le fichier audio ({len(file_bytes) / (1024 * 1024):.1f} Mo) "
                f"dépasse la taille maximale autorisée de 25 Mo."
            ),
        )

    # Check extension
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower().strip()

    # Normalize mime type (strip charset/codec params like audio/webm;codecs=opus)
    base_mime = ""
    if content_type:
        base_mime = content_type.split(";")[0].lower().strip()

    is_ext_valid = ext in ALLOWED_AUDIO_EXTENSIONS
    is_mime_valid = base_mime in ALLOWED_AUDIO_MIME_TYPES

    if not is_ext_valid and not is_mime_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Format audio non supporté ('{base_mime or ext}'). "
                f"Formats acceptés : webm, mp4, m4a, mp3, wav, ogg."
            ),
        )


def build_whisper_prompt(mode: VoiceNoteMode, campus_scope: CampusScope) -> tuple[str, str | None]:
    """Map mode and campus_scope to Whisper prompt and language parameter.

    - auto: omit language parameter (allows French/Derja/Arabic code-switching)
    - derja: language='ar' with Tunisian Derja + French academic prompt
    - ar: language='ar' with Modern Standard Arabic academic prompt
    - fr: language='fr' with French academic prompt
    """
    base_prompt = WHISPER_PROMPTS.get(mode, WHISPER_PROMPTS["auto"])
    if campus_scope != "all":
        base_prompt += f" Focus école : {campus_scope}."

    language: str | None = None
    if mode == "derja":
        language = "ar"
    elif mode == "ar":
        language = "ar"
    elif mode == "fr":
        language = "fr"
    else:  # auto
        language = None

    return base_prompt, language


async def transcribe_audio(
    file: UploadFile,
    mode: VoiceNoteMode = "auto",
    campus_scope: CampusScope = "all",
) -> VoiceNoteTranscribeResponse:
    """Execute audio transcription via Whisper API with domain prompts and review step."""
    file_bytes = await file.read(MAX_AUDIO_SIZE_BYTES + 1)
    validate_audio_upload(file.filename, file.content_type, file_bytes)

    transcript_id = f"vn-{uuid4()}"
    prompt, language = build_whisper_prompt(mode, campus_scope)

    provider = settings.WHISPER_PROVIDER.lower().strip()
    api_key = settings.effective_whisper_api_key
    use_mock = provider == "mock" or (provider != "local" and not api_key)
    if use_mock:
        logger.warning(
            "Whisper STT running in mock mode (provider='%s', key_set=%s) for mode='%s'.",
            provider,
            bool(api_key),
            mode,
        )
        mock_text = MOCK_VOICE_TRANSCRIPTS.get(mode, MOCK_VOICE_TRANSCRIPTS["auto"])
        if campus_scope != "all" and campus_scope not in mock_text:
            mock_text += f" (Campus {campus_scope})"
        return VoiceNoteTranscribeResponse(
            transcript_id=transcript_id,
            transcript=mock_text,
            mode=mode,
            campus_scope=campus_scope,
            requires_review=True,
            audio_filename=file.filename,
        )

    # Prepare multipart request to Whisper STT API (keyless when WHISPER_PROVIDER=local)
    mime_type = file.content_type or "audio/webm"
    filename = file.filename or "audio.webm"
    form_data: dict[str, str] = {
        "model": settings.WHISPER_MODEL,
        "prompt": prompt,
        "response_format": "json",
    }
    if language:
        form_data["language"] = language

    whisper_url = f"{settings.effective_whisper_base_url}/audio/transcriptions"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=settings.WHISPER_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                whisper_url,
                headers=headers,
                data=form_data,
                files={"file": (filename, file_bytes, mime_type)},
            )
    except httpx.TimeoutException as exc:
        logger.error("Whisper STT API timeout after %ss.", settings.WHISPER_TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Le service de transcription Whisper a expiré. Veuillez réessayer.",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Network error while calling Whisper STT API: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Erreur de connexion réseau au service de transcription Whisper.",
        ) from exc

    if resp.status_code != 200:
        logger.error(
            "Whisper STT API returned status %s: %s",
            resp.status_code,
            resp.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Erreur du service de transcription audio Whisper (code {resp.status_code}). "
                "Veuillez réessayer ultérieurement."
            ),
        )

    try:
        data = resp.json()
        transcript = str(data.get("text", "")).strip()
    except Exception as exc:
        logger.error("Failed to parse OpenAI Whisper response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Réponse invalide reçue de l'API OpenAI Whisper.",
        ) from exc

    if not transcript:
        transcript = "(Aucune parole détectée dans l'enregistrement)"

    return VoiceNoteTranscribeResponse(
        transcript_id=transcript_id,
        transcript=transcript,
        mode=mode,
        campus_scope=campus_scope,
        requires_review=True,
        audio_filename=file.filename,
    )
