"""Authentication, OTP, Google OAuth 2.0, JWT refresh, and INPDP consent REST endpoints (SA-28)."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentActor, get_current_actor
from app.schemas.auth import (
    AuthTokenResponse,
    AuthUserSummary,
    ConsentCreateRequest,
    ConsentResponse,
    GoogleOAuthRequest,
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    RefreshTokenRequest,
)
from app.services import auth_service as svc

router = APIRouter()


@router.post(
    "/otp/send",
    response_model=OtpSendResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a 6-digit OTP code via email or SMS",
)
@router.post(
    "/otp/request",
    response_model=OtpSendResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def send_otp(payload: OtpSendRequest) -> OtpSendResponse:
    return await svc.send_otp(payload)


@router.post(
    "/otp/verify",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify a 6-digit OTP code and issue Bearer JWT tokens",
)
async def verify_otp(
    payload: OtpVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    return await svc.verify_otp(db, payload)


@router.post(
    "/oauth/google",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate or register via Google OAuth 2.0 ID token",
)
async def google_oauth_login(
    payload: GoogleOAuthRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    return await svc.authenticate_google_oauth(db, payload)


@router.post(
    "/refresh",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange a valid refresh JWT for a fresh access token",
)
async def refresh_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    return await svc.refresh_access_token(db, payload)


@router.get(
    "/me",
    response_model=AuthUserSummary,
    status_code=status.HTTP_200_OK,
    summary="Return the currently authenticated user's profile and RBAC role",
)
async def get_me(
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(get_current_actor),
) -> AuthUserSummary:
    return await svc.get_current_user_summary(db, actor)


@router.post(
    "/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
    summary="Record explicit INPDP (Law 2004-63) consent before document upload",
)
async def grant_consent(
    payload: ConsentCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(get_current_actor),
) -> ConsentResponse:
    client_ip = request.client.host if request.client else None
    return await svc.record_consent(db, actor, payload, client_ip=client_ip)

