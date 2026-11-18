from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    """Schema for returning guest session state."""

    guest_id: str
    session_data: dict[str, Any]
    ttl_seconds: int | None = None
