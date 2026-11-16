"""Live PostgreSQL 16 verification for schemas, 40 tables, FKs, constraints, and RLS."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import engine  # noqa: E402
from app.models import Base  # noqa: E402


async def _verify_catalog_objects() -> None:
    expected_tables = set(Base.metadata.tables.keys())

    async with engine.connect() as conn:
        schemas = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name IN ('core', 'iit', 'isb', 'isa', 'knowledge')"
                    )
                )
            ).fetchall()
        }
        assert schemas == {"core", "iit", "isb", "isa", "knowledge"}, f"Schemas mismatch: {schemas}"

        ext_rows = (
            await conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
        ).fetchall()
        assert len(ext_rows) == 1, "pgvector extension missing"

        actual_tables = {
            f"{row[0]}.{row[1]}"
            for row in (
                await conn.execute(
                    text(
                        "SELECT table_schema, table_name FROM information_schema.tables "
                        "WHERE table_schema IN ('core', 'iit', 'isb', 'isa', 'knowledge') "
                        "AND table_type = 'BASE TABLE' "
                        "AND table_name != 'alembic_version'"
                    )
                )
            ).fetchall()
        }
        assert actual_tables == expected_tables, (
            f"Table mismatch! Missing={expected_tables - actual_tables}, "
            f"Extra={actual_tables - expected_tables}"
        )

        fk_count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM pg_constraint "
                    "WHERE contype = 'f' "
                    "AND connamespace::regnamespace::text IN "
                    "('core', 'iit', 'isb', 'isa', 'knowledge')"
                )
            )
        ).scalar_one()
        assert fk_count >= 45, f"Expected >= 45 foreign keys, found {fk_count}"

        hnsw_exists = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE schemaname = 'knowledge' "
                    "AND indexname = 'idx_knowledge_chunks_embedding_hnsw'"
                )
            )
        ).fetchone()
        assert hnsw_exists is not None, "HNSW pgvector index missing"

        rls_tables = {
            f"{row[0]}.{row[1]}"
            for row in (
                await conn.execute(
                    text(
                        "SELECT n.nspname, c.relname "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relrowsecurity = TRUE "
                        "AND n.nspname IN ('core', 'iit', 'isb', 'isa')"
                    )
                )
            ).fetchall()
        }
        for required_rls in (
            "core.candidate",
            "core.slot",
            "core.rh_ticket",
            "iit.dossiers",
            "iit.decisions",
            "isb.decisions",
            "isa.decisions",
        ):
            assert required_rls in rls_tables, f"RLS not enabled on {required_rls}"

    print(
        f"[OK] Verified 5 schemas, pgvector, {len(actual_tables)} tables, "
        f"{fk_count} FKs, HNSW, RLS"
    )


async def _verify_constraints_and_rls_behavior() -> None:
    """Run transactional constraint + RLS boundary checks and roll everything back cleanly."""
    conn = await engine.connect()
    trans = await conn.begin()
    try:
        await conn.execute(
            text(
                "INSERT INTO core.school (code, name, is_extension, is_active) VALUES "
                "('iit', 'IIT', FALSE, TRUE), ('isb', 'ISB', FALSE, TRUE) "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
        prog_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO core.program (id, code, school_code, title, degree_level, "
                "degree_title, duration_years, source_document) VALUES "
                "(:pid, 'TEST-IIT-GL', 'iit', 'Test GL', 'cycle_ingenieur', "
                "'Ingenieur', 3, 'test.pdf')"
            ),
            {"pid": prog_id},
        )
        acc_student = str(uuid.uuid4())
        acc_resp = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO core.accounts (id, email, full_name, auth_provider) VALUES "
                "(:s_id, :s_mail, 'Student Test', 'EMAIL_OTP'), "
                "(:r_id, :r_mail, 'Responsable IIT', 'SSO')"
            ),
            {
                "s_id": acc_student,
                "s_mail": f"s-{uuid.uuid4().hex[:8]}@example.tn",
                "r_id": acc_resp,
                "r_mail": f"r-{uuid.uuid4().hex[:8]}@example.tn",
            },
        )
        cand_1 = str(uuid.uuid4())
        cand_2 = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO core.candidate "
                "(id, account_id, candidate_number, email, school_target) VALUES "
                "(:c1, :acc1, :n1, 'c1@example.tn', 'iit'), "
                "(:c2, NULL, :n2, 'c2@example.tn', 'isb')"
            ),
            {
                "c1": cand_1,
                "acc1": acc_student,
                "n1": f"NAU-T1-{uuid.uuid4().hex[:6]}",
                "c2": cand_2,
                "n2": f"NAU-T2-{uuid.uuid4().hex[:6]}",
            },
        )
        doc_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO core.document (id, candidate_id, type, uri) "
                "VALUES (:did, :c1, 'RELEVE_BAC', 's3://bucket/test.pdf')"
            ),
            {"did": doc_id, "c1": cand_1},
        )
        dossier_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO iit.dossiers "
                "(id, dossier_number, candidate_id, program_id, document_id) "
                "VALUES (:dos, :dnum, :c1, :pid, :did)"
            ),
            {
                "dos": dossier_id,
                "dnum": f"DOS-{uuid.uuid4().hex[:8]}",
                "c1": cand_1,
                "pid": prog_id,
                "did": doc_id,
            },
        )

        # Check no double-booking slot constraint & max 2 reschedules CHECK
        start_t = datetime.now(UTC) + timedelta(days=1)
        end_t = start_t + timedelta(minutes=30)
        slot_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO core.slot (id, responsable_id, school_code, start_time, end_time) "
                "VALUES (:sid, :rid, 'iit', :st, :et)"
            ),
            {"sid": slot_id, "rid": acc_resp, "st": start_t, "et": end_t},
        )
        sp = await conn.begin_nested()
        try:
            await conn.execute(
                text(
                    "INSERT INTO core.slot (responsable_id, school_code, start_time, end_time) "
                    "VALUES (:rid, 'iit', :st, :et)"
                ),
                {"rid": acc_resp, "st": start_t, "et": end_t},
            )
            raise AssertionError("Duplicate responsable/start_time slot should have failed")
        except IntegrityError:
            await sp.rollback()

        sp2 = await conn.begin_nested()
        try:
            await conn.execute(
                text(
                    "INSERT INTO core.appointment "
                    "(candidate_id, slot_id, school_code, reschedules) "
                    "VALUES (:c1, :sid, 'iit', 3)"
                ),
                {"c1": cand_1, "sid": slot_id},
            )
            raise AssertionError(
                "reschedules = 3 should violate CHECK (reschedules BETWEEN 0 AND 2)"
            )
        except IntegrityError:
            await sp2.rollback()

        # Check RLS: student isolation & HITL lock on iit.decisions
        sp3 = await conn.begin_nested()
        await conn.execute(text("SET LOCAL ROLE authenticated_student"))
        await conn.execute(
            text("SELECT set_config('request.jwt.claim.candidate_id', :cid, true)"),
            {"cid": cand_1},
        )
        visible = (await conn.execute(text("SELECT id FROM core.candidate"))).scalars().all()
        assert [str(v) for v in visible] == [cand_1], (
            f"Student saw unexpected candidates: {visible}"
        )

        try:
            await conn.execute(
                text(
                    "INSERT INTO iit.decisions (dossier_id, candidate_id, decision, decided_by) "
                    "VALUES (:dos, :c1, 'ADMITTED', :rid)"
                ),
                {"dos": dossier_id, "c1": cand_1, "rid": acc_resp},
            )
            raise AssertionError("Student must NEVER be allowed to write iit.decisions")
        except DBAPIError:
            await sp3.rollback()

        # Check RLS: Human RH cannot write decisions
        sp4 = await conn.begin_nested()
        await conn.execute(text("SET LOCAL ROLE human_rh"))
        try:
            await conn.execute(
                text(
                    "INSERT INTO iit.decisions (dossier_id, candidate_id, decision, decided_by) "
                    "VALUES (:dos, :c1, 'ADMITTED', :rid)"
                ),
                {"dos": dossier_id, "c1": cand_1, "rid": acc_resp},
            )
            raise AssertionError("Human RH must NEVER be allowed to write iit.decisions")
        except DBAPIError:
            await sp4.rollback()

        # Check RLS: ISB Responsable cannot write IIT decision; IIT Responsable can
        sp5 = await conn.begin_nested()
        await conn.execute(text("SET LOCAL ROLE nau_responsable"))
        await conn.execute(text("SELECT set_config('request.jwt.claim.school', 'isb', true)"))
        await conn.execute(text("SELECT set_config('request.jwt.claim.role', 'responsable', true)"))
        try:
            await conn.execute(
                text(
                    "INSERT INTO iit.decisions (dossier_id, candidate_id, decision, decided_by) "
                    "VALUES (:dos, :c1, 'ADMITTED', :rid)"
                ),
                {"dos": dossier_id, "c1": cand_1, "rid": acc_resp},
            )
            raise AssertionError("ISB Responsable must NOT write into iit.decisions")
        except DBAPIError:
            await sp5.rollback()

        sp6 = await conn.begin_nested()
        await conn.execute(text("SET LOCAL ROLE nau_responsable"))
        await conn.execute(text("SELECT set_config('request.jwt.claim.school', 'iit', true)"))
        await conn.execute(text("SELECT set_config('request.jwt.claim.role', 'responsable', true)"))
        await conn.execute(
            text(
                "INSERT INTO iit.decisions (dossier_id, candidate_id, decision, decided_by) "
                "VALUES (:dos, :c1, 'ADMITTED', :rid)"
            ),
            {"dos": dossier_id, "c1": cand_1, "rid": acc_resp},
        )
        await sp6.rollback()

        print(
            "[OK] Verified DB constraints (slot uniqueness, max reschedules) "
            "and RLS/HITL isolation"
        )
    finally:
        await trans.rollback()
        await conn.close()


async def main() -> None:
    try:
        await _verify_catalog_objects()
        await _verify_constraints_and_rls_behavior()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
