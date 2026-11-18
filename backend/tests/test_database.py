import asyncio
import inspect
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.core.database import async_session_factory, check_db_health, engine, get_db

MOCK_CONNECTED_HEALTH = {
    "status": "connected",
    "database": "smartcampus_db",
    "user": "smartcampus_app",
    "postgres_version": "PostgreSQL 16.2 on x86_64",
    "pgvector": {"installed": True, "version": "0.7.0"},
    "schemas": {
        "expected": ["core", "iit", "isb", "isa", "knowledge"],
        "found": ["core", "iit", "isb", "isa", "knowledge"],
        "all_present": True,
    },
    "connection_pool": {"size": 10, "checkedin": 10, "checkedout": 0, "overflow": 0},
}

MOCK_DISCONNECTED_HEALTH = {
    "status": "disconnected",
    "error": "connection to server failed",
    "connection_pool": {"size": 10, "checkedin": 0, "checkedout": 0, "overflow": 0},
}


def test_database_settings_defaults(monkeypatch):
    """Verifies that database connection and pooling settings load with correct defaults."""
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    cfg = Settings()
    assert cfg.POSTGRES_USER == "smartcampus_app"
    assert cfg.POSTGRES_DB == "smartcampus_db"
    assert cfg.DB_POOL_SIZE == 10
    assert cfg.DB_MAX_OVERFLOW == 20
    assert cfg.DB_POOL_TIMEOUT == 30
    assert cfg.DB_POOL_RECYCLE == 1800
    assert "postgresql+asyncpg://" in cfg.async_database_url


def test_async_database_url_normalization():
    """Verifies that standard postgresql:// URL gets normalized to postgresql+asyncpg://."""
    cfg = Settings(DATABASE_URL="postgresql://user:pass@host:5432/db")
    assert cfg.async_database_url.startswith("postgresql+asyncpg://user:pass@host:5432/db")


def test_connection_pool_configuration():
    """Verifies that SQLAlchemy engine is configured with connection pooling parameters."""
    pool = engine.pool
    assert pool.size() == settings.DB_POOL_SIZE
    pool_timeout = getattr(pool, "_timeout", None)
    if pool_timeout is not None:
        assert pool_timeout == settings.DB_POOL_TIMEOUT


def test_check_db_health_returns_dict():
    """Verifies that check_db_health returns a dict with status and connection pool info."""
    result = asyncio.run(check_db_health())
    assert isinstance(result, dict)
    assert "status" in result
    assert "connection_pool" in result


def test_get_db_session_factory():
    """Verifies that async_session_factory creates an AsyncSession instance."""
    session = async_session_factory()
    assert isinstance(session, AsyncSession)


def test_get_db_generator():
    """Verifies that get_db dependency is an async generator function."""
    assert inspect.isasyncgenfunction(get_db)


def test_health_db_endpoint_structure(client):
    """Verifies that GET /health/db returns structured database and pool information."""
    with patch(
        "app.api.v1.endpoints.health.check_db_health",
        new=AsyncMock(return_value=MOCK_CONNECTED_HEALTH),
    ):
        response = client.get("/health/db")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert "connection_pool" in data
        assert data["connection_pool"]["size"] == 10
        assert data["connection_pool"]["checkedin"] == 10


def test_api_v1_health_db_endpoint_structure(client):
    """Verifies that GET /api/v1/health/db returns structured database and pool information."""
    with patch(
        "app.api.v1.endpoints.health.check_db_health",
        new=AsyncMock(return_value=MOCK_CONNECTED_HEALTH),
    ):
        response = client.get("/api/v1/health/db")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert "connection_pool" in data


def test_health_db_mocked_connected(client):
    """Verifies that GET /health/db returns 200 OK when database and all schemas are connected."""
    with patch(
        "app.api.v1.endpoints.health.check_db_health",
        new=AsyncMock(return_value=MOCK_CONNECTED_HEALTH),
    ):
        response = client.get("/health/db")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert data["pgvector"]["installed"] is True
        assert data["schemas"]["all_present"] is True
        assert set(data["schemas"]["found"]) == {"core", "iit", "isb", "isa", "knowledge"}


def test_health_db_mocked_disconnected(client):
    """Verifies that GET /health/db returns 503 Service Unavailable when database is unreachable."""
    with patch(
        "app.api.v1.endpoints.health.check_db_health",
        new=AsyncMock(return_value=MOCK_DISCONNECTED_HEALTH),
    ):
        response = client.get("/health/db")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "disconnected"
        assert data["error"] == "connection to server failed"
