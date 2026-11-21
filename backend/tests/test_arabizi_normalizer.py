"""Unit tests for Tunisian Arabizi to structured French normalization module (Bonus-4).

Verifies:
1. Phonetic digit mapping (3 -> ع, 7 -> ح, 9 -> ق, 5 -> خ, 2 -> ء).
2. Strict preservation of academic figures (Bac+3, Bac+5, 15.75, 2026-2027, 7500 DT).
3. Lexicon normalization of common Derja question stems, verbs, and conjunctions.
4. Full sentence Arabizi -> structured French normalization.
5. Integration as a pre-processing step before LLM call (_build_messages).
6. Regression tests: pure French (merci, bourse, prepa, L3, 3ème) and multi-word 'men ghir'.
"""

from app.schemas.chat import ChatMessageRequest
from app.services.arabizi_normalizer import (
    is_arabizi_text,
    normalize_arabizi_to_french,
    transliterate_phonetic_arabizi_word,
)
from app.services.llm_service import _build_messages


def test_phonetic_digit_transliteration():
    """Verify Arabizi phonetic substitutions: 3=ع, 7=ح, 9=ق, 5=خ, 2=ء."""
    # 3 -> ع ('ain)
    assert transliterate_phonetic_arabizi_word("3aslema") == "عaslema"
    assert transliterate_phonetic_arabizi_word("3andi") == "عandi"

    # 7 -> ح (7a)
    assert transliterate_phonetic_arabizi_word("n7eb") == "nحeb"
    assert transliterate_phonetic_arabizi_word("mar7ba") == "marحba"

    # 9 -> ق (qaf)
    assert transliterate_phonetic_arabizi_word("9adech") == "قadech"

    # 5 -> خ (kha)
    assert transliterate_phonetic_arabizi_word("5ater") == "خater"
    assert transliterate_phonetic_arabizi_word("nod5el") == "nodخel"

    # 2 -> ء (hamza)
    assert transliterate_phonetic_arabizi_word("sou2el") == "souءel"


def test_academic_and_numeric_entities_preserved():
    """Verify that academic tokens and real numbers are NOT corrupted by phonetic mapping."""
    # Bac+3 and Bac+5 must never become Bac+ع or Bac+خ
    res_bac = normalize_arabizi_to_french("3andi Bac+3 w n7eb na3mel Bac+5 fel IIT")
    assert "Bac+3" in res_bac.normalized_french
    assert "Bac+5" in res_bac.normalized_french
    assert "Bac+ع" not in res_bac.transliterated_arabic
    assert "Bac+خ" not in res_bac.transliterated_arabic

    # Grades and averages (e.g. 15.75) must stay intact
    res_grade = normalize_arabizi_to_french("moyenne mte3i 15.75 najem nod5el ?")
    assert "15.75" in res_grade.normalized_french

    # Academic years and financial amounts
    res_fee = normalize_arabizi_to_french("frais 2026-2027 9adech ? 7500 DT ?")
    assert "2026-2027" in res_fee.normalized_french
    assert "7500 DT" in res_fee.normalized_french


def test_common_lexicon_mapping():
    """Verify common Arabizi tokens convert to clear structured French equivalents."""
    # Greetings & politeness
    r_greet = normalize_arabizi_to_french("3aslema bellehi")
    assert "bonjour" in r_greet.normalized_french.lower()
    assert "s'il vous plaît" in r_greet.normalized_french.lower()

    # Question words
    r_q = normalize_arabizi_to_french("chnowa kifech win 9adech fama")
    assert "qu'est-ce que" in r_q.normalized_french.lower()
    assert "comment" in r_q.normalized_french.lower()
    assert "où" in r_q.normalized_french.lower()
    assert "combien" in r_q.normalized_french.lower()
    assert "y a-t-il" in r_q.normalized_french.lower()

    # Verbs
    r_v = normalize_arabizi_to_french("n7eb nod5el nsob")
    assert "je souhaite" in r_v.normalized_french.lower()
    assert "intégrer" in r_v.normalized_french.lower()
    assert "déposer" in r_v.normalized_french.lower()


def test_full_sentence_normalization():
    """Verify full Tunisian Arabizi sentences normalize into structured French semantics."""
    # Sentence 1: Inquiry about engineering specialties
    s1 = "3aslema, chnowa les filières d'ingénieur fel IIT ?"
    r1 = normalize_arabizi_to_french(s1)
    assert r1.is_arabizi is True
    assert "bonjour" in r1.normalized_french.lower()
    assert "filières d'ingénieur" in r1.normalized_french
    assert "iit" in r1.normalized_french.lower()

    # Sentence 2: Eligibility and prerequisite inquiry with degree
    s2 = "kifech najem nod5el lel cycle ingénieur 5ater 3andi licence ?"
    r2 = normalize_arabizi_to_french(s2)
    assert r2.is_arabizi is True
    assert "comment" in r2.normalized_french.lower()
    assert "puis-je" in r2.normalized_french.lower()
    assert "intégrer" in r2.normalized_french.lower()
    assert "parce que" in r2.normalized_french.lower()
    assert "licence" in r2.normalized_french.lower()


def test_is_arabizi_detection():
    """Verify Arabizi detection accurately distinguishes Arabizi from pure French and Arabic."""
    assert is_arabizi_text("3aslema kifech najem nod5el ?") is True
    assert is_arabizi_text("9adech les frais ?") is True
    assert is_arabizi_text("Quelles sont les filières proposées à l'IIT ?") is False
    assert is_arabizi_text("ما هي شروط القبول في IIT ؟") is False


def test_preprocessing_integration_before_llm_call():
    """Verify that normalized Arabizi is injected into system context before LLM call."""
    payload = ChatMessageRequest(
        message="3aslema, 9adech les frais mte3 cycle ingénieur fi IIT ?",
        session_id="qa-sess-arabizi-01",
    )
    messages = _build_messages(
        payload=payload,
        lang_code="ar-TN-arabizi",
        detected_dialect="Arabizi",
        intent="INQUIRE_PROGRAMS",
    )

    # Check system prompt message
    assert len(messages) >= 2
    system_msg = messages[0]["content"]
    assert "Pré-traitement Arabizi -> Français structuré (Bonus-4)" in system_msg
    assert "bonjour" in system_msg.lower()
    assert "combien" in system_msg.lower()
    # Ensure user message is kept original
    user_msg = messages[-1]["content"]
    assert user_msg == "3aslema, 9adech les frais mte3 cycle ingénieur fi IIT ?"


def test_regression_pure_french_words_not_classified_as_arabizi():
    """Verify that pure French sentences (merci, bourse, prepa, releve) do not trigger Arabizi."""
    msg1 = "Bonjour, merci ! Y a-t-il une bourse pour les étudiants ?"
    assert is_arabizi_text(msg1) is False
    res1 = normalize_arabizi_to_french(msg1)
    assert res1.is_arabizi is False
    assert res1.normalized_french == msg1

    msg2 = "Je veux envoyer mon releve de notes pour la prepa"
    assert is_arabizi_text(msg2) is False
    res2 = normalize_arabizi_to_french(msg2)
    assert res2.is_arabizi is False


def test_regression_academic_levels_and_ordinals_protected():
    """Verify L1-L3, M1-M2, and French ordinals (3ème, 2ème) are untouched and not Arabizi."""
    msg = "Je suis en L3, je veux entrer en 3ème année de cycle ingénieur"
    assert is_arabizi_text(msg) is False
    res = normalize_arabizi_to_french(msg)
    assert res.is_arabizi is False
    assert "L3" in res.normalized_french
    assert "3ème" in res.normalized_french
    assert "Lع" not in res.transliterated_arabic
    assert "عème" not in res.transliterated_arabic

    # Additional ordinals and levels
    assert is_arabizi_text("Candidature en M1 après une 1ère et 2ème année") is False


def test_regression_multi_word_men_ghir_normalized_to_sans():
    """Verify multi-word expression 'men ghir' normalizes to 'sans' instead of 'de sans'."""
    sentence = "najem nod5el men ghir concours ?"
    assert is_arabizi_text(sentence) is True
    res = normalize_arabizi_to_french(sentence)
    assert res.is_arabizi is True
    assert "sans" in res.normalized_french
    assert "de sans" not in res.normalized_french
    assert "men ghir" in res.detected_tokens
    assert "puis-je" in res.normalized_french
    assert "intégrer" in res.normalized_french
