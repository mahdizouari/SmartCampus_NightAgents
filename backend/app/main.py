import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.health import (
    get_database_health,
    get_health,
    get_redis_health,
)
from app.api.v1.endpoints.voice_notes import router as voice_notes_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import close_db_pool
from app.core.rate_limit import RateLimitMiddleware
from app.core.redis import close_redis_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application lifecycle: startup and graceful shutdown."""
    logger.info("Starting up %s (env: %s)...", settings.PROJECT_NAME, settings.ENVIRONMENT)
    yield
    logger.info("Shutting down %s, closing database & redis pools...", settings.PROJECT_NAME)
    await close_db_pool()
    await close_redis_pool()


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

# Enforce per-session and per-IP rate limiting with IETF RateLimit-* headers (SA-25)
app.add_middleware(RateLimitMiddleware)

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

app.add_api_route(
    "/health/redis",
    get_redis_health,
    methods=["GET"],
    tags=["Health"],
    summary="Root Redis Health Check",
    description="Verifies Redis 7 connection and memory usage.",
)

# Include v1 API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Also expose voice notes directly under /api/voice-notes for direct contract compliance
app.include_router(
    voice_notes_router,
    prefix="/api/voice-notes",
    tags=["Voice Notes & Whisper"],
)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs": "/docs",
        "health": "/health",
        "health_db": "/health/db",
        "health_redis": "/health/redis",
        "api_v1": settings.API_V1_STR,
    }
