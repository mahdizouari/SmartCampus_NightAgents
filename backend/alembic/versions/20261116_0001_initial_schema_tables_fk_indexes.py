"""Create initial multi-schema PostgreSQL 16 tables, FKs, constraints, and indexes.

Revision ID: 20261116_0001
Revises:
Create Date: 2026-11-16 22:35:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.base import (
    APPOINTMENT_STATUS_VALUES,
    CANDIDATE_STATUS_VALUES,
    HUMAN_DECISION_VALUES,
    SCHOOL_CODES,
    SLOT_STATUS_VALUES,
)

revision: str = "20261116_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Historical vector dimension for revision 0001; migration 20261127_0003 migrates to 1024 (bge-m3).
INITIAL_EMBEDDING_DIMENSION = 1536


def _ensure_schemas_and_extensions() -> None:
    """Ensure required schemas/extensions exist when run as superuser on a blank PG instance."""
    op.execute(
        """
        DO $$
        BEGIN
            IF has_database_privilege(current_user, current_database(), 'CREATE') THEN
                CREATE SCHEMA IF NOT EXISTS core;
                CREATE SCHEMA IF NOT EXISTS iit;
                CREATE SCHEMA IF NOT EXISTS isb;
                CREATE SCHEMA IF NOT EXISTS isa;
                CREATE SCHEMA IF NOT EXISTS knowledge;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper) THEN
                CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
                CREATE EXTENSION IF NOT EXISTS "vector";
            END IF;
        END
        $$
        """
    )


def _create_enum_types() -> None:
    enums = (
        ("candidate_status", CANDIDATE_STATUS_VALUES),
        ("slot_status", SLOT_STATUS_VALUES),
        ("appointment_status", APPOINTMENT_STATUS_VALUES),
        ("human_decision", HUMAN_DECISION_VALUES),
    )
    for enum_name, values in enums:
        values_sql = ", ".join(f"'{value}'" for value in values)
        op.execute(
            f"""
            DO $$
            BEGIN
                CREATE TYPE core.{enum_name} AS ENUM ({values_sql});
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END
            $$
            """
        )


def _create_catalog_tables() -> None:
    op.create_table(
        "school",
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "is_extension",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("code IN ('iit', 'isb', 'isa')", name="school_code_valid"),
        sa.PrimaryKeyConstraint("code", name="pk_school"),
        schema="core",
    )

    op.create_table(
        "program",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("school_code", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("degree_level", sa.String(32), nullable=False),
        sa.Column("degree_title", sa.String(150), nullable=False),
        sa.Column("duration_years", sa.SmallInteger(), nullable=False),
        sa.Column(
            "study_modes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source_document", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "degree_level IN ('licence', 'prepa', 'cycle_ingenieur', 'master')",
            name="program_degree_level_valid",
        ),
        sa.CheckConstraint("duration_years BETWEEN 1 AND 6", name="program_duration_range"),
        sa.ForeignKeyConstraint(
            ["school_code"],
            ["core.school.code"],
            name="fk_program_school_code_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_program"),
        sa.UniqueConstraint("code", name="uq_program_code"),
        schema="core",
    )
    op.create_index(
        "idx_program_school_level",
        "program",
        ["school_code", "degree_level"],
        schema="core",
        postgresql_where=sa.text("is_active = TRUE"),
    )

    op.create_table(
        "program_prerequisite",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_track", sa.String(32), nullable=False),
        sa.Column("required_diploma", sa.String(150), nullable=False),
        sa.Column("min_average", sa.Numeric(4, 2), nullable=True),
        sa.Column("yaml_rule_key", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source_document", sa.String(255), nullable=False),
        sa.CheckConstraint(
            "entry_track IN ('bac', 'licence', 'prepa', 'passerelle')",
            name="prereq_entry_track_valid",
        ),
        sa.CheckConstraint(
            "min_average IS NULL OR (min_average >= 0 AND min_average <= 20)",
            name="prereq_min_average_range",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_program_prerequisite_program_id_program",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_program_prerequisite"),
        schema="core",
    )
    op.create_index(
        "idx_prereq_program_track",
        "program_prerequisite",
        ["program_id", "entry_track"],
        schema="core",
    )

    op.create_table(
        "program_fee",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academic_year", sa.String(16), nullable=False),
        sa.Column("tuition_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'TND'")),
        sa.Column("payment_schedule", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_document", sa.String(255), nullable=False),
        sa.CheckConstraint("tuition_amount >= 0", name="fee_tuition_non_negative"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="fee_effective_dates_valid",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_program_fee_program_id_program",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_program_fee"),
        sa.UniqueConstraint(
            "program_id",
            "academic_year",
            "effective_from",
            name="uq_program_fee_year_start",
        ),
        schema="core",
    )
    op.create_index(
        "idx_fee_program_year",
        "program_fee",
        ["program_id", "academic_year"],
        schema="core",
    )

    op.create_table(
        "admission_campaign",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("school_code", sa.String(16), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("academic_year", sa.String(16), nullable=False),
        sa.Column("phase_name", sa.String(100), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("source_document", sa.String(255), nullable=False),
        sa.CheckConstraint("closes_at > opens_at", name="campaign_window_valid"),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_admission_campaign_program_id_program",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["school_code"],
            ["core.school.code"],
            name="fk_admission_campaign_school_code_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admission_campaign"),
        schema="core",
    )
    op.create_index(
        "idx_campaign_school_open",
        "admission_campaign",
        ["school_code", "is_open", "closes_at"],
        schema="core",
    )


def _create_user_and_candidate_tables() -> None:
    candidate_status_col = postgresql.ENUM(
        *CANDIDATE_STATUS_VALUES,
        name="candidate_status",
        schema="core",
        create_type=False,
    )

    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("auth_provider", sa.String(32), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=True),
        sa.Column("department", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "auth_provider IN ('EMAIL_OTP', 'PHONE_OTP', 'GOOGLE_OAUTH', 'SSO')",
            name="accounts_auth_provider_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
        schema="core",
    )
    op.create_index(
        "uq_accounts_provider_subject",
        "accounts",
        ["auth_provider", "provider_subject"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("provider_subject IS NOT NULL"),
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.SmallInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "code IN ('student', 'rh', 'responsable', 'direction', 'admin')",
            name="roles_code_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
        schema="core",
    )

    op.create_table(
        "account_roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", sa.SmallInteger(), nullable=False),
        sa.Column("school_scope", sa.String(16), nullable=True),
        sa.Column(
            "responsibility_scope",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'ALL'"),
        ),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "school_scope IS NULL OR school_scope IN ('iit', 'isb', 'isa')",
            name="account_roles_school_scope_valid",
        ),
        sa.CheckConstraint(
            "responsibility_scope IN ('ALL', 'ACADEMIC', 'INTERVIEW')",
            name="account_roles_responsibility_valid",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["core.accounts.id"],
            name="fk_account_roles_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["core.accounts.id"],
            name="fk_account_roles_assigned_by_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["core.roles.id"],
            name="fk_account_roles_role_id_roles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["school_scope"],
            ["core.school.code"],
            name="fk_account_roles_school_scope_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_roles"),
        schema="core",
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_account_roles_scope "
        "ON core.account_roles (account_id, role_id, COALESCE(school_scope, 'GLOBAL'))"
    )

    op.create_table(
        "candidate",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_number", sa.String(32), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            candidate_status_col,
            nullable=False,
            server_default=sa.text("'AUTHENTICATED'"),
        ),
        sa.Column("school_target", sa.String(16), nullable=True),
        sa.Column("owner", sa.String(64), nullable=False, server_default=sa.text("'JARVIS'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "school_target IS NULL OR school_target IN ('iit', 'isb', 'isa')",
            name="candidate_school_target_valid",
        ),
        sa.CheckConstraint(
            "owner = 'JARVIS' OR owner LIKE 'RH:%'",
            name="candidate_one_owner_valid",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["core.accounts.id"],
            name="fk_candidate_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["school_target"],
            ["core.school.code"],
            name="fk_candidate_school_target_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate"),
        sa.UniqueConstraint("account_id", name="uq_candidate_account_id"),
        sa.UniqueConstraint("candidate_number", name="uq_candidate_candidate_number"),
        schema="core",
    )
    op.create_index(
        "idx_candidate_status_school",
        "candidate",
        ["status", "school_target"],
        schema="core",
    )
    op.create_index(
        "idx_candidate_email",
        "candidate",
        [sa.text("LOWER(email)")],
        schema="core",
    )

    op.create_table(
        "guest_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("preferred_language", sa.String(24), nullable=True),
        sa.Column("merged_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'MERGED', 'EXPIRED')",
            name="guest_sessions_status_valid",
        ),
        sa.CheckConstraint(
            "preferred_language IS NULL OR preferred_language IN ('FR', 'DERJA', 'ARABIZI')",
            name="guest_sessions_language_valid",
        ),
        sa.ForeignKeyConstraint(
            ["merged_candidate_id"],
            ["core.candidate.id"],
            name="fk_guest_sessions_merged_candidate_id_candidate",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guest_sessions"),
        schema="core",
    )

    op.create_table(
        "candidate_profile",
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "facts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("current_education_level", sa.String(64), nullable=True),
        sa.Column("current_specialty", sa.String(128), nullable=True),
        sa.Column("declared_overall_avg", sa.Numeric(5, 2), nullable=True),
        sa.Column("preferred_language", sa.String(24), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "declared_overall_avg IS NULL "
            "OR (declared_overall_avg >= 0 AND declared_overall_avg <= 20)",
            name="profile_declared_avg_range",
        ),
        sa.CheckConstraint(
            "preferred_language IS NULL OR preferred_language IN ('FR', 'DERJA', 'ARABIZI')",
            name="profile_language_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_candidate_profile_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_id", name="pk_candidate_profile"),
        schema="core",
    )

    op.create_table(
        "document",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("ocr_json", postgresql.JSONB(), nullable=True),
        sa.Column("min_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "type IN ("
            "'RELEVE_BAC', 'RELEVE_UNIVERSITAIRE', 'DIPLOME', 'ATTESTATION_EQUIVALENCE', 'AUTRE'"
            ")",
            name="candidate_document_type_valid",
        ),
        sa.CheckConstraint(
            "min_confidence IS NULL OR (min_confidence >= 0 AND min_confidence <= 1)",
            name="candidate_document_confidence_range",
        ),
        sa.CheckConstraint(
            "verification_status IN ('PENDING', 'CONFIRMED_BY_STUDENT', 'MISMATCH_RH', 'VERIFIED')",
            name="candidate_document_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_document_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document"),
        schema="core",
    )
    op.create_index(
        "idx_document_candidate_status",
        "document",
        ["candidate_id", "verification_status"],
        schema="core",
    )

    op.create_table(
        "declared_grades",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academic_year", sa.String(16), nullable=True),
        sa.Column("diploma_stage", sa.String(32), nullable=False),
        sa.Column("subject_name", sa.String(128), nullable=False),
        sa.Column("grade_value", sa.Numeric(5, 2), nullable=False),
        sa.Column("grade_scale", sa.Numeric(5, 2), nullable=False, server_default=sa.text("20.00")),
        sa.Column("coefficient", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "entry_source",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'DECLARED_CHAT'"),
        ),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "diploma_stage IN ('BAC', 'L1', 'L2', 'L3', 'PREPA_1', 'PREPA_2', 'M1', 'OTHER')",
            name="grades_diploma_stage_valid",
        ),
        sa.CheckConstraint("grade_scale > 0", name="grades_scale_positive"),
        sa.CheckConstraint(
            "grade_value >= 0 AND grade_value <= grade_scale",
            name="grades_value_range",
        ),
        sa.CheckConstraint(
            "coefficient IS NULL OR coefficient > 0",
            name="grades_coeff_positive",
        ),
        sa.CheckConstraint(
            "entry_source IN ('DECLARED_CHAT', 'CANDIDATE_FORM', 'VLM_CONFIRMED', 'RH_CORRECTED')",
            name="grades_entry_source_valid",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="grades_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_declared_grades_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["core.document.id"],
            name="fk_declared_grades_source_document_id_document",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_declared_grades"),
        schema="core",
    )
    op.create_index(
        "idx_declared_grades_candidate",
        "declared_grades",
        ["candidate_id", "diploma_stage"],
        schema="core",
    )

    op.create_table(
        "consent",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column(
            "given_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('ORIENTATION_PROFILE', 'DOCUMENT_OCR', 'ADMISSION_FOLLOWUP')",
            name="consent_purpose_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_consent_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consent"),
        schema="core",
    )
    op.create_index(
        "idx_consent_candidate_purpose",
        "consent",
        ["candidate_id", "purpose", sa.text("given_at DESC")],
        schema="core",
    )

    op.create_table(
        "conversation_threads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("langgraph_thread_id", sa.String(128), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_detected_dialect", sa.String(24), nullable=True),
        sa.Column("cold_archive_uri", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "candidate_id IS NOT NULL OR guest_session_id IS NOT NULL",
            name="conversation_threads_owner_present",
        ),
        sa.CheckConstraint(
            "turn_count >= 0",
            name="conversation_threads_turns_non_negative",
        ),
        sa.CheckConstraint(
            "last_detected_dialect IS NULL OR last_detected_dialect IN ('FR', 'DERJA', 'ARABIZI')",
            name="conversation_threads_dialect_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_conversation_threads_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["guest_session_id"],
            ["core.guest_sessions.id"],
            name="fk_conversation_threads_guest_session_id_guest_sessions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_threads"),
        sa.UniqueConstraint(
            "langgraph_thread_id",
            name="uq_conversation_threads_langgraph_thread_id",
        ),
        schema="core",
    )

    op.create_table(
        "shortlist",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("rank BETWEEN 1 AND 5", name="shortlist_rank_range"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="shortlist_score_range"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_shortlist_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_shortlist_program_id_program",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shortlist"),
        sa.UniqueConstraint("candidate_id", "program_id", name="uq_shortlist_candidate_program"),
        schema="core",
    )

    op.create_table(
        "equivalence_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_diploma", sa.String(128), nullable=False),
        sa.Column("source_institution", sa.String(192), nullable=True),
        sa.Column("target_school", sa.String(16), nullable=False),
        sa.Column("target_program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("yaml_rule_key", sa.String(100), nullable=True),
        sa.Column("gatekeeper_verdict", sa.String(32), nullable=False),
        sa.Column(
            "review_status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'PENDING_REVIEW'"),
        ),
        sa.Column("supporting_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "target_school IN ('iit', 'isb', 'isa')",
            name="equiv_target_school_valid",
        ),
        sa.CheckConstraint(
            "gatekeeper_verdict IN ('ELIGIBLE', 'BORDERLINE_TO_CONFIRM', 'INELIGIBLE')",
            name="equiv_verdict_valid",
        ),
        sa.CheckConstraint(
            "review_status IN ("
            "'AUTO_VALIDATED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'NEEDS_MORE_INFO'"
            ")",
            name="equiv_review_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_equivalence_requests_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["core.accounts.id"],
            name="fk_equivalence_requests_reviewed_by_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supporting_document_id"],
            ["core.document.id"],
            name="fk_equivalence_requests_supporting_document_id_document",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_program_id"],
            ["core.program.id"],
            name="fk_equivalence_requests_target_program_id_program",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_school"],
            ["core.school.code"],
            name="fk_equivalence_requests_target_school_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_equivalence_requests"),
        schema="core",
    )

    op.create_table(
        "outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("retry_count >= 0", name="outbox_retry_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_outbox"),
        schema="core",
    )
    op.create_index(
        "idx_outbox_unprocessed",
        "outbox",
        ["created_at"],
        schema="core",
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(96), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "actor_type IN ("
            "'STUDENT', 'RH', 'RESPONSABLE', 'DIRECTION', 'ADMIN', 'VISION', 'JARVIS', 'SYSTEM'"
            ")",
            name="audit_actor_type_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        schema="core",
    )
    op.create_index(
        "idx_audit_log_entity",
        "audit_log",
        ["entity_type", "entity_id", "created_at"],
        schema="core",
    )


def _create_documents_and_knowledge_tables() -> None:
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("school", sa.String(20), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("target_program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "school IN ('iit', 'isb', 'isa', 'nau_common')",
            name="documents_school_valid",
        ),
        sa.CheckConstraint(
            "category IN ("
            "'admission_rules', 'curriculum', 'fees_schedule', 'equivalence_grid', "
            "'general_brochure', 'brochure', 'reglement', 'faq', 'programme'"
            ")",
            name="documents_category_valid",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'DELETED')",
            name="documents_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["core.accounts.id"],
            name="fk_documents_created_by_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_program_id"],
            ["core.program.id"],
            name="fk_documents_target_program_id_program",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("code", name="uq_documents_code"),
        schema="core",
    )
    op.create_index(
        "idx_documents_school_category",
        "documents",
        ["school", "category"],
        schema="core",
    )
    op.create_index("idx_documents_status", "documents", ["status"], schema="core")

    op.create_table(
        "document_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_path_storage", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_hash_sha256", sa.CHAR(64), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("version_number > 0", name="doc_versions_number_positive"),
        sa.CheckConstraint(
            "mime_type IN ("
            "'application/pdf', "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document', "
            "'text/markdown', "
            "'text/plain'"
            ")",
            name="doc_versions_mime_valid",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="doc_versions_size_positive"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["core.documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["core.accounts.id"],
            name="fk_document_versions_uploaded_by_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.UniqueConstraint("document_id", "file_hash_sha256", name="uq_document_file_hash"),
        schema="core",
    )
    op.create_index(
        "idx_document_versions_active",
        "document_versions",
        ["document_id", "is_active"],
        schema="core",
    )

    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
        ondelete="SET NULL",
    )

    op.create_table(
        "document_sync_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column(
            "pipeline_stage",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PARSING'"),
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column(
            "embedding_model",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'text-embedding-3-small'"),
        ),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default=sa.text("512")),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default=sa.text("64")),
        sa.Column("total_pages", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("total_chunks", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "action_type IN ('INGEST', 'REINDEX', 'DEACTIVATE', 'PURGE')",
            name="sync_jobs_action_valid",
        ),
        sa.CheckConstraint(
            "pipeline_stage IN ("
            "'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'COMPLETED', 'FAILED'"
            ")",
            name="sync_jobs_stage_valid",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SYNCED', 'FAILED', 'CANCELLED')",
            name="sync_jobs_status_valid",
        ),
        sa.CheckConstraint("chunk_size > 0", name="sync_jobs_chunk_size_positive"),
        sa.CheckConstraint("chunk_overlap >= 0", name="sync_jobs_overlap_non_negative"),
        sa.CheckConstraint("retry_count >= 0", name="sync_jobs_retry_non_negative"),
        sa.CheckConstraint("max_retries > 0", name="sync_jobs_max_retries_positive"),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["core.document_versions.id"],
            name="fk_document_sync_jobs_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_sync_jobs"),
        schema="core",
    )
    op.create_index("idx_sync_jobs_status", "document_sync_jobs", ["status"], schema="core")
    op.create_index("idx_sync_jobs_version", "document_sync_jobs", ["version_id"], schema="core")

    op.create_table(
        "document_sync_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')",
            name="sync_logs_level_valid",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["core.document_sync_jobs.id"],
            name="fk_document_sync_logs_job_id_document_sync_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_sync_logs"),
        schema="core",
    )
    op.create_index(
        "idx_sync_logs_job",
        "document_sync_logs",
        ["job_id", "created_at"],
        schema="core",
    )

    op.create_table(
        "chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section_ref", sa.String(100), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('french', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(INITIAL_EMBEDDING_DIMENSION), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("chunk_index >= 0", name="knowledge_chunks_index_non_negative"),
        sa.CheckConstraint("token_count > 0", name="knowledge_chunks_tokens_positive"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["core.documents.id"],
            name="fk_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_chunks_program_id_program",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["core.document_versions.id"],
            name="fk_chunks_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint("version_id", "chunk_index", name="uq_knowledge_chunks_version_idx"),
        schema="knowledge",
    )
    op.create_index(
        "idx_knowledge_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        schema="knowledge",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "idx_knowledge_chunks_tsv",
        "chunks",
        ["content_tsv"],
        schema="knowledge",
        postgresql_using="gin",
    )
    op.create_index(
        "idx_knowledge_chunks_active_doc",
        "chunks",
        ["is_active", "document_id"],
        schema="knowledge",
    )
    op.create_index(
        "idx_knowledge_chunks_program",
        "chunks",
        ["program_id"],
        schema="knowledge",
        postgresql_where=sa.text("program_id IS NOT NULL"),
    )
    op.create_index(
        "idx_knowledge_chunks_metadata_gin",
        "chunks",
        ["metadata"],
        schema="knowledge",
        postgresql_using="gin",
    )


def _create_scheduling_and_rh_tables() -> None:
    slot_status_col = postgresql.ENUM(
        *SLOT_STATUS_VALUES,
        name="slot_status",
        schema="core",
        create_type=False,
    )
    appointment_status_col = postgresql.ENUM(
        *APPOINTMENT_STATUS_VALUES,
        name="appointment_status",
        schema="core",
        create_type=False,
    )

    op.create_table(
        "slot",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("responsable_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_code", sa.String(16), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("room_or_link", sa.String(255), nullable=True),
        sa.Column("status", slot_status_col, nullable=False, server_default=sa.text("'FREE'")),
        sa.Column("held_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_by_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("end_time > start_time", name="slot_time_window_valid"),
        sa.ForeignKeyConstraint(
            ["held_by_candidate_id"],
            ["core.candidate.id"],
            name="fk_slot_held_by_candidate_id_candidate",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["responsable_id"],
            ["core.accounts.id"],
            name="fk_slot_responsable_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["school_code"],
            ["core.school.code"],
            name="fk_slot_school_code_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_slot"),
        sa.UniqueConstraint("responsable_id", "start_time", name="uq_slot_responsable_start"),
        schema="core",
    )
    op.create_index(
        "idx_slot_school_status_start",
        "slot",
        ["school_code", "status", "start_time"],
        schema="core",
    )

    op.create_table(
        "appointment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("school_code", sa.String(16), nullable=False),
        sa.Column(
            "state",
            appointment_status_col,
            nullable=False,
            server_default=sa.text("'AWAITING_SLOT'"),
        ),
        sa.Column("reschedules", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("reschedules BETWEEN 0 AND 2", name="appointment_max_reschedules"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_appointment_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["school_code"],
            ["core.school.code"],
            name="fk_appointment_school_code_school",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            ["core.slot.id"],
            name="fk_appointment_slot_id_slot",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment"),
        schema="core",
    )
    op.create_index(
        "uq_appointment_active_slot",
        "appointment",
        ["slot_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text(
            "slot_id IS NOT NULL AND state IN ('INVITED', 'BOOKED', 'REMINDED')"
        ),
    )
    op.create_index(
        "idx_appointment_candidate_state",
        "appointment",
        ["candidate_id", "state"],
        schema="core",
    )

    op.create_table(
        "appointment_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("from_state", appointment_status_col, nullable=True),
        sa.Column("to_state", appointment_status_col, nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["core.appointment.id"],
            name="fk_appointment_event_appointment_id_appointment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_event"),
        sa.UniqueConstraint("idempotency_key", name="uq_appointment_event_idempotency_key"),
        schema="core",
    )
    op.create_index(
        "idx_appointment_event_appt",
        "appointment_event",
        ["appointment_id", "created_at"],
        schema="core",
    )

    op.create_table(
        "rh_ticket",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_code", sa.String(16), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'NORMAL'")),
        sa.Column(
            "owner",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'RH:unassigned'"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="rh_ticket_priority_valid",
        ),
        sa.CheckConstraint(
            "owner = 'JARVIS' OR owner LIKE 'RH:%'",
            name="rh_ticket_one_owner_valid",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CLAIMED', 'RESOLVED', 'CLOSED')",
            name="rh_ticket_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_rh_ticket_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["core.accounts.id"],
            name="fk_rh_ticket_resolved_by_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["school_code"],
            ["core.school.code"],
            name="fk_rh_ticket_school_code_school",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rh_ticket"),
        schema="core",
    )
    op.create_index(
        "idx_rh_ticket_status_owner",
        "rh_ticket",
        ["status", "owner"],
        schema="core",
    )
    op.create_index("idx_rh_ticket_candidate", "rh_ticket", ["candidate_id"], schema="core")


def _create_school_tables(schema_name: str) -> None:
    human_decision_col = postgresql.ENUM(
        *HUMAN_DECISION_VALUES,
        name="human_decision",
        schema="core",
        create_type=False,
    )
    dossier_ref = f"{schema_name}.dossiers.id"

    op.create_table(
        "dossiers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dossier_number", sa.String(32), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "confirmed_profile",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'PREINSCRIBED'"),
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PREINSCRIBED', 'QCM_DONE', 'SCHEDULED', 'INTERVIEWED', 'DECIDED', 'WITHDRAWN'"
            ")",
            name=f"{schema_name}_dossier_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_dossiers_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["core.document.id"],
            name="fk_dossiers_document_id_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["core.program.id"],
            name="fk_dossiers_program_id_program",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dossiers"),
        sa.UniqueConstraint("candidate_id", name="uq_dossiers_candidate_id"),
        sa.UniqueConstraint("dossier_number", name="uq_dossiers_dossier_number"),
        schema=schema_name,
    )
    op.create_index(
        f"idx_{schema_name}_dossiers_status",
        "dossiers",
        ["status"],
        schema=schema_name,
    )

    op.create_table(
        "qcm_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_questions", sa.SmallInteger(), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name=f"{schema_name}_qcm_score_range",
        ),
        sa.CheckConstraint(
            "total_questions BETWEEN 1 AND 50",
            name=f"{schema_name}_qcm_questions_range",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_qcm_results_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dossier_id"],
            [dossier_ref],
            name="fk_qcm_results_dossier_id_dossiers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_qcm_results"),
        schema=schema_name,
    )
    op.create_index(
        f"idx_{schema_name}_qcm_candidate",
        "qcm_results",
        ["candidate_id"],
        schema=schema_name,
    )

    op.create_table(
        "briefs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_markdown", sa.Text(), nullable=False),
        sa.Column("brief_pdf_uri", sa.Text(), nullable=True),
        sa.Column(
            "academic_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("compatibility_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "generated_by",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'VISION_1.6'"),
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "compatibility_score IS NULL "
            "OR (compatibility_score >= 0 AND compatibility_score <= 100)",
            name=f"{schema_name}_brief_compat_range",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_briefs_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dossier_id"],
            [dossier_ref],
            name="fk_briefs_dossier_id_dossiers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_briefs"),
        sa.UniqueConstraint("dossier_id", name="uq_briefs_dossier_id"),
        schema=schema_name,
    )

    op.create_table(
        "decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", human_decision_col, nullable=False),
        sa.Column("assigned_program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("interviewer_notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signature_ref", sa.String(128), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_program_id"],
            ["core.program.id"],
            name="fk_decisions_assigned_program_id_program",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["core.candidate.id"],
            name="fk_decisions_candidate_id_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["core.accounts.id"],
            name="fk_decisions_decided_by_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dossier_id"],
            [dossier_ref],
            name="fk_decisions_dossier_id_dossiers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decisions"),
        sa.UniqueConstraint("dossier_id", name="uq_decisions_dossier_id"),
        schema=schema_name,
    )


def upgrade() -> None:
    """Create all schemas/enums/tables/FKs/constraints/indexes across the 5 DB schemas."""
    _ensure_schemas_and_extensions()
    _create_enum_types()
    _create_catalog_tables()
    _create_user_and_candidate_tables()
    _create_documents_and_knowledge_tables()
    _create_scheduling_and_rh_tables()
    for school in SCHOOL_CODES:
        _create_school_tables(school)


def downgrade() -> None:
    """Drop all SmartCampus tables and enum types in strict reverse dependency order."""
    for school in reversed(SCHOOL_CODES):
        op.drop_table("decisions", schema=school)
        op.drop_table("briefs", schema=school)
        op.drop_index(f"idx_{school}_qcm_candidate", table_name="qcm_results", schema=school)
        op.drop_table("qcm_results", schema=school)
        op.drop_index(f"idx_{school}_dossiers_status", table_name="dossiers", schema=school)
        op.drop_table("dossiers", schema=school)

    op.drop_index("idx_rh_ticket_candidate", table_name="rh_ticket", schema="core")
    op.drop_index("idx_rh_ticket_status_owner", table_name="rh_ticket", schema="core")
    op.drop_table("rh_ticket", schema="core")
    op.drop_index("idx_appointment_event_appt", table_name="appointment_event", schema="core")
    op.drop_table("appointment_event", schema="core")
    op.drop_index("idx_appointment_candidate_state", table_name="appointment", schema="core")
    op.drop_index("uq_appointment_active_slot", table_name="appointment", schema="core")
    op.drop_table("appointment", schema="core")
    op.drop_index("idx_slot_school_status_start", table_name="slot", schema="core")
    op.drop_table("slot", schema="core")

    op.drop_index("idx_knowledge_chunks_metadata_gin", table_name="chunks", schema="knowledge")
    op.drop_index("idx_knowledge_chunks_program", table_name="chunks", schema="knowledge")
    op.drop_index("idx_knowledge_chunks_active_doc", table_name="chunks", schema="knowledge")
    op.drop_index("idx_knowledge_chunks_tsv", table_name="chunks", schema="knowledge")
    op.drop_index("idx_knowledge_chunks_embedding_hnsw", table_name="chunks", schema="knowledge")
    op.drop_table("chunks", schema="knowledge")

    op.drop_index("idx_sync_logs_job", table_name="document_sync_logs", schema="core")
    op.drop_table("document_sync_logs", schema="core")
    op.drop_index("idx_sync_jobs_version", table_name="document_sync_jobs", schema="core")
    op.drop_index("idx_sync_jobs_status", table_name="document_sync_jobs", schema="core")
    op.drop_table("document_sync_jobs", schema="core")

    op.drop_constraint(
        "fk_documents_current_version",
        "documents",
        schema="core",
        type_="foreignkey",
    )
    op.drop_index("idx_document_versions_active", table_name="document_versions", schema="core")
    op.drop_table("document_versions", schema="core")
    op.drop_index("idx_documents_status", table_name="documents", schema="core")
    op.drop_index("idx_documents_school_category", table_name="documents", schema="core")
    op.drop_table("documents", schema="core")

    op.drop_index("idx_audit_log_entity", table_name="audit_log", schema="core")
    op.drop_table("audit_log", schema="core")
    op.drop_index("idx_outbox_unprocessed", table_name="outbox", schema="core")
    op.drop_table("outbox", schema="core")
    op.drop_table("equivalence_requests", schema="core")
    op.drop_table("shortlist", schema="core")
    op.drop_table("conversation_threads", schema="core")
    op.drop_index("idx_consent_candidate_purpose", table_name="consent", schema="core")
    op.drop_table("consent", schema="core")
    op.drop_index("idx_declared_grades_candidate", table_name="declared_grades", schema="core")
    op.drop_table("declared_grades", schema="core")
    op.drop_index("idx_document_candidate_status", table_name="document", schema="core")
    op.drop_table("document", schema="core")
    op.drop_table("candidate_profile", schema="core")
    op.drop_table("guest_sessions", schema="core")
    op.drop_index("idx_candidate_email", table_name="candidate", schema="core")
    op.drop_index("idx_candidate_status_school", table_name="candidate", schema="core")
    op.drop_table("candidate", schema="core")
    op.execute("DROP INDEX IF EXISTS core.uq_account_roles_scope")
    op.drop_table("account_roles", schema="core")
    op.drop_table("roles", schema="core")
    op.drop_index("uq_accounts_provider_subject", table_name="accounts", schema="core")
    op.drop_table("accounts", schema="core")

    op.drop_index("idx_campaign_school_open", table_name="admission_campaign", schema="core")
    op.drop_table("admission_campaign", schema="core")
    op.drop_index("idx_fee_program_year", table_name="program_fee", schema="core")
    op.drop_table("program_fee", schema="core")
    op.drop_index("idx_prereq_program_track", table_name="program_prerequisite", schema="core")
    op.drop_table("program_prerequisite", schema="core")
    op.drop_index("idx_program_school_level", table_name="program", schema="core")
    op.drop_table("program", schema="core")
    op.drop_table("school", schema="core")

    for enum_name in ("human_decision", "appointment_status", "slot_status", "candidate_status"):
        op.execute(f"DROP TYPE IF EXISTS core.{enum_name}")
