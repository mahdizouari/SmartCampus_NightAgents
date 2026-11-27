"""Authentication, Guest Sessions, OTP, Google OAuth 2.0, JWT, and INPDP consent (SA-25 & SA-28)."""

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
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
from app.schemas.session import (
    GuestSessionCreateRequest,
    GuestSessionDeleteResponse,
    GuestSessionIssuedResponse,
    GuestSessionUpdateRequest,
    SessionMergeRequest,
    SessionMergeResponse,
)
from app.services import auth_service as svc
from app.services import guest_session_service as guest_svc

router = APIRouter()


@router.post(
    "/guest-session",
    response_model=GuestSessionIssuedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue or refresh a signed anonymous guest session (7-day sliding TTL)",
)
async def create_guest_session(
    request: Request,
    response: Response,
    payload: GuestSessionCreateRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> GuestSessionIssuedResponse:
    return await guest_svc.create_or_refresh_guest_session(
        request,
        response,
        payload=payload,
        db=db,
    )


@router.get(
    "/guest-session",
    response_model=GuestSessionIssuedResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate signed anonymous guest session cookie and refresh its 7-day sliding TTL",
)
async def get_guest_session(
    request: Request,
    response: Response,
    auto_create: bool = Query(
        default=False,
        description="Automatically create a temporary session if no cookie/header is present",
    ),
    db: AsyncSession = Depends(get_db),
) -> GuestSessionIssuedResponse:
    return await guest_svc.validate_and_touch_guest_session(
        request,
        response,
        db=db,
        auto_create=auto_create,
    )


@router.patch(
    "/guest-session",
    response_model=GuestSessionIssuedResponse,
    status_code=status.HTTP_200_OK,
    summary="Update anonymous guest session state (language, step, facts) and refresh 7-day TTL",
)
async def patch_guest_session(
    payload: GuestSessionUpdateRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GuestSessionIssuedResponse:
    return await guest_svc.update_guest_session(
        request,
        response,
        payload,
        db=db,
    )


@router.delete(
    "/guest-session",
    response_model=GuestSessionDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Terminate anonymous guest session, purge Redis state, and clear signed cookie",
)
async def delete_guest_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GuestSessionDeleteResponse:
    return await guest_svc.terminate_guest_session(
        request,
        response,
        db=db,
    )


@router.post(
    "/merge-session",
    response_model=SessionMergeResponse,
    status_code=status.HTTP_200_OK,
    summary="Merge an anonymous visitor session (Redis history + profile) into candidate account",
)
async def merge_session(
    payload: SessionMergeRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(get_current_actor),
) -> SessionMergeResponse:
    return await guest_svc.merge_guest_session_to_candidate(
        db,
        actor=actor,
        payload=payload,
        request=request,
        response=response,
    )



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
    summary="Verify a 6-digit OTP code, auto-merge Visitor session, and issue Bearer JWT tokens",
)
async def verify_otp(
    payload: OtpVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    return await svc.verify_otp(db, payload, request=request, response=response)


@router.post(
    "/oauth/google",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate via Google OAuth 2.0 ID token and auto-merge Visitor session",
)
async def google_oauth_login(
    payload: GoogleOAuthRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    return await svc.authenticate_google_oauth(db, payload, request=request, response=response)


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

