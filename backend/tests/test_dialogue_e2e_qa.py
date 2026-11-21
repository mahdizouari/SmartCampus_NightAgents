"""QA cross-testing for VISION dialogue, LLM memory, and guardrails (SA-38).

End-to-end verification covering:
1. Multi-turn dialogue flows (guest and authenticated).
2. Guest-to-authenticated session transition and state merge.
3. Redis conversation memory sliding window (max 16 messages / 7 days TTL).
4. Guardrails: Zero-hallucination out-of-corpus traps, HITL governance, INPDP privacy.
5. Multilingual comprehension across French, Tunisian Derja, and Arabizi.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import create_access_token
from app.core.rate_limit import clear_rate_limit_store_for_tests
from app.schemas.session import MessageTurn
from app.services.redis_service import GuestSessionService, HotConversationService


@pytest.fixture(autouse=True)
def _setup_test_env(monkeypatch: pytest.MonkeyPatch):
    """Ensure tests run deterministically in mock fallback mode without external API keys."""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_TO_MOCK", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    clear_rate_limit_store_for_tests()
    yield
    clear_rate_limit_store_for_tests()


def test_multiturn_guest_dialogue_flow(client: TestClient):
    """Verify multi-turn orientation conversation as an anonymous guest with session tracking."""
    session_id = "guest-qa-e2e-001"

    # Turn 1: Initial greeting and general program inquiry in French
    r1 = client.post(
        "/api/v1/chat/message",
        headers={"X-Session-ID": session_id},
        json={"message": "Bonjour, quelles sont les formations d'ingénieur proposées à l'IIT ?"},
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["session_id"] == session_id
    assert b1["detected_language"] == "fr"
    assert b1["detectedDialect"] == "French"
    assert b1["intent"] == "INQUIRE_PROGRAMS"
    assert b1["sub_agent"] == "1.2_Librarian"
    assert b1["used_fallback"] is True
    assert "IIT" in b1["reply_text"] or "ingénieur" in b1["reply_text"].lower()

    # Turn 2: Follow-up question on tuition fees passing conversational context
    history_turn = [
        {
            "role": "user",
            "content": "Quelles sont les formations d'ingénieur proposées à l'IIT ?",
        },
        {"role": "assistant", "content": b1["reply_text"]},
    ]
    r2 = client.post(
        "/api/v1/chat/message",
        headers={"X-Session-ID": session_id},
        json={
            "message": "Et quels sont les frais de scolarité pour le cycle ingénieur ?",
            "history": history_turn,
        },
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["session_id"] == session_id
    assert b2["intent"] == "INQUIRE_PROGRAMS"
    assert b2["sub_agent"] == "1.2_Librarian"


def test_guest_to_authenticated_merge_flow(client: TestClient):
    """Verify session transition: guest orientation prompts auth before document upload."""
    guest_sid = "guest-qa-merge-042"

    # Step 1: Anonymous student asks to upload transcript
    r_guest = client.post(
        "/api/v1/chat/message",
        headers={"X-Session-ID": guest_sid},
        json={"message": "Je souhaite déposer mon relevé de notes pour mon admission."},
    )
    assert r_guest.status_code == 200
    bg = r_guest.json()
    assert bg["session_id"] == guest_sid
    # Guardrail rule: anonymous uploads require authentication
    assert bg["actionPrompt"] == "AUTH_REQUIRED"
    assert bg["sub_agent"] == "1.6_Registrar"
    assert "connexion" in bg["reply_text"].lower() or "compte" in bg["reply_text"].lower()

    # Step 2: Student registers and acquires valid JWT Bearer token
    candidate_id = "cand-uuid-qa-999"
    token = create_access_token(
        account_id="acc-uuid-qa-999",
        role="student",
        candidate_id=candidate_id,
        email="melek.qa@smartcampus.tn",
    )

    # Step 3: Same request sent with Bearer JWT token -> actionPrompt switches to UPLOAD_RELEVE
    r_auth = client.post(
        "/api/v1/chat/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Je souhaite déposer mon relevé de notes pour mon admission."},
    )
    assert r_auth.status_code == 200
    ba = r_auth.json()
    assert ba["session_id"] == candidate_id
    assert ba["actionPrompt"] == "UPLOAD_RELEVE"
    assert ba["sub_agent"] == "1.6_Registrar"


@pytest.mark.asyncio
async def test_redis_hot_conversation_memory_window():
    """Verify Redis conversation memory sliding window retains chronological turns up to limit."""
    mock_redis = MagicMock()
    mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    # Simulate 4 sequential turns stored in Redis
    raw_payloads = [
        MessageTurn(role="user", content="Q1", timestamp=100.0).model_dump_json(),
        MessageTurn(role="assistant", content="R1", timestamp=101.0).model_dump_json(),
        MessageTurn(role="user", content="Q2", timestamp=102.0).model_dump_json(),
        MessageTurn(role="assistant", content="R2", timestamp=103.0).model_dump_json(),
    ]
    # In Redis LPUSH, index 0 is the newest message
    mock_redis.lrange = AsyncMock(return_value=list(reversed(raw_payloads)))
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.ttl = AsyncMock(return_value=604800)

    svc = HotConversationService(redis_client=mock_redis, max_messages=16)

    # Retrieve history and ensure chronological order (Q1 -> R1 -> Q2 -> R2)
    history = await svc.get_history("test-sess-qa")
    assert len(history) == 4
    assert history[0].content == "Q1"
    assert history[1].content == "R1"
    assert history[2].content == "Q2"
    assert history[3].content == "R2"

    ttl = await svc.get_ttl("test-sess-qa")
    assert ttl == 604800

    cleared = await svc.clear_history("test-sess-qa")
    assert cleared is True


@pytest.mark.asyncio
async def test_redis_guest_session_state_migration():
    """Verify atomic state migration from guest session to authenticated user session."""
    mock_redis = MagicMock()
    pipe_mock = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.delete = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[True, True, True])
    mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe_mock)
    mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    guest_svc = GuestSessionService(redis_client=mock_redis)
    guest_svc.get_session = AsyncMock(
        return_value={
            "step": "ORIENTATION",
            "school_target": "iit",
            "preferred_degree": "cycle_ingenieur",
        }
    )

    migrated = await guest_svc.migrate_session(
        guest_id="guest-temp-777",
        new_user_id="user-auth-888",
    )
    assert migrated is not None
    assert migrated["migrated_from_guest"] == "guest-temp-777"
    assert migrated["school_target"] == "iit"
    pipe_mock.delete.assert_called_with("guest:session:guest-temp-777")


def test_guardrail_zero_hallucination_out_of_corpus(client: TestClient):
    """Verify strict rejection of out-of-corpus queries (medicine, law, pharmacy, guarantees)."""
    # 1. Inquiry about non-existent medical degree
    r_med = client.post(
        "/api/v1/chat/message",
        json={"message": "Est-ce que je peux m'inscrire en faculté de médecine à l'IIT ?"},
    )
    assert r_med.status_code == 200
    b_med = r_med.json()
    assert b_med["intent"] == "OUT_OF_CORPUS_TRAP"
    assert b_med["sub_agent"] == "1.1_Listener"
    assert "ne relève pas" in b_med["reply_text"].lower() or "officielle" in b_med["reply_text"]

    # 2. Inquiry promising guaranteed scholarships in Arabizi
    r_trap_ab = client.post(
        "/api/v1/chat/message",
        json={"message": "fama bourse garantie 100% fel IIT ?"},
    )
    assert r_trap_ab.status_code == 200
    assert r_trap_ab.json()["intent"] == "OUT_OF_CORPUS_TRAP"


def test_guardrail_human_in_the_loop_admission_governance(client: TestClient):
    """Verify that VISION reminds students that only human juries decide admissions."""
    r_hitl = client.post(
        "/api/v1/chat/message",
        json={"message": "Est-ce que l'intelligence artificielle peut accepter mon dossier ?"},
    )
    assert r_hitl.status_code == 200
    b_hitl = r_hitl.json()
    assert b_hitl["intent"] == "ADMISSION_GOVERNANCE"
    assert b_hitl["sub_agent"] == "1.1_Listener"
    assert "humain" in b_hitl["reply_text"].lower() or "jury" in b_hitl["reply_text"].lower()


def test_guardrail_inpdp_privacy_compliance(client: TestClient):
    """Verify user privacy and INPDP data protection compliance inquiries."""
    r_inpdp = client.post(
        "/api/v1/chat/message",
        json={"message": "Comment mes données personnelles sont protégées selon la loi INPDP ?"},
    )
    assert r_inpdp.status_code == 200
    b_inpdp = r_inpdp.json()
    assert b_inpdp["intent"] == "DATA_PRIVACY"
    assert b_inpdp["sub_agent"] == "1.1_Listener"
    assert "Consulter la politique INPDP" in b_inpdp["suggested_actions"]


def test_multilingual_arabizi_and_derja_comprehension(client: TestClient):
    """Verify accurate dialect detection across Arabizi, Tunisian Derja, and French."""
    # 1. Arabizi query with numeric characters (3, 7, 9)
    r_ab = client.post(
        "/api/v1/chat/message",
        json={"message": "3aslema, 9addech les frais mte3 cycle ingénieur fi IIT ?"},
    )
    assert r_ab.status_code == 200
    bab = r_ab.json()
    assert bab["detectedDialect"] == "Arabizi"
    assert bab["intent"] == "INQUIRE_PROGRAMS"
    assert bab["sub_agent"] == "1.2_Librarian"

    # 2. Tunisian Derja in Arabic script
    r_tn = client.post(
        "/api/v1/chat/message",
        json={"message": "شنوة الاختصاصات المتاحة في الهندسة ؟"},
    )
    assert r_tn.status_code == 200
    btn = r_tn.json()
    assert btn["detectedDialect"] == "Tunisian_Derja"
    assert btn["intent"] == "INQUIRE_PROGRAMS"


def test_unintelligible_input_triggers_safe_reformulation(client: TestClient):
    """Verify that vague dialect input safely triggers French reformulation fallback."""
    r_vague = client.post(
        "/api/v1/chat/message",
        json={"message": "kfh n3ml ?"},
    )
    assert r_vague.status_code == 200
    bn = r_vague.json()
    assert bn["needs_reformulation"] is True
    assert bn["comprehension_confidence"] < 0.45
    assert "reformuler" in bn["reply_text"].lower() or "français" in bn["reply_text"].lower()
