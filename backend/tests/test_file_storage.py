"""
Unit and integration tests for SA-50: Binary file storage and document metadata.

Covers:
1. Magic-byte verification across PDF, TXT, MD, PNG, JPEG, DOCX.
2. Rejection of corrupted files, empty files, oversized files, and unsupported MIME types.
3. Path traversal attack prevention (saving, reading, deleting).
4. Binary file storage lifecycle (save, read, delete, directory leak prevention).
5. PostgreSQL metadata persistence:
   - knowledge.document / core.documents (CorpusDocument & CorpusDocumentVersion)
   - core.document (CandidateDocument)
6. REST API integration: download endpoint /api/v1/knowledge/documents/{id}/download.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.documents_rag import CorpusDocument, CorpusDocumentVersion
from app.services.file_storage_service import (
    DOCX_MIME,
    delete_document_bytes,
    persist_candidate_document_metadata,
    persist_corpus_document_metadata,
    read_document_bytes,
    resolve_safe_storage_path,
    sanitize_filename,
    save_document_bytes,
    validate_file_content,
)
from tests.test_knowledge_documents import DEFAULT_ADMIN_ID, MockAsyncSession, make_test_jwt

# Valid binary samples with magic bytes
SAMPLE_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
SAMPLE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4"
)
SAMPLE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"
SAMPLE_DOCX = b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00sample_docx_content"
SAMPLE_MARKDOWN = b"# Guide d'Orientation IIT 2026\n\nBienvenue a l'IIT Sfax."
SAMPLE_TXT = b"Reglement interieur de l'ecole ISB Sfax.\nAnnee 2026."


# ---------------------------------------------------------------------------
# 1. Magic-Byte Verification & Content Validation
# ---------------------------------------------------------------------------

def test_validate_magic_bytes_valid_formats():
    """All supported formats with valid magic bytes are properly detected."""
    assert validate_file_content(SAMPLE_PDF, "brochure.pdf") == "application/pdf"
    assert validate_file_content(SAMPLE_PNG, "releve.png") == "image/png"
    assert validate_file_content(SAMPLE_JPEG, "diplome.jpg") == "image/jpeg"
    assert validate_file_content(SAMPLE_DOCX, "reglement.docx") == DOCX_MIME
    assert validate_file_content(SAMPLE_MARKDOWN, "curriculum.md") == "text/markdown"
    assert validate_file_content(SAMPLE_TXT, "notes.txt") == "text/plain"


def test_validate_magic_bytes_corrupted_files_rejected():
    """Files with mismatched extensions and forged magic bytes are rejected."""
    # Corrupted PDF (missing %PDF-)
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"Hello I am a text file", "fake.pdf")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid PDF" in exc_info.value.detail

    # Corrupted PNG
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"%PDF-1.4 disguised as png", "fake.png")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid PNG" in exc_info.value.detail

    # Corrupted JPEG
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"Not a real jpeg header", "fake.jpg")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid JPEG" in exc_info.value.detail

    # Corrupted DOCX
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"Not a zip file", "fake.docx")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid DOCX" in exc_info.value.detail

    # Text file with binary null bytes
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"Text with null \x00 byte", "corrupt.txt")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "binary/null bytes" in exc_info.value.detail


def test_validate_empty_and_oversized_files():
    """Empty files and files exceeding the size cap are rejected."""
    # Empty 0-byte file
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(b"", "empty.pdf")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "0 bytes" in exc_info.value.detail

    # File exceeding cap (e.g. 50 bytes cap test)
    with pytest.raises(HTTPException) as exc_info:
        validate_file_content(SAMPLE_PDF * 10, "large.pdf", max_file_size=50)
    assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


def test_validate_unsupported_mime_types():
    """Unsupported executable, archive, or script extensions are rejected."""
    for bad_name in ["malware.exe", "script.sh", "archive.tar.gz", "code.py"]:
        with pytest.raises(HTTPException) as exc_info:
            validate_file_content(b"binary content", bad_name)
        assert exc_info.value.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


# ---------------------------------------------------------------------------
# 2. Path Traversal & Sanitization Protection
# ---------------------------------------------------------------------------

def test_sanitize_filename_strips_traversal():
    """Filenames with directory traversal or path separators are safely cleaned."""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\calc.exe") == "calc.exe"
    assert sanitize_filename("folder/subfolder/file.pdf") == "file.pdf"
    assert sanitize_filename("normal_brochure_2026.pdf") == "normal_brochure_2026.pdf"

    # Strips carriage returns and newlines (\r, \n) to prevent header injection
    assert sanitize_filename("injected\r\nheader.pdf") == "injectedheader.pdf"
    assert sanitize_filename("path/to/\r\nfile.txt") == "file.txt"

    # Empty or dangerous names get safe fallback
    fallback = sanitize_filename("..")
    assert fallback.startswith("doc_") and fallback.endswith(".bin")


def test_resolve_safe_storage_path_detects_traversal(tmp_path: Path):
    """Storage paths attempting to escape the storage root raise HTTPException(400)."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    # Valid subpath
    safe = resolve_safe_storage_path(storage_root, "knowledge", "iit", "brochure.pdf")
    assert safe.is_relative_to(storage_root.resolve())

    # Traversal in bucket
    with pytest.raises(HTTPException) as exc_info:
        resolve_safe_storage_path(storage_root, "../escaped_bucket", "file.pdf")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST

    # Traversal in subpath
    with pytest.raises(HTTPException) as exc_info:
        resolve_safe_storage_path(storage_root, "knowledge", "..", "..", "etc", "passwd")
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# 3. Storage CRUD Lifecycle & Directory Leak Prevention
# ---------------------------------------------------------------------------

def test_save_read_delete_document_lifecycle(tmp_path: Path):
    """Full lifecycle: save binary file, compute SHA-256, read back, delete, and prune."""
    storage_root = tmp_path / "app_storage"

    # 1. Save document
    meta = save_document_bytes(
        SAMPLE_PDF,
        filename="brochure_iit.pdf",
        bucket="knowledge",
        sub_path="iit/IIT_01/brochure_iit.pdf",
        storage_root=storage_root,
    )
    assert meta.file_name == "brochure_iit.pdf"
    assert meta.mime_type == "application/pdf"
    assert meta.file_size_bytes == len(SAMPLE_PDF)
    assert meta.file_hash_sha256 == hashlib.sha256(SAMPLE_PDF).hexdigest()
    assert meta.storage_path == "knowledge/iit/IIT_01/brochure_iit.pdf"
    assert Path(meta.absolute_path).exists()

    # 2. Read document
    read_bytes, read_mime = read_document_bytes(meta.storage_path, storage_root=storage_root)
    assert read_bytes == SAMPLE_PDF
    assert read_mime == "application/pdf"

    # Also test reading via file:// URI
    uri_bytes, _ = read_document_bytes(meta.storage_uri, storage_root=storage_root)
    assert uri_bytes == SAMPLE_PDF

    # 3. Delete document
    deleted = delete_document_bytes(meta.storage_path, storage_root=storage_root)
    assert deleted is True
    assert not Path(meta.absolute_path).exists()

    # Deleting again returns False (not present)
    assert delete_document_bytes(meta.storage_path, storage_root=storage_root) is False

    # Reading non-existent file raises 404
    with pytest.raises(HTTPException) as exc_info:
        read_document_bytes(meta.storage_path, storage_root=storage_root)
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_save_with_uuid_hash_key_when_no_subpath(tmp_path: Path):
    """When sub_path is omitted, a unique UUID/hash folder is generated."""
    storage_root = tmp_path / "storage"
    meta = save_document_bytes(
        SAMPLE_PNG,
        filename="releve.png",
        bucket="candidates",
        storage_root=storage_root,
    )
    assert meta.mime_type == "image/png"
    assert "candidates/" in meta.storage_path
    assert meta.file_name == "releve.png"
    assert Path(meta.absolute_path).exists()


# ---------------------------------------------------------------------------
# 4. PostgreSQL Metadata Persistence Integration (knowledge & candidate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_corpus_document_metadata_postgresql(tmp_path: Path):
    """persist_corpus_document_metadata creates CorpusDocument and CorpusDocumentVersion."""
    mock_db = MockAsyncSession()
    admin_uuid = uuid.UUID(DEFAULT_ADMIN_ID)
    storage_root = tmp_path / "storage"

    doc, version = await persist_corpus_document_metadata(
        mock_db,
        code="IIT_BROCHURE_2026",
        title="Brochure Officielle IIT 2026",
        school_code="iit",
        doc_type="brochure",
        file_bytes=SAMPLE_PDF,
        filename="brochure.pdf",
        actor_id=admin_uuid,
        is_published=True,
        description="Documentation officielle de l'ecole d'ingenieurs",
        storage_root=storage_root,
    )

    # Verify CorpusDocument
    assert doc.code == "IIT_BROCHURE_2026"
    assert doc.title == "Brochure Officielle IIT 2026"
    assert doc.school == "iit"
    assert doc.category == "brochure"
    assert doc.status == "PUBLISHED"
    assert doc.created_by == admin_uuid
    assert doc.current_version_id == version.id

    # Verify CorpusDocumentVersion
    assert version.document_id == doc.id
    assert version.version_number == 1
    assert version.file_name == "brochure.pdf"
    assert version.mime_type == "application/pdf"
    assert version.file_size_bytes == len(SAMPLE_PDF)
    assert version.file_hash_sha256 == hashlib.sha256(SAMPLE_PDF).hexdigest()
    assert version.uploaded_by == admin_uuid
    assert version.is_active is True

    # Verify physical file written
    target_file = storage_root / version.file_path_storage
    assert target_file.exists()
    assert target_file.read_bytes() == SAMPLE_PDF

    # Verify sync job enqueued with required action_type and pipeline_stage
    assert len(mock_db.jobs) == 1
    job = next(iter(mock_db.jobs.values()))
    assert job.version_id == version.id
    assert job.status == "PENDING"
    assert job.action_type == "INGEST"
    assert job.pipeline_stage == "PARSING"


@pytest.mark.asyncio
async def test_persist_candidate_document_metadata_postgresql(tmp_path: Path):
    """persist_candidate_document_metadata creates CandidateDocument with URI and OCR payload."""
    mock_db = MockAsyncSession()
    candidate_uuid = uuid.uuid4()
    storage_root = tmp_path / "storage"

    ocr_payload = {"average": 15.5, "section": "Sciences Techniques", "session": "Principale"}
    doc = await persist_candidate_document_metadata(
        mock_db,
        candidate_id=candidate_uuid,
        doc_type="RELEVE_BAC",
        file_bytes=SAMPLE_PNG,
        filename="bac_releve.png",
        ocr_json=ocr_payload,
        min_confidence=Decimal("0.950"),
        verification_status="PENDING",
        storage_root=storage_root,
    )

    assert doc.candidate_id == candidate_uuid
    assert doc.type == "RELEVE_BAC"
    assert doc.uri.startswith("file://candidates/")
    assert doc.ocr_json == ocr_payload
    assert doc.min_confidence == Decimal("0.950")
    assert doc.verification_status == "PENDING"

    # Physical file stored
    clean_path = doc.uri.replace("file://", "")
    assert (storage_root / clean_path).exists()


@pytest.mark.asyncio
async def test_persist_corpus_metadata_cleans_up_file_on_db_error(tmp_path: Path):
    """When DB operations fail during corpus persistence, the stored file is deleted."""
    mock_db = MockAsyncSession()
    storage_root = tmp_path / "storage"

    # Monkeypatch flush to simulate a DB failure
    async def failing_flush():
        raise RuntimeError("Simulated DB failure during flush")

    mock_db.flush = failing_flush

    with pytest.raises(RuntimeError):
        await persist_corpus_document_metadata(
            mock_db,
            code="FAIL_CORPUS_DB",
            title="Failed Corpus Doc",
            school_code="iit",
            doc_type="brochure",
            file_bytes=SAMPLE_PDF,
            filename="fail_corpus.pdf",
            actor_id=uuid.uuid4(),
            storage_root=storage_root,
        )

    # Verify no file remains on disk in storage_root
    created_files = list(storage_root.rglob("*.pdf"))
    assert len(created_files) == 0


@pytest.mark.asyncio
async def test_persist_candidate_metadata_cleans_up_file_on_db_error(tmp_path: Path):
    """When DB operations fail during candidate persistence, the stored file is deleted."""
    mock_db = MockAsyncSession()
    storage_root = tmp_path / "storage"

    async def failing_flush():
        raise RuntimeError("Simulated DB failure during candidate flush")

    mock_db.flush = failing_flush

    with pytest.raises(RuntimeError):
        await persist_candidate_document_metadata(
            mock_db,
            candidate_id=uuid.uuid4(),
            doc_type="RELEVE_BAC",
            file_bytes=SAMPLE_PNG,
            filename="fail_candidate.png",
            storage_root=storage_root,
        )

    created_files = list(storage_root.rglob("*.png"))
    assert len(created_files) == 0


# ---------------------------------------------------------------------------
# 5. REST API Integration: File Download Endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def test_client_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mock_db = MockAsyncSession()
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, mock_db, storage_root
    app.dependency_overrides.clear()


def test_download_document_file_endpoint(test_client_storage):
    """GET /api/v1/knowledge/documents/{id}/download streams the binary file."""
    client, mock_db, storage_root = test_client_storage
    admin_token = make_test_jwt(role="admin")
    admin_uuid = uuid.UUID(DEFAULT_ADMIN_ID)

    # 1. Create a document in storage and mock DB
    meta = save_document_bytes(
        SAMPLE_PDF,
        filename="brochure_iit.pdf",
        bucket="knowledge",
        sub_path="iit/IIT_01/brochure_iit.pdf",
        storage_root=storage_root,
    )

    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()

    doc = CorpusDocument(
        id=doc_id,
        code="IIT_01",
        title="IIT Brochure",
        school="iit",
        category="brochure",
        status="PUBLISHED",
        created_by=admin_uuid,
        current_version_id=version_id,
    )
    version = CorpusDocumentVersion(
        id=version_id,
        document_id=doc_id,
        version_number=1,
        file_name="brochure_iit.pdf",
        file_path_storage=meta.storage_path,
        mime_type="application/pdf",
        file_size_bytes=len(SAMPLE_PDF),
        file_hash_sha256=meta.file_hash_sha256,
        uploaded_by=admin_uuid,
        is_active=True,
    )

    mock_db.add(doc)
    mock_db.add(version)

    # 2. Download file
    res = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.content == SAMPLE_PDF
    assert res.headers["Content-Type"] == "application/pdf"
    assert 'filename="brochure_iit.pdf"' in res.headers["Content-Disposition"]
    assert res.headers["Content-Length"] == str(len(SAMPLE_PDF))

    # 3. Download non-existent document returns 404
    missing_id = uuid.uuid4()
    res_404 = client.get(
        f"/api/v1/knowledge/documents/{missing_id}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_404.status_code == status.HTTP_404_NOT_FOUND
