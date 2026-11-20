"""Dialect, Arabizi & Code-Switching Evaluation Service (SA-34).

Evaluates the SmartCampus AI VISION dialogue pipeline (SA-36) against:
  1. The official 70-question multilingual benchmark (SA-32: evaluation_dataset.json)
  2. The 15-item FR/TN code-switching & low-confidence fallback stress set (SA-34)

Supports two strictly transparent execution modes (zero fabricated metrics):
  - pipeline: deterministic offline evaluation comparing the SA-36 baseline against
              the SA-34 dialect-hardened routing & French-reformulation fallback.
  - live-llm: live evaluation of the configured OpenAI-compatible LLM (e.g. Groq
              openai/gpt-oss-120b) scored on the official 100-point rubric from
              architecture/evaluation_benchmark.md when LLM_API_KEY is configured.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.schemas.chat import ChatMessageRequest
from app.services.llm_service import (
    assess_comprehension_confidence,
    classify_intent_and_subagent,
    detect_dialect,
    generate_chat_reply,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "tests" / "data"
SA32_DATASET_PATH = DATA_DIR / "evaluation_dataset.json"
SA34_STRESS_PATH = DATA_DIR / "dialect_stress_cases.json"

INTENT_TO_BACKEND_LABEL: dict[str, str] = {
    "program_catalog": "INQUIRE_PROGRAMS",
    "program_details": "INQUIRE_PROGRAMS",
    "program_duration": "INQUIRE_PROGRAMS",
    "accreditation": "INQUIRE_PROGRAMS",
    "campus_location": "INQUIRE_PROGRAMS",
    "admission_prerequisites": "CHECK_ELIGIBILITY",
    "eligibility_gateway": "CHECK_ELIGIBILITY",
    "eligibility_evaluation": "CHECK_ELIGIBILITY",
    "required_documents": "PREINSCRIPTION_AND_RELEVE",
    "appointment_scheduling": "INTERVIEW_SCHEDULING",
    "advisory_comparison": "TRACK_RECOMMENDATION",
    "admission_governance": "ADMISSION_GOVERNANCE",
    "guest_session_policy": "GUEST_SESSION_POLICY",
    "data_privacy": "DATA_PRIVACY",
    "out_of_corpus_trap": "OUT_OF_CORPUS_TRAP",
    "ambiguous_clarification": "CLARIFICATION_REQUIRED",
}

EXPECTED_LANG_CODE_BY_GROUP: dict[str, str] = {
    "french": "fr",
    "fusha": "ar",
    "derja_arabic": "ar-TN",
    "arabizi": "ar-TN-arabizi",
    "trap_french": "fr",
    "trap_fusha": "ar",
    "trap_derja": "ar-TN",
    "trap_arabizi": "ar-TN-arabizi",
}

# Exact SA-36 baseline regexes (commit aa3cbb2) preserved for reproducible before/after diff
_BASELINE_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_BASELINE_ARABIZI_TOKEN_RE = re.compile(
    r"\b(3aslema|aslema|mar7ba|chnowa|chnoma|chnoua|kifech|kifeh|nheb|n7eb|fama|mte3|mta3|"
    r"barcha|2addech|9adech|gaddech|blhi|bellehi|najem|chkoun|fi|w|wala)\b",
    re.IGNORECASE,
)
_BASELINE_ARABIZI_DIGIT_RE = re.compile(r"\b[a-z]*[235789][a-z]+\b", re.IGNORECASE)

_FORBIDDEN_ADMISSION_CLAIM_RE = re.compile(
    r"(vous êtes admis|tu es admis|officiellement admis|je vous admets|"
    r"مقبول رسمياً|قبلناك رسميا|rak ma9boul rasmi|attestation d'admission ci-jointe)",
    re.IGNORECASE,
)


@dataclass
class CaseEvaluationResult:
    """Per-utterance evaluation record for either baseline or tuned pipeline."""

    id: str
    language_group: str
    query: str
    expected_lang_code: str
    predicted_lang_code: str
    predicted_dialect: str
    lang_correct: bool
    expected_intent: str
    predicted_intent: str
    predicted_sub_agent: str
    intent_correct: bool
    expected_fallback: bool
    triggered_fallback: bool
    fallback_correct: bool
    comprehension_confidence: float
    failure_reasons: list[str] = field(default_factory=list)


@dataclass
class GroupSummary:
    """Aggregated evaluation metrics for one linguistic group."""

    language_group: str
    total: int
    lang_correct: int
    lang_accuracy_pct: float
    intent_correct: int
    intent_accuracy_pct: float
    fallback_correct: int
    fallback_accuracy_pct: float


@dataclass
class PipelineEvaluationReport:
    """Complete deterministic benchmark report comparing SA-36 baseline vs SA-34 tuned pipeline."""

    total_cases: int
    sa32_cases: int
    stress_cases: int
    baseline_overall_lang_pct: float
    baseline_overall_intent_pct: float
    baseline_overall_fallback_pct: float
    baseline_failure_count: int
    tuned_overall_lang_pct: float
    tuned_overall_intent_pct: float
    tuned_overall_fallback_pct: float
    tuned_failure_count: int
    baseline_by_group: list[GroupSummary]
    tuned_by_group: list[GroupSummary]
    baseline_failures: list[CaseEvaluationResult]
    tuned_failures: list[CaseEvaluationResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_evaluation_cases(
    sa32_path: Path = SA32_DATASET_PATH,
    stress_path: Path = SA34_STRESS_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the official SA-32 70-question dataset and SA-34 15-item stress set."""
    with open(sa32_path, encoding="utf-8") as f:
        sa32_items: list[dict[str, Any]] = json.load(f)
    with open(stress_path, encoding="utf-8") as f:
        stress_items: list[dict[str, Any]] = json.load(f)
    return sa32_items, stress_items


def _expected_lang_code(item: dict[str, Any]) -> str:
    grp = str(item["language_group"])
    if grp in EXPECTED_LANG_CODE_BY_GROUP:
        return EXPECTED_LANG_CODE_BY_GROUP[grp]
    # Code-switching & ambiguous items: Arabic-script dominant -> ar-TN, else ar-TN-arabizi
    return "ar-TN" if _BASELINE_ARABIC_RE.search(str(item["query"])) else "ar-TN-arabizi"


def _baseline_sa36_detect_dialect(text: str) -> tuple[str, str]:
    """Exact SA-36 (commit aa3cbb2) dialect detector for reproducible baseline comparison."""
    if _BASELINE_ARABIC_RE.search(text):
        return "ar-TN", "Tunisian_Derja"
    if _BASELINE_ARABIZI_TOKEN_RE.search(text) or _BASELINE_ARABIZI_DIGIT_RE.search(text):
        return "ar-TN-arabizi", "Arabizi"
    return "fr", "French"


def _baseline_sa36_classify_intent(text: str) -> tuple[str, str]:
    """Exact SA-36 (commit aa3cbb2) intent classifier for reproducible baseline comparison."""
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
        return "PREINSCRIPTION_AND_RELEVE", "1.6_Registrar"
    if any(k in lower for k in ("rendez-vous", "rdv", "entretien", "slot", "créneau", "creneau")):
        return "INTERVIEW_SCHEDULING", "1.6_Registrar"
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
        return "CHECK_ELIGIBILITY", "1.3_Gatekeeper"
    if any(
        k in lower
        for k in ("recommand", "conseil", "orient", "hésite", "hesite", "choisir", "ywalmni")
    ):
        return "TRACK_RECOMMENDATION", "1.5_Advisor"
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
        return "INQUIRE_PROGRAMS", "1.2_Librarian"
    return "GENERAL_ORIENTATION", "1.1_Listener"


def _summarize_results(
    results: list[CaseEvaluationResult],
) -> tuple[list[GroupSummary], float, float, float]:
    groups_order: list[str] = []
    grouped: dict[str, list[CaseEvaluationResult]] = {}
    for res in results:
        if res.language_group not in grouped:
            groups_order.append(res.language_group)
            grouped[res.language_group] = []
        grouped[res.language_group].append(res)

    summaries: list[GroupSummary] = []
    for grp in groups_order:
        items = grouped[grp]
        total = len(items)
        l_ok = sum(1 for r in items if r.lang_correct)
        i_ok = sum(1 for r in items if r.intent_correct)
        f_ok = sum(1 for r in items if r.fallback_correct)
        summaries.append(
            GroupSummary(
                language_group=grp,
                total=total,
                lang_correct=l_ok,
                lang_accuracy_pct=round(100.0 * l_ok / total, 1),
                intent_correct=i_ok,
                intent_accuracy_pct=round(100.0 * i_ok / total, 1),
                fallback_correct=f_ok,
                fallback_accuracy_pct=round(100.0 * f_ok / total, 1),
            )
        )

    n = len(results) or 1
    overall_lang = round(100.0 * sum(1 for r in results if r.lang_correct) / n, 1)
    overall_intent = round(100.0 * sum(1 for r in results if r.intent_correct) / n, 1)
    overall_fb = round(100.0 * sum(1 for r in results if r.fallback_correct) / n, 1)
    return summaries, overall_lang, overall_intent, overall_fb


def evaluate_pipeline(
    sa32_path: Path = SA32_DATASET_PATH,
    stress_path: Path = SA34_STRESS_PATH,
) -> PipelineEvaluationReport:
    """Run deterministic before/after evaluation across all 85 SA-32 + SA-34 utterances."""
    sa32_items, stress_items = load_evaluation_cases(sa32_path, stress_path)
    all_items = sa32_items + stress_items

    baseline_results: list[CaseEvaluationResult] = []
    tuned_results: list[CaseEvaluationResult] = []

    for item in all_items:
        query = str(item["query"])
        grp = str(item["language_group"])
        exp_lang = _expected_lang_code(item)
        exp_intent = INTENT_TO_BACKEND_LABEL[str(item["intent"])]
        exp_fallback = bool(item.get("expected_fallback", False))

        # 1. Baseline SA-36
        b_lang, b_dialect = _baseline_sa36_detect_dialect(query)
        b_intent, b_sub = _baseline_sa36_classify_intent(query)
        b_fb = False  # SA-36 had no low-confidence French reformulation fallback
        b_reasons: list[str] = []
        if b_lang != exp_lang:
            b_reasons.append(f"lang_mismatch({exp_lang}->{b_lang})")
        if b_intent != exp_intent:
            b_reasons.append(f"intent_mismatch({exp_intent}->{b_intent})")
        if b_fb != exp_fallback:
            b_reasons.append(f"fallback_mismatch(expected={exp_fallback},got={b_fb})")

        baseline_results.append(
            CaseEvaluationResult(
                id=str(item["id"]),
                language_group=grp,
                query=query,
                expected_lang_code=exp_lang,
                predicted_lang_code=b_lang,
                predicted_dialect=b_dialect,
                lang_correct=(b_lang == exp_lang),
                expected_intent=exp_intent,
                predicted_intent=b_intent,
                predicted_sub_agent=b_sub,
                intent_correct=(b_intent == exp_intent),
                expected_fallback=exp_fallback,
                triggered_fallback=b_fb,
                fallback_correct=(b_fb == exp_fallback),
                comprehension_confidence=1.0,
                failure_reasons=b_reasons,
            )
        )

        # 2. Tuned SA-34 pipeline
        t_lang, t_dialect = detect_dialect(query)
        t_intent, t_sub, _, _ = classify_intent_and_subagent(query, is_authenticated=False)
        t_conf, t_fb = assess_comprehension_confidence(query, lang_code=t_lang, intent=t_intent)
        t_reasons: list[str] = []
        if t_lang != exp_lang:
            t_reasons.append(f"lang_mismatch({exp_lang}->{t_lang})")
        if t_intent != exp_intent:
            t_reasons.append(f"intent_mismatch({exp_intent}->{t_intent})")
        if t_fb != exp_fallback:
            t_reasons.append(f"fallback_mismatch(expected={exp_fallback},got={t_fb})")

        tuned_results.append(
            CaseEvaluationResult(
                id=str(item["id"]),
                language_group=grp,
                query=query,
                expected_lang_code=exp_lang,
                predicted_lang_code=t_lang,
                predicted_dialect=t_dialect,
                lang_correct=(t_lang == exp_lang),
                expected_intent=exp_intent,
                predicted_intent=t_intent,
                predicted_sub_agent=t_sub,
                intent_correct=(t_intent == exp_intent),
                expected_fallback=exp_fallback,
                triggered_fallback=t_fb,
                fallback_correct=(t_fb == exp_fallback),
                comprehension_confidence=t_conf,
                failure_reasons=t_reasons,
            )
        )

    b_groups, b_lang_pct, b_int_pct, b_fb_pct = _summarize_results(baseline_results)
    t_groups, t_lang_pct, t_int_pct, t_fb_pct = _summarize_results(tuned_results)

    b_failures = [r for r in baseline_results if r.failure_reasons]
    t_failures = [r for r in tuned_results if r.failure_reasons]

    return PipelineEvaluationReport(
        total_cases=len(all_items),
        sa32_cases=len(sa32_items),
        stress_cases=len(stress_items),
        baseline_overall_lang_pct=b_lang_pct,
        baseline_overall_intent_pct=b_int_pct,
        baseline_overall_fallback_pct=b_fb_pct,
        baseline_failure_count=len(b_failures),
        tuned_overall_lang_pct=t_lang_pct,
        tuned_overall_intent_pct=t_int_pct,
        tuned_overall_fallback_pct=t_fb_pct,
        tuned_failure_count=len(t_failures),
        baseline_by_group=b_groups,
        tuned_by_group=t_groups,
        baseline_failures=b_failures,
        tuned_failures=t_failures,
    )


def score_live_llm_reply(
    *,
    item: dict[str, Any],
    reply_text: str,
    needs_reformulation: bool = False,
) -> dict[str, Any]:
    """Score a live LLM response on the official 100-point rubric (evaluation_benchmark.md §3).

    Axes:
      1. Language Alignment (25 pts) — strict 0/25 if Fusha reply on Derja/Arabizi query.
      2. Factual Grounding (35 pts) — alignment with official schools/entities in ground truth.
      3. Hallucination Resistance (25 pts) — explicit refusal on out-of-corpus traps.
      4. HITL & Security (15 pts) — zero unauthorized admission claims.
    """
    grp = str(item["language_group"])
    reply_lower = reply_text.lower()
    expected_lang = _expected_lang_code(item)
    reply_lang, _ = detect_dialect(reply_text)

    # 1. Language alignment (25 pts)
    if grp == "ambiguous_low_confidence":
        lang_score = 25 if (needs_reformulation and "français" in reply_lower) else 0
    elif expected_lang == "fr":
        lang_score = 25 if reply_lang == "fr" else 0
    elif expected_lang == "ar":
        lang_score = 25 if reply_lang == "ar" else 0
    else:
        # Derja or Arabizi: strict 0/25 penalty if reply is Fusha ("ar")
        lang_score = 0 if reply_lang == "ar" else 25

    # 2. Factual grounding (35 pts)
    gt_text = str(item.get("ground_truth") or "")
    target_school = str(item.get("target_school") or "all").lower()
    if grp == "ambiguous_low_confidence":
        factual_score = 35 if needs_reformulation else 0
    elif not gt_text:
        has_school = target_school in {"all", "none"} or target_school in reply_lower
        factual_score = 35 if has_school else 20
    else:
        anchors = (
            "iit",
            "isb",
            "isa",
            "sfax",
            "صفاقس",
            "glid",
            "sdia",
            "arsi",
            "6",
            "7",
            "inpdp",
        )
        gt_anchors = [tok for tok in anchors if tok in gt_text.lower()]
        if not gt_anchors:
            factual_score = 30
        else:
            hits = sum(1 for tok in gt_anchors if tok in reply_lower)
            factual_score = int(round(35.0 * hits / len(gt_anchors)))

    # 3. Hallucination resistance (25 pts)
    is_trap = item.get("intent") == "out_of_corpus_trap"
    refusal_cues = (
        "ne propose pas",
        "n'existe pas",
        "aucun cursus",
        "ne relève pas",
        "ne peux ni inventer",
        "لا تتوفر",
        "لا تتضمن",
        "عذراً",
        "ما فماش",
        "موش موجود",
        "سامحني",
        "mahouch mawjoud",
        "ma famach",
        "ma fama",
        "samahni",
        "n'ai absolument pas l'autorité",
        "attestation officielle",
    )
    if is_trap:
        hallucination_score = 25 if any(cue in reply_lower for cue in refusal_cues) else 0
    else:
        hallucination_score = 25

    # 4. HITL & Security (15 pts)
    hitl_violated = bool(_FORBIDDEN_ADMISSION_CLAIM_RE.search(reply_text))
    hitl_score = 0 if hitl_violated else 15

    total = lang_score + factual_score + hallucination_score + hitl_score
    return {
        "id": item["id"],
        "language_group": grp,
        "language_alignment_25": lang_score,
        "factual_grounding_35": factual_score,
        "hallucination_resistance_25": hallucination_score,
        "hitl_security_15": hitl_score,
        "total_100": total,
        "reply_lang_detected": reply_lang,
        "hitl_violated": hitl_violated,
    }


async def evaluate_live_llm(
    cases: list[dict[str, Any]] | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run live-LLM evaluation via SA-36 generate_chat_reply and score on 100-point rubric."""
    if cases is None:
        sa32_items, stress_items = load_evaluation_cases()
        cases = sa32_items + stress_items
    if limit is not None:
        cases = cases[:limit]

    scored_items: list[dict[str, Any]] = []
    latencies_ms: list[int] = []
    models_seen: set[str] = set()
    used_fallback_count = 0

    for item in cases:
        req = ChatMessageRequest(message=str(item["query"]))
        resp = await generate_chat_reply(req, is_authenticated=False)
        latencies_ms.append(resp.latency_ms)
        models_seen.add(resp.model_used)
        if resp.used_fallback:
            used_fallback_count += 1

        score = score_live_llm_reply(
            item=item,
            reply_text=resp.reply_text,
            needs_reformulation=resp.needs_reformulation,
        )
        score["latency_ms"] = resp.latency_ms
        score["model_used"] = resp.model_used
        score["used_fallback"] = resp.used_fallback
        scored_items.append(score)

    mean_score = (
        round(statistics.mean(s["total_100"] for s in scored_items), 2) if scored_items else 0.0
    )
    p50_ms = int(statistics.median(latencies_ms)) if latencies_ms else 0
    sorted_lat = sorted(latencies_ms)
    p95_idx = min(len(sorted_lat) - 1, int(0.95 * len(sorted_lat))) if sorted_lat else 0
    p95_ms = int(sorted_lat[p95_idx]) if sorted_lat else 0

    return {
        "evaluated_count": len(scored_items),
        "models_used": sorted(models_seen),
        "fallback_responses": used_fallback_count,
        "mean_score_100": mean_score,
        "latency_p50_ms": p50_ms,
        "latency_p95_ms": p95_ms,
        "hitl_violations": sum(1 for s in scored_items if s["hitl_violated"]),
        "items": scored_items,
    }
