"""Authentication, OTP, Google OAuth 2.0, JWT issuance, and INPDP consent service (SA-28)."""

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    CurrentActor,
    create_access_token,
    create_refresh_token,
    decode_hs256_jwt,
    is_candidate_role,
    resolve_actor_stage,
)
from app.models.users import (
    Account,
    AccountRole,
    AuditLog,
    Candidate,
    CandidateProfile,
    Consent,
    Role,
)
from app.schemas.auth import (
    AuthTokenResponse,
    AuthUserSummary,
    ConsentCreateRequest,
    ConsentPurpose,
    ConsentResponse,
    GoogleOAuthRequest,
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    RefreshTokenRequest,
)

logger = logging.getLogger(__name__)

_MAX_OTP_ATTEMPTS = 5
_OTP_COOLDOWN_SECONDS = 60


@dataclass
class _OtpChallenge:
    code_hash: str
    salt: str
    expires_at: float
    sent_at: float
    attempts: int = 0


# In-memory OTP store (keyed by "{channel}:{destination}").
_OTP_STORE: dict[str, _OtpChallenge] = {}

_ACTOR_TYPE_BY_ROLE: dict[str, str] = {
    "guest": "VISITOR",
    "visitor": "VISITOR",
    "visiteur": "VISITOR",
    "student": "STUDENT",
    "candidate": "CANDIDATE",
    "candidat": "CANDIDATE",
    "rh": "RH",
    "responsable": "RESPONSABLE",
    "direction": "DIRECTION",
    "admin": "ADMIN",
}


def _hash_otp(code: str, salt: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{salt}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _hash_ip(raw_ip: str | None) -> str | None:
    if not raw_ip:
        return None
    return hashlib.sha256(f"{settings.SECRET_KEY}:{raw_ip}".encode()).hexdigest()


def clear_otp_store_for_tests() -> None:
    """Reset the in-memory OTP store between unit tests."""
    _OTP_STORE.clear()


async def send_otp(payload: OtpSendRequest) -> OtpSendResponse:
    """Generate and store a 6-digit OTP challenge with a 60-second resend cooldown."""
    destination = payload.destination or ""
    key = f"{payload.channel}:{destination}"
    now = time.time()

    existing = _OTP_STORE.get(key)
    if existing and existing.expires_at > now and (now - existing.sent_at) < _OTP_COOLDOWN_SECONDS:
        remaining = int(_OTP_COOLDOWN_SECONDS - (now - existing.sent_at)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining}s before requesting another OTP.",
            headers={"Retry-After": str(remaining)},
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    _OTP_STORE[key] = _OtpChallenge(
        code_hash=_hash_otp(code, salt),
        salt=salt,
        expires_at=now + settings.OTP_EXPIRE_SECONDS,
        sent_at=now,
    )

    return OtpSendResponse(
        success=True,
        message="OTP sent successfully",
        expires_in_seconds=settings.OTP_EXPIRE_SECONDS,
        retry_after_seconds=_OTP_COOLDOWN_SECONDS,
        dev_otp_code=code if settings.ENVIRONMENT != "production" else None,
    )


async def _ensure_role(db: AsyncSession, role_code: str) -> Role:
    res = await db.execute(select(Role).where(Role.code == role_code))
    role = res.scalar_one_or_none()
    if role is not None:
        return role
    role = Role(
        code=role_code,
        label=role_code.capitalize(),
        description=f"Auto-initialized {role_code} role",
    )
    db.add(role)
    await db.flush()
    return role


async def _resolve_account_role_and_candidate(
    db: AsyncSession,
    account: Account,
) -> tuple[str, str | None, Candidate | None]:
    """Return (role_code, school_scope, candidate_or_none) with student defaults."""
    ar_res = await db.execute(
        select(AccountRole).where(AccountRole.account_id == account.id)
    )
    acc_role = ar_res.scalars().first()

    if acc_role is None:
        student_role = await _ensure_role(db, "student")
        acc_role = AccountRole(
            account_id=account.id,
            role_id=student_role.id,
            school_scope=None,
            responsibility_scope="ALL",
        )
        db.add(acc_role)
        await db.flush()
        role_code = "student"
        school_scope = None
    else:
        role_obj = await db.get(Role, acc_role.role_id)
        role_code = role_obj.code if role_obj else "student"
        school_scope = acc_role.school_scope

    candidate: Candidate | None = None
    if is_candidate_role(role_code):
        cand_res = await db.execute(
            select(Candidate).where(Candidate.account_id == account.id)
        )
        candidate = cand_res.scalar_one_or_none()
        if candidate is None:
            year = datetime.now(UTC).year
            suffix = secrets.token_hex(3).upper()
            candidate = Candidate(
                account_id=account.id,
                candidate_number=f"NAU-{year}-{suffix}",
                email=account.email,
                status="AUTHENTICATED",
                owner="JARVIS",
            )
            db.add(candidate)
            await db.flush()
            db.add(
                CandidateProfile(
                    candidate_id=candidate.id,
                    facts={},
                    summary="",
                )
            )
            await db.flush()
        elif candidate.status in {"ANONYMOUS", "PROFILING", "ELIGIBLE", "ORIENTED"}:
            candidate.status = "AUTHENTICATED"
            candidate.updated_at = datetime.now(UTC)

    return role_code, school_scope, candidate


def _build_token_response(
    account: Account,
    role_code: str,
    school_scope: str | None,
    candidate: Candidate | None,
    *,
    session_merged: bool = False,
    turns_merged: int = 0,
) -> AuthTokenResponse:
    cand_id = str(candidate.id) if candidate else None
    cand_status = candidate.status if candidate else "AUTHENTICATED"
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    actor_stage = resolve_actor_stage(role_code, is_authenticated=True)

    access_token = create_access_token(
        account_id=str(account.id),
        role=role_code,
        email=account.email,
        school_scope=school_scope,
        candidate_id=cand_id,
        expires_in_seconds=expires_in,
    )
    refresh_token = create_refresh_token(
        account_id=str(account.id),
        role=role_code,
    )
    return AuthTokenResponse(
        access_token=access_token,
        token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=expires_in,
        actor_stage=actor_stage,
        session_merged=session_merged,
        turns_merged=turns_merged,
        user=AuthUserSummary(
            id=account.id,
            email=account.email,
            full_name=account.full_name,
            fullName=account.full_name,
            role=role_code,
            actor_stage=actor_stage,
            school_scope=school_scope,
            school=school_scope.upper() if school_scope else None,
            candidate_id=candidate.id if candidate else None,
            status=cand_status,
            isAnonymous=False,
        ),
    )


async def _attempt_auto_guest_session_merge(
    db: AsyncSession,
    *,
    account: Account,
    role_code: str,
    school_scope: str | None,
    candidate: Candidate | None,
    anonymous_session_id: UUID | None,
    consent_merge: bool,
    request: Request | None = None,
    response: Response | None = None,
) -> tuple[bool, int]:
    """Automatically merge an active Visitor session (payload, signed cookie, or X-Session-ID).

    Never fails authentication when no active guest session exists or when a stale cookie
    cannot be merged; logs a warning for observability instead of failing silently.
    """
    if not consent_merge or candidate is None:
        return False, 0

    has_cookie = (
        request is not None
        and bool(request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME))
    )
    has_header = request is not None and bool(request.headers.get("x-session-id"))
    if anonymous_session_id is None and not has_cookie and not has_header:
        return False, 0

    try:
        from app.schemas.session import SessionMergeRequest
        from app.services.guest_session_service import merge_guest_session_to_candidate

        merge_actor = CurrentActor(
            account_id=str(account.id),
            role=role_code,
            school_scope=school_scope,
            candidate_id=str(candidate.id),
            email=account.email,
        )
        merge_result = await merge_guest_session_to_candidate(
            db,
            actor=merge_actor,
            payload=SessionMergeRequest(
                anonymous_session_id=anonymous_session_id,
                consent_explicit=consent_merge,
            ),
            request=request,
            response=response,
        )
        if merge_result.current_status:
            candidate.status = merge_result.current_status
        return True, merge_result.turns_merged
    except Exception as exc:
        logger.warning(
            "Visitor -> Candidate automatic guest session merge skipped for account %s: %s",
            account.id,
            exc,
        )
        return False, 0


async def _find_or_create_account(
    db: AsyncSession,
    *,
    email: str,
    phone: str | None,
    full_name: str | None,
    auth_provider: str,
    provider_subject: str | None = None,
) -> Account:
    now = datetime.now(UTC)
    res = await db.execute(select(Account).where(Account.email == email))
    account = res.scalar_one_or_none()

    if account is None:
        inferred_name = full_name or email.split("@")[0].replace(".", " ").title()
        account = Account(
            email=email,
            phone=phone,
            full_name=inferred_name,
            auth_provider=auth_provider,
            provider_subject=provider_subject,
            is_active=True,
            email_verified_at=now,
            last_login_at=now,
        )
        db.add(account)
        await db.flush()
    else:
        if not account.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated.",
            )
        account.last_login_at = now
        account.email_verified_at = account.email_verified_at or now
        if provider_subject and not account.provider_subject:
            account.provider_subject = provider_subject
        account.updated_at = now

    return account


async def verify_otp(
    db: AsyncSession,
    payload: OtpVerifyRequest,
    *,
    request: Request | None = None,
    response: Response | None = None,
) -> AuthTokenResponse:
    """Verify the 6-digit OTP, provision/fetch account & candidate, and issue JWT tokens."""
    destination = payload.destination or ""
    key = f"{payload.channel}:{destination}"
    challenge = _OTP_STORE.get(key)
    now = time.time()

    if challenge is None or challenge.expires_at < now:
        _OTP_STORE.pop(key, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP has expired or was not requested.",
        )

    challenge.attempts += 1
    if challenge.attempts > _MAX_OTP_ATTEMPTS:
        _OTP_STORE.pop(key, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Maximum OTP verification attempts exceeded. Please request a new code.",
        )

    expected_hash = _hash_otp(payload.code, challenge.salt)
    if not hmac.compare_digest(expected_hash, challenge.code_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OTP verification code.",
        )

    _OTP_STORE.pop(key, None)

    is_email = payload.channel == "email"
    email = destination if is_email else f"{destination}@sms.smartcampus.tn"
    phone = None if is_email else destination
    provider = "EMAIL_OTP" if is_email else "PHONE_OTP"

    account = await _find_or_create_account(
        db,
        email=email,
        phone=phone,
        full_name=payload.full_name,
        auth_provider=provider,
    )
    role_code, school_scope, candidate = await _resolve_account_role_and_candidate(db, account)

    db.add(
        AuditLog(
            actor_type=_ACTOR_TYPE_BY_ROLE.get(role_code, "STUDENT"),
            actor_id=str(account.id),
            action="auth.otp.verified",
            entity_type="account",
            entity_id=account.id,
            event_metadata={"channel": payload.channel, "role": role_code},
        )
    )
    await db.commit()

    session_merged, turns_merged = await _attempt_auto_guest_session_merge(
        db,
        account=account,
        role_code=role_code,
        school_scope=school_scope,
        candidate=candidate,
        anonymous_session_id=payload.anonymous_session_id,
        consent_merge=payload.consent_merge,
        request=request,
        response=response,
    )

    return _build_token_response(
        account,
        role_code,
        school_scope,
        candidate,
        session_merged=session_merged,
        turns_merged=turns_merged,
    )


async def verify_google_id_token(id_token: str) -> dict[str, Any]:
    """Validate a Google OAuth 2.0 ID token against Google's tokeninfo endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Google OAuth verification service.",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Google OAuth ID token.",
        )

    claims = resp.json()
    if settings.GOOGLE_OAUTH_CLIENT_ID and claims.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google ID token audience mismatch.",
        )

    if str(claims.get("email_verified", "")).lower() != "true" or not claims.get("email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified.",
        )

    return claims


async def authenticate_google_oauth(
    db: AsyncSession,
    payload: GoogleOAuthRequest,
    *,
    request: Request | None = None,
    response: Response | None = None,
) -> AuthTokenResponse:
    """Verify Google OAuth ID token, provision/fetch account & candidate, and issue JWTs."""
    claims = await verify_google_id_token(payload.id_token)
    email = str(claims["email"]).strip().lower()
    sub = str(claims.get("sub", "")) or None
    full_name = claims.get("name")

    account = await _find_or_create_account(
        db,
        email=email,
        phone=None,
        full_name=full_name,
        auth_provider="GOOGLE_OAUTH",
        provider_subject=sub,
    )
    role_code, school_scope, candidate = await _resolve_account_role_and_candidate(db, account)

    db.add(
        AuditLog(
            actor_type=_ACTOR_TYPE_BY_ROLE.get(role_code, "STUDENT"),
            actor_id=str(account.id),
            action="auth.oauth.google.verified",
            entity_type="account",
            entity_id=account.id,
            event_metadata={"role": role_code},
        )
    )
    await db.commit()

    session_merged, turns_merged = await _attempt_auto_guest_session_merge(
        db,
        account=account,
        role_code=role_code,
        school_scope=school_scope,
        candidate=candidate,
        anonymous_session_id=payload.anonymous_session_id,
        consent_merge=payload.consent_merge,
        request=request,
        response=response,
    )

    return _build_token_response(
        account,
        role_code,
        school_scope,
        candidate,
        session_merged=session_merged,
        turns_merged=turns_merged,
    )


async def refresh_access_token(
    db: AsyncSession,
    payload: RefreshTokenRequest,
) -> AuthTokenResponse:
    """Validate a refresh token and issue a fresh access + refresh token pair."""
    claims = decode_hs256_jwt(payload.refresh_token, expected_type="refresh")
    try:
        account_uuid = UUID(str(claims["sub"]))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token subject.",
        ) from exc

    account = await db.get(Account, account_uuid)
    if account is None or not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or inactive.",
        )

    role_code, school_scope, candidate = await _resolve_account_role_and_candidate(db, account)
    await db.commit()
    return _build_token_response(account, role_code, school_scope, candidate)


async def get_current_user_summary(
    db: AsyncSession,
    actor: CurrentActor,
) -> AuthUserSummary:
    """Return the authenticated user's current profile and role summary."""
    try:
        account_uuid = UUID(actor.account_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid account identifier in token.",
        ) from exc

    account = await db.get(Account, account_uuid)
    if account is None or not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated account not found or inactive.",
        )

    role_code, school_scope, candidate = await _resolve_account_role_and_candidate(db, account)
    await db.commit()
    return AuthUserSummary(
        id=account.id,
        email=account.email,
        full_name=account.full_name,
        fullName=account.full_name,
        role=role_code,
        actor_stage=resolve_actor_stage(role_code, is_authenticated=True),
        school_scope=school_scope,
        school=school_scope.upper() if school_scope else None,
        candidate_id=candidate.id if candidate else None,
        status=candidate.status if candidate else "AUTHENTICATED",
        isAnonymous=False,
    )


async def record_consent(
    db: AsyncSession,
    actor: CurrentActor,
    payload: ConsentCreateRequest,
    client_ip: str | None = None,
) -> ConsentResponse:
    """Record explicit INPDP (Law 2004-63) consent proofs for the authenticated candidate."""
    try:
        account_uuid = UUID(actor.account_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid account identifier in token.",
        ) from exc

    account = await db.get(Account, account_uuid)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authenticated account not found.",
        )

    _, _, candidate = await _resolve_account_role_and_candidate(db, account)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent recording is only applicable to candidate accounts.",
        )

    now = datetime.now(UTC)
    ip_hash = _hash_ip(client_ip)
    purposes: list[ConsentPurpose] = ["ORIENTATION_PROFILE", "ADMISSION_FOLLOWUP"]
    if payload.transcript_processing_accepted:
        purposes.append("DOCUMENT_OCR")

    primary_consent: Consent | None = None
    for purpose in purposes:
        consent_row = Consent(
            candidate_id=candidate.id,
            purpose=purpose,
            version=payload.policy_version,
            granted=True,
            ip_hash=ip_hash,
            given_at=now,
        )
        db.add(consent_row)
        if primary_consent is None:
            primary_consent = consent_row

    await db.flush()
    assert primary_consent is not None

    db.add(
        AuditLog(
            actor_type=_ACTOR_TYPE_BY_ROLE.get(actor.role.lower(), "STUDENT"),
            actor_id=str(account.id),
            action="auth.consent.granted",
            entity_type="consent",
            entity_id=primary_consent.id,
            event_metadata={
                "policy_version": payload.policy_version,
                "purposes": list(purposes),
            },
        )
    )
    await db.commit()
    return ConsentResponse(
        consent_id=primary_consent.id,
        logged_at=now,
        status="VALID",
        purposes_granted=purposes,
    )
