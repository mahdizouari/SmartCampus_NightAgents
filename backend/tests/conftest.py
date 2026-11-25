from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.core.redis as redis_mod
from app.core.rate_limit import clear_rate_limit_store_for_tests
from app.main import app
from app.services.guest_session_service import clear_guest_session_store_for_tests
from app.services.vision_supervisor import reset_default_vision_supervisor


@pytest.fixture(autouse=True)
def _reset_vision_supervisor_singleton():
    """Ensure the process-wide VisionSupervisor singleton & MemorySaver reset between tests."""
    reset_default_vision_supervisor()
    yield
    reset_default_vision_supervisor()


@pytest.fixture(autouse=True)
def _reset_rate_limit_and_guest_stores():
    """Ensure rate-limit counters and guest session fallback state do not bleed across tests."""
    clear_rate_limit_store_for_tests()
    clear_guest_session_store_for_tests()
    yield
    clear_rate_limit_store_for_tests()
    clear_guest_session_store_for_tests()
    redis_mod.redis_pool = None


@pytest.fixture(autouse=True)
def _stub_redis_services():
    """Stub HotConversationService and GuestSessionService so tests never hit a real Redis.

    SA-37: generate_chat_reply now calls Redis; every test that exercises the HTTP layer
    (or generate_chat_reply directly without its own patch) must have Redis stubbed out.
    Tests that need specific Redis behaviour patch it themselves inside the test body.
    """
    noop_hot = AsyncMock()
    noop_hot.get_history = AsyncMock(return_value=[])
    noop_hot.add_turn = AsyncMock()

    noop_guest = AsyncMock()
    noop_guest.get_session = AsyncMock(return_value=None)
    noop_guest.update_fields = AsyncMock()

    with (
        patch("app.services.llm_service.HotConversationService", return_value=noop_hot),
        patch("app.services.llm_service.GuestSessionService", return_value=noop_guest),
    ):
        yield


@pytest.fixture(scope="session")
def client():
    """Session-scoped TestClient fixture for API integration tests."""
    with TestClient(app) as test_client:
        yield test_client

