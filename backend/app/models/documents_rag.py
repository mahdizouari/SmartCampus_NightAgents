"""Official corpus document management, sync pipeline, and unified pgvector RAG (SA-14/SA-12)."""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Aligned with BAAI/bge-m3 (1024d) local open-source embedding standard (Alembic 20261127_0003).
DEFAULT_EMBEDDING_DIMENSION = 1024


class CorpusDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Master catalog of official institutional RAG documents (distinct from core.document)."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "school IN ('iit', 'isb', 'isa', 'nau_common')",
            name="documents_school_valid",
        ),
        CheckConstraint(
            "category IN ("
            "'admission_rules', 'curriculum', 'fees_schedule', 'equivalence_grid', "
            "'general_brochure', 'brochure', 'reglement', 'faq', 'programme'"
            ")",
            name="documents_category_valid",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'DELETED')",
            name="documents_status_valid",
        ),
        Index("idx_documents_school_category", "school", "category"),
        Index("idx_documents_status", "status"),
        {"schema": "core"},
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    school: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    target_program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "core.document_versions.id",
            name="fk_documents_current_version",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'DRAFT'"),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )


class CorpusDocumentVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable physical file versions in MinIO/S3 with SHA-256 deduplication."""

    __tablename__ = "document_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="doc_versions_number_positive"),
        CheckConstraint(
            "mime_type IN ("
            "'application/pdf', "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document', "
            "'text/markdown', "
            "'text/plain'"
            ")",
            name="doc_versions_mime_valid",
        ),
        CheckConstraint("file_size_bytes > 0", name="doc_versions_size_positive"),
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "file_hash_sha256", name="uq_document_file_hash"),
        Index("idx_document_versions_active", "document_id", "is_active"),
        {"schema": "core"},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path_storage: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class DocumentSyncJob(UUIDPrimaryKeyMixin, Base):
    """Asynchronous ingestion/re-indexing job state for official RAG corpus documents."""

    __tablename__ = "document_sync_jobs"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('INGEST', 'REINDEX', 'DEACTIVATE', 'PURGE')",
            name="sync_jobs_action_valid",
        ),
        CheckConstraint(
            "pipeline_stage IN ("
            "'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'COMPLETED', 'FAILED'"
            ")",
            name="sync_jobs_stage_valid",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SYNCED', 'FAILED', 'CANCELLED')",
            name="sync_jobs_status_valid",
        ),
        CheckConstraint("chunk_size > 0", name="sync_jobs_chunk_size_positive"),
        CheckConstraint("chunk_overlap >= 0", name="sync_jobs_overlap_non_negative"),
        CheckConstraint("retry_count >= 0", name="sync_jobs_retry_non_negative"),
        CheckConstraint("max_retries > 0", name="sync_jobs_max_retries_positive"),
        Index("idx_sync_jobs_status", "status"),
        Index("idx_sync_jobs_version", "version_id"),
        {"schema": "core"},
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pipeline_stage: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'PARSING'"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        server_default=text("'bge-m3'"),
    )
    chunk_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("512"),
    )
    chunk_overlap: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("64"),
    )
    total_pages: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        server_default=text("0"),
    )
    total_chunks: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        server_default=text("0"),
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        server_default=text("0"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class DocumentSyncLog(Base):
    """Granular execution diagnostics emitted by corpus sync jobs."""

    __tablename__ = "document_sync_logs"
    __table_args__ = (
        CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')",
            name="sync_logs_level_valid",
        ),
        Index("idx_sync_logs_job", "job_id", "created_at"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document_sync_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, Base):
    """Unified RAG knowledge store combining pgvector cosine search and BM25 full-text."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="knowledge_chunks_index_non_negative"),
        CheckConstraint("token_count > 0", name="knowledge_chunks_tokens_positive"),
        UniqueConstraint("version_id", "chunk_index", name="uq_knowledge_chunks_version_idx"),
        Index(
            "idx_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "idx_knowledge_chunks_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
        Index("idx_knowledge_chunks_active_doc", "is_active", "document_id"),
        Index(
            "idx_knowledge_chunks_program",
            "program_id",
            postgresql_where=text("program_id IS NOT NULL"),
        ),
        Index(
            "idx_knowledge_chunks_metadata_gin",
            "metadata",
            postgresql_using="gin",
        ),
        {"schema": "knowledge"},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    section_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('french', content)", persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(DEFAULT_EMBEDDING_DIMENSION),
        nullable=False,
    )
    chunk_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
