"""Offline structural tests for SQLAlchemy models and Alembic migrations (SA-17)."""

import contextlib
import io
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from app.models import (
    APPOINTMENT_STATUS_VALUES,
    CANDIDATE_STATUS_VALUES,
    HUMAN_DECISION_VALUES,
    SCHOOL_CODES,
    SLOT_STATUS_VALUES,
    Base,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

EXPECTED_CORE_TABLES = {
    "core.school",
    "core.program",
    "core.program_prerequisite",
    "core.program_fee",
    "core.admission_campaign",
    "core.accounts",
    "core.roles",
    "core.account_roles",
    "core.candidate",
    "core.guest_sessions",
    "core.candidate_profile",
    "core.document",
    "core.declared_grades",
    "core.consent",
    "core.conversation_threads",
    "core.shortlist",
    "core.equivalence_requests",
    "core.outbox",
    "core.audit_log",
    "core.documents",
    "core.document_versions",
    "core.document_sync_jobs",
    "core.document_sync_logs",
    "core.slot",
    "core.appointment",
    "core.appointment_event",
    "core.rh_ticket",
}

EXPECTED_SCHOOL_TABLE_NAMES = ("dossiers", "qcm_results", "briefs", "decisions")
EXPECTED_KNOWLEDGE_TABLES = {"knowledge.chunks"}


def _all_expected_tables() -> set[str]:
    school_tables = {
        f"{school}.{table}"
        for school in SCHOOL_CODES
        for table in EXPECTED_SCHOOL_TABLE_NAMES
    }
    return EXPECTED_CORE_TABLES | EXPECTED_KNOWLEDGE_TABLES | school_tables


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def test_metadata_contains_all_40_tables_across_5_schemas():
    """Verify all 40 tables from SA-12/SA-13/SA-14/v3 exist in Base.metadata."""
    expected = _all_expected_tables()
    actual = set(Base.metadata.tables.keys())
    assert len(expected) == 40
    assert actual == expected


def test_canonical_enum_values_match_v3_specification():
    """Verify candidate, slot, appointment, and HITL decision enum values."""
    assert CANDIDATE_STATUS_VALUES == (
        "ANONYMOUS",
        "PROFILING",
        "ELIGIBLE",
        "INELIGIBLE",
        "ORIENTED",
        "AUTHENTICATED",
        "PREINSCRIBED",
        "QCM_DONE",
        "SCHEDULED",
        "INTERVIEWED",
        "ADMITTED",
        "WAITLIST",
        "REJECTED",
        "DORMANT",
        "ESCALATED",
        "WITHDRAWN",
    )
    assert SLOT_STATUS_VALUES == ("FREE", "HELD", "BOOKED")
    assert APPOINTMENT_STATUS_VALUES == (
        "AWAITING_SLOT",
        "INVITED",
        "BOOKED",
        "REMINDED",
        "ATTENDED",
        "CANCELLED",
        "NO_SHOW",
        "ESCALATED",
        "WITHDRAWN",
    )
    assert HUMAN_DECISION_VALUES == ("ADMITTED", "WAITLIST", "REJECTED")


def test_critical_cross_schema_foreign_keys_defined():
    """Verify critical cross-module FKs harmonize SA-12, SA-13, SA-14, and school schemas."""
    expected_fks = {
        "core.shortlist": {("program_id", "core.program.id")},
        "core.equivalence_requests": {
            ("target_school", "core.school.code"),
            ("target_program_id", "core.program.id"),
            ("supporting_document_id", "core.document.id"),
        },
        "core.documents": {
            ("target_program_id", "core.program.id"),
            ("current_version_id", "core.document_versions.id"),
            ("created_by", "core.accounts.id"),
        },
        "knowledge.chunks": {
            ("document_id", "core.documents.id"),
            ("version_id", "core.document_versions.id"),
            ("program_id", "core.program.id"),
        },
        "iit.decisions": {
            ("dossier_id", "iit.dossiers.id"),
            ("candidate_id", "core.candidate.id"),
            ("decided_by", "core.accounts.id"),
        },
        "isb.decisions": {
            ("dossier_id", "isb.dossiers.id"),
            ("candidate_id", "core.candidate.id"),
        },
        "isa.decisions": {
            ("dossier_id", "isa.dossiers.id"),
            ("candidate_id", "core.candidate.id"),
        },
    }

    for table_key, required_pairs in expected_fks.items():
        table = Base.metadata.tables[table_key]
        actual_pairs = {
            (fk.parent.name, fk.target_fullname)
            for fk in table.foreign_keys
        }
        assert required_pairs.issubset(actual_pairs), (
            f"Missing FKs on {table_key}: {required_pairs - actual_pairs}"
        )


def test_alembic_revision_chain_is_linear():
    """Verify Alembic history has a single linear chain ending at 20261116_0002."""
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    revisions = [rev.revision for rev in script.walk_revisions()]
    assert heads == ["20261116_0002"]
    assert revisions == ["20261116_0002", "20261116_0001"]


def test_alembic_offline_upgrade_and_downgrade_sql():
    """Render full offline PostgreSQL SQL for upgrade and downgrade without DB connection."""
    up_buf = io.StringIO()
    with contextlib.redirect_stdout(up_buf):
        command.upgrade(_alembic_config(), "head", sql=True)
    upgrade_sql = up_buf.getvalue()

    for table_key in _all_expected_tables():
        assert f"CREATE TABLE {table_key}" in upgrade_sql

    assert "CREATE TABLE core.alembic_version" in upgrade_sql
    assert "USING hnsw (embedding vector_cosine_ops)" in upgrade_sql
    assert "USING gin (content_tsv)" in upgrade_sql
    assert "uq_account_roles_scope" in upgrade_sql
    assert "ALTER TABLE core.candidate ENABLE ROW LEVEL SECURITY;" in upgrade_sql
    for school in SCHOOL_CODES:
        assert f"ALTER TABLE {school}.decisions ENABLE ROW LEVEL SECURITY;" in upgrade_sql
        assert f"CREATE POLICY responsable_{school}_write_decisions" in upgrade_sql

    down_buf = io.StringIO()
    with contextlib.redirect_stdout(down_buf):
        command.downgrade(_alembic_config(), "20261116_0002:base", sql=True)
    downgrade_sql = down_buf.getvalue()

    for table_key in _all_expected_tables():
        assert f"DROP TABLE {table_key}" in downgrade_sql
    assert "DROP TYPE IF EXISTS core.human_decision" in downgrade_sql
