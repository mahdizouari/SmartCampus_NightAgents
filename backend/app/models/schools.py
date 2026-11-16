"""School-isolated dossiers, QCM results, briefs, and HITL decisions (iit, isb, isa)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models.base import (
    HUMAN_DECISION_ENUM,
    SCHOOL_CODES,
    Base,
    UUIDPrimaryKeyMixin,
)


class _SchoolDossierMixin(UUIDPrimaryKeyMixin):
    """Shared column layout for isolated `<school>.dossiers` tables."""

    dossier_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.document.id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_profile: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'PREINSCRIBED'"),
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class _SchoolQcmResultMixin(UUIDPrimaryKeyMixin):
    """Shared column layout for isolated `<school>.qcm_results` tables."""

    @declared_attr
    def dossier_id(cls) -> Mapped[uuid.UUID]:
        schema = cls.__table_args__[-1]["schema"]
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey(f"{schema}.dossiers.id", ondelete="CASCADE"),
            nullable=False,
        )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    total_questions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    answers: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class _SchoolBriefMixin(UUIDPrimaryKeyMixin):
    """Shared column layout for isolated `<school>.briefs` tables."""

    @declared_attr
    def dossier_id(cls) -> Mapped[uuid.UUID]:
        schema = cls.__table_args__[-1]["schema"]
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey(f"{schema}.dossiers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    brief_pdf_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    academic_summary: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    compatibility_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    generated_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'VISION_1.6'"),
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


class _SchoolDecisionMixin(UUIDPrimaryKeyMixin):
    """Shared column layout for HITL `<school>.decisions` tables."""

    @declared_attr
    def dossier_id(cls) -> Mapped[uuid.UUID]:
        schema = cls.__table_args__[-1]["schema"]
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey(f"{schema}.dossiers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(HUMAN_DECISION_ENUM, nullable=False)
    assigned_program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="RESTRICT"),
        nullable=True,
    )
    interviewer_notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    signature_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


@dataclass(frozen=True)
class SchoolSchemaModels:
    """Grouped ORM classes for one isolated school PostgreSQL schema."""

    schema: str
    dossier: type[Base]
    qcm_result: type[Base]
    brief: type[Base]
    decision: type[Base]


def build_school_models(schema_name: str) -> SchoolSchemaModels:
    """Construct distinct ORM classes for `dossiers`, `qcm_results`, `briefs`, `decisions`."""
    prefix = schema_name.capitalize()

    dossier_cls = type(
        f"{prefix}Dossier",
        (_SchoolDossierMixin, Base),
        {
            "__module__": __name__,
            "__tablename__": "dossiers",
            "__table_args__": (
                CheckConstraint(
                    "status IN ("
                    "'PREINSCRIBED', 'QCM_DONE', 'SCHEDULED', 'INTERVIEWED', 'DECIDED', 'WITHDRAWN'"
                    ")",
                    name=f"{schema_name}_dossier_status_valid",
                ),
                Index(f"idx_{schema_name}_dossiers_status", "status"),
                {"schema": schema_name},
            ),
        },
    )

    qcm_cls = type(
        f"{prefix}QcmResult",
        (_SchoolQcmResultMixin, Base),
        {
            "__module__": __name__,
            "__tablename__": "qcm_results",
            "__table_args__": (
                CheckConstraint(
                    "score >= 0 AND score <= 100",
                    name=f"{schema_name}_qcm_score_range",
                ),
                CheckConstraint(
                    "total_questions BETWEEN 1 AND 50",
                    name=f"{schema_name}_qcm_questions_range",
                ),
                Index(f"idx_{schema_name}_qcm_candidate", "candidate_id"),
                {"schema": schema_name},
            ),
        },
    )

    brief_cls = type(
        f"{prefix}Brief",
        (_SchoolBriefMixin, Base),
        {
            "__module__": __name__,
            "__tablename__": "briefs",
            "__table_args__": (
                CheckConstraint(
                    "compatibility_score IS NULL "
                    "OR (compatibility_score >= 0 AND compatibility_score <= 100)",
                    name=f"{schema_name}_brief_compat_range",
                ),
                {"schema": schema_name},
            ),
        },
    )

    decision_cls = type(
        f"{prefix}Decision",
        (_SchoolDecisionMixin, Base),
        {
            "__module__": __name__,
            "__tablename__": "decisions",
            "__table_args__": ({"schema": schema_name},),
        },
    )

    return SchoolSchemaModels(
        schema=schema_name,
        dossier=dossier_cls,
        qcm_result=qcm_cls,
        brief=brief_cls,
        decision=decision_cls,
    )


SCHOOL_MODELS: dict[str, SchoolSchemaModels] = {
    school: build_school_models(school) for school in SCHOOL_CODES
}

IitDossier = SCHOOL_MODELS["iit"].dossier
IitQcmResult = SCHOOL_MODELS["iit"].qcm_result
IitBrief = SCHOOL_MODELS["iit"].brief
IitDecision = SCHOOL_MODELS["iit"].decision

IsbDossier = SCHOOL_MODELS["isb"].dossier
IsbQcmResult = SCHOOL_MODELS["isb"].qcm_result
IsbBrief = SCHOOL_MODELS["isb"].brief
IsbDecision = SCHOOL_MODELS["isb"].decision

IsaDossier = SCHOOL_MODELS["isa"].dossier
IsaQcmResult = SCHOOL_MODELS["isa"].qcm_result
IsaBrief = SCHOOL_MODELS["isa"].brief
IsaDecision = SCHOOL_MODELS["isa"].decision
