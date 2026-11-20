"""Async LLM provider connection, VISION prompt engineering, and dialect routing (SA-36)."""

import logging
import re
import time
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.chat import (
    ActionPrompt,
    ChatCitation,
    ChatMessageRequest,
    ChatMessageResponse,
    DetectedDialect,
    LlmHealthResponse,
    VisionSubAgent,
)

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = (
    "Tu es VISION, le superviseur conversationnel d'orientation et de préinscription "
    "de SmartCampus AI pour les écoles IIT et ISB (ainsi que l'extension ISA) "
    "au sein du groupe NAU à Sfax.\n\n"
    "RÈGLES STRICTES ET NON NÉGOCIABLES (SC V4) :\n"
    "1. Multilinguisme : Réponds toujours dans la langue ou le registre utilisé par "
    "l'étudiant (Français, Derja tunisienne en alphabet arabe, ou Arabizi tunisien).\n"
    "2. Zéro hallucination de faits académiques : N'invente JAMAIS de frais de scolarité, "
    "de seuils d'admission, de dates limites ou d'intitulés de cours qui ne figurent pas "
    "explicitement dans le contexte officiel fourni. Si une donnée officielle n'est pas "
    "fournie dans le contexte, indique honnêtement qu'elle doit être consultée dans le "
    "catalogue officiel NAU.\n"
    "3. Gouvernance Human-in-the-Loop (HITL) : Tu ne peux JAMAIS admettre, refuser, "
    "mettre en liste d'attente ni promettre une admission. Seul le Responsable NAU "
    "humain prend et signe la décision finale après entretien.\n"
    "4. Règle « Give or Keep » : L'orientation et les questions-réponses sont 100 % "
    "libres sans compte. L'authentification n'est requise qu'au moment de téléverser "
    "un relevé de notes officiel, sauvegarder une shortlist ou soumettre une "
    "préinscription.\n"
    "5. Style : Sois concis, structuré, bienveillant et cite systématiquement les "
    "documents sources fournis dans le contexte."
)

_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIZI_TOKEN_RE = re.compile(
    r"\b(3aslema|aslema|mar7ba|chnowa|chnoma|chnoua|kifech|kifeh|nheb|n7eb|fama|mte3|mta3|"
    r"barcha|2addech|9adech|gaddech|blhi|bellehi|najem|chkoun|fi|w|wala)\b",
    re.IGNORECASE,
)
_ARABIZI_DIGIT_WORD_RE = re.compile(r"\b[a-z]*[235789][a-z]+\b", re.IGNORECASE)


def detect_dialect(
    text: str,
    language_hint: str = "auto",
    explicit_dialect: DetectedDialect | None = None,
) -> tuple[str, DetectedDialect]:
    """Detect (language_code, DetectedDialect) from student message and optional hints."""
    if explicit_dialect == "Arabizi" or language_hint == "arabizi":
        return "ar-TN-arabizi", "Arabizi"
    if explicit_dialect == "Tunisian_Derja" or language_hint == "derja":
        return "ar-TN", "Tunisian_Derja"
    if explicit_dialect == "French" or language_hint == "fr":
        return "fr", "French"

    if _ARABIC_SCRIPT_RE.search(text):
        return "ar-TN", "Tunisian_Derja"
    if _ARABIZI_TOKEN_RE.search(text) or _ARABIZI_DIGIT_WORD_RE.search(text):
        return "ar-TN-arabizi", "Arabizi"
    return "fr", "French"


def classify_intent_and_subagent(
    text: str,
    *,
    is_authenticated: bool,
) -> tuple[str, VisionSubAgent, ActionPrompt | None, list[str]]:
    """Classify the high-level student intent and target VISION sub-agent."""
    lower = text.lower()

    if any(
        k in lower
        for k in (
            "relevé",
            "releve",
            "ocr",
            "préinscription",
            "preinscription",
            "upload",
            "dossier",
        )
    ):
        action: ActionPrompt = "UPLOAD_RELEVE" if is_authenticated else "AUTH_REQUIRED"
        first_action = (
            "Téléverser mon relevé" if is_authenticated else "Créer un compte / Se connecter"
        )
        return (
            "PREINSCRIPTION_AND_RELEVE",
            "1.6_Registrar",
            action,
            [
                first_action,
                "Voir les pièces requises",
            ],
        )

    if any(
        k in lower
        for k in ("rendez-vous", "rdv", "entretien", "slot", "créneau", "creneau")
    ):
        action = "BOOK_SLOT" if is_authenticated else "AUTH_REQUIRED"
        return (
            "INTERVIEW_SCHEDULING",
            "1.6_Registrar",
            action,
            ["Consulter les créneaux disponibles", "Préparer le QCM d'entretien"],
        )

    if any(
        k in lower
        for k in (
            "éligib",
            "eligib",
            "moyenne",
            "seuil",
            "admissible",
            "score",
            "najem",
            "équivalence",
        )
    ):
        return (
            "CHECK_ELIGIBILITY",
            "1.3_Gatekeeper",
            None,
            ["Simuler mon éligibilité", "Obtenir une recommandation de parcours"],
        )

    if any(
        k in lower
        for k in ("recommand", "conseil", "orient", "hésite", "hesite", "choisir", "ywalmni")
    ):
        return (
            "TRACK_RECOMMENDATION",
            "1.5_Advisor",
            "CONFIRM_SHORTLIST",
            ["Comparer les filières IIT / ISB", "Sauvegarder ma shortlist"],
        )

    if any(
        k in lower
        for k in (
            "frais",
            "tarif",
            "prix",
            "9adech",
            "gaddech",
            "spécialité",
            "specialite",
            "filière",
            "filiere",
            "programme",
            "licence",
            "ingénieur",
            "ingenieur",
            "prépa",
            "prepa",
            "master",
            "iit",
            "isb",
            "isa",
        )
    ):
        return (
            "INQUIRE_PROGRAMS",
            "1.2_Librarian",
            None,
            ["Consulter le catalogue IIT / ISB", "Vérifier les conditions d'accès"],
        )

    return (
        "GENERAL_ORIENTATION",
        "1.1_Listener",
        None,
        ["Découvrir les formations IIT & ISB", "Simuler mon orientation"],
    )


def _build_citations(context_chunks: list[dict[str, Any]] | None) -> list[ChatCitation]:
    if not context_chunks:
        return []
    citations: list[ChatCitation] = []
    for idx, chunk in enumerate(context_chunks, start=1):
        src = str(chunk.get("source_document") or chunk.get("documentTitle") or "Corpus_NAU")
        citations.append(
            ChatCitation(
                id=str(chunk.get("id") or chunk.get("chunk_id") or f"cit-{idx}"),
                documentTitle=str(chunk.get("documentTitle") or src),
                source_document=src,
                school=chunk.get("school"),
                program=chunk.get("program"),
                section=chunk.get("section"),
                page=chunk.get("page"),
                snippet=chunk.get("snippet") or chunk.get("content"),
                chunk_id=str(chunk.get("chunk_id") or f"chunk-{idx}"),
            )
        )
    return citations


def _build_messages(
    payload: ChatMessageRequest,
    detected_dialect: DetectedDialect,
    intent: str,
) -> list[dict[str, str]]:
    system_content = VISION_SYSTEM_PROMPT + (
        f"\n\n[MÉTADONNÉES TOUR COURANT]\n"
        f"- Dialecte détecté : {detected_dialect}\n"
        f"- Intention détectée : {intent}\n"
    )
    if payload.context_chunks:
        formatted_chunks = "\n".join(
            f"- [{c.get('source_document', 'Corpus_NAU')}] "
            f"{c.get('content') or c.get('snippet', '')}"
            for c in payload.context_chunks
        )
        system_content += f"\n[CONTEXTE OFFICIEL RAG / CATALOGUE]\n{formatted_chunks}\n"
    else:
        system_content += (
            "\n[CONTEXTE OFFICIEL RAG / CATALOGUE]\n"
            "Aucun extrait spécifique fourni pour ce tour. Ne cite aucun montant de frais, "
            "seuil chiffré ou date limite de mémoire ; invite l'étudiant à préciser sa filière "
            "(IIT, ISB ou extension ISA) ou son profil (Bac, Licence, Prépa)."
        )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    if payload.history:
        for turn in payload.history[-16:]:
            messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": payload.message})
    return messages


def _generate_deterministic_fallback_reply(
    *,
    dialect: DetectedDialect,
    intent: str,
    is_authenticated: bool,
    citations: list[ChatCitation],
) -> str:
    """Safe, zero-hallucination fallback reply when no external LLM API key is configured."""
    if dialect == "Arabizi":
        if intent == "PREINSCRIPTION_AND_RELEVE" and not is_authenticated:
            return (
                "Mar7ba bik fi SmartCampus AI! Bech tsob relevé de notes mte3ek wala tabda "
                "l'préinscription, lazem ta3mel compte sghir b'code OTP (règle Give or Keep)."
            )
        if intent == "INQUIRE_PROGRAMS":
            return (
                "Mar7ba! Fi NAU fama l'IIT (Cycle Ingénieur, Prépa, Licence) w l'ISB "
                "(Licences & Masters Business), bil idhafa lil extension ISA. "
                "Qolli chnowa l'Bac wala l'spécialité li t7eb 3liha bech na3tik les détails "
                "men l'catalogue officiel!"
            )
        return (
            "3aslema! Ena VISION, l'conseiller d'orientation mte3ek l'IIT w l'ISB. "
            "T7eb nes2lek 3al spécialités, l'éligibilité mte3ek, wala l'préinscription?"
        )

    if dialect == "Tunisian_Derja":
        if intent == "PREINSCRIPTION_AND_RELEVE" and not is_authenticated:
            return (
                "مرحبا بيك في SmartCampus AI! باش تنجم ترفع كشف الأعداد (relevé de notes) "
                "أو تبدأ التسجيل الأولي، يلزمك تسجل الدخول برمز OTP أولاً."
            )
        return (
            "مرحبا بيك! أنا VISION مستشارك الذكي للتوجيه الجامعي في IIT و ISB. "
            "أعطيني شعبة البكالوريا أو الاختصاص الي تحب عليه باش نعاونك بالمعلومات الرسمية."
        )

    if intent == "PREINSCRIPTION_AND_RELEVE" and not is_authenticated:
        return (
            "Bienvenue sur SmartCampus AI ! Conformément à notre règle « Give or Keep », "
            "l'orientation est libre mais la connexion (par OTP ou Google OAuth) est requise "
            "avant de téléverser votre relevé de notes officiel ou de soumettre votre "
            "préinscription."
        )

    if citations:
        top = citations[0]
        return (
            f"Selon la source officielle ({top.documentTitle}) : {top.snippet or ''}\n\n"
            "Souhaitez-vous vérifier votre éligibilité ou comparer les parcours IIT et ISB ?"
        ).strip()

    if intent == "INQUIRE_PROGRAMS":
        return (
            "SmartCampus AI vous accompagne pour les formations de l'IIT (Cycle Préparatoire, "
            "Licence, Cycle Ingénieur) et de l'ISB (Licences et Masters en Business & Management), "
            "ainsi que l'extension ISA. Précisez votre diplôme actuel et la filière souhaitée "
            "pour consulter les données officielles du catalogue."
        )

    return (
        "Bonjour ! Je suis VISION, votre assistant d'orientation pour l'IIT et l'ISB "
        "(groupe NAU). Vous pouvez me parler en Français, en Derja tunisienne ou en Arabizi. "
        "Quel est votre parcours actuel (Bac, Prépa, Licence) et quels domaines vous intéressent ?"
    )


async def _call_openai_compatible_api(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Call an OpenAI-compatible /chat/completions endpoint via httpx."""
    base_url = settings.LLM_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    request_body = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        logger.warning("LLM request timed out after %ss: %s", settings.LLM_TIMEOUT_SECONDS, exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The LLM service timed out while generating a response.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("LLM HTTP transport error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach the configured LLM provider.",
        ) from exc

    if resp.status_code != 200:
        logger.warning("LLM provider returned HTTP %s: %s", resp.status_code, resp.text[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider returned error status {resp.status_code}.",
        )

    try:
        data = resp.json()
        reply_text = str(data["choices"][0]["message"]["content"]).strip()
        model_used = str(data.get("model") or settings.LLM_MODEL)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Malformed response payload received from LLM provider.",
        ) from exc

    if not reply_text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM provider returned an empty response.",
        )

    return reply_text, model_used


async def generate_chat_reply(
    payload: ChatMessageRequest,
    *,
    session_id: str | None = None,
    is_authenticated: bool = False,
) -> ChatMessageResponse:
    """Orchestrate dialect detection, intent routing, and LLM completion (or safe fallback)."""
    start_ns = time.perf_counter_ns()
    resolved_session_id = session_id or payload.session_id or str(uuid4())

    lang_code, dialect = detect_dialect(
        payload.message,
        language_hint=payload.language_hint,
        explicit_dialect=payload.detectedDialect,
    )
    intent, sub_agent, action_prompt, suggested_actions = classify_intent_and_subagent(
        payload.message,
        is_authenticated=is_authenticated,
    )
    citations = _build_citations(payload.context_chunks)
    messages = _build_messages(payload, dialect, intent)

    provider = settings.LLM_PROVIDER.lower().strip()
    can_call_remote = provider == "ollama" or (
        provider == "openai_compatible" and bool(settings.LLM_API_KEY.strip())
    )

    used_fallback = False
    if provider == "mock" or (not can_call_remote and settings.LLM_FALLBACK_TO_MOCK):
        used_fallback = True
        reply_text = _generate_deterministic_fallback_reply(
            dialect=dialect,
            intent=intent,
            is_authenticated=is_authenticated,
            citations=citations,
        )
        model_used = "smartcampus-fallback-v1"
    elif not can_call_remote:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM_API_KEY is not configured and LLM_FALLBACK_TO_MOCK is disabled.",
        )
    else:
        reply_text, model_used = await _call_openai_compatible_api(messages)

    elapsed_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))

    return ChatMessageResponse(
        session_id=resolved_session_id,
        sessionId=resolved_session_id,
        reply_text=reply_text,
        reply=reply_text,
        sub_agent=sub_agent,
        subAgent=sub_agent,
        detected_language=lang_code,
        detectedDialect=dialect,
        intent=intent,
        citations=citations,
        suggested_actions=suggested_actions,
        actionPrompt=action_prompt,
        provider=provider,
        model_used=model_used,
        latency_ms=elapsed_ms,
        used_fallback=used_fallback,
    )


def get_llm_health() -> LlmHealthResponse:
    """Return current LLM provider configuration and readiness status."""
    provider = settings.LLM_PROVIDER.lower().strip()
    has_key = bool(settings.LLM_API_KEY.strip())

    if provider == "ollama" or (provider == "openai_compatible" and has_key):
        llm_status = "ready"
    elif provider == "mock" or settings.LLM_FALLBACK_TO_MOCK:
        llm_status = "fallback_only"
    else:
        llm_status = "misconfigured"

    return LlmHealthResponse(
        status=llm_status,
        provider=provider,
        model=settings.LLM_MODEL,
        base_url=settings.LLM_BASE_URL,
        api_key_configured=has_key,
        fallback_enabled=settings.LLM_FALLBACK_TO_MOCK,
    )

