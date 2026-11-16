"""Academic catalog models in the 'core' schema (SA-12)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class School(TimestampMixin, Base):
    """Official NAU institutions supported by SmartCampus AI (iit, isb, extension isa)."""

    __tablename__ = "school"
    __table_args__ = (
        CheckConstraint("code IN ('iit', 'isb', 'isa')", name="school_code_valid"),
        {"schema": "core"},
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_extension: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )


class Program(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Official academic programs/tracks referenced by orientation and admissions."""

    __tablename__ = "program"
    __table_args__ = (
        CheckConstraint(
            "degree_level IN ('licence', 'prepa', 'cycle_ingenieur', 'master')",
            name="program_degree_level_valid",
        ),
        CheckConstraint("duration_years BETWEEN 1 AND 6", name="program_duration_range"),
        Index(
            "idx_program_school_level",
            "school_code",
            "degree_level",
            postgresql_where=text("is_active = TRUE"),
        ),
        {"schema": "core"},
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    school_code: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    degree_level: Mapped[str] = mapped_column(String(32), nullable=False)
    degree_title: Mapped[str] = mapped_column(String(150), nullable=False)
    duration_years: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    study_modes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )


class ProgramPrerequisite(UUIDPrimaryKeyMixin, Base):
    """Structured eligibility prerequisites per entry track for VISION 1.3 Gatekeeper."""

    __tablename__ = "program_prerequisite"
    __table_args__ = (
        CheckConstraint(
            "entry_track IN ('bac', 'licence', 'prepa', 'passerelle')",
            name="prereq_entry_track_valid",
        ),
        CheckConstraint(
            "min_average IS NULL OR (min_average >= 0 AND min_average <= 20)",
            name="prereq_min_average_range",
        ),
        Index("idx_prereq_program_track", "program_id", "entry_track"),
        {"schema": "core"},
    )

    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_track: Mapped[str] = mapped_column(String(32), nullable=False)
    required_diploma: Mapped[str] = mapped_column(String(150), nullable=False)
    min_average: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    yaml_rule_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)


class ProgramFee(UUIDPrimaryKeyMixin, Base):
    """Official tuition schedules per program and academic year (never LLM-generated)."""

    __tablename__ = "program_fee"
    __table_args__ = (
        CheckConstraint("tuition_amount >= 0", name="fee_tuition_non_negative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="fee_effective_dates_valid",
        ),
        UniqueConstraint(
            "program_id",
            "academic_year",
            "effective_from",
            name="uq_program_fee_year_start",
        ),
        Index("idx_fee_program_year", "program_id", "academic_year"),
        {"schema": "core"},
    )

    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="CASCADE"),
        nullable=False,
    )
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False)
    tuition_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'TND'"),
    )
    payment_schedule: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)


class AdmissionCampaign(UUIDPrimaryKeyMixin, Base):
    """Official campaign open/close windows per school and optional program."""

    __tablename__ = "admission_campaign"
    __table_args__ = (
        CheckConstraint("closes_at > opens_at", name="campaign_window_valid"),
        Index("idx_campaign_school_open", "school_code", "is_open", "closes_at"),
        {"schema": "core"},
    )

    school_code: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.program.id", ondelete="CASCADE"),
        nullable=True,
    )
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False)
    phase_name: Mapped[str] = mapped_column(String(100), nullable=False)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_open: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)
