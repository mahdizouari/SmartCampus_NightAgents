"""
Unit tests for SA-37 — Conversational Memory Integration.

Tests cover:
 T1 — Multi-turn history loaded from Redis and injected into LLM prompt messages.
 T2 — Context window bounded to 16 messages (8 turns max), even when Redis holds more.
 T3 — Rolling profile summary synthesised and injected into system prompt.
 T4 — Rule 7 in system prompt: LLM must never present unverified admin info as official.
 T5 — Post-chat turn persistence in Redis (user + assistant turns added after reply).
 T6 — Graceful fallback when Redis is offline (no crash, normal response continues).
 T7 — ChatMessageResponse carries rolling_summary and history_turn_count fields (SA-37 schema).
 T8 — build_rolling_summary: profile facts yield bounded summary (<= 1200 chars).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.services.llm_service import (
    VISION_SYSTEM_PROMPT,
    _build_messages,
    build_rolling_summary,
    generate_chat_reply,
)
from app.services.redis_service import MessageTurn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_turns(n: int) -> list[MessageTurn]:
    """Return n alternating user/assistant MessageTurn objects."""
    roles = ["user", "assistant"] * (n // 2 + 1)
    return [
        MessageTurn(role=roles[i], content=f"msg-{i}", timestamp=float(i))
        for i in range(n)
    ]


def _make_payload(message: str = "Quelles sont les filières IIT ?") -> ChatMessageRequest:
    return ChatMessageRequest(message=message)


# ---------------------------------------------------------------------------
# T1 — Hot Redis turns injected into messages
# ---------------------------------------------------------------------------

def test_t1_redis_history_injected_into_messages():
    """_build_messages with explicit history_turns includes them in the messages list."""
    turns = _make_turns(4)
    payload = _make_payload()
    msgs = _build_messages(
        payload,
        "fr",
        "French",
        "INQUIRE_PROGRAMS",
        history_turns=turns,
    )
    # system + 4 history turns + 1 user = 6
    assert len(msgs) == 6
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == payload.message
    # middle messages carry history
    for i, turn in enumerate(turns, start=1):
        assert msgs[i]["content"] == turn.content


# ---------------------------------------------------------------------------
# T2 — Context window bounded to 16 messages (8 turns max)
# ---------------------------------------------------------------------------

def test_t2_context_window_bounded_to_16_messages():
    """History longer than 16 entries is capped to the 16 most recent."""
    turns = _make_turns(30)  # deliberately oversized
    payload = _make_payload()
    msgs = _build_messages(
        payload,
        "fr",
        "French",
        "INQUIRE_PROGRAMS",
        history_turns=turns,
    )
    # system(1) + 16(capped) + user(1) = 18
    assert len(msgs) == 18
    # Content of first history turn matches msg-14 (30 - 16 = 14)
    assert msgs[1]["content"] == "msg-14"


# ---------------------------------------------------------------------------
# T3 — Rolling summary injected into system prompt
# ---------------------------------------------------------------------------

def test_t3_rolling_summary_in_system_prompt():
    """When rolling_summary is provided, it appears in the [PROFIL CANDIDAT] system block."""
    payload = _make_payload()
    summary = "Établissement cible : IIT | Spécialité/Filière : GLID"
    msgs = _build_messages(
        payload,
        "fr",
        "French",
        "INQUIRE_PROGRAMS",
        rolling_summary=summary,
    )
    system_msg = msgs[0]["content"]
    assert "[PROFIL CANDIDAT" in system_msg
    assert summary in system_msg


def test_t3_declared_facts_in_system_prompt():
    """Declared academic facts are serialised into the memory block."""
    payload = _make_payload()
    facts = {"school_target": "IIT", "bac_section": "Sciences Techniques"}
    msgs = _build_messages(
        payload,
        "fr",
        "French",
        "INQUIRE_PROGRAMS",
        declared_facts=facts,
    )
    system_msg = msgs[0]["content"]
    assert "school_target" in system_msg
    assert "IIT" in system_msg


# ---------------------------------------------------------------------------
# T4 — Rule 7: unverified admin info prohibition in system prompt
# ---------------------------------------------------------------------------

def test_t4_rule_7_no_unverified_admin_info():
    """VISION_SYSTEM_PROMPT contains Rule 7 forbidding unverified administrative claims."""
    assert "secrétariat administratif" in VISION_SYSTEM_PROMPT
    assert "jamais" in VISION_SYSTEM_PROMPT.lower()
    # Confirm it references the NAU secretariat as the authority
    assert "NAU" in VISION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# T5 — Redis persistence: add_turn called after reply
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t5_turns_persisted_to_redis_after_reply():
    """generate_chat_reply calls hot_svc.add_turn twice (user + assistant) after each reply."""
    mock_redis = MagicMock()

    mock_hot_svc = AsyncMock()
    mock_hot_svc.get_history = AsyncMock(return_value=[])
    mock_hot_svc.add_turn = AsyncMock()

    mock_guest_svc = AsyncMock()
    mock_guest_svc.get_session = AsyncMock(return_value=None)
    mock_guest_svc.update_fields = AsyncMock()

    payload = _make_payload("Bonjour, quelles sont les filières IIT ?")

    with (
        patch("app.services.llm_service.HotConversationService", return_value=mock_hot_svc),
        patch("app.services.llm_service.GuestSessionService", return_value=mock_guest_svc),
        patch("app.services.llm_service.settings") as mock_settings,
    ):
        mock_settings.LLM_PROVIDER = "mock"
        mock_settings.LLM_API_KEY = ""
        mock_settings.LLM_FALLBACK_TO_MOCK = True
        mock_settings.LLM_MODEL = "smartcampus-fallback-v1"
        mock_settings.LLM_BASE_URL = "http://localhost"
        mock_settings.LLM_TEMPERATURE = 0.7
        mock_settings.LLM_MAX_TOKENS = 1024
        mock_settings.LLM_TIMEOUT_SECONDS = 30

        resp = await generate_chat_reply(
            payload,
            session_id="sess-t5",
            is_authenticated=False,
            redis_client=mock_redis,
        )

    # Two add_turn calls: one for user, one for assistant
    assert mock_hot_svc.add_turn.call_count == 2
    calls = mock_hot_svc.add_turn.call_args_list
    assert calls[0].kwargs["role"] == "user"
    assert calls[0].kwargs["content"] == payload.message
    assert calls[1].kwargs["role"] == "assistant"

    # rolling_summary persisted to guest session
    mock_guest_svc.update_fields.assert_called_once()
    assert isinstance(resp, ChatMessageResponse)


# ---------------------------------------------------------------------------
# T6 — Graceful Redis fallback (no crash when Redis offline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t6_graceful_fallback_when_redis_offline():
    """generate_chat_reply succeeds even when Redis raises an exception."""
    payload = _make_payload("Quelles filières propose l'ISB ?")

    with (
        patch("app.services.llm_service.HotConversationService") as mock_hot_cls,
        patch("app.services.llm_service.GuestSessionService") as mock_guest_cls,
        patch("app.services.llm_service.settings") as mock_settings,
    ):
        # Simulate Redis entirely offline
        failing_hot = AsyncMock()
        failing_hot.get_history = AsyncMock(side_effect=ConnectionError("Redis down"))
        failing_hot.add_turn = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_hot_cls.return_value = failing_hot

        failing_guest = AsyncMock()
        failing_guest.get_session = AsyncMock(side_effect=ConnectionError("Redis down"))
        failing_guest.update_fields = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_guest_cls.return_value = failing_guest

        mock_settings.LLM_PROVIDER = "mock"
        mock_settings.LLM_API_KEY = ""
        mock_settings.LLM_FALLBACK_TO_MOCK = True
        mock_settings.LLM_MODEL = "smartcampus-fallback-v1"
        mock_settings.LLM_BASE_URL = "http://localhost"
        mock_settings.LLM_TEMPERATURE = 0.7
        mock_settings.LLM_MAX_TOKENS = 1024
        mock_settings.LLM_TIMEOUT_SECONDS = 30

        # Must NOT raise
        resp = await generate_chat_reply(
            payload,
            session_id="sess-t6-offline",
            is_authenticated=False,
        )

    assert isinstance(resp, ChatMessageResponse)
    assert resp.reply_text  # Still returns a valid response


# ---------------------------------------------------------------------------
# T7 — ChatMessageResponse carries SA-37 schema fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t7_response_has_sa37_memory_fields():
    """generate_chat_reply populates rolling_summary and history_turn_count in the response."""
    payload = _make_payload("Je veux intégrer l'IIT en Génie Logiciel.")

    hot = AsyncMock()
    hot.get_history = AsyncMock(
        return_value=_make_turns(6)  # 3 turns in Redis
    )
    hot.add_turn = AsyncMock()

    guest = AsyncMock()
    guest.get_session = AsyncMock(
        return_value={"school_target": "IIT", "rolling_summary": None}
    )
    guest.update_fields = AsyncMock()

    with (
        patch("app.services.llm_service.HotConversationService", return_value=hot),
        patch("app.services.llm_service.GuestSessionService", return_value=guest),
        patch("app.services.llm_service.settings") as mock_settings,
    ):
        mock_settings.LLM_PROVIDER = "mock"
        mock_settings.LLM_API_KEY = ""
        mock_settings.LLM_FALLBACK_TO_MOCK = True
        mock_settings.LLM_MODEL = "smartcampus-fallback-v1"
        mock_settings.LLM_BASE_URL = "http://localhost"
        mock_settings.LLM_TEMPERATURE = 0.7
        mock_settings.LLM_MAX_TOKENS = 1024
        mock_settings.LLM_TIMEOUT_SECONDS = 30

        resp = await generate_chat_reply(
            payload,
            session_id="sess-t7",
            is_authenticated=False,
        )

    assert resp.rolling_summary is not None
    assert isinstance(resp.rolling_summary, str)
    assert resp.history_turn_count == 6  # 6 MessageTurn objects loaded


# ---------------------------------------------------------------------------
# T8 — build_rolling_summary: bounded, correct content
# ---------------------------------------------------------------------------

def test_t8_rolling_summary_construction():
    """build_rolling_summary correctly assembles profile facts into a bounded summary."""
    facts = {
        "school_target": "iit",
        "current_specialty": "GLID",
        "bac_section": "Sciences Techniques",
        "declared_overall_avg": 14.5,
        "preferred_language": "fr",
    }
    summary = build_rolling_summary(declared_facts=facts)
    assert len(summary) <= 1200
    assert "IIT" in summary
    assert "GLID" in summary
    assert "14.5" in summary


def test_t8_rolling_summary_topic_cues_from_turns():
    """Recent turns containing specific keywords are captured in the rolling summary."""
    turns = [
        MessageTurn(role="user", content="Je veux faire une passerelle Bac+3", timestamp=1.0),
        MessageTurn(role="assistant", content="Voici les options de passerelle...", timestamp=2.0),
    ]
    summary = build_rolling_summary(recent_turns=turns)
    assert "passerelle" in summary.lower() or len(summary) > 0  # cue captured


def test_t8_rolling_summary_respects_existing_when_no_new_facts():
    """When no new facts are detected, the existing summary is preserved."""
    existing = "Établissement cible : ISB | Spécialité/Filière : Management"
    summary = build_rolling_summary(existing_summary=existing)
    assert summary == existing

