"""Automated tests for SA-34 Tunisian Derja, Arabizi & Code-Switching Evaluation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services.dialect_eval_service import (
    SA34_STRESS_PATH,
    evaluate_live_llm,
    evaluate_pipeline,
    score_live_llm_reply,
)

STRESS_PATH = Path(__file__).parent / "data" / "dialect_stress_cases.json"


def test_dialect_stress_dataset_structure():
    """Verify the 15-item FR/TN code-switching and ambiguous fallback stress dataset."""
    assert STRESS_PATH.exists(), f"Missing stress dataset at {STRESS_PATH}"
    data = json.loads(STRESS_PATH.read_text(encoding="utf-8"))
    assert len(data) == 15

    cs_items = [q for q in data if q["language_group"] == "code_switching_fr_tn"]
    amb_items = [q for q in data if q["language_group"] == "ambiguous_low_confidence"]
    assert len(cs_items) == 10
    assert len(amb_items) == 5
    assert all(q["expected_fallback"] is False for q in cs_items)
    assert all(q["expected_fallback"] is True for q in amb_items)


def test_sa34_before_and_after_pipeline_evaluation_metrics():
    """Verify measurable improvement from SA-36 baseline to SA-34 hardened dialect pipeline."""
    report = evaluate_pipeline(stress_path=SA34_STRESS_PATH)
    assert report.total_cases == 85
    assert report.sa32_cases == 70
    assert report.stress_cases == 15

    # Baseline SA-36 had documented failure cases across Fusha, Derja, Arabizi, Traps, Fallback
    assert report.baseline_failure_count >= 40
    baseline_by_group = {g.language_group: g for g in report.baseline_by_group}
    assert baseline_by_group["fusha"].lang_accuracy_pct == 0.0
    assert baseline_by_group["derja_arabic"].intent_accuracy_pct == 40.0
    assert baseline_by_group["arabizi"].lang_accuracy_pct < 100.0
    assert baseline_by_group["ambiguous_low_confidence"].intent_accuracy_pct == 0.0

    # Post-tuning SA-34 achieves 100% dialect register, intent routing, and fallback accuracy
    assert report.tuned_overall_lang_pct == 100.0
    assert report.tuned_overall_intent_pct == 100.0
    assert report.tuned_overall_fallback_pct == 100.0
    assert report.tuned_failure_count == 0


def test_biscript_arabizi_fusha_and_code_switching_via_chat_endpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify /api/v1/chat/message distinguishes Fusha, Derja, Arabizi, and bi-script mixes."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", True)

    # 1. AB-10 bi-script Arabizi with stray Arabic word -> stays ar-TN-arabizi / Arabizi
    r_ab10 = client.post(
        "/api/v1/chat/message",
        json={"message": "win mawjouda el fac بالضبط ? fel 3asma walla fi Sfax ?"},
    )
    assert r_ab10.status_code == 200
    b_ab10 = r_ab10.json()
    assert b_ab10["detected_language"] == "ar-TN-arabizi"
    assert b_ab10["detectedDialect"] == "Arabizi"
    assert b_ab10["intent"] == "INQUIRE_PROGRAMS"
    assert b_ab10["needs_reformulation"] is False

    # 2. AR-01 Classical Arabic (Fusha) -> detected_language == "ar"
    ar01_query = (
        "ما هي الاختصاصات الهندسية المتوفرة في معهد تكنولوجيا الهندسة الدولي (IIT)؟"
    )
    r_ar01 = client.post(
        "/api/v1/chat/message",
        json={"message": ar01_query},
    )
    assert r_ar01.status_code == 200
    b_ar01 = r_ar01.json()
    assert b_ar01["detected_language"] == "ar"
    assert b_ar01["intent"] == "INQUIRE_PROGRAMS"
    assert b_ar01["needs_reformulation"] is False

    # 3. TN-13 Tunisian Derja appointment scheduling -> 1.6_Registrar + AUTH_REQUIRED
    r_tn13 = client.post(
        "/api/v1/chat/message",
        json={"message": "كيفاش نجم نبرمج رونديفو مع موجه بش نتفاهمو في الترسيم ؟"},
    )
    assert r_tn13.status_code == 200
    b_tn13 = r_tn13.json()
    assert b_tn13["detected_language"] == "ar-TN"
    assert b_tn13["intent"] == "INTERVIEW_SCHEDULING"
    assert b_tn13["sub_agent"] == "1.6_Registrar"
    assert b_tn13["actionPrompt"] == "AUTH_REQUIRED"

    # 4. CS-08 Code-switching HITL governance -> ADMISSION_GOVERNANCE (never admits)
    cs08_query = (
        "Est-ce que l'IA mte3kom tnajem ta3tini acceptation finale "
        "walla el décision 3and le jury humain ?"
    )
    r_cs08 = client.post("/api/v1/chat/message", json={"message": cs08_query})
    assert r_cs08.status_code == 200
    b_cs08 = r_cs08.json()
    assert b_cs08["intent"] == "ADMISSION_GOVERNANCE"
    assert "humaine" in b_cs08["reply_text"].lower() or "humain" in b_cs08["reply_text"].lower()

    # 5. TR-01 Out-of-corpus trap (Medicine) -> OUT_OF_CORPUS_TRAP guardrail refusal
    tr01_query = (
        "Pouvez-vous me donner les frais d'inscription et le programme "
        "de la Faculté de Médecine de l'IIT ?"
    )
    r_tr01 = client.post("/api/v1/chat/message", json={"message": tr01_query})
    assert r_tr01.status_code == 200
    b_tr01 = r_tr01.json()
    assert b_tr01["intent"] == "OUT_OF_CORPUS_TRAP"
    assert "ne relève pas" in b_tr01["reply_text"].lower()


@pytest.mark.parametrize(
    "ambiguous_query",
    [
        "slm brbi 3tini hedhi mte3 lfac",
        "kfh n3ml hedha w 9dh",
        "?? brbi 3awenni f heki mte3 l3am jey",
        "عسلامة بربي هكا متاع القراية شنوة نعمل فيه",
        "3slm x9z2 ???",
    ],
)
def test_low_confidence_dialect_triggers_french_reformulation_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    ambiguous_query: str,
):
    """Verify SA-34 fallback requests polite French reformulation when comprehension is low."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "dummy-key-should-short-circuit")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", False)

    resp = client.post("/api/v1/chat/message", json={"message": ambiguous_query})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "CLARIFICATION_REQUIRED"
    assert body["needs_reformulation"] is True
    assert body["comprehension_confidence"] < 0.45
    assert body["reformulation_prompt"] is not None
    assert "français" in body["reply_text"].lower()
    assert body["model_used"] == "smartcampus-clarification-fallback-v1"


@pytest.mark.asyncio
async def test_live_llm_100_point_rubric_scorer_and_mocked_runner(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify 100-point rubric penalizes Fusha-on-Arabizi and scores mocked live LLM runs."""
    ab_item = {
        "id": "AB-01",
        "language_group": "arabizi",
        "query": "chnoma les branches d'ingénieurs eli 3andkom fel IIT ?",
        "intent": "program_catalog",
        "target_school": "iit",
        "ground_truth": "fel IIT 3anna 7 filières ingénieurs: GLID, SDIA, ARSI...",
    }
    # Strict 0/25 language penalty if model answers in Fusha to an Arabizi question
    bad_fusha_score = score_live_llm_reply(
        item=ab_item,
        reply_text="يقدم المعهد الدولي للتكنولوجيا (IIT) سبعة برامج وطنية معتمدة لتكوين المهندسين.",
    )
    assert bad_fusha_score["language_alignment_25"] == 0

    good_reply = (
        "Fel IIT 3anna 7 filières ingénieurs reconnues: "
        "GLID, SDIA, ARSI, Mécanique, Industriel, Énergétique w Civil."
    )
    good_derja_score = score_live_llm_reply(
        item=ab_item,
        reply_text=good_reply,
    )
    assert good_derja_score["language_alignment_25"] == 25
    assert good_derja_score["total_100"] == 100

    # Verify live-llm evaluator runner with mocked provider
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-oss-120b")

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": good_reply,
                }
            }
        ],
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_http_resp)):
        summary = await evaluate_live_llm(cases=[ab_item])

    assert summary["evaluated_count"] == 1
    assert summary["models_used"] == ["openai/gpt-oss-120b"]
    assert summary["mean_score_100"] == 100.0
    assert summary["hitl_violations"] == 0


def test_evaluate_dialect_endpoint_and_swagger_default_override(client: TestClient):
    """Verify GET /api/v1/chat/evaluate-dialect and natural script detection over default hint."""
    r_bench = client.get("/api/v1/chat/evaluate-dialect")
    assert r_bench.status_code == 200
    bench = r_bench.json()
    assert bench["total_cases"] == 85
    assert bench["baseline_failure_count"] == 49
    assert bench["tuned_failure_count"] == 0
    assert bench["tuned_overall_lang_pct"] == 100.0
    assert bench["tuned_overall_intent_pct"] == 100.0

    # Even if a client leaves detectedDialect='French' with language_hint='auto',
    # unmistakable Arabizi tokens are still detected as Arabizi.
    r_msg = client.post(
        "/api/v1/chat/message",
        json={
            "message": "slm brbi 3tini hedhi mte3 lfac",
            "language_hint": "auto",
            "detectedDialect": "French",
        },
    )
    assert r_msg.status_code == 200
    body = r_msg.json()
    assert body["detected_language"] == "ar-TN-arabizi"
    assert body["detectedDialect"] == "Arabizi"
    assert body["needs_reformulation"] is True
