"""Async LLM provider connection, VISION prompt engineering, and dialect routing (SA-36 & SA-34)."""

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
    "documents sources fournis dans le contexte."
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
    r"\b(hedhi|hedha|heki|kfh|n3ml|9dh|3slm|lfac|l3am|jey|x9z2)\b|"
    r"(هكا متاع القراية|شنوة نعمل فيه)",
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
    r"اختصاص|شعبة|فروع|توجيه|orient|recommand|compar|الفرق|حاير|n5ayar)",
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
    if explicit_dialect == "Arabizi" or language_hint == "arabizi":
        return "ar-TN-arabizi", "Arabizi"
    if explicit_dialect == "Tunisian_Derja" or language_hint == "derja":
        return "ar-TN", "Tunisian_Derja"
    if explicit_dialect == "French" or language_hint == "fr":
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

        # Very short non-greeting dialect/Arabizi input with no recognizable academic entity
        word_count = len(re.findall(r"[\w\u0600-\u06FF]+", stripped))
        if lang_code in {"ar-TN", "ar-TN-arabizi"} and word_count <= 4:
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
) -> tuple[str, VisionSubAgent, ActionPrompt | None, list[str]]:
    """Classify student intent and target VISION sub-agent across FR, Fusha, Derja & Arabizi."""
    lower = text.lower()

    # 1. Out-of-corpus traps & prohibited official attestations (zero-hallucination guardrail)
    if any(
        k in lower
        for k in (
            "médecine",
            "medecine",
            "طب الأسنان",
            "صيدلة",
            "pharmacie",
            "ليسونس الدرو",
            "(droit)",
            "faculté de droit",
            "permis de conduire",
            "astrophysique",
            "cosmologie",
            "الشريعة",
            "الفقه الإسلامي",
            "إيراسموس",
            "1500 أورو",
            "criminologie",
            "attestation officielle d'admission",
            "génie agricole",
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

    # 2. Human-in-the-Loop (HITL) admission governance questions
    if any(
        k in lower
        for k in (
            "intelligence artificielle peut accepter",
            "القرار النهائي في قبول",
            "الروبو هذا ينجم يرفضلي",
            "trfadhnii fel inscription",
            "acceptation finale",
            "jury humain",
        )
    ):
        return (
            "ADMISSION_GOVERNANCE",
            "1.1_Listener",
            None,
            ["Comprendre le processus d'admission humaine", "Consulter le catalogue IIT / ISB"],
        )

    # 3. Anonymous guest session policy ("Give or Keep")
    if any(
        k in lower
        for k in (
            "en tant qu'invité",
            "sans créer de compte",
            "من غير ما نعمل كونت",
            "en mode guest",
            "men ghir ma na3mel compte",
        )
    ):
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
            "حماية بياناتي الشخصية",
            "inpdp",
            "données personnelles",
            "confidentialité",
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
            "طلب موعد",
            "حجز موعد",
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
            "documents obligatoires",
            "les papiers lezmin",
            "الوثائق الرسمية",
            "ملف التسجيل",
            "الأوراق اللازمة",
            "دوسيا",
            "كشف علامات",
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
    if any(
        k in lower
        for k in (
            "recommand",
            "conseil",
            "orient",
            "hésite",
            "hesite",
            "choisir",
            "comparer",
            "ywalmni",
            "ywalmouni",
            "n5ayar",
            "حاير",
            "الفرق الأكاديمي",
            "الفرق بيناتهم",
        )
    ):
        return (
            "TRACK_RECOMMENDATION",
            "1.5_Advisor",
            "CONFIRM_SHORTLIST",
            ["Comparer les filières IIT / ISB", "Sauvegarder ma shortlist"],
        )

    # 8. Eligibility, prerequisites & gateways (FR, Fusha, Derja, Arabizi)
    if any(
        k in lower
        for k in (
            "éligib",
            "eligib",
            "moyenne",
            "seuil",
            "admissible",
            "score",
            "équivalence",
            "passerelle",
            "baccalauréat est requis",
            "diplôme bac+3",
            "شروط القبول",
            "اختبار قبول",
            "الترشح مباشرة",
            "باك تكنيك بمعدل",
            "باك+3",
            "bac math",
            "bac sciences",
            "licence informatique",
            "أحمل إجازة",
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
            "sfax",
            "صفاقس",
            "مقرات",
            "الفاك وين",
            "الأرشيتكتور",
            "الأركيتكتور",
            "الآفاق المهنية",
            "مرحلة تحضيرية",
            "إدارة الشبكات",
            "جيني ميكانيك",
            "ليسونس ديزاين",
        )
    ):
        return (
            "INQUIRE_PROGRAMS",
            "1.2_Librarian",
            None,
            ["Consulter le catalogue IIT / ISB", "Vérifier les conditions d'accès"],
        )

    # 10. Low-comprehension / ambiguous dialect check -> French reformulation fallback
    lang_code, _ = detect_dialect(text)
    _, needs_reformulation = assess_comprehension_confidence(
        text, lang_code=lang_code, intent="GENERAL_ORIENTATION"
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


def _build_messages(
    payload: ChatMessageRequest,
    lang_code: str,
    detected_dialect: DetectedDialect,
    intent: str,
) -> list[dict[str, str]]:
    register_instruction = _expected_register_label(lang_code, detected_dialect)
    system_content = VISION_SYSTEM_PROMPT + (
        f"\n\n[MÉTADONNÉES TOUR COURANT]\n"
        f"- Code langue détecté : {lang_code}\n"
        f"- Dialecte détecté : {detected_dialect}\n"
        f"- Registre de réponse obligatoire : {register_instruction}\n"
        f"- Intention détectée : {intent}\n"
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

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    if payload.history:
        for turn in payload.history[-16:]:
            messages.append({"role": turn.role, "content": turn.content})
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
) -> ChatMessageResponse:
    """Orchestrate dialect detection, intent routing, confidence fallback, and LLM completion."""
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
    messages = _build_messages(payload, lang_code, dialect, intent)

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
