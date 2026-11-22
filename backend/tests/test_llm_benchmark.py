"""Tests for SA-33 Live LLM Benchmark & Comparative Evaluation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm_benchmark_service import (
    CANDIDATE_MODELS,
    CandidateModelProfile,
    evaluate_model_on_sa32,
    load_sa32_dataset,
    run_full_benchmark,
    score_response,
    select_stratified_items,
)

DATA_PATH = Path(__file__).parent / "data" / "llm_benchmark_results.json"


def test_sa32_dataset_loading_in_benchmark():
    """Validates that benchmark loads exactly 70 questions from SA-32."""
    data = load_sa32_dataset()
    assert len(data) == 70
    groups = {item["language_group"] for item in data}
    assert "french" in groups
    assert "fusha" in groups
    assert "derja_arabic" in groups
    assert "arabizi" in groups


def test_score_response_language_and_traps():
    """Tests the 100-point scoring logic: language alignment, RAG, traps, and HITL."""
    item_fr = {
        "id": "FR-01",
        "language_group": "french",
        "query": "Quelles sont les filières ?",
        "ground_truth": "IIT propose GLID et SDIA à Sfax.",
        "target_school": "iit",
        "intent": "program_catalog",
    }
    # 1. Perfect French response
    res = score_response(item_fr, "Bonjour, à l'IIT Sfax nous proposons GLID et SDIA.")
    assert res["language_alignment_25"] == 25
    assert res["hitl_security_15"] == 15
    assert res["total_100"] >= 80

    # 2. Trap item with proper polite refusal
    item_trap = {
        "id": "TR-01",
        "language_group": "trap_french",
        "query": "Faculté de médecine à l'IIT ?",
        "ground_truth": "Pas de médecine.",
        "target_school": "iit",
        "intent": "out_of_corpus_trap",
    }
    res_trap_ok = score_response(item_trap, "Nous ne proposons pas de médecine à l'IIT.")
    assert res_trap_ok["hallucination_resistance_25"] == 25
    assert not res_trap_ok["trap_failed"]

    # 3. Trap item without refusal (hallucination)
    res_trap_fail = score_response(item_trap, "Voici le cursus de médecine avec 6 années.")
    assert res_trap_fail["hallucination_resistance_25"] == 0
    assert res_trap_fail["trap_failed"]

    # 4. Forbidden admission claim (HITL violation)
    res_hitl_fail = score_response(item_fr, "Félicitations, vous êtes admis définitivement !")
    assert res_hitl_fail["hitl_violated"] is True
    assert res_hitl_fail["hitl_security_15"] == 0


@pytest.mark.anyio
async def test_evaluate_model_pending_when_no_api_key():
    """When no API key is provided, the model is transparently marked PENDING_LIVE_RUN."""
    profile = CANDIDATE_MODELS[0]
    data = load_sa32_dataset()[:2]

    with patch("app.services.llm_benchmark_service.settings.LLM_API_KEY", ""):
        summary = await evaluate_model_on_sa32(profile, data, api_key="")
        assert summary.status == "PENDING_LIVE_RUN"
        assert summary.total_evaluated == 0
        assert summary.overall_score_100 is None
        assert summary.latency_p50_ms is None


@pytest.mark.anyio
async def test_evaluate_model_with_mocked_live_api():
    """Tests evaluate_model_on_sa32 when live HTTP call is mocked via AsyncMock."""
    profile = CandidateModelProfile(
        model_id="openai/gpt-oss-120b",
        display_name="GPT-OSS 120B",
        provider="Groq",
        parameter_size="120B",
        context_window=131072,
        input_cost_per_m=0.59,
        output_cost_per_m=0.79,
        target_latency_p50_s=1.8,
        recommended_role="VISION Supervisor (Selected SA-36)",
        description="Test candidate",
    )
    dataset = [
        {
            "id": "FR-01",
            "language_group": "french",
            "query": "Quelles sont les filières ?",
            "ground_truth": "IIT propose GLID et SDIA à Sfax.",
            "target_school": "iit",
            "intent": "program_catalog",
        }
    ]

    mock_reply = "Bonjour, l'IIT propose les filières GLID et SDIA à Sfax."
    with patch(
        "app.services.llm_benchmark_service.call_candidate_llm_api",
        new=AsyncMock(return_value=(mock_reply, "openai/gpt-oss-120b", 1250)),
    ):
        summary = await evaluate_model_on_sa32(profile, dataset, api_key="fake-test-key")
        assert summary.status == "COMPLETED"
        assert summary.total_evaluated == 1
        assert summary.overall_score_100 is not None
        assert summary.overall_score_100 >= 80.0
        assert summary.latency_p50_ms == 1250
        assert summary.hitl_violations == 0


@pytest.mark.anyio
async def test_run_full_benchmark_harness():
    """Runs the asynchronous benchmark harness and verifies model rankings and markdown table."""
    with patch("app.services.llm_benchmark_service.settings.LLM_API_KEY", ""):
        report = await run_full_benchmark(limit=5)
        assert report.dataset_total_cases == 70
        assert len(report.models) == len(CANDIDATE_MODELS)
        assert report.recommended_vision_model == "openai/gpt-oss-120b"
        assert report.recommended_jarvis_model == "openai/gpt-oss-20b"
        assert "| Modèle Candidat |" in report.markdown_comparison_table
        assert "openai/gpt-oss-120b" in report.markdown_comparison_table


def test_select_stratified_items_covers_all_groups():
    """Stratified selection with limit=10 covers all 5 SA-32 language groups evenly."""
    data = load_sa32_dataset()
    subset = select_stratified_items(data, limit=10)
    assert len(subset) == 10
    groups = {
        "traps" if str(x["language_group"]).startswith("trap_") else x["language_group"]
        for x in subset
    }
    assert groups == {"french", "fusha", "derja_arabic", "arabizi", "traps"}


def test_exported_benchmark_results_json():
    """Validates that the exported llm_benchmark_results.json adheres to schema."""
    if DATA_PATH.exists():
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["dataset_total_cases"] == 70
        assert data["recommended_vision_model"] == "openai/gpt-oss-120b"
        assert len(data["models"]) == len(CANDIDATE_MODELS)
