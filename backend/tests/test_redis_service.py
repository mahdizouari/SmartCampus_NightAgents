import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.redis import check_redis_health
from app.services.redis_service import (
    GuestSessionService,
    HotConversationService,
    _deserialize_hash_value,
    _serialize_hash_value,
)


class FakePipeline:
    """In-memory Redis pipeline emulator for atomic testing."""

    def __init__(self, store: dict, ttls: dict) -> None:
        self.store = store
        self.ttls = ttls
        self.ops = []

    def lpush(self, key: str, value: str):
        self.ops.append(("lpush", key, value))

    def ltrim(self, key: str, start: int, stop: int):
        self.ops.append(("ltrim", key, start, stop))

    def expire(self, key: str, ttl: int):
        self.ops.append(("expire", key, ttl))

    def hset(self, key: str, mapping: dict):
        self.ops.append(("hset", key, mapping))

    def delete(self, key: str):
        self.ops.append(("delete", key))

    async def execute(self):
        for op in self.ops:
            action = op[0]
            if action == "lpush":
                key, val = op[1], op[2]
                self.store.setdefault(key, [])
                self.store[key].insert(0, val)
            elif action == "ltrim":
                key, start, stop = op[1], op[2], op[3]
                if key in self.store:
                    self.store[key] = self.store[key][start : stop + 1]
            elif action == "expire":
                key, ttl = op[1], op[2]
                self.ttls[key] = ttl
            elif action == "hset":
                key, mapping = op[1], op[2]
                self.store.setdefault(key, {})
                self.store[key].update(mapping)
            elif action == "delete":
                key = op[1]
                self.store.pop(key, None)
                self.ttls.pop(key, None)
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class FakeRedis:
    """Async Redis mock that supports List and Hash operations with pipelines."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self, transaction: bool = True):
        return FakePipeline(self.store, self.ttls)

    async def lrange(self, key: str, start: int, stop: int):
        lst = self.store.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def delete(self, key: str):
        existed = key in self.store
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed else 0

    async def ttl(self, key: str):
        if key not in self.store:
            return -2
        return self.ttls.get(key, -1)

    async def hget(self, key: str, field: str):
        return self.store.get(key, {}).get(field)

    async def hgetall(self, key: str):
        return dict(self.store.get(key, {}))

    async def ping(self):
        return True

    async def info(self, section: str = "memory"):
        return {"used_memory_human": "1.5M"}


def test_redis_settings_defaults():
    """Verifies that Redis configuration loads with correct defaults."""
    cfg = Settings()
    assert cfg.REDIS_HOST == "redis"
    assert cfg.REDIS_PORT == 6379
    assert cfg.REDIS_DB == 0
    assert cfg.REDIS_CHAT_HISTORY_TTL == 604800  # 7 days
    assert cfg.REDIS_CHAT_MAX_MESSAGES == 16  # 8 turns
    assert cfg.REDIS_GUEST_SESSION_TTL == 86400  # 24 hours
    assert "redis://redis:6379/0" in cfg.effective_redis_url


def test_redis_custom_url_override():
    """Verifies that REDIS_URL explicitly overrides host/port."""
    cfg = Settings(REDIS_URL="redis://:secret@prod-redis:6380/2")
    assert cfg.effective_redis_url == "redis://:secret@prod-redis:6380/2"


def test_hash_value_serialization():
    """Verifies serialization and deserialization of various Python types."""
    assert _serialize_hash_value("text") == "text"
    assert _serialize_hash_value(123) == "123"
    assert _serialize_hash_value(True) == "true"
    assert _serialize_hash_value({"a": 1}) == '{"a": 1}'
    assert _serialize_hash_value(["item1", "item2"]) == '["item1", "item2"]'

    assert _deserialize_hash_value("text") == "text"
    assert _deserialize_hash_value("123") == 123
    assert _deserialize_hash_value("true") is True
    assert _deserialize_hash_value('{"a": 1}') == {"a": 1}
    assert _deserialize_hash_value('["item1", "item2"]') == ["item1", "item2"]
    assert _deserialize_hash_value(None) is None


@pytest.mark.asyncio
async def test_hot_conversation_add_turn_and_history():
    """Verifies adding single turns, 7-day TTL, and chronological ordering."""
    fake_redis = FakeRedis()
    service = HotConversationService(redis_client=fake_redis)

    await service.add_turn("sess-1", "user", "Bonjour")
    await service.add_turn("sess-1", "assistant", "Bonjour! Comment puis-je vous aider ?")

    history = await service.get_history("sess-1")
    assert len(history) == 2
    # Chronological: first message was user, second was assistant
    assert history[0].role == "user"
    assert history[0].content == "Bonjour"
    assert history[1].role == "assistant"
    assert history[1].content == "Bonjour! Comment puis-je vous aider ?"

    ttl = await service.get_ttl("sess-1")
    assert ttl == 604800


@pytest.mark.asyncio
async def test_hot_conversation_trims_to_max_turns():
    """Verifies that the conversation list strictly trims to 16 messages (8 turns)."""
    fake_redis = FakeRedis()
    service = HotConversationService(redis_client=fake_redis, max_messages=16)

    # Push 20 messages (10 turns)
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        await service.add_turn("sess-trim", role, f"Message {i}")

    history = await service.get_history("sess-trim")
    assert len(history) == 16
    # Oldest retained message should be Message 4 (0 to 3 were trimmed out)
    assert history[0].content == "Message 4"
    # Newest retained message should be Message 19
    assert history[-1].content == "Message 19"


@pytest.mark.asyncio
async def test_hot_conversation_clear_history():
    """Verifies clearing conversation history."""
    fake_redis = FakeRedis()
    service = HotConversationService(redis_client=fake_redis)

    await service.add_turn("sess-clear", "user", "Test")
    assert len(await service.get_history("sess-clear")) == 1

    cleared = await service.clear_history("sess-clear")
    assert cleared is True
    assert len(await service.get_history("sess-clear")) == 0


@pytest.mark.asyncio
async def test_guest_session_crud_and_partial_update():
    """Verifies partial hash updates without overwriting previous fields."""
    fake_redis = FakeRedis()
    service = GuestSessionService(redis_client=fake_redis)

    # Initial state
    await service.update_fields("guest-100", {"step": "PROFILING", "cartCount": 0})
    session = await service.get_session("guest-100")
    assert session == {"step": "PROFILING", "cartCount": 0}

    # Partial update: modify cartCount, add preferences, keep step intact
    await service.update_fields(
        "guest-100",
        {"cartCount": 2, "preferences": ["prepa_integree", "genie_logiciel"]},
    )

    updated = await service.get_session("guest-100")
    assert updated["step"] == "PROFILING"
    assert updated["cartCount"] == 2
    assert updated["preferences"] == ["prepa_integree", "genie_logiciel"]

    # Read single field
    step_val = await service.get_field("guest-100", "step")
    assert step_val == "PROFILING"

    ttl = await service.get_ttl("guest-100")
    assert ttl == 86400


@pytest.mark.asyncio
async def test_guest_session_migration():
    """Verifies atomic migration from guest session to authenticated user session."""
    fake_redis = FakeRedis()
    service = GuestSessionService(redis_client=fake_redis)

    await service.update_fields(
        "guest-abc",
        {"candidate_name": "Yassine", "school_target": "iit", "score": 15.5},
    )

    migrated = await service.migrate_session("guest-abc", "user-uuid-999")
    assert migrated is not None
    assert migrated["candidate_name"] == "Yassine"
    assert migrated["school_target"] == "iit"
    assert migrated["score"] == 15.5
    assert migrated["migrated_from_guest"] == "guest-abc"
    assert "migrated_at" in migrated

    # Guest session key must be deleted
    assert await service.get_session("guest-abc") is None
    # User key exists in store
    user_data = fake_redis.store.get("user:session:user-uuid-999")
    assert user_data is not None
    assert json.loads(user_data["score"]) == 15.5


@pytest.mark.asyncio
async def test_guest_session_delete():
    """Verifies guest session deletion."""
    fake_redis = FakeRedis()
    service = GuestSessionService(redis_client=fake_redis)

    await service.update_fields("guest-del", {"active": True})
    assert await service.delete_session("guest-del") is True
    assert await service.get_session("guest-del") is None


def test_redis_health_endpoints_with_mock(client):
    """Verifies /health/redis root and v1 endpoints return 200 on connected."""
    mock_health = {
        "status": "connected",
        "ping": True,
        "used_memory_human": "2.1M",
    }
    with patch(
        "app.api.v1.endpoints.health.check_redis_health",
        new=AsyncMock(return_value=mock_health),
    ):
        resp_root = client.get("/health/redis")
        assert resp_root.status_code == 200
        data = resp_root.json()
        assert data["status"] == "connected"
        assert data["ping"] is True
        assert data["used_memory_human"] == "2.1M"

        resp_v1 = client.get("/api/v1/health/redis")
        assert resp_v1.status_code == 200


def test_redis_health_endpoint_disconnected(client):
    """Verifies /health/redis returns 503 Service Unavailable when Redis is unreachable."""
    mock_health = {
        "status": "disconnected",
        "error": "Error 111 connecting to redis:6379. Connection refused.",
    }
    with patch(
        "app.api.v1.endpoints.health.check_redis_health",
        new=AsyncMock(return_value=mock_health),
    ):
        response = client.get("/health/redis")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "disconnected"
        assert "Connection refused" in data["error"]


def test_check_redis_health_returns_dict():
    """Verifies check_redis_health returns proper status dict."""
    result = asyncio.run(check_redis_health())
    assert isinstance(result, dict)
    assert "status" in result
