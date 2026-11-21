import logging
from collections.abc import AsyncGenerator
from typing import Any

from redis import asyncio as aioredis
from redis.asyncio import ConnectionPool, Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global connection pool singleton
redis_pool: ConnectionPool | None = None


def get_redis_pool() -> ConnectionPool:
    """Initializes or returns the global Redis ConnectionPool."""
    global redis_pool
    if redis_pool is None:
        redis_pool = ConnectionPool.from_url(
            settings.effective_redis_url,
            max_connections=50,
            decode_responses=True,
            socket_timeout=1.5,
            socket_connect_timeout=1.5,
        )
    return redis_pool


def get_redis_client() -> Redis:
    """Returns an async Redis client from the connection pool."""
    pool = get_redis_pool()
    return aioredis.Redis(connection_pool=pool)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI dependency yielding an async Redis client."""
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()


async def close_redis_pool() -> None:
    """Closes all Redis connections on application shutdown."""
    global redis_pool
    if redis_pool is not None:
        logger.info("Closing Redis connection pool...")
        await redis_pool.disconnect()
        redis_pool = None
        logger.info("Redis connection pool closed.")


async def check_redis_health() -> dict[str, Any]:
    """Verifies Redis connectivity and reports status."""
    try:
        client = get_redis_client()
        pong = await client.ping()
        info = await client.info("memory")
        return {
            "status": "connected" if pong else "degraded",
            "ping": bool(pong),
            "used_memory_human": info.get("used_memory_human", "unknown"),
        }
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return {
            "status": "disconnected",
            "error": str(exc),
        }
