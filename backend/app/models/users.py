"""Core user, RBAC, candidate profile, consent, outbox, and audit models (SA-13)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    CANDIDATE_STATUS_ENUM,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Account(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authenticated platform accounts for candidates and internal NAU staff."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "auth_provider IN ('EMAIL_OTP', 'PHONE_OTP', 'GOOGLE_OAUTH', 'SSO')",
            name="accounts_auth_provider_valid",
        ),
        Index(
            "uq_accounts_provider_subject",
            "auth_provider",
            "provider_subject",
            unique=True,
            postgresql_where=text("provider_subject IS NOT NULL"),
        ),
        {"schema": "core"},
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Role(Base):
    """RBAC role catalog aligned with JWT role claims."""

    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "code IN ('student', 'rh', 'responsable', 'direction', 'admin')",
            name="roles_code_valid",
        ),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(SmallInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class AccountRole(UUIDPrimaryKeyMixin, Base):
    """Account-to-role assignments scoped per school for RLS partitioning."""

    __tablename__ = "account_roles"
    __table_args__ = (
        CheckConstraint(
            "school_scope IS NULL OR school_scope IN ('iit', 'isb', 'isa')",
            name="account_roles_school_scope_valid",
        ),
        CheckConstraint(
            "responsibility_scope IN ('ALL', 'ACADEMIC', 'INTERVIEW')",
            name="account_roles_responsibility_valid",
        ),
        Index(
            "uq_account_roles_scope",
            text("account_id"),
            text("role_id"),
            text("COALESCE(school_scope, 'GLOBAL')"),
            unique=True,
        ),
        {"schema": "core"},
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("core.roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    school_scope: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=True,
    )
    responsibility_scope: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'ALL'"),
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Central candidate dossier state and exclusive case ownership (JARVIS | RH:<id>)."""

    __tablename__ = "candidate"
    __table_args__ = (
        CheckConstraint(
            "school_target IS NULL OR school_target IN ('iit', 'isb', 'isa')",
            name="candidate_school_target_valid",
        ),
        CheckConstraint(
            "owner = 'JARVIS' OR owner LIKE 'RH:%'",
            name="candidate_one_owner_valid",
        ),
        Index("idx_candidate_status_school", "status", "school_target"),
        Index("idx_candidate_email", text("LOWER(email)")),
        {"schema": "core"},
    )

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    candidate_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        CANDIDATE_STATUS_ENUM,
        nullable=False,
        server_default=text("'AUTHENTICATED'"),
    )
    school_target: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=True,
    )
    owner: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'JARVIS'"),
    )


class GuestSession(Base):
    """PII-free anonymous session tracker for idempotent Redis-to-Postgres merge."""

    __tablename__ = "guest_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'MERGED', 'EXPIRED')",
            name="guest_sessions_status_valid",
        ),
        CheckConstraint(
            "preferred_language IS NULL OR preferred_language IN ('FR', 'DERJA', 'ARABIZI')",
            name="guest_sessions_language_valid",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    preferred_language: Mapped[str | None] = mapped_column(String(24), nullable=True)
    merged_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="SET NULL"),
        nullable=True,
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class CandidateProfile(Base):
    """Warm conversation memory: structured academic facts and <= 300-token rolling summary."""

    __tablename__ = "candidate_profile"
    __table_args__ = (
        CheckConstraint(
            "declared_overall_avg IS NULL "
            "OR (declared_overall_avg >= 0 AND declared_overall_avg <= 20)",
            name="profile_declared_avg_range",
        ),
        CheckConstraint(
            "preferred_language IS NULL OR preferred_language IN ('FR', 'DERJA', 'ARABIZI')",
            name="profile_language_valid",
        ),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        primary_key=True,
    )
    facts: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    current_education_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    declared_overall_avg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(24), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class CandidateDocument(UUIDPrimaryKeyMixin, Base):
    """Private candidate transcripts/diplomas in MinIO + VISION 1.4 Reader OCR extraction."""

    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint(
            "type IN ("
            "'RELEVE_BAC', 'RELEVE_UNIVERSITAIRE', 'DIPLOME', 'ATTESTATION_EQUIVALENCE', 'AUTRE'"
            ")",
            name="candidate_document_type_valid",
        ),
        CheckConstraint(
            "min_confidence IS NULL OR (min_confidence >= 0 AND min_confidence <= 1)",
            name="candidate_document_confidence_range",
        ),
        CheckConstraint(
            "verification_status IN ('PENDING', 'CONFIRMED_BY_STUDENT', 'MISMATCH_RH', 'VERIFIED')",
            name="candidate_document_status_valid",
        ),
        Index("idx_document_candidate_status", "candidate_id", "verification_status"),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    min_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class DeclaredGrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Subject grades tracking declared orientation input separately from VLM-verified marks."""

    __tablename__ = "declared_grades"
    __table_args__ = (
        CheckConstraint(
            "diploma_stage IN ('BAC', 'L1', 'L2', 'L3', 'PREPA_1', 'PREPA_2', 'M1', 'OTHER')",
            name="grades_diploma_stage_valid",
        ),
        CheckConstraint("grade_scale > 0", name="grades_scale_positive"),
        CheckConstraint(
            "grade_value >= 0 AND grade_value <= grade_scale",
            name="grades_value_range",
        ),
        CheckConstraint("coefficient IS NULL OR coefficient > 0", name="grades_coeff_positive"),
        CheckConstraint(
            "entry_source IN ('DECLARED_CHAT', 'CANDIDATE_FORM', 'VLM_CONFIRMED', 'RH_CORRECTED')",
            name="grades_entry_source_valid",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="grades_confidence_range",
        ),
        Index("idx_declared_grades_candidate", "candidate_id", "diploma_stage"),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    academic_year: Mapped[str | None] = mapped_column(String(16), nullable=True)
    diploma_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(128), nullable=False)
    grade_value: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    grade_scale: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default=text("20.00"),
    )
    coefficient: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    entry_source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'DECLARED_CHAT'"),
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)


class Consent(UUIDPrimaryKeyMixin, Base):
    """Explicit INPDP (Law 2004-63) consent proof records with version and withdrawal date."""

    __tablename__ = "consent"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('ORIENTATION_PROFILE', 'DOCUMENT_OCR', 'ADMISSION_FOLLOWUP')",
            name="consent_purpose_valid",
        ),
        Index(
            "idx_consent_candidate_purpose",
            "candidate_id",
            "purpose",
            text("given_at DESC"),
        ),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    given_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationThread(UUIDPrimaryKeyMixin, Base):
    """Links LangGraph checkpoints and MinIO cold archives to guest or authenticated threads."""

    __tablename__ = "conversation_threads"
    __table_args__ = (
        CheckConstraint(
            "candidate_id IS NOT NULL OR guest_session_id IS NOT NULL",
            name="conversation_threads_owner_present",
        ),
        CheckConstraint("turn_count >= 0", name="conversation_threads_turns_non_negative"),
        CheckConstraint(
            "last_detected_dialect IS NULL OR last_detected_dialect IN ('FR', 'DERJA', 'ARABIZI')",
            name="conversation_threads_dialect_valid",
        ),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=True,
    )
    guest_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    langgraph_thread_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    turn_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    last_detected_dialect: Mapped[str | None] = mapped_column(String(24), nullable=True)
    cold_archive_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class Shortlist(UUIDPrimaryKeyMixin, Base):
    """Ranked track recommendations produced by VISION 1.5 Advisor with official citations."""

    __tablename__ = "shortlist"
    __table_args__ = (
        CheckConstraint("rank BETWEEN 1 AND 5", name="shortlist_rank_range"),
        CheckConstraint("score >= 0 AND score <= 100", name="shortlist_score_range"),
        UniqueConstraint("candidate_id", "program_id", name="uq_shortlist_candidate_program"),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="CASCADE"),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class EquivalenceRequest(UUIDPrimaryKeyMixin, Base):
    """Passerelle / equivalence borderline assessments confirmed exclusively by humans."""

    __tablename__ = "equivalence_requests"
    __table_args__ = (
        CheckConstraint(
            "target_school IN ('iit', 'isb', 'isa')",
            name="equiv_target_school_valid",
        ),
        CheckConstraint(
            "gatekeeper_verdict IN ('ELIGIBLE', 'BORDERLINE_TO_CONFIRM', 'INELIGIBLE')",
            name="equiv_verdict_valid",
        ),
        CheckConstraint(
            "review_status IN ("
            "'AUTO_VALIDATED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'NEEDS_MORE_INFO'"
            ")",
            name="equiv_review_status_valid",
        ),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_diploma: Mapped[str] = mapped_column(String(128), nullable=False)
    source_institution: Mapped[str | None] = mapped_column(String(192), nullable=True)
    target_school: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=False,
    )
    target_program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="RESTRICT"),
        nullable=False,
    )
    yaml_rule_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gatekeeper_verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'PENDING_REVIEW'"),
    )
    supporting_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    """Transactional outbox decoupling VISION front-office from JARVIS back-office workers."""

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="outbox_retry_non_negative"),
        Index(
            "idx_outbox_unprocessed",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
        {"schema": "core"},
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
    )


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Immutable state-change audit trail (who / what / why) without cleartext PII."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ("
            "'STUDENT', 'RH', 'RESPONSABLE', 'DIRECTION', 'ADMIN', 'VISION', 'JARVIS', 'SYSTEM'"
            ")",
            name="audit_actor_type_valid",
        ),
        Index("idx_audit_log_entity", "entity_type", "entity_id", "created_at"),
        {"schema": "core"},
    )

    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
