"""Tests for SA-33 LLM Models Benchmark & Comparative Evaluation."""

import json
from pathlib import Path

from app.services.llm_benchmark_service import (
    CANDIDATE_MODELS,
    load_sa32_dataset,
    run_full_benchmark,
    score_response,
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


def test_full_benchmark_execution_and_model_selection():
    """Runs the benchmark across all candidate models and verifies SA-36 baseline selection."""
    report = run_full_benchmark()

    assert report.dataset_total_cases == 70
    assert len(report.models) == len(CANDIDATE_MODELS)

    # 1. Verify models are ranked by overall score descending
    scores = [m.overall_score_100 for m in report.models]
    assert scores == sorted(scores, reverse=True)

    # 2. Verify GPT-OSS 120B is ranked #1 and selected for VISION
    top_model = report.models[0]
    assert top_model.model_id == "openai/gpt-oss-120b"
    assert "VISION" in top_model.selection_verdict
    assert report.recommended_vision_model == "openai/gpt-oss-120b"
    assert top_model.overall_score_100 > 90.0

    # 3. Verify GPT-OSS 20B is selected for JARVIS
    jarvis_model = next(m for m in report.models if m.model_id == "openai/gpt-oss-20b")
    assert "JARVIS" in jarvis_model.selection_verdict
    assert report.recommended_jarvis_model == "openai/gpt-oss-20b"
    assert jarvis_model.latency_p50_ms < 1000  # Sub-second latency for async scheduler

    # 4. Verify markdown table has all models
    table = report.markdown_comparison_table
    assert "| Modèle Candidat |" in table
    assert "openai/gpt-oss-120b" in table
    assert "meta-llama/llama-3.3-70b-versatile" in table
    assert "mistralai/mixtral-8x7b-32768" in table
    assert "qwen/qwen-2.5-72b-instruct" in table


def test_exported_benchmark_results_json():
    """Validates that the exported llm_benchmark_results.json adheres to schema."""
    if DATA_PATH.exists():
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["dataset_total_cases"] == 70
        assert data["recommended_vision_model"] == "openai/gpt-oss-120b"
        assert len(data["models"]) >= 5
