"""Per-session and per-IP HTTP rate limiting with Redis and in-memory fallback (SA-25)."""

import hashlib
import time
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.deps import decode_hs256_jwt
from app.core.redis import get_redis_client

_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# In-memory fallback counters: key -> (count, window_expires_at_epoch)
_LOCAL_RATE_STORE: dict[str, tuple[int, float]] = {}
_REDIS_BACKOFF_UNTIL: float = 0.0


@dataclass(frozen=True)
class RateLimitDecision:
    """Computed rate-limit evaluation for the current HTTP request."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    scope: str


def clear_rate_limit_store_for_tests() -> None:
    """Clear in-memory rate-limit counters and isolate test runs from shared live Redis buckets."""
    _LOCAL_RATE_STORE.clear()
    mark_redis_unavailable_temporarily(60.0)


def mark_redis_unavailable_temporarily(backoff_seconds: float = 30.0) -> None:
    """Circuit-breaker helper avoiding repeated DNS timeouts when Redis is offline."""
    global _REDIS_BACKOFF_UNTIL
    _REDIS_BACKOFF_UNTIL = time.monotonic() + backoff_seconds


def is_redis_temporarily_unavailable() -> bool:
    """Return True if the offline Redis circuit-breaker is currently open."""
    return time.monotonic() < _REDIS_BACKOFF_UNTIL


def _hash_identifier(raw: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}:rl:{raw}".encode()).hexdigest()[:24]


def _is_exempt_path(path: str) -> bool:
    if path == "/":
        return True
    return any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _extract_authenticated_actor_claims(request: Request) -> dict | None:
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        return decode_hs256_jwt(token, expected_type="access")
    except Exception:
        return None


def _extract_guest_session_id(request: Request) -> str | None:
    cookie_token = request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    if cookie_token:
        try:
            payload = decode_hs256_jwt(cookie_token, expected_type="guest_session")
            sid = str(payload.get("sub") or payload.get("sid") or "")
            UUID(sid)
            return sid
        except Exception:
            pass

    header_sid = request.headers.get("x-session-id")
    if header_sid:
        try:
            return str(UUID(header_sid.strip()))
        except Exception:
            return None
    return None


async def _increment_bucket(key: str, window_seconds: int) -> tuple[int, int]:
    """Increment counter in Redis (or fast local fallback if Redis is unavailable)."""
    now = time.time()
    redis_key = f"ratelimit:{key}"

    if not is_redis_temporarily_unavailable():
        try:
            redis = get_redis_client()
            count = int(await redis.incr(redis_key))
            if count == 1:
                await redis.expire(redis_key, window_seconds)
                ttl = window_seconds
            else:
                ttl = int(await redis.ttl(redis_key))
                if ttl <= 0:
                    await redis.expire(redis_key, window_seconds)
                    ttl = window_seconds
            return count, max(1, ttl)
        except Exception:
            mark_redis_unavailable_temporarily(30.0)

    existing = _LOCAL_RATE_STORE.get(redis_key)
    if existing is None or existing[1] <= now:
        count = 1
        expires_at = now + window_seconds
    else:
        count = existing[0] + 1
        expires_at = existing[1]
    _LOCAL_RATE_STORE[redis_key] = (count, expires_at)
    reset_seconds = max(1, int(expires_at - now))
    return count, reset_seconds


async def evaluate_rate_limit(
    request: Request,
    *,
    scope_override: str | None = None,
    limit_override: int | None = None,
) -> RateLimitDecision:
    """Evaluate per-session and per-IP quotas defined in SA-5 (rbac_matrix.md §5.1)."""
    window = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
    path = request.url.path

    actor_claims = _extract_authenticated_actor_claims(request)
    if actor_claims is not None:
        role = str(actor_claims.get("role", "student")).lower()
        account_id = str(actor_claims.get("sub"))
        limit = (
            limit_override
            if limit_override is not None
            else (
                settings.RATE_LIMIT_STUDENT_PER_MINUTE
                if role == "student"
                else settings.RATE_LIMIT_STAFF_PER_MINUTE
            )
        )
        scope = scope_override or f"account:{role}"
        count, reset_sec = await _increment_bucket(
            f"{scope}:{_hash_identifier(account_id)}",
            window,
        )
        remaining = max(0, limit - count)
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=remaining,
            reset_seconds=reset_sec,
            scope=scope,
        )

    # Anonymous Guest: enforce both per-IP quota and (when present) per-session quota
    client_ip = _extract_client_ip(request)
    ip_limit = (
        limit_override
        if limit_override is not None
        else settings.RATE_LIMIT_GUEST_IP_PER_MINUTE
    )
    ip_scope = scope_override or "guest_ip"
    ip_count, ip_reset = await _increment_bucket(
        f"{ip_scope}:{_hash_identifier(client_ip)}",
        window,
    )
    ip_decision = RateLimitDecision(
        allowed=ip_count <= ip_limit,
        limit=ip_limit,
        remaining=max(0, ip_limit - ip_count),
        reset_seconds=ip_reset,
        scope=ip_scope,
    )

    guest_sid = _extract_guest_session_id(request)
    if not guest_sid:
        return ip_decision

    is_chat_or_orientation = (
        "/chat" in path
        or "/orientation" in path
        or scope_override == "guest_chat_session"
    )
    sess_limit = (
        limit_override
        if limit_override is not None
        else (
            settings.RATE_LIMIT_GUEST_CHAT_PER_MINUTE
            if is_chat_or_orientation
            else settings.RATE_LIMIT_GUEST_SESSION_PER_MINUTE
        )
    )
    sess_scope = (
        scope_override
        or ("guest_chat_session" if is_chat_or_orientation else "guest_session")
    )
    sess_count, sess_reset = await _increment_bucket(
        f"{sess_scope}:{_hash_identifier(guest_sid)}",
        window,
    )
    sess_decision = RateLimitDecision(
        allowed=sess_count <= sess_limit,
        limit=sess_limit,
        remaining=max(0, sess_limit - sess_count),
        reset_seconds=sess_reset,
        scope=sess_scope,
    )

    if not sess_decision.allowed:
        return sess_decision
    if not ip_decision.allowed:
        return ip_decision
    return sess_decision if sess_decision.remaining <= ip_decision.remaining else ip_decision


def attach_rate_limit_headers(response: Response, decision: RateLimitDecision) -> None:
    """Attach standard IETF rate-limit headers (rbac_matrix.md §5.3)."""
    response.headers["RateLimit-Limit"] = str(decision.limit)
    response.headers["RateLimit-Remaining"] = str(decision.remaining)
    response.headers["RateLimit-Reset"] = str(decision.reset_seconds)


async def enforce_guest_chat_rate_limit(
    request: Request,
    response: Response,
) -> RateLimitDecision:
    """FastAPI dependency enforcing the 15 req/min anonymous chat/orientation session limit."""
    existing: RateLimitDecision | None = getattr(request.state, "rate_limit_decision", None)
    if existing is not None and existing.scope == "guest_chat_session":
        decision = existing
    else:
        decision = await evaluate_rate_limit(
            request,
            scope_override="guest_chat_session",
            limit_override=settings.RATE_LIMIT_GUEST_CHAT_PER_MINUTE,
        )
    attach_rate_limit_headers(response, decision)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Anonymous session rate limit exceeded ({decision.limit} req/min). "
                f"Retry in {decision.reset_seconds}s."
            ),
            headers={
                "Retry-After": str(decision.reset_seconds),
                "RateLimit-Limit": str(decision.limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(decision.reset_seconds),
            },
        )
    return decision


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global HTTP middleware enforcing per-IP and per-session rate limits on /api/v1/*."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not settings.RATE_LIMIT_ENABLED or _is_exempt_path(request.url.path):
            return await call_next(request)

        decision = await evaluate_rate_limit(request)
        request.state.rate_limit_decision = decision
        if not decision.allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "status": 429,
                    "message": "Too Many Requests",
                    "detail": (
                        f"Rate limit exceeded ({decision.limit} req/min for {decision.scope}). "
                        f"Please retry in {decision.reset_seconds}s."
                    ),
                    "instance": request.url.path,
                    "retry_after_seconds": decision.reset_seconds,
                },
                headers={
                    "Retry-After": str(decision.reset_seconds),
                    "RateLimit-Limit": str(decision.limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(decision.reset_seconds),
                },
            )

        response = await call_next(request)
        if "RateLimit-Limit" not in response.headers:
            attach_rate_limit_headers(response, decision)
        return response
