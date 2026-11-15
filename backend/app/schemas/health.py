from datetime import UTC, datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Schema for service health check response."""

    status: str = Field(default="ok", description="Service status", examples=["ok"])
    app_name: str = Field(..., description="Application name", examples=["SmartCampus AI"])
    version: str = Field(..., description="Application version", examples=["0.1.0"])
    environment: str = Field(..., description="Runtime environment", examples=["development"])
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of the health check in UTC",
    )
