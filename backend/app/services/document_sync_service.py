"""Deterministic document change detection, immutable versioning, and sync-queue service (SA-56).

Component type: **deterministic code** (SHA-256 checksums, metadata/text diff, SQL state
transitions) — NOT an AI agent.

Responsibilities (GitHub Issue #71 & VisioraAI SA-56):
1. Compute SHA-256 checksums on canonical document bytes (LF/UTF-8 normalized for text/markdown,
   exact raw bytes for binary PDF/DOCX) and compare against persisted `core.document_versions`.
2. Classify incoming changes (`NEW_DOCUMENT`, `UNCHANGED`, `NEW_VERSION`, `METADATA_CHANGED`,
   `DUPLICATE_HISTORICAL_VERSION`, `DELETED`) without side effects (dry-run safe).
3. Skip unchanged uploads cleanly without creating duplicate versions or redundant embedding jobs.
4. Increment version numbers (`1 -> 2 -> 3...`) under row-level locking (`FOR UPDATE`) using
   immutable versioned storage paths (`{school}/{code}/v{N}/{safe_name}`) and surface `STALE`
   index status when a new version or RAG-relevant metadata update awaits re-indexing.
5. Automatically record EVERY add, modification, rollback, and deletion in PostgreSQL
   (`core.document_sync_jobs`, `core.document_sync_logs`, `core.audit_log`) for downstream
   vector synchronization workers (SA-57 / SA-58).
6. Provide the atomic Blue-Green version activation / rollback primitive (`SA-14 §6.2`) reusable
   by both REST rollback endpoints (`commit=True`) and SA-57 workers (`commit=False`).
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentActor
from app.models.documents_rag import (
    CorpusDocument,
    CorpusDocumentVersion,
    DocumentSyncJob,
    DocumentSyncLog,
    KnowledgeChunk,
)
from app.models.users import Account, AuditLog
from app.schemas.knowledge import (
    ALLOWED_MIME_TYPES,
    ChangeDetectionResponse,
    ChangeTypeEnum,
    ContentDiffSummary,
    CrossDocumentHashMatch,
    DocumentStatusEnum,
    DocumentSyncJobListResponse,
    DocumentSyncJobResponse,
    DocumentSyncJobWithLogsResponse,
    DocumentSyncLogListResponse,
    DocumentSyncLogResponse,
    DocumentUpdate,
    DocumentVersionDetailResponse,
    DocumentVersionListResponse,
    FieldDiff,
    IndexStatusEnum,
    SyncActionTypeEnum,
    SyncJobStatusEnum,
    SyncLogLevelEnum,
    SyncPipelineStageEnum,
    VersionActivateResponse,
    VersionUploadResponse,
)
from app.services import file_storage_service as storage_svc

logger = logging.getLogger(__name__)

TEXT_MIME_TYPES: frozenset[str] = frozenset({"text/markdown", "text/plain"})
RAG_RELEVANT_METADATA_FIELDS: frozenset[str] = frozenset(
    {"title", "school", "category", "target_program_id", "status"}
)
TRACKED_METADATA_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "school",
    "category",
    "target_program_id",
    "status",
)

# Aligned with BAAI/bge-m3 (1024d) local open-source embedding standard (Alembic 20261127_0003).
DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64
DEFAULT_MAX_RETRIES = 3

SYNC_LOG_STAGE_CHANGE_DETECTOR = "CHANGE_DETECTOR"

_ACTOR_TYPE_BY_ROLE: dict[str, str] = {
    "admin": "ADMIN",
    "responsable": "RESPONSABLE",
    "direction": "DIRECTION",
    "rh": "RH",
    "student": "STUDENT",
    "candidat": "CANDIDAT",
    "visiteur": "VISITEUR",
    "guest": "VISITEUR",
}
_VALID_AUDIT_ACTOR_TYPES: frozenset[str] = frozenset(
    {
        "VISITEUR",
        "CANDIDAT",
        "STUDENT",
        "RH",
        "RESPONSABLE",
        "DIRECTION",
        "ADMIN",
        "VISION",
        "JARVIS",
        "SYSTEM",
    }
)


# ---------------------------------------------------------------------------
# Pure Normalization, Hashing, Diff & Status Computation Helpers
# ---------------------------------------------------------------------------


def normalize_text_bytes(content: bytes) -> bytes:
    """Normalize UTF-8 text/markdown bytes by stripping BOM and converting CRLF/CR to LF.

    Ensures identical documents uploaded from Windows (CRLF) and Linux/macOS (LF) produce
    the exact same SHA-256 checksum without altering semantic content.
    """
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def compute_content_sha256(content: bytes, mime_type: str | None = None) -> str:
    """Compute SHA-256 digest (canonical LF UTF-8 for text/markdown, exact raw bytes for binary)."""
    payload = normalize_text_bytes(content) if mime_type in TEXT_MIME_TYPES else content
    return storage_svc.compute_sha256(payload)


def prepare_bytes_for_storage(content: bytes, mime_type: str) -> bytes:
    """Return canonical bytes to persist in object storage (LF-normalized for text formats)."""
    if mime_type in TEXT_MIME_TYPES:
        return normalize_text_bytes(content)
    return content


def build_versioned_sub_path(
    school_code: str,
    doc_code: str,
    version_number: int,
    filename: str,
) -> str:
    """Build an immutable version-scoped storage path so v2+ never overwrites v1 on disk."""
    safe_name = storage_svc.sanitize_filename(filename)
    return f"{school_code}/{doc_code}/v{version_number}/{safe_name}"


def actor_type_from_role(role: str | None) -> str:
    """Map JWT actor role to a valid core.audit_log.actor_type enum string."""
    if not role:
        return "SYSTEM"
    return _ACTOR_TYPE_BY_ROLE.get(role.strip().lower(), "SYSTEM")


def enforce_document_school_scope(actor: CurrentActor, doc_school: str) -> None:
    """Enforce school-level isolation for NAU_RESPONSABLE actors when school_scope is set."""
    if actor.role == "responsable" and actor.school_scope:
        if actor.school_scope.strip().lower() != doc_school.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied: responsable scoped to '{actor.school_scope}' "
                    f"cannot manage documents for school '{doc_school}'."
                ),
            )


def _serialize_metadata_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def compute_metadata_diff(
    doc: CorpusDocument,
    incoming_updates: dict[str, Any],
) -> tuple[dict[str, FieldDiff], bool]:
    """Compare incoming metadata fields against a CorpusDocument.

    Returns:
        (field_diff_map, has_rag_relevant_changes)
    """
    diff: dict[str, FieldDiff] = {}
    rag_relevant = False

    for field in TRACKED_METADATA_FIELDS:
        if field not in incoming_updates:
            continue
        new_val = incoming_updates[field]
        old_val = getattr(doc, field, None)
        if old_val != new_val:
            diff[field] = FieldDiff(
                old=_serialize_metadata_value(old_val),
                new=_serialize_metadata_value(new_val),
            )
            if field in RAG_RELEVANT_METADATA_FIELDS:
                rag_relevant = True

    return diff, rag_relevant


def compute_text_diff_summary(
    old_bytes: bytes | None,
    new_bytes: bytes,
    *,
    mime_type: str,
    old_file_name: str | None = None,
    new_file_name: str | None = None,
    old_mime_type: str | None = None,
    include_preview: bool = True,
    max_preview_lines: int = 40,
    max_preview_chars: int = 4000,
) -> ContentDiffSummary:
    """Compute a deterministic content delta summary (stdlib difflib for text/markdown)."""
    prev_size = len(old_bytes) if old_bytes is not None else None
    inc_size = len(new_bytes)
    size_delta = (inc_size - prev_size) if prev_size is not None else None
    file_name_changed = bool(
        old_file_name is not None and new_file_name is not None and old_file_name != new_file_name
    )
    mime_type_changed = bool(old_mime_type is not None and old_mime_type != mime_type)

    if mime_type not in TEXT_MIME_TYPES:
        return ContentDiffSummary(
            is_binary=True,
            previous_size_bytes=prev_size,
            incoming_size_bytes=inc_size,
            size_delta_bytes=size_delta,
            file_name_changed=file_name_changed,
            mime_type_changed=mime_type_changed,
        )

    old_text = (
        normalize_text_bytes(old_bytes).decode("utf-8", errors="replace")
        if old_bytes is not None
        else ""
    )
    new_text = normalize_text_bytes(new_bytes).decode("utf-8", errors="replace")

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_file_name or "previous",
            tofile=new_file_name or "incoming",
            lineterm="",
        )
    )

    lines_added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    lines_removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    preview: str | None = None
    if include_preview and diff_lines:
        truncated = diff_lines[:max_preview_lines]
        joined = "\n".join(truncated)
        if len(diff_lines) > max_preview_lines or len(joined) > max_preview_chars:
            joined = joined[: max_preview_chars - 24].rstrip() + "\n... [diff truncated]"
        preview = joined

    return ContentDiffSummary(
        is_binary=False,
        previous_size_bytes=prev_size,
        incoming_size_bytes=inc_size,
        size_delta_bytes=size_delta,
        file_name_changed=file_name_changed,
        mime_type_changed=mime_type_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
        diff_preview=preview,
    )


def compute_document_index_status(
    doc: CorpusDocument,
    *,
    current_version: CorpusDocumentVersion | None = None,
    latest_version: CorpusDocumentVersion | None = None,
    latest_job: DocumentSyncJob | None = None,
    current_job: DocumentSyncJob | None = None,
    latest_version_job: DocumentSyncJob | None = None,
) -> str:
    """Derive the high-level RAG index_status (NOT_INDEXED/PENDING/PROCESSING/INDEXED/STALE/FAILED).

    Keeps `core.document_sync_jobs.status` strictly within its SQL CHECK constraint
    (`PENDING`, `PROCESSING`, `SYNCED`, `FAILED`, `CANCELLED`) while exposing `STALE`
    whenever a newer staged version or pending `REINDEX`/`DEACTIVATE` awaits execution.
    When an active rollback points to an older version (while a newer version's job is
    already `SYNCED` or `CANCELLED`), status is derived from the active version's job.
    """
    if doc.status == DocumentStatusEnum.DELETED.value:
        return IndexStatusEnum.NOT_INDEXED.value

    if current_version is None and latest_version is None:
        return IndexStatusEnum.NOT_INDEXED.value

    effective_current_job = current_job if current_job is not None else latest_job
    effective_latest_job = latest_version_job if latest_version_job is not None else latest_job

    # A newer version exists ahead of the currently active version:
    # only mark STALE/FAILED when that newer version is genuinely staged awaiting sync.
    if (
        latest_version is not None
        and current_version is not None
        and latest_version.id != current_version.id
        and latest_version.version_number > current_version.version_number
    ):
        if (
            effective_latest_job is not None
            and effective_latest_job.status == SyncJobStatusEnum.FAILED.value
        ):
            return IndexStatusEnum.FAILED.value
        if effective_latest_job is None or effective_latest_job.status in (
            SyncJobStatusEnum.PENDING.value,
            SyncJobStatusEnum.PROCESSING.value,
        ):
            return IndexStatusEnum.STALE.value
        # Newer version is SYNCED or CANCELLED (e.g. post-rollback) -> evaluate active version's job

    active_job = effective_current_job
    if active_job is None:
        return IndexStatusEnum.NOT_INDEXED.value

    if active_job.status == SyncJobStatusEnum.FAILED.value:
        return IndexStatusEnum.FAILED.value

    if active_job.status == SyncJobStatusEnum.CANCELLED.value:
        return IndexStatusEnum.STALE.value if current_version else IndexStatusEnum.NOT_INDEXED.value

    if active_job.action_type in (
        SyncActionTypeEnum.REINDEX.value,
        SyncActionTypeEnum.DEACTIVATE.value,
    ) and active_job.status in (
        SyncJobStatusEnum.PENDING.value,
        SyncJobStatusEnum.PROCESSING.value,
    ):
        return IndexStatusEnum.STALE.value

    if active_job.status == SyncJobStatusEnum.PENDING.value:
        return IndexStatusEnum.PENDING.value

    if active_job.status == SyncJobStatusEnum.PROCESSING.value:
        return IndexStatusEnum.PROCESSING.value

    if active_job.status == SyncJobStatusEnum.SYNCED.value:
        if active_job.action_type in (
            SyncActionTypeEnum.DEACTIVATE.value,
            SyncActionTypeEnum.PURGE.value,
        ):
            return IndexStatusEnum.NOT_INDEXED.value
        return IndexStatusEnum.INDEXED.value

    return IndexStatusEnum.NOT_INDEXED.value


# ---------------------------------------------------------------------------
# Serialization Helpers
# ---------------------------------------------------------------------------


def serialize_sync_job(job: DocumentSyncJob) -> DocumentSyncJobResponse:
    """Convert an ORM DocumentSyncJob instance to DocumentSyncJobResponse."""
    return DocumentSyncJobResponse(
        id=job.id,
        version_id=job.version_id,
        action_type=job.action_type,
        pipeline_stage=job.pipeline_stage,
        status=job.status,
        embedding_model=job.embedding_model or DEFAULT_EMBEDDING_MODEL,
        chunk_size=job.chunk_size if job.chunk_size is not None else DEFAULT_CHUNK_SIZE,
        chunk_overlap=(
            job.chunk_overlap if job.chunk_overlap is not None else DEFAULT_CHUNK_OVERLAP
        ),
        total_pages=job.total_pages,
        total_chunks=job.total_chunks,
        total_tokens=job.total_tokens,
        error_message=job.error_message,
        retry_count=job.retry_count if job.retry_count is not None else 0,
        max_retries=job.max_retries if job.max_retries is not None else DEFAULT_MAX_RETRIES,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_ms=job.duration_ms,
        created_at=job.created_at,
    )


def serialize_sync_log(log: DocumentSyncLog) -> DocumentSyncLogResponse:
    """Convert an ORM DocumentSyncLog instance to DocumentSyncLogResponse."""
    return DocumentSyncLogResponse(
        id=getattr(log, "id", None),
        job_id=log.job_id,
        stage=log.stage,
        level=log.level,
        message=log.message,
        details=log.details or {},
        created_at=log.created_at,
    )


def serialize_version_detail(
    version: CorpusDocumentVersion,
    latest_job: DocumentSyncJob | None = None,
) -> DocumentVersionDetailResponse:
    """Convert an ORM CorpusDocumentVersion to DocumentVersionDetailResponse."""
    return DocumentVersionDetailResponse(
        id=version.id,
        document_id=version.document_id,
        version_number=version.version_number,
        file_name=version.file_name,
        file_path_storage=version.file_path_storage,
        mime_type=version.mime_type,
        file_size_bytes=version.file_size_bytes,
        file_hash_sha256=version.file_hash_sha256,
        changelog=version.changelog,
        published_date=version.published_date,
        is_active=version.is_active,
        uploaded_by=version.uploaded_by,
        created_at=version.created_at,
        latest_sync_job=serialize_sync_job(latest_job) if latest_job else None,
    )


# ---------------------------------------------------------------------------
# Audit, Sync Log & Sync Job Queue Primitives
# ---------------------------------------------------------------------------


async def verify_actor_account_exists(db: AsyncSession, actor: CurrentActor) -> uuid.UUID:
    """Validate actor.account_id is a valid UUID referencing an existing core.accounts row."""
    try:
        user_uuid = uuid.UUID(actor.account_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid actor account identifier.",
        ) from exc

    acc_exists = await db.execute(select(Account.id).where(Account.id == user_uuid))
    if not acc_exists.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated account not found.",
        )
    return user_uuid


def record_document_audit(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    entity_id: uuid.UUID,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an immutable, PII-free state-change audit trail entry in core.audit_log."""
    normalized_type = actor_type.upper()
    if normalized_type not in _VALID_AUDIT_ACTOR_TYPES:
        normalized_type = "SYSTEM"

    entry = AuditLog(
        id=uuid.uuid4(),
        actor_type=normalized_type,
        actor_id=str(actor_id)[:64],
        action=action[:96],
        entity_type="corpus_document",
        entity_id=entity_id,
        reason=reason,
        event_metadata=metadata or {},
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    return entry


def append_sync_log(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    stage: str = SYNC_LOG_STAGE_CHANGE_DETECTOR,
    level: str = SyncLogLevelEnum.INFO.value,
    message: str,
    details: dict[str, Any] | None = None,
) -> DocumentSyncLog:
    """Append a granular diagnostic log entry to core.document_sync_logs."""
    log_entry = DocumentSyncLog(
        job_id=job_id,
        stage=stage[:30],
        level=level,
        message=message,
        details=details or {},
        created_at=datetime.now(UTC),
    )
    db.add(log_entry)
    return log_entry


def _safe_rowcount(result: Any) -> int:
    rc = getattr(result, "rowcount", 0)
    return rc if isinstance(rc, int) and rc >= 0 else 0


async def _fetch_document_versions(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> list[CorpusDocumentVersion]:
    res = await db.execute(
        select(CorpusDocumentVersion)
        .where(CorpusDocumentVersion.document_id == document_id)
        .order_by(CorpusDocumentVersion.version_number.desc())
    )
    rows = res.scalars().all()
    if not isinstance(rows, list):
        rows = list(rows)
    return sorted(rows, key=lambda v: v.version_number, reverse=True)


async def _fetch_jobs_for_versions(
    db: AsyncSession,
    version_ids: list[uuid.UUID],
    *,
    status_filter: str | None = None,
) -> list[DocumentSyncJob]:
    if not version_ids:
        return []
    stmt = select(DocumentSyncJob).where(DocumentSyncJob.version_id.in_(version_ids))
    if status_filter is not None:
        stmt = stmt.where(DocumentSyncJob.status == status_filter)
    stmt = stmt.order_by(DocumentSyncJob.created_at.desc())
    res = await db.execute(stmt)
    rows = res.scalars().all()
    if not isinstance(rows, list):
        rows = list(rows)
    return rows


async def _fetch_latest_job_for_version(
    db: AsyncSession,
    version_id: uuid.UUID,
) -> DocumentSyncJob | None:
    res = await db.execute(
        select(DocumentSyncJob)
        .where(DocumentSyncJob.version_id == version_id)
        .order_by(DocumentSyncJob.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def cancel_superseded_pending_jobs(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    exclude_version_id: uuid.UUID | None = None,
    max_version_number: int | None = None,
    reason: str = "Superseded by newer document change",
) -> list[DocumentSyncJob]:
    """Cancel PENDING jobs on target versions of the same document without touching PROCESSING."""
    versions = await _fetch_document_versions(db, document_id)
    target_version_ids = [
        v.id
        for v in versions
        if (exclude_version_id is None or v.id != exclude_version_id)
        and (max_version_number is None or v.version_number <= max_version_number)
    ]
    if not target_version_ids:
        return []

    pending_jobs = await _fetch_jobs_for_versions(
        db,
        target_version_ids,
        status_filter=SyncJobStatusEnum.PENDING.value,
    )
    now = datetime.now(UTC)
    cancelled: list[DocumentSyncJob] = []
    for job in pending_jobs:
        if job.status != SyncJobStatusEnum.PENDING.value:
            continue
        job.status = SyncJobStatusEnum.CANCELLED.value
        job.completed_at = now
        append_sync_log(
            db,
            job_id=job.id,
            stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
            level=SyncLogLevelEnum.INFO.value,
            message=reason,
            details={"document_id": str(document_id), "cancelled_job_id": str(job.id)},
        )
        cancelled.append(job)

    return cancelled


async def enqueue_sync_job(
    db: AsyncSession,
    *,
    version_id: uuid.UUID,
    action_type: str,
    pipeline_stage: str = SyncPipelineStageEnum.PARSING.value,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    log_message: str | None = None,
    log_details: dict[str, Any] | None = None,
    cancel_superseded_for_document_id: uuid.UUID | None = None,
    cancel_max_version_number: int | None = None,
) -> tuple[DocumentSyncJob, bool]:
    """Create a deduplicated PENDING DocumentSyncJob and emit its initial DocumentSyncLog.

    Guarantees a single version never accumulates conflicting PENDING jobs:
    - Exact match (PENDING or PROCESSING) is reused.
    - Requested REINDEX when a PENDING INGEST already awaits execution on the same version
      reuses that PENDING INGEST job (appending a CHANGE_DETECTOR log noting reuse).
    - Action flips on the same version (e.g. INGEST/REINDEX <-> DEACTIVATE/PURGE) cancel
      any superseded PENDING jobs on that same version before inserting the new job.

    Returns:
        (job, created) where created=False if an existing active job was reused.
    """
    existing_jobs = await _fetch_jobs_for_versions(db, [version_id])

    # 1a. Exact match deduplication (PENDING or PROCESSING)
    for job in existing_jobs:
        if job.action_type == action_type and job.status in (
            SyncJobStatusEnum.PENDING.value,
            SyncJobStatusEnum.PROCESSING.value,
        ):
            append_sync_log(
                db,
                job_id=job.id,
                stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
                level=SyncLogLevelEnum.DEBUG.value,
                message=f"Deduplicated {action_type} sync job request; existing active job reused.",
                details=log_details or {},
            )
            return job, False

    # 1b. Reuse an unstarted PENDING INGEST job when REINDEX is requested on the same version
    if action_type == SyncActionTypeEnum.REINDEX.value:
        for job in existing_jobs:
            if (
                job.action_type == SyncActionTypeEnum.INGEST.value
                and job.status == SyncJobStatusEnum.PENDING.value
            ):
                append_sync_log(
                    db,
                    job_id=job.id,
                    stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
                    level=SyncLogLevelEnum.INFO.value,
                    message=(
                        "Reused existing PENDING INGEST sync job instead of creating "
                        "conflicting REINDEX job."
                    ),
                    details={
                        **(log_details or {}),
                        "requested_action_type": action_type,
                        "reused_job_id": str(job.id),
                        "reused_action_type": job.action_type,
                    },
                )
                return job, False

    # 2. Cancel conflicting PENDING jobs on the SAME version (action flip, e.g. INGEST->DEACTIVATE)
    now = datetime.now(UTC)
    for job in existing_jobs:
        if job.status == SyncJobStatusEnum.PENDING.value:
            job.status = SyncJobStatusEnum.CANCELLED.value
            job.completed_at = now
            append_sync_log(
                db,
                job_id=job.id,
                stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
                level=SyncLogLevelEnum.INFO.value,
                message=(
                    f"Cancelled conflicting PENDING {job.action_type} job on version "
                    f"{version_id}; superseded by {action_type}."
                ),
                details={
                    **(log_details or {}),
                    "cancelled_job_id": str(job.id),
                    "cancelled_action_type": job.action_type,
                    "superseded_by_action_type": action_type,
                },
            )

    # 3. Cancel superseded PENDING jobs on other target versions of the same document
    if cancel_superseded_for_document_id is not None:
        await cancel_superseded_pending_jobs(
            db,
            document_id=cancel_superseded_for_document_id,
            exclude_version_id=version_id,
            max_version_number=cancel_max_version_number,
            reason=f"Superseded by new {action_type} job for version {version_id}",
        )

    new_job = DocumentSyncJob(
        id=uuid.uuid4(),
        version_id=version_id,
        action_type=action_type,
        pipeline_stage=pipeline_stage,
        status=SyncJobStatusEnum.PENDING.value,
        embedding_model=embedding_model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        total_pages=0,
        total_chunks=0,
        total_tokens=0,
        retry_count=0,
        max_retries=DEFAULT_MAX_RETRIES,
        created_at=now,
    )
    db.add(new_job)
    await db.flush()

    append_sync_log(
        db,
        job_id=new_job.id,
        stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
        level=SyncLogLevelEnum.INFO.value,
        message=log_message or f"Enqueued {action_type} synchronization job.",
        details=log_details or {},
    )
    return new_job, True


async def _find_cross_document_hash_matches(
    db: AsyncSession,
    file_hash_sha256: str,
    *,
    exclude_document_id: uuid.UUID | None = None,
    actor: CurrentActor | None = None,
) -> list[CrossDocumentHashMatch]:
    """Find versions under OTHER documents that share the exact same SHA-256 checksum.

    For school-scoped ``responsable`` callers, restricts cross-school matches to publicly
    ``PUBLISHED`` active versions so unpublished drafts from other schools are never leaked.
    """
    stmt = select(CorpusDocumentVersion).where(
        CorpusDocumentVersion.file_hash_sha256 == file_hash_sha256
    )
    if exclude_document_id is not None:
        stmt = stmt.where(CorpusDocumentVersion.document_id != exclude_document_id)

    res = await db.execute(stmt)
    matching_versions = res.scalars().all()
    if not isinstance(matching_versions, list):
        matching_versions = list(matching_versions)

    candidate_versions = [
        ver
        for ver in matching_versions
        if exclude_document_id is None or ver.document_id != exclude_document_id
    ]
    if not candidate_versions:
        return []

    other_doc_ids = list({ver.document_id for ver in candidate_versions})
    docs_res = await db.execute(
        select(CorpusDocument).where(
            CorpusDocument.id.in_(other_doc_ids),
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
    )
    raw_docs = docs_res.scalars().all()
    if not isinstance(raw_docs, list):
        raw_docs = list(raw_docs)
    docs_by_id = {
        d.id: d for d in raw_docs if d.status != DocumentStatusEnum.DELETED.value
    }

    scoped_school = (
        actor.school_scope.strip().lower()
        if actor is not None and actor.role == "responsable" and actor.school_scope
        else None
    )

    matches: list[CrossDocumentHashMatch] = []
    for ver in candidate_versions:
        other_doc = docs_by_id.get(ver.document_id)
        if other_doc is None:
            continue
        if scoped_school is not None and other_doc.school.strip().lower() != scoped_school:
            if other_doc.status != DocumentStatusEnum.PUBLISHED.value or not ver.is_active:
                continue
        matches.append(
            CrossDocumentHashMatch(
                document_id=other_doc.id,
                document_code=other_doc.code,
                version_number=ver.version_number,
                is_active=ver.is_active,
            )
        )
    return matches


def _try_read_version_bytes(
    version: CorpusDocumentVersion | None,
    *,
    storage_root: Path | None = None,
) -> bytes | None:
    if version is None or not version.file_path_storage:
        return None
    try:
        old_bytes, _ = storage_svc.read_document_bytes(
            version.file_path_storage,
            storage_root=storage_root,
        )
        return old_bytes
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# 1. Dry-Run Change Detection Engine
# ---------------------------------------------------------------------------


async def detect_document_change(
    db: AsyncSession,
    *,
    document_id: uuid.UUID | None = None,
    code: str | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    declared_mime_type: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    include_text_diff: bool = False,
    actor: CurrentActor | None = None,
    storage_root: Path | None = None,
) -> ChangeDetectionResponse:
    """Classify an incoming document/metadata change without writing to PostgreSQL or disk."""
    if document_id is None and (code is None or not code.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'document_id' or 'code' must be provided for change detection.",
        )

    # Validate incoming binary/text bytes first if supplied
    verified_mime: str | None = None
    incoming_hash: str | None = None
    raw_hash: str | None = None
    safe_name: str | None = None
    canonical_bytes: bytes | None = None

    if file_bytes is not None:
        safe_name = storage_svc.sanitize_filename(filename or "incoming_doc")
        verified_mime = storage_svc.validate_file_content(
            file_bytes,
            safe_name,
            declared_mime_type=declared_mime_type,
            allowed_mime_types=ALLOWED_MIME_TYPES,
        )
        canonical_bytes = prepare_bytes_for_storage(file_bytes, verified_mime)
        incoming_hash = compute_content_sha256(file_bytes, verified_mime)
        raw_hash = storage_svc.compute_sha256(file_bytes)

    # Lookup existing document (including DELETED so DELETED status is accurately surfaced)
    doc: CorpusDocument | None = None
    if document_id is not None:
        res = await db.execute(select(CorpusDocument).where(CorpusDocument.id == document_id))
        doc = res.scalar_one_or_none()
    elif code is not None:
        res = await db.execute(select(CorpusDocument).where(CorpusDocument.code == code.strip()))
        doc = res.scalar_one_or_none()

    if doc is not None and actor is not None:
        enforce_document_school_scope(actor, doc.school)
    if actor is not None and metadata_updates and metadata_updates.get("school"):
        enforce_document_school_scope(actor, str(metadata_updates["school"]))

    cross_matches: list[CrossDocumentHashMatch] = []
    if incoming_hash is not None:
        cross_matches = await _find_cross_document_hash_matches(
            db,
            incoming_hash,
            exclude_document_id=doc.id if doc else None,
            actor=actor,
        )

    # Case 1: Brand-new document code (not yet in core.documents)
    if doc is None:
        has_file = canonical_bytes is not None
        content_diff = (
            compute_text_diff_summary(
                None,
                canonical_bytes,
                mime_type=verified_mime or "text/plain",
                new_file_name=safe_name,
                include_preview=include_text_diff,
            )
            if canonical_bytes is not None
            else None
        )
        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.NEW_DOCUMENT,
            document_id=None,
            document_code=code.strip() if code else None,
            current_version_number=None,
            next_version_number=1 if has_file else None,
            active_hash_sha256=None,
            incoming_hash_sha256=incoming_hash,
            requires_sync=has_file,
            recommended_action=SyncActionTypeEnum.INGEST if has_file else None,
            projected_index_status=(
                IndexStatusEnum.PENDING if has_file else IndexStatusEnum.NOT_INDEXED
            ),
            metadata_diff={},
            content_diff=content_diff,
            cross_document_matches=cross_matches,
        )

    # Case 2: Document is already DELETED
    if doc.status == DocumentStatusEnum.DELETED.value:
        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.DELETED,
            document_id=doc.id,
            document_code=doc.code,
            current_version_number=None,
            next_version_number=None,
            active_hash_sha256=None,
            incoming_hash_sha256=incoming_hash,
            requires_sync=False,
            recommended_action=None,
            projected_index_status=IndexStatusEnum.NOT_INDEXED,
            metadata_diff={},
            content_diff=None,
            cross_document_matches=cross_matches,
        )

    versions = await _fetch_document_versions(db, doc.id)
    active_version = next(
        (v for v in versions if v.id == doc.current_version_id),
        next((v for v in versions if v.is_active), None),
    )
    max_version_num = max((v.version_number for v in versions), default=0)
    active_hash = active_version.file_hash_sha256 if active_version else None

    metadata_diff, rag_meta_changed = compute_metadata_diff(doc, metadata_updates or {})

    # Case 3: Incoming metadata explicitly transitions status to DELETED
    requested_status = (metadata_updates or {}).get("status")
    if requested_status == DocumentStatusEnum.DELETED.value:
        has_versions = len(versions) > 0
        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.DELETED,
            document_id=doc.id,
            document_code=doc.code,
            current_version_number=active_version.version_number if active_version else None,
            next_version_number=None,
            active_hash_sha256=active_hash,
            incoming_hash_sha256=incoming_hash,
            requires_sync=has_versions,
            recommended_action=SyncActionTypeEnum.DEACTIVATE if has_versions else None,
            projected_index_status=IndexStatusEnum.NOT_INDEXED,
            metadata_diff=metadata_diff,
            content_diff=None,
            cross_document_matches=cross_matches,
        )

    # Case 4: Metadata-only dry-run (no file supplied)
    if canonical_bytes is None or incoming_hash is None:
        if not metadata_diff:
            return ChangeDetectionResponse(
                change_type=ChangeTypeEnum.UNCHANGED,
                document_id=doc.id,
                document_code=doc.code,
                current_version_number=active_version.version_number if active_version else None,
                next_version_number=None,
                active_hash_sha256=active_hash,
                incoming_hash_sha256=None,
                matched_version_id=active_version.id if active_version else None,
                matched_version_number=active_version.version_number if active_version else None,
                requires_sync=False,
                recommended_action=None,
                projected_index_status=IndexStatusEnum.INDEXED
                if active_version
                else IndexStatusEnum.NOT_INDEXED,
                metadata_diff={},
                content_diff=None,
                cross_document_matches=[],
            )

        target_status = requested_status or doc.status
        rec_action: SyncActionTypeEnum | None = None
        projected = IndexStatusEnum.NOT_INDEXED
        if (
            doc.status == DocumentStatusEnum.PUBLISHED.value
            and target_status in (DocumentStatusEnum.DRAFT.value, DocumentStatusEnum.ARCHIVED.value)
            and active_version is not None
        ):
            rec_action = SyncActionTypeEnum.DEACTIVATE
            projected = IndexStatusEnum.STALE
        elif (
            rag_meta_changed
            and target_status == DocumentStatusEnum.PUBLISHED.value
            and active_version is not None
        ):
            rec_action = SyncActionTypeEnum.REINDEX
            projected = IndexStatusEnum.STALE
        elif active_version is not None:
            projected = IndexStatusEnum.INDEXED

        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.METADATA_CHANGED,
            document_id=doc.id,
            document_code=doc.code,
            current_version_number=active_version.version_number if active_version else None,
            next_version_number=None,
            active_hash_sha256=active_hash,
            incoming_hash_sha256=None,
            requires_sync=rec_action is not None,
            recommended_action=rec_action,
            projected_index_status=projected,
            metadata_diff=metadata_diff,
            content_diff=None,
            cross_document_matches=[],
        )

    # Case 5: File supplied — compare SHA-256 against active version first
    candidate_hashes = {incoming_hash}
    if raw_hash is not None:
        candidate_hashes.add(raw_hash)

    if active_version is not None and active_version.file_hash_sha256 in candidate_hashes:
        if metadata_diff:
            target_status = requested_status or doc.status
            rec_action = None
            if (
                doc.status == DocumentStatusEnum.PUBLISHED.value
                and target_status
                in (DocumentStatusEnum.DRAFT.value, DocumentStatusEnum.ARCHIVED.value)
            ):
                rec_action = SyncActionTypeEnum.DEACTIVATE
            elif rag_meta_changed and target_status == DocumentStatusEnum.PUBLISHED.value:
                rec_action = SyncActionTypeEnum.REINDEX

            return ChangeDetectionResponse(
                change_type=ChangeTypeEnum.METADATA_CHANGED,
                document_id=doc.id,
                document_code=doc.code,
                current_version_number=active_version.version_number,
                next_version_number=None,
                active_hash_sha256=active_hash,
                incoming_hash_sha256=incoming_hash,
                matched_version_id=active_version.id,
                matched_version_number=active_version.version_number,
                requires_sync=rec_action is not None,
                recommended_action=rec_action,
                projected_index_status=(
                    IndexStatusEnum.STALE if rec_action else IndexStatusEnum.INDEXED
                ),
                metadata_diff=metadata_diff,
                content_diff=None,
                cross_document_matches=cross_matches,
            )

        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.UNCHANGED,
            document_id=doc.id,
            document_code=doc.code,
            current_version_number=active_version.version_number,
            next_version_number=None,
            active_hash_sha256=active_hash,
            incoming_hash_sha256=incoming_hash,
            matched_version_id=active_version.id,
            matched_version_number=active_version.version_number,
            requires_sync=False,
            recommended_action=None,
            projected_index_status=IndexStatusEnum.INDEXED,
            metadata_diff={},
            content_diff=None,
            cross_document_matches=cross_matches,
        )

    # Case 6: File hash matches an older (inactive) version -> uq_document_file_hash guard
    historical_match = next(
        (v for v in versions if v.file_hash_sha256 in candidate_hashes),
        None,
    )
    if historical_match is not None:
        return ChangeDetectionResponse(
            change_type=ChangeTypeEnum.DUPLICATE_HISTORICAL_VERSION,
            document_id=doc.id,
            document_code=doc.code,
            current_version_number=active_version.version_number if active_version else None,
            next_version_number=None,
            active_hash_sha256=active_hash,
            incoming_hash_sha256=incoming_hash,
            matched_version_id=historical_match.id,
            matched_version_number=historical_match.version_number,
            requires_sync=False,
            recommended_action=None,
            projected_index_status=IndexStatusEnum.INDEXED
            if active_version
            else IndexStatusEnum.NOT_INDEXED,
            metadata_diff=metadata_diff,
            content_diff=None,
            cross_document_matches=cross_matches,
        )

    # Case 7: Brand-new file hash for this document -> NEW_DOCUMENT (first file) or NEW_VERSION
    is_first_version = len(versions) == 0
    old_bytes = _try_read_version_bytes(active_version, storage_root=storage_root)
    content_diff = compute_text_diff_summary(
        old_bytes,
        canonical_bytes,
        mime_type=verified_mime or "application/octet-stream",
        old_file_name=active_version.file_name if active_version else None,
        new_file_name=safe_name,
        old_mime_type=active_version.mime_type if active_version else None,
        include_preview=include_text_diff,
    )

    return ChangeDetectionResponse(
        change_type=ChangeTypeEnum.NEW_DOCUMENT if is_first_version else ChangeTypeEnum.NEW_VERSION,
        document_id=doc.id,
        document_code=doc.code,
        current_version_number=active_version.version_number if active_version else None,
        next_version_number=max_version_num + 1,
        active_hash_sha256=active_hash,
        incoming_hash_sha256=incoming_hash,
        requires_sync=True,
        recommended_action=(
            SyncActionTypeEnum.INGEST if is_first_version else SyncActionTypeEnum.REINDEX
        ),
        projected_index_status=(
            IndexStatusEnum.PENDING if is_first_version else IndexStatusEnum.STALE
        ),
        metadata_diff=metadata_diff,
        content_diff=content_diff,
        cross_document_matches=cross_matches,
    )


# ---------------------------------------------------------------------------
# 2. Incremental Version Creation with SHA-256 Skip & Rollback Guard
# ---------------------------------------------------------------------------


async def create_document_version(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    actor: CurrentActor,
    file_bytes: bytes,
    filename: str,
    declared_mime_type: str | None = None,
    changelog: str | None = None,
    published_date: date | None = None,
    activate_immediately: bool = False,
    storage_root: Path | None = None,
) -> VersionUploadResponse:
    """Upload a new immutable version of an existing corpus document with SHA-256 deduplication.

    - Matching active version SHA-256 -> returns skipped=True, created=False (no disk write/job).
    - Matching inactive historical version SHA-256 -> raises 409 directing caller to rollback.
    - New content -> stores under `{school}/{code}/v{N}/{safe_name}`, creates CorpusDocumentVersion,
      cancels superseded PENDING jobs, enqueues REINDEX (or INGEST for v1), logs DocumentSyncLog
      and AuditLog. Cleans up newly written file on DB failure without touching earlier versions.
    """
    user_uuid = await verify_actor_account_exists(db, actor)

    # Lock parent document row to serialize version_number allocation (uq_document_version_number)
    doc_res = await db.execute(
        select(CorpusDocument)
        .where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
        .with_for_update()
    )
    doc = doc_res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    enforce_document_school_scope(actor, doc.school)

    # Validate file content & compute canonical checksum BEFORE touching disk
    safe_name = storage_svc.sanitize_filename(filename)
    verified_mime = storage_svc.validate_file_content(
        file_bytes,
        safe_name,
        declared_mime_type=declared_mime_type,
        allowed_mime_types=ALLOWED_MIME_TYPES,
    )
    canonical_bytes = prepare_bytes_for_storage(file_bytes, verified_mime)
    incoming_hash = compute_content_sha256(file_bytes, verified_mime)
    raw_hash = storage_svc.compute_sha256(file_bytes)
    candidate_hashes = {incoming_hash, raw_hash}

    versions = await _fetch_document_versions(db, doc.id)
    active_version = next(
        (v for v in versions if v.id == doc.current_version_id),
        next((v for v in versions if v.is_active), None),
    )

    cross_matches = await _find_cross_document_hash_matches(
        db,
        incoming_hash,
        exclude_document_id=doc.id,
        actor=actor,
    )

    # Guard A: Unchanged vs currently active version -> idempotent skip (no disk write / no job)
    if active_version is not None and active_version.file_hash_sha256 in candidate_hashes:
        current_job = await _fetch_latest_job_for_version(db, active_version.id)
        latest_ver = versions[0] if versions else active_version
        latest_ver_job = (
            current_job
            if latest_ver.id == active_version.id
            else await _fetch_latest_job_for_version(db, latest_ver.id)
        )
        idx_status = compute_document_index_status(
            doc,
            current_version=active_version,
            latest_version=latest_ver,
            current_job=current_job,
            latest_version_job=latest_ver_job,
        )
        return VersionUploadResponse(
            document_id=doc.id,
            document_code=doc.code,
            change_type=ChangeTypeEnum.UNCHANGED,
            created=False,
            skipped=True,
            activated=active_version.is_active,
            index_status=idx_status,
            version=serialize_version_detail(active_version, current_job),
            sync_job=serialize_sync_job(current_job) if current_job else None,
            sync_job_deduplicated=False,
            content_diff=None,
            cross_document_matches=cross_matches,
        )

    # Guard B: Matches another inactive version (older historical OR newer staged)
    historical_match = next(
        (v for v in versions if v.file_hash_sha256 in candidate_hashes),
        None,
    )
    if historical_match is not None:
        is_staged = (
            active_version is not None
            and historical_match.version_number > active_version.version_number
        )
        version_kind = "staged" if is_staged else "historical"
        action_verb = "promote" if is_staged else "roll back"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Content matches {version_kind} version v{historical_match.version_number} "
                f"(version_id={historical_match.id}). Use "
                f"POST /api/v1/knowledge/documents/{doc.id}/versions/"
                f"{historical_match.version_number}/activate to {action_verb}."
            ),
        )

    next_version_number = max((v.version_number for v in versions), default=0) + 1
    is_first_version = len(versions) == 0 and doc.current_version_id is None
    should_activate = activate_immediately or is_first_version

    old_bytes = _try_read_version_bytes(active_version, storage_root=storage_root)
    content_diff = compute_text_diff_summary(
        old_bytes,
        canonical_bytes,
        mime_type=verified_mime,
        old_file_name=active_version.file_name if active_version else None,
        new_file_name=safe_name,
        old_mime_type=active_version.mime_type if active_version else None,
        include_preview=True,
    )

    sub_path = build_versioned_sub_path(doc.school, doc.code, next_version_number, safe_name)
    meta = storage_svc.save_document_bytes(
        canonical_bytes,
        filename=safe_name,
        bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
        sub_path=sub_path,
        declared_mime_type=verified_mime,
        allowed_mime_types=ALLOWED_MIME_TYPES,
        storage_root=storage_root,
    )

    try:
        now = datetime.now(UTC)
        new_version = CorpusDocumentVersion(
            id=uuid.uuid4(),
            document_id=doc.id,
            version_number=next_version_number,
            file_name=meta.file_name,
            file_path_storage=meta.storage_path,
            mime_type=meta.mime_type,
            file_size_bytes=meta.file_size_bytes,
            file_hash_sha256=meta.file_hash_sha256,
            uploaded_by=user_uuid,
            changelog=changelog,
            is_active=should_activate,
            published_date=published_date,
            created_at=now,
        )
        db.add(new_version)
        await db.flush()

        if should_activate:
            await db.execute(
                update(KnowledgeChunk)
                .where(
                    KnowledgeChunk.document_id == doc.id,
                    KnowledgeChunk.version_id != new_version.id,
                    KnowledgeChunk.is_active.is_(True),
                )
                .values(is_active=False)
            )
            await db.execute(
                update(CorpusDocumentVersion)
                .where(
                    CorpusDocumentVersion.document_id == doc.id,
                    CorpusDocumentVersion.id != new_version.id,
                )
                .values(is_active=False)
            )
            doc.current_version_id = new_version.id

        doc.updated_at = now

        action_type = (
            SyncActionTypeEnum.INGEST.value
            if is_first_version
            else SyncActionTypeEnum.REINDEX.value
        )
        change_type = (
            ChangeTypeEnum.NEW_DOCUMENT if is_first_version else ChangeTypeEnum.NEW_VERSION
        )
        job, job_created = await enqueue_sync_job(
            db,
            version_id=new_version.id,
            action_type=action_type,
            pipeline_stage=SyncPipelineStageEnum.PARSING.value,
            log_message=(
                f"Detected {change_type.value}: registered version v{next_version_number} "
                f"(sha256={meta.file_hash_sha256[:12]}...)"
            ),
            log_details={
                "change_type": change_type.value,
                "document_id": str(doc.id),
                "document_code": doc.code,
                "previous_version_number": (
                    active_version.version_number if active_version else None
                ),
                "new_version_number": next_version_number,
                "previous_hash_sha256": active_version.file_hash_sha256 if active_version else None,
                "incoming_hash_sha256": meta.file_hash_sha256,
                "activated_immediately": should_activate,
                "actor_id": str(user_uuid),
            },
            cancel_superseded_for_document_id=doc.id,
        )

        record_document_audit(
            db,
            actor_type=actor_type_from_role(actor.role),
            actor_id=str(user_uuid),
            action="knowledge.document.version_create",
            entity_id=doc.id,
            reason=changelog,
            metadata={
                "document_code": doc.code,
                "school": doc.school,
                "change_type": change_type.value,
                "version_id": str(new_version.id),
                "version_number": next_version_number,
                "file_hash_sha256": meta.file_hash_sha256,
                "activated_immediately": should_activate,
                "sync_job_id": str(job.id),
            },
        )

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        storage_svc.delete_document_bytes(meta.storage_path, storage_root=storage_root)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate version number or content checksum conflict detected.",
        ) from exc
    except Exception:
        await db.rollback()
        storage_svc.delete_document_bytes(meta.storage_path, storage_root=storage_root)
        raise

    effective_current = new_version if should_activate else active_version
    current_job = (
        job
        if effective_current is None or effective_current.id == new_version.id
        else await _fetch_latest_job_for_version(db, effective_current.id)
    )
    idx_status = compute_document_index_status(
        doc,
        current_version=effective_current,
        latest_version=new_version,
        current_job=current_job,
        latest_version_job=job,
    )
    return VersionUploadResponse(
        document_id=doc.id,
        document_code=doc.code,
        change_type=change_type,
        created=True,
        skipped=False,
        activated=should_activate,
        index_status=idx_status,
        version=serialize_version_detail(new_version, job),
        sync_job=serialize_sync_job(job),
        sync_job_deduplicated=not job_created,
        content_diff=content_diff,
        cross_document_matches=cross_matches,
    )


# ---------------------------------------------------------------------------
# 3. Atomic Blue-Green Version Promotion & Rollback (SA-14 §6.2 + SA-57 Hook)
# ---------------------------------------------------------------------------


async def promote_document_version(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    target_version_id: uuid.UUID | None = None,
    target_version_number: int | None = None,
    actor: CurrentActor | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str = "sync_worker",
    reason: str | None = None,
    enqueue_reindex_if_unindexed: bool = True,
    commit: bool = True,
) -> VersionActivateResponse:
    """Atomically promote a staged version or roll back to a prior version (SA-14 §6.2).

    Reusable by:
    - REST rollback/promotion endpoint (`commit=True`, `actor=CurrentActor`)
    - Downstream SA-57 indexing worker (`commit=False`, `actor_type="SYSTEM"`) inside the
      worker's final commit transaction so `current_version_id`, `is_active`, `knowledge.chunks`,
      and `DocumentSyncJob.status = 'SYNCED'` swap atomically with zero RAG downtime.
    """
    if target_version_id is None and target_version_number is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'target_version_id' or 'target_version_number' must be provided.",
        )

    effective_actor_type = actor_type
    effective_actor_id = actor_id
    if actor is not None:
        user_uuid = await verify_actor_account_exists(db, actor)
        effective_actor_type = actor_type_from_role(actor.role)
        effective_actor_id = str(user_uuid)

    doc_res = await db.execute(
        select(CorpusDocument)
        .where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
        .with_for_update()
    )
    doc = doc_res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    if actor is not None:
        enforce_document_school_scope(actor, doc.school)

    versions = await _fetch_document_versions(db, doc.id)
    target_version: CorpusDocumentVersion | None = None
    for ver in versions:
        if target_version_id is not None and ver.id == target_version_id:
            target_version = ver
            break
        if target_version_number is not None and ver.version_number == target_version_number:
            target_version = ver
            break

    if target_version is None:
        identifier = (
            f"v{target_version_number}" if target_version_number is not None else target_version_id
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version '{identifier}' not found for document '{document_id}'.",
        )

    previous_version = next(
        (v for v in versions if v.id == doc.current_version_id),
        next((v for v in versions if v.is_active), None),
    )
    prev_id = previous_version.id if previous_version else None
    prev_num = previous_version.version_number if previous_version else None
    is_rollback = prev_num is not None and target_version.version_number < prev_num

    # Exact 5-step atomic switch from architecture/document_management_and_sync.md §6.2
    deact_chunks_res = await db.execute(
        update(KnowledgeChunk)
        .where(
            KnowledgeChunk.document_id == doc.id,
            KnowledgeChunk.version_id != target_version.id,
        )
        .values(is_active=False)
    )
    deactivated_chunks = _safe_rowcount(deact_chunks_res)

    await db.execute(
        update(CorpusDocumentVersion)
        .where(
            CorpusDocumentVersion.document_id == doc.id,
            CorpusDocumentVersion.id != target_version.id,
        )
        .values(is_active=False)
    )

    target_version.is_active = True
    doc.current_version_id = target_version.id
    doc.updated_at = datetime.now(UTC)

    act_chunks_res = await db.execute(
        update(KnowledgeChunk)
        .where(
            KnowledgeChunk.document_id == doc.id,
            KnowledgeChunk.version_id == target_version.id,
        )
        .values(is_active=True)
    )
    activated_chunks = _safe_rowcount(act_chunks_res)

    action_label = "rollback" if is_rollback else "activation"
    event_label = "ROLLBACK" if is_rollback else "ACTIVATE"

    # Always cancel superseded PENDING jobs on other versions so SA-57 cannot overwrite activation
    await cancel_superseded_pending_jobs(
        db,
        document_id=doc.id,
        exclude_version_id=target_version.id,
        reason=(
            f"Superseded by {action_label} to version v{target_version.version_number}."
        ),
    )

    sync_job: DocumentSyncJob | None = None
    if activated_chunks == 0 and enqueue_reindex_if_unindexed:
        sync_job, _ = await enqueue_sync_job(
            db,
            version_id=target_version.id,
            action_type=SyncActionTypeEnum.REINDEX.value,
            pipeline_stage=SyncPipelineStageEnum.PARSING.value,
            log_message=(
                f"Version v{target_version.version_number} activated via {action_label} "
                "with 0 existing chunks; queued REINDEX."
            ),
            log_details={
                "event": event_label,
                "document_id": str(doc.id),
                "previous_version_number": prev_num,
                "active_version_number": target_version.version_number,
                "reason": reason,
            },
        )
    else:
        existing_target_job = await _fetch_latest_job_for_version(db, target_version.id)
        if existing_target_job is not None:
            append_sync_log(
                db,
                job_id=existing_target_job.id,
                stage=SYNC_LOG_STAGE_CHANGE_DETECTOR,
                level=SyncLogLevelEnum.INFO.value,
                message=(
                    f"Version v{target_version.version_number} activated via {action_label} "
                    f"(previous=v{prev_num}, chunks_reactivated={activated_chunks})."
                ),
                details={
                    "event": event_label,
                    "document_id": str(doc.id),
                    "previous_version_number": prev_num,
                    "active_version_number": target_version.version_number,
                    "deactivated_chunks_count": deactivated_chunks,
                    "activated_chunks_count": activated_chunks,
                    "reason": reason,
                },
            )

    audit_action = (
        "knowledge.document.version_rollback"
        if is_rollback
        else "knowledge.document.version_activate"
    )
    record_document_audit(
        db,
        actor_type=effective_actor_type,
        actor_id=effective_actor_id,
        action=audit_action,
        entity_id=doc.id,
        reason=reason,
        metadata={
            "document_code": doc.code,
            "previous_version_id": str(prev_id) if prev_id else None,
            "previous_version_number": prev_num,
            "active_version_id": str(target_version.id),
            "active_version_number": target_version.version_number,
            "is_rollback": is_rollback,
            "deactivated_chunks_count": deactivated_chunks,
            "activated_chunks_count": activated_chunks,
            "sync_job_id": str(sync_job.id) if sync_job else None,
        },
    )

    if commit:
        await db.commit()
    else:
        await db.flush()

    effective_job = sync_job or await _fetch_latest_job_for_version(db, target_version.id)
    latest_ver = versions[0] if versions else target_version
    latest_ver_job = (
        effective_job
        if latest_ver.id == target_version.id
        else await _fetch_latest_job_for_version(db, latest_ver.id)
    )
    idx_status = compute_document_index_status(
        doc,
        current_version=target_version,
        latest_version=latest_ver,
        current_job=effective_job,
        latest_version_job=latest_ver_job,
    )
    return VersionActivateResponse(
        document_id=doc.id,
        previous_version_id=prev_id,
        previous_version_number=prev_num,
        active_version_id=target_version.id,
        active_version_number=target_version.version_number,
        is_rollback=is_rollback,
        index_status=idx_status,
        deactivated_chunks_count=deactivated_chunks,
        activated_chunks_count=activated_chunks,
        sync_job=serialize_sync_job(sync_job) if sync_job else None,
    )


# ---------------------------------------------------------------------------
# 4. Metadata Change & Soft-Delete Sync Recording (Wired into PATCH & DELETE)
# ---------------------------------------------------------------------------


async def record_document_metadata_change(
    db: AsyncSession,
    *,
    doc: CorpusDocument,
    payload: DocumentUpdate,
    actor: CurrentActor,
) -> tuple[CorpusDocument, dict[str, FieldDiff], DocumentSyncJob | None]:
    """Detect metadata diffs on PATCH, apply updates, enqueue REINDEX/DEACTIVATE, and audit."""
    enforce_document_school_scope(actor, doc.school)
    if payload.school_code is not None:
        enforce_document_school_scope(actor, payload.school_code.value)

    fields_set = payload.model_fields_set
    incoming: dict[str, Any] = {}
    if payload.title is not None:
        incoming["title"] = payload.title
    if "description" in fields_set:
        incoming["description"] = payload.description
    if payload.school_code is not None:
        incoming["school"] = payload.school_code.value
    if payload.doc_type is not None:
        incoming["category"] = payload.doc_type.value
    if "target_program_id" in fields_set:
        incoming["target_program_id"] = payload.target_program_id

    if payload.status is not None:
        incoming["status"] = payload.status.value
    elif payload.is_published is not None:
        incoming["status"] = (
            DocumentStatusEnum.PUBLISHED.value
            if payload.is_published
            else DocumentStatusEnum.DRAFT.value
        )

    previous_status = doc.status
    metadata_diff, rag_relevant_changed = compute_metadata_diff(doc, incoming)

    # No-op guard: if nothing actually changed, avoid redundant DB sync jobs / audit noise
    if not metadata_diff:
        return doc, {}, None

    for field, value in incoming.items():
        setattr(doc, field, value)
    doc.updated_at = datetime.now(UTC)

    sync_job: DocumentSyncJob | None = None
    if doc.status == DocumentStatusEnum.DELETED.value:
        # Transitioning to DELETED via PATCH must match soft-delete vector/version deactivation
        versions = await _fetch_document_versions(db, doc.id)
        deact_chunks_res = await db.execute(
            update(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == doc.id)
            .values(is_active=False)
        )
        deactivated_chunks = _safe_rowcount(deact_chunks_res)
        await db.execute(
            update(CorpusDocumentVersion)
            .where(CorpusDocumentVersion.document_id == doc.id)
            .values(is_active=False)
        )
        for ver in versions:
            ver.is_active = False

        await cancel_superseded_pending_jobs(
            db,
            document_id=doc.id,
            reason="Cancelled pending sync job due to document deletion via PATCH.",
        )

        target_version_id = doc.current_version_id or (versions[0].id if versions else None)
        if target_version_id is not None:
            sync_job, _ = await enqueue_sync_job(
                db,
                version_id=target_version_id,
                action_type=SyncActionTypeEnum.DEACTIVATE.value,
                pipeline_stage=SyncPipelineStageEnum.INDEXING.value,
                log_message=(
                    f"Document '{doc.code}' transitioned to DELETED via PATCH; "
                    "deactivated versions & chunks and queued DEACTIVATE job."
                ),
                log_details={
                    "change_type": ChangeTypeEnum.DELETED.value,
                    "document_id": str(doc.id),
                    "changed_fields": sorted(metadata_diff.keys()),
                    "deactivated_versions_count": len(versions),
                    "deactivated_chunks_count": deactivated_chunks,
                },
            )
    elif doc.current_version_id is not None:
        if previous_status == DocumentStatusEnum.PUBLISHED.value and doc.status in (
            DocumentStatusEnum.DRAFT.value,
            DocumentStatusEnum.ARCHIVED.value,
        ):
            await db.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc.id)
                .values(is_active=False)
            )
            sync_job, _ = await enqueue_sync_job(
                db,
                version_id=doc.current_version_id,
                action_type=SyncActionTypeEnum.DEACTIVATE.value,
                pipeline_stage=SyncPipelineStageEnum.INDEXING.value,
                log_message=(
                    f"Document status changed {previous_status} -> {doc.status}; "
                    "deactivated chunks and queued DEACTIVATE sync job."
                ),
                log_details={
                    "change_type": ChangeTypeEnum.METADATA_CHANGED.value,
                    "document_id": str(doc.id),
                    "changed_fields": sorted(metadata_diff.keys()),
                    "previous_status": previous_status,
                    "new_status": doc.status,
                },
                cancel_superseded_for_document_id=doc.id,
            )
        elif rag_relevant_changed and doc.status == DocumentStatusEnum.PUBLISHED.value:
            versions = await _fetch_document_versions(db, doc.id)
            current_ver = next(
                (v for v in versions if v.id == doc.current_version_id),
                None,
            )
            sync_job, _ = await enqueue_sync_job(
                db,
                version_id=doc.current_version_id,
                action_type=SyncActionTypeEnum.REINDEX.value,
                pipeline_stage=SyncPipelineStageEnum.PARSING.value,
                log_message=(
                    "RAG-relevant metadata updated "
                    f"({', '.join(sorted(metadata_diff.keys()))}); queued REINDEX sync job."
                ),
                log_details={
                    "change_type": ChangeTypeEnum.METADATA_CHANGED.value,
                    "document_id": str(doc.id),
                    "changed_fields": sorted(metadata_diff.keys()),
                },
                cancel_superseded_for_document_id=doc.id if current_ver is not None else None,
                cancel_max_version_number=(
                    current_ver.version_number if current_ver is not None else None
                ),
            )

    record_document_audit(
        db,
        actor_type=actor_type_from_role(actor.role),
        actor_id=actor.account_id,
        action="knowledge.document.metadata_update",
        entity_id=doc.id,
        metadata={
            "document_code": doc.code,
            "changed_fields": sorted(metadata_diff.keys()),
            "rag_relevant_changed": rag_relevant_changed,
            "sync_job_id": str(sync_job.id) if sync_job else None,
        },
    )

    await db.commit()
    await db.refresh(doc)
    return doc, metadata_diff, sync_job


async def record_document_deletion(
    db: AsyncSession,
    *,
    doc: CorpusDocument,
    actor: CurrentActor,
    purge_storage: bool = False,
    storage_root: Path | None = None,
) -> DocumentSyncJob | None:
    """Soft-delete document, deactivate versions & chunks immediately, enqueue DEACTIVATE/PURGE."""
    enforce_document_school_scope(actor, doc.school)

    versions = await _fetch_document_versions(db, doc.id)
    storage_paths_to_purge = [v.file_path_storage for v in versions if v.file_path_storage]

    doc.status = DocumentStatusEnum.DELETED.value
    doc.updated_at = datetime.now(UTC)

    # Immediate vector & version deactivation (SA-14 §6.3)
    deact_chunks_res = await db.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == doc.id)
        .values(is_active=False)
    )
    deactivated_chunks = _safe_rowcount(deact_chunks_res)

    await db.execute(
        update(CorpusDocumentVersion)
        .where(CorpusDocumentVersion.document_id == doc.id)
        .values(is_active=False)
    )
    for ver in versions:
        ver.is_active = False

    await cancel_superseded_pending_jobs(
        db,
        document_id=doc.id,
        reason="Cancelled pending sync job due to document deletion.",
    )

    target_version_id = doc.current_version_id or (versions[0].id if versions else None)
    sync_job: DocumentSyncJob | None = None
    if target_version_id is not None:
        action = (
            SyncActionTypeEnum.PURGE.value
            if purge_storage
            else SyncActionTypeEnum.DEACTIVATE.value
        )
        sync_job, _ = await enqueue_sync_job(
            db,
            version_id=target_version_id,
            action_type=action,
            pipeline_stage=SyncPipelineStageEnum.INDEXING.value,
            log_message=(
                f"Document '{doc.code}' soft-deleted (purge_storage={purge_storage}); "
                f"deactivated versions & chunks and queued {action} job."
            ),
            log_details={
                "change_type": ChangeTypeEnum.DELETED.value,
                "document_id": str(doc.id),
                "document_code": doc.code,
                "purge_storage": purge_storage,
                "deactivated_versions_count": len(versions),
                "deactivated_chunks_count": deactivated_chunks,
            },
        )

    record_document_audit(
        db,
        actor_type=actor_type_from_role(actor.role),
        actor_id=actor.account_id,
        action="knowledge.document.delete",
        entity_id=doc.id,
        metadata={
            "document_code": doc.code,
            "school": doc.school,
            "purge_storage": purge_storage,
            "deactivated_versions_count": len(versions),
            "deactivated_chunks_count": deactivated_chunks,
            "sync_job_id": str(sync_job.id) if sync_job else None,
        },
    )

    await db.commit()

    if purge_storage:
        for storage_path in storage_paths_to_purge:
            storage_svc.delete_document_bytes(storage_path, storage_root=storage_root)

    return sync_job


# ---------------------------------------------------------------------------
# 5. Version History, Sync Job Queue & Diagnostic Log Queries (SA-56 / SA-57)
# ---------------------------------------------------------------------------


async def list_pending_sync_jobs(
    db: AsyncSession,
    *,
    action_types: list[str] | None = None,
    limit: int = 50,
) -> list[DocumentSyncJob]:
    """Return FIFO queue of PENDING DocumentSyncJobs ready for SA-57 / SA-58 workers."""
    stmt = select(DocumentSyncJob).where(
        DocumentSyncJob.status == SyncJobStatusEnum.PENDING.value
    )
    if action_types:
        stmt = stmt.where(DocumentSyncJob.action_type.in_(action_types))
    stmt = stmt.order_by(DocumentSyncJob.created_at.asc()).limit(limit)
    res = await db.execute(stmt)
    rows = res.scalars().all()
    if not isinstance(rows, list):
        rows = list(rows)
    return rows


async def list_document_versions_with_jobs(
    db: AsyncSession,
    *,
    doc: CorpusDocument,
) -> DocumentVersionListResponse:
    """Return all versions for a document (newest first) enriched with their latest sync job."""
    versions = await _fetch_document_versions(db, doc.id)
    version_ids = [v.id for v in versions]
    jobs = await _fetch_jobs_for_versions(db, version_ids)

    latest_job_by_version: dict[uuid.UUID, DocumentSyncJob] = {}
    for job in jobs:
        if job.version_id not in latest_job_by_version:
            latest_job_by_version[job.version_id] = job

    current_version = next(
        (v for v in versions if v.id == doc.current_version_id),
        next((v for v in versions if v.is_active), None),
    )
    latest_version = versions[0] if versions else None
    current_job = (
        latest_job_by_version.get(current_version.id)
        if current_version is not None
        else None
    )
    latest_ver_job = (
        latest_job_by_version.get(latest_version.id)
        if latest_version is not None
        else None
    )
    idx_status = compute_document_index_status(
        doc,
        current_version=current_version,
        latest_version=latest_version,
        current_job=current_job,
        latest_version_job=latest_ver_job,
    )

    items = [
        serialize_version_detail(v, latest_job_by_version.get(v.id))
        for v in versions
    ]
    return DocumentVersionListResponse(
        document_id=doc.id,
        document_code=doc.code,
        current_version_id=doc.current_version_id,
        index_status=idx_status,
        total_versions=len(items),
        versions=items,
    )


async def list_document_sync_jobs_with_logs(
    db: AsyncSession,
    *,
    doc: CorpusDocument,
    status_filter: str | None = None,
    limit: int = 50,
) -> DocumentSyncJobListResponse:
    """Return full sync job & change-log history for a document."""
    versions = await _fetch_document_versions(db, doc.id)
    version_num_by_id = {v.id: v.version_number for v in versions}
    version_ids = [v.id for v in versions]

    jobs = await _fetch_jobs_for_versions(db, version_ids, status_filter=status_filter)
    limited_jobs = jobs[:limit]
    job_ids = [job.id for job in limited_jobs]

    logs_by_job_id: dict[uuid.UUID, list[DocumentSyncLog]] = {}
    if job_ids:
        logs_res = await db.execute(
            select(DocumentSyncLog)
            .where(DocumentSyncLog.job_id.in_(job_ids))
            .order_by(DocumentSyncLog.created_at.asc())
        )
        all_logs = logs_res.scalars().all()
        if not isinstance(all_logs, list):
            all_logs = list(all_logs)
        for lg in all_logs:
            logs_by_job_id.setdefault(lg.job_id, []).append(lg)

    items: list[DocumentSyncJobWithLogsResponse] = []
    for job in limited_jobs:
        raw_logs = logs_by_job_id.get(job.id, [])
        base_job = serialize_sync_job(job)
        items.append(
            DocumentSyncJobWithLogsResponse(
                **base_job.model_dump(),
                version_number=version_num_by_id.get(job.version_id),
                logs=[serialize_sync_log(lg) for lg in raw_logs],
            )
        )

    current_version = next(
        (v for v in versions if v.id == doc.current_version_id),
        next((v for v in versions if v.is_active), None),
    )
    latest_version = versions[0] if versions else None
    all_jobs = jobs if status_filter is None else await _fetch_jobs_for_versions(db, version_ids)
    latest_job_by_ver: dict[uuid.UUID, DocumentSyncJob] = {}
    for job in all_jobs:
        if job.version_id not in latest_job_by_ver:
            latest_job_by_ver[job.version_id] = job
    current_job = (
        latest_job_by_ver.get(current_version.id)
        if current_version is not None
        else None
    )
    latest_ver_job = (
        latest_job_by_ver.get(latest_version.id)
        if latest_version is not None
        else None
    )
    idx_status = compute_document_index_status(
        doc,
        current_version=current_version,
        latest_version=latest_version,
        current_job=current_job,
        latest_version_job=latest_ver_job,
    )
    return DocumentSyncJobListResponse(
        document_id=doc.id,
        index_status=idx_status,
        items=items,
        total=len(jobs),
    )


async def get_sync_job_logs(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    actor: CurrentActor,
) -> DocumentSyncLogListResponse:
    """Return granular diagnostic logs for a single DocumentSyncJob (RBAC & school-scoped)."""
    job_res = await db.execute(select(DocumentSyncJob).where(DocumentSyncJob.id == job_id))
    job = job_res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sync job '{job_id}' not found.",
        )

    ver_res = await db.execute(
        select(CorpusDocumentVersion).where(CorpusDocumentVersion.id == job.version_id)
    )
    version = ver_res.scalar_one_or_none()
    if version is not None:
        doc_res = await db.execute(
            select(CorpusDocument).where(CorpusDocument.id == version.document_id)
        )
        doc = doc_res.scalar_one_or_none()
        if doc is not None:
            enforce_document_school_scope(actor, doc.school)

    logs_res = await db.execute(
        select(DocumentSyncLog)
        .where(DocumentSyncLog.job_id == job_id)
        .order_by(DocumentSyncLog.created_at.asc())
    )
    raw_logs = logs_res.scalars().all()
    if not isinstance(raw_logs, list):
        raw_logs = list(raw_logs)

    serialized = [serialize_sync_log(lg) for lg in raw_logs]
    return DocumentSyncLogListResponse(
        job_id=job_id,
        items=serialized,
        total=len(serialized),
    )
