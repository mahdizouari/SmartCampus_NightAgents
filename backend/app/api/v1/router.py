from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    catalog,
    catalog_management,
    chat,
    documents,
    health,
    knowledge,
    voice_notes,
)

api_router = APIRouter()

# Include health endpoints under /api/v1/health
api_router.include_router(health.router, tags=["Health"])

# Include authentication, OAuth, JWT & INPDP consent endpoints under /api/v1/auth (SA-28)
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"],
)

# Include VISION conversational AI & LLM endpoints under /api/v1/chat (SA-36)
api_router.include_router(
    chat.router,
    prefix="/chat",
    tags=["Chat & LLM"],
)

# Include Whisper voice-note transcription and review endpoints under /api/v1/voice-notes
api_router.include_router(
    voice_notes.router,
    prefix="/voice-notes",
    tags=["Voice Notes & Whisper"],
)

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

# Include VLM transcript extraction endpoints under /api/v1/documents (Bonus-1 / SA-42)
api_router.include_router(
    documents.router,
    prefix="/documents",
    tags=["Documents & VLM Extraction"],
)

# Include official RAG corpus document management endpoints (SA-49)
api_router.include_router(
    knowledge.router,
    prefix="/knowledge/documents",
    tags=["Knowledge Corpus Management"],
)

