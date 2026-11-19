import pytest
from fastapi.testclient import TestClient

import app.core.redis as redis_mod
from app.core.rate_limit import clear_rate_limit_store_for_tests
from app.main import app
from app.services.guest_session_service import clear_guest_session_store_for_tests


@pytest.fixture(autouse=True)
def _reset_rate_limit_and_guest_stores():
    """Ensure rate-limit counters and guest session fallback state do not bleed across tests."""
    clear_rate_limit_store_for_tests()
    clear_guest_session_store_for_tests()
    yield
    clear_rate_limit_store_for_tests()
    clear_guest_session_store_for_tests()
    redis_mod.redis_pool = None


@pytest.fixture(scope="session")
def client():
    """Session-scoped TestClient fixture for API integration tests."""
    with TestClient(app) as test_client:
        yield test_client

