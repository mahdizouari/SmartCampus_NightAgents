"""Unit and integration tests for Whisper voice-notes transcription and review pipeline."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.whisper_service import (
    MAX_AUDIO_SIZE_BYTES,
    build_whisper_prompt,
    validate_audio_upload,
)

# =============================================================================
# 1. Validation & Prompt Building Tests
# =============================================================================


def test_validate_audio_empty():
    with pytest.raises(HTTPException) as exc_info:
        validate_audio_upload("test.webm", "audio/webm", b"")
    assert exc_info.value.status_code == 400


def test_validate_audio_oversize():
    big_bytes = b"x" * (MAX_AUDIO_SIZE_BYTES + 10)
    with pytest.raises(HTTPException) as exc_info:
        validate_audio_upload("test.mp3", "audio/mpeg", big_bytes)
    assert exc_info.value.status_code == 413


def test_validate_audio_unsupported_format():
    with pytest.raises(HTTPException) as exc_info:
        validate_audio_upload("test.exe", "application/x-msdownload", b"RIFF1234")
    assert exc_info.value.status_code == 400
    assert "Format audio non supporté" in exc_info.value.detail


def test_validate_audio_valid_formats():
    valid_samples = [
        ("note.webm", "audio/webm"),
        ("note.mp4", "audio/mp4"),
        ("note.m4a", "audio/x-m4a"),
        ("note.mp3", "audio/mpeg"),
        ("note.wav", "audio/wav"),
        ("note.ogg", "audio/ogg"),
    ]
    for fname, mime in valid_samples:
        # Should not raise
        validate_audio_upload(fname, mime, b"fake audio content")


def test_build_whisper_prompt_modes():
    # Auto mode: omit language
    prompt_auto, lang_auto = build_whisper_prompt("auto", "all")
    assert lang_auto is None
    assert "SmartCampus" in prompt_auto
    assert "IIT" in prompt_auto
    assert "ISB" in prompt_auto
    assert "ISA" in prompt_auto

    # Derja mode: language="ar"
    prompt_derja, lang_derja = build_whisper_prompt("derja", "IIT")
    assert lang_derja == "ar"
    assert "فما" in prompt_derja
    assert "Focus école : IIT" in prompt_derja

    # Arabic mode: language="ar"
    prompt_ar, lang_ar = build_whisper_prompt("ar", "ISB")
    assert lang_ar == "ar"
    assert "توجيه" in prompt_ar
    assert "Focus école : ISB" in prompt_ar

    # French mode: language="fr"
    prompt_fr, lang_fr = build_whisper_prompt("fr", "ISA")
    assert lang_fr == "fr"
    assert "orientation universitaire" in prompt_fr
    assert "Focus école : ISA" in prompt_fr


# =============================================================================
# 2. Endpoint Tests: /api/voice-notes/transcribe & /api/v1/voice-notes/transcribe
# =============================================================================


@pytest.mark.parametrize(
    "endpoint",
    ["/api/voice-notes/transcribe", "/api/v1/voice-notes/transcribe"],
)
def test_transcribe_endpoint_dev_mock(client, endpoint):
    """Test voice note transcription returns review card payload without calling LLM."""
    fake_audio = io.BytesIO(b"RIFF dummy wav audio data 1234567890")
    files = {"audio": ("sample.wav", fake_audio, "audio/wav")}
    data = {"mode": "auto", "campus_scope": "IIT"}

    response = client.post(endpoint, files=files, data=data)
    assert response.status_code == 200
    res_data = response.json()

    assert "transcript_id" in res_data
    assert res_data["transcript_id"].startswith("vn-")
    assert "transcript" in res_data
    assert len(res_data["transcript"]) > 0
    assert res_data["mode"] == "auto"
    assert res_data["campus_scope"] == "IIT"
    assert res_data["requires_review"] is True
    # Crucial: verify that LLM was not run in transcribe step (no reply or agent fields)
    assert "reply" not in res_data
    assert "reply_text" not in res_data


def test_transcribe_endpoint_invalid_file(client):
    files = {"audio": ("bad.txt", io.BytesIO(b"hello world"), "text/plain")}
    response = client.post("/api/voice-notes/transcribe", files=files)
    assert response.status_code == 400


def test_transcribe_with_mocked_openai_api(client, monkeypatch):
    """Test OpenAI Whisper API integration when OPENAI_API_KEY is configured."""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-key-12345")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {
        "text": "3aslema, nheb na3ref kifech nsajel fi l'IIT w chnowa les filières d'ingénierie."
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        fake_audio = io.BytesIO(b"OGG dummy audio bytes")
        files = {"audio": ("recording.ogg", fake_audio, "audio/ogg")}
        data = {"mode": "derja", "campus_scope": "IIT"}

        response = client.post("/api/voice-notes/transcribe", files=files, data=data)
        assert response.status_code == 200
        res_data = response.json()

        expected_text = (
            "3aslema, nheb na3ref kifech nsajel fi l'IIT w chnowa les filières d'ingénierie."
        )
        assert res_data["transcript"] == expected_text
        assert res_data["mode"] == "derja"
        assert res_data["campus_scope"] == "IIT"
        assert res_data["requires_review"] is True

        # Verify that Whisper API was called with proper headers and data
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test-key-12345"
        assert kwargs["data"]["model"] == settings.WHISPER_MODEL
        assert kwargs["data"]["language"] == "ar"


def test_transcribe_openai_api_error_handling(client, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-key-12345")

    mock_resp = AsyncMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"error": {"message": "Invalid API key"}}'

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        files = {"audio": ("recording.webm", io.BytesIO(b"webm bytes"), "audio/webm")}
        response = client.post("/api/voice-notes/transcribe", files=files)
        assert response.status_code == 502
        assert "Erreur de l'API OpenAI Whisper" in response.json()["detail"]


# =============================================================================
# 3. Endpoint Tests: /api/voice-notes/process & /api/v1/voice-notes/process
# =============================================================================


@pytest.mark.parametrize(
    "endpoint",
    ["/api/voice-notes/process", "/api/v1/voice-notes/process"],
)
def test_process_endpoint_success(client, endpoint):
    """Test user-confirmed transcript is routed into SmartCampus agent/LLM pipeline."""
    payload = {
        "transcript_id": "vn-12345",
        "final_transcript": "Quelles sont les filières d'ingénierie informatique à l'IIT ?",
        "campus_scope": "IIT",
        "mode": "fr",
        "session_id": "session-test-01",
    }

    response = client.post(endpoint, json=payload)
    assert response.status_code == 200
    res_data = response.json()

    assert res_data["transcript_id"] == "vn-12345"
    assert res_data["campus_scope"] == "IIT"
    assert res_data["mode"] == "fr"
    assert res_data["final_transcript"] == payload["final_transcript"]
    assert "reply" in res_data
    assert "subAgent" in res_data
    assert len(res_data["reply"]) > 0
    assert res_data["detectedDialect"] == "French"


def test_process_endpoint_derja_arabizi(client):
    payload = {
        "transcript_id": "vn-derja-1",
        "final_transcript": "3aslema, nheb na3ref kifech nsajel fi l'ISB w chnowa les licences ?",
        "campus_scope": "ISB",
        "mode": "derja",
    }
    response = client.post("/api/voice-notes/process", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["detectedDialect"] in {"Arabizi", "Tunisian_Derja"}
    assert len(res_data["reply"]) > 0


def test_process_endpoint_empty_transcript(client):
    """Ensure non-empty final_transcript validation is enforced."""
    payload = {
        "transcript_id": "vn-empty",
        "final_transcript": "   ",
        "campus_scope": "all",
    }
    response = client.post("/api/voice-notes/process", json=payload)
    assert response.status_code == 422
