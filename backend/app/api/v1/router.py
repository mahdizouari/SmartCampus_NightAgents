from fastapi import APIRouter

from app.api.v1.endpoints import auth, catalog_management, health

api_router = APIRouter()

# Include health endpoints under /api/v1/health
api_router.include_router(health.router, tags=["Health"])

# Include authentication, OAuth, JWT & INPDP consent endpoints under /api/v1/auth (SA-28)
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"],
)

# Include admin catalog management endpoints under /api/v1/catalog (SA-20)
api_router.include_router(
    catalog_management.router,
    prefix="/catalog",
    tags=["Catalog Management"],
)

