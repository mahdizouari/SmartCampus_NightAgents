"""Async LLM provider connection, VISION prompt engineering, and dialect routing (SA-36 & SA-34)."""

import logging
import re
import time
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.users import CandidateProfile
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
    GuestSessionService,
    HotConversationService,
)

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = (
    "Tu es VISION, le superviseur conversationnel d'orientation et de préinscription "
    "de SmartCampus AI pour les écoles IIT et ISB (ainsi que l'extension ISA) "
    "au sein du groupe NAU à Sfax.\n\n"
    "RÈGLES STRICTES ET NON NÉGOCIABLES (SC V4) :\n"
    "1. Multilinguisme : Réponds toujours dans la langue ou le registre utilisé par "
    "l'étudiant (Français, Arabe standard Fusha, Derja tunisienne en alphabet arabe, "
    "ou Derja tunisienne naturelle pour une requête en Arabizi / code-switching FR-TN). "
    "Ne réponds jamais en Arabe classique (Fusha) à une question en Derja ou Arabizi.\n"
    "2. Zéro hallucination de faits académiques : N'invente JAMAIS de frais de scolarité, "
    "de seuils d'admission, de dates limites, de bourses garanties ni de filières "
    "inexistantes (ex. médecine, pharmacie, droit licence, astrophysique, criminologie, "
    "génie agricole/vétérinaire). Si une donnée officielle n'est pas fournie dans le "
    "contexte, indique honnêtement qu'elle doit être consultée dans le catalogue officiel NAU.\n"
    "3. Gouvernance Human-in-the-Loop (HITL) : Tu ne peux JAMAIS admettre, refuser, "
    "mettre en liste d'attente, promettre une admission ni délivrer une attestation "
    "officielle. Seul le Responsable NAU humain prend et signe la décision finale.\n"
    "4. Règle « Give or Keep » : L'orientation et les questions-réponses sont 100 % "
    "libres sans compte (session anonyme temporaire de 7 jours). L'authentification n'est "
    "requise qu'au moment de téléverser un relevé de notes officiel, sauvegarder une shortlist, "
    "réserver un créneau d'entretien ou soumettre une préinscription.\n"
    "5. Repli en cas d'incompréhension dialectale / Arabizi : Si le message est trop ambigu, "
    "abrégé ou bruité pour identifier l'intention avec certitude, ne devine jamais ; demande "
    "poliment à l'étudiant de reformuler sa question en français simple.\n"
    "6. Style : Sois concis, structuré, bienveillant et cite systématiquement les "
    "documents sources fournis dans le contexte.\n"
    "7. Rigueur administrative et séparation déclaratif / officiel : Ne présente JAMAIS "
    "d'informations administratives non vérifiées, spéculatives ou déclaratives comme des faits "
    "officiels. Toute information sur les frais de scolarité, bourses, seuils ou équivalences "
    "doit provenir exclusivement des extraits de catalogue officiels RAG fournis. "
    "En l'absence de source officielle formelle dans le contexte, rappelle explicitement à "
    "l'étudiant que l'information doit être confirmée par le secrétariat administratif NAU."
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
    r"relevé|releve|رولفي|كشف|dossier|دوسيا|ملف|préinscription|preinscription|ترسيم|تسجيل|"
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
    turns_to_scan = (recent_turns or [])[-6:]
    for t in turns_to_scan:
        content = (
            getattr(t, "content", None)
            or (t.get("content") if isinstance(t, dict) else "")
        ).lower()
        if any(k in content for k in ["passerelle", "licence", "bac+3"]):
            topic_cues.append("passerelle Bac+3")
        if any(k in content for k in ["glid", "logiciel"]):
            topic_cues.append("Génie Logiciel (GLID)")
        if any(k in content for k in ["sdia", "data", "ia"]):
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
) -> list[dict[str, str]]:
    register_instruction = _expected_register_label(lang_code, detected_dialect)
    system_content = VISION_SYSTEM_PROMPT + (
        f"\n\n[MÉTADONNÉES TOUR COURANT]\n"
        f"- Code langue détecté : {lang_code}\n"
        f"- Dialecte détecté : {detected_dialect}\n"
        f"- Registre de réponse obligatoire : {register_instruction}\n"
        f"- Intention détectée : {intent}\n"
    )
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
            "seuil chiffré ou date limite de mémoire ; invite l'étudiant à préciser sa filière "
            "(IIT, ISB ou extension ISA) ou son profil (Bac, Licence, Prépa)."
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

    if lang_code == "ar":
        if intent == "PREINSCRIPTION_AND_RELEVE" and not is_authenticated:
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
    redis_client: Redis | None = None,
    db: AsyncSession | None = None,
) -> ChatMessageResponse:
    """Orchestrate conversational memory, dialect detection, intent routing, and LLM completion."""
    start_ns = time.perf_counter_ns()
    resolved_session_id = session_id or payload.session_id or str(uuid4())

    # --- Conversational Memory Integration (SA-37) ---
    hot_svc = HotConversationService(redis_client=redis_client)
    guest_svc = GuestSessionService(redis_client=redis_client)

    # 1. Fetch recent hot turns from Redis if client payload does not provide explicit history
    history_turns: list[Any] = []
    if payload.history:
        history_turns = list(payload.history)
    else:
        try:
            cached_turns = await hot_svc.get_history(resolved_session_id)
            if cached_turns:
                history_turns = list(cached_turns)
        except Exception as exc:
            logger.debug(
                "Unable to read hot history from Redis for %s: %s",
                resolved_session_id,
                exc,
            )

    # 2. Fetch declared facts & rolling summary from Redis guest session & PostgreSQL (if given)
    declared_facts: dict[str, Any] = {}
    existing_summary: str | None = None

    try:
        guest_data = await guest_svc.get_session(resolved_session_id)
        if guest_data:
            if isinstance(guest_data.get("declared_facts"), dict):
                declared_facts.update(guest_data["declared_facts"])
            for field_name in (
                "school_target",
                "current_specialty",
                "bac_section",
                "declared_overall_avg",
                "preferred_language",
            ):
                if guest_data.get(field_name) is not None:
                    declared_facts[field_name] = guest_data[field_name]
            if guest_data.get("rolling_summary"):
                existing_summary = str(guest_data["rolling_summary"])
    except Exception as exc:
        logger.debug(
            "Unable to read guest session from Redis for %s: %s",
            resolved_session_id,
            exc,
        )

    if is_authenticated and db is not None:
        try:
            cand_uuid = UUID(str(resolved_session_id))
            cand_profile = await db.get(CandidateProfile, cand_uuid)
            if cand_profile:
                if cand_profile.facts and isinstance(cand_profile.facts, dict):
                    declared_facts.update(cand_profile.facts)
                if cand_profile.current_specialty:
                    declared_facts["current_specialty"] = cand_profile.current_specialty
                if cand_profile.preferred_language:
                    declared_facts["preferred_language"] = cand_profile.preferred_language
                if cand_profile.declared_overall_avg is not None:
                    declared_facts["declared_overall_avg"] = float(
                        cand_profile.declared_overall_avg
                    )
                if cand_profile.summary and not existing_summary:
                    existing_summary = cand_profile.summary
        except Exception as exc:
            logger.debug(
                "Unable to read CandidateProfile for %s: %s",
                resolved_session_id,
                exc,
            )

    # 3. Synthesize updated rolling orientation summary (bounded <= 300 tokens)
    rolling_summary = build_rolling_summary(
        declared_facts=declared_facts,
        recent_turns=history_turns,
        existing_summary=existing_summary,
        latest_query=payload.message,
    )

    lang_code, dialect = detect_dialect(
        payload.message,
        language_hint=payload.language_hint,
        explicit_dialect=payload.detectedDialect,
    )
    intent, sub_agent, action_prompt, suggested_actions = classify_intent_and_subagent(
        payload.message,
        is_authenticated=is_authenticated,
        lang_code=lang_code,
    )
    if intent == "CLARIFICATION_REQUIRED":
        confidence, needs_reformulation = 0.25, True
    else:
        confidence, needs_reformulation = assess_comprehension_confidence(
            payload.message,
            lang_code=lang_code,
            intent=intent,
        )
    reformulation_msg = (
        _build_reformulation_fallback_reply(lang_code, dialect)
        if needs_reformulation
        else None
    )
    citations = _build_citations(payload.context_chunks)
    messages = _build_messages(
        payload,
        lang_code,
        dialect,
        intent,
        history_turns=history_turns,
        rolling_summary=rolling_summary,
        declared_facts=declared_facts,
    )

    provider = settings.LLM_PROVIDER.lower().strip()
    can_call_remote = provider == "ollama" or (
        provider == "openai_compatible" and bool(settings.LLM_API_KEY.strip())
    )

    used_fallback = False
    if needs_reformulation:
        # SA-34 safety fallback: never guess on unintelligible/ambiguous dialect inputs;
        # explicitly request a clear French reformulation.
        used_fallback = True
        reply_text = reformulation_msg or FRENCH_REFORMULATION_FALLBACK_FR
        model_used = "smartcampus-clarification-fallback-v1"
    elif provider == "mock" or (not can_call_remote and settings.LLM_FALLBACK_TO_MOCK):
        used_fallback = True
        reply_text = _generate_deterministic_fallback_reply(
            lang_code=lang_code,
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

    # 4. Persist new user turn and assistant reply to Redis hot history, and sync rolling summary
    try:
        await hot_svc.add_turn(resolved_session_id, role="user", content=payload.message)
        await hot_svc.add_turn(resolved_session_id, role="assistant", content=reply_text)
        await guest_svc.update_fields(
            resolved_session_id,
            {"rolling_summary": rolling_summary},
        )
    except Exception as exc:
        logger.debug(
            "Unable to persist turn to Redis for %s: %s",
            resolved_session_id,
            exc,
        )

    if is_authenticated and db is not None:
        try:
            cand_uuid = UUID(str(resolved_session_id))
            cand_profile = await db.get(CandidateProfile, cand_uuid)
            if cand_profile:
                cand_profile.summary = rolling_summary[:1200]
                await db.commit()
        except Exception as exc:
            logger.debug("Unable to update CandidateProfile summary in DB: %s", exc)

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
        comprehension_confidence=confidence,
        needs_reformulation=needs_reformulation,
        reformulation_prompt=reformulation_msg,
        rolling_summary=rolling_summary,
        history_turn_count=len(history_turns),
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
