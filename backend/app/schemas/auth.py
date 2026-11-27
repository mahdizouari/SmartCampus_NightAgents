"""Pydantic v2 request and response schemas for authentication, OAuth, and INPDP consent (SA-28)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.session import ActorStage

OtpChannel = Literal["email", "sms"]
ConsentPurpose = Literal["ORIENTATION_PROFILE", "DOCUMENT_OCR", "ADMISSION_FOLLOWUP"]


class OtpSendRequest(BaseModel):
    """Supports both api_contract.md (channel + destination) and frontend (email)."""

    channel: OtpChannel = "email"
    destination: str | None = Field(default=None, min_length=3, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)

    @model_validator(mode="after")
    def resolve_destination(self) -> "OtpSendRequest":
        target = (self.destination or self.email or "").strip()
        if not target:
            raise ValueError("Either 'destination' or 'email' must be provided.")
        if self.channel == "email" and "@" not in target:
            raise ValueError("Invalid email address format.")
        self.destination = target.lower() if self.channel == "email" else target
        return self


class OtpSendResponse(BaseModel):
    success: bool = True
    message: str = "OTP sent successfully"
    expires_in_seconds: int
    retry_after_seconds: int = 60
    dev_otp_code: str | None = Field(
        default=None,
        description="Only populated in development/test mode to ease local OTP testing.",
    )


class OtpVerifyRequest(BaseModel):
    """Supports both api_contract.md (channel + destination + code) and frontend (email + code)."""

    channel: OtpChannel = "email"
    destination: str | None = Field(default=None, min_length=3, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    anonymous_session_id: UUID | None = Field(
        default=None,
        description="Optional guest session to automatically associate upon login/signup (SA-29)",
    )
    consent_merge: bool = Field(
        default=True,
        description="Explicit agreement to associate guest conversation upon verification",
    )

    @model_validator(mode="after")
    def resolve_destination(self) -> "OtpVerifyRequest":
        target = (self.destination or self.email or "").strip()
        if not target:
            raise ValueError("Either 'destination' or 'email' must be provided.")
        if self.channel == "email" and "@" not in target:
            raise ValueError("Invalid email address format.")
        self.destination = target.lower() if self.channel == "email" else target
        return self


class GoogleOAuthRequest(BaseModel):
    """Google OAuth 2.0 ID token exchange request."""

    id_token: str = Field(..., min_length=10)
    anonymous_session_id: UUID | None = Field(
        default=None,
        description="Optional guest session to automatically associate upon Google sign-in (SA-29)",
    )
    consent_merge: bool = Field(
        default=True,
        description="Explicit agreement to associate guest conversation upon Google sign-in",
    )


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class AuthUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    fullName: str
    role: str
    actor_stage: ActorStage = "CANDIDAT"
    school_scope: str | None = None
    school: str | None = None
    candidate_id: UUID | None = None
    status: str = "AUTHENTICATED"
    isAnonymous: bool = False


class AuthTokenResponse(BaseModel):
    """Compatible with both api_contract.md (access_token) and frontend (token)."""

    access_token: str
    token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    actor_stage: ActorStage = "CANDIDAT"
    session_merged: bool = False
    turns_merged: int = 0
    user: AuthUserSummary


class ConsentCreateRequest(BaseModel):
    """Explicit INPDP (Law 2004-63) consent payload (api_contract.md §1.5)."""

    terms_accepted: bool
    privacy_policy_accepted: bool
    transcript_processing_accepted: bool = True
    policy_version: str = Field(default="2026.1", min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_mandatory_consents(self) -> "ConsentCreateRequest":
        if not self.terms_accepted or not self.privacy_policy_accepted:
            raise ValueError(
                "Both terms_accepted and privacy_policy_accepted must be true to proceed."
            )
        return self


class ConsentResponse(BaseModel):
    consent_id: UUID
    logged_at: datetime
    status: Literal["VALID"] = "VALID"
    purposes_granted: list[ConsentPurpose]

