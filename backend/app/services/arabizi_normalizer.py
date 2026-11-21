"""Tunisian Arabizi to structured French normalization and pre-processing module (Bonus-4).

Translates and normalizes Tunisian Arabizi tokens and phonetic digit substitutions:
- 3 -> ع ('ain)
- 7 -> ح (7a)
- 9 -> ق (qaf)
- 5 -> خ (kha)
- 2 -> ء / أ (hamza)

Preserves academic and numeric entities intact (Bac+3, Bac+5, L1-L3, M1-M2, 1ère, 2ème, 3ème,
grades 15.75, years 2026-2027, fees 7500 DT).
Translates common Derja conversational tokens into clean structured French for LLM ingestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Phonetic character mapping for Arabizi words
PHONETIC_ARABIZI_MAP: dict[str, str] = {
    "3": "ع",
    "7": "ح",
    "9": "ق",
    "5": "خ",
    "2": "ء",
}

# Regex protecting academic and numeric figures from phonetic digit substitution
_ACADEMIC_NUMBER_PROTECT_RE = re.compile(
    r"\b("
    r"bac\s*\+[1-9]|"
    r"[lms][1-6]|"
    r"\d+(?:[èe]re|[èe]me|er|nd|nde)|"
    r"\d{1,2}[.,]\d{1,2}|"
    r"\d{4}-\d{4}|"
    r"\d{4}/\d{4}|"
    r"\d+(?:\s*(?:dt|tnd|eur|dinars?|dt/an|ans?|mois))|"
    r"iit|isb|isa|glid|sdia|arsi"
    r")\b",
    re.IGNORECASE,
)

# Multi-word Tunisian Arabizi expressions to normalize in a pre-tokenization phrase pass
MULTI_WORD_ARABIZI_LEXICON: dict[str, tuple[str, str]] = {
    "men ghir": ("sans", "من غير"),
    "min ghir": ("sans", "من غير"),
}

# Core dictionary mapping common Tunisian Arabizi tokens to structured French expressions
# (Pure French words such as merci, prepa, releve, bourse are excluded to prevent false positives)
ARABIZI_TO_FRENCH_LEXICON: dict[str, str] = {
    # Salutations & formules de politesse
    "3aslema": "bonjour",
    "aslema": "bonjour",
    "3slm": "bonjour",
    "slm": "bonjour",
    "mar7ba": "bienvenue",
    "marhba": "bienvenue",
    "bellehi": "s'il vous plaît",
    "blhi": "s'il vous plaît",
    "brbi": "s'il vous plaît",
    "yechmik": "merci",
    # Mots interrogatifs
    "chnowa": "qu'est-ce que",
    "chnoma": "quelles sont",
    "chnoua": "qu'est-ce que",
    "chnouwa": "qu'est-ce que",
    "chnouma": "quelles sont",
    "chniyya": "quelle est",
    "kifech": "comment",
    "kifeh": "comment",
    "kfh": "comment",
    "9adech": "combien coûtent",
    "gaddech": "combien coûtent",
    "2addech": "combien coûtent",
    "win": "où se trouve",
    "fayn": "où se trouve",
    "chkoun": "qui",
    "wa9tech": "quand",
    "wa9tah": "quand",
    "za3ma": "est-ce que",
    # Existants & quantificateurs
    "fama": "y a-t-il",
    "famach": "y a-t-il",
    "barcha": "beaucoup",
    "chwaya": "un peu",
    "kol": "chaque",
    # Verbes courants
    "n7eb": "je souhaite",
    "nheb": "je souhaite",
    "nhebb": "je souhaite",
    "t7eb": "vous souhaitez",
    "theb": "vous souhaitez",
    "najem": "puis-je",
    "tnajem": "pouvez-vous",
    "ynajem": "peut-on",
    "ynajmou": "peuvent-ils",
    "nod5el": "intégrer",
    "nodkhel": "intégrer",
    "nod5lou": "intégrer",
    "net5arej": "obtenir mon diplôme",
    "netkharej": "obtenir mon diplôme",
    "na5ou": "obtenir",
    "ne5ou": "obtenir",
    "ne5dhou": "obtenir",
    "tsob": "déposer",
    "nsob": "déposer",
    "n3amer": "remplir",
    "n3amel": "postuler",
    "ywalmni": "me convient",
    "ywalmouni": "me conviennent",
    "n5ayar": "choisir",
    "nkhtar": "choisir",
    "ta9bel": "acceptez-vous",
    "ya9blou": "acceptent-ils",
    "trfadh": "refusez-vous",
    "trfadhnii": "me refusez-vous",
    "na3ref": "savoir",
    "ta3rfou": "savez-vous",
    "a3tini": "donnez-moi",
    "ta3tini": "donnez-moi",
    # Prépositions, pronoms & conjonctions
    "mte3": "de",
    "mta3": "de",
    "mte3i": "mon",
    "mte3ek": "votre",
    "mte3kom": "votre",
    "fel": "dans le",
    "fi": "à",
    "mel": "de",
    "men": "de",
    "lel": "pour le",
    "bech": "pour",
    "bch": "pour",
    "5ater": "parce que",
    "khater": "parce que",
    "w": "et",
    "wala": "ou",
    "walla": "ou",
    "tawa": "actuellement",
    "taw": "maintenant",
    "3andi": "j'ai",
    "3andkom": "vous avez",
    "3anna": "nous avons",
    "hedha": "ceci",
    "hedhi": "cette",
    "heki": "cela",
    "ken": "seulement",
    "ghir": "sans",
    # Termes académiques et temporels tunisiens
    "lfac": "la faculté",
    "l3am": "l'année",
    "snin": "années",
    "3am": "année",
    "3amin": "deux ans",
}


@dataclass(frozen=True)
class ArabiziNormalizationResult:
    """Result of Arabizi normalization and translation to structured French."""

    original_text: str
    is_arabizi: bool
    normalized_french: str
    transliterated_arabic: str
    detected_tokens: list[str] = field(default_factory=list)


def is_arabizi_text(text: str) -> bool:
    """Return True if text contains Tunisian Arabizi tokens or phonetic digits."""
    # Mask protected academic entities (L1-L3, 3ème, Bac+3, etc.) before checking
    cleaned = _ACADEMIC_NUMBER_PROTECT_RE.sub(" ", text)

    # Check multi-word Arabizi phrases first (e.g. men ghir)
    for phrase in MULTI_WORD_ARABIZI_LEXICON:
        if re.search(rf"\b{re.escape(phrase)}\b", cleaned, re.IGNORECASE):
            return True

    words = re.findall(r"[A-Za-z0-9']+", cleaned.lower())
    for word in words:
        if word in ARABIZI_TO_FRENCH_LEXICON:
            return True
        if re.search(r"[a-z]+[23579][a-z]*|[a-z]*[23579][a-z]+", word):
            return True
    return False


def transliterate_phonetic_arabizi_word(word: str) -> str:
    """Substitute Arabizi phonetic numbers (3, 7, 9, 5, 2) with Arabic characters within a word."""
    # Check if word is purely an academic or numeric entity (e.g. Bac+3, L3, 3ème, 15, 2026)
    if re.fullmatch(
        r"bac\+[1-9]|[lms][1-6]|\d+(?:[èe]re|[èe]me|er)?|\d+",
        word,
        re.IGNORECASE,
    ):
        return word

    result = []
    for char in word:
        result.append(PHONETIC_ARABIZI_MAP.get(char, char))
    return "".join(result)


def normalize_arabizi_to_french(text: str) -> ArabiziNormalizationResult:
    """Convert Tunisian Arabizi message into clean, structured French text for LLM pre-processing.

    Protects academic figures (e.g. Bac+3, L3, 3ème, 15.75, 2026-2027, 7500 DT).
    Normalizes Arabizi phrases (e.g. men ghir -> sans) and tokens into clear French syntax.
    """
    if not text or not text.strip():
        return ArabiziNormalizationResult(
            original_text=text,
            is_arabizi=False,
            normalized_french="",
            transliterated_arabic="",
            detected_tokens=[],
        )

    # 1. Protect academic entities and numerical expressions with temporary placeholders
    placeholders: dict[str, str] = {}

    def _replace_academic(match: re.Match[str]) -> str:
        token = match.group(0)
        idx = len(placeholders)
        key = f"__ACADEMIC_ENT_{idx}__"
        placeholders[key] = token
        return key

    protected_text = _ACADEMIC_NUMBER_PROTECT_RE.sub(_replace_academic, text)

    # 2. Multi-word phrase pre-pass (e.g. "men ghir" -> "sans")
    phrase_placeholders: dict[str, tuple[str, str, str]] = {}
    has_arabizi = False
    detected_tokens: list[str] = []

    for phrase, (french_repl, arabic_repl) in MULTI_WORD_ARABIZI_LEXICON.items():
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
        for m in pattern.finditer(protected_text):
            has_arabizi = True
            matched_str = m.group(0)
            detected_tokens.append(matched_str)
            ph_key = f"__PHRASE_ENT_{len(phrase_placeholders)}__"
            phrase_placeholders[ph_key] = (french_repl, arabic_repl, matched_str)
        protected_text = pattern.sub(
            lambda m, pk=f"__PHRASE_ENT_{len(phrase_placeholders) - 1}__": pk,
            protected_text,
        )

    # 3. Tokenize and identify Arabizi words
    words = re.findall(r"[^\s.,!?;:()'\"]+|[.,!?;:()'\"]|\s+", protected_text)
    french_tokens: list[str] = []
    arabic_translit_tokens: list[str] = []

    for token in words:
        # Pass whitespace and punctuation through unchanged
        if not re.match(r"[A-Za-z0-9'_]+", token):
            french_tokens.append(token)
            arabic_translit_tokens.append(token)
            continue

        # Restore protected academic placeholders
        if token.startswith("__ACADEMIC_ENT_") and token in placeholders:
            real_val = placeholders[token]
            french_tokens.append(real_val)
            arabic_translit_tokens.append(real_val)
            continue

        # Restore multi-word phrase placeholders
        if token.startswith("__PHRASE_ENT_") and token in phrase_placeholders:
            french_repl, arabic_repl, _ = phrase_placeholders[token]
            french_tokens.append(french_repl)
            arabic_translit_tokens.append(arabic_repl)
            continue

        lower_token = token.lower()

        # 4. Check against Tunisian Arabizi dictionary
        if lower_token in ARABIZI_TO_FRENCH_LEXICON:
            has_arabizi = True
            detected_tokens.append(token)
            french_tokens.append(ARABIZI_TO_FRENCH_LEXICON[lower_token])
            arabic_translit_tokens.append(transliterate_phonetic_arabizi_word(token))
        # 5. Check for digit-based Arabizi words (e.g. 3aslema, n7eb, 9adech, nod5el)
        elif re.search(r"[23579]", lower_token) and re.search(r"[a-z]", lower_token):
            has_arabizi = True
            detected_tokens.append(token)
            translit = transliterate_phonetic_arabizi_word(token)
            arabic_translit_tokens.append(translit)
            french_tokens.append(ARABIZI_TO_FRENCH_LEXICON.get(lower_token, token))
        else:
            french_tokens.append(token)
            arabic_translit_tokens.append(token)

    normalized_french = "".join(french_tokens)
    normalized_french = re.sub(r"\s+", " ", normalized_french).strip()
    transliterated = "".join(arabic_translit_tokens)
    transliterated = re.sub(r"\s+", " ", transliterated).strip()

    # Re-insert any protected placeholders if still present
    for ph_key, real_val in placeholders.items():
        normalized_french = normalized_french.replace(ph_key, real_val)
        transliterated = transliterated.replace(ph_key, real_val)

    return ArabiziNormalizationResult(
        original_text=text,
        is_arabizi=has_arabizi,
        normalized_french=normalized_french,
        transliterated_arabic=transliterated,
        detected_tokens=detected_tokens,
    )
