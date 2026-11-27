"""Shared FastAPI JWT creation, verification, and RBAC dependencies (SA-20 & SA-28)."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.session import ActorStage, GuestSessionIssuedResponse

_bearer_scheme = HTTPBearer(auto_error=False)
_DEFAULT_INSECURE_SECRET = "change_me_in_production_use_a_long_random_string"

CANDIDATE_ROLE_ALIASES: frozenset[str] = frozenset({"student", "candidat"})
VISITOR_ROLE_ALIASES: frozenset[str] = frozenset({"guest", "visiteur"})
STAFF_ROLE_ALIASES: frozenset[str] = frozenset({"responsable", "admin", "rh", "direction"})


def normalize_role_alias(role: str | None) -> str:
    """Normalize equivalent role aliases (candidat <-> student, visiteur <-> guest)."""
    cleaned = (role or "").strip().lower()
    if cleaned in CANDIDATE_ROLE_ALIASES:
        return "student"
    if not cleaned or cleaned in VISITOR_ROLE_ALIASES:
        return "guest"
    return cleaned


def is_candidate_role(role: str | None) -> bool:
    """Return True when the role represents an authenticated candidate/student."""
    return (role or "").strip().lower() in CANDIDATE_ROLE_ALIASES


def resolve_actor_stage(role: str | None, *, is_authenticated: bool = True) -> ActorStage:
    """Resolve the simplified actor stage: VISITEUR -> CANDIDAT or RESPONSABLE."""
    if not is_authenticated:
        return "VISITEUR"
    cleaned = (role or "").strip().lower()
    if not cleaned or cleaned in VISITOR_ROLE_ALIASES:
        return "VISITEUR"
    if cleaned in CANDIDATE_ROLE_ALIASES:
        return "CANDIDAT"
    return "RESPONSABLE"


def _expand_role_aliases(roles: Iterable[str]) -> set[str]:
    expanded = {r.strip().lower() for r in roles if r and r.strip()}
    if expanded & CANDIDATE_ROLE_ALIASES:
        expanded |= CANDIDATE_ROLE_ALIASES
    if expanded & VISITOR_ROLE_ALIASES:
        expanded |= VISITOR_ROLE_ALIASES
    return expanded


@dataclass(frozen=True)
class CurrentActor:
    """Authenticated actor extracted from the verified JWT claims."""

    account_id: str
    role: str
    school_scope: str | None = None
    candidate_id: str | None = None
    email: str | None = None

    @property
    def actor_stage(self) -> ActorStage:
        """Canonical actor stage (CANDIDAT or RESPONSABLE for authenticated actors)."""
        return resolve_actor_stage(self.role, is_authenticated=True)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _ensure_production_secret_configured() -> None:
    if (
        settings.ENVIRONMENT == "production"
        and settings.SECRET_KEY == _DEFAULT_INSECURE_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server SECRET_KEY is not configured for production.",
        )


def encode_hs256_jwt(payload: dict[str, Any]) -> str:
    """Sign and encode a standard HS256 JWT using settings.SECRET_KEY."""
    _ensure_production_secret_configured()
    header_b64 = _b64url_encode(
        json.dumps({"alg": settings.JWT_ALGORITHM, "typ": "JWT"}, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def create_access_token(
    *,
    account_id: str,
    role: str,
    email: str | None = None,
    school_scope: str | None = None,
    candidate_id: str | None = None,
    expires_in_seconds: int | None = None,
) -> str:
    """Create a signed short-lived access JWT carrying SmartCampus RBAC/RLS claims."""
    now = int(time.time())
    ttl = (
        expires_in_seconds
        if expires_in_seconds is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    claims: dict[str, Any] = {
        "sub": str(account_id),
        "role": role.lower(),
        "type": "access",
        "iat": now,
        "exp": now + ttl,
    }
    if email:
        claims["email"] = email
    if school_scope:
        claims["school_scope"] = school_scope
    if candidate_id:
        claims["candidate_id"] = str(candidate_id)
    return encode_hs256_jwt(claims)


def create_refresh_token(*, account_id: str, role: str) -> str:
    """Create a signed refresh JWT for token renewal."""
    now = int(time.time())
    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    return encode_hs256_jwt(
        {
            "sub": str(account_id),
            "role": role.lower(),
            "type": "refresh",
            "iat": now,
            "exp": now + ttl,
        }
    )


def decode_hs256_jwt(token: str, *, expected_type: str | None = "access") -> dict[str, Any]:
    """Verify and decode a standard HS256 JWT using settings.SECRET_KEY."""
    _ensure_production_secret_configured()

    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token format.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != settings.JWT_ALGORITHM:
            raise ValueError("Unsupported JWT algorithm")
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"{header_b64}.{payload_b64}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise ValueError("Signature mismatch")
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or malformed authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    exp = payload.get("exp")
    if exp is not None and isinstance(exp, (int, float)) and exp < time.time():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not payload.get("sub") or not payload.get("role"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required 'sub' or 'role' claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_type = payload.get("type", "access")
    if expected_type is not None and token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected '{expected_type}' token, got '{token_type}'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


async def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentActor:
    """Extract and validate the authenticated actor from the Bearer JWT."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_hs256_jwt(credentials.credentials, expected_type="access")
    return CurrentActor(
        account_id=str(payload["sub"]),
        role=str(payload["role"]).lower(),
        school_scope=payload.get("school_scope"),
        candidate_id=payload.get("candidate_id"),
        email=payload.get("email"),
    )


def require_roles(*allowed_roles: str) -> Callable[..., Any]:
    """Build a FastAPI dependency requiring the actor to hold one of allowed_roles.

    Transparently accepts equivalent aliases (`student` <-> `candidat`, `guest` <-> `visiteur`).
    """
    normalized = _expand_role_aliases(allowed_roles)

    async def _guard(actor: CurrentActor = Depends(get_current_actor)) -> CurrentActor:
        if actor.role.lower() not in normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role in: {sorted(normalized)}.",
            )
        return actor

    return _guard


async def require_catalog_admin(
    actor: CurrentActor = Depends(get_current_actor),
) -> CurrentActor:
    """Enforce PLATFORM_ADMIN RBAC role for top-level school registry mutations (SA-5 / SA-20)."""
    if actor.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PLATFORM_ADMIN privileges are required to modify the academic catalog.",
        )
    return actor


async def require_catalog_manager(
    actor: CurrentActor = Depends(get_current_actor),
) -> CurrentActor:
    """Allow school-scoped Responsable or Admin to manage catalog sub-resources."""
    if actor.role not in {"responsable", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Responsable or Admin privileges are required to modify catalog entries.",
        )
    return actor


async def get_current_guest_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GuestSessionIssuedResponse:
    """Validate the visitor's signed guest session cookie/header and refresh its 7-day TTL."""
    from app.services.guest_session_service import validate_and_touch_guest_session

    return await validate_and_touch_guest_session(
        request,
        response,
        db=db,
        auto_create=False,
    )


async def get_or_create_guest_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GuestSessionIssuedResponse:
    """Validate existing guest session or auto-create a signed 7-day session when starting chat."""
    from app.services.guest_session_service import validate_and_touch_guest_session

    return await validate_and_touch_guest_session(
        request,
        response,
        db=db,
        auto_create=True,
    )

