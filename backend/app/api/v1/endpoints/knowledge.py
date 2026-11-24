"""Official knowledge corpus REST endpoints for SA-49.

Manages RAG knowledge documents across IIT, ISB, and ISA schools:
- Uploading PDF/Markdown/text brochures
- Structured text/markdown creation
- Listing with filtering (school_code, index_status, is_published)
- Metadata & publication state updates (publish/unpublish)
- Deleting/archiving documents
- Strict RBAC: write endpoints require admin, responsable, or direction roles.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentActor, require_roles
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

router = APIRouter()

# Strict RBAC dependency for write operations
RequireDocumentAdmin = Annotated[
    CurrentActor,
    Depends(require_roles("admin", "responsable", "direction")),
]


def _build_document_response(
    doc: CorpusDocument,
    latest_job: DocumentSyncJob | None = None,
    current_version: CorpusDocumentVersion | None = None,
) -> DocumentResponse:
    # Determine index status from latest sync job
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
        academic_year=None,
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
    # Optional file upload for multipart
    file: UploadFile | None = File(default=None),
    # Form fields matching creation requirements
    code: str = Form(...),
    title: str = Form(...),
    school_code: SchoolCodeEnum = Form(...),
    doc_type: DocumentCategoryEnum = Form(...),
    description: str | None = Form(default=None),
    academic_year: str | None = Form(default=None),
    is_published: bool = Form(default=False),
    target_program_id: str | None = Form(default=None),
    content: str | None = Form(default=None),
) -> DocumentResponse:
    # Handle empty string for target_program_id if sent by Swagger
    parsed_program_id: uuid.UUID | None = None
    if target_program_id and target_program_id.strip():
        try:
            parsed_program_id = uuid.UUID(target_program_id.strip())
        except ValueError:
            parsed_program_id = None
    # 1. Check code uniqueness
    existing_stmt = select(CorpusDocument).where(CorpusDocument.code == code)
    res = await db.execute(existing_stmt)
    if res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document with code '{code}' already exists.",
        )

    # Resolve author to an existing account to satisfy core.accounts foreign key
    user_uuid: uuid.UUID | None = None
    try:
        candidate_uuid = uuid.UUID(actor.account_id)
        acc_exists = await db.execute(select(Account.id).where(Account.id == candidate_uuid))
        if acc_exists.scalar_one_or_none():
            user_uuid = candidate_uuid
    except (ValueError, TypeError):
        pass

    if user_uuid is None:
        # Fallback to demo admin or first available account in DB
        acc_stmt = select(Account.id).limit(1)
        res_acc = await db.execute(acc_stmt)
        user_uuid = res_acc.scalar_one_or_none()
        if user_uuid is None:
            # Create a fallback system account if empty database
            user_uuid = uuid.uuid4()
            sys_account = Account(
                id=user_uuid,
                email="admin@smartcampus.tn",
                full_name="Admin SmartCampus",
                auth_provider="EMAIL_OTP",
            )
            db.add(sys_account)
            await db.flush()

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

    # 2. Process file or raw content if supplied
    has_file = file is not None and getattr(file, "filename", None) and file.filename.strip() != ""
    if has_file:
        file_bytes = await file.read(MAX_DOCUMENT_FILE_SIZE_BYTES + 1)
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

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        version_id = uuid.uuid4()
        storage_path = f"knowledge/{school_code.value}/{doc.code}/{file.filename or 'document'}"

        current_version = CorpusDocumentVersion(
            id=version_id,
            document_id=doc.id,
            version_number=1,
            file_name=file.filename or "uploaded_doc",
            file_path_storage=storage_path,
            mime_type=mime_type,
            file_size_bytes=len(file_bytes),
            file_hash_sha256=file_hash,
            uploaded_by=user_uuid,
            is_active=True,
        )
        db.add(current_version)
        # Flush version so it exists, THEN update the document's FK reference
        await db.flush()
        doc.current_version_id = version_id

        # Trigger sync/indexing job
        latest_job = DocumentSyncJob(
            id=uuid.uuid4(),
            version_id=version_id,
            action_type="INGEST",
            pipeline_stage="PARSING",
            status="PENDING",
        )
        db.add(latest_job)

    elif content is not None:
        content_bytes = content.encode("utf-8")
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        version_id = uuid.uuid4()
        storage_path = f"knowledge/{school_code.value}/{doc.code}/content.md"

        current_version = CorpusDocumentVersion(
            id=version_id,
            document_id=doc.id,
            version_number=1,
            file_name="content.md",
            file_path_storage=storage_path,
            mime_type="text/markdown",
            file_size_bytes=len(content_bytes),
            file_hash_sha256=file_hash,
            uploaded_by=user_uuid,
            is_active=True,
        )
        db.add(current_version)
        # Flush version so it exists, THEN update the document's FK reference
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

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_query)
    total = total_res.scalar_one() or 0

    # Paginate
    query = query.order_by(CorpusDocument.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    docs_res = await db.execute(query)
    docs = docs_res.scalars().all()

    items: list[DocumentResponse] = []
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
        items.append(resp)

    return DocumentListResponse(
        items=items,
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

    if payload.title is not None:
        doc.title = payload.title
    if payload.description is not None:
        doc.description = payload.description
    if payload.school_code is not None:
        doc.school = payload.school_code.value
    if payload.doc_type is not None:
        doc.category = payload.doc_type.value
    if payload.target_program_id is not None:
        # Verify program exists in DB before setting FK, ignore if invalid
        from app.models.catalog import Program

        prog_res = await db.execute(
            select(Program.id).where(Program.id == payload.target_program_id)
        )
        if prog_res.scalar_one_or_none():
            doc.target_program_id = payload.target_program_id
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

    # Soft delete / mark as DELETED
    doc.status = DocumentStatusEnum.DELETED.value
    await db.commit()
