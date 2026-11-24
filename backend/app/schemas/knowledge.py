"""Pydantic schemas for SA-49 official RAG corpus document management."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SchoolCodeEnum(StrEnum):
    IIT = "iit"
    ISB = "isb"
    ISA = "isa"
    NAU_COMMON = "nau_common"


class DocumentCategoryEnum(StrEnum):
    ADMISSION_RULES = "admission_rules"
    CURRICULUM = "curriculum"
    FEES_SCHEDULE = "fees_schedule"
    EQUIVALENCE_GRID = "equivalence_grid"
    GENERAL_BROCHURE = "general_brochure"
    BROCHURE = "brochure"
    REGLEMENT = "reglement"
    FAQ = "faq"
    PROGRAMME = "programme"


class DocumentStatusEnum(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class IndexStatusEnum(StrEnum):
    NOT_INDEXED = "NOT_INDEXED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


# Allowed file MIME types and size constraints
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/markdown",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_DOCUMENT_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


class DocumentUpdate(BaseModel):
    """Payload to update document metadata and publication status."""

    title: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    school_code: SchoolCodeEnum | None = None
    doc_type: DocumentCategoryEnum | None = None
    target_program_id: UUID | None = None
    is_published: bool | None = None
    status: DocumentStatusEnum | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentVersionResponse(BaseModel):
    """Metadata of an uploaded or compiled physical version."""

    id: UUID
    version_number: int
    file_name: str
    file_path_storage: str
    mime_type: str
    file_size_bytes: int
    file_hash_sha256: str
    is_active: bool = True
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    """Official knowledge document representation."""

    id: UUID
    code: str
    title: str
    description: str | None = None
    school_code: str
    doc_type: str
    target_program_id: UUID | None = None
    current_version_id: UUID | None = None
    status: str
    is_published: bool = False
    index_status: str = "NOT_INDEXED"
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    current_version: DocumentVersionResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    """Paginated list of official knowledge corpus documents."""

    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
