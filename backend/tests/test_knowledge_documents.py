"""Unit and integration tests for SA-49 knowledge corpus document management endpoints."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.documents_rag import (
    CorpusDocument,
    CorpusDocumentVersion,
    DocumentSyncJob,
    DocumentSyncLog,
)
from app.models.users import AuditLog
from app.services import file_storage_service as storage_svc

DEFAULT_ADMIN_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_PROGRAM_ID = "00000000-0000-0000-0000-000000000010"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_test_jwt(
    sub: str = DEFAULT_ADMIN_ID,
    role: str = "admin",
    exp_offset: int = 3600,
    secret: str | None = None,
) -> str:
    """Create a real HS256 JWT matching SA-20/SA-28 verification logic."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url_encode(
        json.dumps(
            {
                "sub": sub,
                "role": role,
                "exp": int(time.time()) + exp_offset,
            }
        ).encode("utf-8")
    )
    signing_key = (secret or settings.SECRET_KEY).encode("utf-8")
    sig = hmac.new(signing_key, f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


class MockAsyncSession:
    """In-memory mock session for testing endpoints without hitting a live PG database."""

    def __init__(self):
        self.documents: dict[uuid.UUID, CorpusDocument] = {}
        self.versions: dict[uuid.UUID, CorpusDocumentVersion] = {}
        self.jobs: dict[uuid.UUID, DocumentSyncJob] = {}
        self.sync_logs: list[DocumentSyncLog] = []
        self.audit_logs: list[AuditLog] = []
        self.accounts: set[uuid.UUID] = {uuid.UUID(DEFAULT_ADMIN_ID)}
        self.programs: set[uuid.UUID] = {uuid.UUID(DEFAULT_PROGRAM_ID)}

    def add(self, obj: Any):
        if isinstance(obj, CorpusDocument):
            self.documents[obj.id] = obj
        elif isinstance(obj, CorpusDocumentVersion):
            self.versions[obj.id] = obj
        elif isinstance(obj, DocumentSyncJob):
            self.jobs[obj.id] = obj
        elif isinstance(obj, DocumentSyncLog):
            self.sync_logs.append(obj)
        elif isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj: Any):
        pass

    async def execute(self, statement: Any):
        mock_result = MagicMock()
        mock_result.rowcount = 0
        stmt_str = str(statement)

        # Handle UPDATE statements gracefully in the lightweight mock
        if stmt_str.lstrip().upper().startswith("UPDATE "):
            return mock_result

        # Check for accounts lookup
        if "from core.accounts" in stmt_str.lower():
            params = statement.compile().params
            acct_val = next(iter(params.values()), None)
            mock_result.scalar_one_or_none.return_value = (
                acct_val if acct_val in self.accounts else None
            )
            return mock_result

        # Check for program lookup
        if "from core.program" in stmt_str.lower():
            params = statement.compile().params
            prog_val = next(iter(params.values()), None)
            mock_result.scalar_one_or_none.return_value = (
                prog_val if prog_val in self.programs else None
            )
            return mock_result

        # Check for single document by code
        if "documents.code =" in stmt_str:
            mock_result.scalar_one_or_none.return_value = None
            return mock_result

        # Check for single document by ID
        if "documents.id =" in stmt_str:
            params = statement.compile().params
            req_id = next((v for v in params.values() if isinstance(v, uuid.UUID)), None)
            if req_id is not None:
                doc = self.documents.get(req_id)
                found = doc if doc and doc.status != "DELETED" else None
            else:
                found = None
                for d in self.documents.values():
                    if d.status != "DELETED":
                        found = d
                        break
            mock_result.scalar_one_or_none.return_value = found
            return mock_result

        # Check for versions
        if "document_versions" in stmt_str:
            compiled = statement.compile()
            sql_lower = str(compiled).lower()
            params = dict(compiled.params)
            rows = list(self.versions.values())
            if "document_versions.document_id in" in sql_lower:
                doc_ids = set(params.get("document_id_1") or [])
                rows = [v for v in rows if v.document_id in doc_ids]
            elif "document_versions.document_id =" in sql_lower:
                req_doc = params.get("document_id_1")
                rows = [v for v in rows if v.document_id == req_doc]
            if "document_versions.id =" in sql_lower:
                req_id = params.get("id_1")
                rows = [v for v in rows if v.id == req_id]
            rows.sort(key=lambda v: v.version_number, reverse=True)
            found_v = rows[0] if rows else None
            mock_result.scalar_one_or_none.return_value = found_v
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = rows
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        # Check for sync jobs
        if "document_sync_jobs" in stmt_str:
            compiled = statement.compile()
            sql_lower = str(compiled).lower()
            params = dict(compiled.params)
            rows = list(self.jobs.values())
            if "document_sync_jobs.version_id in" in sql_lower:
                ver_ids = set(params.get("version_id_1") or [])
                rows = [j for j in rows if j.version_id in ver_ids]
            elif "document_sync_jobs.version_id =" in sql_lower:
                req_ver = params.get("version_id_1")
                rows = [j for j in rows if j.version_id == req_ver]
            found_j = rows[0] if rows else None
            mock_result.scalar_one_or_none.return_value = found_j
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = rows
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        # Default: list documents
        active_docs = [d for d in self.documents.values() if d.status != "DELETED"]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = active_docs
        mock_result.scalars.return_value = mock_scalars
        return mock_result


@pytest.fixture
def mock_db():
    return MockAsyncSession()


@pytest.fixture
def test_client(mock_db, tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, mock_db
    app.dependency_overrides.clear()


def test_create_document_unauthorized_fails(test_client):
    client, _ = test_client
    res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_REG_2024",
            "title": "IIT Reglement",
            "school_code": "iit",
            "doc_type": "reglement",
        },
    )
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_document_unknown_account_unauthorized(test_client):
    client, _ = test_client
    unknown_token = make_test_jwt(sub=str(uuid.uuid4()), role="admin")
    res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_REG_2024",
            "title": "IIT Reglement",
            "school_code": "iit",
            "doc_type": "reglement",
        },
        headers={"Authorization": f"Bearer {unknown_token}"},
    )
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_document_student_forbidden(test_client):
    client, _ = test_client
    student_token = make_test_jwt(role="student")
    res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_REG_2024",
            "title": "IIT Reglement",
            "school_code": "iit",
            "doc_type": "reglement",
        },
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_create_document_admin_success(test_client):
    client, mock_db = test_client
    admin_token = make_test_jwt(role="admin")
    res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_BROCHURE_2024",
            "title": "IIT Official Brochure 2024",
            "school_code": "iit",
            "doc_type": "brochure",
            "description": "Comprehensive engineering brochure",
            "is_published": "true",
            "content": "# IIT Engineering Guide\nWelcome to IIT.",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["code"] == "IIT_BROCHURE_2024"
    assert data["school_code"] == "iit"
    assert data["doc_type"] == "brochure"
    assert data["is_published"] is True
    assert data["status"] == "PUBLISHED"
    assert len(mock_db.documents) == 1
    assert len(mock_db.versions) == 1


def test_create_document_empty_file_and_magic_bytes_validation(test_client):
    client, mock_db = test_client
    admin_token = make_test_jwt(role="responsable")

    # 0-byte file rejected with 400
    empty_res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "ISB_EMPTY_PDF",
            "title": "Empty PDF",
            "school_code": "isb",
            "doc_type": "general_brochure",
        },
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert empty_res.status_code == status.HTTP_400_BAD_REQUEST

    # Fake PDF without %PDF- magic bytes rejected with 400
    fake_pdf = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "ISB_FAKE_PDF",
            "title": "Fake PDF",
            "school_code": "isb",
            "doc_type": "general_brochure",
        },
        files={"file": ("fake.pdf", b"not a real pdf", "application/pdf")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert fake_pdf.status_code == status.HTTP_400_BAD_REQUEST

    # Invalid MIME type upload rejected with 415
    bad_mime = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "ISB_EXEC_EXE",
            "title": "Invalid executable",
            "school_code": "isb",
            "doc_type": "general_brochure",
        },
        files={"file": ("malicious.exe", b"binary content", "application/x-msdownload")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert bad_mime.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    # Valid PDF upload with %PDF- header succeeds
    pdf_res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "ISA_SYLLABUS_2024",
            "title": "ISA Architecture Syllabus",
            "school_code": "isa",
            "doc_type": "curriculum",
        },
        files={"file": ("../syllabus.pdf", b"%PDF-1.4 test pdf content", "application/pdf")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert pdf_res.status_code == status.HTTP_201_CREATED
    assert pdf_res.json()["school_code"] == "isa"
    assert pdf_res.json()["current_version"]["file_name"] == "syllabus.pdf"
    assert pdf_res.json()["current_version"]["mime_type"] == "application/pdf"


def test_create_document_program_id_validation(test_client):
    client, _ = test_client
    admin_token = make_test_jwt(role="admin")

    # Malformed UUID -> 422
    bad_uuid = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_BAD_PROG",
            "title": "Bad Program UUID",
            "school_code": "iit",
            "doc_type": "curriculum",
            "target_program_id": "not-a-uuid",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert bad_uuid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Non-existent program UUID -> 404
    missing_prog = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_MISSING_PROG",
            "title": "Missing Program UUID",
            "school_code": "iit",
            "doc_type": "curriculum",
            "target_program_id": str(uuid.uuid4()),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert missing_prog.status_code == status.HTTP_404_NOT_FOUND


def test_list_and_filter_documents(test_client):
    client, mock_db = test_client
    doc = CorpusDocument(
        id=uuid.uuid4(),
        code="IIT_FAQ_01",
        title="IIT FAQ",
        school="iit",
        category="faq",
        status="PUBLISHED",
        created_by=uuid.uuid4(),
    )
    mock_db.add(doc)

    res = client.get("/api/v1/knowledge/documents?school_code=iit")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] >= 1
    assert data["items"][0]["code"] == "IIT_FAQ_01"


def test_update_and_publish_document(test_client):
    client, mock_db = test_client
    admin_token = make_test_jwt(role="responsable")
    doc_id = uuid.uuid4()
    doc = CorpusDocument(
        id=doc_id,
        code="IIT_CURR_2024",
        title="IIT Old Curriculum",
        school="iit",
        category="curriculum",
        status="DRAFT",
        created_by=uuid.uuid4(),
    )
    mock_db.add(doc)

    # Non-existent program in PATCH -> 404
    bad_patch = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"target_program_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert bad_patch.status_code == status.HTTP_404_NOT_FOUND

    patch_res = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"title": "IIT Updated Curriculum 2024", "is_published": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert patch_res.json()["title"] == "IIT Updated Curriculum 2024"
    assert patch_res.json()["is_published"] is True


def test_delete_document_rbac(test_client):
    client, mock_db = test_client
    doc_id = uuid.uuid4()
    doc = CorpusDocument(
        id=doc_id,
        code="ISB_ARCHIVED",
        title="Old brochure",
        school="isb",
        category="brochure",
        status="DRAFT",
        created_by=uuid.uuid4(),
    )
    mock_db.add(doc)

    student_token = make_test_jwt(role="student")
    del_forbidden = client.delete(
        f"/api/v1/knowledge/documents/{doc_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert del_forbidden.status_code == status.HTTP_403_FORBIDDEN

    admin_token = make_test_jwt(role="admin")
    del_ok = client.delete(
        f"/api/v1/knowledge/documents/{doc_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_ok.status_code == status.HTTP_204_NO_CONTENT
    assert doc.status == "DELETED"


def test_download_document_student_draft_forbidden(test_client):
    """Students cannot download DRAFT/ARCHIVED docs; staff can, and students can once PUBLISHED."""
    client, mock_db = test_client
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    sample_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

    meta = storage_svc.save_document_bytes(
        sample_pdf,
        filename="draft_doc.pdf",
        bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
        sub_path="iit/IIT_DRAFT/draft_doc.pdf",
    )

    doc = CorpusDocument(
        id=doc_id,
        code="IIT_DRAFT",
        title="Draft Syllabus",
        school="iit",
        category="brochure",
        status="DRAFT",
        created_by=uuid.UUID(DEFAULT_ADMIN_ID),
        current_version_id=version_id,
    )
    version = CorpusDocumentVersion(
        id=version_id,
        document_id=doc_id,
        version_number=1,
        file_name="draft_doc.pdf",
        file_path_storage=meta.storage_path,
        mime_type="application/pdf",
        file_size_bytes=len(sample_pdf),
        file_hash_sha256=meta.file_hash_sha256,
        uploaded_by=uuid.UUID(DEFAULT_ADMIN_ID),
        is_active=True,
    )
    mock_db.add(doc)
    mock_db.add(version)

    student_token = make_test_jwt(role="student")
    admin_token = make_test_jwt(role="admin")

    # 1. Student cannot download DRAFT document -> 403 Forbidden
    res_student = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/download",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res_student.status_code == status.HTTP_403_FORBIDDEN
    assert "published" in res_student.json()["detail"].lower()

    # 2. Staff (admin) CAN download DRAFT document -> 200 OK
    res_admin = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == status.HTTP_200_OK
    assert res_admin.content == sample_pdf

    # 3. Once published, student CAN download -> 200 OK
    doc.status = "PUBLISHED"
    res_student_published = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/download",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res_student_published.status_code == status.HTTP_200_OK
    assert res_student_published.content == sample_pdf


def test_create_document_cleans_up_file_on_db_error(test_client, monkeypatch):
    """When DB commit fails during document creation, stored file is deleted from disk."""
    client, mock_db = test_client
    admin_token = make_test_jwt(role="admin")
    sample_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

    # Simulate DB flush/commit error during document creation
    async def failing_commit():
        raise RuntimeError("Simulated DB connection failure during commit")

    monkeypatch.setattr(mock_db, "commit", failing_commit)

    with pytest.raises(RuntimeError):
        client.post(
            "/api/v1/knowledge/documents",
            data={
                "code": "IIT_FAIL_CLEANUP",
                "title": "Will Fail DB",
                "school_code": "iit",
                "doc_type": "brochure",
            },
            files={"file": ("cleanup_test.pdf", sample_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    # Check that file was cleaned up from storage and does not exist
    storage_root = settings.effective_storage_root
    assert list(storage_root.rglob("*.pdf")) == []
