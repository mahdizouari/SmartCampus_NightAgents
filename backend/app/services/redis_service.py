"""
Redis service module for managing:
1. Hot Conversation History (chat:history:<sessionId>):
   - Retains the last 8 turns (16 messages max: indices 0 to 15) in a Redis List
   - Refreshes a 7-day TTL (604,800s) on every new turn
   - Retrieves turns in chronological order (oldest to newest)

2. Temporary Guest Session State (guest:session:<guestId>):
   - Partial updates via Redis Hash (HSET / HGET / HGETALL)
   - Configurable TTL (default: 24h / 86,400s) refreshed on updates
   - Atomic migration from guest session to authenticated user session

Usage Example:
--------------
```python
import asyncio
from app.services.redis_service import GuestSessionService, HotConversationService

async def example():
    chat_svc = HotConversationService()
    guest_svc = GuestSessionService()

    # 1. Add conversation turns
    await chat_svc.add_turn(
        "sess-1", role="user", content="Quelles sont les filières ?"
    )
    await chat_svc.add_turn(
        "sess-1", role="assistant", content="L'IIT propose Génie Info..."
    )
    history = await chat_svc.get_history("sess-1")  # Chronological order

    # 2. Manage guest session state (partial updates)
    await guest_svc.update_fields(
        "guest-100", {"step": "PROFILING", "school_target": "iit"}
    )
    await guest_svc.update_fields("guest-100", {"cartCount": 1})
    session = await guest_svc.get_session("guest-100")

    # Migrate to authenticated user upon registration
    await guest_svc.migrate_session("guest-100", "user-uuid-999")
```
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis import get_redis_client
from app.schemas.session import MessageTurn

logger = logging.getLogger(__name__)


def _serialize_hash_value(value: Any) -> str:
    """Serializes a Python object into a string for Redis Hash storage."""
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value)
    return str(value)


def _deserialize_hash_value(value: str | None) -> Any:
    """Attempts to deserialize a Redis Hash string value back to a Python type."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class HotConversationService:
    """
    Manages sliding hot conversation memory in Redis.

    Key Pattern: chat:history:<sessionId>
    Structure: Redis List (LPUSH + LTRIM + EXPIRE)
    Retains: Last 8 turns (16 messages max: indices 0 to 15)
    TTL: 7 days (604800s), refreshed on every new turn.
    """

    def __init__(
        self,
        redis_client: Redis | None = None,
        ttl_seconds: int = settings.REDIS_CHAT_HISTORY_TTL,
        max_messages: int = settings.REDIS_CHAT_MAX_MESSAGES,
    ) -> None:
        self._redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    @staticmethod
    def get_key(session_id: str) -> str:
        """Returns the Redis key for the conversation history."""
        return f"chat:history:{session_id}"

    async def add_turn(
        self,
        session_id: str,
        role: Literal["user", "assistant"],
        content: str,
        timestamp: float | None = None,
    ) -> MessageTurn:
        """
        Appends a single message turn to the session history atomically.
        Trims the list to max_messages and refreshes the TTL.
        """
        if timestamp is None:
            timestamp = datetime.now(UTC).timestamp()

        turn = MessageTurn(role=role, content=content, timestamp=timestamp)
        payload = turn.model_dump_json()
        key = self.get_key(session_id)

        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.lpush(key, payload)
                pipe.ltrim(key, 0, self.max_messages - 1)
                pipe.expire(key, self.ttl_seconds)
                await pipe.execute()
            return turn
        except Exception as exc:
            logger.error("Failed to add turn for session %s: %s", session_id, exc)
            raise

    async def add_turns(
        self,
        session_id: str,
        turns: list[MessageTurn],
    ) -> list[MessageTurn]:
        """
        Appends multiple message turns atomically, keeping chronological order.
        """
        if not turns:
            return []

        key = self.get_key(session_id)
        payloads = [turn.model_dump_json() for turn in turns]

        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                for payload in payloads:
                    pipe.lpush(key, payload)
                pipe.ltrim(key, 0, self.max_messages - 1)
                pipe.expire(key, self.ttl_seconds)
                await pipe.execute()
            return turns
        except Exception as exc:
            logger.error("Failed to add turns for session %s: %s", session_id, exc)
            raise

    async def get_history(self, session_id: str) -> list[MessageTurn]:
        """
        Retrieves the conversation history in chronological order (oldest first).
        """
        key = self.get_key(session_id)
        try:
            raw_items = await self.redis.lrange(key, 0, self.max_messages - 1)
            # LPUSH inserts newest at index 0, so reversing gives chronological order
            turns = [
                MessageTurn.model_validate_json(item)
                for item in reversed(raw_items)
            ]
            return turns
        except Exception as exc:
            logger.error("Failed to get history for session %s: %s", session_id, exc)
            raise

    async def clear_history(self, session_id: str) -> bool:
        """Deletes the conversation history for a given session."""
        key = self.get_key(session_id)
        try:
            deleted_count = await self.redis.delete(key)
            return bool(deleted_count > 0)
        except Exception as exc:
            logger.error("Failed to clear history for session %s: %s", session_id, exc)
            raise

    async def get_ttl(self, session_id: str) -> int:
        """Returns the remaining TTL in seconds for the conversation history."""
        key = self.get_key(session_id)
        try:
            return await self.redis.ttl(key)
        except Exception as exc:
            logger.error("Failed to get TTL for session %s: %s", session_id, exc)
            raise


class GuestSessionService:
    """
    Manages temporary guest session state in Redis.

    Key Pattern: guest:session:<guestId>
    Structure: Redis Hash (HSET + HGET + HGETALL + EXPIRE)
    Supports: Partial field updates, TTL refresh, session migration.
    Default TTL: 24 hours (86400s), configurable and refreshed on updates.
    """

    def __init__(
        self,
        redis_client: Redis | None = None,
        default_ttl: int = settings.REDIS_GUEST_SESSION_TTL,
    ) -> None:
        self._redis = redis_client
        self.default_ttl = default_ttl

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    @staticmethod
    def get_key(guest_id: str) -> str:
        """Returns the Redis key for the guest session."""
        return f"guest:session:{guest_id}"

    async def get_field(self, guest_id: str, field: str) -> Any:
        """Retrieves and deserializes a single field from the guest session."""
        key = self.get_key(guest_id)
        try:
            val = await self.redis.hget(key, field)
            return _deserialize_hash_value(val)
        except Exception as exc:
            logger.error(
                "Failed to get field '%s' for guest %s: %s",
                field,
                guest_id,
                exc,
            )
            raise

    async def get_session(self, guest_id: str) -> dict[str, Any] | None:
        """Retrieves and deserializes the full guest session dictionary."""
        key = self.get_key(guest_id)
        try:
            raw_hash = await self.redis.hgetall(key)
            if not raw_hash:
                return None
            return {k: _deserialize_hash_value(v) for k, v in raw_hash.items()}
        except Exception as exc:
            logger.error("Failed to get session for guest %s: %s", guest_id, exc)
            raise

    async def update_fields(
        self,
        guest_id: str,
        fields: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> bool:
        """
        Updates specific fields of the guest session without overwriting other fields.
        Atomically applies field updates and refreshes the TTL.
        """
        if not fields:
            return True

        key = self.get_key(guest_id)
        effective_ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        serialized = {k: _serialize_hash_value(v) for k, v in fields.items()}

        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping=serialized)
                pipe.expire(key, effective_ttl)
                await pipe.execute()
            return True
        except Exception as exc:
            logger.error("Failed to update fields for guest %s: %s", guest_id, exc)
            raise

    async def delete_session(self, guest_id: str) -> bool:
        """Deletes the guest session entirely."""
        key = self.get_key(guest_id)
        try:
            deleted = await self.redis.delete(key)
            return bool(deleted > 0)
        except Exception as exc:
            logger.error("Failed to delete session for guest %s: %s", guest_id, exc)
            raise

    async def migrate_session(
        self,
        guest_id: str,
        new_user_id: str,
        user_session_ttl: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Migrates data from a guest session to a registered user session.
        Atomically copies the data to `user:session:<new_user_id>` and deletes
        the guest session key, returning the migrated session payload.
        """
        key_guest = self.get_key(guest_id)
        key_user = f"user:session:{new_user_id}"
        effective_ttl = user_session_ttl or self.default_ttl

        try:
            session_data = await self.get_session(guest_id)
            if not session_data:
                return None

            serialized = {k: _serialize_hash_value(v) for k, v in session_data.items()}
            # Track migration metadata
            serialized["migrated_from_guest"] = guest_id
            serialized["migrated_at"] = datetime.now(UTC).isoformat()

            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hset(key_user, mapping=serialized)
                pipe.expire(key_user, effective_ttl)
                pipe.delete(key_guest)
                await pipe.execute()

            session_data["migrated_from_guest"] = guest_id
            session_data["migrated_at"] = serialized["migrated_at"]
            return session_data
        except Exception as exc:
            logger.error(
                "Failed to migrate guest %s to user %s: %s",
                guest_id,
                new_user_id,
                exc,
            )
            raise

    async def get_ttl(self, guest_id: str) -> int:
        """Returns the remaining TTL in seconds for the guest session."""
        key = self.get_key(guest_id)
        try:
            return await self.redis.ttl(key)
        except Exception as exc:
            logger.error("Failed to get TTL for guest %s: %s", guest_id, exc)
            raise
