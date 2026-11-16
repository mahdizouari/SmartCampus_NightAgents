import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.health import get_database_health, get_health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import close_db_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application lifecycle: startup and graceful shutdown."""
    logger.info("Starting up %s (env: %s)...", settings.PROJECT_NAME, settings.ENVIRONMENT)
    yield
    logger.info("Shutting down %s, closing database connection pool...", settings.PROJECT_NAME)
    await close_db_pool()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Backend API for SmartCampus AI — "
        "Multilingual agentic orientation & admission platform."
    ),
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS Middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Root-level health check endpoints
app.add_api_route(
    "/health",
    get_health,
    methods=["GET"],
    tags=["Health"],
    summary="Root Health Check",
    description="Direct service health check endpoint returning 200 OK.",
)

app.add_api_route(
    "/health/db",
    get_database_health,
    methods=["GET"],
    tags=["Health"],
    summary="Root Database Health Check",
    description=(
        "Verifies PostgreSQL connection, pgvector extension, "
        "schemas, and connection pool status."
    ),
)

# Include v1 API routes
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs": "/docs",
        "health": "/health",
        "health_db": "/health/db",
        "api_v1": settings.API_V1_STR,
    }
