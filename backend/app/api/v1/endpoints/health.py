from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.core.database import check_db_health
from app.schemas.health import DatabaseHealthResponse, HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health Check",
    description="Returns the health status of the SmartCampus AI backend service.",
)
async def get_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
    )


@router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    summary="Database & pgvector Health Check",
    description=(
        "Verifies connection to PostgreSQL 16, pgvector extension, "
        "required schemas (core, iit, isb, isa, knowledge), and connection pooling status."
    ),
)
async def get_database_health(response: Response) -> DatabaseHealthResponse:
    health_data = await check_db_health()
    if health_data.get("status") == "disconnected":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DatabaseHealthResponse(**health_data)
