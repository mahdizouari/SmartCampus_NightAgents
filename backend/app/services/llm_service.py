"""Async LLM provider connection, VISION prompt engineering, and dialect routing (SA-36 & SA-34)."""

import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.arabizi_normalizer import normalize_arabizi_to_french
from app.services.redis_service import (
    GuestSessionService as GuestSessionService,
)
from app.services.redis_service import (
    HotConversationService as HotConversationService,
)

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = (
    "You are VISION, the conversational academic orientation and pre-registration supervisor "
    "for SmartCampus AI covering the IIT and ISB schools (plus the announced ISA extension) "
    "within the NAU group in Sfax, Tunisia. You welcome any Visitor (unauthenticated visitor: "
    "high-school student, university student, or parent/relative inquiring on behalf of a "
    "prospective candidate) as well as any authenticated Candidate.\n\n"
    "STRICT AND NON-NEGOTIABLE RULES (SC V3):\n"
    "1. Multilingualism: Always reply in the exact language or register used by the Visitor "
    "or Candidate (French, Modern Standard Arabic / Fusha, Tunisian Derja in Arabic script, or "
    "natural Tunisian Derja / Arabizi for Arabizi and FR-TN code-switching queries). "
    "NEVER reply in Classical/Modern Standard Arabic (Fusha) to a Derja or Arabizi message.\n"
    "2. Zero hallucination of academic facts: NEVER invent tuition fees, admission thresholds, "
    "deadlines, guaranteed scholarships, or non-existent programs (e.g., medicine, pharmacy, "
    "law degree, astrophysics, criminology, agricultural/veterinary engineering). If an "
    "official fact is not provided in the context, state honestly that it must be verified "
    "in the official NAU catalog managed by the school Responsable.\n"
    "3. Human-in-the-Loop (HITL) Governance: You may NEVER admit, reject, waitlist, promise "
    "admission, or issue an official certificate. Only the human NAU Responsable makes and "
    "signs the final admission decision.\n"
    "4. 'Give or Keep' Rule (Visitor -> Candidate): Orientation and Q&A are 100% open to "
    "any Visitor without an account (7-day anonymous temporary session). Authentication "
    "(OTP or Google OAuth, which automatically merges the session and promotes the Visitor "
    "to Candidate) is required only when uploading an official grade transcript, saving a "
    "shortlist, booking an interview slot, or submitting a pre-registration.\n"
    "5. Dialect / Arabizi ambiguity fallback: If a message is too ambiguous, abbreviated, or "
    "noisy to identify the Visitor's or Candidate's intent reliably, never guess; politely "
    "ask them to rephrase their question in simple French.\n"
    "6. Style: Be concise, structured, and helpful, and systematically cite the official "
    "source documents provided in the context using [Source: ...].\n"
    "7. Administrative rigor and declarative vs. official separation: NEVER present "
    "unverified, speculative, or self-declared administrative information as official facts. "
    "Any information about tuition fees, scholarships, admission thresholds, or equivalences "
    "must come exclusively from the provided official RAG catalog excerpts or structured DB "
    "records maintained by the Responsable. When no formal official source is in the context, "
    "explicitly remind the Visitor or Candidate that the information must be confirmed by "
    "the NAU Responsable or administrative secretariat."
)

FRENCH_REFORMULATION_FALLBACK_ARABIZI = (
    "Samahni, ma fhemtech bethabt chnowa t7eb ta3ref (el message fih abréviations walla "
    "manque de précision). Tnajem t3awed tektbli sou2elek bel français clair, mathalan : "
    "« Quelles sont les filières ingénieur à l'IIT ? » ou « Suis-je éligible avec un Bac "
    "Mathématiques ? »\n\n"
    "Pourriez-vous reformuler votre question en français afin que je puisse vous orienter "
    "précisément sur l'IIT, l'ISB ou l'ISA ?"
)

FRENCH_REFORMULATION_FALLBACK_DERJA = (
    "سامحني، ما فهمتش بالضبط شنوة تقصد في سؤالك. تنجم تعاود تكتبلي سؤالك بالفرنسية بوضوح "
    "باش نجم نعاونك بالمعلومات الرسمية متاع IIT و ISB و ISA؟\n\n"
    "Pourriez-vous reformuler votre question en français (par exemple en précisant l'école "
    "IIT/ISB/ISA, la filière souhaitée ou votre diplôme actuel) ?"
)

FRENCH_REFORMULATION_FALLBACK_FR = (
    "Je n'ai pas compris votre demande avec suffisamment de certitude pour vous répondre "
    "sans risque d'erreur. Pourriez-vous reformuler votre question en français en précisant "
    "l'école (IIT, ISB ou ISA), la filière visée ou votre diplôme actuel ?"
)

LOW_CONFIDENCE_THRESHOLD = 0.45

_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_WORD_RE = re.compile(r"[\u0600-\u06FF]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9']+")

_ARABIZI_TOKEN_RE = re.compile(
    r"\b("
    r"3aslema|aslema|3slm|slm|mar7ba|marhba|chnowa|chnoma|chnoua|chnouwa|chnouma|"
    r"kifech|kifeh|kfh|nheb|n7eb|fama|famach|mte3|mta3|mte3i|mte3ek|mte3kom|"
    r"barcha|2addech|9adech|9dh|gaddech|blhi|bellehi|brbi|najem|tnajem|ynajmou|"
    r"chkoun|walla|wala|fel|lel|mel|bech|bch|n3ml|na3mel|n3amer|na5ou|ne5ou|"
    r"nod5el|net5arej|net7adeth|n5ayar|trfadhnii|za3ma|3andi|3andkom|3anna|"
    r"t5adem|ya9raw|9rayet|ywalmni|ywalmouni|yzidni|hedhi|hedha|heki|3awenni|"
    r"tawa|taw|win|mawjouda|chwaya|a3tini|tsob|lfac|l3am|jey|3asma|bghit|na3ref|"
    r"ma9boul|ta3tini|m3a|m3akom|ghir|snin|3am|3amin"
    r")\b",
    re.IGNORECASE,
)
_ARABIZI_DIGIT_WORD_RE = re.compile(r"\b[a-z]*[235789][a-z]+\b", re.IGNORECASE)

_TUNISIAN_DERJA_ARABIC_RE = re.compile(
    r"(شنوة|شنيما|شنية|كيفاش|قداش|بقداش|بربي|نجم|تنجم|فما|متاع|حاير|زعمة|"
    r"برشا|توة|موش|ماكش|عسلامة|هكا|دوسيا|رونديفو|بريبا|"
    r"باسرال|ليسونس|الفاك|كونت|ماخذ|إنتغري|تخدم|تخدّم|قراية|مريقل|يرفضلي|"
    r"يقبلني|نتسجل|نحكي|معاكم|نبرمج|نتفاهمو|عنا|عندكم|نحب نعرف|"
    r"مانجمش|ما فماش|إيليجيبل|جيني لوجيسيال|جيني ميكانيك|أنجينيور|أرشيتكتور|أركيتكتور)|"
    r"(?<![\u0600-\u06FF])(الي|اللي|بش|باش)(?![\u0600-\u06FF])"
)

_FUSHA_ARABIC_RE = re.compile(
    r"(ما هي|ما الفرق|هل تقدم|هل تمنح|هل يشترط|هل يتخذ|هل تضمن|هل يشمل|"
    r"كم تبلغ|أين تقع|كيف يمكن|أحمل إجازة|المرحلة التحضيرية|الشهادة الوطنية|"
    r"المعهد الدولي|مدرسة الأعمال|المجمع الأكاديمي|المترشح|التشريعات|الوثائق الرسمية|"
    r"الآفاق المهنية|ماجستير|الماستر المهني)"
)

_VAGUE_ARABIZI_OR_DERJA_RE = re.compile(
    r"\b(hedhi|hedha|heki|kfh|n3ml|9dh|3slm)\b|"
    r"(?<![\u0600-\u06FF])(هكا|هاكي|هذي)(?![\u0600-\u06FF])",
    re.IGNORECASE,
)
_NOISE_GIBBERISH_RE = re.compile(r"\b(?=[a-z]*\d)(?=[0-9]*[a-z])[a-z0-9]{3,}\b", re.IGNORECASE)
_DOMAIN_ANCHOR_RE = re.compile(
    r"(iit|isb|isa|nau|sfax|صفاقس|glid|sdia|arsi|prépa|prepa|بريبا|تحضيرية|"
    r"licence|ليسونس|إجازة|master|ماستر|ماجستير|mba|msc|ingénieur|ingenieur|أنجينيور|هندسة|"
    r"architecture|أركيتكتور|أرشيتكتور|عمارة|finance|فينونس|مالية|marketing|تسويق|"
    r"business|إدارة الأعمال|mécanique|ميكانيك|civil|سيفيل|industriel|énergétique|"
    r"bac|باك|بكالوريا|passerelle|باسرال|معابر|éligib|eligib|moyenne|معدل|شروط|"
    r"relevé|releve|رولفي|كشف|الأعداد|النقاط|dossier|دوسيا|ملف|préinscription|preinscription|ترسيم|تسجيل|"
    r"rendez-vous|rdv|رونديفو|موعد|مقابلة|entretien|slot|créneau|creneau|"
    r"guest|invité|انفيتي|كونت|compte|inpdp|données|بيانات|الروبو|intelligence artificielle|l'ia|"
    r"médecine|medecine|طب|صيدلة|pharmacie|droit|درو|حقوق|astrophysique|شريعة|"
    r"criminologie|agricole|vétérinaire|veterinaire|permis de conduire|erasmus|إيراسموس|"
    r"attestation officielle|وثائق|أوراق|papiers|documents|débouchés|الآفاق|spécialité|filière|"
    r"اختصاص|شعبة|فروع|توجيه|orient|recommand|compar|الفرق|حاير|n5ayar|"
    r"bourse|منحة|nabda|نبدا|commencer|frais|tarif|9adech|قداش)",
    re.IGNORECASE,
)
_GREETING_ONLY_RE = re.compile(
    r"^\s*(bonjour|bonsoir|salut|coucou|hello|hi|salam|slm|3aslema|aslema|mar7ba|"
    r"مرحبا|عسلامة|السلام عليكم|اهلا|أهلا)(\s+(vision|smartcampus))?[\s!?.]*$",
    re.IGNORECASE,
)


def _expected_register_label(lang_code: str, dialect: DetectedDialect) -> str:
    if lang_code == "ar":
        return "Arabe standard soutenu (Fusha / الفصحى) uniquement, sans dialecte"
    if lang_code == "ar-TN":
        return "Derja tunisienne authentique en alphabet arabe (الدارجة التونسية), sans Fusha"
    if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
        return "Derja tunisienne naturelle (style conversationnel tunisien / Arabizi), sans Fusha"
    return "Français fluide et professionnel"


def detect_dialect(
    text: str,
    language_hint: str = "auto",
    explicit_dialect: DetectedDialect | None = None,
) -> tuple[str, DetectedDialect]:
    """Detect (language_code, DetectedDialect) across French, Fusha, Derja, Arabizi & FR-TN mix."""
    if language_hint == "arabizi":
        return "ar-TN-arabizi", "Arabizi"
    if language_hint == "derja":
        return "ar-TN", "Tunisian_Derja"
    if language_hint == "fr":
        return "fr", "French"

    has_arabic = bool(_ARABIC_SCRIPT_RE.search(text))
    arabizi_hits = len(_ARABIZI_TOKEN_RE.findall(text)) + len(
        _ARABIZI_DIGIT_WORD_RE.findall(text)
    )

    if has_arabic:
        arabic_words = _ARABIC_WORD_RE.findall(text)
        latin_words = _LATIN_WORD_RE.findall(text)
        # Bi-script Arabizi with a stray Arabic word (e.g. AB-10 "win mawjouda el fac بالضبط ?")
        if (
            arabizi_hits >= 2
            and len(arabic_words) <= 2
            and len(latin_words) >= len(arabic_words) * 3
        ):
            return "ar-TN-arabizi", "Arabizi"
        # Distinguish Tunisian Derja (Arabic script) from Modern Standard Arabic (Fusha)
        if _TUNISIAN_DERJA_ARABIC_RE.search(text):
            return "ar-TN", "Tunisian_Derja"
        if _FUSHA_ARABIC_RE.search(text):
            return "ar", "Tunisian_Derja"
        return "ar-TN", "Tunisian_Derja"

    if arabizi_hits > 0:
        return "ar-TN-arabizi", "Arabizi"

    if explicit_dialect == "Arabizi":
        return "ar-TN-arabizi", "Arabizi"
    if explicit_dialect == "Tunisian_Derja":
        return "ar-TN", "Tunisian_Derja"
    if explicit_dialect == "French":
        return "fr", "French"
    return "fr", "French"


def assess_comprehension_confidence(
    text: str,
    *,
    lang_code: str,
    intent: str,
) -> tuple[float, bool]:
    """Estimate comprehension confidence and decide whether to request a French reformulation."""
    stripped = text.strip()
    if _GREETING_ONLY_RE.match(stripped):
        return 0.95, False

    has_domain_anchor = bool(_DOMAIN_ANCHOR_RE.search(stripped))
    has_vague_markers = bool(_VAGUE_ARABIZI_OR_DERJA_RE.search(stripped))

    if intent != "GENERAL_ORIENTATION" and intent != "CLARIFICATION_REQUIRED":
        return (0.92 if has_domain_anchor else 0.85), False

    if not has_domain_anchor:
        # Check for unintelligible/noisy alphanumeric tokens or vague dialect demonstratives
        non_arabizi_noise = [
            tok
            for tok in _NOISE_GIBBERISH_RE.findall(stripped)
            if not _ARABIZI_TOKEN_RE.fullmatch(tok)
        ]
        if has_vague_markers or non_arabizi_noise:
            return 0.25, True

        # Only trigger hard short-circuit for isolated single-token / unintelligible utterances;
        # normal short questions (e.g. "fama bourse ?", "kifech nabda ?") pass through to the LLM.
        word_count = len(re.findall(r"[\w\u0600-\u06FF]+", stripped))
        if lang_code in {"ar-TN", "ar-TN-arabizi"} and word_count <= 2:
            return 0.35, True

    return 0.80, False


def _build_reformulation_fallback_reply(lang_code: str, dialect: DetectedDialect) -> str:
    if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
        return FRENCH_REFORMULATION_FALLBACK_ARABIZI
    if lang_code in {"ar-TN", "ar"}:
        return FRENCH_REFORMULATION_FALLBACK_DERJA
    return FRENCH_REFORMULATION_FALLBACK_FR


def classify_intent_and_subagent(
    text: str,
    *,
    is_authenticated: bool,
    lang_code: str | None = None,
) -> tuple[str, VisionSubAgent, ActionPrompt | None, list[str]]:
    """Classify student intent and target VISION sub-agent across FR, Fusha, Derja & Arabizi."""
    lower = text.lower()

    # 1. Out-of-corpus traps & prohibited official attestations (zero-hallucination guardrail)
    if any(
        k in lower
        for k in (
            "médecine",
            "medecine",
            "كلية طب",
            "طب الأسنان",
            "الطب",
            "صيدلة",
            "pharmacie",
            "درو",
            "(droit)",
            "faculté de droit",
            "حقوق",
            "permis de conduire",
            "astrophysique",
            "cosmologie",
            "شريعة",
            "فقه",
            "bourse garantie",
            "منحة مضمونة",
            "erasmus",
            "إيراسموس",
            "criminologie",
            "attestation officielle",
            "agricole",
            "vétérinaire",
            "veterinaire",
        )
    ):
        return (
            "OUT_OF_CORPUS_TRAP",
            "1.1_Listener",
            None,
            ["Découvrir les vraies filières IIT / ISB / ISA", "Parler à un conseiller NAU"],
        )

    # 2. Human-in-the-Loop (HITL) admission governance questions (natural AI/robot + decision stems)
    has_ai_actor = any(
        k in lower
        for k in (
            "intelligence artificielle",
            "l'ia",
            "الذكاء الاصطناعي",
            "روبو",
            "robot",
            "jury humain",
        )
    )
    has_admission_decision = any(
        k in lower
        for k in (
            "accept",
            "rejet",
            "refus",
            "رفض",
            "يرفض",
            "قبول",
            "يقبل",
            "trfadh",
            "ta9bel",
            "décision",
            "قرار",
        )
    )
    if has_ai_actor and has_admission_decision:
        return (
            "ADMISSION_GOVERNANCE",
            "1.1_Listener",
            None,
            ["Comprendre le processus d'admission humaine", "Consulter le catalogue IIT / ISB"],
        )

    # 3. Anonymous guest session policy ("Give or Keep")
    has_guest_cue = any(k in lower for k in ("invité", "guest", "anonyme", "إنفيتي"))
    has_without_cue = any(
        k in lower for k in ("sans créer", "sans compte", "من غير", "men ghir")
    )
    has_no_account_cue = has_without_cue and any(k in lower for k in ("compte", "كونت"))
    if has_guest_cue or has_no_account_cue:
        return (
            "GUEST_SESSION_POLICY",
            "1.1_Listener",
            None,
            ["Découvrir les formations sans compte", "Simuler mon éligibilité"],
        )

    # 4. Data privacy & INPDP compliance
    if any(
        k in lower
        for k in (
            "inpdp",
            "données personnelles",
            "confidentialité",
            "حماية بيانات",
            "معطيات شخصية",
            "خصوصية",
        )
    ):
        return (
            "DATA_PRIVACY",
            "1.1_Listener",
            None,
            ["Consulter la politique INPDP", "Découvrir les formations IIT / ISB"],
        )

    # 5. Interview appointment scheduling (FR, Fusha, Derja, Arabizi)
    if any(
        k in lower
        for k in (
            "rendez-vous",
            "rdv",
            "entretien",
            "slot",
            "créneau",
            "creneau",
            "رونديفو",
            "موعد",
        )
    ):
        action: ActionPrompt = "BOOK_SLOT" if is_authenticated else "AUTH_REQUIRED"
        return (
            "INTERVIEW_SCHEDULING",
            "1.6_Registrar",
            action,
            ["Consulter les créneaux disponibles", "Préparer le QCM d'entretien"],
        )

    # 6. Pre-inscription documents & transcript upload (FR, Fusha, Derja, Arabizi)
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
            "documents",
            "papiers",
            "وثائق",
            "أوراق",
            "ملف",
            "دوسيا",
            "كشف",
            "رولفي",
        )
    ):
        action = "UPLOAD_RELEVE" if is_authenticated else "AUTH_REQUIRED"
        first_action = (
            "Téléverser mon relevé" if is_authenticated else "Créer un compte / Se connecter"
        )
        return (
            "PREINSCRIPTION_AND_RELEVE",
            "1.6_Registrar",
            action,
            [first_action, "Voir les pièces requises"],
        )

    # 7. Advisory track comparison & recommendation (FR, Fusha, Derja, Arabizi)
    is_prepa_comparison = any(k in lower for k in ("prépa", "prepa", "بريبا"))
    has_comparison_stem = any(
        k in lower
        for k in (
            "recommand",
            "conseil",
            "orient",
            "hésite",
            "hesite",
            "choisir",
            "compar",
            "ywalm",
            "n5ayar",
            "نخير",
            "حاير",
        )
    ) or ("الفرق" in lower and not is_prepa_comparison)
    if has_comparison_stem:
        return (
            "TRACK_RECOMMENDATION",
            "1.5_Advisor",
            "CONFIRM_SHORTLIST",
            ["Comparer les filières IIT / ISB", "Sauvegarder ma shortlist"],
        )

    # 8. Eligibility, prerequisites & gateways (FR, Fusha, Derja, Arabizi)
    has_bac_or_degree_admission = any(
        k in lower for k in ("bac", "باك", "بكالوريا", "إجازة", "ليسونس", "licence")
    ) and any(
        k in lower
        for k in ("requis", "intégrer", "nod5el", "ندخل", "ترشح", "معدل", "moyenne", "شروط")
    )
    if has_bac_or_degree_admission or any(
        k in lower
        for k in (
            "éligib",
            "eligib",
            "moyenne",
            "معدل",
            "seuil",
            "admissible",
            "score",
            "équivalence",
            "passerelle",
            "باسرال",
            "معابر",
            "شروط القبول",
            "اختبار قبول",
            "bac+3",
            "باك+3",
        )
    ):
        return (
            "CHECK_ELIGIBILITY",
            "1.3_Gatekeeper",
            None,
            ["Simuler mon éligibilité", "Obtenir une recommandation de parcours"],
        )

    # 9. Program catalog, details, duration, accreditation & campus location
    if any(
        k in lower
        for k in (
            "frais",
            "tarif",
            "prix",
            "9adech",
            "gaddech",
            "قداش",
            "spécialité",
            "specialite",
            "filière",
            "filiere",
            "programme",
            "licence",
            "ليسونس",
            "إجازة",
            "ingénieur",
            "ingenieur",
            "أنجينيور",
            "هندسة",
            "prépa",
            "prepa",
            "بريبا",
            "تحضيرية",
            "master",
            "ماستر",
            "ماجستير",
            "iit",
            "isb",
            "isa",
            "sfax",
            "صفاقس",
            "مقرات",
            "الفاك",
            "أرشيتكتور",
            "أركيتكتور",
            "عمارة",
            "آفاق",
            "ميكانيك",
            "شبكات",
        )
    ):
        return (
            "INQUIRE_PROGRAMS",
            "1.2_Librarian",
            None,
            ["Consulter le catalogue IIT / ISB", "Vérifier les conditions d'accès"],
        )

    # 10. Low-comprehension / ambiguous dialect check -> French reformulation fallback
    resolved_lang = lang_code if lang_code is not None else detect_dialect(text)[0]
    _, needs_reformulation = assess_comprehension_confidence(
        text, lang_code=resolved_lang, intent="GENERAL_ORIENTATION"
    )
    if needs_reformulation:
        return (
            "CLARIFICATION_REQUIRED",
            "1.1_Listener",
            None,
            [
                "Reformuler ma question en français",
                "Découvrir les formations IIT & ISB",
                "Vérifier mon éligibilité Bac / Passerelle",
            ],
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


def build_rolling_summary(
    declared_facts: dict[str, Any] | None = None,
    recent_turns: list[Any] | None = None,
    existing_summary: str | None = None,
    latest_query: str | None = None,
) -> str:
    """Build or update a bounded (<= 300 tokens) rolling orientation profile summary (SA-37)."""
    facts = declared_facts or {}
    elements: list[str] = []

    school = facts.get("school_target") or facts.get("target_school")
    if school:
        elements.append(f"Établissement cible : {str(school).upper()}")

    edu_level = facts.get("current_education_level") or facts.get("education_level")
    if edu_level:
        elements.append(f"Niveau d'études : {edu_level}")

    specialty = (
        facts.get("current_specialty")
        or facts.get("specialty")
        or facts.get("filiere")
    )
    if specialty:
        elements.append(f"Spécialité/Filière : {specialty}")

    bac_section = facts.get("bac_section") or facts.get("section")
    if bac_section:
        elements.append(f"Baccalauréat : {bac_section}")

    avg = facts.get("declared_overall_avg") or facts.get("moyenne")
    if avg is not None:
        elements.append(f"Moyenne déclarée : {avg}/20")

    lang = facts.get("preferred_language")
    if lang:
        elements.append(f"Langue préférée : {lang}")

    topic_cues: list[str] = []
    texts_to_scan: list[str] = [
        (
            getattr(t, "content", None)
            or (t.get("content") if isinstance(t, dict) else "")
        ).lower()
        for t in (recent_turns or [])[-6:]
    ]
    if latest_query:
        texts_to_scan.append(latest_query.lower())

    for content in texts_to_scan:
        if any(k in content for k in ["passerelle", "licence", "bac+3"]):
            topic_cues.append("passerelle Bac+3")
        if any(k in content for k in ["glid", "logiciel"]):
            topic_cues.append("Génie Logiciel (GLID)")
        if re.search(r"\b(sdia|data|ia)\b|intelligence artificielle", content):
            topic_cues.append("Data & IA (SDIA)")
        if any(k in content for k in ["rendez-vous", "entretien", "créneau"]):
            topic_cues.append("prise de rendez-vous")
        if any(k in content for k in ["frais", "tarif", "paiement"]):
            topic_cues.append("tarifs officiels")

    unique_topics = sorted(set(topic_cues))
    if unique_topics:
        elements.append(f"Sujets abordés : {', '.join(unique_topics)}")

    if existing_summary and len(elements) <= 1:
        return existing_summary[:1200]

    if not elements:
        summary = "Orientation préliminaire en cours (profil académique en attente de précision)."
    else:
        summary = " | ".join(elements)

    if len(summary) > 1200:
        summary = summary[:1197] + "..."
    return summary


def _build_messages(
    payload: ChatMessageRequest,
    lang_code: str,
    detected_dialect: DetectedDialect,
    intent: str,
    *,
    history_turns: list[Any] | None = None,
    rolling_summary: str | None = None,
    declared_facts: dict[str, Any] | None = None,
    sub_agent_addon: str | None = None,
) -> list[dict[str, str]]:
    register_instruction = _expected_register_label(lang_code, detected_dialect)
    system_content = VISION_SYSTEM_PROMPT + (
        f"\n\n[MÉTADONNÉES TOUR COURANT]\n"
        f"- Code langue détecté : {lang_code}\n"
        f"- Dialecte détecté : {detected_dialect}\n"
        f"- Registre de réponse obligatoire : {register_instruction}\n"
        f"- Intention détectée : {intent}\n"
    )
    if sub_agent_addon:
        system_content += f"\n{sub_agent_addon}\n"
    if getattr(payload, "campus_scope", None) and payload.campus_scope != "all":
        system_content += (
            f"- Périmètre d'établissement ciblé (campus_scope) : {payload.campus_scope}\n"
            "- Consigne de périmètre : Focalise en priorité tes conseils et filières "
            f"sur l'établissement {payload.campus_scope} (Groupe NAU Sfax).\n"
        )
    arabizi_norm = normalize_arabizi_to_french(payload.message)
    if arabizi_norm.is_arabizi or detected_dialect == "Arabizi":
        system_content += (
            f"- Pré-traitement Arabizi -> Français structuré (Bonus-4) : "
            f"{arabizi_norm.normalized_french}\n"
        )
    if intent == "OUT_OF_CORPUS_TRAP":
        system_content += (
            "- ALERTE GARDE-FOU : La requête semble porter sur une filière hors-catalogue "
            "ou une attestation/promesse interdite. Refuse poliment sans rien inventer et "
            "rappelle uniquement les vraies écoles NAU (IIT, ISB, ISA).\n"
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
            "seuil chiffré ou date limite de mémoire ; invite le visiteur ou candidat à "
            "préciser sa filière (IIT, ISB ou extension ISA) ou son profil (Bac, Licence, Prépa)."
        )

    # Inject rolling memory & profile summary if available (SA-37)
    if declared_facts or rolling_summary:
        system_content += "\n[PROFIL CANDIDAT & SYNTHÈSE GLISSANTE (MÉMOIRE TIÈDE)]\n"
        if declared_facts:
            facts_str = ", ".join(
                f"{k}: {v}" for k, v in declared_facts.items() if v is not None and k != "facts"
            )
            if facts_str:
                system_content += f"- Faits académiques déclarés : {facts_str}\n"
        if rolling_summary:
            system_content += (
                f"- Synthèse glissante de l'orientation (<= 300 tokens) : {rolling_summary}\n"
            )

    # Context window bounded: last 6-8 turns (12-16 messages max, oldest to newest)
    raw_history = history_turns if history_turns is not None else (payload.history or [])
    bounded_turns = list(raw_history[-16:])
    if (
        bounded_turns
        and (
            getattr(bounded_turns[-1], "role", None)
            or (bounded_turns[-1].get("role") if isinstance(bounded_turns[-1], dict) else None)
        )
        == "user"
        and (
            getattr(bounded_turns[-1], "content", None)
            or (bounded_turns[-1].get("content") if isinstance(bounded_turns[-1], dict) else None)
        )
        == payload.message
    ):
        bounded_turns.pop()

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    for turn in bounded_turns:
        role = getattr(turn, "role", None) or (
            turn.get("role") if isinstance(turn, dict) else "user"
        )
        content = getattr(turn, "content", None) or (
            turn.get("content") if isinstance(turn, dict) else ""
        )
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": payload.message})
    return messages


def _generate_deterministic_fallback_reply(
    *,
    lang_code: str,
    dialect: DetectedDialect,
    intent: str,
    is_authenticated: bool,
    citations: list[ChatCitation],
) -> str:
    """Safe, zero-hallucination fallback reply when no external LLM API key is configured."""
    if intent == "CLARIFICATION_REQUIRED":
        return _build_reformulation_fallback_reply(lang_code, dialect)

    if intent == "OUT_OF_CORPUS_TRAP":
        if lang_code == "ar":
            return (
                "عذراً، هذا التخصص أو الطلب غير متوفر ضمن برامج المجمع الأكاديمي NAU. "
                "تقتصر مؤسساتنا على الهندسة والتكنولوجيا (IIT)، إدارة الأعمال والمالية (ISB)، "
                "والهندسة المعمارية (ISA)، ولا يمكن للذكاء الاصطناعي إصدار شهادات قبول رسمية."
            )
        if lang_code == "ar-TN":
            return (
                "سامحني، الاختصاص ولا الطلب هذا موش موجود في مؤسسات الـ NAU. "
                "عنا كان الـ IIT للهندسة والتكنولوجيا، الـ ISB للتصرف والمالية، و الـ ISA "
                "للهندسة المعمارية، وما نجموش نخترعو برامج ولا نعطيو قرارات قبول رسمية."
            )
        if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
            return (
                "Samahni, el filière walla el talab hedha mahouch mawjoud fel NAU. "
                "3anna ken l'IIT (Ingénierie & Tech), l'ISB (Business & Finance) w l'ISA "
                "(Architecture 6 ans), w ma najemch na5tare3 programmes walla attestation."
            )
        return (
            "Ce programme ou cette demande ne relève pas de l'offre officielle du groupe NAU "
            "(IIT pour l'ingénierie/technologie, ISB pour le management/finance, ISA pour "
            "l'architecture). En tant qu'assistant IA, je ne peux ni inventer de cursus ni "
            "délivrer d'attestation officielle d'admission."
        )

    if intent == "ADMISSION_GOVERNANCE":
        if lang_code == "ar":
            return (
                "كلا، يقتصر دور الذكاء الاصطناعي على التوجيه فقط. تعود قرارات القبول أو "
                "الرفض أو قائمة الانتظار حصراً للجان الأكاديمية والمسؤولين البشريين (HITL)."
            )
        if lang_code == "ar-TN":
            return (
                "لا، الروبو ما يقررش القبول ولا الرفض. دوري كان التوجيه، والقرار النهائي "
                "يقعد ديما عند مسؤولي الجامعة واللجنة البشرية."
            )
        if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
            return (
                "La, l'IA juste t3awnek fel orientation. El décision finale (acceptation, "
                "liste d'attente walla refus) ta3melha ken el commission humaine (IIT/ISB/ISA)."
            )
        return (
            "Non : conformément à notre gouvernance Human-in-the-Loop (HITL), aucune IA ne "
            "peut admettre, mettre en liste d'attente ni refuser un candidat. Seul le "
            "Responsable NAU humain décide après étude du dossier et entretien."
        )

    auth_required_intents = {
        "PREINSCRIPTION_AND_RELEVE",
        "TRANSCRIPT_OCR_EXTRACTION",
        "INTERVIEW_SCHEDULING",
    }

    if lang_code == "ar":
        if intent in auth_required_intents and not is_authenticated:
            return (
                "مرحباً بك في SmartCampus AI. لرفع كشف الأعداد أو استكمال ملف التسجيل الأولي، "
                "يرجى تسجيل الدخول عبر رمز OTP أو Google OAuth (وفق قاعدة Give or Keep)."
            )
        return (
            "مرحباً بك في SmartCampus AI. أنا VISION مساعدك الذكي للتوجيه الجامعي في "
            "المعهد الدولي للتكنولوجيا (IIT) ومدرسة الأعمال الدولية (ISB) ومعهد العمارة (ISA) "
            "بصفاقس. يرجى تحديد الشعبة أو التخصص المطلوب للاطلاع على البيانات الرسمية."
        )

    if dialect == "Arabizi":
        if intent in auth_required_intents and not is_authenticated:
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
        if intent in auth_required_intents and not is_authenticated:
            return (
                "مرحبا بيك في SmartCampus AI! باش تنجم ترفع كشف الأعداد (relevé de notes) "
                "أو تبدأ التسجيل الأولي، يلزمك تسجل الدخول برمز OTP أولاً."
            )
        return (
            "مرحبا بيك! أنا VISION مستشارك الذكي للتوجيه الجامعي في IIT و ISB. "
            "أعطيني شعبة البكالوريا أو الاختصاص الي تحب عليه باش نعاونك بالمعلومات الرسمية."
        )

    if intent in auth_required_intents and not is_authenticated:
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


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.IGNORECASE | re.DOTALL)


def _strip_thinking_tags(text: str) -> str:
    """Remove Qwen3 / reasoning <think>...</think> tags so students only see final answers."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    return cleaned or text.strip()


async def _call_openai_compatible_api(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[str, str]:
    """Call an OpenAI-compatible /chat/completions endpoint via httpx (also serves local Ollama)."""
    base_url = settings.LLM_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    outgoing_messages = [dict(m) for m in messages]
    model_lower = settings.LLM_MODEL.lower()
    if (
        not settings.LLM_ENABLE_THINKING
        and settings.LLM_PROVIDER.lower().strip() == "ollama"
        and any(tag in model_lower for tag in ("qwen3", "smartcampus-vision"))
        and outgoing_messages
        and outgoing_messages[0].get("role") == "system"
        and "/no_think" not in outgoing_messages[0].get("content", "")
    ):
        outgoing_messages[0]["content"] = f"{outgoing_messages[0]['content']}\n/no_think"

    request_body = {
        "model": settings.LLM_MODEL,
        "messages": outgoing_messages,
        "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": settings.LLM_MAX_TOKENS if max_tokens is None else max_tokens,
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
        raw_reply = str(data["choices"][0]["message"]["content"]).strip()
        reply_text = raw_reply if settings.LLM_ENABLE_THINKING else _strip_thinking_tags(raw_reply)
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
    candidate_id: str | None = None,
    account_id: str | None = None,
    is_authenticated: bool = False,
    actor_role: str | None = None,
    redis_client: Redis | None = None,
    db: AsyncSession | None = None,
) -> ChatMessageResponse:
    """Orchestrate conversational memory, dialect detection, intent routing, and LLM completion.

    Delegates execution through the compiled LangGraph `VisionSupervisor` (SA-69)
    while preserving 100% backwards compatibility with Sprint 1 schemas and callers.
    """
    from app.schemas.agent import ActorRole, VisionTurnInput
    from app.services.vision_supervisor import get_default_vision_supervisor

    resolved_role: ActorRole = (
        actor_role  # type: ignore[assignment]
        if actor_role
        in {
            "guest",
            "visitor",
            "visiteur",
            "student",
            "candidate",
            "candidat",
            "rh",
            "responsable",
            "direction",
            "admin",
        }
        else ("student" if is_authenticated else "guest")
    )
    turn_input = VisionTurnInput(
        payload=payload,
        session_id=session_id,
        candidate_id=candidate_id,
        account_id=account_id,
        is_authenticated=is_authenticated,
        actor_role=resolved_role,
        candidate_status="AUTHENTICATED" if is_authenticated else "ANONYMOUS",
    )
    supervisor = get_default_vision_supervisor()
    turn_output = await supervisor.run_turn(
        turn_input,
        redis_client=redis_client,
        db=db,
    )
    return turn_output.response


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
