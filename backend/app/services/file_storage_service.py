"""
Binary File Storage Adapter and PostgreSQL Document Metadata Persistence (SA-50).

Provides:
1. Local object storage / MinIO-compatible bucket layout with safe path sanitization.
2. File content validation via magic bytes (PDF, TXT, MD, PNG, JPEG, DOCX) and max file size.
3. Path-traversal attack protection.
4. SHA-256 checksum computation for deduplication.
5. Storage lifecycle: save_document_bytes(), read_document_bytes(), delete_document_bytes().
6. PostgreSQL metadata persistence:
   - knowledge.document / core.documents (CorpusDocument & CorpusDocumentVersion)
   - core.document (CandidateDocument)
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.documents_rag import CorpusDocument, CorpusDocumentVersion, DocumentSyncJob
from app.models.users import CandidateDocument

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Supported MIME types across RAG knowledge corpus and candidate documents
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/markdown",
    "text/plain",
    DOCX_MIME,
}

# Magic-byte file signatures
MAGIC_SIGNATURES: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    DOCX_MIME: b"PK\x03\x04",
}


@dataclass
class StoredDocumentMetadata:
    """Immutable metadata record for a binary document saved to storage."""

    storage_uri: str
    storage_path: str
    absolute_path: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    file_hash_sha256: str


# ---------------------------------------------------------------------------
# Path Sanitization & Traversal Prevention
# ---------------------------------------------------------------------------

_TRAVERSAL_PATTERN = re.compile(r"(\.\.[/\\]|[/\\]\.\.|^\.\.$)")


def sanitize_filename(filename: str) -> str:
    """
    Sanitize an untrusted filename, stripping directory components, path traversal
    sequences (../, ..\\), null bytes, and control characters.
    """
    if not filename:
        return f"doc_{uuid.uuid4().hex[:8]}.bin"

    # Strip null bytes and normalize path separators (handles both / and \ on all OSes)
    clean = filename.replace("\x00", "").strip().replace("\\", "/")

    # Extract base filename (removes any leading path / or \)
    clean = PurePosixPath(clean).name

    # Remove any remaining dangerous characters
    clean = re.sub(r'[\/:*?"<>|\\]', "_", clean)
    clean = clean.strip(". ")

    if not clean or clean == "..":
        return f"doc_{uuid.uuid4().hex[:8]}.bin"

    return clean[:255]


def resolve_safe_storage_path(
    storage_root: Path,
    bucket: str,
    *parts: str,
) -> Path:
    """
    Safely construct and resolve a file path inside storage_root/bucket.
    Raises HTTPException(400) if any path traversal or root escape is detected.
    """
    root_resolved = storage_root.resolve()

    # Validate bucket name
    clean_bucket = bucket.strip().replace("\x00", "")
    has_separator = "/" in clean_bucket or "\\" in clean_bucket
    if not clean_bucket or _TRAVERSAL_PATTERN.search(clean_bucket) or has_separator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage bucket identifier.",
        )

    # Combine parts safely
    target_path = root_resolved / clean_bucket
    for part in parts:
        part_clean = str(part).replace("\x00", "").strip()
        if not part_clean:
            continue
        if _TRAVERSAL_PATTERN.search(part_clean):
            logger.warning("Path traversal attempt detected in storage path: %s", part)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid path: path traversal detected.",
            )
        target_path = target_path / part_clean

    target_resolved = target_path.resolve()

    # Ensure resolved path is strictly within the storage root
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        logger.warning(
            "Directory escape detected: target '%s' escapes root '%s'",
            target_resolved,
            root_resolved,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: path traversal detected.",
        ) from exc

    return target_resolved


# ---------------------------------------------------------------------------
# Magic-Byte Verification & Content Validation
# ---------------------------------------------------------------------------

def validate_file_content(
    file_bytes: bytes,
    filename: str,
    declared_mime_type: str | None = None,
    allowed_mime_types: set[str] | None = None,
    max_file_size: int = settings.MAX_DOCUMENT_FILE_SIZE_BYTES,
) -> str:
    """
    Validate binary file content: non-empty, size within bounds, and verify magic bytes.
    Returns the detected/verified MIME type.
    """
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file cannot be empty (0 bytes).",
        )

    if len(file_bytes) > max_file_size:
        max_mb = max_file_size // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {max_mb}MB.",
        )

    allowed = allowed_mime_types or SUPPORTED_MIME_TYPES
    ext = Path(filename).suffix.lower()

    # Detect MIME type from extension or declared type
    candidate_mime = declared_mime_type
    if not candidate_mime or candidate_mime == "application/octet-stream":
        if ext == ".pdf":
            candidate_mime = "application/pdf"
        elif ext == ".png":
            candidate_mime = "image/png"
        elif ext in {".jpg", ".jpeg"}:
            candidate_mime = "image/jpeg"
        elif ext == ".docx":
            candidate_mime = DOCX_MIME
        elif ext in {".md", ".markdown"}:
            candidate_mime = "text/markdown"
        elif ext == ".txt":
            candidate_mime = "text/plain"
        else:
            candidate_mime = "application/octet-stream"

    # Enforce allowed MIME types
    if candidate_mime not in allowed:
        allowed_list = sorted(allowed)
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File mime type '{candidate_mime}' not allowed. Must be one of {allowed_list}.",
        )

    # Magic byte validation for binary formats
    if candidate_mime == "application/pdf":
        if not file_bytes.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PDF file: missing %PDF- header.",
            )
    elif candidate_mime == "image/png":
        if not file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PNG file: missing PNG signature.",
            )
    elif candidate_mime == "image/jpeg":
        if not file_bytes.startswith(b"\xff\xd8\xff"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JPEG file: missing JPEG SOI signature.",
            )
    elif candidate_mime == DOCX_MIME:
        if not file_bytes.startswith(b"PK\x03\x04"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid DOCX file: missing ZIP/OOXML signature.",
            )
    elif candidate_mime in {"text/markdown", "text/plain"}:
        # Reject binary files disguised as text (presence of null bytes)
        if b"\x00" in file_bytes[:1024]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid text/markdown file: contains binary/null bytes.",
            )
        try:
            file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid text file: cannot decode as UTF-8 text.",
            ) from exc

    return candidate_mime


# ---------------------------------------------------------------------------
# Storage CRUD Operations
# ---------------------------------------------------------------------------

def save_document_bytes(
    file_bytes: bytes,
    filename: str,
    *,
    bucket: str = "knowledge",
    sub_path: str | None = None,
    declared_mime_type: str | None = None,
    allowed_mime_types: set[str] | None = None,
    storage_root: Path | None = None,
) -> StoredDocumentMetadata:
    """
    Validate, hash, and persist binary document bytes to local object storage.

    Returns:
        StoredDocumentMetadata with storage URI, relative storage path, and SHA-256 hash.
    """
    root = storage_root or settings.effective_storage_root
    safe_name = sanitize_filename(filename)

    # 1. Validate magic bytes and MIME type
    mime_type = validate_file_content(
        file_bytes,
        safe_name,
        declared_mime_type=declared_mime_type,
        allowed_mime_types=allowed_mime_types,
    )

    # 2. Compute SHA-256 checksum
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # 3. Resolve destination storage path
    if sub_path:
        # Split sub_path and resolve safely
        sub_parts = [p for p in re.split(r"[/\\]+", sub_path.strip("/\\")) if p]
        dest_path = resolve_safe_storage_path(root, bucket, *sub_parts)
    else:
        # Structured key: {bucket}/{hash_prefix}_{uuid}/{safe_name}
        key_folder = f"{file_hash[:8]}_{uuid.uuid4().hex[:8]}"
        dest_path = resolve_safe_storage_path(root, bucket, key_folder, safe_name)

    # 4. Write binary file safely
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(file_bytes)
    logger.info("Saved binary document (%d bytes) to %s", len(file_bytes), dest_path)

    # Relative storage path from root
    rel_path = dest_path.relative_to(root.resolve()).as_posix()
    storage_uri = f"file://{rel_path}"

    return StoredDocumentMetadata(
        storage_uri=storage_uri,
        storage_path=rel_path,
        absolute_path=str(dest_path),
        file_name=safe_name,
        mime_type=mime_type,
        file_size_bytes=len(file_bytes),
        file_hash_sha256=file_hash,
    )


def read_document_bytes(
    storage_path_or_uri: str,
    *,
    storage_root: Path | None = None,
) -> tuple[bytes, str]:
    """
    Read stored binary file bytes by relative storage path or file:// URI.

    Returns:
        (file_bytes, detected_mime_type)
    """
    root = storage_root or settings.effective_storage_root

    # Clean URI prefix
    clean_path = storage_path_or_uri
    if clean_path.startswith("file://"):
        clean_path = clean_path[len("file://") :]

    clean_path = clean_path.strip().lstrip("/\\")

    # Resolve safe path
    parts = [p for p in re.split(r"[/\\]+", clean_path) if p]
    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage path cannot be empty.",
        )

    bucket = parts[0]
    rel_parts = parts[1:]
    target_path = resolve_safe_storage_path(root, bucket, *rel_parts)

    if not target_path.exists() or not target_path.is_file():
        logger.warning("Requested file not found in storage: %s", target_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored document file '{clean_path}' not found.",
        )

    file_bytes = target_path.read_bytes()
    mime_type = validate_file_content(file_bytes, target_path.name)
    return file_bytes, mime_type


def delete_document_bytes(
    storage_path_or_uri: str,
    *,
    storage_root: Path | None = None,
) -> bool:
    """
    Delete a stored binary document file and prune empty parent folders to prevent storage leaks.

    Returns:
        True if file was deleted, False if file did not exist.
    """
    root = storage_root or settings.effective_storage_root

    clean_path = storage_path_or_uri
    if clean_path.startswith("file://"):
        clean_path = clean_path[len("file://") :]

    clean_path = clean_path.strip().lstrip("/\\")
    parts = [p for p in re.split(r"[/\\]+", clean_path) if p]
    if not parts:
        return False

    bucket = parts[0]
    rel_parts = parts[1:]

    try:
        target_path = resolve_safe_storage_path(root, bucket, *rel_parts)
    except HTTPException:
        return False

    if not target_path.exists() or not target_path.is_file():
        return False

    target_path.unlink()
    logger.info("Deleted document from storage: %s", target_path)

    # Clean up empty parent directory if inside bucket
    try:
        parent = target_path.parent
        bucket_dir = (root / bucket).resolve()
        if parent != bucket_dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception as exc:
        logger.debug("Parent folder cleanup skipped: %s", exc)

    return True


# ---------------------------------------------------------------------------
# PostgreSQL Metadata Persistence Integration (knowledge.document & core.document)
# ---------------------------------------------------------------------------

async def persist_corpus_document_metadata(
    db: AsyncSession,
    *,
    code: str,
    title: str,
    school_code: str,
    doc_type: str,
    file_bytes: bytes,
    filename: str,
    actor_id: uuid.UUID,
    is_published: bool = False,
    description: str | None = None,
    target_program_id: uuid.UUID | None = None,
    changelog: str | None = None,
    storage_root: Path | None = None,
) -> tuple[CorpusDocument, CorpusDocumentVersion]:
    """
    Full atomic persistence workflow for official RAG documents (SA-50):
    1. Validates magic bytes and stores binary file in storage/knowledge/.
    2. Persists CorpusDocument record in PostgreSQL.
    3. Persists CorpusDocumentVersion record in PostgreSQL with SHA-256 and storage URI.
    4. Automatically creates pending DocumentSyncJob for RAG ingestion.
    """
    safe_filename = sanitize_filename(filename)
    sub_path = f"{school_code}/{code}/{safe_filename}"

    # 1. Store binary file
    meta = save_document_bytes(
        file_bytes,
        safe_filename,
        bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
        sub_path=sub_path,
        storage_root=storage_root,
    )

    # 2. Persist CorpusDocument
    doc_id = uuid.uuid4()
    initial_status = "PUBLISHED" if is_published else "DRAFT"

    doc = CorpusDocument(
        id=doc_id,
        code=code,
        title=title,
        description=description,
        school=school_code,
        category=doc_type,
        target_program_id=target_program_id,
        status=initial_status,
        created_by=actor_id,
    )
    db.add(doc)
    await db.flush()

    # 3. Persist CorpusDocumentVersion
    version_id = uuid.uuid4()
    version = CorpusDocumentVersion(
        id=version_id,
        document_id=doc.id,
        version_number=1,
        file_name=meta.file_name,
        file_path_storage=meta.storage_path,
        mime_type=meta.mime_type,
        file_size_bytes=meta.file_size_bytes,
        file_hash_sha256=meta.file_hash_sha256,
        uploaded_by=actor_id,
        changelog=changelog,
        is_active=True,
    )
    db.add(version)
    await db.flush()

    # Update foreign key current_version_id
    doc.current_version_id = version_id

    # 4. Enqueue DocumentSyncJob
    job = DocumentSyncJob(
        id=uuid.uuid4(),
        version_id=version_id,
        status="PENDING",
    )
    db.add(job)
    await db.flush()

    return doc, version


async def persist_candidate_document_metadata(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    doc_type: str,
    file_bytes: bytes,
    filename: str,
    ocr_json: dict[str, Any] | None = None,
    min_confidence: Decimal | float | None = None,
    verification_status: str = "PENDING",
    storage_root: Path | None = None,
) -> CandidateDocument:
    """
    Full atomic persistence workflow for candidate private uploads (transcripts/diplomas):
    1. Validates magic bytes (PDF, PNG, JPEG) and stores file in storage/candidates/.
    2. Persists CandidateDocument record in core.document.
    """
    safe_filename = sanitize_filename(filename)
    sub_path = f"{candidate_id}/{uuid.uuid4().hex[:8]}_{safe_filename}"

    # Candidate documents can be PDF, PNG, or JPEG
    meta = save_document_bytes(
        file_bytes,
        safe_filename,
        bucket=settings.STORAGE_BUCKET_CANDIDATES,
        sub_path=sub_path,
        allowed_mime_types={"application/pdf", "image/png", "image/jpeg"},
        storage_root=storage_root,
    )

    conf_dec: Decimal | None = None
    if min_confidence is not None:
        conf_dec = Decimal(str(min_confidence)).quantize(Decimal("0.001"))

    doc = CandidateDocument(
        id=uuid.uuid4(),
        candidate_id=candidate_id,
        type=doc_type,
        uri=meta.storage_uri,
        ocr_json=ocr_json,
        min_confidence=conf_dec,
        verification_status=verification_status,
    )
    db.add(doc)
    await db.flush()
    return doc
