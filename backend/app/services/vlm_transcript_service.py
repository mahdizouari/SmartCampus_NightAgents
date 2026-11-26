"""VISION 1.4_Reader — VLM Tunisian Baccalaureate Transcript Extractor (Bonus-1 / SA-42).

Parses uploaded Tunisian Baccalaureate transcript images (PNG/JPEG/WEBP) and PDFs into
strictly validated Pydantic JSON with subject grades, official coefficients, recomputed
weighted average cross-check, per-field confidence scores, and HITL review flags.

Compatible with Groq (qwen/qwen3.8-27b), OpenAI (gpt-4o-mini), and OpenRouter Vision APIs,
with a genuine pypdf text-layer parser fallback when running offline without API keys.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
import unicodedata
from typing import Any

import httpx
from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.core.config import settings
from app.schemas.transcript import (
    BacSection,
    BacTranscriptExtractionResult,
    DiplomaStage,
    DocumentType,
    ExtractedSubjectGrade,
    ExtractionSource,
    TranscriptFieldConfidence,
    TranscriptSessionType,
    VlmHealthResponse,
)

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_MIME_TYPES: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")
SUPPORTED_MIME_TYPES: tuple[str, ...] = (*SUPPORTED_IMAGE_MIME_TYPES, "application/pdf")

DISCREPANCY_TOLERANCE = 0.25
MAX_VLM_IMAGE_DIMENSION = 1280

# Official Tunisian Ministère de l'Éducation Baccalaureate section coefficients
TUNISIAN_BAC_COEFFICIENTS: dict[BacSection, dict[str, float]] = {
    "Mathématiques": {
        "mathématiques": 4.0,
        "sciences physiques": 4.0,
        "physique": 4.0,
        "physique-chimie": 4.0,
        "sciences de la vie et de la terre": 1.0,
        "svt": 1.0,
        "français": 1.0,
        "anglais": 1.0,
        "philosophie": 1.0,
        "arabe": 1.0,
        "informatique": 1.0,
        "éducation physique": 1.0,
    },
    "Sciences Expérimentales": {
        "sciences de la vie et de la terre": 4.0,
        "svt": 4.0,
        "sciences physiques": 4.0,
        "physique": 4.0,
        "physique-chimie": 4.0,
        "mathématiques": 3.0,
        "français": 1.0,
        "anglais": 1.0,
        "philosophie": 1.0,
        "arabe": 1.0,
        "informatique": 1.0,
        "éducation physique": 1.0,
    },
    "Sciences de l'Informatique": {
        "algorithmique et programmation": 3.0,
        "mathématiques": 3.0,
        "sciences de la technologie": 3.0,
        "sti": 3.0,
        "sciences physiques": 2.0,
        "physique": 2.0,
        "physique-chimie": 2.0,
        "français": 1.0,
        "anglais": 1.0,
        "philosophie": 1.0,
        "arabe": 1.0,
        "éducation physique": 1.0,
    },
    "Sciences Techniques": {
        "technologie": 3.0,
        "mathématiques": 3.0,
        "sciences physiques": 3.0,
        "physique": 3.0,
        "physique-chimie": 3.0,
        "tp technologie": 1.0,
        "français": 1.0,
        "anglais": 1.0,
        "philosophie": 1.0,
        "arabe": 1.0,
        "informatique": 1.0,
        "éducation physique": 1.0,
    },
    "Économie et Gestion": {
        "économie": 3.0,
        "gestion": 3.0,
        "mathématiques": 2.0,
        "histoire et géographie": 2.0,
        "français": 1.0,
        "anglais": 1.0,
        "philosophie": 1.0,
        "arabe": 1.0,
        "informatique": 1.0,
        "éducation physique": 1.0,
    },
}

# Official Arabic subject names printed on Tunisian Bac transcripts (بيان أعداد البكالوريا)
ARABIC_TO_CANONICAL_SUBJECT: dict[str, str] = {
    "رياضيات": "Mathématiques",
    "الرياضيات": "Mathématiques",
    "علوم فيزيائية": "Sciences Physiques",
    "العلوم الفيزيائية": "Sciences Physiques",
    "علوم الحياة والأرض": "Sciences de la Vie et de la Terre",
    "علوم الحياة والارض": "Sciences de la Vie et de la Terre",
    "انقليزية": "Anglais",
    "إنقليزية": "Anglais",
    "إنجليزية": "Anglais",
    "انجليزية": "Anglais",
    "فرنسية": "Français",
    "عربية": "Arabe",
    "فلسفة": "Philosophie",
    "إعلامية": "Informatique",
    "اعلامية": "Informatique",
    "تربية بدنية": "Éducation Physique",
    "تكنولوجيا": "Technologie",
    "خوارزميات وبرمجة": "Algorithmique et Programmation",
    "اقتصاد": "Économie",
    "تصرف": "Gestion",
    "تاريخ وجغرافيا": "Histoire et Géographie",
    "إسبانية": "Espagnol (Option)",
    "اسبانية": "Espagnol (Option)",
    "ألمانية": "Allemand (Option)",
    "المانية": "Allemand (Option)",
    "إيطالية": "Italien (Option)",
    "ايطالية": "Italien (Option)",
    "صينية": "Chinois (Option)",
    "روسية": "Russe (Option)",
    "موسيقى": "Musique (Option)",
    "تربية تشكيلية": "Arts Plastiques (Option)",
}

OPTIONAL_BAC_SUBJECT_KEYS: frozenset[str] = frozenset(
    {
        "espagnol",
        "espagnol (option)",
        "allemand",
        "allemand (option)",
        "italien",
        "italien (option)",
        "chinois",
        "chinois (option)",
        "russe",
        "russe (option)",
        "musique",
        "musique (option)",
        "arts plastiques",
        "arts plastiques (option)",
        "إسبانية",
        "اسبانية",
        "ألمانية",
        "المانية",
        "إيطالية",
        "ايطالية",
        "صينية",
        "روسية",
        "موسيقى",
        "تربية تشكيلية",
    }
)

ARABIC_MENTION_MAP: dict[str, str] = {
    "متوسط": "Passable",
    "قريب من الحسن": "Assez Bien",
    "حسن": "Bien",
    "حسن جدا": "Très Bien",
    "حسن جداً": "Très Bien",
}

VLM_TRANSCRIPT_SYSTEM_PROMPT = (
    "Tu es VISION 1.4_Reader, le module d'extraction OCR/VLM de relevés de notes officiels "
    "tunisiens pour SmartCampus AI (IIT / ISB / ISA — groupe NAU Sfax).\n"
    "Tu prends en charge DEUX types de relevés officiels :\n"
    "A. Relevé du Bac Tunisien (`document_type: \"RELEVE_BAC\"`, `diploma_stage: \"BAC\"`, "
    "« بيان أعداد امتحان البكالوريا - الجمهورية التونسية، وزارة التربية ») :\n"
    "   - Document en arabe (droite à gauche) avec 3 colonnes : المواد (Matières), الدورة الرئيسية "
    "(Session Principale) et دورة المراقبة (Session de Contrôle, affichant '*****' si admis).\n"
    "   - Lis impérativement la ligne « الشعبة : ... » (Section) en utilisant la table exacte :\n"
    "     الرياضيات = Mathématiques | العلوم التجريبية = Sciences Expérimentales | "
    "العلوم التقنية = Sciences Techniques | علوم الإعلامية = Sciences de l'Informatique | "
    "الاقتصاد والتصرف = Économie et Gestion | الآداب = Lettres | الرياضة = Sport.\n"
    "   - Traduis fidèlement les matières arabes (ne confonds jamais انقليزية et إعلامية) :\n"
    "     رياضيات = Mathématiques | علوم فيزيائية = Sciences Physiques | "
    "علوم الحياة والأرض = Sciences de la Vie et de la Terre | انقليزية = Anglais | "
    "فرنسية = Français | عربية = Arabe | فلسفة = Philosophie | إعلامية = Informatique | "
    "تربية بدنية = Éducation Physique | إسبانية = Espagnol (Option).\n"
    "   - Gouvernorat : lis « مكانها : ... » (ex. صفاقس = Sfax) ; ne prends pas « تونس في » "
    "(ville d'émission du Ministère).\n"
    "   - La colonne coefficient n'est pas imprimée : omets `coefficient` (ou mets null) pour que "
    "le backend applique les coefficients officiels du Ministère de l'Éducation selon la section.\n"
    "   - Notes à virgule (ex. '09,25' -> 9.25, '11,51' -> 11.51) ; langues facultatives "
    "(إسبانية, ألمانية, إيطالية, صينية, روسية, موسيقى, تربية تشكيلية) -> `is_optional: true`.\n"
    "   - Mentions arabes : متوسط = Passable, قريب من الحسن = Assez Bien, حسن = Bien, "
    "حسن جدا = Très Bien.\n"
    "   - Si une donnée personnelle (ex. الإسم واللقب, CIN, signature) est masquée/noircie "
    "(anonymisée INPDP), retourne strictement null pour `student_name` sans jamais deviner.\n"
    "B. Relevé Universitaire / Licence Passerelle (`document_type: \"RELEVE_UNIVERSITAIRE\"`, "
    "`diploma_stage: \"L1\"|\"L2\"|\"L3\"|\"PREPA_1\"|\"PREPA_2\"|\"M1\"`, ex. Université de Sfax "
    "/ ISIMS pour passerelle Bac+3 vers Cycle Ingénieur IIT) :\n"
    "   - Extrais les Unités d'Enseignement (Unités, Coef, Moyenne finale en privilégiant la "
    "Session de Rattrapage si supérieure/présente, Crédits acquis), la `moyenne générale`, "
    "`credits_acquired`, `program_title`, `mention` et `jury_decision`.\n\n"
    "INSTRUCTIONS STRICTES :\n"
    "1. Extrais UNIQUEMENT les données réellement visibles sur le relevé fourni. "
    "N'invente JAMAIS une note, une matière ou une moyenne illisible.\n"
    "2. Retourne EXCLUSIVEMENT un objet JSON valide respectant exactement ce schéma :\n"
    "{\n"
    '  "document_type": "RELEVE_BAC | RELEVE_UNIVERSITAIRE",\n'
    '  "diploma_stage": "BAC | L1 | L2 | L3 | PREPA_1 | PREPA_2 | M1 | OTHER",\n'
    '  "bac_section": "Mathématiques | Sciences Expérimentales | Sciences Techniques | '
    'Sciences de l\'Informatique | Économie et Gestion | Lettres | Sport | Autre | null",\n'
    '  "program_title": "Intitulé du diplôme universitaire ou null",\n'
    '  "session_year": "2021 ou 2023-2024",\n'
    '  "session_type": "Principale | Contrôle | Rattrapage | Inconnue",\n'
    '  "student_name": "Nom Prénom ou null (si masqué/anonymisé)",\n'
    '  "institution": "Établissement / Lycée / Faculté ou null",\n'
    '  "governorate": "Gouvernorat (ex. Sfax) ou null",\n'
    '  "general_average": 11.51,\n'
    '  "credits_acquired": null,\n'
    '  "mention": "Passable | Assez Bien | Bien | Très Bien | null",\n'
    '  "jury_decision": "Admis | Refusé | Contrôle | null",\n'
    '  "subjects": [\n'
    '    {"name": "Mathématiques", "grade": 9.25, "coefficient": 4.0, '
    '"credits_acquired": null, "is_optional": false, "confidence": 0.96}\n'
    "  ],\n"
    '  "field_confidences": {\n'
    '    "bac_section": 0.98,\n'
    '    "session_year": 0.98,\n'
    '    "general_average": 0.97,\n'
    '    "student_name": null,\n'
    '    "institution": null\n'
    "  }\n"
    "}\n"
    "3. Pour tout champ flou, partiellement caché ou incertain, assigne un score de confiance "
    "inférieur à 0.85 afin de déclencher la vérification humaine (HITL).\n"
    "4. Les notes doivent être sur 20 (nombre décimal entre 0.0 et 20.0)."
)


def _detect_magic_mime(raw_bytes: bytes) -> str | None:
    """Verify file magic bytes rather than trusting client-supplied extensions alone."""
    if raw_bytes.startswith(b"%PDF-"):
        return "application/pdf"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_uploaded_transcript(
    raw_bytes: bytes,
    *,
    file_name: str,
    declared_content_type: str | None = None,
) -> str:
    """Validate upload size and magic bytes; return verified canonical MIME type."""
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded transcript file is empty.",
        )
    if len(raw_bytes) > settings.VLM_MAX_FILE_SIZE_BYTES:
        max_mb = settings.VLM_MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Transcript file exceeds maximum allowed size of {max_mb} MB.",
        )

    detected_mime = _detect_magic_mime(raw_bytes)
    if detected_mime is None or detected_mime not in SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported or invalid transcript file '{file_name}' "
                f"(declared={declared_content_type}). Supported formats: PNG, JPEG, WEBP, PDF."
            ),
        )
    return detected_mime


def normalize_bac_section(raw_section: str | None) -> BacSection:
    """Map raw OCR/PDF section text to canonical Tunisian Baccalaureate section."""
    if not raw_section:
        return "Autre"
    lower = raw_section.lower()
    if "math" in lower or "رياضيات" in lower:
        return "Mathématiques"
    if "exp" in lower or "تجريبية" in lower:
        return "Sciences Expérimentales"
    if "info" in lower or "إعلامية" in lower:
        return "Sciences de l'Informatique"
    if "tech" in lower or "تقنية" in lower:
        return "Sciences Techniques"
    if "éco" in lower or "eco" in lower or "gestion" in lower or "اقتصاد" in lower:
        return "Économie et Gestion"
    if "lettr" in lower or "آداب" in lower:
        return "Lettres"
    if "sport" in lower or "رياضة" in lower:
        return "Sport"
    return "Autre"


def compute_tunisian_bac_average(subjects: list[ExtractedSubjectGrade]) -> float | None:
    """Compute official Tunisian Baccalaureate weighted average from extracted subjects.

    Mandatory subjects contribute (grade * coefficient) to numerator and coefficient to
    denominator. Optional subjects contribute only bonus points above 10/20 (standard
    Tunisian Ministère de l'Éducation formula).
    """
    if not subjects:
        return None
    total_weighted = 0.0
    total_coeff = 0.0
    for subj in subjects:
        if subj.is_optional:
            if subj.grade > 10.0:
                total_weighted += (subj.grade - 10.0) * subj.coefficient
        else:
            total_weighted += subj.grade * subj.coefficient
            total_coeff += subj.coefficient
    if total_coeff <= 0:
        return None
    return round(min(20.0, total_weighted / total_coeff), 2)


def infer_mention(avg: float | None) -> str | None:
    """Infer official Tunisian Baccalaureate mention from general average."""
    if avg is None or avg < 10.0:
        return None
    if avg >= 16.0:
        return "Très Bien"
    if avg >= 14.0:
        return "Bien"
    if avg >= 12.0:
        return "Assez Bien"
    return "Passable"


def normalize_mention(raw_mention: str | None, avg: float | None) -> str | None:
    """Normalize Arabic/French Tunisian Baccalaureate mention labels."""
    if raw_mention:
        cleaned = str(raw_mention).strip()
        if cleaned in ARABIC_MENTION_MAP:
            return ARABIC_MENTION_MAP[cleaned]
        folded = _ascii_fold(cleaned)
        if "tres bien" in folded:
            return "Très Bien"
        if "assez bien" in folded:
            return "Assez Bien"
        if "bien" in folded:
            return "Bien"
        if "passable" in folded:
            return "Passable"
        return cleaned
    return infer_mention(avg)


def _parse_decimal_number(raw: Any) -> float:
    """Parse numeric grades accepting both dot ('11.51') and Tunisian comma ('09,25') formats."""
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = str(raw).strip().replace(",", ".")
    return float(cleaned)


def resolve_tunisian_subject_and_coefficient(
    raw_name: str,
    bac_section: BacSection,
    provided_coeff: Any = None,
    provided_optional: Any = None,
) -> tuple[str, float, bool]:
    """Map Arabic/French subject name to canonical label, official coeff, and optional flag."""
    stripped = raw_name.strip()
    canonical_name = ARABIC_TO_CANONICAL_SUBJECT.get(stripped, stripped)
    if _ascii_fold(canonical_name) in {"physique", "physique-chimie", "physique chimie"}:
        canonical_name = "Sciences Physiques"
    folded = _ascii_fold(canonical_name)
    raw_folded = _ascii_fold(stripped)

    is_optional = (
        bool(provided_optional)
        or "option" in folded
        or stripped in OPTIONAL_BAC_SUBJECT_KEYS
        or raw_folded in OPTIONAL_BAC_SUBJECT_KEYS
        or folded in OPTIONAL_BAC_SUBJECT_KEYS
    )

    if provided_coeff is not None and str(provided_coeff).strip() != "":
        coeff = round(_parse_decimal_number(provided_coeff), 2)
        return canonical_name, coeff, is_optional

    # Real Tunisian Bac transcripts do not print a coefficient column: infer from official table
    section_table = {
        _ascii_fold(k): v for k, v in TUNISIAN_BAC_COEFFICIENTS.get(bac_section, {}).items()
    }
    inferred_coeff = section_table.get(folded, 1.0)
    return canonical_name, inferred_coeff, is_optional


def _prepare_image_data_uri(raw_bytes: bytes, mime_type: str) -> tuple[str, tuple[int, int]]:
    """Validate image with Pillow, safely resize oversized scans, and encode base64 data URI."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            width, height = img.size
            if max(width, height) > MAX_VLM_IMAGE_DIMENSION:
                resized = img.copy()
                resized.thumbnail((MAX_VLM_IMAGE_DIMENSION, MAX_VLM_IMAGE_DIMENSION))
                buf = io.BytesIO()
                out_fmt = "PNG" if mime_type == "image/png" else "JPEG"
                if out_fmt == "JPEG" and resized.mode not in ("RGB", "L"):
                    resized = resized.convert("RGB")
                resized.save(buf, format=out_fmt)
                payload_bytes = buf.getvalue()
                out_mime = "image/png" if out_fmt == "PNG" else "image/jpeg"
            else:
                payload_bytes = raw_bytes
                out_mime = mime_type
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corrupted or unreadable image file.",
        ) from exc

    b64 = base64.b64encode(payload_bytes).decode("ascii")
    return f"data:{out_mime};base64,{b64}", (width, height)


def _extract_pdf_content(raw_bytes: bytes) -> tuple[str, int, bytes | None]:
    """Extract embedded text, page count, and first embedded raster scan image from a PDF."""
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        pages_text = [(page.extract_text() or "").strip() for page in reader.pages]
        joined_text = "\n".join(t for t in pages_text if t)
        first_image_bytes: bytes | None = None
        for page in reader.pages:
            if page.images:
                first_image_bytes = bytes(page.images[0].data)
                break
        return joined_text, len(reader.pages), first_image_bytes
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corrupted or unreadable PDF transcript file.",
        ) from exc


def _ascii_fold(text: str) -> str:
    """Fold French diacritics to ASCII lowercase for resilient subject matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().strip()


def _parse_tunisian_bac_pdf_text(text: str) -> dict[str, Any]:
    """Deterministically parse structured fields from a Tunisian Baccalaureate digital PDF."""
    section_match = re.search(r"Section\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    year_match = re.search(
        r"Session\s*(?:Principale|Contr[oô]le)?\s*[:\-]?\s*(20\d{2})",
        text,
        re.IGNORECASE,
    )
    session_type: TranscriptSessionType = "Principale"
    if re.search(r"Rattrapage", text, re.IGNORECASE):
        session_type = "Rattrapage"
    elif re.search(r"Contr[oô]le", text, re.IGNORECASE):
        session_type = "Contrôle"

    name_match = re.search(r"(?:Nom\s*et\s*Pr[ée]nom|Candidat)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    lycee_match = re.search(r"(?:[ÉE]tablissement|Lyc[ée]e)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    gov_match = re.search(r"(?:Gouvernorat|D[ée]l[ée]gation)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    avg_match = re.search(
        r"Moyenne\s+G[ée]n[ée]rale\s*[:\-]?\s*([0-2]?\d(?:[.,]\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    mention_match = re.search(
        r"Mention\s*[:\-]\s*(Tr[èe]s\s+Bien|Assez\s+Bien|Bien|Passable)",
        text,
        re.IGNORECASE,
    )

    bac_section = normalize_bac_section(section_match.group(1).strip() if section_match else None)
    section_coeffs = {
        _ascii_fold(k): v for k, v in TUNISIAN_BAC_COEFFICIENTS.get(bac_section, {}).items()
    }

    # Match rows formatted as: "Matière | Coeff | Note" or "Matière   4   17.50"
    subjects: list[dict[str, Any]] = []
    row_pattern = re.compile(
        r"^\s*([A-Za-zÀ-ÿ'\-() ]{3,64}?)\s*(?:\||\s{2,})\s*"
        r"(\d+(?:[.,]\d+)?)\s*(?:\||\s{2,})\s*"
        r"([0-2]?\d(?:[.,]\d{1,2})?)\s*(?:/\s*20)?\s*$"
    )
    skip_headers = {"matiere", "total", "moyenne", "somme"}

    for line in text.splitlines():
        m = row_pattern.match(line)
        if not m:
            continue
        subj_name = re.sub(r"\s+", " ", m.group(1)).strip(" -|:")
        folded_name = _ascii_fold(subj_name)
        if folded_name in skip_headers or folded_name.startswith("moyenne"):
            continue
        coeff_val = float(m.group(2).replace(",", "."))
        grade_val = float(m.group(3).replace(",", "."))
        if not (0.0 <= grade_val <= 20.0) or coeff_val <= 0:
            continue

        expected_coeff = section_coeffs.get(folded_name)
        conf = 0.97 if (expected_coeff is None or abs(expected_coeff - coeff_val) < 0.01) else 0.80
        subjects.append(
            {
                "name": subj_name,
                "grade": grade_val,
                "coefficient": coeff_val,
                "is_optional": "option" in subj_name.lower(),
                "confidence": conf,
            }
        )

    general_avg = float(avg_match.group(1).replace(",", ".")) if avg_match else None
    return {
        "bac_section": bac_section,
        "session_year": year_match.group(1) if year_match else None,
        "session_type": session_type,
        "student_name": name_match.group(1).strip() if name_match else None,
        "institution": lycee_match.group(1).strip() if lycee_match else None,
        "governorate": gov_match.group(1).strip() if gov_match else None,
        "general_average": general_avg,
        "mention": mention_match.group(1).strip() if mention_match else infer_mention(general_avg),
        "subjects": subjects,
        "field_confidences": {
            "bac_section": 0.98 if section_match else 0.40,
            "session_year": 0.98 if year_match else 0.50,
            "general_average": 0.98 if avg_match else 0.40,
            "student_name": 0.96 if name_match else None,
            "institution": 0.96 if lycee_match else None,
        },
    }


def _strip_json_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


async def _call_vlm_openai_compatible(
    *,
    image_data_uri: str | None = None,
    pdf_text: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Call OpenAI-compatible Vision API (Ollama qwen2.5vl:7b, Groq, OpenAI, or OpenRouter)."""
    url = f"{settings.effective_vlm_base_url}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.effective_vlm_api_key:
        headers["Authorization"] = f"Bearer {settings.effective_vlm_api_key}"

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Extrais toutes les matières, notes, coefficients, moyenne générale et scores "
                "de confiance de ce relevé de notes du Baccalauréat Tunisien au format JSON."
            ),
        }
    ]
    if image_data_uri:
        user_content.append({"type": "image_url", "image_url": {"url": image_data_uri}})
    if pdf_text:
        user_content.append(
            {"type": "text", "text": f"[TEXTE EXTRAIT DU PDF]\n{pdf_text[:6000]}"}
        )

    request_body = {
        "model": settings.VLM_MODEL,
        "messages": [
            {"role": "system", "content": VLM_TRANSCRIPT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 1536,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.VLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        logger.warning("VLM request timed out after %ss: %s", settings.VLM_TIMEOUT_SECONDS, exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The Vision LLM timed out while extracting transcript grades.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("VLM HTTP transport error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach the configured Vision LLM provider.",
        ) from exc

    if resp.status_code != 200:
        logger.warning("VLM provider returned HTTP %s: %s", resp.status_code, resp.text[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision LLM provider returned error status {resp.status_code}.",
        )

    try:
        payload = resp.json()
        raw_content = str(payload["choices"][0]["message"]["content"])
        parsed_json = json.loads(_strip_json_fences(raw_content))
        if not isinstance(parsed_json, dict):
            raise ValueError("VLM response JSON root is not an object.")
        model_used = str(payload.get("model") or settings.VLM_MODEL)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Malformed JSON payload received from Vision LLM provider.",
        ) from exc

    return parsed_json, model_used


def _assemble_validated_result(
    raw_data: dict[str, Any],
    *,
    file_name: str,
    content_type: str,
    file_size_bytes: int,
    extraction_source: ExtractionSource,
    model_used: str,
    latency_ms: int,
    declared_average: float | None = None,
    initial_warnings: list[str] | None = None,
) -> BacTranscriptExtractionResult:
    """Validate extracted fields, recompute weighted average, and compute HITL review flags."""
    warnings = list(initial_warnings or [])
    threshold = settings.VLM_CONFIDENCE_THRESHOLD

    raw_doc_type = str(raw_data.get("document_type") or "RELEVE_BAC")
    document_type: DocumentType = (
        "RELEVE_UNIVERSITAIRE" if raw_doc_type == "RELEVE_UNIVERSITAIRE" else "RELEVE_BAC"
    )
    raw_stage = str(
        raw_data.get("diploma_stage")
        or ("L3" if document_type == "RELEVE_UNIVERSITAIRE" else "BAC")
    )
    valid_stages = ("BAC", "L1", "L2", "L3", "PREPA_1", "PREPA_2", "M1", "OTHER")
    diploma_stage: DiplomaStage = raw_stage if raw_stage in valid_stages else "OTHER"  # type: ignore[assignment]

    bac_section: BacSection | None = (
        normalize_bac_section(raw_data.get("bac_section"))
        if document_type == "RELEVE_BAC" or raw_data.get("bac_section")
        else None
    )
    raw_session_type = str(raw_data.get("session_type") or "Principale")
    valid_sessions = ("Principale", "Contrôle", "Rattrapage", "Inconnue")
    session_type: TranscriptSessionType = (
        raw_session_type if raw_session_type in valid_sessions else "Inconnue"  # type: ignore[assignment]
    )

    subjects: list[ExtractedSubjectGrade] = []
    low_conf_fields: list[str] = []

    for row in raw_data.get("subjects") or []:
        if not isinstance(row, dict):
            continue
        try:
            raw_grade = row.get("grade")
            if raw_grade is None or str(raw_grade).strip().startswith("*"):
                continue
            grade_val = round(_parse_decimal_number(raw_grade), 2)
            raw_name = str(row["name"]).strip()
            canonical_name, coeff_val, is_opt = resolve_tunisian_subject_and_coefficient(
                raw_name,
                bac_section or "Autre",
                provided_coeff=row.get("coefficient"),
                provided_optional=row.get("is_optional"),
            )
            conf_val = round(_parse_decimal_number(row.get("confidence", 0.5)), 3)
            raw_cr = row.get("credits_acquired")
            subj_credits = round(_parse_decimal_number(raw_cr), 2) if raw_cr is not None else None
            needs_confirm = conf_val < threshold
            weighted = (
                round(max(0.0, grade_val - 10.0) * coeff_val, 2)
                if is_opt
                else round(grade_val * coeff_val, 2)
            )
            subj = ExtractedSubjectGrade(
                name=canonical_name,
                grade=grade_val,
                grade_scale=20.0,
                coefficient=coeff_val,
                weighted_points=weighted,
                credits_acquired=subj_credits,
                is_optional=is_opt,
                confidence=conf_val,
                needs_confirmation=needs_confirm,
            )
            subjects.append(subj)
            if needs_confirm:
                low_conf_fields.append(f"subject:{subj.name}")
        except (KeyError, TypeError, ValueError):
            warnings.append("Skipped malformed subject row returned during extraction.")

    raw_fc = raw_data.get("field_confidences") or {}
    fc = TranscriptFieldConfidence(
        bac_section=round(float(raw_fc.get("bac_section", 0.5)), 3),
        session_year=round(float(raw_fc.get("session_year", 0.5)), 3),
        general_average=round(float(raw_fc.get("general_average", 0.5)), 3),
        student_name=(
            round(float(raw_fc["student_name"]), 3)
            if raw_fc.get("student_name") is not None
            else None
        ),
        institution=(
            round(float(raw_fc["institution"]), 3)
            if raw_fc.get("institution") is not None
            else None
        ),
    )

    for fname in ("bac_section", "session_year", "general_average", "student_name", "institution"):
        val = getattr(fc, fname)
        if val is not None and val < threshold:
            low_conf_fields.append(fname)

    all_confidences = [fc.bac_section, fc.session_year, fc.general_average]
    if fc.student_name is not None:
        all_confidences.append(fc.student_name)
    if fc.institution is not None:
        all_confidences.append(fc.institution)
    all_confidences.extend(s.confidence for s in subjects)

    min_conf = round(min(all_confidences), 3) if all_confidences else 0.0
    overall_conf = round(sum(all_confidences) / len(all_confidences), 3) if all_confidences else 0.0

    raw_avg = raw_data.get("general_average")
    general_average = round(_parse_decimal_number(raw_avg), 2) if raw_avg is not None else None
    calculated_average = compute_tunisian_bac_average(subjects)

    discrepancy: float | None = None
    discrepancy_detected = False
    if general_average is not None and calculated_average is not None:
        discrepancy = round(abs(general_average - calculated_average), 2)
        if discrepancy > DISCREPANCY_TOLERANCE:
            discrepancy_detected = True
            warnings.append(
                f"Discrepancy between printed average ({general_average:.2f}) and "
                f"weighted average ({calculated_average:.2f}): delta={discrepancy:.2f}."
            )

    if declared_average is not None and general_average is not None:
        decl_delta = round(abs(general_average - declared_average), 2)
        if decl_delta > DISCREPANCY_TOLERANCE:
            discrepancy_detected = True
            discrepancy = max(discrepancy or 0.0, decl_delta)
            warnings.append(
                f"Discrepancy between extracted average ({general_average:.2f}) and "
                f"candidate declared average ({declared_average:.2f}): delta={decl_delta:.2f}."
            )

    if not subjects:
        warnings.append(
            "No subject grades could be extracted automatically; manual entry required."
        )

    needs_manual_review = (
        bool(low_conf_fields)
        or discrepancy_detected
        or not subjects
        or min_conf < threshold
    )
    verification_status = "MISMATCH_RH" if discrepancy_detected else "PENDING"

    raw_total_credits = raw_data.get("credits_acquired")
    total_credits = (
        round(_parse_decimal_number(raw_total_credits), 2)
        if raw_total_credits is not None
        else None
    )

    return BacTranscriptExtractionResult(
        document_type=document_type,
        diploma_stage=diploma_stage,
        bac_section=bac_section,
        program_title=(
            str(raw_data["program_title"]).strip() if raw_data.get("program_title") else None
        ),
        session_year=(
            str(raw_data["session_year"]).strip() if raw_data.get("session_year") else None
        ),
        session_type=session_type,
        student_name=(
            str(raw_data["student_name"]).strip() if raw_data.get("student_name") else None
        ),
        institution=(
            str(raw_data["institution"]).strip() if raw_data.get("institution") else None
        ),
        governorate=(
            str(raw_data["governorate"]).strip() if raw_data.get("governorate") else None
        ),
        general_average=general_average,
        calculated_average=calculated_average,
        declared_average=round(declared_average, 2) if declared_average is not None else None,
        average_discrepancy=discrepancy,
        discrepancy_detected=discrepancy_detected,
        credits_acquired=total_credits,
        mention=normalize_mention(raw_data.get("mention"), general_average or calculated_average),
        jury_decision=(
            str(raw_data["jury_decision"]).strip() if raw_data.get("jury_decision") else None
        ),
        subjects=subjects,
        field_confidences=fc,
        overall_confidence=overall_conf,
        min_confidence=min_conf,
        low_confidence_fields=low_conf_fields,
        needs_manual_review=needs_manual_review,
        verification_status=verification_status,
        extraction_source=extraction_source,
        model_used=model_used,
        file_name=file_name,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        latency_ms=max(1, latency_ms),
        warnings=warnings,
    )


async def extract_bac_transcript(
    raw_bytes: bytes,
    *,
    file_name: str,
    declared_content_type: str | None = None,
    declared_average: float | None = None,
) -> BacTranscriptExtractionResult:
    """Validate and extract Tunisian Baccalaureate grades from an uploaded image or PDF."""
    start_ns = time.perf_counter_ns()
    mime_type = validate_uploaded_transcript(
        raw_bytes,
        file_name=file_name,
        declared_content_type=declared_content_type,
    )

    vlm_provider = settings.VLM_PROVIDER.lower().strip()
    has_live_key = vlm_provider == "ollama" or (
        vlm_provider == "openai_compatible" and bool(settings.effective_vlm_api_key)
    )

    if mime_type == "application/pdf":
        pdf_text, page_count, embedded_img = await asyncio.to_thread(
            _extract_pdf_content, raw_bytes
        )
        warnings = []
        if has_live_key and embedded_img is not None and not pdf_text:
            # Real scanned PDF (e.g. official Tunisian Bac or ISIMS Sfax scan): extract scan image
            img_mime = _detect_magic_mime(embedded_img) or "image/jpeg"
            data_uri, _ = await asyncio.to_thread(_prepare_image_data_uri, embedded_img, img_mime)
            raw_json, model_used = await _call_vlm_openai_compatible(image_data_uri=data_uri)
            source: ExtractionSource = "vlm_live"
        elif has_live_key and pdf_text:
            raw_json, model_used = await _call_vlm_openai_compatible(pdf_text=pdf_text)
            source = "vlm_live"
        elif pdf_text and settings.VLM_FALLBACK_TO_LOCAL_PARSER:
            raw_json = _parse_tunisian_bac_pdf_text(pdf_text)
            model_used = "smartcampus-pdf-bac-parser-v1"
            source = "pdf_text_parser"
            if page_count > 1:
                warnings.append(f"Multi-page PDF detected ({page_count} pages); parsed all text.")
        elif not has_live_key and not settings.VLM_FALLBACK_TO_LOCAL_PARSER:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VLM_API_KEY is not configured and local PDF fallback is disabled.",
            )
        else:
            # Scanned image-only PDF with no embedded text layer and no live VLM key
            raw_json = {
                "bac_section": "Autre",
                "subjects": [],
                "field_confidences": {
                    "bac_section": 0.0,
                    "session_year": 0.0,
                    "general_average": 0.0,
                },
            }
            model_used = "smartcampus-vlm-unconfigured"
            source = "image_fallback"
            warnings = [
                "Scanned PDF contains raster page image(s) without embedded text and VLM_API_KEY "
                "is unset. Configure VLM_API_KEY (Groq qwen/qwen3.8-27b or OpenAI gpt-4o-mini)."
            ]
    else:
        data_uri, (width, height) = await asyncio.to_thread(
            _prepare_image_data_uri, raw_bytes, mime_type
        )
        warnings = []
        if min(width, height) < 400:
            warnings.append(
                f"Low-resolution transcript scan detected ({width}x{height}px); "
                "manual review recommended."
            )
        if has_live_key:
            raw_json, model_used = await _call_vlm_openai_compatible(image_data_uri=data_uri)
            source = "vlm_live"
        elif settings.VLM_FALLBACK_TO_LOCAL_PARSER:
            # Honest zero-hallucination behavior: never fabricate grades from a raster image
            raw_json = {
                "bac_section": "Autre",
                "subjects": [],
                "field_confidences": {
                    "bac_section": 0.0,
                    "session_year": 0.0,
                    "general_average": 0.0,
                },
            }
            model_used = "smartcampus-image-fallback-v1"
            source = "image_fallback"
            warnings.append(
                "VLM_API_KEY is not configured in .env; raster image OCR requires a live "
                "Vision LLM (Groq qwen/qwen3.8-27b or GPT-4o-mini), or upload a digital PDF."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VLM_API_KEY is not configured and image fallback is disabled.",
            )

    elapsed_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))
    return _assemble_validated_result(
        raw_json,
        file_name=file_name,
        content_type=mime_type,
        file_size_bytes=len(raw_bytes),
        extraction_source=source,
        model_used=model_used,
        latency_ms=elapsed_ms,
        declared_average=declared_average,
        initial_warnings=warnings,
    )


def get_vlm_health() -> VlmHealthResponse:
    """Return current VLM configuration and readiness status."""
    provider = settings.VLM_PROVIDER.lower().strip()
    has_key = bool(settings.effective_vlm_api_key)
    if provider == "ollama" or (provider == "openai_compatible" and has_key):
        vlm_status = "ready"
    elif settings.VLM_FALLBACK_TO_LOCAL_PARSER:
        vlm_status = "local_pdf_fallback"
    else:
        vlm_status = "misconfigured"

    return VlmHealthResponse(
        status=vlm_status,
        provider=provider,
        model=settings.VLM_MODEL,
        base_url=settings.effective_vlm_base_url,
        api_key_configured=has_key,
        confidence_threshold=settings.VLM_CONFIDENCE_THRESHOLD,
        max_file_size_bytes=settings.VLM_MAX_FILE_SIZE_BYTES,
        supported_mime_types=list(SUPPORTED_MIME_TYPES),
        local_pdf_fallback_enabled=settings.VLM_FALLBACK_TO_LOCAL_PARSER,
    )
