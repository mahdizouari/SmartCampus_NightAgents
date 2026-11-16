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


class PgVectorInfo(BaseModel):
    installed: bool = Field(..., description="Whether pgvector extension is installed")
    version: str | None = Field(None, description="Version of pgvector extension")


class SchemasInfo(BaseModel):
    expected: list[str] = Field(..., description="Expected schemas (core, iit, isb, isa, knowledge)")
    found: list[str] = Field(..., description="Schemas found in database")
    all_present: bool = Field(..., description="Whether all expected schemas are present")


class ConnectionPoolInfo(BaseModel):
    size: int = Field(..., description="Configured pool size")
    checkedin: int = Field(..., description="Number of connections returned to pool")
    checkedout: int = Field(..., description="Number of connections currently checked out")
    overflow: int = Field(..., description="Current overflow connection count")


class DatabaseHealthResponse(BaseModel):
    """Schema for database connectivity and configuration health response."""

    status: str = Field(..., description="Database status (connected, degraded, disconnected)")
    database: str | None = Field(None, description="Current database name")
    user: str | None = Field(None, description="Connected database user")
    postgres_version: str | None = Field(None, description="PostgreSQL version string")
    pgvector: PgVectorInfo | None = Field(None, description="pgvector extension details")
    schemas: SchemasInfo | None = Field(None, description="Schemas status")
    connection_pool: ConnectionPoolInfo = Field(..., description="Connection pool statistics")
    error: str | None = Field(None, description="Error message if disconnected")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of the health check in UTC",
    )
