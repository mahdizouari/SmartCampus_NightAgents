from fastapi import APIRouter

from app.api.v1.endpoints import catalog, catalog_management, health

api_router = APIRouter()

# Include health endpoints under /api/v1/health
api_router.include_router(health.router, tags=["Health"])

# Include public catalog consultation endpoints under /api/v1/catalog (SA-21)
api_router.include_router(
    catalog.router,
    prefix="/catalog",
    tags=["Catalog Consultation"],
)

# Include admin catalog management endpoints under /api/v1/catalog (SA-20)
api_router.include_router(
    catalog_management.router,
    prefix="/catalog",
    tags=["Catalog Management"],
)
