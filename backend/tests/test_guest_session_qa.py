"""QA automated tests for Guest Sessions & Anonymous State Tracking (SA-23 & SA-22).

Verifies Redis key patterns, partial field updates, TTL preservation, and atomic migration.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.session import GuestSessionResponse
from app.services.redis_service import GuestSessionService


def test_guest_session_key_pattern():
    """Verify that Redis keys for guest sessions follow the strict pattern guest:session:<id>."""
    key = GuestSessionService.get_key("anon-uuid-12345")
    assert key == "guest:session:anon-uuid-12345"


@pytest.mark.asyncio
async def test_guest_session_update_and_get():
    """Verify partial field updates and deserialization of stored guest session data."""
    mock_redis = MagicMock()
    # Mock pipeline for update_fields
    pipe_mock = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[1, True])
    mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe_mock)
    mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    # Mock hgetall
    mock_redis.hgetall = AsyncMock(
        return_value={
            "step": "PROFILING",
            "school_target": "iit",
            "degree_level": "cycle_ingenieur",
        }
    )
    # Mock hget for single field
    mock_redis.hget = AsyncMock(return_value="iit")

    service = GuestSessionService(redis_client=mock_redis)

    # 1. Update partial fields
    success = await service.update_fields(
        "guest-100", {"step": "PROFILING", "school_target": "iit"}
    )
    assert success is True

    # 2. Get single field
    school = await service.get_field("guest-100", "school_target")
    assert school == "iit"

    # 3. Get entire session
    session_data = await service.get_session("guest-100")
    assert session_data is not None
    assert session_data["step"] == "PROFILING"
    assert session_data["school_target"] == "iit"


@pytest.mark.asyncio
async def test_guest_session_migration():
    """Verify that migrating a guest session copies data to user session and deletes guest key."""
    mock_redis = MagicMock()
    service = GuestSessionService(redis_client=mock_redis)

    # Mock get_session on guest
    service.get_session = AsyncMock(
        return_value={"step": "ELIGIBLE", "target": "isb", "cart": [1, 2]}
    )

    # Mock pipeline for migration
    pipe_mock = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.delete = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[True, True, True])
    mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe_mock)
    mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    migrated = await service.migrate_session(
        guest_id="guest-999",
        new_user_id="user-uuid-111",
    )
    assert migrated is not None
    assert migrated["migrated_from_guest"] == "guest-999"
    assert migrated["target"] == "isb"
    # Verify pipe deleted the old guest session
    pipe_mock.delete.assert_called_with("guest:session:guest-999")


@pytest.mark.asyncio
async def test_guest_session_deletion():
    """Verify that deleting a guest session calls redis.delete."""
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock(return_value=1)
    service = GuestSessionService(redis_client=mock_redis)

    deleted = await service.delete_session("guest-123")
    assert deleted is True
    mock_redis.delete.assert_called_with("guest:session:guest-123")


@pytest.mark.asyncio
async def test_guest_session_ttl():
    """Verify getting remaining TTL for an active guest session."""
    mock_redis = MagicMock()
    mock_redis.ttl = AsyncMock(return_value=86400)
    service = GuestSessionService(redis_client=mock_redis)

    ttl = await service.get_ttl("guest-123")
    assert ttl == 86400


def test_guest_session_schema():
    """Verify GuestSessionResponse schema validates correctly."""
    response_obj = GuestSessionResponse(
        guest_id="guest-xyz",
        session_data={"step": "ORIENTATION", "preferred_language": "FR"},
        ttl_seconds=3600,
    )
    assert response_obj.guest_id == "guest-xyz"
    assert response_obj.session_data["preferred_language"] == "FR"
    assert response_obj.ttl_seconds == 3600
