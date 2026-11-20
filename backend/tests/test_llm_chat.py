"""Unit and endpoint tests for SA-36 LLM backend connection and VISION dialogue routing."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import create_access_token


def test_chat_llm_health_endpoint(client: TestClient):
    resp = client.get("/api/v1/chat/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] in {"openai_compatible", "ollama", "mock"}
    assert "model" in data
    assert data["status"] in {"ready", "fallback_only", "misconfigured"}


def test_chat_message_arabizi_and_derja_detection_with_safe_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", True)

    # 1. Arabizi query about IIT specialties
    r_arabizi = client.post(
        "/api/v1/chat/message",
        headers={"X-Session-ID": "guest-sess-01"},
        json={"message": "3aslema, chnoma les spécialités mte3 cycle ingénieur fi IIT ?"},
    )
    assert r_arabizi.status_code == 200
    b1 = r_arabizi.json()
    assert b1["session_id"] == "guest-sess-01"
    assert b1["sessionId"] == "guest-sess-01"
    assert b1["detected_language"] == "ar-TN-arabizi"
    assert b1["detectedDialect"] == "Arabizi"
    assert b1["intent"] == "INQUIRE_PROGRAMS"
    assert b1["sub_agent"] == "1.2_Librarian"
    assert b1["used_fallback"] is True
    assert b1["reply"] == b1["reply_text"]

    # 2. Tunisian Derja in Arabic script
    r_derja = client.post(
        "/api/v1/chat/message",
        json={"message": "شنوة الاختصاصات الي في IIT ؟", "sessionId": "guest-sess-02"},
    )
    assert r_derja.status_code == 200
    b2 = r_derja.json()
    assert b2["detected_language"] == "ar-TN"
    assert b2["detectedDialect"] == "Tunisian_Derja"


def test_give_or_keep_rule_prompts_auth_for_anonymous_releve_upload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", True)

    # Anonymous user asking to upload relevé -> actionPrompt = AUTH_REQUIRED
    r_anon = client.post(
        "/api/v1/chat/message",
        json={"message": "Je veux téléverser mon relevé de notes pour la préinscription."},
    )
    assert r_anon.status_code == 200
    assert r_anon.json()["actionPrompt"] == "AUTH_REQUIRED"
    assert r_anon.json()["sub_agent"] == "1.6_Registrar"

    # Authenticated student asking the same -> actionPrompt = UPLOAD_RELEVE
    student_jwt = create_access_token(
        account_id="acc-123",
        role="student",
        candidate_id="cand-456",
    )
    r_auth = client.post(
        "/api/v1/chat/message",
        headers={"Authorization": f"Bearer {student_jwt}"},
        json={"message": "Je veux téléverser mon relevé de notes pour la préinscription."},
    )
    assert r_auth.status_code == 200
    assert r_auth.json()["actionPrompt"] == "UPLOAD_RELEVE"
    assert r_auth.json()["session_id"] == "cand-456"


def test_openai_compatible_llm_provider_success_and_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-api-key-123")
    monkeypatch.setattr(settings, "LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")

    mock_http_response = MagicMock()
    mock_http_response.status_code = 200
    mock_http_response.json.return_value = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        "Selon le catalogue officiel IIT, le Cycle Ingénieur propose "
                        "Génie Logiciel (GLID), Réseaux & Sécurité (ARSI) et Data Science (DSIA)."
                    ),
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_http_response)) as m_post:
        resp = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Quelles sont les spécialités ingénieur à l'IIT ?",
                "history": [{"role": "user", "content": "Bonjour VISION"}],
                "context_chunks": [
                    {
                        "id": "chunk-1",
                        "source_document": "Brochure_IIT_2026.pdf",
                        "school": "IIT",
                        "program": "Cycle Ingénieur",
                        "page": 4,
                        "snippet": "Spécialités : GLID, ARSI, DSIA.",
                    }
                ],
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["used_fallback"] is False
    assert body["model_used"] == "Qwen/Qwen2.5-7B-Instruct"
    assert "GLID" in body["reply_text"]
    assert len(body["citations"]) == 1
    assert body["citations"][0]["source_document"] == "Brochure_IIT_2026.pdf"

    # Verify system prompt with HITL & zero-hallucination rules was sent to LLM provider
    called_kwargs = m_post.call_args.kwargs
    sent_messages = called_kwargs["json"]["messages"]
    assert sent_messages[0]["role"] == "system"
    assert "Zéro hallucination" in sent_messages[0]["content"]
    assert "Brochure_IIT_2026.pdf" in sent_messages[0]["content"]


def test_llm_provider_timeout_and_upstream_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-api-key-123")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", False)

    # 1. Timeout -> 504 Gateway Timeout
    with patch(
        "httpx.AsyncClient.post",
        new=AsyncMock(side_effect=httpx.ReadTimeout("LLM timed out")),
    ):
        r_timeout = client.post(
            "/api/v1/chat/message",
            json={"message": "Bonjour"},
        )
    assert r_timeout.status_code == 504

    # 2. Upstream HTTP 500 -> 502 Bad Gateway
    bad_resp = MagicMock()
    bad_resp.status_code = 500
    bad_resp.text = "Internal Server Error"
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=bad_resp)):
        r_bad = client.post(
            "/api/v1/chat/message",
            json={"message": "Bonjour"},
        )
    assert r_bad.status_code == 502

    # 3. Missing API key with fallback disabled -> 503 Service Unavailable
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    r_unconf = client.post(
        "/api/v1/chat/message",
        json={"message": "Bonjour"},
    )
    assert r_unconf.status_code == 503

