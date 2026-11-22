"""
Guardrail Node Service (SA-46 / Bonus-5).

Implements input and output guardrails for SmartCampus AI (VISION supervisor):
1. Input Guardrail:
   - Detects and blocks prompt injection & jailbreak attempts.
   - Detects and blocks out-of-corpus queries (medicine, law, theology, criminology, etc.).
   - Detects and blocks general off-topic queries (cooking, weather, sports, politics, etc.).
   - Returns polite, respectful safe refusal responses tailored to the user's language/dialect
     (French, Tunisian Derja, Arabizi, Modern Standard Arabic).

2. Output Guardrail:
   - Enforces Human-in-the-Loop (HITL) admission governance: intercepts and neutralizes any
     hallucinated admission promises, official acceptance claims, or guaranteed outcomes.
   - Shields against unverified tuition fee claims or deadlines when ungrounded in RAG context.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.schemas.chat import DetectedDialect

logger = logging.getLogger(__name__)


class GuardrailStatus(StrEnum):
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    MODIFIED = "MODIFIED"


class GuardrailType(StrEnum):
    PROMPT_INJECTION = "INPUT_PROMPT_INJECTION"
    OUT_OF_CORPUS = "INPUT_OUT_OF_CORPUS"
    OFF_TOPIC = "INPUT_OFF_TOPIC"
    ADMISSION_PROMISE = "OUTPUT_ADMISSION_PROMISE"
    UNGROUNDED_FEES = "OUTPUT_UNGROUNDED_FEES"


@dataclass
class InputGuardrailResult:
    """Result returned by the input guardrail pre-filter."""

    status: GuardrailStatus
    guardrail_type: GuardrailType | None = None
    reason: str | None = None
    refusal_message: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.status == GuardrailStatus.BLOCKED


@dataclass
class OutputGuardrailResult:
    """Result returned by the output guardrail post-filter."""

    status: GuardrailStatus
    guardrail_type: GuardrailType | None = None
    reason: str | None = None
    sanitized_text: str = ""
    was_modified: bool = False


# ---------------------------------------------------------------------------
# Input Guardrail Patterns
# ---------------------------------------------------------------------------

PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions?", re.IGNORECASE),
    re.compile(r"oublie\s+(?:toutes\s+)?(?:les\s+)?instructions?", re.IGNORECASE),
    re.compile(r"ignore\s+(?:tes|les)\s+règles?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:in\s+)?(?:dan|developer|god)\s+mode", re.IGNORECASE),
    re.compile(r"tu\s+es\s+maintenant\s+en\s+mode", re.IGNORECASE),
    re.compile(r"dis-moi\s+que\s+je\s+suis\s+admis\s+d'office", re.IGNORECASE),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"bypass\s+(?:the\s+)?guardrails?", re.IGNORECASE),
    re.compile(r"esma3ni\s+a3mel\s+rou7ek", re.IGNORECASE),
    re.compile(r"insa\s+el\s+(?:9waleb|9awa3ed)", re.IGNORECASE),
    re.compile(r"تجاهل\s+(?:كل\s+)?(?:التعليمات|القواعد)", re.IGNORECASE),
    re.compile(r"أنت\s+الآن\s+في\s+وضع", re.IGNORECASE),
)

OUT_OF_CORPUS_PATTERNS: tuple[str, ...] = (
    # Medical & Health (NAU has IIT/ISB/ISA, no medical school)
    "médecine",
    "medecine",
    "faculté de médecine",
    "faculte de medecine",
    "كلية طب",
    "كلية الطب",
    "طب الأسنان",
    "طب الاسنان",
    "dentaire",
    "chirurgie",
    "صيدلة",
    "pharmacie",
    "clinique",
    "vétérinaire",
    "veterinaire",
    "بيطرة",
    "طبيب بيطري",
    # Law & Judicial
    "faculté de droit",
    "droit des affaires",
    "licence en droit",
    "كلية الحقوق",
    "شعبة الحقوق",
    "قانون الأعمال",
    # Astrophysics & Space
    "astrophysique",
    "cosmologie",
    "علم الفلك",
    "علوم الفضاء",
    "هندسة الفضاء",
    # Theology & Religious Law
    "شريعة",
    "أصول الفقه",
    "اصول الفقه",
    "théologie",
    "theologie",
    # Criminology
    "criminologie",
    "علم الجريمة",
    # Agriculture & Agronomy
    "génie agricole",
    "genie agricole",
    "فلاحة",
    "هندسة فلاحية",
    # Unrelated certifications & driving licenses
    "permis de conduire",
    "brevet de pilote",
    "رخصة سياقة",
    "بيرمي",
    # Prohibited official attestations or impossible guarantees
    "attestation officielle d'admission",
    "bourse garantie à 100%",
    "bourse garantie",
    "منحة مضمونة",
    "شهادة قبول رسمية",
)

# Word-boundary Arabic/Derja traps to avoid false positives on "دروس", "الطبيعية",
# "فضاء الطالب", or "حقوق التسجيل".
OUT_OF_CORPUS_REGEX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("الطب", re.compile(r"(?<![\u0600-\u06FF])الطب(?![\u0600-\u06FF])")),
    ("درو", re.compile(r"(?<![\u0600-\u06FF])درو(?![\u0600-\u06FF])")),
    (
        "حقوق_دراسة",
        re.compile(
            r"(?<![\u0600-\u06FF])(?:شعبة|إجازة|اجازة|ماستر|دراسة|قراية|نقرا)\s+"
            r"(?:ال)?حقوق(?![\u0600-\u06FF])"
        ),
    ),
)

OFF_TOPIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Cooking & Food recipes
    re.compile(
        r"\b(?:recette|couscous|pizza|tajine|cuisiner|gâteau|gateau|pâtisserie|"
        r"كسكسي|بيتزا|طاجين|وصفة\s+طبخ|كيفية\s+تحضير|طيبلي)\b",
        re.IGNORECASE,
    ),
    # Weather & Forecast
    re.compile(
        r"\b(?:météo|prevision\s+meteo|prévisions\s+météo|pleuvoir|température\s+demain|"
        r"الطقس|درجة\s+الحرارة|طقس\s+غدوة)\b",
        re.IGNORECASE,
    ),
    # Sports & Pop Culture
    re.compile(
        r"(?:coupe\s+du\s+monde|ballon\s+d'or|champions\s+league|classement\s+ligue\s+1|"
        r"كأس\s+العالم|كاس\s+العالم|دوري\s+أبطال|مباراة|الماتش|ماتش|chkoune\s+rba7|chkoun\s+rba7)",
        re.IGNORECASE,
    ),
    # General Hacking / Malware / Generic OS scripting unrelated to university
    re.compile(
        r"\b(?:hacker\s+un\s+compte|pirater|script\s+ddos|crack\s+wifi|"
        r"اختراق\s+حساب|تهكير|سرقة\s+كلمة\s+سر)\b",
        re.IGNORECASE,
    ),
    # Medical Diagnosis
    re.compile(
        r"\b(?:mal\s+à\s+la\s+tête|fièvre\s+depuis|ordonnance\s+médicale|traitement\s+antibiotique|"
        r"وجيعة\s+رأس|سخانة\s+طالعة|عندي\s+مرض|دواء\s+للوجع)\b",
        re.IGNORECASE,
    ),
    # Politics & Elections
    re.compile(
        r"\b(?:pour\s+qui\s+voter|élections\s+présidentielles|parti\s+politique|"
        r"انتخابات\s+رئاسية|حزب\s+سياسي|رأي\s+سياسي)\b",
        re.IGNORECASE,
    ),
    # Horoscope & Astrology
    re.compile(
        r"\b(?:horoscope|astrologie|signe\s+astrologique|برج\s+الحوت|ابراج\s+اليوم)\b",
        re.IGNORECASE,
    ),
)


# ---------------------------------------------------------------------------
# Output Guardrail Patterns (HITL Admission Guarantees & Fake Claims)
# ---------------------------------------------------------------------------

FORBIDDEN_ADMISSION_PROMISES: tuple[re.Pattern[str], ...] = (
    # French
    re.compile(
        r"(?:vous\s+êtes\s+admis|tu\s+es\s+admis|candidature\s+acceptée\s+d'office|"
        r"admission\s+garantie|nous\s+vous\s+acceptons|votre\s+admission\s+est\s+confirmée|"
        r"je\s+vous\s+déclare\s+admis|félicitations[,\s]+vous\s+êtes\s+admis|"
        r"dossier\s+accepté\s+d'office|acceptation\s+garantie|accepté\s+définitif)",
        re.IGNORECASE,
    ),
    # Arabic (Fusha & Derja in Arabic script)
    re.compile(
        r"(?:تم\s+قبولك|أنت\s+مقبول\s+رسمياً|نضمن\s+لك\s+القبول|مبروك\s+تم\s+قبولك|"
        r"قبولك\s+مضمون|راك\s+مقبول|مقبول\s+رسمي|قبلناك\s+رسميا|قبولك\s+نهائي)",
        re.IGNORECASE,
    ),
    # Arabizi
    re.compile(
        r"(?:rak\s+ma9boul|mabrouk\s+rak\s+ma9boul|ma9boul\s+d'office|dossier\s+mte3ek\s+ma9boul|"
        r"admission\s+garantie|accepté\s+d'office)",
        re.IGNORECASE,
    ),
)

UNGROUNDED_FEES_PATTERN = re.compile(
    r"\b(\d{3,6})\s*(?:dt|dinars?|dinars?\s+tunisiens?|euros?|€|\$|usd|دينار)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Refusal Message Builders
# ---------------------------------------------------------------------------

def build_off_topic_refusal(lang_code: str, dialect: DetectedDialect | str) -> str:
    """Generate a polite, welcoming safe refusal in the student's exact language/dialect."""
    if lang_code == "ar" or dialect == "Modern_Standard_Arabic":
        return (
            "عذراً، هذا التخصص أو الموضوع لا يتوفر ضمن برامج مجمع NAU الأكاديمي. "
            "يقتصر دور مساعد SmartCampus AI على التوجيه الأكاديمي وإجراءات القبول "
            "لمؤسسات المجمع (IIT للهندسة والتكنولوجيا، ISB لإدارة الأعمال، و ISA للعمارة)، "
            "ولا يمكن للذكاء الاصطناعي إصدار شهادات قبول رسمية. "
            "يسعدنا الإجابة عن أي استفسار يخص التخصصات المتاحة أو شروط التسجيل لدينا."
        )

    if lang_code == "ar-TN" or dialect == "Tunisian_Derja":
        return (
            "سامحني، الاختصاص ولا الموضوع هذا موش موجود في مؤسسات الـ NAU وما فماش في برامجنا. "
            "أنا مخصص كان للتوجيه والتسجيل في مؤسسات الـ NAU (الـ IIT للهندسة والتكنولوجيا، "
            "الـ ISB للتصرف وإدارة الأعمال، والـ ISA للهندسة المعمارية). "
            "إذا عندك أي سؤال على شعبنا، شروط القبول أو إجراءات التسجيل، تفضل اسألني!"
        )

    if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
        return (
            "Samahni, el filière walla el sujet hedha mahouch mawjoud fel NAU w ma famach "
            "fi programmes mte3na. Ena spécialisé ken fel orientation wel inscription "
            "mte3 les écoles NAU (l'IIT pour l'ingénierie, l'ISB pour business & finance, "
            "w l'ISA pour l'architecture). Tnejjem tes2elni 3al filières walla conditions "
            "d'accès mar7ba bik!"
        )

    # Default: French
    return (
        "Ce programme ou ce sujet ne relève pas de l'offre officielle du groupe NAU. "
        "SmartCampus AI est exclusivement dédié à l'orientation et à l'admission pour les "
        "établissements de l'université NAU (IIT pour l'ingénierie et technologie, ISB pour "
        "le management et la finance, et ISA pour l'architecture). En tant qu'assistant IA, "
        "je ne peux ni inventer de cursus ni délivrer d'attestation officielle d'admission. "
        "N'hésitez pas à me poser vos questions sur nos filières ou démarches d'inscription !"
    )


def build_prompt_injection_refusal(lang_code: str, dialect: DetectedDialect | str) -> str:
    """Refuse prompt-injection or jailbreak attempts firmly and politely."""
    if lang_code == "ar" or dialect == "Modern_Standard_Arabic":
        return (
            "طلب غير مصرح به: لا يمكن تعديل أو تجاوز بروتوكولات الأمان والدور الأكاديمي "
            "لمساعد VISION التابع لجامعة NAU. كيف يمكنني مساعدتك فيما يخص التوجيه "
            "الأكاديمي لمؤسسات IIT أو ISB أو ISA؟"
        )

    if lang_code == "ar-TN" or dialect == "Tunisian_Derja":
        return (
            "طلب غير مسموح به: ما نجمش نبدل دوري كمستشار توجيه لمؤسسات الـ NAU. "
            "كيفاش نجم نعاونك بخصوص دراستك وتوجيهك في الـ IIT أو الـ ISB أو الـ ISA ؟"
        )

    if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
        return (
            "Demande non autorisée : Ma najjamch nbadal el rôle mte3i kima conseiller orientation "
            "fel NAU. Kifech nnejjem n3awnek fel orientation mte3ek fel IIT, l'ISB walla l'ISA ?"
        )

    return (
        "Requête non autorisée : Les consignes de sécurité et le rôle de VISION en tant que "
        "conseiller d'orientation NAU ne peuvent pas être contournés ou modifiés. "
        "Comment puis-je vous aider concernant votre orientation à l'IIT, l'ISB ou l'ISA ?"
    )


def build_hitl_disclaimer(lang_code: str, dialect: DetectedDialect | str) -> str:
    """Human-in-the-Loop governance reminder neutralizing admission claims."""
    if lang_code == "ar" or dialect == "Modern_Standard_Arabic":
        return (
            "\n\n[تنبيه هام في الحوكمة الأكاديمية (HITL)]: "
            "وفقاً للوائح الرسمية لمجمع NAU، لا يمكن لأي ذكاء اصطناعي اتخاذ قرار قبول أو رفض "
            "أو إصدار ضمانات قبول نهائية. تُتخذ كافة قرارات القبول حصراً من قِبل لجان القبول "
            "البشرية بعد مراجعة الملف والمقابلة الشفهية."
        )

    if lang_code == "ar-TN" or dialect == "Tunisian_Derja":
        return (
            "\n\n[تذكير مهم بقوانين القبول (HITL)]: "
            "حسب قوانين مجمع الـ NAU، الذكاء الاصطناعي ما ينجمش يعطي قرار قبول رسمي ولا يضمن "
            "قبول أي طالب. لجنة الأساتذة والمسؤولين البشريين هوما الوحيدين المخولين باش يقررو "
            "قبول ملفك بعد المقابلة."
        )

    if lang_code == "ar-TN-arabizi" or dialect == "Arabizi":
        return (
            "\n\n[Rappel important HITL]: "
            "7asb 9awanin el jami3a (HITL), l'IA ma tnejjemch ta3ti décision d'admission "
            "officielle walla todhmen l'acceptation. Ken el commission académique humaine "
            "hia li t9arrer ba3d dirasset el dossier."
        )

    return (
        "\n\n[Rappel officiel de gouvernance HITL] : "
        "Conformément aux règles de l'université NAU (Human-in-the-Loop), aucune intelligence "
        "artificielle ne peut valider, garantir ni prononcer une décision d'admission. "
        "Seul le jury académique humain et la commission d'admission statuent officiellement "
        "sur votre dossier après examen et entretien."
    )


def build_ungrounded_fee_disclaimer(lang_code: str, dialect: DetectedDialect | str) -> str:
    """Caveat appended when tuition fees or deadlines appear without official RAG citations."""
    if lang_code == "ar" or dialect == "Modern_Standard_Arabic":
        return (
            "\n(ملاحظة هامة: يجب تأكيد كافة الرسوم الدراسية والمواعيد النهائية حصراً "
            "من قِبل مصلحة التسجيل التابعة لمجمع NAU)."
        )

    if lang_code in {"ar-TN", "ar-TN-arabizi"} or dialect in {"Tunisian_Derja", "Arabizi"}:
        return (
            "\n(تذكير: معاليم التسجيل والآجال الرسمية يلزم ديما تأكيدها من إدارة الـ NAU مباشرة)."
        )

    return (
        "\n(Important : Tout montant de frais ou calendrier doit impérativement être validé "
        "auprès du secrétariat NAU ou sur la brochure officielle en vigueur)."
    )


# ---------------------------------------------------------------------------
# Core Guardrail Node Functions
# ---------------------------------------------------------------------------

def check_input_guardrail(
    query: str,
    *,
    lang_code: str,
    dialect: DetectedDialect | str,
) -> InputGuardrailResult:
    """
    Inspect incoming student query for prompt injection, out-of-corpus traps, and off-topic topics.

    Returns:
        InputGuardrailResult with status=BLOCKED and polite refusal message if triggered,
        or status=PASSED if the query is safe for orientation processing.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return InputGuardrailResult(status=GuardrailStatus.PASSED)

    lower_query = cleaned_query.lower()

    # 1. Prompt Injection & Jailbreak Pre-Filter
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(lower_query):
            logger.info(
                "Guardrail triggered: PROMPT_INJECTION (query_len=%d)",
                len(cleaned_query),
            )
            refusal = build_prompt_injection_refusal(lang_code, dialect)
            return InputGuardrailResult(
                status=GuardrailStatus.BLOCKED,
                guardrail_type=GuardrailType.PROMPT_INJECTION,
                reason="Attempted prompt injection or system override detected.",
                refusal_message=refusal,
            )

    # 2. Out-of-Corpus Academic Traps (Medicine, Law, Criminology, etc.)
    for trap in OUT_OF_CORPUS_PATTERNS:
        if trap in lower_query:
            logger.info("Guardrail triggered: OUT_OF_CORPUS (match='%s')", trap)
            refusal = build_off_topic_refusal(lang_code, dialect)
            return InputGuardrailResult(
                status=GuardrailStatus.BLOCKED,
                guardrail_type=GuardrailType.OUT_OF_CORPUS,
                reason=f"Query references out-of-corpus domain or prohibited claim: {trap}",
                refusal_message=refusal,
            )

    for label, pattern in OUT_OF_CORPUS_REGEX_PATTERNS:
        if pattern.search(lower_query):
            logger.info("Guardrail triggered: OUT_OF_CORPUS (regex='%s')", label)
            refusal = build_off_topic_refusal(lang_code, dialect)
            return InputGuardrailResult(
                status=GuardrailStatus.BLOCKED,
                guardrail_type=GuardrailType.OUT_OF_CORPUS,
                reason=f"Query references out-of-corpus domain or prohibited claim: {label}",
                refusal_message=refusal,
            )

    # 3. Non-Academic Off-Topic Topics (Cooking, Weather, Sports, Politics, etc.)
    for pattern in OFF_TOPIC_PATTERNS:
        if pattern.search(lower_query):
            logger.info(
                "Guardrail triggered: OFF_TOPIC (query_len=%d)",
                len(cleaned_query),
            )
            refusal = build_off_topic_refusal(lang_code, dialect)
            return InputGuardrailResult(
                status=GuardrailStatus.BLOCKED,
                guardrail_type=GuardrailType.OFF_TOPIC,
                reason="Query is outside the academic orientation and admissions domain.",
                refusal_message=refusal,
            )

    return InputGuardrailResult(status=GuardrailStatus.PASSED)


def check_output_guardrail(
    reply_text: str,
    *,
    context_chunks: list[dict[str, Any]] | None = None,
    lang_code: str,
    dialect: DetectedDialect | str,
) -> OutputGuardrailResult:
    """
    Inspect LLM or fallback output before delivery to the user:
    1. Intercepts and neutralizes any unauthorized admission promises (HITL rule).
    2. Protects against unverified tuition fee assertions when context chunks are empty.

    Returns:
        OutputGuardrailResult with sanitized text and modification details.
    """
    if not reply_text:
        return OutputGuardrailResult(
            status=GuardrailStatus.PASSED,
            sanitized_text=reply_text,
            was_modified=False,
        )

    current_text = reply_text
    was_modified = False
    triggered_type: GuardrailType | None = None
    reason: str | None = None

    # 1. Admission Promise Check (HITL Compliance)
    has_admission_promise = any(
        pattern.search(current_text) for pattern in FORBIDDEN_ADMISSION_PROMISES
    )
    if has_admission_promise:
        logger.warning("Output Guardrail: Admission promise detected in reply; neutralizing.")
        # Neutralize each forbidden pattern
        for pattern in FORBIDDEN_ADMISSION_PROMISES:
            current_text = pattern.sub(
                "[Information soumise à validation du jury humain]", current_text
            )
        # Append strict HITL disclaimer
        disclaimer = build_hitl_disclaimer(lang_code, dialect)
        current_text = current_text.strip() + disclaimer
        was_modified = True
        triggered_type = GuardrailType.ADMISSION_PROMISE
        reason = "Hallucinated or unauthorized admission promise neutralized via HITL governance."

    # 2. Ungrounded Tuition Fees / Deadlines Check
    has_fee_mention = bool(UNGROUNDED_FEES_PATTERN.search(current_text))
    has_rag_grounding = bool(context_chunks and len(context_chunks) > 0)

    if has_fee_mention and not has_rag_grounding:
        logger.info(
            "Output Guardrail: Ungrounded fee amount found without RAG chunks; adding caveat."
        )
        caveat = build_ungrounded_fee_disclaimer(lang_code, dialect)
        current_text = current_text.strip() + caveat
        was_modified = True
        if triggered_type is None:
            triggered_type = GuardrailType.UNGROUNDED_FEES
            reason = "Financial assertion without verified RAG citation flagged with caveat."

    if was_modified:
        return OutputGuardrailResult(
            status=GuardrailStatus.MODIFIED,
            guardrail_type=triggered_type,
            reason=reason,
            sanitized_text=current_text,
            was_modified=True,
        )

    return OutputGuardrailResult(
        status=GuardrailStatus.PASSED,
        sanitized_text=reply_text,
        was_modified=False,
    )
