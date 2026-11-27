"""Official knowledge corpus REST endpoints for SA-49, SA-50 & SA-56.

Manages RAG knowledge documents across IIT, ISB, ISA, and nau_common:
- Uploading PDF/Markdown/text/DOCX brochures (SA-49 / SA-50)
- Structured text/markdown creation
- Listing with filtering (school_code, index_status, is_published)
- Metadata & publication state updates with automatic change detection & sync job enqueueing
- Binary file downloads with ETag (active version + version-specific historical downloads)
- SHA-256 dry-run change detection, incremental immutable versioning, atomic promotion & rollback,
  sync job queue inspection, and granular sync logs (SA-56)
- Strict RBAC + school-scope isolation for NAU_RESPONSABLE actors.
"""

import re
import unicodedata
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import Path as FastAPIPath
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentActor, require_roles
from app.models.catalog import Program
from app.models.documents_rag import CorpusDocument, CorpusDocumentVersion, DocumentSyncJob
from app.schemas.knowledge import (
    ALLOWED_MIME_TYPES,
    MAX_DOCUMENT_FILE_SIZE_BYTES,
    ChangeDetectionResponse,
    ChangeTypeEnum,
    DocumentCategoryEnum,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusEnum,
    DocumentSyncJobListResponse,
    DocumentSyncLogListResponse,
    DocumentUpdate,
    DocumentVersionListResponse,
    DocumentVersionResponse,
    IndexStatusEnum,
    SchoolCodeEnum,
    SyncActionTypeEnum,
    SyncJobStatusEnum,
    SyncLogLevelEnum,
    SyncPipelineStageEnum,
    VersionActivateRequest,
    VersionActivateResponse,
    VersionUploadResponse,
)
from app.services import document_sync_service as sync_svc
from app.services import file_storage_service as storage_svc

router = APIRouter()

# Post-review write RBAC: Responsable (school-scoped) & Admin only (Direction write retired)
RequireDocumentAdmin = Annotated[
    CurrentActor,
    Depends(require_roles("admin", "responsable")),
]

# Strict SA-56 write RBAC for version mutations, rollback, and deletion (rbac_matrix.md & SA-14 §7)
RequireDocumentVersionWriter = Annotated[
    CurrentActor,
    Depends(require_roles("admin", "responsable")),
]

# Read-only staff RBAC for version history, change logs, and sync diagnostics (SA-14 §7)
RequireDocumentStaffReader = Annotated[
    CurrentActor,
    Depends(require_roles("admin", "responsable", "direction", "rh")),
]

STAFF_ROLES = {"admin", "responsable", "direction", "rh"}


def _build_content_disposition(file_name: str) -> str:
    """Build an ASCII-safe + RFC 5987 UTF-8 Content-Disposition attachment header."""
    cleaned = file_name.replace("\r", "").replace("\n", "").replace("\x00", "").strip()
    if not cleaned:
        cleaned = "document"

    ascii_candidate = (
        cleaned.replace("\u0153", "oe")
        .replace("\u0152", "OE")
        .replace("\u00e6", "ae")
        .replace("\u00c6", "AE")
        .replace("\u2019", "_")
        .replace("\u2018", "_")
    )
    normalized = unicodedata.normalize("NFKD", ascii_candidate)
    ascii_raw = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_safe = re.sub(r'["\\;\x00-\x1f\x7f]', "_", ascii_raw).strip(" .")
    stem, dot, ext = ascii_safe.rpartition(".")
    if dot and not stem.strip("_- "):
        ascii_safe = f"document.{ext}" if ext else "document"
    elif not ascii_safe or not ascii_safe.strip("_- "):
        ascii_safe = "document"

    encoded_utf8 = quote(cleaned, safe="")
    return f'attachment; filename="{ascii_safe}"; filename*=UTF-8\'\'{encoded_utf8}'


def _build_document_response(
    doc: CorpusDocument,
    latest_job: DocumentSyncJob | None = None,
    current_version: CorpusDocumentVersion | None = None,
    latest_version: CorpusDocumentVersion | None = None,
    *,
    current_job: DocumentSyncJob | None = None,
    latest_version_job: DocumentSyncJob | None = None,
) -> DocumentResponse:
    effective_latest_version = latest_version or current_version
    index_status = sync_svc.compute_document_index_status(
        doc,
        current_version=current_version,
        latest_version=effective_latest_version,
        latest_job=latest_job,
        current_job=current_job,
        latest_version_job=latest_version_job,
    )

    version_resp = None
    if current_version:
        version_resp = DocumentVersionResponse(
            id=current_version.id,
            version_number=current_version.version_number,
            file_name=current_version.file_name,
            file_path_storage=current_version.file_path_storage,
            mime_type=current_version.mime_type,
            file_size_bytes=current_version.file_size_bytes,
            file_hash_sha256=current_version.file_hash_sha256,
            changelog=current_version.changelog,
            published_date=current_version.published_date,
            is_active=current_version.is_active,
            created_at=current_version.created_at,
        )

    latest_version_number = (
        effective_latest_version.version_number if effective_latest_version else None
    )

    return DocumentResponse(
        id=doc.id,
        code=doc.code,
        title=doc.title,
        description=doc.description,
        school_code=doc.school,
        doc_type=doc.category,
        target_program_id=doc.target_program_id,
        current_version_id=doc.current_version_id,
        latest_version_number=latest_version_number,
        status=doc.status,
        is_published=(doc.status == DocumentStatusEnum.PUBLISHED.value),
        index_status=index_status,
        created_by=doc.created_by,
        created_at=doc.created_at or datetime.now(UTC),
        updated_at=doc.updated_at or datetime.now(UTC),
        current_version=version_resp,
    )


async def _load_document_or_404(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> CorpusDocument:
    stmt = select(CorpusDocument).where(CorpusDocument.id == document_id)
    if not include_deleted:
        stmt = stmt.where(CorpusDocument.status != DocumentStatusEnum.DELETED.value)
    if for_update:
        stmt = stmt.with_for_update()
    doc_res = await db.execute(stmt)
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )
    return doc


async def _load_doc_version_and_job(
    db: AsyncSession,
    doc: CorpusDocument,
) -> tuple[
    CorpusDocumentVersion | None,
    CorpusDocumentVersion | None,
    DocumentSyncJob | None,
    DocumentSyncJob | None,
]:
    current_version: CorpusDocumentVersion | None = None
    latest_version: CorpusDocumentVersion | None = None
    current_job: DocumentSyncJob | None = None
    latest_version_job: DocumentSyncJob | None = None

    if doc.current_version_id:
        v_res = await db.execute(
            select(CorpusDocumentVersion).where(CorpusDocumentVersion.id == doc.current_version_id)
        )
        current_version = v_res.scalar_one_or_none()

    latest_v_res = await db.execute(
        select(CorpusDocumentVersion)
        .where(CorpusDocumentVersion.document_id == doc.id)
        .order_by(CorpusDocumentVersion.version_number.desc())
        .limit(1)
    )
    latest_version = latest_v_res.scalar_one_or_none() or current_version

    if current_version is not None:
        cur_job_res = await db.execute(
            select(DocumentSyncJob)
            .where(DocumentSyncJob.version_id == current_version.id)
            .order_by(DocumentSyncJob.created_at.desc())
            .limit(1)
        )
        current_job = cur_job_res.scalar_one_or_none()

    if latest_version is not None:
        if current_version is not None and latest_version.id == current_version.id:
            latest_version_job = current_job
        else:
            lat_job_res = await db.execute(
                select(DocumentSyncJob)
                .where(DocumentSyncJob.version_id == latest_version.id)
                .order_by(DocumentSyncJob.created_at.desc())
                .limit(1)
            )
            latest_version_job = lat_job_res.scalar_one_or_none()

    return current_version, latest_version, current_job, latest_version_job


async def _read_upload_or_content(
    file: UploadFile | None,
    content: str | None,
) -> tuple[bytes | None, str | None, str | None]:
    """Read bounded bytes from UploadFile or inline markdown content with MIME inference."""
    has_file = (
        file is not None
        and getattr(file, "filename", None)
        and file.filename.strip() != ""
    )
    if has_file:
        file_bytes = await file.read(MAX_DOCUMENT_FILE_SIZE_BYTES + 1)
        safe_name = storage_svc.sanitize_filename(file.filename or "uploaded_doc")
        verified_mime = storage_svc.validate_file_content(
            file_bytes,
            safe_name,
            declared_mime_type=file.content_type,
            allowed_mime_types=ALLOWED_MIME_TYPES,
            max_file_size=MAX_DOCUMENT_FILE_SIZE_BYTES,
        )
        return file_bytes, safe_name, verified_mime

    if content is not None and content.strip():
        return content.encode("utf-8"), "content.md", "text/markdown"

    return None, None, None


# ---------------------------------------------------------------------------
# Collection Routes & Static SA-56 Routes (MUST precede /{document_id}!)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or upload an official knowledge corpus document (Multipart or structured)",
)
async def create_document(
    actor: RequireDocumentAdmin,
    db: AsyncSession = Depends(get_db),
    file: UploadFile | None = File(default=None),
    code: str = Form(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    title: str = Form(..., min_length=3, max_length=255),
    school_code: SchoolCodeEnum = Form(...),
    doc_type: DocumentCategoryEnum = Form(...),
    description: str | None = Form(default=None, max_length=2000),
    is_published: bool = Form(default=False),
    target_program_id: str | None = Form(default=None),
    content: str | None = Form(default=None),
) -> DocumentResponse:
    # 1. Verify actor.account_id is a valid existing account & enforce school scope
    user_uuid = await sync_svc.verify_actor_account_exists(db, actor)
    sync_svc.enforce_document_school_scope(actor, school_code.value)

    # 2. Validate target_program_id if provided
    parsed_program_id: uuid.UUID | None = None
    if target_program_id and target_program_id.strip():
        try:
            parsed_program_id = uuid.UUID(target_program_id.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="target_program_id must be a valid UUID.",
            ) from exc

        prog_res = await db.execute(select(Program.id).where(Program.id == parsed_program_id))
        if not prog_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Program '{parsed_program_id}' not found.",
            )

    # 3. Check code uniqueness
    existing_stmt = select(CorpusDocument).where(CorpusDocument.code == code)
    res = await db.execute(existing_stmt)
    if res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document with code '{code}' already exists.",
        )

    # 4. Validate uploaded file or inline content BEFORE mutating DB or disk
    raw_bytes, safe_name, mime_type = await _read_upload_or_content(file, content)

    doc_id = uuid.uuid4()
    initial_status = (
        DocumentStatusEnum.PUBLISHED.value if is_published else DocumentStatusEnum.DRAFT.value
    )

    doc = CorpusDocument(
        id=doc_id,
        code=code,
        title=title,
        description=description,
        school=school_code.value,
        category=doc_type.value,
        target_program_id=parsed_program_id,
        status=initial_status,
        created_by=user_uuid,
    )

    current_version: CorpusDocumentVersion | None = None
    latest_job: DocumentSyncJob | None = None
    saved_storage_paths: list[str] = []

    try:
        db.add(doc)
        # Flush the document FIRST so it exists in DB before we reference it via FK
        await db.flush()

        if raw_bytes is not None and safe_name is not None and mime_type is not None:
            canonical_bytes = sync_svc.prepare_bytes_for_storage(raw_bytes, mime_type)
            versioned_sub_path = sync_svc.build_versioned_sub_path(
                school_code.value,
                doc.code,
                1,
                safe_name,
            )
            meta = storage_svc.save_document_bytes(
                canonical_bytes,
                filename=safe_name,
                bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
                sub_path=versioned_sub_path,
                declared_mime_type=mime_type,
                allowed_mime_types=ALLOWED_MIME_TYPES,
            )
            saved_storage_paths.append(meta.storage_path)
            version_id = uuid.uuid4()

            current_version = CorpusDocumentVersion(
                id=version_id,
                document_id=doc.id,
                version_number=1,
                file_name=meta.file_name,
                file_path_storage=meta.storage_path,
                mime_type=meta.mime_type,
                file_size_bytes=meta.file_size_bytes,
                file_hash_sha256=meta.file_hash_sha256,
                uploaded_by=user_uuid,
                is_active=True,
            )
            db.add(current_version)
            await db.flush()
            doc.current_version_id = version_id

            latest_job = DocumentSyncJob(
                id=uuid.uuid4(),
                version_id=version_id,
                action_type=SyncActionTypeEnum.INGEST.value,
                pipeline_stage=SyncPipelineStageEnum.PARSING.value,
                status=SyncJobStatusEnum.PENDING.value,
            )
            db.add(latest_job)
            await db.flush()

            sync_svc.append_sync_log(
                db,
                job_id=latest_job.id,
                stage=sync_svc.SYNC_LOG_STAGE_CHANGE_DETECTOR,
                level=SyncLogLevelEnum.INFO.value,
                message=(
                    f"Initial document version v1 registered (NEW_DOCUMENT, "
                    f"sha256={meta.file_hash_sha256[:12]}...)."
                ),
                details={
                    "change_type": ChangeTypeEnum.NEW_DOCUMENT.value,
                    "document_id": str(doc.id),
                    "document_code": doc.code,
                    "version_number": 1,
                    "file_hash_sha256": meta.file_hash_sha256,
                },
            )

        sync_svc.record_document_audit(
            db,
            actor_type=sync_svc.actor_type_from_role(actor.role),
            actor_id=str(user_uuid),
            action="knowledge.document.create",
            entity_id=doc.id,
            metadata={
                "document_code": doc.code,
                "school": doc.school,
                "category": doc.category,
                "status": doc.status,
                "has_initial_version": current_version is not None,
                "sync_job_id": str(latest_job.id) if latest_job else None,
            },
        )

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        for storage_path in saved_storage_paths:
            storage_svc.delete_document_bytes(storage_path)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document with code '{code}' already exists.",
        ) from exc
    except Exception:
        await db.rollback()
        for storage_path in saved_storage_paths:
            storage_svc.delete_document_bytes(storage_path)
        raise

    # Refresh strictly AFTER commit so a post-commit failure can never delete committed disk files
    await db.refresh(doc)

    return _build_document_response(doc, latest_job, current_version, current_version)


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List official corpus documents with school and status filters",
)
async def list_documents(
    db: AsyncSession = Depends(get_db),
    school_code: SchoolCodeEnum | None = Query(default=None, description="Filter by school"),
    index_status: IndexStatusEnum | None = Query(default=None, description="RAG index status"),
    is_published: bool | None = Query(default=None, description="Filter by publication state"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DocumentListResponse:
    query = select(CorpusDocument).where(CorpusDocument.status != DocumentStatusEnum.DELETED.value)

    if school_code is not None:
        query = query.where(CorpusDocument.school == school_code.value)
    if is_published is not None:
        if is_published:
            query = query.where(CorpusDocument.status == DocumentStatusEnum.PUBLISHED.value)
        else:
            query = query.where(CorpusDocument.status != DocumentStatusEnum.PUBLISHED.value)

    query = query.order_by(CorpusDocument.created_at.desc())
    docs_res = await db.execute(query)
    docs = list(docs_res.scalars().all())

    versions_by_doc: dict[uuid.UUID, list[CorpusDocumentVersion]] = {}
    version_by_id: dict[uuid.UUID, CorpusDocumentVersion] = {}
    latest_job_by_version: dict[uuid.UUID, DocumentSyncJob] = {}

    if docs:
        doc_ids = [doc.id for doc in docs]
        versions_res = await db.execute(
            select(CorpusDocumentVersion)
            .where(CorpusDocumentVersion.document_id.in_(doc_ids))
            .order_by(CorpusDocumentVersion.version_number.desc())
        )
        all_versions = list(versions_res.scalars().all())
        for ver in all_versions:
            versions_by_doc.setdefault(ver.document_id, []).append(ver)
            version_by_id[ver.id] = ver
        for doc_vers in versions_by_doc.values():
            doc_vers.sort(key=lambda v: v.version_number, reverse=True)

        needed_version_ids: set[uuid.UUID] = set()
        for doc in docs:
            doc_vers = versions_by_doc.get(doc.id, [])
            cur_ver = (
                version_by_id.get(doc.current_version_id)
                if doc.current_version_id is not None
                else None
            )
            lat_ver = doc_vers[0] if doc_vers else cur_ver
            if cur_ver is not None:
                needed_version_ids.add(cur_ver.id)
            if lat_ver is not None:
                needed_version_ids.add(lat_ver.id)

        if needed_version_ids:
            jobs_res = await db.execute(
                select(DocumentSyncJob)
                .where(DocumentSyncJob.version_id.in_(list(needed_version_ids)))
                .order_by(DocumentSyncJob.created_at.desc())
            )
            all_jobs = list(jobs_res.scalars().all())
            all_jobs.sort(
                key=lambda j: j.created_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            for job in all_jobs:
                if job.version_id not in latest_job_by_version:
                    latest_job_by_version[job.version_id] = job

    all_items: list[DocumentResponse] = []
    for doc in docs:
        doc_vers = versions_by_doc.get(doc.id, [])
        current_version = (
            version_by_id.get(doc.current_version_id)
            if doc.current_version_id is not None
            else None
        )
        latest_version = doc_vers[0] if doc_vers else current_version
        current_job = (
            latest_job_by_version.get(current_version.id)
            if current_version is not None
            else None
        )
        latest_version_job = (
            latest_job_by_version.get(latest_version.id)
            if latest_version is not None
            else None
        )
        resp = _build_document_response(
            doc,
            current_version=current_version,
            latest_version=latest_version,
            current_job=current_job,
            latest_version_job=latest_version_job,
        )
        if index_status is not None:
            # Treat INDEXED and SYNCED as aliases for backward compatibility with SA-49 callers
            if index_status in (IndexStatusEnum.INDEXED, IndexStatusEnum.SYNCED):
                if resp.index_status not in (
                    IndexStatusEnum.INDEXED.value,
                    IndexStatusEnum.SYNCED.value,
                ):
                    continue
            elif resp.index_status != index_status.value:
                continue
        all_items.append(resp)

    total = len(all_items)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_items = all_items[start:end]

    return DocumentListResponse(
        items=paginated_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/check-changes",
    response_model=ChangeDetectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Dry-run SHA-256 and metadata change detection without mutating DB or storage (SA-56)",
)
async def check_document_changes(
    actor: RequireDocumentAdmin,
    db: AsyncSession = Depends(get_db),
    document_id: uuid.UUID | None = Form(default=None),
    code: str | None = Form(default=None, max_length=64),
    file: UploadFile | None = File(default=None),
    content: str | None = Form(default=None),
    title: str | None = Form(default=None, max_length=255),
    description: str | None = Form(default=None, max_length=2000),
    school_code: SchoolCodeEnum | None = Form(default=None),
    doc_type: DocumentCategoryEnum | None = Form(default=None),
    target_program_id: str | None = Form(default=None),
    doc_status: DocumentStatusEnum | None = Form(default=None, alias="status"),
    is_published: bool | None = Form(default=None),
    include_text_diff: bool = Form(default=False),
) -> ChangeDetectionResponse:
    """Inspect whether an incoming file or metadata update requires a new version or re-index."""
    raw_bytes, safe_name, mime_type = await _read_upload_or_content(file, content)

    metadata_updates: dict[str, Any] = {}
    if title is not None:
        metadata_updates["title"] = title
    if description is not None:
        metadata_updates["description"] = description
    if school_code is not None:
        metadata_updates["school"] = school_code.value
    if doc_type is not None:
        metadata_updates["category"] = doc_type.value
    if target_program_id and target_program_id.strip():
        try:
            metadata_updates["target_program_id"] = uuid.UUID(target_program_id.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="target_program_id must be a valid UUID.",
            ) from exc
    if doc_status is not None:
        metadata_updates["status"] = doc_status.value
    elif is_published is not None:
        metadata_updates["status"] = (
            DocumentStatusEnum.PUBLISHED.value if is_published else DocumentStatusEnum.DRAFT.value
        )

    return await sync_svc.detect_document_change(
        db,
        document_id=document_id,
        code=code,
        file_bytes=raw_bytes,
        filename=safe_name,
        declared_mime_type=mime_type,
        metadata_updates=metadata_updates or None,
        include_text_diff=include_text_diff,
        actor=actor,
    )


@router.get(
    "/sync-jobs/{job_id}/logs",
    response_model=DocumentSyncLogListResponse,
    status_code=status.HTTP_200_OK,
    summary="Inspect granular diagnostic logs for a document synchronization job (SA-56)",
)
async def get_sync_job_logs(
    job_id: uuid.UUID,
    actor: RequireDocumentStaffReader,
    db: AsyncSession = Depends(get_db),
) -> DocumentSyncLogListResponse:
    return await sync_svc.get_sync_job_logs(db, job_id=job_id, actor=actor)


# ---------------------------------------------------------------------------
# Single-Document Routes (/{document_id}/*)
# ---------------------------------------------------------------------------


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get single official document details",
)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    doc = await _load_document_or_404(db, document_id)
    (
        current_version,
        latest_version,
        current_job,
        latest_version_job,
    ) = await _load_doc_version_and_job(db, doc)
    return _build_document_response(
        doc,
        current_version=current_version,
        latest_version=latest_version,
        current_job=current_job,
        latest_version_job=latest_version_job,
    )


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update document metadata or publication state with sync tracking (SA-49 & SA-56)",
)
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    actor: RequireDocumentAdmin,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    doc = await _load_document_or_404(db, document_id, for_update=True)
    sync_svc.enforce_document_school_scope(actor, doc.school)
    if payload.school_code is not None:
        sync_svc.enforce_document_school_scope(actor, payload.school_code.value)

    if payload.target_program_id is not None:
        prog_res = await db.execute(
            select(Program.id).where(Program.id == payload.target_program_id)
        )
        if not prog_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Program '{payload.target_program_id}' not found.",
            )

    doc, _, triggered_job = await sync_svc.record_document_metadata_change(
        db,
        doc=doc,
        payload=payload,
        actor=actor,
    )

    (
        current_version,
        latest_version,
        current_job,
        latest_version_job,
    ) = await _load_doc_version_and_job(db, doc)
    return _build_document_response(
        doc,
        current_version=current_version,
        latest_version=latest_version,
        current_job=triggered_job or current_job,
        latest_version_job=latest_version_job,
    )


@router.get(
    "/{document_id}/download",
    status_code=status.HTTP_200_OK,
    summary="Download the raw binary file for an official knowledge document (SA-50)",
)
async def download_document_file(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(
        require_roles("admin", "responsable", "direction", "student", "rh")
    ),
) -> Response:
    """Stream or return the stored binary document with validated MIME type and filename."""
    doc = await _load_document_or_404(db, document_id)

    # Prevent students and non-staff roles from downloading unpublished DRAFT / ARCHIVED documents
    if actor.role not in STAFF_ROLES and doc.status != DocumentStatusEnum.PUBLISHED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only published documents can be downloaded by students.",
        )

    # Enforce school scope for scoped responsable actors on unpublished DRAFT / ARCHIVED documents
    if doc.status != DocumentStatusEnum.PUBLISHED.value:
        sync_svc.enforce_document_school_scope(actor, doc.school)

    if not doc.current_version_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' has no active file version.",
        )

    v_res = await db.execute(
        select(CorpusDocumentVersion).where(
            CorpusDocumentVersion.id == doc.current_version_id
        )
    )
    version = v_res.scalar_one_or_none()
    if not version or not version.file_path_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File version storage record not found.",
        )

    file_bytes, mime_type = storage_svc.read_document_bytes(version.file_path_storage)
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={
            "Content-Disposition": _build_content_disposition(version.file_name),
            "Content-Length": str(len(file_bytes)),
            "ETag": f'"{version.file_hash_sha256}"',
        },
    )


# ---------------------------------------------------------------------------
# SA-56 Versioning, Rollback & Sync History Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{document_id}/versions",
    response_model=VersionUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version with SHA-256 deduplication and auto sync-job queueing (SA-56)",
)
async def upload_document_version(
    document_id: uuid.UUID,
    response: Response,
    actor: RequireDocumentVersionWriter,
    db: AsyncSession = Depends(get_db),
    file: UploadFile | None = File(default=None),
    content: str | None = Form(default=None),
    changelog: str | None = Form(default=None, max_length=2000),
    published_date: date | None = Form(default=None),
    activate_immediately: bool = Form(default=False),
) -> VersionUploadResponse:
    """Create version N+1 if content SHA-256 changed; skip idempotently (200) if UNCHANGED."""
    raw_bytes, safe_name, mime_type = await _read_upload_or_content(file, content)
    if raw_bytes is None or safe_name is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'file' or non-empty 'content' must be supplied to upload a version.",
        )

    result = await sync_svc.create_document_version(
        db,
        document_id=document_id,
        actor=actor,
        file_bytes=raw_bytes,
        filename=safe_name,
        declared_mime_type=mime_type,
        changelog=changelog,
        published_date=published_date,
        activate_immediately=activate_immediately,
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List full immutable version history for a corpus document (SA-56)",
)
async def list_document_versions(
    document_id: uuid.UUID,
    actor: RequireDocumentStaffReader,
    db: AsyncSession = Depends(get_db),
) -> DocumentVersionListResponse:
    doc = await _load_document_or_404(db, document_id, include_deleted=True)
    sync_svc.enforce_document_school_scope(actor, doc.school)
    return await sync_svc.list_document_versions_with_jobs(db, doc=doc)


@router.get(
    "/{document_id}/versions/{version_number}/download",
    status_code=status.HTTP_200_OK,
    summary="Download binary file for a specific historical or staged document version (SA-56)",
)
async def download_document_version_file(
    document_id: uuid.UUID,
    actor: RequireDocumentStaffReader,
    version_number: int = FastAPIPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    doc = await _load_document_or_404(db, document_id, include_deleted=True)
    sync_svc.enforce_document_school_scope(actor, doc.school)

    v_res = await db.execute(
        select(CorpusDocumentVersion).where(
            CorpusDocumentVersion.document_id == doc.id,
            CorpusDocumentVersion.version_number == version_number,
        )
    )
    version = v_res.scalar_one_or_none()
    if not version or not version.file_path_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version v{version_number} not found for document '{document_id}'.",
        )

    file_bytes, mime_type = storage_svc.read_document_bytes(version.file_path_storage)
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={
            "Content-Disposition": _build_content_disposition(version.file_name),
            "Content-Length": str(len(file_bytes)),
            "ETag": f'"{version.file_hash_sha256}"',
        },
    )


@router.post(
    "/{document_id}/versions/{version_number}/activate",
    response_model=VersionActivateResponse,
    status_code=status.HTTP_200_OK,
    summary="Atomically promote a staged version or roll back to a previous version (SA-56)",
)
async def activate_document_version(
    document_id: uuid.UUID,
    actor: RequireDocumentVersionWriter,
    version_number: int = FastAPIPath(..., ge=1),
    db: AsyncSession = Depends(get_db),
    payload: VersionActivateRequest | None = Body(default=None),
) -> VersionActivateResponse:
    req = payload or VersionActivateRequest()
    return await sync_svc.promote_document_version(
        db,
        document_id=document_id,
        target_version_number=version_number,
        actor=actor,
        reason=req.reason,
        enqueue_reindex_if_unindexed=req.enqueue_reindex_if_unindexed,
        commit=True,
    )


@router.get(
    "/{document_id}/sync-jobs",
    response_model=DocumentSyncJobListResponse,
    status_code=status.HTTP_200_OK,
    summary="List synchronization jobs and change logs recorded for a document (SA-56)",
)
async def list_document_sync_jobs(
    document_id: uuid.UUID,
    actor: RequireDocumentStaffReader,
    db: AsyncSession = Depends(get_db),
    job_status: SyncJobStatusEnum | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
) -> DocumentSyncJobListResponse:
    doc = await _load_document_or_404(db, document_id, include_deleted=True)
    sync_svc.enforce_document_school_scope(actor, doc.school)
    return await sync_svc.list_document_sync_jobs_with_logs(
        db,
        doc=doc,
        status_filter=job_status.value if job_status else None,
        limit=limit,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete an official document, deactivate chunks/versions, and queue sync (SA-56)",
)
async def delete_document(
    document_id: uuid.UUID,
    actor: RequireDocumentVersionWriter,
    db: AsyncSession = Depends(get_db),
    purge_storage: bool = Query(
        default=False,
        description=(
            "If true, immediately unlink physical files and enqueue PURGE instead of DEACTIVATE."
        ),
    ),
) -> None:
    doc = await _load_document_or_404(db, document_id, for_update=True)
    await sync_svc.record_document_deletion(
        db,
        doc=doc,
        actor=actor,
        purge_storage=purge_storage,
    )
