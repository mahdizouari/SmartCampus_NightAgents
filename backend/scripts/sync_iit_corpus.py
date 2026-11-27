"""Idempotent SA-56 PostgreSQL synchronizer for the scraped `https://iit.tn/` RAG corpus.

Component type: **deterministic code** (SHA-256 change detection, immutable versioned storage,
and PostgreSQL `CorpusDocument` / `CorpusDocumentVersion` / `DocumentSyncJob` registration) —
NOT an AI agent.

Behavior:
- Run 1 (empty corpus tables): registers each scraped `.md` file from `manifest.json` as
  `NEW_DOCUMENT` (`status='PUBLISHED'`, `v1` in `storage/knowledge/iit/<code>/v1/`, and one
  `PENDING INGEST` `DocumentSyncJob` in `core.document_sync_jobs`).
- Run 2+ (unchanged `.md` SHA-256): detects `UNCHANGED` via `SA-56` (`create_document_version`)
  and skips cleanly with **zero duplicate versions and zero duplicate sync jobs**.
- Run N (updated `.md` content after a website update): creates `v{N}` atomically and enqueues
  a single `PENDING REINDEX` job while deactivating superseded chunks/versions.

Usage:
    docker compose exec backend python scripts/sync_iit_corpus.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Ensure `app.*` imports resolve when executed directly as `python scripts/sync_iit_corpus.py`
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402
from app.core.deps import CurrentActor  # noqa: E402
from app.models.catalog import Program, School  # noqa: E402
from app.models.documents_rag import (  # noqa: E402
    CorpusDocument,
    CorpusDocumentVersion,
    DocumentSyncJob,
)
from app.models.users import Account, AccountRole, Role  # noqa: E402
from app.schemas.knowledge import (  # noqa: E402
    ALLOWED_MIME_TYPES,
    ChangeTypeEnum,
    DocumentStatusEnum,
    SyncActionTypeEnum,
    SyncJobStatusEnum,
    SyncLogLevelEnum,
    SyncPipelineStageEnum,
)
from app.services import document_sync_service as sync_svc  # noqa: E402
from app.services import file_storage_service as storage_svc  # noqa: E402

logger = logging.getLogger("sync_iit_corpus")

DEFAULT_CORPUS_DIR = _BACKEND_ROOT / "data" / "corpus" / "iit"
DEFAULT_IIT_RESPONSABLE_EMAIL = "responsable.iit@smartcampus.tn"
FALLBACK_ADMIN_EMAIL = "admin@smartcampus.tn"


@dataclass
class DocumentSyncOutcome:
    """Per-document synchronization result returned by `sync_iit_corpus_to_db`."""

    code: str
    source_url: str
    change_type: str
    version_number: int
    sha256: str
    sync_job_id: str | None


@dataclass
class CorpusSyncSummary:
    """Aggregated synchronization summary across all manifest documents."""

    corpus_dir: str
    total_manifest_documents: int
    created_new: int = 0
    updated_versions: int = 0
    reactivated_versions: int = 0
    skipped_unchanged: int = 0
    jobs_enqueued: int = 0
    outcomes: list[DocumentSyncOutcome] = field(default_factory=list)


async def _resolve_or_bootstrap_iit_actor(db: AsyncSession) -> CurrentActor:
    """Resolve existing IIT Responsable (or Admin) account without polluting `seed_db` counts.

    If `seed_db.py` has already populated `core.accounts`, reuses `responsable.iit@smartcampus.tn`
    (or `admin@smartcampus.tn`). On a blank database prior to seeding, bootstraps `iit` school,
    `responsable` role, and `responsable.iit@smartcampus.tn` using the exact `seed_db.py` email
    so subsequent `seed_db.py` runs merge via `ON CONFLICT (email) DO UPDATE` with zero extra rows.
    """
    for email, role_name, scope in (
        (DEFAULT_IIT_RESPONSABLE_EMAIL, "responsable", "iit"),
        (FALLBACK_ADMIN_EMAIL, "admin", None),
    ):
        res = await db.execute(select(Account).where(Account.email == email))
        acc = res.scalar_one_or_none()
        if acc is not None:
            return CurrentActor(
                account_id=str(acc.id),
                role=role_name,
                school_scope=scope,
                email=acc.email,
            )

    any_res = await db.execute(select(Account).limit(1))
    any_acc = any_res.scalar_one_or_none()
    if any_acc is not None:
        return CurrentActor(
            account_id=str(any_acc.id),
            role="admin",
            school_scope=None,
            email=any_acc.email,
        )

    # Blank database fallback: bootstrap IIT school + responsable role + canonical IIT responsable
    school_res = await db.execute(select(School).where(School.code == "iit"))
    if school_res.scalar_one_or_none() is None:
        db.add(
            School(
                code="iit",
                name="Institut International de Technologie",
                is_extension=False,
                is_active=True,
            )
        )
        await db.flush()

    role_res = await db.execute(select(Role).where(Role.code == "responsable"))
    role_obj = role_res.scalar_one_or_none()
    if role_obj is None:
        role_obj = Role(
            code="responsable",
            label="Responsable de Filière",
            description="Responsable pédagogique d'une école (IIT, ISB ou ISA).",
        )
        db.add(role_obj)
        await db.flush()

    boot_acc = Account(
        id=uuid.uuid4(),
        email=DEFAULT_IIT_RESPONSABLE_EMAIL,
        full_name="Dr. Mohamed Ben Salah (Resp. IIT)",
        auth_provider="EMAIL_OTP",
        department="Département Ingénierie & Technologies IIT",
        is_active=True,
    )
    db.add(boot_acc)
    await db.flush()

    db.add(
        AccountRole(
            id=uuid.uuid4(),
            account_id=boot_acc.id,
            role_id=role_obj.id,
            school_scope="iit",
            responsibility_scope="ALL",
        )
    )
    await db.commit()

    return CurrentActor(
        account_id=str(boot_acc.id),
        role="responsable",
        school_scope="iit",
        email=boot_acc.email,
    )


def load_corpus_manifest(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[dict[str, Any]]:
    """Load and validate `manifest.json` from the scraped IIT corpus directory."""
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"IIT corpus manifest not found at '{manifest_path}'. "
            "Run `python scripts/scrape_iit_tn.py` first."
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = payload.get("documents")
    if not isinstance(docs, list) or not docs:
        raise ValueError(f"Manifest '{manifest_path}' contains no document entries.")
    return docs


async def _create_initial_corpus_document(
    db: AsyncSession,
    *,
    actor: CurrentActor,
    code: str,
    title: str,
    description: str,
    school: str,
    doc_type: str,
    target_program_id: uuid.UUID | None,
    file_name: str,
    raw_bytes: bytes,
    source_url: str,
    storage_root: Path | None = None,
) -> DocumentSyncOutcome:
    """Register a brand-new official IIT `CorpusDocument` (`v1`, `PUBLISHED`, `PENDING INGEST`)."""
    user_uuid = uuid.UUID(actor.account_id)
    safe_name = storage_svc.sanitize_filename(file_name)
    canonical_bytes = sync_svc.prepare_bytes_for_storage(raw_bytes, "text/markdown")
    sub_path = sync_svc.build_versioned_sub_path(school, code, 1, safe_name)

    meta = storage_svc.save_document_bytes(
        canonical_bytes,
        filename=safe_name,
        bucket=settings.STORAGE_BUCKET_KNOWLEDGE,
        sub_path=sub_path,
        declared_mime_type="text/markdown",
        allowed_mime_types=ALLOWED_MIME_TYPES,
        storage_root=storage_root,
    )

    try:
        doc = CorpusDocument(
            id=uuid.uuid4(),
            code=code,
            title=title[:255],
            description=description,
            school=school,
            category=doc_type,
            target_program_id=target_program_id,
            status=DocumentStatusEnum.PUBLISHED.value,
            created_by=user_uuid,
        )
        db.add(doc)
        await db.flush()

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
            uploaded_by=user_uuid,
            changelog=f"Initial official web scrape from {source_url}",
            is_active=True,
        )
        db.add(version)
        await db.flush()
        doc.current_version_id = version_id

        job = DocumentSyncJob(
            id=uuid.uuid4(),
            version_id=version_id,
            action_type=SyncActionTypeEnum.INGEST.value,
            pipeline_stage=SyncPipelineStageEnum.PARSING.value,
            status=SyncJobStatusEnum.PENDING.value,
        )
        db.add(job)
        await db.flush()

        sync_svc.append_sync_log(
            db,
            job_id=job.id,
            stage=sync_svc.SYNC_LOG_STAGE_CHANGE_DETECTOR,
            level=SyncLogLevelEnum.INFO.value,
            message=(
                f"Official IIT page '{code}' registered as v1 (NEW_DOCUMENT, "
                f"sha256={meta.file_hash_sha256[:12]}...)."
            ),
            details={
                "change_type": ChangeTypeEnum.NEW_DOCUMENT.value,
                "document_id": str(doc.id),
                "document_code": code,
                "source_url": source_url,
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
                "document_code": code,
                "school": school,
                "category": doc_type,
                "source_url": source_url,
                "status": doc.status,
                "sync_job_id": str(job.id),
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        storage_svc.delete_document_bytes(meta.storage_path, storage_root=storage_root)
        raise

    return DocumentSyncOutcome(
        code=code,
        source_url=source_url,
        change_type=ChangeTypeEnum.NEW_DOCUMENT.value,
        version_number=1,
        sha256=meta.file_hash_sha256,
        sync_job_id=str(job.id),
    )


async def sync_iit_corpus_to_db(
    db: AsyncSession,
    *,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    actor: CurrentActor | None = None,
    storage_root: Path | None = None,
) -> CorpusSyncSummary:
    """Idempotently synchronize all scraped `iit.tn` Markdown documents into PostgreSQL (SA-56)."""
    manifest_docs = load_corpus_manifest(corpus_dir)
    effective_actor = actor or await _resolve_or_bootstrap_iit_actor(db)

    prog_res = await db.execute(
        select(Program.code, Program.id).where(Program.school_code == "iit")
    )
    program_id_by_code: dict[str, uuid.UUID] = {
        str(code): prog_id for code, prog_id in prog_res.all()
    }

    summary = CorpusSyncSummary(
        corpus_dir=str(corpus_dir),
        total_manifest_documents=len(manifest_docs),
    )

    for item in manifest_docs:
        code = str(item["code"])
        file_name = str(item["file_name"])
        title = str(item["title"])
        source_url = str(item["source_url"])
        school = str(item.get("school") or "iit")
        doc_type = str(item["doc_type"])
        prog_code = item.get("target_program_code")
        target_program_id = program_id_by_code.get(str(prog_code)) if prog_code else None
        description = f"Page officielle IIT Sfax ({source_url})"

        md_path = corpus_dir / file_name
        if not md_path.exists():
            raise FileNotFoundError(f"Corpus file '{md_path}' referenced by manifest is missing.")
        raw_bytes = md_path.read_bytes()

        existing_res = await db.execute(select(CorpusDocument).where(CorpusDocument.code == code))
        existing_doc = existing_res.scalar_one_or_none()

        if existing_doc is None:
            outcome = await _create_initial_corpus_document(
                db,
                actor=effective_actor,
                code=code,
                title=title,
                description=description,
                school=school,
                doc_type=doc_type,
                target_program_id=target_program_id,
                file_name=file_name,
                raw_bytes=raw_bytes,
                source_url=source_url,
                storage_root=storage_root,
            )
            summary.created_new += 1
            summary.jobs_enqueued += 1
            summary.outcomes.append(outcome)
            continue

        # If document was previously soft-deleted, restore to PUBLISHED before syncing version
        if existing_doc.status == DocumentStatusEnum.DELETED.value:
            existing_doc.status = DocumentStatusEnum.PUBLISHED.value
            await db.commit()

        try:
            ver_resp = await sync_svc.create_document_version(
                db,
                document_id=existing_doc.id,
                actor=effective_actor,
                file_bytes=raw_bytes,
                filename=file_name,
                declared_mime_type="text/markdown",
                changelog=f"Updated official web scrape from {source_url}",
                activate_immediately=True,
                storage_root=storage_root,
            )
            if ver_resp.skipped or not ver_resp.created:
                summary.skipped_unchanged += 1
                summary.outcomes.append(
                    DocumentSyncOutcome(
                        code=code,
                        source_url=source_url,
                        change_type=ChangeTypeEnum.UNCHANGED.value,
                        version_number=ver_resp.version.version_number,
                        sha256=ver_resp.version.file_hash_sha256,
                        sync_job_id=None,
                    )
                )
            else:
                summary.updated_versions += 1
                if not ver_resp.sync_job_deduplicated:
                    summary.jobs_enqueued += 1
                summary.outcomes.append(
                    DocumentSyncOutcome(
                        code=code,
                        source_url=source_url,
                        change_type=ver_resp.change_type.value,
                        version_number=ver_resp.version.version_number,
                        sha256=ver_resp.version.file_hash_sha256,
                        sync_job_id=(
                            str(ver_resp.sync_job.id) if ver_resp.sync_job is not None else None
                        ),
                    )
                )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # Content matches an inactive historical version -> promote/reactivate atomically
            det = await sync_svc.detect_document_change(
                db,
                document_id=existing_doc.id,
                file_bytes=raw_bytes,
                filename=file_name,
                declared_mime_type="text/markdown",
                actor=effective_actor,
                storage_root=storage_root,
            )
            if det.matched_version_number is None:
                raise
            act_resp = await sync_svc.promote_document_version(
                db,
                document_id=existing_doc.id,
                target_version_number=det.matched_version_number,
                actor=effective_actor,
                reason=f"Re-activated matching historical scrape from {source_url}",
                commit=True,
            )
            summary.reactivated_versions += 1
            if act_resp.sync_job is not None:
                summary.jobs_enqueued += 1
            summary.outcomes.append(
                DocumentSyncOutcome(
                    code=code,
                    source_url=source_url,
                    change_type=ChangeTypeEnum.DUPLICATE_HISTORICAL_VERSION.value,
                    version_number=act_resp.active_version_number,
                    sha256=det.incoming_hash_sha256 or "",
                    sync_job_id=str(act_resp.sync_job.id) if act_resp.sync_job else None,
                )
            )

    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Idempotent SA-56 PostgreSQL sync for scraped https://iit.tn/ Markdown corpus."
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help="Path containing manifest.json and scraped .md files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON summary to stdout.",
    )
    return parser


async def _async_main() -> None:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stderr if args.json else sys.stdout)],
    )

    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as db:
            summary = await sync_iit_corpus_to_db(db, corpus_dir=args.corpus_dir)
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
    else:
        logger.info(
            "IIT Corpus Sync Complete: manifest=%d | created_new=%d | updated_versions=%d | "
            "reactivated=%d | skipped_unchanged=%d | jobs_enqueued=%d",
            summary.total_manifest_documents,
            summary.created_new,
            summary.updated_versions,
            summary.reactivated_versions,
            summary.skipped_unchanged,
            summary.jobs_enqueued,
        )


if __name__ == "__main__":
    asyncio.run(_async_main())
