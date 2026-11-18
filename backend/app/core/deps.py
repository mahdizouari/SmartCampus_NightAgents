"""Shared FastAPI security and RBAC dependencies (ready for SA-28 JWT/OAuth integration)."""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)
_DEFAULT_INSECURE_SECRET = "change_me_in_production_use_a_long_random_string"


@dataclass(frozen=True)
class CurrentActor:
    """Authenticated actor extracted from the verified JWT claims."""

    account_id: str
    role: str
    school_scope: str | None = None


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_hs256_jwt(token: str) -> dict:
    """Verify and decode a standard HS256 JWT using settings.SECRET_KEY."""
    if (
        settings.ENVIRONMENT == "production"
        and settings.SECRET_KEY == _DEFAULT_INSECURE_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server SECRET_KEY is not configured for production.",
        )

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

    payload = decode_hs256_jwt(credentials.credentials)
    return CurrentActor(
        account_id=str(payload["sub"]),
        role=str(payload["role"]).lower(),
        school_scope=payload.get("school_scope"),
    )


async def require_catalog_admin(
    actor: CurrentActor = Depends(get_current_actor),
) -> CurrentActor:
    """Enforce PLATFORM_ADMIN RBAC role for catalog management mutations (SA-5 / SA-20)."""
    if actor.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PLATFORM_ADMIN privileges are required to modify the academic catalog.",
        )
    return actor

