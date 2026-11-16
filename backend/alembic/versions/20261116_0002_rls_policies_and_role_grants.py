"""Enable PostgreSQL Row-Level Security (RLS) policies and RBAC table grants.

Revision ID: 20261116_0002
Revises: 20261116_0001
Create Date: 2026-11-16 22:40:00+00:00
"""

from collections.abc import Sequence

from alembic import op
from app.models.base import SCHOOL_CODES

revision: str = "20261116_0002"
down_revision: str | Sequence[str] | None = "20261116_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANDIDATE_CLAIM = "NULLIF(current_setting('request.jwt.claim.candidate_id', true), '')::uuid"
_SCHOOL_CLAIM = "NULLIF(current_setting('request.jwt.claim.school', true), '')"
_ROLE_CLAIM = "NULLIF(current_setting('request.jwt.claim.role', true), '')"

_CORE_CANDIDATE_CHILD_TABLES: tuple[str, ...] = (
    "candidate_profile",
    "declared_grades",
    "consent",
    "conversation_threads",
    "document",
    "shortlist",
    "equivalence_requests",
    "appointment",
)

_RLS_ENABLED_CORE_TABLES: tuple[str, ...] = (
    "candidate",
    *_CORE_CANDIDATE_CHILD_TABLES,
    "slot",
    "rh_ticket",
    "documents",
    "document_versions",
    "document_sync_jobs",
    "document_sync_logs",
    "accounts",
)


def _exec_statements(*statements: str) -> None:
    """Execute one SQL command per op.execute() call (required by asyncpg prepared stmts)."""
    for stmt in statements:
        op.execute(stmt)


def _apply_optional_role_grants() -> None:
    """Apply grants/policies for direction and platform_admin when those PG roles exist."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'direction') THEN
                GRANT USAGE ON SCHEMA core, iit, isb, isa, knowledge TO direction;
                GRANT SELECT ON ALL TABLES IN SCHEMA core, iit, isb, isa, knowledge TO direction;

                DROP POLICY IF EXISTS direction_read_all ON core.candidate;
                CREATE POLICY direction_read_all ON core.candidate
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_slot ON core.slot;
                CREATE POLICY direction_read_slot ON core.slot
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_rh_ticket ON core.rh_ticket;
                CREATE POLICY direction_read_rh_ticket ON core.rh_ticket
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_documents ON core.documents;
                CREATE POLICY direction_read_documents ON core.documents
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_doc_versions ON core.document_versions;
                CREATE POLICY direction_read_doc_versions ON core.document_versions
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_sync_jobs ON core.document_sync_jobs;
                CREATE POLICY direction_read_sync_jobs ON core.document_sync_jobs
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_sync_logs ON core.document_sync_logs;
                CREATE POLICY direction_read_sync_logs ON core.document_sync_logs
                    FOR SELECT TO direction USING (TRUE);

                DROP POLICY IF EXISTS direction_read_accounts ON core.accounts;
                CREATE POLICY direction_read_accounts ON core.accounts
                    FOR SELECT TO direction USING (TRUE);
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin') THEN
                GRANT USAGE ON SCHEMA core, iit, isb, isa, knowledge TO platform_admin;
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA core, iit, isb, isa, knowledge TO platform_admin;
                GRANT USAGE, SELECT, UPDATE
                    ON ALL SEQUENCES IN SCHEMA core, iit, isb, isa, knowledge TO platform_admin;

                DROP POLICY IF EXISTS admin_all_candidate ON core.candidate;
                CREATE POLICY admin_all_candidate ON core.candidate
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_slot ON core.slot;
                CREATE POLICY admin_all_slot ON core.slot
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_rh_ticket ON core.rh_ticket;
                CREATE POLICY admin_all_rh_ticket ON core.rh_ticket
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_documents ON core.documents;
                CREATE POLICY admin_all_documents ON core.documents
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_doc_versions ON core.document_versions;
                CREATE POLICY admin_all_doc_versions ON core.document_versions
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_sync_jobs ON core.document_sync_jobs;
                CREATE POLICY admin_all_sync_jobs ON core.document_sync_jobs
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_sync_logs ON core.document_sync_logs;
                CREATE POLICY admin_all_sync_logs ON core.document_sync_logs
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);

                DROP POLICY IF EXISTS admin_all_accounts ON core.accounts;
                CREATE POLICY admin_all_accounts ON core.accounts
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE);
            END IF;
        END
        $$
        """
    )


def _apply_core_grants_and_rls() -> None:
    _exec_statements(
        """
        GRANT SELECT ON
            core.school,
            core.program,
            core.program_prerequisite,
            core.program_fee,
            core.admission_campaign,
            core.roles,
            knowledge.chunks
        TO authenticated_student, human_rh, nau_responsable
        """,
        """
        GRANT SELECT, INSERT, UPDATE ON
            core.candidate,
            core.candidate_profile,
            core.declared_grades,
            core.consent,
            core.conversation_threads,
            core.document,
            core.appointment,
            core.appointment_event
        TO authenticated_student
        """,
        "GRANT SELECT, INSERT, DELETE ON core.shortlist TO authenticated_student",
        "GRANT SELECT, INSERT ON core.equivalence_requests TO authenticated_student",
        "GRANT SELECT ON core.slot, core.accounts TO authenticated_student",
        """
        GRANT SELECT ON
            core.accounts,
            core.account_roles,
            core.guest_sessions,
            core.candidate_profile,
            core.consent,
            core.conversation_threads,
            core.document,
            core.shortlist,
            core.appointment,
            core.appointment_event,
            core.outbox,
            core.audit_log,
            core.documents,
            core.document_versions,
            core.document_sync_jobs,
            core.document_sync_logs
        TO human_rh
        """,
        """
        GRANT SELECT, UPDATE ON
            core.candidate,
            core.declared_grades,
            core.equivalence_requests
        TO human_rh
        """,
        "GRANT SELECT, INSERT, UPDATE ON core.slot, core.rh_ticket TO human_rh",
        "GRANT USAGE, SELECT ON SEQUENCE core.document_sync_logs_id_seq TO human_rh",
        """
        GRANT SELECT ON
            core.accounts,
            core.account_roles,
            core.candidate,
            core.candidate_profile,
            core.declared_grades,
            core.document,
            core.shortlist,
            core.appointment,
            core.appointment_event
        TO nau_responsable
        """,
        "GRANT SELECT, UPDATE ON core.equivalence_requests TO nau_responsable",
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            core.slot,
            core.documents,
            core.document_versions,
            core.document_sync_jobs
        TO nau_responsable
        """,
        "GRANT SELECT, INSERT ON core.document_sync_logs TO nau_responsable",
        "GRANT USAGE, SELECT ON SEQUENCE core.document_sync_logs_id_seq TO nau_responsable",
    )

    for table in _RLS_ENABLED_CORE_TABLES:
        op.execute(f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY")

    _exec_statements(
        """
        CREATE POLICY accounts_self_select ON core.accounts
            FOR SELECT TO authenticated_student
            USING (id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
        """,
        """
        CREATE POLICY accounts_staff_select ON core.accounts
            FOR SELECT TO human_rh, nau_responsable
            USING (TRUE)
        """,
        f"""
        CREATE POLICY student_own_candidate ON core.candidate
            FOR ALL TO authenticated_student
            USING (id = {_CANDIDATE_CLAIM})
            WITH CHECK (id = {_CANDIDATE_CLAIM})
        """,
        """
        CREATE POLICY rh_all_candidates ON core.candidate
            FOR ALL TO human_rh
            USING (TRUE)
            WITH CHECK (TRUE)
        """,
        f"""
        CREATE POLICY responsable_school_candidates ON core.candidate
            FOR SELECT TO nau_responsable
            USING (school_target = {_SCHOOL_CLAIM})
        """,
    )

    for table in _CORE_CANDIDATE_CHILD_TABLES:
        _exec_statements(
            f"""
            CREATE POLICY student_own_{table} ON core.{table}
                FOR ALL TO authenticated_student
                USING (candidate_id = {_CANDIDATE_CLAIM})
                WITH CHECK (candidate_id = {_CANDIDATE_CLAIM})
            """,
            f"""
            CREATE POLICY rh_read_{table} ON core.{table}
                FOR SELECT TO human_rh
                USING (TRUE)
            """,
        )

        if table == "equivalence_requests":
            _exec_statements(
                """
                CREATE POLICY rh_update_equivalence_requests ON core.equivalence_requests
                    FOR UPDATE TO human_rh
                    USING (TRUE)
                    WITH CHECK (TRUE)
                """,
                f"""
                CREATE POLICY responsable_school_equivalence_requests ON core.equivalence_requests
                    FOR ALL TO nau_responsable
                    USING (target_school = {_SCHOOL_CLAIM})
                    WITH CHECK (target_school = {_SCHOOL_CLAIM})
                """,
            )
        else:
            op.execute(
                f"""
                CREATE POLICY responsable_school_{table} ON core.{table}
                    FOR SELECT TO nau_responsable
                    USING (
                        EXISTS (
                            SELECT 1
                            FROM core.candidate c
                            WHERE c.id = core.{table}.candidate_id
                              AND c.school_target = {_SCHOOL_CLAIM}
                        )
                    )
                """
            )

    op.execute(
        """
        CREATE POLICY rh_update_declared_grades ON core.declared_grades
            FOR UPDATE TO human_rh
            USING (TRUE)
            WITH CHECK (TRUE)
        """
    )

    for table in _CORE_CANDIDATE_CHILD_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'direction') THEN
                    EXECUTE 'DROP POLICY IF EXISTS direction_read_{table} ON core.{table}';
                    EXECUTE 'CREATE POLICY direction_read_{table} ON core.{table}
                        FOR SELECT TO direction USING (TRUE)';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin') THEN
                    EXECUTE 'DROP POLICY IF EXISTS admin_all_{table} ON core.{table}';
                    EXECUTE 'CREATE POLICY admin_all_{table} ON core.{table}
                        FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE)';
                END IF;
            END
            $$
            """
        )

    _exec_statements(
        """
        CREATE POLICY slot_public_read ON core.slot
            FOR SELECT TO authenticated_student, human_rh
            USING (TRUE)
        """,
        """
        CREATE POLICY rh_manage_slots ON core.slot
            FOR ALL TO human_rh
            USING (TRUE)
            WITH CHECK (TRUE)
        """,
        f"""
        CREATE POLICY responsable_school_slots ON core.slot
            FOR ALL TO nau_responsable
            USING (school_code = {_SCHOOL_CLAIM})
            WITH CHECK (school_code = {_SCHOOL_CLAIM})
        """,
        """
        CREATE POLICY rh_manage_tickets ON core.rh_ticket
            FOR ALL TO human_rh
            USING (TRUE)
            WITH CHECK (TRUE)
        """,
        "CREATE POLICY rh_read_documents ON core.documents FOR SELECT TO human_rh USING (TRUE)",
        f"""
        CREATE POLICY responsable_school_documents ON core.documents
            FOR ALL TO nau_responsable
            USING (school = {_SCHOOL_CLAIM})
            WITH CHECK (school = {_SCHOOL_CLAIM})
        """,
        """
        CREATE POLICY rh_read_doc_versions ON core.document_versions
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_school_doc_versions ON core.document_versions
            FOR ALL TO nau_responsable
            USING (
                EXISTS (
                    SELECT 1 FROM core.documents d
                    WHERE d.id = core.document_versions.document_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM core.documents d
                    WHERE d.id = core.document_versions.document_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
        """,
        """
        CREATE POLICY rh_read_sync_jobs ON core.document_sync_jobs
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_school_sync_jobs ON core.document_sync_jobs
            FOR ALL TO nau_responsable
            USING (
                EXISTS (
                    SELECT 1
                    FROM core.document_versions v
                    JOIN core.documents d ON d.id = v.document_id
                    WHERE v.id = core.document_sync_jobs.version_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                    FROM core.document_versions v
                    JOIN core.documents d ON d.id = v.document_id
                    WHERE v.id = core.document_sync_jobs.version_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
        """,
        """
        CREATE POLICY rh_read_sync_logs ON core.document_sync_logs
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_school_sync_logs ON core.document_sync_logs
            FOR ALL TO nau_responsable
            USING (
                EXISTS (
                    SELECT 1
                    FROM core.document_sync_jobs j
                    JOIN core.document_versions v ON v.id = j.version_id
                    JOIN core.documents d ON d.id = v.document_id
                    WHERE j.id = core.document_sync_logs.job_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                    FROM core.document_sync_jobs j
                    JOIN core.document_versions v ON v.id = j.version_id
                    JOIN core.documents d ON d.id = v.document_id
                    WHERE j.id = core.document_sync_logs.job_id
                      AND d.school = {_SCHOOL_CLAIM}
                )
            )
        """,
    )

    _apply_optional_role_grants()


def _apply_school_grants_and_rls(school: str) -> None:
    """Isolate per-school tables and enforce strict HITL lock on <school>.decisions."""
    _exec_statements(
        f"""
        GRANT SELECT, INSERT, UPDATE ON {school}.dossiers, {school}.qcm_results
            TO authenticated_student
        """,
        f"GRANT SELECT ON {school}.decisions TO authenticated_student",
        f"""
        GRANT SELECT ON {school}.dossiers, {school}.qcm_results, {school}.briefs, {school}.decisions
            TO human_rh
        """,
        f"GRANT SELECT, UPDATE ON {school}.dossiers TO nau_responsable",
        f"GRANT SELECT ON {school}.qcm_results, {school}.briefs TO nau_responsable",
        f"GRANT SELECT, INSERT, UPDATE ON {school}.decisions TO nau_responsable",
        f"ALTER TABLE {school}.dossiers ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {school}.qcm_results ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {school}.briefs ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {school}.decisions ENABLE ROW LEVEL SECURITY",
        f"""
        CREATE POLICY student_own_{school}_dossier ON {school}.dossiers
            FOR ALL TO authenticated_student
            USING (candidate_id = {_CANDIDATE_CLAIM})
            WITH CHECK (candidate_id = {_CANDIDATE_CLAIM})
        """,
        f"""
        CREATE POLICY rh_read_{school}_dossiers ON {school}.dossiers
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_{school}_dossiers ON {school}.dossiers
            FOR SELECT TO nau_responsable
            USING ({_SCHOOL_CLAIM} = '{school}')
        """,
        f"""
        CREATE POLICY responsable_{school}_dossiers_update ON {school}.dossiers
            FOR UPDATE TO nau_responsable
            USING ({_SCHOOL_CLAIM} = '{school}')
            WITH CHECK ({_SCHOOL_CLAIM} = '{school}')
        """,
        f"""
        CREATE POLICY student_own_{school}_qcm ON {school}.qcm_results
            FOR ALL TO authenticated_student
            USING (candidate_id = {_CANDIDATE_CLAIM})
            WITH CHECK (candidate_id = {_CANDIDATE_CLAIM})
        """,
        f"""
        CREATE POLICY rh_read_{school}_qcm ON {school}.qcm_results
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_{school}_qcm ON {school}.qcm_results
            FOR SELECT TO nau_responsable
            USING ({_SCHOOL_CLAIM} = '{school}')
        """,
        f"""
        CREATE POLICY rh_read_{school}_briefs ON {school}.briefs
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_{school}_briefs ON {school}.briefs
            FOR SELECT TO nau_responsable
            USING ({_SCHOOL_CLAIM} = '{school}')
        """,
        f"""
        CREATE POLICY student_read_own_{school}_decision ON {school}.decisions
            FOR SELECT TO authenticated_student
            USING (candidate_id = {_CANDIDATE_CLAIM})
        """,
        f"""
        CREATE POLICY rh_read_{school}_decisions ON {school}.decisions
            FOR SELECT TO human_rh USING (TRUE)
        """,
        f"""
        CREATE POLICY responsable_{school}_read_decisions ON {school}.decisions
            FOR SELECT TO nau_responsable
            USING ({_SCHOOL_CLAIM} = '{school}')
        """,
        f"""
        CREATE POLICY responsable_{school}_write_decisions ON {school}.decisions
            FOR INSERT TO nau_responsable
            WITH CHECK (
                {_SCHOOL_CLAIM} = '{school}'
                AND {_ROLE_CLAIM} = 'responsable'
            )
        """,
        f"""
        CREATE POLICY responsable_{school}_update_decisions ON {school}.decisions
            FOR UPDATE TO nau_responsable
            USING (
                {_SCHOOL_CLAIM} = '{school}'
                AND {_ROLE_CLAIM} = 'responsable'
            )
            WITH CHECK (
                {_SCHOOL_CLAIM} = '{school}'
                AND {_ROLE_CLAIM} = 'responsable'
            )
        """,
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'direction') THEN
                EXECUTE 'DROP POLICY IF EXISTS direction_read_{school}_dossiers '
                     || 'ON {school}.dossiers';
                EXECUTE 'CREATE POLICY direction_read_{school}_dossiers ON {school}.dossiers
                    FOR SELECT TO direction USING (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS direction_read_{school}_qcm ON {school}.qcm_results';
                EXECUTE 'CREATE POLICY direction_read_{school}_qcm ON {school}.qcm_results
                    FOR SELECT TO direction USING (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS direction_read_{school}_briefs ON {school}.briefs';
                EXECUTE 'CREATE POLICY direction_read_{school}_briefs ON {school}.briefs
                    FOR SELECT TO direction USING (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS direction_read_{school}_decisions '
                     || 'ON {school}.decisions';
                EXECUTE 'CREATE POLICY direction_read_{school}_decisions ON {school}.decisions
                    FOR SELECT TO direction USING (TRUE)';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin') THEN
                EXECUTE 'DROP POLICY IF EXISTS admin_all_{school}_dossiers ON {school}.dossiers';
                EXECUTE 'CREATE POLICY admin_all_{school}_dossiers ON {school}.dossiers
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS admin_all_{school}_qcm ON {school}.qcm_results';
                EXECUTE 'CREATE POLICY admin_all_{school}_qcm ON {school}.qcm_results
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS admin_all_{school}_briefs ON {school}.briefs';
                EXECUTE 'CREATE POLICY admin_all_{school}_briefs ON {school}.briefs
                    FOR ALL TO platform_admin USING (TRUE) WITH CHECK (TRUE)';
                EXECUTE 'DROP POLICY IF EXISTS admin_read_{school}_decisions ON {school}.decisions';
                EXECUTE 'CREATE POLICY admin_read_{school}_decisions ON {school}.decisions
                    FOR SELECT TO platform_admin USING (TRUE)';
            END IF;
        END
        $$
        """,
    )


def upgrade() -> None:
    """Apply least-privilege grants and PostgreSQL Row-Level Security across schemas."""
    _apply_core_grants_and_rls()
    for school in SCHOOL_CODES:
        _apply_school_grants_and_rls(school)


def downgrade() -> None:
    """Disable RLS and drop policies across school and core tables."""
    for school in reversed(SCHOOL_CODES):
        for table in ("decisions", "briefs", "qcm_results", "dossiers"):
            op.execute(f"ALTER TABLE {school}.{table} DISABLE ROW LEVEL SECURITY")
        for policy in (
            f"student_own_{school}_dossier",
            f"rh_read_{school}_dossiers",
            f"responsable_{school}_dossiers",
            f"responsable_{school}_dossiers_update",
            f"direction_read_{school}_dossiers",
            f"admin_all_{school}_dossiers",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {school}.dossiers")
        for policy in (
            f"student_own_{school}_qcm",
            f"rh_read_{school}_qcm",
            f"responsable_{school}_qcm",
            f"direction_read_{school}_qcm",
            f"admin_all_{school}_qcm",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {school}.qcm_results")
        for policy in (
            f"rh_read_{school}_briefs",
            f"responsable_{school}_briefs",
            f"direction_read_{school}_briefs",
            f"admin_all_{school}_briefs",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {school}.briefs")
        for policy in (
            f"student_read_own_{school}_decision",
            f"rh_read_{school}_decisions",
            f"responsable_{school}_read_decisions",
            f"responsable_{school}_write_decisions",
            f"responsable_{school}_update_decisions",
            f"direction_read_{school}_decisions",
            f"admin_read_{school}_decisions",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {school}.decisions")

    for table in _RLS_ENABLED_CORE_TABLES:
        op.execute(f"ALTER TABLE core.{table} DISABLE ROW LEVEL SECURITY")

    for policy, table in (
        ("accounts_self_select", "accounts"),
        ("accounts_staff_select", "accounts"),
        ("direction_read_accounts", "accounts"),
        ("admin_all_accounts", "accounts"),
        ("student_own_candidate", "candidate"),
        ("rh_all_candidates", "candidate"),
        ("responsable_school_candidates", "candidate"),
        ("direction_read_all", "candidate"),
        ("admin_all_candidate", "candidate"),
        ("slot_public_read", "slot"),
        ("rh_manage_slots", "slot"),
        ("responsable_school_slots", "slot"),
        ("direction_read_slot", "slot"),
        ("admin_all_slot", "slot"),
        ("rh_manage_tickets", "rh_ticket"),
        ("direction_read_rh_ticket", "rh_ticket"),
        ("admin_all_rh_ticket", "rh_ticket"),
        ("rh_read_documents", "documents"),
        ("responsable_school_documents", "documents"),
        ("direction_read_documents", "documents"),
        ("admin_all_documents", "documents"),
        ("rh_read_doc_versions", "document_versions"),
        ("responsable_school_doc_versions", "document_versions"),
        ("direction_read_doc_versions", "document_versions"),
        ("admin_all_doc_versions", "document_versions"),
        ("rh_read_sync_jobs", "document_sync_jobs"),
        ("responsable_school_sync_jobs", "document_sync_jobs"),
        ("direction_read_sync_jobs", "document_sync_jobs"),
        ("admin_all_sync_jobs", "document_sync_jobs"),
        ("rh_read_sync_logs", "document_sync_logs"),
        ("responsable_school_sync_logs", "document_sync_logs"),
        ("direction_read_sync_logs", "document_sync_logs"),
        ("admin_all_sync_logs", "document_sync_logs"),
        ("rh_update_declared_grades", "declared_grades"),
        ("rh_update_equivalence_requests", "equivalence_requests"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON core.{table}")

    for table in _CORE_CANDIDATE_CHILD_TABLES:
        for policy in (
            f"student_own_{table}",
            f"rh_read_{table}",
            f"responsable_school_{table}",
            f"direction_read_{table}",
            f"admin_all_{table}",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON core.{table}")
