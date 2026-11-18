"""Pydantic schemas for Redis hot conversation history and signed anonymous guest sessions."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

GuestLanguage = Literal["FR", "DERJA", "ARABIZI"]
GuestPipelineStep = Literal["ANONYMOUS", "PROFILING", "ELIGIBLE", "INELIGIBLE", "ORIENTED"]
GuestSchoolCode = Literal["iit", "isb", "isa"]


class MessageTurn(BaseModel):
    """Schema for a single conversation turn stored in Redis."""

    role: Literal["user", "assistant"] = Field(
        ...,
        description="Role of the message sender",
        examples=["user", "assistant"],
    )
    content: str = Field(
        ...,
        description="Content of the message turn",
        examples=["Quelles sont les conditions d'admission à l'IIT ?"],
    )
    timestamp: float = Field(
        default_factory=lambda: datetime.now(UTC).timestamp(),
        description="POSIX timestamp in seconds",
    )


class HotConversationResponse(BaseModel):
    """Schema for returning conversation history."""

    session_id: str
    messages: list[MessageTurn]
    total_messages: int
    ttl_seconds: int | None = None


class GuestSessionResponse(BaseModel):
    """Schema for returning raw Redis guest session state (SA-26)."""

    guest_id: str
    session_data: dict[str, Any]
    ttl_seconds: int | None = None


class GuestSessionCreateRequest(BaseModel):
    """Optional parameters when initializing an anonymous guest session (SA-25)."""

    preferred_language: GuestLanguage | None = Field(
        default=None,
        description="Initial language/dialect preference (FR, DERJA, ARABIZI)",
    )
    force_new: bool = Field(
        default=False,
        description="When true, always issues a fresh session ID even if a cookie exists",
    )


class GuestSessionUpdateRequest(BaseModel):
    """Partial state update for an active anonymous guest session (SA-25)."""

    preferred_language: GuestLanguage | None = None
    step: GuestPipelineStep | None = None
    school_target: GuestSchoolCode | None = None
    declared_facts: dict[str, Any] | None = None


class GuestSessionIssuedResponse(BaseModel):
    """Response schema for issued/validated anonymous guest sessions (api_contract.md §1.1)."""

    session_id: UUID
    status: Literal["ANONYMOUS"] = "ANONYMOUS"
    expires_at: datetime
    ttl_seconds: int = 604800
    preferred_language: GuestLanguage | None = None
    step: str = "ANONYMOUS"
    school_target: str | None = None
    turn_count: int = 0
    signed_cookie_issued: bool = True
    session_data: dict[str, Any] = Field(default_factory=dict)


class GuestSessionDeleteResponse(BaseModel):
    """Response returned when an anonymous guest session is terminated."""

    session_id: UUID
    status: Literal["EXPIRED"] = "EXPIRED"
    expired: bool = True
    message: str = "Anonymous guest session terminated and cookie cleared."
