"""Pydantic schemas for official RAG corpus management (SA-49) and change detection (SA-56)."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
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
    """High-level RAG synchronization state surfaced by the Knowledge API (SA-49 & SA-56)."""

    NOT_INDEXED = "NOT_INDEXED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    SYNCED = "SYNCED"
    STALE = "STALE"
    FAILED = "FAILED"


class ChangeTypeEnum(StrEnum):
    """Deterministic classification produced by the SA-56 change-detection engine."""

    NEW_DOCUMENT = "NEW_DOCUMENT"
    NEW_VERSION = "NEW_VERSION"
    METADATA_CHANGED = "METADATA_CHANGED"
    UNCHANGED = "UNCHANGED"
    DUPLICATE_HISTORICAL_VERSION = "DUPLICATE_HISTORICAL_VERSION"
    DELETED = "DELETED"


class SyncActionTypeEnum(StrEnum):
    """Matches core.document_sync_jobs.action_type SQL CHECK constraint."""

    INGEST = "INGEST"
    REINDEX = "REINDEX"
    DEACTIVATE = "DEACTIVATE"
    PURGE = "PURGE"


class SyncPipelineStageEnum(StrEnum):
    """Matches core.document_sync_jobs.pipeline_stage SQL CHECK constraint."""

    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SyncJobStatusEnum(StrEnum):
    """Matches core.document_sync_jobs.status SQL CHECK constraint."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SyncLogLevelEnum(StrEnum):
    """Matches core.document_sync_logs.level SQL CHECK constraint."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


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
    changelog: str | None = None
    published_date: date | None = None
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
    latest_version_number: int | None = None
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


# ---------------------------------------------------------------------------
# SA-56 Change Detection, Versioning & Sync Queue Schemas
# ---------------------------------------------------------------------------


class FieldDiff(BaseModel):
    """Old vs new value for a modified metadata field."""

    old: Any | None = None
    new: Any | None = None


class ContentDiffSummary(BaseModel):
    """Deterministic file-level delta summary (text diff for MD/TXT, metadata delta for binary)."""

    is_binary: bool
    previous_size_bytes: int | None = None
    incoming_size_bytes: int
    size_delta_bytes: int | None = None
    file_name_changed: bool = False
    mime_type_changed: bool = False
    lines_added: int | None = None
    lines_removed: int | None = None
    diff_preview: str | None = None


class CrossDocumentHashMatch(BaseModel):
    """Non-blocking warning when identical file bytes already exist under another corpus doc."""

    document_id: UUID
    document_code: str
    version_number: int
    is_active: bool


class DocumentSyncJobResponse(BaseModel):
    """Serialized representation of core.document_sync_jobs."""

    id: UUID
    version_id: UUID
    action_type: str
    pipeline_stage: str
    status: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    total_pages: int | None = 0
    total_chunks: int | None = 0
    total_tokens: int | None = 0
    error_message: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentSyncLogResponse(BaseModel):
    """Serialized representation of core.document_sync_logs."""

    id: int | None = None
    job_id: UUID
    stage: str
    level: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ChangeDetectionResponse(BaseModel):
    """Dry-run change-detection report (SA-56) with zero DB or disk side effects."""

    change_type: ChangeTypeEnum
    document_id: UUID | None = None
    document_code: str | None = None
    current_version_number: int | None = None
    next_version_number: int | None = None
    active_hash_sha256: str | None = None
    incoming_hash_sha256: str | None = None
    matched_version_id: UUID | None = None
    matched_version_number: int | None = None
    requires_sync: bool = False
    recommended_action: SyncActionTypeEnum | None = None
    projected_index_status: IndexStatusEnum = IndexStatusEnum.NOT_INDEXED
    metadata_diff: dict[str, FieldDiff] = Field(default_factory=dict)
    content_diff: ContentDiffSummary | None = None
    cross_document_matches: list[CrossDocumentHashMatch] = Field(default_factory=list)


class DocumentVersionDetailResponse(DocumentVersionResponse):
    """Detailed version record including uploader attribution and latest sync job."""

    document_id: UUID
    uploaded_by: UUID
    latest_sync_job: DocumentSyncJobResponse | None = None


class DocumentVersionListResponse(BaseModel):
    """Full version history for a corpus document (newest first)."""

    document_id: UUID
    document_code: str
    current_version_id: UUID | None = None
    index_status: str
    total_versions: int
    versions: list[DocumentVersionDetailResponse]


class VersionUploadResponse(BaseModel):
    """Result of uploading a new version (or skipping an unchanged file via SHA-256)."""

    document_id: UUID
    document_code: str
    change_type: ChangeTypeEnum
    created: bool
    skipped: bool
    activated: bool
    index_status: str
    version: DocumentVersionDetailResponse
    sync_job: DocumentSyncJobResponse | None = None
    sync_job_deduplicated: bool = False
    content_diff: ContentDiffSummary | None = None
    cross_document_matches: list[CrossDocumentHashMatch] = Field(default_factory=list)


class VersionActivateRequest(BaseModel):
    """Optional parameters when promoting a staged version or rolling back to an older version."""

    reason: str | None = Field(default=None, max_length=500)
    enqueue_reindex_if_unindexed: bool = True


class VersionActivateResponse(BaseModel):
    """Result of an atomic version promotion or rollback (SA-14 §6.2)."""

    document_id: UUID
    previous_version_id: UUID | None = None
    previous_version_number: int | None = None
    active_version_id: UUID
    active_version_number: int
    is_rollback: bool
    index_status: str
    deactivated_chunks_count: int = 0
    activated_chunks_count: int = 0
    sync_job: DocumentSyncJobResponse | None = None


class DocumentSyncJobWithLogsResponse(DocumentSyncJobResponse):
    """Sync job enriched with its version number and granular diagnostic logs."""

    version_number: int | None = None
    logs: list[DocumentSyncLogResponse] = Field(default_factory=list)


class DocumentSyncJobListResponse(BaseModel):
    """Change & synchronization job history for a corpus document."""

    document_id: UUID
    index_status: str
    items: list[DocumentSyncJobWithLogsResponse]
    total: int


class DocumentSyncLogListResponse(BaseModel):
    """Granular execution logs emitted for a single DocumentSyncJob."""

    job_id: UUID
    items: list[DocumentSyncLogResponse]
    total: int
