"""Live LLM Benchmark & Model Evaluation Service (SA-33).

Benchmarks candidate LLM models against the official SA-32 70-question multilingual dataset
(French, Fusha, Tunisian Derja, Arabizi, and out-of-corpus traps) using real live API calls.

Strict anti-hallucination compliance (SC V3 §10):
  - Zero fabricated or simulated scores/latencies.
  - When LLM_API_KEY is configured, sends asynchronous /chat/completions requests to the
    provider (Groq / OpenRouter) parameterizing each candidate model ID.
  - When LLM_API_KEY is not configured, models are transparently marked as
    'Pending live run (requires LLM_API_KEY)' rather than producing fake metrics.
  - Unit tests use explicit AsyncMock / unittest.mock to test evaluation & scoring logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.chat import ChatMessageRequest
from app.services.llm_service import (
    _build_messages,
    classify_intent_and_subagent,
    detect_dialect,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "tests" / "data"
SA32_DATASET_PATH = DATA_DIR / "evaluation_dataset.json"

_FORBIDDEN_ADMISSION_CLAIM_RE = re.compile(
    r"(vous êtes admis|tu es admis|officiellement admis|je vous admets|"
    r"مقبول رسمياً|قبلناك رسميا|rak ma9boul rasmi|attestation d'admission ci-jointe)",
    re.IGNORECASE,
)


@dataclass
class CandidateModelProfile:
    """Specification and empirical runtime parameters for a candidate LLM."""

    model_id: str
    display_name: str
    provider: str
    parameter_size: str
    context_window: int
    input_cost_per_m: float  # USD per 1M input tokens
    output_cost_per_m: float  # USD per 1M output tokens
    target_latency_p50_s: float  # Target latency from architecture/llm_models.md
    recommended_role: str  # VISION, JARVIS, VLM, or ALTERNATIVE
    description: str


CANDIDATE_MODELS: list[CandidateModelProfile] = [
    CandidateModelProfile(
        model_id="openai/gpt-oss-120b",
        display_name="GPT-OSS 120B",
        provider="Groq",
        parameter_size="120B",
        context_window=131072,
        input_cost_per_m=0.59,
        output_cost_per_m=0.79,
        target_latency_p50_s=1.8,
        recommended_role="VISION Supervisor (Selected SA-36)",
        description=(
            "Primary multilingual reasoning engine on Groq LPU (SC V3 / SA-36). "
            "High Tunisian Derja & Arabizi retention, low acronym hallucination, target < 2.5s."
        ),
    ),
    CandidateModelProfile(
        model_id="openai/gpt-oss-20b",
        display_name="GPT-OSS 20B",
        provider="Groq",
        parameter_size="20B",
        context_window=131072,
        input_cost_per_m=0.10,
        output_cost_per_m=0.15,
        target_latency_p50_s=0.9,
        recommended_role="JARVIS Scheduler (Selected SA-36)",
        description=(
            "Ultra-low latency (~850ms), high-throughput model optimized for asynchronous Celery "
            "tasks, slot confirmation classification, and candidate follow-ups."
        ),
    ),
    CandidateModelProfile(
        model_id="qwen/qwen3.8-27b",
        display_name="Qwen 3.8 27B",
        provider="Groq",
        parameter_size="27B",
        context_window=32768,
        input_cost_per_m=0.20,
        output_cost_per_m=0.20,
        target_latency_p50_s=1.5,
        recommended_role="VLM Extraction / Alternative (SA-36)",
        description=(
            "Multimodal Vision & text model on Groq. Handles transcript JSON extraction "
            "and serves as primary fallback for academic queries."
        ),
    ),
]


@dataclass
class ModelGroupMetrics:
    """Aggregated evaluation metrics for one linguistic subset."""

    language_group: str
    total_queries: int
    mean_score: float
    lang_alignment_pct: float
    factual_accuracy_pct: float
    hallucination_resistance_pct: float
    hitl_compliance_pct: float
    latency_mean_ms: int


@dataclass
class ModelBenchmarkSummary:
    """Consolidated benchmark results for a single candidate LLM."""

    model_id: str
    display_name: str
    provider: str
    parameter_size: str
    total_evaluated: int
    status: str  # COMPLETED | PENDING_LIVE_RUN | FAILED
    overall_score_100: float | None
    french_score_100: float | None
    fusha_score_100: float | None
    derja_arabic_score_100: float | None
    arabizi_score_100: float | None
    trap_score_100: float | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    latency_mean_ms: int | None
    cost_per_1k_queries_usd: float
    hitl_violations: int
    hallucination_trap_failures: int
    group_metrics: list[ModelGroupMetrics] = field(default_factory=list)
    rank: int = 0
    selection_verdict: str = ""
    error_message: str | None = None


@dataclass
class BenchmarkComparisonReport:
    """Full comparative benchmark report across all candidate LLMs for SA-33."""

    dataset_total_cases: int
    timestamp_iso: str
    provider_base_url: str
    live_run_executed: bool
    models: list[ModelBenchmarkSummary]
    recommended_vision_model: str
    recommended_jarvis_model: str
    markdown_comparison_table: str


def load_sa32_dataset() -> list[dict[str, Any]]:
    """Load the official SA-32 70-question benchmark dataset."""
    if not SA32_DATASET_PATH.exists():
        raise FileNotFoundError(f"SA-32 dataset not found at {SA32_DATASET_PATH}")
    with open(SA32_DATASET_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if len(data) != 70:
        raise ValueError(f"Expected 70 benchmark questions, found {len(data)}")
    return data


def score_response(
    item: dict[str, Any],
    reply_text: str,
    latency_ms: int = 0,
) -> dict[str, Any]:
    """Score a single LLM response on the official 100-point rubric from evaluation_benchmark.md.

    - 1. Language Alignment: 25 pts (strict 0 if Fusha returned for Derja/Arabizi)
    - 2. Factual Grounding & RAG: 35 pts (accurate programs, degrees, duration, locations)
    - 3. Hallucination Resistance: 25 pts (polite refusal of nonexistent programs/traps)
    - 4. HITL Governance & Security: 15 pts (zero admission/rejection claims by AI)
    """
    reply_lower = reply_text.lower()
    reply_lang, _ = detect_dialect(reply_text)
    grp = str(item.get("language_group") or "french")
    expected_lang = "fr"
    if grp == "fusha" or grp == "trap_fusha":
        expected_lang = "ar"
    elif grp in {"derja_arabic", "trap_derja"}:
        expected_lang = "ar-TN"
    elif grp in {"arabizi", "trap_arabizi"}:
        expected_lang = "ar-TN-arabizi"

    # 1. Language alignment (25 pts)
    if expected_lang == "fr":
        lang_score = 25 if reply_lang == "fr" else 0
    elif expected_lang == "ar":
        lang_score = 25 if reply_lang == "ar" else 0
    elif expected_lang == "ar-TN":
        lang_score = 25 if reply_lang in {"ar-TN", "ar-TN-arabizi"} else 0
    else:
        # Arabizi: natural Tunisian reply expected (ar-TN-arabizi or ar-TN). Fusha ("ar") -> 0
        lang_score = 25 if reply_lang in {"ar-TN-arabizi", "ar-TN"} else 0

    # 2. Factual grounding (35 pts)
    gt_text = str(item.get("ground_truth") or "")
    if not gt_text or grp.startswith("trap_"):
        factual_score = 35
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
            "génie logiciel",
            "finance",
            "comptabilité",
            "architecture",
            "6",
            "7",
            "master",
            "licence",
            "ingénieur",
            "passerelle",
            "inpdp",
        )
        gt_anchors = [tok for tok in anchors if tok in gt_text.lower()]
        if not gt_anchors:
            factual_score = 30
        else:
            hits = sum(1 for tok in gt_anchors if tok in reply_lower)
            factual_score = int(round(35.0 * hits / len(gt_anchors)))
            factual_score = max(15, min(35, factual_score))

    # 3. Hallucination resistance (25 pts)
    is_trap = grp.startswith("trap_") or item.get("intent") == "out_of_corpus_trap"
    refusal_cues = (
        "ne propose pas",
        "ne proposons pas",
        "ne dispense pas",
        "ne dispensons pas",
        "ne dispose pas",
        "ne disposons pas",
        "n'existe pas",
        "n'existent pas",
        "aucun cursus",
        "aucune filière",
        "pas de filière",
        "pas de cursus",
        "pas de faculté",
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
        "non habilité",
        "pas d'équivalence directe",
        "n'ai absolument pas l'autorité",
        "attestation officielle",
    )
    if is_trap:
        refused_properly = any(cue in reply_lower for cue in refusal_cues)
        hallucination_score = 25 if refused_properly else 0
    else:
        hallucination_score = 25

    # 4. HITL Governance & Security (15 pts)
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
        "trap_failed": is_trap and (hallucination_score == 0),
        "latency_ms": latency_ms,
    }


def select_stratified_items(
    dataset: list[dict[str, Any]],
    limit: int | None,
) -> list[dict[str, Any]]:
    """Select up to `limit` items balanced across all 5 SA-32 evaluation groups."""
    if limit is None or limit >= len(dataset):
        return list(dataset)
    if limit <= 0:
        return []

    buckets: dict[str, list[dict[str, Any]]] = {
        "french": [],
        "fusha": [],
        "derja_arabic": [],
        "arabizi": [],
        "traps": [],
    }
    for item in dataset:
        grp = str(item.get("language_group") or "french")
        key = "traps" if grp.startswith("trap_") else grp
        buckets.setdefault(key, []).append(item)

    selected: list[dict[str, Any]] = []
    bucket_keys = [k for k, v in buckets.items() if v]
    idx = 0
    while len(selected) < limit and bucket_keys:
        progressed = False
        for key in bucket_keys:
            if idx < len(buckets[key]) and len(selected) < limit:
                selected.append(buckets[key][idx])
                progressed = True
        if not progressed:
            break
        idx += 1
    return selected


def _parse_retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Compute backoff seconds from Retry-After header or Groq error body."""
    header_val = resp.headers.get("retry-after")
    if header_val:
        try:
            return max(1.0, min(float(header_val), 65.0))
        except ValueError:
            pass
    match = re.search(r"try again in (\d+(?:\.\d+)?)s", resp.text, re.IGNORECASE)
    if match:
        return max(1.0, min(float(match.group(1)) + 1.0, 65.0))
    return float(min(5 * (attempt + 1), 30))


async def call_candidate_llm_api(
    model_id: str,
    messages: list[dict[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 4,
) -> tuple[str, str, int]:
    """Execute an async /chat/completions call parameterizing model_id and measure latency."""
    effective_base = (base_url or settings.LLM_BASE_URL).rstrip("/")
    effective_key = api_key if api_key is not None else settings.LLM_API_KEY
    url = f"{effective_base}/chat/completions"

    if not effective_key.strip():
        raise ValueError(
            "LLM_API_KEY is not configured. Cannot perform live LLM benchmark."
        )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {effective_key}",
    }
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": min(settings.LLM_MAX_TOKENS, 512),
    }

    resp: httpx.Response | None = None
    elapsed_ms = 1
    for attempt in range(max_retries + 1):
        start_ns = time.perf_counter_ns()
        if client is not None:
            resp = await client.post(url, headers=headers, json=body)
        else:
            async with httpx.AsyncClient(timeout=timeout_seconds) as ac:
                resp = await ac.post(url, headers=headers, json=body)

        elapsed_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))
        if resp.status_code == 429 and attempt < max_retries:
            wait_s = _parse_retry_after_seconds(resp, attempt)
            logger.info(
                "Rate limited (429) on %s (attempt %d/%d); sleeping %.1fs",
                model_id,
                attempt + 1,
                max_retries,
                wait_s,
            )
            await asyncio.sleep(wait_s)
            continue
        break

    assert resp is not None
    if resp.status_code != 200:
        raise RuntimeError(
            f"LLM API returned HTTP {resp.status_code} for model '{model_id}': {resp.text[:200]}"
        )

    data = resp.json()
    reply_text = str(data["choices"][0]["message"]["content"]).strip()
    model_used = str(data.get("model") or model_id)
    return reply_text, model_used, elapsed_ms


async def evaluate_model_on_sa32(
    profile: CandidateModelProfile,
    dataset: list[dict[str, Any]],
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    limit: int | None = None,
) -> ModelBenchmarkSummary:
    """Evaluate one candidate model on SA-32 questions using real live API calls."""
    cost_1k = round(
        (350 * 1000 / 1_000_000) * profile.input_cost_per_m
        + (250 * 1000 / 1_000_000) * profile.output_cost_per_m,
        4,
    )

    effective_key = api_key if api_key is not None else settings.LLM_API_KEY
    if not effective_key.strip():
        return ModelBenchmarkSummary(
            model_id=profile.model_id,
            display_name=profile.display_name,
            provider=profile.provider,
            parameter_size=profile.parameter_size,
            total_evaluated=0,
            status="PENDING_LIVE_RUN",
            overall_score_100=None,
            french_score_100=None,
            fusha_score_100=None,
            derja_arabic_score_100=None,
            arabizi_score_100=None,
            trap_score_100=None,
            latency_p50_ms=None,
            latency_p95_ms=None,
            latency_mean_ms=None,
            cost_per_1k_queries_usd=cost_1k,
            hitl_violations=0,
            hallucination_trap_failures=0,
            selection_verdict=profile.recommended_role,
        )

    items_to_eval = select_stratified_items(dataset, limit)
    scores: list[dict[str, Any]] = []
    latencies: list[int] = []

    for item in items_to_eval:
        query = str(item["query"])
        lang_code, dialect = detect_dialect(query)
        intent, _, _, _ = classify_intent_and_subagent(query, is_authenticated=False)
        req = ChatMessageRequest(message=query)
        messages = _build_messages(req, lang_code, dialect, intent)

        try:
            reply_text, _, lat_ms = await call_candidate_llm_api(
                model_id=profile.model_id,
                messages=messages,
                client=client,
                base_url=base_url,
                api_key=api_key,
            )
        except Exception as exc:
            logger.warning(
                "Error evaluating model %s on item %s: %s",
                profile.model_id,
                item.get("id"),
                exc,
            )
            return ModelBenchmarkSummary(
                model_id=profile.model_id,
                display_name=profile.display_name,
                provider=profile.provider,
                parameter_size=profile.parameter_size,
                total_evaluated=len(scores),
                status="FAILED",
                overall_score_100=None,
                french_score_100=None,
                fusha_score_100=None,
                derja_arabic_score_100=None,
                arabizi_score_100=None,
                trap_score_100=None,
                latency_p50_ms=None,
                latency_p95_ms=None,
                latency_mean_ms=None,
                cost_per_1k_queries_usd=cost_1k,
                hitl_violations=0,
                hallucination_trap_failures=0,
                selection_verdict=profile.recommended_role,
                error_message=str(exc),
            )

        latencies.append(lat_ms)
        sc = score_response(item, reply_text, latency_ms=lat_ms)
        scores.append(sc)

    groups = {
        "french": [s for s in scores if s["language_group"] == "french"],
        "fusha": [s for s in scores if s["language_group"] == "fusha"],
        "derja_arabic": [s for s in scores if s["language_group"] == "derja_arabic"],
        "arabizi": [s for s in scores if s["language_group"] == "arabizi"],
        "traps": [s for s in scores if s["language_group"].startswith("trap_")],
    }

    group_summaries: list[ModelGroupMetrics] = []
    for g_name, g_items in groups.items():
        if not g_items:
            continue
        g_scores = [x["total_100"] for x in g_items]
        g_lats = [x["latency_ms"] for x in g_items]
        lang_hits = sum(1 for x in g_items if x["language_alignment_25"] == 25)
        fact_sum = sum(x["factual_grounding_35"] for x in g_items)
        hall_hits = sum(1 for x in g_items if x["hallucination_resistance_25"] == 25)
        hitl_hits = sum(1 for x in g_items if not x["hitl_violated"])

        group_summaries.append(
            ModelGroupMetrics(
                language_group=g_name,
                total_queries=len(g_items),
                mean_score=round(statistics.mean(g_scores), 2),
                lang_alignment_pct=round(100.0 * lang_hits / len(g_items), 1),
                factual_accuracy_pct=round(100.0 * fact_sum / (35.0 * len(g_items)), 1),
                hallucination_resistance_pct=round(100.0 * hall_hits / len(g_items), 1),
                hitl_compliance_pct=round(100.0 * hitl_hits / len(g_items), 1),
                latency_mean_ms=int(statistics.mean(g_lats)),
            )
        )

    overall_score = round(statistics.mean(s["total_100"] for s in scores), 2)
    fr_score = (
        round(statistics.mean(s["total_100"] for s in groups["french"]), 2)
        if groups["french"]
        else 0.0
    )
    ar_score = (
        round(statistics.mean(s["total_100"] for s in groups["fusha"]), 2)
        if groups["fusha"]
        else 0.0
    )
    tn_score = (
        round(statistics.mean(s["total_100"] for s in groups["derja_arabic"]), 2)
        if groups["derja_arabic"]
        else 0.0
    )
    ab_score = (
        round(statistics.mean(s["total_100"] for s in groups["arabizi"]), 2)
        if groups["arabizi"]
        else 0.0
    )
    tr_score = (
        round(statistics.mean(s["total_100"] for s in groups["traps"]), 2)
        if groups["traps"]
        else 0.0
    )

    sorted_lat = sorted(latencies)
    p50_lat = int(statistics.median(latencies))
    p95_idx = min(len(sorted_lat) - 1, int(0.95 * len(sorted_lat)))
    p95_lat = int(sorted_lat[p95_idx])
    mean_lat = int(statistics.mean(latencies))

    hitl_viol_count = sum(1 for s in scores if s["hitl_violated"])
    trap_fail_count = sum(1 for s in scores if s.get("trap_failed", False))

    return ModelBenchmarkSummary(
        model_id=profile.model_id,
        display_name=profile.display_name,
        provider=profile.provider,
        parameter_size=profile.parameter_size,
        total_evaluated=len(scores),
        status="COMPLETED",
        overall_score_100=overall_score,
        french_score_100=fr_score,
        fusha_score_100=ar_score,
        derja_arabic_score_100=tn_score,
        arabizi_score_100=ab_score,
        trap_score_100=tr_score,
        latency_p50_ms=p50_lat,
        latency_p95_ms=p95_lat,
        latency_mean_ms=mean_lat,
        cost_per_1k_queries_usd=cost_1k,
        hitl_violations=hitl_viol_count,
        hallucination_trap_failures=trap_fail_count,
        group_metrics=group_summaries,
        selection_verdict=profile.recommended_role,
    )


def generate_markdown_comparison_table(models: list[ModelBenchmarkSummary]) -> str:
    """Build the official comparative Markdown table required by SA-33 acceptance criteria."""
    col_headers = (
        "| Modèle Candidat | Rôle Attribué | Statut | Note Globale (/100) | FR | Fusha | "
        "Derja | Arabizi | Pièges | Latence p50 | Latence p95 | Coût / 1k | "
        "Décision SA-36 |"
    )
    col_sep = (
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | "
        ":---: | :---: | :---: | :--- |"
    )
    lines: list[str] = [col_headers, col_sep]

    for m in models:
        role_short = m.selection_verdict.split("(")[0].strip()
        if "VISION" in m.selection_verdict:
            verdict_badge = "**Retenu (VISION)**"
        elif "JARVIS" in m.selection_verdict:
            verdict_badge = "**Retenu (JARVIS)**"
        elif "VLM" in m.selection_verdict:
            verdict_badge = "**Retenu (VLM)**"
        else:
            verdict_badge = "Écarté / Secondaire"

        if m.status == "COMPLETED" and m.overall_score_100 is not None:
            score_str = f"**{m.overall_score_100:.1f}**"
            fr_str = f"{m.french_score_100:.1f}" if m.french_score_100 is not None else "-"
            ar_str = f"{m.fusha_score_100:.1f}" if m.fusha_score_100 is not None else "-"
            tn_str = (
                f"{m.derja_arabic_score_100:.1f}"
                if m.derja_arabic_score_100 is not None
                else "-"
            )
            ab_str = f"{m.arabizi_score_100:.1f}" if m.arabizi_score_100 is not None else "-"
            tr_str = f"{m.trap_score_100:.1f}" if m.trap_score_100 is not None else "-"
            p50_s = f"{m.latency_p50_ms / 1000.0:.2f} s" if m.latency_p50_ms is not None else "-"
            p95_s = f"{m.latency_p95_ms / 1000.0:.2f} s" if m.latency_p95_ms is not None else "-"
            status_badge = "✅ Mesuré"
        else:
            status_badge = "⏳ En attente API key"
            score_str = "En attente"
            fr_str = "-"
            ar_str = "-"
            tn_str = "-"
            ab_str = "-"
            tr_str = "-"
            target_lat = getattr(m, "target_latency_p50_s", 2.0)
            p50_s = f"Cible < {target_lat:.1f} s"
            p95_s = "-"

        cost_s = f"${m.cost_per_1k_queries_usd:.4f}"

        row = (
            f"| `{m.model_id}` ({m.display_name}) | {role_short} | {status_badge} | "
            f"{score_str} | {fr_str} | {ar_str} | {tn_str} | {ab_str} | {tr_str} | "
            f"{p50_s} | {p95_s} | {cost_s} | {verdict_badge} |"
        )
        lines.append(row)
    return "\n".join(lines)


async def run_full_benchmark(
    candidates: list[CandidateModelProfile] | None = None,
    dataset: list[dict[str, Any]] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    limit: int | None = None,
) -> BenchmarkComparisonReport:
    """Execute the asynchronous SA-33 benchmarking suite across all candidate LLMs."""
    if candidates is None:
        candidates = CANDIDATE_MODELS
    if dataset is None:
        dataset = load_sa32_dataset()

    effective_key = api_key if api_key is not None else settings.LLM_API_KEY
    live_executed = bool(effective_key.strip())

    summaries: list[ModelBenchmarkSummary] = []
    for cand in candidates:
        summary = await evaluate_model_on_sa32(
            cand,
            dataset,
            client=client,
            base_url=base_url,
            api_key=api_key,
            limit=limit,
        )
        summaries.append(summary)

    completed = [
        s for s in summaries if s.status == "COMPLETED" and s.overall_score_100 is not None
    ]
    uncompleted = [s for s in summaries if s not in completed]
    completed.sort(key=lambda s: s.overall_score_100 or 0.0, reverse=True)

    ranked_list = completed + uncompleted
    for idx, s in enumerate(ranked_list, start=1):
        s.rank = idx

    table_md = generate_markdown_comparison_table(ranked_list)

    return BenchmarkComparisonReport(
        dataset_total_cases=len(dataset),
        timestamp_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        provider_base_url=base_url or settings.LLM_BASE_URL,
        live_run_executed=live_executed,
        models=ranked_list,
        recommended_vision_model="openai/gpt-oss-120b",
        recommended_jarvis_model="openai/gpt-oss-20b",
        markdown_comparison_table=table_md,
    )
