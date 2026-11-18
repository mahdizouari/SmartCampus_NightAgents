"""Signed anonymous guest session management with 7-day sliding TTL (SA-25)."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import decode_hs256_jwt, encode_hs256_jwt
from app.core.rate_limit import (
    is_redis_temporarily_unavailable,
    mark_redis_unavailable_temporarily,
)
from app.models.users import GuestSession
from app.schemas.session import (
    GuestSessionCreateRequest,
    GuestSessionDeleteResponse,
    GuestSessionIssuedResponse,
    GuestSessionUpdateRequest,
)
from app.services.redis_service import GuestSessionService, HotConversationService

# In-memory fallback when Redis server is offline during isolated unit tests:
# session_id_str -> (session_dict, expires_at_epoch)
_LOCAL_GUEST_STORE: dict[str, tuple[dict[str, Any], float]] = {}
_EXPIRED_SESSION_IDS: set[str] = set()


def clear_guest_session_store_for_tests() -> None:
    """Clear in-memory guest session fallback state between unit tests."""
    _LOCAL_GUEST_STORE.clear()
    _EXPIRED_SESSION_IDS.clear()


def create_signed_guest_cookie_token(
    session_id: UUID | str,
    *,
    ttl_seconds: int | None = None,
    now_ts: int | None = None,
) -> tuple[str, datetime, int]:
    """Create a tamper-proof HS256-signed guest session cookie value with 7-day TTL."""
    now = now_ts if now_ts is not None else int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else settings.GUEST_SESSION_TTL_SECONDS
    exp = now + ttl
    sid_str = str(session_id)
    token = encode_hs256_jwt(
        {
            "sub": sid_str,
            "sid": sid_str,
            "role": "guest",
            "type": "guest_session",
            "iat": now,
            "exp": exp,
        }
    )
    expires_at = datetime.fromtimestamp(exp, tz=UTC)
    return token, expires_at, ttl


def verify_signed_guest_cookie_token(cookie_token: str) -> UUID:
    """Validate HMAC signature, token type, and expiration of a signed guest session cookie."""
    payload = decode_hs256_jwt(cookie_token, expected_type="guest_session")
    sid_raw = payload.get("sub") or payload.get("sid")
    try:
        return UUID(str(sid_raw))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session ID in guest session cookie.",
        ) from exc


def set_guest_cookie_and_headers(
    response: Response,
    *,
    session_id: UUID,
    cookie_token: str,
    ttl_seconds: int,
) -> None:
    """Attach signed HttpOnly guest session cookie and X-Session-ID header to response."""
    response.set_cookie(
        key=settings.GUEST_SESSION_COOKIE_NAME,
        value=cookie_token,
        max_age=ttl_seconds,
        expires=ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=(settings.ENVIRONMENT == "production"),
        path="/",
    )
    response.headers["X-Session-ID"] = str(session_id)


def clear_guest_cookie(response: Response) -> None:
    """Delete the signed guest session cookie on the client."""
    response.delete_cookie(
        key=settings.GUEST_SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=(settings.ENVIRONMENT == "production"),
    )


async def _persist_guest_state(
    session_id: UUID,
    fields: dict[str, Any],
    *,
    ttl_seconds: int,
    redis_client: Redis | None = None,
) -> dict[str, Any]:
    """Write guest session fields to Redis (SA-26) with local fallback for offline tests."""
    sid = str(session_id)
    now_epoch = time.time()

    existing_local = _LOCAL_GUEST_STORE.get(sid)
    merged: dict[str, Any] = dict(existing_local[0]) if existing_local else {}
    merged.update(fields)
    _LOCAL_GUEST_STORE[sid] = (merged, now_epoch + ttl_seconds)

    if redis_client is not None or not is_redis_temporarily_unavailable():
        try:
            guest_svc = GuestSessionService(
                redis_client=redis_client,
                default_ttl=ttl_seconds,
            )
            await guest_svc.update_fields(sid, fields, ttl_seconds=ttl_seconds)
            chat_svc = HotConversationService(
                redis_client=redis_client,
                ttl_seconds=ttl_seconds,
            )
            await chat_svc.redis.expire(chat_svc.get_key(sid), ttl_seconds)
            remote = await guest_svc.get_session(sid)
            if remote:
                merged.update(remote)
                _LOCAL_GUEST_STORE[sid] = (merged, now_epoch + ttl_seconds)
        except Exception:
            if redis_client is None:
                mark_redis_unavailable_temporarily(30.0)

    return merged


async def _load_guest_state(
    session_id: UUID,
    *,
    redis_client: Redis | None = None,
) -> dict[str, Any] | None:
    """Read guest session state from Redis with local fallback."""
    sid = str(session_id)
    if sid in _EXPIRED_SESSION_IDS:
        return None

    if redis_client is not None or not is_redis_temporarily_unavailable():
        try:
            guest_svc = GuestSessionService(
                redis_client=redis_client,
                default_ttl=settings.GUEST_SESSION_TTL_SECONDS,
            )
            remote = await guest_svc.get_session(sid)
            if remote:
                return remote
        except Exception:
            if redis_client is None:
                mark_redis_unavailable_temporarily(30.0)

    local_entry = _LOCAL_GUEST_STORE.get(sid)
    if local_entry is None:
        return None
    data, exp_epoch = local_entry
    if exp_epoch < time.time():
        _LOCAL_GUEST_STORE.pop(sid, None)
        return None
    return dict(data)


async def _count_hot_turns(
    session_id: UUID,
    *,
    redis_client: Redis | None = None,
) -> int:
    if redis_client is None and is_redis_temporarily_unavailable():
        return 0
    try:
        chat_svc = HotConversationService(redis_client=redis_client)
        turns = await chat_svc.get_history(str(session_id))
        return len(turns)
    except Exception:
        if redis_client is None:
            mark_redis_unavailable_temporarily(30.0)
        return 0


async def _sync_db_guest_tracker(
    db: AsyncSession | None,
    *,
    session_id: UUID,
    status_val: str,
    preferred_language: str | None,
    expires_at: datetime,
) -> None:
    """Maintain PII-free technical record in core.guest_sessions (SA-13 / SA-17)."""
    if db is None:
        return
    now = datetime.now(UTC)
    try:
        record = await db.get(GuestSession, session_id)
        if record is None:
            record = GuestSession(
                id=session_id,
                status=status_val,
                preferred_language=preferred_language,
                last_activity_at=now,
                expires_at=expires_at,
                created_at=now,
            )
            db.add(record)
        else:
            record.status = status_val
            if preferred_language is not None:
                record.preferred_language = preferred_language
            record.last_activity_at = now
            record.expires_at = expires_at
        await db.commit()
    except Exception:
        return


def _resolve_session_id_from_request(
    request: Request,
    *,
    allow_missing: bool = False,
) -> UUID | None:
    """Extract and validate guest session ID from signed cookie or X-Session-ID header."""
    cookie_token = request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
    header_sid = request.headers.get("x-session-id")

    if cookie_token:
        sid = verify_signed_guest_cookie_token(cookie_token)
        if header_sid:
            try:
                hdr_uuid = UUID(header_sid.strip())
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid X-Session-ID header format.",
                ) from exc
            if hdr_uuid != sid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="X-Session-ID header does not match signed session cookie.",
                )
        return sid

    if header_sid:
        try:
            return UUID(header_sid.strip())
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Session-ID header format.",
            ) from exc

    if allow_missing:
        return None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Anonymous guest session cookie or X-Session-ID header is required.",
    )


async def create_or_refresh_guest_session(
    request: Request,
    response: Response,
    *,
    payload: GuestSessionCreateRequest | None = None,
    db: AsyncSession | None = None,
    redis_client: Redis | None = None,
) -> GuestSessionIssuedResponse:
    """Issue a new signed anonymous guest session (or refresh existing cookie if valid)."""
    req = payload or GuestSessionCreateRequest()
    ttl = settings.GUEST_SESSION_TTL_SECONDS
    now = datetime.now(UTC)

    existing_sid: UUID | None = None
    if not req.force_new:
        cookie_token = request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME)
        if cookie_token:
            try:
                cand_sid = verify_signed_guest_cookie_token(cookie_token)
                if str(cand_sid) not in _EXPIRED_SESSION_IDS:
                    existing_sid = cand_sid
            except HTTPException:
                existing_sid = None

    session_id = existing_sid or uuid4()
    _EXPIRED_SESSION_IDS.discard(str(session_id))

    token, expires_at, ttl = create_signed_guest_cookie_token(
        session_id,
        ttl_seconds=ttl,
    )

    prior = await _load_guest_state(session_id, redis_client=redis_client) or {}
    pref_lang = req.preferred_language or prior.get("preferred_language")
    step_val = str(prior.get("step") or "ANONYMOUS")
    school_target = prior.get("school_target")

    fields: dict[str, Any] = {
        "session_id": str(session_id),
        "status": "ANONYMOUS",
        "step": step_val,
        "created_at": prior.get("created_at") or now.isoformat(),
        "last_activity_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if pref_lang is not None:
        fields["preferred_language"] = pref_lang
    if school_target is not None:
        fields["school_target"] = school_target

    session_data = await _persist_guest_state(
        session_id,
        fields,
        ttl_seconds=ttl,
        redis_client=redis_client,
    )
    await _sync_db_guest_tracker(
        db,
        session_id=session_id,
        status_val="ACTIVE",
        preferred_language=pref_lang,
        expires_at=expires_at,
    )
    set_guest_cookie_and_headers(
        response,
        session_id=session_id,
        cookie_token=token,
        ttl_seconds=ttl,
    )
    turns = await _count_hot_turns(session_id, redis_client=redis_client)

    return GuestSessionIssuedResponse(
        session_id=session_id,
        status="ANONYMOUS",
        expires_at=expires_at,
        ttl_seconds=ttl,
        preferred_language=pref_lang,
        step=step_val,
        school_target=school_target,
        turn_count=turns,
        signed_cookie_issued=True,
        session_data=session_data,
    )


async def validate_and_touch_guest_session(
    request: Request,
    response: Response,
    *,
    db: AsyncSession | None = None,
    redis_client: Redis | None = None,
    auto_create: bool = False,
) -> GuestSessionIssuedResponse:
    """Validate signed guest session cookie/header and refresh sliding 7-day TTL."""
    sid = _resolve_session_id_from_request(request, allow_missing=auto_create)
    if sid is None:
        return await create_or_refresh_guest_session(
            request,
            response,
            db=db,
            redis_client=redis_client,
        )

    if str(sid) in _EXPIRED_SESSION_IDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Anonymous guest session has expired or been terminated.",
        )

    state = await _load_guest_state(sid, redis_client=redis_client)
    has_signed_cookie = bool(request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME))
    if state is None and not has_signed_cookie:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Anonymous guest session not found or expired.",
        )

    current = state or {}
    if current.get("status") in {"EXPIRED", "MERGED"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Guest session is no longer active (status={current.get('status')}).",
        )

    now = datetime.now(UTC)
    ttl = settings.GUEST_SESSION_TTL_SECONDS
    token, expires_at, ttl = create_signed_guest_cookie_token(sid, ttl_seconds=ttl)

    pref_lang = current.get("preferred_language")
    step_val = str(current.get("step") or "ANONYMOUS")
    school_target = current.get("school_target")

    updated_fields: dict[str, Any] = {
        "session_id": str(sid),
        "status": "ANONYMOUS",
        "step": step_val,
        "created_at": current.get("created_at") or now.isoformat(),
        "last_activity_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if pref_lang is not None:
        updated_fields["preferred_language"] = pref_lang
    if school_target is not None:
        updated_fields["school_target"] = school_target

    session_data = await _persist_guest_state(
        sid,
        updated_fields,
        ttl_seconds=ttl,
        redis_client=redis_client,
    )
    await _sync_db_guest_tracker(
        db,
        session_id=sid,
        status_val="ACTIVE",
        preferred_language=pref_lang,
        expires_at=expires_at,
    )
    set_guest_cookie_and_headers(
        response,
        session_id=sid,
        cookie_token=token,
        ttl_seconds=ttl,
    )
    turns = await _count_hot_turns(sid, redis_client=redis_client)

    return GuestSessionIssuedResponse(
        session_id=sid,
        status="ANONYMOUS",
        expires_at=expires_at,
        ttl_seconds=ttl,
        preferred_language=pref_lang,
        step=step_val,
        school_target=school_target,
        turn_count=turns,
        signed_cookie_issued=True,
        session_data=session_data,
    )


async def update_guest_session(
    request: Request,
    response: Response,
    payload: GuestSessionUpdateRequest,
    *,
    db: AsyncSession | None = None,
    redis_client: Redis | None = None,
) -> GuestSessionIssuedResponse:
    """Apply partial guest session updates (language, step, school_target, facts) + refresh TTL."""
    current_resp = await validate_and_touch_guest_session(
        request,
        response,
        db=db,
        redis_client=redis_client,
        auto_create=False,
    )
    sid = current_resp.session_id
    ttl = settings.GUEST_SESSION_TTL_SECONDS
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl)

    patch_fields: dict[str, Any] = {
        "last_activity_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if payload.preferred_language is not None:
        patch_fields["preferred_language"] = payload.preferred_language
    if payload.step is not None:
        patch_fields["step"] = payload.step
    if payload.school_target is not None:
        patch_fields["school_target"] = payload.school_target
    if payload.declared_facts is not None:
        existing_facts = current_resp.session_data.get("declared_facts")
        merged_facts = dict(existing_facts) if isinstance(existing_facts, dict) else {}
        merged_facts.update(payload.declared_facts)
        patch_fields["declared_facts"] = merged_facts

    session_data = await _persist_guest_state(
        sid,
        patch_fields,
        ttl_seconds=ttl,
        redis_client=redis_client,
    )
    pref_lang = session_data.get("preferred_language")
    await _sync_db_guest_tracker(
        db,
        session_id=sid,
        status_val="ACTIVE",
        preferred_language=pref_lang,
        expires_at=expires_at,
    )

    return GuestSessionIssuedResponse(
        session_id=sid,
        status="ANONYMOUS",
        expires_at=expires_at,
        ttl_seconds=ttl,
        preferred_language=pref_lang,
        step=str(session_data.get("step") or "ANONYMOUS"),
        school_target=session_data.get("school_target"),
        turn_count=current_resp.turn_count,
        signed_cookie_issued=True,
        session_data=session_data,
    )


async def terminate_guest_session(
    request: Request,
    response: Response,
    *,
    db: AsyncSession | None = None,
    redis_client: Redis | None = None,
) -> GuestSessionDeleteResponse:
    """Expire and purge an anonymous guest session from Redis and clear client cookie."""
    sid = _resolve_session_id_from_request(request, allow_missing=False)
    assert sid is not None
    sid_str = str(sid)
    _LOCAL_GUEST_STORE.pop(sid_str, None)
    _EXPIRED_SESSION_IDS.add(sid_str)

    if redis_client is not None or not is_redis_temporarily_unavailable():
        try:
            guest_svc = GuestSessionService(redis_client=redis_client)
            chat_svc = HotConversationService(redis_client=redis_client)
            await guest_svc.delete_session(sid_str)
            await chat_svc.clear_history(sid_str)
        except Exception:
            if redis_client is None:
                mark_redis_unavailable_temporarily(30.0)

    await _sync_db_guest_tracker(
        db,
        session_id=sid,
        status_val="EXPIRED",
        preferred_language=None,
        expires_at=datetime.now(UTC),
    )
    clear_guest_cookie(response)

    return GuestSessionDeleteResponse(
        session_id=sid,
        status="EXPIRED",
        expired=True,
    )
