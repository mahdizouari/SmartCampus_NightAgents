"""Official knowledge corpus REST endpoints for SA-49.

Manages RAG knowledge documents across IIT, ISB, and ISA schools:
- Uploading PDF/Markdown/text/DOCX brochures
- Structured text/markdown creation
- Listing with filtering (school_code, index_status, is_published)
- Metadata & publication state updates (publish/unpublish)
- Deleting/archiving documents
- Strict RBAC: write endpoints require admin, responsable, or direction roles.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentActor, require_roles
from app.models.catalog import Program
from app.models.documents_rag import CorpusDocument, CorpusDocumentVersion, DocumentSyncJob
from app.models.users import Account
from app.schemas.knowledge import (
    ALLOWED_MIME_TYPES,
    MAX_DOCUMENT_FILE_SIZE_BYTES,
    DocumentCategoryEnum,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusEnum,
    DocumentUpdate,
    DocumentVersionResponse,
    IndexStatusEnum,
    SchoolCodeEnum,
)
from app.services import file_storage_service as storage_svc

router = APIRouter()

# Strict RBAC dependency for write operations
RequireDocumentAdmin = Annotated[
    CurrentActor,
    Depends(require_roles("admin", "responsable", "direction")),
]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _validate_magic_bytes(mime_type: str, file_bytes: bytes) -> None:
    """Verify binary file signatures match the declared MIME type."""
    if mime_type == "application/pdf" and not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PDF file: missing %PDF- header.",
        )
    if mime_type == _DOCX_MIME and not file_bytes.startswith(b"PK\x03\x04"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid DOCX file: missing ZIP/OOXML signature.",
        )


def _build_document_response(
    doc: CorpusDocument,
    latest_job: DocumentSyncJob | None = None,
    current_version: CorpusDocumentVersion | None = None,
) -> DocumentResponse:
    index_status = IndexStatusEnum.NOT_INDEXED.value
    if latest_job:
        index_status = latest_job.status

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
            is_active=current_version.is_active,
            created_at=current_version.created_at,
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
        status=doc.status,
        is_published=(doc.status == DocumentStatusEnum.PUBLISHED.value),
        index_status=index_status,
        created_by=doc.created_by,
        created_at=doc.created_at or datetime.now(UTC),
        updated_at=doc.updated_at or datetime.now(UTC),
        current_version=version_resp,
    )


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
    # 1. Verify actor.account_id is a valid existing account (preserve audit attribution)
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
    db.add(doc)
    # Flush the document FIRST so it exists in DB before we reference it via FK
    await db.flush()

    current_version: CorpusDocumentVersion | None = None
    latest_job: DocumentSyncJob | None = None

    # 4. Process file or raw content if supplied
    has_file = file is not None and getattr(file, "filename", None) and file.filename.strip() != ""
    if has_file:
        file_bytes = await file.read(MAX_DOCUMENT_FILE_SIZE_BYTES + 1)
        if len(file_bytes) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file cannot be empty (0 bytes).",
            )
        if len(file_bytes) > MAX_DOCUMENT_FILE_SIZE_BYTES:
            max_mb = MAX_DOCUMENT_FILE_SIZE_BYTES // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds maximum size of {max_mb}MB.",
            )

        mime_type = file.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_MIME_TYPES:
            allowed = sorted(ALLOWED_MIME_TYPES)
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"File mime type '{mime_type}' not allowed. Must be one of {allowed}.",
            )

        _validate_magic_bytes(mime_type, file_bytes)

        safe_name = Path(file.filename or "uploaded_doc").name or "uploaded_doc"
        meta = storage_svc.save_document_bytes(
            file_bytes,
            filename=safe_name,
            bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
            sub_path=f"{school_code.value}/{doc.code}/{safe_name}",
            declared_mime_type=mime_type,
            allowed_mime_types=ALLOWED_MIME_TYPES,
        )
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
            action_type="INGEST",
            pipeline_stage="PARSING",
            status="PENDING",
        )
        db.add(latest_job)

    elif content and content.strip():
        content_bytes = content.encode("utf-8")
        meta = storage_svc.save_document_bytes(
            content_bytes,
            filename="content.md",
            bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
            sub_path=f"{school_code.value}/{doc.code}/content.md",
            declared_mime_type="text/markdown",
            allowed_mime_types=ALLOWED_MIME_TYPES,
        )
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
            action_type="INGEST",
            pipeline_stage="PARSING",
            status="PENDING",
        )
        db.add(latest_job)

    await db.commit()
    await db.refresh(doc)

    return _build_document_response(doc, latest_job, current_version)


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
    docs = docs_res.scalars().all()

    all_items: list[DocumentResponse] = []
    for doc in docs:
        latest_job = None
        current_version = None
        if doc.current_version_id:
            v_res = await db.execute(
                select(CorpusDocumentVersion).where(
                    CorpusDocumentVersion.id == doc.current_version_id
                )
            )
            current_version = v_res.scalar_one_or_none()

            job_res = await db.execute(
                select(DocumentSyncJob)
                .where(DocumentSyncJob.version_id == doc.current_version_id)
                .order_by(DocumentSyncJob.created_at.desc())
                .limit(1)
            )
            latest_job = job_res.scalar_one_or_none()

        resp = _build_document_response(doc, latest_job, current_version)
        if index_status is not None and resp.index_status != index_status.value:
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
    doc_res = await db.execute(
        select(CorpusDocument).where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
    )
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    current_version = None
    latest_job = None
    if doc.current_version_id:
        v_res = await db.execute(
            select(CorpusDocumentVersion).where(CorpusDocumentVersion.id == doc.current_version_id)
        )
        current_version = v_res.scalar_one_or_none()

        job_res = await db.execute(
            select(DocumentSyncJob)
            .where(DocumentSyncJob.version_id == doc.current_version_id)
            .order_by(DocumentSyncJob.created_at.desc())
            .limit(1)
        )
        latest_job = job_res.scalar_one_or_none()

    return _build_document_response(doc, latest_job, current_version)


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update document metadata or publish/unpublish state (RBAC protected)",
)
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    actor: RequireDocumentAdmin,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    doc_res = await db.execute(
        select(CorpusDocument).where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
    )
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    if payload.target_program_id is not None:
        prog_res = await db.execute(
            select(Program.id).where(Program.id == payload.target_program_id)
        )
        if not prog_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Program '{payload.target_program_id}' not found.",
            )
        doc.target_program_id = payload.target_program_id

    if payload.title is not None:
        doc.title = payload.title
    if payload.description is not None:
        doc.description = payload.description
    if payload.school_code is not None:
        doc.school = payload.school_code.value
    if payload.doc_type is not None:
        doc.category = payload.doc_type.value
    if payload.status is not None:
        doc.status = payload.status.value
    elif payload.is_published is not None:
        doc.status = (
            DocumentStatusEnum.PUBLISHED.value
            if payload.is_published
            else DocumentStatusEnum.DRAFT.value
        )

    await db.commit()
    await db.refresh(doc)

    current_version = None
    latest_job = None
    if doc.current_version_id:
        v_res = await db.execute(
            select(CorpusDocumentVersion).where(CorpusDocumentVersion.id == doc.current_version_id)
        )
        current_version = v_res.scalar_one_or_none()

        job_res = await db.execute(
            select(DocumentSyncJob)
            .where(DocumentSyncJob.version_id == doc.current_version_id)
            .order_by(DocumentSyncJob.created_at.desc())
            .limit(1)
        )
        latest_job = job_res.scalar_one_or_none()

    return _build_document_response(doc, latest_job, current_version)


@router.get(
    "/{document_id}/download",
    status_code=status.HTTP_200_OK,
    summary="Download the raw binary file for an official knowledge document (SA-50)",
)
async def download_document_file(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _actor: CurrentActor = Depends(
        require_roles("admin", "responsable", "direction", "student", "rh")
    ),
) -> Response:
    """Stream or return the stored binary document with validated MIME type and filename."""
    doc_res = await db.execute(
        select(CorpusDocument).where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
    )
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

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
            "Content-Disposition": f'attachment; filename="{version.file_name}"',
            "Content-Length": str(len(file_bytes)),
            "ETag": f'"{version.file_hash_sha256}"',
        },
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete or archive an official document (RBAC protected)",
)
async def delete_document(
    document_id: uuid.UUID,
    actor: RequireDocumentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    doc_res = await db.execute(
        select(CorpusDocument).where(
            CorpusDocument.id == document_id,
            CorpusDocument.status != DocumentStatusEnum.DELETED.value,
        )
    )
    doc = doc_res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    # Clean up physical storage file if present
    if doc.current_version_id:
        v_res = await db.execute(
            select(CorpusDocumentVersion).where(
                CorpusDocumentVersion.id == doc.current_version_id
            )
        )
        version = v_res.scalar_one_or_none()
        if version and version.file_path_storage:
            storage_svc.delete_document_bytes(version.file_path_storage)

    doc.status = DocumentStatusEnum.DELETED.value
    await db.commit()
