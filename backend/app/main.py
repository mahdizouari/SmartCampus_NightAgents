from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.health import get_health
from app.api.v1.router import api_router
from app.core.config import settings

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

# Root-level /health endpoint (Acceptance Criteria: GET /health returns 200 OK)
app.add_api_route(
    "/health",
    get_health,
    methods=["GET"],
    tags=["Health"],
    summary="Root Health Check",
    description="Direct health check endpoint returning 200 OK.",
)

# Include v1 API routes (e.g. /api/v1/health, and future /api/v1/programs, /api/v1/auth, etc.)
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs": "/docs",
        "health": "/health",
        "api_v1": settings.API_V1_STR,
    }
