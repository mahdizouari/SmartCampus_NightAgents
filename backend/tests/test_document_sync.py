"""Unit and API integration tests for SA-56: Document change detection, versioning & sync queue.

Covers GitHub Issue #71 & VisioraAI SA-56 acceptance criteria (CI-safe, no live PG/Redis):
1. Pure SHA-256 content hashing (CRLF/BOM text normalization vs exact binary PDF/DOCX bytes),
   metadata diffing, text unified diff summary, and high-level `index_status` derivation (`STALE`).
2. Dry-run `POST /api/v1/knowledge/documents/check-changes` with zero DB/disk side effects:
   NEW_DOCUMENT, UNCHANGED (including CRLF vs LF equivalence), NEW_VERSION, METADATA_CHANGED,
   DUPLICATE_HISTORICAL_VERSION, DELETED, and cross-document duplicate warnings.
3. Incremental version uploads `POST /api/v1/knowledge/documents/{id}/versions`:
   - Unchanged upload skipped idempotently (200 OK, no new version, no disk write, no sync job).
   - Modified upload increments version (v1 -> v2 -> v3), preserves v1 on disk via `v{N}/` paths,
     surfaces `STALE` index status, cancels superseded `PENDING` jobs, and enqueues `REINDEX`.
   - Re-uploading historical inactive hash returns 409 directing caller to rollback endpoint.
   - DB commit failure deletes only the newly written v2 file without touching v1 on disk.
4. Atomic Blue-Green promotion & rollback (`POST /{id}/versions/{N}/activate`)
   and reusable SA-57 worker mode (`commit=False`).
5. Enhanced `PATCH /{id}` (no-op skip, RAG metadata -> REINDEX, unpublish -> DEACTIVATE)
   and `DELETE /{id}` (deactivates versions & chunks, cancels PENDING jobs, queues sync).
6. Version history, version-specific download, sync job queue & `DocumentSyncLog` endpoints.
7. Strict RBAC (401/403), NAU_RESPONSABLE `school_scope` isolation, and PII-free `AuditLog` trails.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import create_access_token
from app.main import app
from app.models.documents_rag import (
    CorpusDocument,
    CorpusDocumentVersion,
    DocumentSyncJob,
    DocumentSyncLog,
    KnowledgeChunk,
)
from app.models.users import AuditLog
from app.schemas.knowledge import ChangeTypeEnum, IndexStatusEnum
from app.services import document_sync_service as sync_svc
from app.services import file_storage_service as storage_svc

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
RESP_IIT_ID = uuid.UUID("00000000-0000-0000-0000-000000000102")
RESP_ISB_ID = uuid.UUID("00000000-0000-0000-0000-000000000103")
RH_ID = uuid.UUID("00000000-0000-0000-0000-000000000104")
DIRECTION_ID = uuid.UUID("00000000-0000-0000-0000-000000000105")
STUDENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000106")
PROGRAM_ID = uuid.UUID("00000000-0000-0000-0000-000000000201")

PDF_V1 = b"%PDF-1.4\n1 0 obj\n<< /Title (Synthetic Test Doc V1) >>\nendobj\n%%EOF"
PDF_V2 = b"%PDF-1.4\n1 0 obj\n<< /Title (Synthetic Test Doc V2 Revised) >>\nendobj\n%%EOF"
PDF_V3 = b"%PDF-1.4\n1 0 obj\n<< /Title (Synthetic Test Doc V3 Final) >>\nendobj\n%%EOF"
MD_V1_LF = b"# Guide Test\n\nSection A\nLigne commune\n"
MD_V1_CRLF_BOM = b"\xef\xbb\xbf# Guide Test\r\n\r\nSection A\r\nLigne commune\r\n"
MD_V2_LF = b"# Guide Test\n\nSection A (mise a jour)\nLigne commune\nNouvelle section B\n"


def _make_token(
    account_id: uuid.UUID,
    role: str,
    *,
    school_scope: str | None = None,
) -> str:
    return create_access_token(
        account_id=str(account_id),
        role=role,
        school_scope=school_scope,
    )


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, *, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class SyncFakeAsyncSession:
    """High-fidelity stateful async SQLAlchemy double for SA-56 change-detection tests."""

    def __init__(self) -> None:
        self.documents: dict[uuid.UUID, CorpusDocument] = {}
        self.versions: dict[uuid.UUID, CorpusDocumentVersion] = {}
        self.jobs: dict[uuid.UUID, DocumentSyncJob] = {}
        self.sync_logs: list[DocumentSyncLog] = []
        self.chunks: dict[uuid.UUID, KnowledgeChunk] = {}
        self.audit_logs: list[AuditLog] = []
        self.accounts: set[uuid.UUID] = {
            ADMIN_ID,
            RESP_IIT_ID,
            RESP_ISB_ID,
            RH_ID,
            DIRECTION_ID,
            STUDENT_ID,
        }
        self.programs: set[uuid.UUID] = {PROGRAM_ID}
        self._next_log_id = 1
        self._pending_objects: list[Any] = []
        self._last_ts: datetime = datetime.now(UTC)
        self.executed_sql: list[str] = []

    def _monotonic_ts(self, candidate: datetime | None) -> datetime:
        ts = candidate or datetime.now(UTC)
        if ts <= self._last_ts:
            ts = self._last_ts + timedelta(microseconds=1)
        self._last_ts = ts
        return ts

    def add(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, CorpusDocument):
            if obj.id is None:
                obj.id = uuid.uuid4()
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.documents[obj.id] = obj
        elif isinstance(obj, CorpusDocumentVersion):
            if obj.id is None:
                obj.id = uuid.uuid4()
            obj.created_at = obj.created_at or now
            self.versions[obj.id] = obj
        elif isinstance(obj, DocumentSyncJob):
            if obj.id is None:
                obj.id = uuid.uuid4()
            obj.created_at = self._monotonic_ts(obj.created_at)
            if obj.embedding_model is None:
                obj.embedding_model = sync_svc.DEFAULT_EMBEDDING_MODEL
            if obj.chunk_size is None:
                obj.chunk_size = sync_svc.DEFAULT_CHUNK_SIZE
            if obj.chunk_overlap is None:
                obj.chunk_overlap = sync_svc.DEFAULT_CHUNK_OVERLAP
            if obj.retry_count is None:
                obj.retry_count = 0
            if obj.max_retries is None:
                obj.max_retries = sync_svc.DEFAULT_MAX_RETRIES
            self.jobs[obj.id] = obj
        elif isinstance(obj, DocumentSyncLog):
            if getattr(obj, "id", None) is None:
                obj.id = self._next_log_id
                self._next_log_id += 1
            obj.created_at = self._monotonic_ts(obj.created_at)
            self.sync_logs.append(obj)
        elif isinstance(obj, KnowledgeChunk):
            if obj.id is None:
                obj.id = uuid.uuid4()
            obj.created_at = obj.created_at or now
            self.chunks[obj.id] = obj
        elif isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = uuid.uuid4()
            obj.created_at = obj.created_at or now
            self.audit_logs.append(obj)
        self._pending_objects.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self._pending_objects.clear()

    async def rollback(self) -> None:
        for obj in self._pending_objects:
            if isinstance(obj, CorpusDocument):
                self.documents.pop(obj.id, None)
            elif isinstance(obj, CorpusDocumentVersion):
                self.versions.pop(obj.id, None)
            elif isinstance(obj, DocumentSyncJob):
                self.jobs.pop(obj.id, None)
            elif isinstance(obj, DocumentSyncLog) and obj in self.sync_logs:
                self.sync_logs.remove(obj)
            elif isinstance(obj, AuditLog) and obj in self.audit_logs:
                self.audit_logs.remove(obj)
        self._pending_objects.clear()

    async def refresh(self, obj: Any) -> None:
        pass

    async def execute(self, statement: Any) -> _FakeResult:
        compiled = statement.compile()
        sql = str(compiled)
        sql_lower = sql.lower()
        params = dict(compiled.params)
        self.executed_sql.append(sql_lower)

        # ── UPDATE knowledge.chunks ──────────────────────────────────────────
        if sql_lower.startswith("update knowledge.chunks"):
            new_active = params.get("is_active")
            doc_id = params.get("document_id_1")
            ver_id = params.get("version_id_1")
            count = 0
            for chunk in self.chunks.values():
                if doc_id is not None and chunk.document_id != doc_id:
                    continue
                if "is_active is true" in sql_lower and not chunk.is_active:
                    continue
                if "version_id !=" in sql_lower and chunk.version_id == ver_id:
                    continue
                if "version_id =" in sql_lower and "version_id !=" not in sql_lower:
                    if chunk.version_id != ver_id:
                        continue
                chunk.is_active = bool(new_active)
                count += 1
            return _FakeResult(rowcount=count)

        # ── UPDATE core.document_versions ────────────────────────────────────
        if sql_lower.startswith("update core.document_versions"):
            new_active = params.get("is_active")
            doc_id = params.get("document_id_1")
            ver_id = params.get("id_1")
            count = 0
            for ver in self.versions.values():
                if doc_id is not None and ver.document_id != doc_id:
                    continue
                if "document_versions.id !=" in sql_lower and ver.id == ver_id:
                    continue
                ver.is_active = bool(new_active)
                count += 1
            return _FakeResult(rowcount=count)

        # ── SELECT core.accounts ─────────────────────────────────────────────
        if "from core.accounts" in sql_lower:
            acct_id = next(iter(params.values()), None)
            return _FakeResult([acct_id] if acct_id in self.accounts else [])

        # ── SELECT core.program ──────────────────────────────────────────────
        if "from core.program" in sql_lower:
            prog_id = next(iter(params.values()), None)
            return _FakeResult([prog_id] if prog_id in self.programs else [])

        # ── SELECT core.documents ────────────────────────────────────────────
        if "from core.documents" in sql_lower:
            rows = list(self.documents.values())
            if "documents.status !=" in sql_lower:
                excluded_statuses = {
                    v for k, v in params.items() if k.startswith("status_") and isinstance(v, str)
                } or {"DELETED"}
                rows = [d for d in rows if d.status not in excluded_statuses]
            elif "documents.status =" in sql_lower:
                req_status = params.get("status_1")
                rows = [d for d in rows if d.status == req_status]
            if "documents.id in" in sql_lower:
                req_ids = set(params.get("id_1") or [])
                rows = [d for d in rows if d.id in req_ids]
            elif "documents.id =" in sql_lower:
                req_id = params.get("id_1")
                rows = [d for d in rows if d.id == req_id]
            if "documents.code =" in sql_lower:
                req_code = params.get("code_1")
                rows = [d for d in rows if d.code == req_code]
            if "documents.school =" in sql_lower:
                req_school = params.get("school_1")
                rows = [d for d in rows if d.school == req_school]
            rows.sort(key=lambda d: d.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
            return _FakeResult(rows)

        # ── SELECT core.document_versions ────────────────────────────────────
        if "from core.document_versions" in sql_lower:
            rows = list(self.versions.values())
            if "document_versions.id =" in sql_lower:
                req_id = params.get("id_1")
                rows = [v for v in rows if v.id == req_id]
            if "document_versions.document_id in" in sql_lower:
                req_docs = set(params.get("document_id_1") or [])
                rows = [v for v in rows if v.document_id in req_docs]
            elif "document_versions.document_id !=" in sql_lower:
                excl_doc = params.get("document_id_1")
                rows = [v for v in rows if v.document_id != excl_doc]
            elif "document_versions.document_id =" in sql_lower:
                req_doc = params.get("document_id_1")
                rows = [v for v in rows if v.document_id == req_doc]
            if "document_versions.version_number =" in sql_lower:
                req_num = params.get("version_number_1")
                rows = [v for v in rows if v.version_number == req_num]
            if "document_versions.file_hash_sha256 =" in sql_lower:
                req_hash = params.get("file_hash_sha256_1")
                rows = [v for v in rows if v.file_hash_sha256 == req_hash]
            rows.sort(key=lambda v: v.version_number, reverse=True)
            return _FakeResult(rows)

        # ── SELECT core.document_sync_jobs ───────────────────────────────────
        if "from core.document_sync_jobs" in sql_lower:
            rows = list(self.jobs.values())
            if "document_sync_jobs.id =" in sql_lower:
                req_id = params.get("id_1")
                rows = [j for j in rows if j.id == req_id]
            if "document_sync_jobs.version_id in" in sql_lower:
                ver_ids = set(params.get("version_id_1") or [])
                rows = [j for j in rows if j.version_id in ver_ids]
            elif "document_sync_jobs.version_id =" in sql_lower:
                req_ver = params.get("version_id_1")
                rows = [j for j in rows if j.version_id == req_ver]
            if "document_sync_jobs.status =" in sql_lower:
                req_status = params.get("status_1")
                rows = [j for j in rows if j.status == req_status]
            if "document_sync_jobs.action_type in" in sql_lower:
                allowed_actions = set(params.get("action_type_1") or [])
                rows = [j for j in rows if j.action_type in allowed_actions]
            reverse = "desc" in sql_lower
            rows.sort(
                key=lambda j: j.created_at or datetime.min.replace(tzinfo=UTC),
                reverse=reverse,
            )
            limit = params.get("param_1")
            if isinstance(limit, int):
                rows = rows[:limit]
            return _FakeResult(rows)

        # ── SELECT core.document_sync_logs ───────────────────────────────────
        if "from core.document_sync_logs" in sql_lower:
            if "document_sync_logs.job_id in" in sql_lower:
                req_jobs = set(params.get("job_id_1") or [])
                rows = [lg for lg in self.sync_logs if lg.job_id in req_jobs]
            else:
                req_job = params.get("job_id_1")
                rows = [lg for lg in self.sync_logs if lg.job_id == req_job]
            rows.sort(key=lambda lg: lg.created_at or datetime.min.replace(tzinfo=UTC))
            return _FakeResult(rows)

        return _FakeResult([])


@pytest.fixture
def sync_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide isolated TestClient, stateful SyncFakeAsyncSession, and tmp storage root."""
    fake_db = SyncFakeAsyncSession()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(storage_root))

    async def override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, fake_db, storage_root
    app.dependency_overrides.clear()


def _seed_doc_with_v1(
    fake_db: SyncFakeAsyncSession,
    storage_root: Path,
    *,
    code: str = "IIT_GUIDE_TEST",
    title: str = "Synthetic IIT Guide",
    school: str = "iit",
    category: str = "brochure",
    doc_status: str = "PUBLISHED",
    file_bytes: bytes = PDF_V1,
    filename: str = "guide.pdf",
    mime_type: str = "application/pdf",
    job_status: str = "SYNCED",
    with_chunks: int = 2,
) -> tuple[CorpusDocument, CorpusDocumentVersion, DocumentSyncJob]:
    canonical = sync_svc.prepare_bytes_for_storage(file_bytes, mime_type)
    sub_path = sync_svc.build_versioned_sub_path(school, code, 1, filename)
    meta = storage_svc.save_document_bytes(
        canonical,
        filename=filename,
        bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
        sub_path=sub_path,
        declared_mime_type=mime_type,
        storage_root=storage_root,
    )
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    doc = CorpusDocument(
        id=doc_id,
        code=code,
        title=title,
        description="Initial synthetic description",
        school=school,
        category=category,
        status=doc_status,
        current_version_id=ver_id,
        created_by=ADMIN_ID,
    )
    ver = CorpusDocumentVersion(
        id=ver_id,
        document_id=doc_id,
        version_number=1,
        file_name=meta.file_name,
        file_path_storage=meta.storage_path,
        mime_type=meta.mime_type,
        file_size_bytes=meta.file_size_bytes,
        file_hash_sha256=meta.file_hash_sha256,
        uploaded_by=ADMIN_ID,
        is_active=True,
    )
    job = DocumentSyncJob(
        id=uuid.uuid4(),
        version_id=ver_id,
        action_type="INGEST",
        pipeline_stage="COMPLETED" if job_status == "SYNCED" else "PARSING",
        status=job_status,
    )
    fake_db.add(doc)
    fake_db.add(ver)
    fake_db.add(job)
    for idx in range(with_chunks):
        fake_db.add(
            KnowledgeChunk(
                id=uuid.uuid4(),
                document_id=doc_id,
                version_id=ver_id,
                chunk_index=idx,
                content=f"Synthetic chunk {idx} for v1",
                token_count=10,
                embedding=[0.0] * 1024,
                chunk_metadata={"source": code, "version": 1},
                is_active=True,
            )
        )
    return doc, ver, job


# ---------------------------------------------------------------------------
# 1. Pure Unit Tests: Normalization, SHA-256, Metadata Diff, Text Diff, Index Status
# ---------------------------------------------------------------------------


def test_normalize_text_bytes_and_sha256_equivalence():
    """Text/markdown CRLF + UTF-8 BOM normalize to the exact same SHA-256 as LF UTF-8."""
    assert sync_svc.normalize_text_bytes(MD_V1_CRLF_BOM) == MD_V1_LF
    assert sync_svc.compute_content_sha256(
        MD_V1_CRLF_BOM, "text/markdown"
    ) == sync_svc.compute_content_sha256(MD_V1_LF, "text/markdown")

    # Binary files are NEVER newline-normalized
    raw_pdf_with_crlf = b"%PDF-1.4\r\nstream\r\nendstream\n%%EOF"
    assert (
        sync_svc.compute_content_sha256(raw_pdf_with_crlf, "application/pdf")
        == hashlib.sha256(raw_pdf_with_crlf).hexdigest()
    )


def test_compute_metadata_diff_rag_relevant_vs_cosmetic():
    doc = CorpusDocument(
        id=uuid.uuid4(),
        code="IIT_META_01",
        title="Old Title",
        description="Old desc",
        school="iit",
        category="brochure",
        status="PUBLISHED",
        created_by=ADMIN_ID,
    )
    # Identical values -> empty diff
    diff_none, rag_none = sync_svc.compute_metadata_diff(doc, {"title": "Old Title"})
    assert diff_none == {}
    assert rag_none is False

    # Description only -> cosmetic, not RAG-relevant
    diff_desc, rag_desc = sync_svc.compute_metadata_diff(doc, {"description": "New desc"})
    assert "description" in diff_desc
    assert rag_desc is False

    # Title changed -> RAG-relevant
    diff_title, rag_title = sync_svc.compute_metadata_diff(doc, {"title": "Updated Title"})
    assert diff_title["title"].old == "Old Title"
    assert diff_title["title"].new == "Updated Title"
    assert rag_title is True


def test_compute_text_diff_summary_markdown_and_binary():
    md_summary = sync_svc.compute_text_diff_summary(
        MD_V1_LF,
        MD_V2_LF,
        mime_type="text/markdown",
        old_file_name="guide.md",
        new_file_name="guide.md",
    )
    assert md_summary.is_binary is False
    assert md_summary.lines_added == 2
    assert md_summary.lines_removed == 1
    assert md_summary.diff_preview is not None
    assert "+Nouvelle section B" in md_summary.diff_preview

    pdf_summary = sync_svc.compute_text_diff_summary(
        PDF_V1,
        PDF_V2,
        mime_type="application/pdf",
        old_file_name="v1.pdf",
        new_file_name="v2.pdf",
    )
    assert pdf_summary.is_binary is True
    assert pdf_summary.file_name_changed is True
    assert pdf_summary.size_delta_bytes == len(PDF_V2) - len(PDF_V1)
    assert pdf_summary.diff_preview is None


def test_compute_document_index_status_transitions():
    doc = CorpusDocument(
        id=uuid.uuid4(),
        code="IIT_STAT",
        title="Status Doc",
        school="iit",
        category="brochure",
        status="PUBLISHED",
        created_by=ADMIN_ID,
    )
    v1 = CorpusDocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=1,
        file_name="a.pdf",
        file_path_storage="knowledge/iit/IIT_STAT/v1/a.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        file_hash_sha256="a" * 64,
        uploaded_by=ADMIN_ID,
        is_active=True,
    )
    v2 = CorpusDocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=2,
        file_name="a.pdf",
        file_path_storage="knowledge/iit/IIT_STAT/v2/a.pdf",
        mime_type="application/pdf",
        file_size_bytes=120,
        file_hash_sha256="b" * 64,
        uploaded_by=ADMIN_ID,
        is_active=False,
    )
    job_ingest_pending = DocumentSyncJob(
        id=uuid.uuid4(), version_id=v1.id, action_type="INGEST", status="PENDING"
    )
    job_ingest_synced = DocumentSyncJob(
        id=uuid.uuid4(), version_id=v1.id, action_type="INGEST", status="SYNCED"
    )
    job_reindex_pending = DocumentSyncJob(
        id=uuid.uuid4(), version_id=v2.id, action_type="REINDEX", status="PENDING"
    )
    job_failed = DocumentSyncJob(
        id=uuid.uuid4(), version_id=v2.id, action_type="REINDEX", status="FAILED"
    )

    assert sync_svc.compute_document_index_status(doc) == IndexStatusEnum.NOT_INDEXED.value
    assert (
        sync_svc.compute_document_index_status(
            doc, current_version=v1, latest_version=v1, latest_job=job_ingest_pending
        )
        == IndexStatusEnum.PENDING.value
    )
    assert (
        sync_svc.compute_document_index_status(
            doc, current_version=v1, latest_version=v1, latest_job=job_ingest_synced
        )
        == IndexStatusEnum.INDEXED.value
    )
    # Staged v2 ahead of active v1 -> STALE
    assert (
        sync_svc.compute_document_index_status(
            doc, current_version=v1, latest_version=v2, latest_job=job_reindex_pending
        )
        == IndexStatusEnum.STALE.value
    )
    # Failed job -> FAILED
    assert (
        sync_svc.compute_document_index_status(
            doc, current_version=v1, latest_version=v2, latest_job=job_failed
        )
        == IndexStatusEnum.FAILED.value
    )


# ---------------------------------------------------------------------------
# 2. Dry-Run Change Detection Endpoint: POST /check-changes
# ---------------------------------------------------------------------------


def test_dry_run_check_changes_classifications_and_zero_side_effects(sync_env):
    client, fake_db, storage_root = sync_env
    admin_token = _make_token(ADMIN_ID, "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # A. Brand-new document code -> NEW_DOCUMENT (0 DB writes, 0 disk files)
    res_new = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "ISB_NEW_2026", "school_code": "isb"},
        files={"file": ("brochure.pdf", PDF_V1, "application/pdf")},
        headers=headers,
    )
    assert res_new.status_code == status.HTTP_200_OK
    body_new = res_new.json()
    assert body_new["change_type"] == ChangeTypeEnum.NEW_DOCUMENT.value
    assert body_new["next_version_number"] == 1
    assert body_new["recommended_action"] == "INGEST"
    assert body_new["requires_sync"] is True
    assert len(fake_db.documents) == 0
    assert list(storage_root.rglob("*")) == []

    # Seed doc v1 (Markdown LF)
    doc, ver1, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_MD_DRYRUN",
        file_bytes=MD_V1_LF,
        filename="guide.md",
        mime_type="text/markdown",
    )

    # B. Same Markdown with CRLF + BOM -> UNCHANGED (no false positive!)
    res_same = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"document_id": str(doc.id)},
        files={"file": ("guide.md", MD_V1_CRLF_BOM, "text/markdown")},
        headers=headers,
    )
    assert res_same.status_code == status.HTTP_200_OK
    assert res_same.json()["change_type"] == ChangeTypeEnum.UNCHANGED.value
    assert res_same.json()["requires_sync"] is False
    assert res_same.json()["recommended_action"] is None

    # C. Modified Markdown with include_text_diff=true -> NEW_VERSION + unified diff preview
    res_mod = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"document_id": str(doc.id), "include_text_diff": "true"},
        files={"file": ("guide.md", MD_V2_LF, "text/markdown")},
        headers=headers,
    )
    assert res_mod.status_code == status.HTTP_200_OK
    body_mod = res_mod.json()
    assert body_mod["change_type"] == ChangeTypeEnum.NEW_VERSION.value
    assert body_mod["current_version_number"] == 1
    assert body_mod["next_version_number"] == 2
    assert body_mod["recommended_action"] == "REINDEX"
    assert body_mod["projected_index_status"] == "STALE"
    assert body_mod["content_diff"]["lines_added"] == 2
    assert body_mod["content_diff"]["lines_removed"] == 1
    assert "+Nouvelle section B" in body_mod["content_diff"]["diff_preview"]

    # D. Metadata-only RAG-relevant change -> METADATA_CHANGED + REINDEX
    res_meta = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"document_id": str(doc.id), "title": "Updated Official Title"},
        headers=headers,
    )
    assert res_meta.status_code == status.HTTP_200_OK
    assert res_meta.json()["change_type"] == ChangeTypeEnum.METADATA_CHANGED.value
    assert res_meta.json()["recommended_action"] == "REINDEX"
    assert res_meta.json()["metadata_diff"]["title"]["new"] == "Updated Official Title"

    # E. Cross-document duplicate warning on a brand-new code using same file bytes
    res_cross = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "ISB_DUPLICATE_CODE"},
        files={"file": ("copy.md", MD_V1_LF, "text/markdown")},
        headers=headers,
    )
    assert res_cross.status_code == status.HTTP_200_OK
    matches = res_cross.json()["cross_document_matches"]
    assert len(matches) == 1
    assert matches[0]["document_code"] == "IIT_MD_DRYRUN"
    assert matches[0]["version_number"] == 1

    # F. Deleted document detected cleanly
    doc.status = "DELETED"
    res_del = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "IIT_MD_DRYRUN"},
        headers=headers,
    )
    assert res_del.status_code == status.HTTP_200_OK
    assert res_del.json()["change_type"] == ChangeTypeEnum.DELETED.value


# ---------------------------------------------------------------------------
# 3. Incremental Versioning, SHA-256 Skip, Superseded Cancellation & Rollback Guard
# ---------------------------------------------------------------------------


def test_version_upload_skips_unchanged_and_increments_on_change(sync_env):
    client, fake_db, storage_root = sync_env
    admin_token = _make_token(ADMIN_ID, "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    doc, v1, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_BROCHURE_VER",
        file_bytes=PDF_V1,
        filename="brochure.pdf",
        mime_type="application/pdf",
    )
    v1_path = storage_root / v1.file_path_storage
    assert v1_path.exists()
    jobs_before = len(fake_db.jobs)

    # 1. Re-uploading identical bytes -> 200 OK UNCHANGED (skipped, 0 new versions, 0 new jobs)
    res_skip = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("brochure.pdf", PDF_V1, "application/pdf")},
        headers=headers,
    )
    assert res_skip.status_code == status.HTTP_200_OK
    skip_data = res_skip.json()
    assert skip_data["change_type"] == ChangeTypeEnum.UNCHANGED.value
    assert skip_data["created"] is False
    assert skip_data["skipped"] is True
    assert skip_data["version"]["version_number"] == 1
    assert len(fake_db.versions) == 1
    assert len(fake_db.jobs) == jobs_before

    # 2. Uploading modified PDF with SAME filename -> 201 Created v2, v1 preserved on disk!
    res_v2 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"changelog": "Updated sections for new cycle"},
        files={"file": ("brochure.pdf", PDF_V2, "application/pdf")},
        headers=headers,
    )
    assert res_v2.status_code == status.HTTP_201_CREATED
    v2_data = res_v2.json()
    assert v2_data["change_type"] == ChangeTypeEnum.NEW_VERSION.value
    assert v2_data["created"] is True
    assert v2_data["skipped"] is False
    assert v2_data["activated"] is False  # staged for SA-57 blue-green swap
    assert v2_data["index_status"] == IndexStatusEnum.STALE.value
    assert v2_data["version"]["version_number"] == 2
    assert "/v2/" in v2_data["version"]["file_path_storage"]
    assert v2_data["sync_job"]["action_type"] == "REINDEX"
    assert v2_data["sync_job"]["status"] == "PENDING"

    # Both v1 and v2 exist independently on disk!
    v2_path = storage_root / v2_data["version"]["file_path_storage"]
    assert v1_path.exists() and v1_path.read_bytes() == PDF_V1
    assert v2_path.exists() and v2_path.read_bytes() == PDF_V2

    # GET /{doc_id} surfaces STALE index_status and latest_version_number=2 while v1 stays active
    doc_get = client.get(f"/api/v1/knowledge/documents/{doc.id}")
    assert doc_get.status_code == status.HTTP_200_OK
    assert doc_get.json()["index_status"] == IndexStatusEnum.STALE.value
    assert doc_get.json()["current_version"]["version_number"] == 1
    assert doc_get.json()["latest_version_number"] == 2

    # 3. Uploading v3 while v2 REINDEX job is still PENDING cancels v2 job and enqueues v3 job
    v2_job_id = uuid.UUID(v2_data["sync_job"]["id"])
    res_v3 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"changelog": "Hotfix v3", "activate_immediately": "true"},
        files={"file": ("brochure.pdf", PDF_V3, "application/pdf")},
        headers=headers,
    )
    assert res_v3.status_code == status.HTTP_201_CREATED
    assert res_v3.json()["version"]["version_number"] == 3
    assert res_v3.json()["activated"] is True
    assert fake_db.jobs[v2_job_id].status == "CANCELLED"
    assert len(fake_db.sync_logs) >= 3
    assert any(a.action == "knowledge.document.version_create" for a in fake_db.audit_logs)

    # 4. Re-uploading inactive historical v1 bytes -> 409 Conflict pointing to rollback endpoint
    res_hist = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("brochure.pdf", PDF_V1, "application/pdf")},
        headers=headers,
    )
    assert res_hist.status_code == status.HTTP_409_CONFLICT
    assert "v1" in res_hist.json()["detail"]
    assert "/versions/1/activate" in res_hist.json()["detail"]

    # 5. Version history and version-specific downloads work for every version
    hist_res = client.get(f"/api/v1/knowledge/documents/{doc.id}/versions", headers=headers)
    assert hist_res.status_code == status.HTTP_200_OK
    assert hist_res.json()["total_versions"] == 3
    assert [v["version_number"] for v in hist_res.json()["versions"]] == [3, 2, 1]

    dl_v1 = client.get(
        f"/api/v1/knowledge/documents/{doc.id}/versions/1/download", headers=headers
    )
    dl_v2 = client.get(
        f"/api/v1/knowledge/documents/{doc.id}/versions/2/download", headers=headers
    )
    assert dl_v1.status_code == status.HTTP_200_OK and dl_v1.content == PDF_V1
    assert dl_v2.status_code == status.HTTP_200_OK and dl_v2.content == PDF_V2


def test_version_upload_cleans_up_new_file_on_db_error_without_touching_v1(sync_env, monkeypatch):
    """DB commit failure removes ONLY the newly uploaded v2 file and leaves v1 intact."""
    client, fake_db, storage_root = sync_env
    admin_token = _make_token(ADMIN_ID, "admin")
    doc, v1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_CLEANUP_V2")
    v1_path = storage_root / v1.file_path_storage

    async def failing_commit():
        raise RuntimeError("Simulated DB failure on v2 commit")

    monkeypatch.setattr(fake_db, "commit", failing_commit)

    with pytest.raises(RuntimeError):
        client.post(
            f"/api/v1/knowledge/documents/{doc.id}/versions",
            files={"file": ("guide.pdf", PDF_V2, "application/pdf")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert v1_path.exists() and v1_path.read_bytes() == PDF_V1
    pdf_files = list(storage_root.rglob("*.pdf"))
    assert len(pdf_files) == 1
    assert pdf_files[0] == v1_path


# ---------------------------------------------------------------------------
# 4. Atomic Blue-Green Promotion & Rollback + SA-57 Worker Contract
# ---------------------------------------------------------------------------


def test_atomic_version_promotion_and_rollback(sync_env):
    client, fake_db, storage_root = sync_env
    admin_token = _make_token(ADMIN_ID, "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    doc, v1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_ROLLBACK", with_chunks=2)

    # Upload staged v2
    res_v2 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("guide.pdf", PDF_V2, "application/pdf")},
        headers=headers,
    )
    assert res_v2.status_code == status.HTTP_201_CREATED
    v2_id = uuid.UUID(res_v2.json()["version"]["id"])

    # Simulate SA-57 inserting 3 inactive chunks for v2 before atomic activation
    for idx in range(3):
        fake_db.add(
            KnowledgeChunk(
                id=uuid.uuid4(),
                document_id=doc.id,
                version_id=v2_id,
                chunk_index=idx,
                content=f"Synthetic chunk {idx} for v2",
                token_count=12,
                embedding=[0.0] * 1024,
                chunk_metadata={"source": doc.code, "version": 2},
                is_active=False,
            )
        )

    # Promote v2 atomically
    prom_res = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions/2/activate",
        json={"reason": "Promote indexed v2"},
        headers=headers,
    )
    assert prom_res.status_code == status.HTTP_200_OK
    prom_body = prom_res.json()
    assert prom_body["is_rollback"] is False
    assert prom_body["previous_version_number"] == 1
    assert prom_body["active_version_number"] == 2
    assert prom_body["deactivated_chunks_count"] == 2
    assert prom_body["activated_chunks_count"] == 3
    assert prom_body["sync_job"] is None  # already has 3 chunks -> no unnecessary REINDEX
    assert doc.current_version_id == v2_id
    assert fake_db.versions[v1.id].is_active is False
    assert fake_db.versions[v2_id].is_active is True

    # Roll back from v2 -> v1
    rb_res = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions/1/activate",
        json={"reason": "Revert typo in v2"},
        headers=headers,
    )
    assert rb_res.status_code == status.HTTP_200_OK
    rb_body = rb_res.json()
    assert rb_body["is_rollback"] is True
    assert rb_body["previous_version_number"] == 2
    assert rb_body["active_version_number"] == 1
    assert rb_body["deactivated_chunks_count"] == 3
    assert rb_body["activated_chunks_count"] == 2
    assert any(a.action == "knowledge.document.version_rollback" for a in fake_db.audit_logs)


@pytest.mark.asyncio
async def test_sa57_worker_handoff_list_pending_and_promote_without_commit(tmp_path: Path):
    """SA-57 worker polls FIFO pending jobs and calls promote_document_version(commit=False)."""
    fake_db = SyncFakeAsyncSession()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    doc, v1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_SA57_CONTRACT")

    v2_id = uuid.uuid4()
    v2 = CorpusDocumentVersion(
        id=v2_id,
        document_id=doc.id,
        version_number=2,
        file_name="v2.pdf",
        file_path_storage="knowledge/iit/IIT_SA57_CONTRACT/v2/v2.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(PDF_V2),
        file_hash_sha256=sync_svc.compute_content_sha256(PDF_V2, "application/pdf"),
        uploaded_by=ADMIN_ID,
        is_active=False,
    )
    fake_db.add(v2)
    job, created = await sync_svc.enqueue_sync_job(
        fake_db,
        version_id=v2_id,
        action_type="REINDEX",
        cancel_superseded_for_document_id=doc.id,
    )
    assert created is True

    # Deduplication: re-enqueuing same (version_id, action_type) reuses the active PENDING job
    same_job, created_again = await sync_svc.enqueue_sync_job(
        fake_db,
        version_id=v2_id,
        action_type="REINDEX",
    )
    assert created_again is False
    assert same_job.id == job.id

    pending = await sync_svc.list_pending_sync_jobs(fake_db, action_types=["INGEST", "REINDEX"])
    assert len(pending) == 1
    assert pending[0].id == job.id

    # Worker calls promote_document_version with commit=False
    commit_called = False

    async def track_commit():
        nonlocal commit_called
        commit_called = True

    fake_db.commit = track_commit  # type: ignore[method-assign]
    res = await sync_svc.promote_document_version(
        fake_db,
        document_id=doc.id,
        target_version_id=v2_id,
        actor_type="SYSTEM",
        actor_id="sync_worker",
        reason=f"sync_job:{job.id}",
        enqueue_reindex_if_unindexed=False,
        commit=False,
    )
    assert commit_called is False
    assert res.active_version_id == v2_id
    assert doc.current_version_id == v2_id


# ---------------------------------------------------------------------------
# 5. PATCH Metadata Sync, DELETE Deactivation & Sync Log Inspection
# ---------------------------------------------------------------------------


def test_patch_metadata_sync_and_delete_deactivation(sync_env):
    client, fake_db, storage_root = sync_env
    admin_token = _make_token(ADMIN_ID, "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    doc, v1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_PATCH_DEL", with_chunks=2)
    v1_path = storage_root / v1.file_path_storage
    initial_jobs = len(fake_db.jobs)

    # 1. No-op PATCH creates 0 jobs and 0 audit logs
    noop = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": doc.title},
        headers=headers,
    )
    assert noop.status_code == status.HTTP_200_OK
    assert len(fake_db.jobs) == initial_jobs
    assert len(fake_db.audit_logs) == 0

    # 2. RAG-relevant metadata PATCH on PUBLISHED doc enqueues REINDEX + STALE
    rag_patch = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Revised Official IIT Guide"},
        headers=headers,
    )
    assert rag_patch.status_code == status.HTTP_200_OK
    assert rag_patch.json()["index_status"] == IndexStatusEnum.STALE.value
    assert len(fake_db.jobs) == initial_jobs + 1

    # Inspect sync-jobs & logs endpoints
    jobs_res = client.get(f"/api/v1/knowledge/documents/{doc.id}/sync-jobs", headers=headers)
    assert jobs_res.status_code == status.HTTP_200_OK
    pending_jobs = [j for j in jobs_res.json()["items"] if j["status"] == "PENDING"]
    assert len(pending_jobs) == 1
    assert pending_jobs[0]["action_type"] == "REINDEX"
    assert len(pending_jobs[0]["logs"]) >= 1

    job_logs_res = client.get(
        f"/api/v1/knowledge/documents/sync-jobs/{pending_jobs[0]['id']}/logs",
        headers=headers,
    )
    assert job_logs_res.status_code == status.HTTP_200_OK
    assert job_logs_res.json()["total"] >= 1
    assert job_logs_res.json()["items"][0]["stage"] == "CHANGE_DETECTOR"

    # 3. Unpublish (PUBLISHED -> DRAFT) deactivates chunks & queues DEACTIVATE
    unpub = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"is_published": False},
        headers=headers,
    )
    assert unpub.status_code == status.HTTP_200_OK
    assert all(not c.is_active for c in fake_db.chunks.values())
    assert any(
        j.action_type == "DEACTIVATE" and j.status == "PENDING" for j in fake_db.jobs.values()
    )

    # 4. Soft-delete (default purge_storage=false) preserves file, deactivates versions/chunks, logs
    del_res = client.delete(f"/api/v1/knowledge/documents/{doc.id}", headers=headers)
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert doc.status == "DELETED"
    assert fake_db.versions[v1.id].is_active is False
    assert all(not c.is_active for c in fake_db.chunks.values())
    assert v1_path.exists()  # deferred to PURGE job per SA-14 §6.3
    assert any(a.action == "knowledge.document.delete" for a in fake_db.audit_logs)

    # 5. Delete with purge_storage=true unlinks physical file and queues PURGE
    doc2, v2_1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_PURGE_NOW")
    v2_path = storage_root / v2_1.file_path_storage
    assert v2_path.exists()
    purge_res = client.delete(
        f"/api/v1/knowledge/documents/{doc2.id}?purge_storage=true",
        headers=headers,
    )
    assert purge_res.status_code == status.HTTP_204_NO_CONTENT
    assert not v2_path.exists()
    assert any(
        j.version_id == v2_1.id and j.action_type == "PURGE" for j in fake_db.jobs.values()
    )


# ---------------------------------------------------------------------------
# 6. Strict RBAC, NAU_RESPONSABLE school_scope Isolation & PII-Free Audit Checks
# ---------------------------------------------------------------------------


def test_rbac_and_responsable_school_scope_isolation(sync_env):
    client, fake_db, storage_root = sync_env
    iit_doc, _, iit_job = _seed_doc_with_v1(
        fake_db, storage_root, code="IIT_SCOPED_DOC", school="iit"
    )
    isb_doc, _, _ = _seed_doc_with_v1(
        fake_db, storage_root, code="ISB_SCOPED_DOC", school="isb"
    )

    student_headers = {"Authorization": f"Bearer {_make_token(STUDENT_ID, 'student')}"}
    rh_headers = {"Authorization": f"Bearer {_make_token(RH_ID, 'rh')}"}
    dir_headers = {"Authorization": f"Bearer {_make_token(DIRECTION_ID, 'direction')}"}
    resp_iit_headers = {
        "Authorization": f"Bearer {_make_token(RESP_IIT_ID, 'responsable', school_scope='iit')}"
    }
    resp_isb_headers = {
        "Authorization": f"Bearer {_make_token(RESP_ISB_ID, 'responsable', school_scope='isb')}"
    }

    # Unauthenticated -> 401
    assert (
        client.get(f"/api/v1/knowledge/documents/{iit_doc.id}/versions").status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # Student -> 403 on all SA-56 change-detection, version & sync endpoints
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions", headers=student_headers
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions",
            files={"file": ("x.pdf", PDF_V2, "application/pdf")},
            headers=student_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{iit_doc.id}/sync-jobs", headers=student_headers
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.post(
            "/api/v1/knowledge/documents/check-changes",
            data={"document_id": str(iit_doc.id)},
            headers=student_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )

    # Human RH -> read-only allowed (200), write forbidden (403)
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions", headers=rh_headers
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/sync-jobs/{iit_job.id}/logs", headers=rh_headers
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions",
            files={"file": ("x.pdf", PDF_V2, "application/pdf")},
            headers=rh_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )

    # Direction -> read-only allowed on versions & sync-jobs, forbidden on new version mutations
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions", headers=dir_headers
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions",
            files={"file": ("x.pdf", PDF_V2, "application/pdf")},
            headers=dir_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )

    # Responsable IIT -> ALLOWED on IIT doc, FORBIDDEN on ISB doc
    ok_iit = client.post(
        f"/api/v1/knowledge/documents/{iit_doc.id}/versions",
        files={"file": ("guide_v2.pdf", PDF_V2, "application/pdf")},
        headers=resp_iit_headers,
    )
    assert ok_iit.status_code == status.HTTP_201_CREATED

    blocked_isb = client.post(
        f"/api/v1/knowledge/documents/{isb_doc.id}/versions",
        files={"file": ("guide_v2.pdf", PDF_V2, "application/pdf")},
        headers=resp_iit_headers,
    )
    assert blocked_isb.status_code == status.HTTP_403_FORBIDDEN

    # Responsable ISB cannot read IIT sync job logs or delete IIT doc
    assert (
        client.get(
            f"/api/v1/knowledge/documents/sync-jobs/{iit_job.id}/logs",
            headers=resp_isb_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.delete(
            f"/api/v1/knowledge/documents/{iit_doc.id}", headers=resp_isb_headers
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )

    # Unknown account UUID -> 401 Unauthorized (audit attribution preserved)
    unknown_headers = {
        "Authorization": f"Bearer {_make_token(uuid.uuid4(), 'admin')}"
    }
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{iit_doc.id}/versions",
            files={"file": ("guide_v3.pdf", PDF_V3, "application/pdf")},
            headers=unknown_headers,
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_edge_cases_unindexed_activation_inline_content_and_patch_dedup(sync_env):
    """Covers 0-chunk activation -> REINDEX, inline markdown upload, 422 guard, and PATCH dedup."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    # 1. Missing both file & content on version upload -> 422
    doc, _, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_EDGE_MD",
        file_bytes=MD_V1_LF,
        filename="content.md",
        mime_type="text/markdown",
        with_chunks=0,
    )
    missing_body = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"changelog": "No payload"},
        headers=admin_headers,
    )
    assert missing_body.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 2. Inline markdown content upload creates v2 (with CRLF -> LF normalized hash)
    res_inline = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"content": MD_V2_LF.decode("utf-8"), "changelog": "Inline update"},
        headers=admin_headers,
    )
    assert res_inline.status_code == status.HTTP_201_CREATED
    assert res_inline.json()["version"]["version_number"] == 2
    assert res_inline.json()["content_diff"]["lines_added"] == 2

    # 3. Activating v2 when it has 0 chunks automatically enqueues REINDEX + STALE
    act_zero = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions/2/activate",
        json={"reason": "Activate unindexed v2", "enqueue_reindex_if_unindexed": True},
        headers=admin_headers,
    )
    assert act_zero.status_code == status.HTTP_200_OK
    assert act_zero.json()["activated_chunks_count"] == 0
    assert act_zero.json()["sync_job"] is not None
    assert act_zero.json()["sync_job"]["action_type"] == "REINDEX"
    assert act_zero.json()["index_status"] == IndexStatusEnum.STALE.value

    # 4. Repeated RAG-relevant PATCH while REINDEX is still PENDING deduplicates (no extra job)
    pending_before = sum(1 for j in fake_db.jobs.values() if j.status == "PENDING")
    p1 = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Title Update One"},
        headers=admin_headers,
    )
    p2 = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Title Update Two"},
        headers=admin_headers,
    )
    assert p1.status_code == status.HTTP_200_OK
    assert p2.status_code == status.HTTP_200_OK
    pending_after = sum(1 for j in fake_db.jobs.values() if j.status == "PENDING")
    assert pending_after == pending_before

    # 5. Invalid version_number (<= 0) rejected with 422
    bad_ver = client.get(
        f"/api/v1/knowledge/documents/{doc.id}/versions/0/download",
        headers=admin_headers,
    )
    assert bad_ver.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 6. Audit logs contain zero raw markdown/document text or PII fields
    for entry in fake_db.audit_logs:
        meta_str = str(entry.event_metadata).lower()
        assert "nouvelle section b" not in meta_str
        assert "email" not in meta_str
        assert "phone" not in meta_str


def test_post_rollback_index_status_cancellation_and_sync_log(sync_env):
    """Fixes #1 & #2: rollback restores INDEXED across GETs, cancels v2 PENDING, logs rollback."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    doc, v1, v1_job = _seed_doc_with_v1(
        fake_db, storage_root, code="IIT_RB_STATUS", with_chunks=2, job_status="SYNCED"
    )

    # Upload staged v2 (leaves v2 PENDING REINDEX job)
    res_v2 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("guide.pdf", PDF_V2, "application/pdf")},
        headers=admin_headers,
    )
    assert res_v2.status_code == status.HTTP_201_CREATED
    v2_id = uuid.UUID(res_v2.json()["version"]["id"])
    v2_job_id = uuid.UUID(res_v2.json()["sync_job"]["id"])

    # Re-uploading staged v2 bytes while v1 is active -> 409 wording says "staged" & "promote"
    dup_staged = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("guide.pdf", PDF_V2, "application/pdf")},
        headers=admin_headers,
    )
    assert dup_staged.status_code == status.HTTP_409_CONFLICT
    assert "staged version v2" in dup_staged.json()["detail"]
    assert "promote" in dup_staged.json()["detail"]

    # Insert 2 chunks for v2, promote v2 (keep v2's job PENDING to test rollback cancellation!)
    for idx in range(2):
        fake_db.add(
            KnowledgeChunk(
                id=uuid.uuid4(),
                document_id=doc.id,
                version_id=v2_id,
                chunk_index=idx,
                content=f"v2 chunk {idx}",
                token_count=8,
                embedding=[0.0] * 1024,
                chunk_metadata={},
                is_active=False,
            )
        )
    prom = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions/2/activate",
        json={"reason": "Promote v2"},
        headers=admin_headers,
    )
    assert prom.status_code == status.HTTP_200_OK

    # Now roll back from v2 -> v1 (v1 has 2 chunks -> activated_chunks > 0, sync_job is None)
    logs_before_rb = len(fake_db.sync_logs)
    rb = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions/1/activate",
        json={"reason": "Rollback to verified v1"},
        headers=admin_headers,
    )
    assert rb.status_code == status.HTTP_200_OK
    assert rb.json()["is_rollback"] is True
    assert rb.json()["index_status"] == IndexStatusEnum.INDEXED.value

    # Fix #2: v2's PENDING job MUST be cancelled and a ROLLBACK DocumentSyncLog MUST be emitted
    assert fake_db.jobs[v2_job_id].status == "CANCELLED"
    assert len(fake_db.sync_logs) > logs_before_rb
    assert any(
        lg.job_id == v1_job.id and (lg.details or {}).get("event") == "ROLLBACK"
        for lg in fake_db.sync_logs
    )

    # Fix #1: GET /{id}, GET /versions, GET /sync-jobs, and UNCHANGED upload ALL report INDEXED
    assert (
        client.get(f"/api/v1/knowledge/documents/{doc.id}").json()["index_status"]
        == IndexStatusEnum.INDEXED.value
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{doc.id}/versions", headers=admin_headers
        ).json()["index_status"]
        == IndexStatusEnum.INDEXED.value
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{doc.id}/sync-jobs", headers=admin_headers
        ).json()["index_status"]
        == IndexStatusEnum.INDEXED.value
    )
    unchanged_v1 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        files={"file": ("guide.pdf", PDF_V1, "application/pdf")},
        headers=admin_headers,
    )
    assert unchanged_v1.status_code == status.HTTP_200_OK
    assert unchanged_v1.json()["index_status"] == IndexStatusEnum.INDEXED.value

    # Subsequent RAG-relevant PATCH on rolled-back v1 transitions GET /{id} to STALE
    patch_v1 = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Updated Title After Rollback"},
        headers=admin_headers,
    )
    assert patch_v1.status_code == status.HTTP_200_OK
    assert patch_v1.json()["index_status"] == IndexStatusEnum.STALE.value
    assert (
        client.get(f"/api/v1/knowledge/documents/{doc.id}").json()["index_status"]
        == IndexStatusEnum.STALE.value
    )


def test_patch_status_deleted_mime_fallback_and_scope_ordering(sync_env):
    """Fixes #3, #4, #5: PATCH DELETED vector cleanup, octet-stream .md fallback, 403 before 404."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}
    resp_isb_headers = {
        "Authorization": f"Bearer {_make_token(RESP_ISB_ID, 'responsable', school_scope='isb')}"
    }
    resp_iit_headers = {
        "Authorization": f"Bearer {_make_token(RESP_IIT_ID, 'responsable', school_scope='iit')}"
    }

    # Fix #4: Windows/curl application/octet-stream upload of .md infers text/markdown cleanly
    create_md = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_OCTET_MD",
            "title": "Octet Stream Markdown",
            "school_code": "iit",
            "doc_type": "faq",
            "is_published": "true",
        },
        files={"file": ("faq.md", MD_V1_LF, "application/octet-stream")},
        headers=admin_headers,
    )
    assert create_md.status_code == status.HTTP_201_CREATED
    doc_id = uuid.UUID(create_md.json()["id"])
    assert create_md.json()["current_version"]["mime_type"] == "text/markdown"

    # Also works on POST /{id}/versions with application/octet-stream
    ver2_md = client.post(
        f"/api/v1/knowledge/documents/{doc_id}/versions",
        files={"file": ("faq.md", MD_V2_LF, "application/octet-stream")},
        headers=admin_headers,
    )
    assert ver2_md.status_code == status.HTTP_201_CREATED
    assert ver2_md.json()["version"]["mime_type"] == "text/markdown"

    # Fix #5: Cross-school responsable on PATCH with non-existent program gets 403 (not 404)
    cross_patch = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"target_program_id": str(uuid.uuid4())},
        headers=resp_isb_headers,
    )
    assert cross_patch.status_code == status.HTTP_403_FORBIDDEN

    # Fix #5: IIT responsable trying to check-changes moving IIT doc to ISB gets 403
    cross_check = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"document_id": str(doc_id), "school_code": "isb"},
        headers=resp_iit_headers,
    )
    assert cross_check.status_code == status.HTTP_403_FORBIDDEN

    # Fix #3: PATCH status="DELETED" deactivates versions & chunks, cancels jobs, queues DEACTIVATE
    doc = fake_db.documents[doc_id]
    fake_db.add(
        KnowledgeChunk(
            id=uuid.uuid4(),
            document_id=doc_id,
            version_id=doc.current_version_id,
            chunk_index=0,
            content="Active chunk before PATCH delete",
            token_count=6,
            embedding=[0.0] * 1024,
            chunk_metadata={},
            is_active=True,
        )
    )
    patch_del = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"status": "DELETED"},
        headers=admin_headers,
    )
    assert patch_del.status_code == status.HTTP_200_OK
    assert patch_del.json()["status"] == "DELETED"
    assert patch_del.json()["index_status"] == IndexStatusEnum.NOT_INDEXED.value
    assert all(not c.is_active for c in fake_db.chunks.values() if c.document_id == doc_id)
    assert all(not v.is_active for v in fake_db.versions.values() if v.document_id == doc_id)
    doc_ver_ids = {v.id for v in fake_db.versions.values() if v.document_id == doc_id}
    assert any(
        j.version_id in doc_ver_ids and j.action_type == "DEACTIVATE" and j.status == "PENDING"
        for j in fake_db.jobs.values()
    )
    assert any(
        j.version_id in doc_ver_ids and j.status == "CANCELLED"
        for j in fake_db.jobs.values()
    )


# ---------------------------------------------------------------------------
# 7. PR #101 Review Regression Tests (Must Fixes #1-#6 & Minor Polish #1-#3)
# ---------------------------------------------------------------------------


def test_pr101_must_fix_1_sync_job_reuse_ingest_and_same_version_flip(sync_env):
    """Must Fix #1: PATCH publish reuses PENDING INGEST; action flips cancel conflicting PENDING."""
    client, fake_db, _ = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    # 1. Create DRAFT doc with v1 -> enqueues PENDING INGEST on v1
    create_res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_MF1_CONFLICT",
            "title": "Must Fix 1 Draft Doc",
            "school_code": "iit",
            "doc_type": "brochure",
            "is_published": "false",
        },
        files={"file": ("mf1.pdf", PDF_V1, "application/pdf")},
        headers=admin_headers,
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    doc_id = uuid.UUID(create_res.json()["id"])
    v1_id = uuid.UUID(create_res.json()["current_version"]["id"])

    v1_pending = [
        j for j in fake_db.jobs.values() if j.version_id == v1_id and j.status == "PENDING"
    ]
    assert len(v1_pending) == 1
    ingest_job_id = v1_pending[0].id
    assert v1_pending[0].action_type == "INGEST"

    # 2. Publish via PATCH -> must REUSE existing PENDING INGEST (no conflicting REINDEX)
    pub_res = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"is_published": True},
        headers=admin_headers,
    )
    assert pub_res.status_code == status.HTTP_200_OK
    v1_pending_after_pub = [
        j for j in fake_db.jobs.values() if j.version_id == v1_id and j.status == "PENDING"
    ]
    assert len(v1_pending_after_pub) == 1
    assert v1_pending_after_pub[0].id == ingest_job_id
    assert v1_pending_after_pub[0].action_type == "INGEST"
    assert any(
        lg.job_id == ingest_job_id
        and lg.stage == "CHANGE_DETECTOR"
        and "reused" in lg.message.lower()
        for lg in fake_db.sync_logs
    )

    # 3. Unpublish (PUBLISHED -> DRAFT) -> cancels PENDING INGEST on v1 & queues PENDING DEACTIVATE
    unpub_res = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"is_published": False},
        headers=admin_headers,
    )
    assert unpub_res.status_code == status.HTTP_200_OK
    assert fake_db.jobs[ingest_job_id].status == "CANCELLED"
    v1_pending_after_unpub = [
        j for j in fake_db.jobs.values() if j.version_id == v1_id and j.status == "PENDING"
    ]
    assert len(v1_pending_after_unpub) == 1
    deact_job_id = v1_pending_after_unpub[0].id
    assert v1_pending_after_unpub[0].action_type == "DEACTIVATE"

    # 4. Re-publish (DRAFT -> PUBLISHED) -> cancels PENDING DEACTIVATE & queues PENDING REINDEX
    repub_res = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"is_published": True},
        headers=admin_headers,
    )
    assert repub_res.status_code == status.HTTP_200_OK
    assert fake_db.jobs[deact_job_id].status == "CANCELLED"
    v1_pending_after_repub = [
        j for j in fake_db.jobs.values() if j.version_id == v1_id and j.status == "PENDING"
    ]
    assert len(v1_pending_after_repub) == 1
    assert v1_pending_after_repub[0].action_type == "REINDEX"


def test_pr101_must_fix_2_patch_preserves_staged_v2_pending_job(sync_env):
    """Must Fix #2: PATCH on v1 must NOT cancel a newer staged v2's PENDING indexing job."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    doc, v1, _ = _seed_doc_with_v1(
        fake_db, storage_root, code="IIT_MF2_STAGED", job_status="SYNCED"
    )

    # Upload staged v2 (activate_immediately=false -> v1 stays current, v2 has PENDING REINDEX)
    res_v2 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"activate_immediately": "false"},
        files={"file": ("guide.pdf", PDF_V2, "application/pdf")},
        headers=admin_headers,
    )
    assert res_v2.status_code == status.HTTP_201_CREATED
    v2_job_id = uuid.UUID(res_v2.json()["sync_job"]["id"])
    assert doc.current_version_id == v1.id
    assert fake_db.jobs[v2_job_id].status == "PENDING"

    # Edit RAG-relevant metadata via PATCH while v2 is staged
    patch_res = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Updated Title While V2 Staged"},
        headers=admin_headers,
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert patch_res.json()["index_status"] == IndexStatusEnum.STALE.value
    # Staged v2's PENDING job MUST remain PENDING (not cancelled!)
    assert fake_db.jobs[v2_job_id].status == "PENDING"

    # Even if v1's REINDEX completes (SYNCED), GET /{id} MUST stay STALE because v2 is PENDING
    for job in fake_db.jobs.values():
        if job.version_id == v1.id and job.status == "PENDING":
            job.status = "SYNCED"

    get_res = client.get(f"/api/v1/knowledge/documents/{doc.id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["index_status"] == IndexStatusEnum.STALE.value


def test_pr101_must_fix_3_activate_immediately_deactivates_old_chunks(sync_env):
    """Must Fix #3: create_document_version(activate_immediately=True) deactivates old chunks."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    doc, v1, _ = _seed_doc_with_v1(
        fake_db, storage_root, code="IIT_MF3_CHUNKS", with_chunks=3
    )
    v1_chunks = [c for c in fake_db.chunks.values() if c.version_id == v1.id]
    assert len(v1_chunks) == 3
    assert all(c.is_active for c in v1_chunks)

    res_v2 = client.post(
        f"/api/v1/knowledge/documents/{doc.id}/versions",
        data={"activate_immediately": "true", "changelog": "Force activate v2"},
        files={"file": ("guide_v2.pdf", PDF_V2, "application/pdf")},
        headers=admin_headers,
    )
    assert res_v2.status_code == status.HTTP_201_CREATED
    v2_id = uuid.UUID(res_v2.json()["version"]["id"])
    assert res_v2.json()["activated"] is True
    assert doc.current_version_id == v2_id
    assert fake_db.versions[v1.id].is_active is False
    assert all(not c.is_active for c in fake_db.chunks.values() if c.version_id == v1.id)


def test_pr101_must_fix_4_create_document_refresh_safety_and_integrity_error(
    sync_env, monkeypatch
):
    """Must Fix #4: validate pre-flush, IntegrityError->409+rollback, post-commit refresh safe."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    # A. Invalid file rejected BEFORE db.add / db.flush -> 0 DB docs created
    bad_res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_MF4_BAD_FILE",
            "title": "Invalid Empty File",
            "school_code": "iit",
            "doc_type": "brochure",
        },
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=admin_headers,
    )
    assert bad_res.status_code == status.HTTP_400_BAD_REQUEST
    assert len(fake_db.documents) == 0

    # B. Concurrent IntegrityError on commit -> 409 Conflict, rollback called, disk file cleaned
    rollback_called = False
    orig_rollback = fake_db.rollback
    orig_commit = fake_db.commit

    async def tracked_rollback():
        nonlocal rollback_called
        rollback_called = True
        await orig_rollback()

    async def integrity_commit():
        raise IntegrityError("INSERT INTO core.documents", {}, Exception("uq_documents_code"))

    monkeypatch.setattr(fake_db, "rollback", tracked_rollback)
    monkeypatch.setattr(fake_db, "commit", integrity_commit)

    conflict_res = client.post(
        "/api/v1/knowledge/documents",
        data={
            "code": "IIT_MF4_RACE",
            "title": "Concurrent Duplicate Code",
            "school_code": "iit",
            "doc_type": "brochure",
        },
        files={"file": ("race.pdf", PDF_V1, "application/pdf")},
        headers=admin_headers,
    )
    assert conflict_res.status_code == status.HTTP_409_CONFLICT
    assert rollback_called is True
    assert len(fake_db.documents) == 0
    assert list(storage_root.rglob("*.pdf")) == []

    # C. Post-commit db.refresh error MUST NOT delete the committed physical file on disk
    monkeypatch.setattr(fake_db, "commit", orig_commit)
    monkeypatch.setattr(fake_db, "rollback", orig_rollback)

    async def failing_refresh(_obj: Any):
        raise RuntimeError("Simulated post-commit refresh failure")

    monkeypatch.setattr(fake_db, "refresh", failing_refresh)

    with pytest.raises(RuntimeError, match="Simulated post-commit refresh failure"):
        client.post(
            "/api/v1/knowledge/documents",
            data={
                "code": "IIT_MF4_REFRESH_OK",
                "title": "Committed Before Refresh Fails",
                "school_code": "iit",
                "doc_type": "brochure",
            },
            files={"file": ("committed.pdf", PDF_V1, "application/pdf")},
            headers=admin_headers,
        )

    pdf_files = list(storage_root.rglob("*.pdf"))
    assert len(pdf_files) == 1
    assert pdf_files[0].read_bytes() == PDF_V1


def test_pr101_must_fix_5_cross_school_isolation_and_direction_delete_403(sync_env):
    """Must Fix #5: scoped responsable draft download/hash isolation & direction DELETE 403."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}
    resp_iit_headers = {
        "Authorization": f"Bearer {_make_token(RESP_IIT_ID, 'responsable', school_scope='iit')}"
    }
    dir_headers = {"Authorization": f"Bearer {_make_token(DIRECTION_ID, 'direction')}"}
    rh_headers = {"Authorization": f"Bearer {_make_token(RH_ID, 'rh')}"}

    isb_draft, _, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="ISB_DRAFT_SECRET",
        school="isb",
        doc_status="DRAFT",
        file_bytes=PDF_V2,
        filename="isb_secret.pdf",
    )
    isb_archived, _, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="ISB_ARCHIVED_SECRET",
        school="isb",
        doc_status="ARCHIVED",
        file_bytes=PDF_V3,
        filename="isb_archived.pdf",
    )
    isb_pub, _, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="ISB_PUBLIC_DOC",
        school="isb",
        doc_status="PUBLISHED",
        file_bytes=MD_V1_LF,
        filename="isb_public.md",
        mime_type="text/markdown",
    )
    iit_draft, iit_v1, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_OWN_DRAFT",
        school="iit",
        doc_status="DRAFT",
        file_bytes=PDF_V1,
        filename="iit_draft.pdf",
    )
    iit_v1_path = storage_root / iit_v1.file_path_storage

    # 1. IIT responsable gets 403 downloading unpublished ISB DRAFT/ARCHIVED docs
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{isb_draft.id}/download",
            headers=resp_iit_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{isb_archived.id}/download",
            headers=resp_iit_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{isb_draft.id}/versions/1/download",
            headers=resp_iit_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    # Allowed on published ISB doc and own-school IIT draft
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{isb_pub.id}/download",
            headers=resp_iit_headers,
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{iit_draft.id}/download",
            headers=resp_iit_headers,
        ).status_code
        == status.HTTP_200_OK
    )

    # 2. Cross-document hash matches do NOT leak ISB unpublished drafts to IIT responsable
    probe_draft = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "IIT_HASH_PROBE", "school_code": "iit"},
        files={"file": ("probe.pdf", PDF_V2, "application/pdf")},
        headers=resp_iit_headers,
    )
    assert probe_draft.status_code == status.HTTP_200_OK
    assert probe_draft.json()["cross_document_matches"] == []

    # Same guarantee on POST /{id}/versions
    ver_upload = client.post(
        f"/api/v1/knowledge/documents/{iit_draft.id}/versions",
        files={"file": ("iit_v2.pdf", PDF_V2, "application/pdf")},
        headers=resp_iit_headers,
    )
    assert ver_upload.status_code == status.HTTP_201_CREATED
    assert ver_upload.json()["cross_document_matches"] == []

    # Published active ISB doc IS visible in cross-document matches for IIT responsable
    probe_pub = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "IIT_PUB_PROBE", "school_code": "iit"},
        files={"file": ("pub.md", MD_V1_LF, "text/markdown")},
        headers=resp_iit_headers,
    )
    assert probe_pub.status_code == status.HTTP_200_OK
    assert [m["document_code"] for m in probe_pub.json()["cross_document_matches"]] == [
        "ISB_PUBLIC_DOC"
    ]

    # Admin sees all cross-school matches including ISB drafts
    admin_probe = client.post(
        "/api/v1/knowledge/documents/check-changes",
        data={"code": "ADMIN_PROBE"},
        files={"file": ("probe.pdf", PDF_V2, "application/pdf")},
        headers=admin_headers,
    )
    assert admin_probe.status_code == status.HTTP_200_OK
    assert "ISB_DRAFT_SECRET" in {
        m["document_code"] for m in admin_probe.json()["cross_document_matches"]
    }

    # 3. Direction (and RH) are strictly read-only -> 403 on DELETE & DELETE ?purge_storage=true
    assert (
        client.delete(
            f"/api/v1/knowledge/documents/{iit_draft.id}",
            headers=dir_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.delete(
            f"/api/v1/knowledge/documents/{iit_draft.id}?purge_storage=true",
            headers=dir_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        client.delete(
            f"/api/v1/knowledge/documents/{iit_draft.id}?purge_storage=true",
            headers=rh_headers,
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert iit_draft.status == "DRAFT"
    assert iit_v1_path.exists()

    # Scoped IIT responsable CAN delete own IIT doc (even with purge_storage=true)
    del_own = client.delete(
        f"/api/v1/knowledge/documents/{iit_draft.id}?purge_storage=true",
        headers=resp_iit_headers,
    )
    assert del_own.status_code == status.HTTP_204_NO_CONTENT
    assert iit_draft.status == "DELETED"
    assert not iit_v1_path.exists()


def test_pr101_must_fix_6_select_for_update_on_patch_and_delete(sync_env):
    """Must Fix #6: PATCH /{id} and DELETE /{id} lock CorpusDocument with SELECT ... FOR UPDATE."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}
    doc, _, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_MF6_LOCK")

    fake_db.executed_sql.clear()
    patch_res = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"title": "Locked Metadata Update"},
        headers=admin_headers,
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert any(
        "from core.documents" in q and "for update" in q for q in fake_db.executed_sql
    )

    fake_db.executed_sql.clear()
    del_res = client.delete(
        f"/api/v1/knowledge/documents/{doc.id}",
        headers=admin_headers,
    )
    assert del_res.status_code == status.HTTP_204_NO_CONTENT
    assert any(
        "from core.documents" in q and "for update" in q for q in fake_db.executed_sql
    )


def test_pr101_minor_polish_1_batch_queries_eliminate_n_plus_1(sync_env):
    """Minor Polish #1: list_documents & list_document_sync_jobs use bulk IN queries (no N+1)."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    seeded_docs: list[CorpusDocument] = []
    for idx in range(5):
        doc, ver, job = _seed_doc_with_v1(
            fake_db,
            storage_root,
            code=f"IIT_BATCH_{idx}",
            job_status="SYNCED",
        )
        seeded_docs.append(doc)
        if idx == 0:
            # Add two more jobs + logs on doc 0 to test sync-jobs log batching
            for extra_idx in range(2):
                extra_job = DocumentSyncJob(
                    id=uuid.uuid4(),
                    version_id=ver.id,
                    action_type="REINDEX",
                    pipeline_stage="COMPLETED",
                    status="SYNCED",
                )
                fake_db.add(extra_job)
                fake_db.add(
                    DocumentSyncLog(
                        job_id=extra_job.id,
                        stage="CHANGE_DETECTOR",
                        level="INFO",
                        message=f"Extra log {extra_idx}",
                        details={},
                    )
                )
            fake_db.add(
                DocumentSyncLog(
                    job_id=job.id,
                    stage="CHANGE_DETECTOR",
                    level="INFO",
                    message="Initial log",
                    details={},
                )
            )

    # 1. GET /documents across 5 docs must issue exactly 3 bulk SQL queries (not 15-20!)
    fake_db.executed_sql.clear()
    list_res = client.get("/api/v1/knowledge/documents")
    assert list_res.status_code == status.HTTP_200_OK
    assert list_res.json()["total"] == 5
    assert len(fake_db.executed_sql) == 3
    assert any(
        "from core.document_versions" in q and "document_id in" in q
        for q in fake_db.executed_sql
    )
    assert any(
        "from core.document_sync_jobs" in q and "version_id in" in q
        for q in fake_db.executed_sql
    )

    # 2. GET /{id}/sync-jobs across 3 jobs must batch-load all DocumentSyncLogs in 1 IN query
    fake_db.executed_sql.clear()
    jobs_res = client.get(
        f"/api/v1/knowledge/documents/{seeded_docs[0].id}/sync-jobs",
        headers=admin_headers,
    )
    assert jobs_res.status_code == status.HTTP_200_OK
    items = jobs_res.json()["items"]
    assert len(items) == 3
    assert all(len(item["logs"]) == 1 for item in items)
    log_queries = [q for q in fake_db.executed_sql if "from core.document_sync_logs" in q]
    assert len(log_queries) == 1
    assert "job_id in" in log_queries[0]


def test_pr101_minor_polish_2_patch_clears_nullable_fields(sync_env):
    """Minor Polish #2: explicit null clears description and unlinks target_program_id."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    doc, v1, _ = _seed_doc_with_v1(fake_db, storage_root, code="IIT_NULLABLE_PATCH")
    doc.target_program_id = PROGRAM_ID
    doc.description = "Linked program description"
    jobs_before = len(fake_db.jobs)

    # 1. Explicit null clears description without triggering REINDEX (cosmetic field)
    clear_desc = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"description": None},
        headers=admin_headers,
    )
    assert clear_desc.status_code == status.HTTP_200_OK
    assert clear_desc.json()["description"] is None
    assert clear_desc.json()["target_program_id"] == str(PROGRAM_ID)
    assert len(fake_db.jobs) == jobs_before

    # 2. Explicit null unlinks target_program_id AND triggers RAG-relevant REINDEX
    unlink_prog = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"target_program_id": None},
        headers=admin_headers,
    )
    assert unlink_prog.status_code == status.HTTP_200_OK
    assert unlink_prog.json()["target_program_id"] is None
    assert unlink_prog.json()["index_status"] == IndexStatusEnum.STALE.value
    assert any(
        j.version_id == v1.id and j.action_type == "REINDEX" and j.status == "PENDING"
        for j in fake_db.jobs.values()
    )
    assert any(
        "target_program_id" in (a.event_metadata or {}).get("changed_fields", [])
        for a in fake_db.audit_logs
    )

    # 3. Repeating explicit null when already None is a no-op (0 extra audit entries)
    audits_before = len(fake_db.audit_logs)
    noop_null = client.patch(
        f"/api/v1/knowledge/documents/{doc.id}",
        json={"target_program_id": None},
        headers=admin_headers,
    )
    assert noop_null.status_code == status.HTTP_200_OK
    assert len(fake_db.audit_logs) == audits_before


def test_pr101_minor_polish_3_rfc5987_non_ascii_content_disposition(sync_env):
    """Minor Polish #3: French/Arabic filenames emit ASCII fallback + RFC 5987 UTF-8 header."""
    client, fake_db, storage_root = sync_env
    admin_headers = {"Authorization": f"Bearer {_make_token(ADMIN_ID, 'admin')}"}

    french_name = "Règlement_d\u2019\u0153uvre_2026.pdf"
    arabic_name = "\u062f\u0644\u064a\u0644_\u0627\u0644\u062a\u0648\u062c\u064a\u0647.pdf"

    fr_doc, fr_ver, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_FR_RFC5987",
        filename=french_name,
    )
    ar_doc, ar_ver, _ = _seed_doc_with_v1(
        fake_db,
        storage_root,
        code="IIT_AR_RFC5987",
        filename=arabic_name,
    )

    for doc_obj, ver_obj, expected_fallback in (
        (fr_doc, fr_ver, "Reglement_d_oeuvre_2026.pdf"),
        (ar_doc, ar_ver, "document.pdf"),
    ):
        for url in (
            f"/api/v1/knowledge/documents/{doc_obj.id}/download",
            f"/api/v1/knowledge/documents/{doc_obj.id}/versions/1/download",
        ):
            res = client.get(url, headers=admin_headers)
            assert res.status_code == status.HTTP_200_OK
            assert res.content == PDF_V1
            cd = res.headers["Content-Disposition"]
            assert cd.isascii()
            assert f'filename="{expected_fallback}"' in cd
            assert f"filename*=UTF-8''{quote(ver_obj.file_name, safe='')}" in cd



