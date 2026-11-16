"""Scheduling (JARVIS) and RH escalation ticket models in the 'core' schema."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    APPOINTMENT_STATUS_ENUM,
    SLOT_STATUS_ENUM,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Slot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Interview availability slots published per NAU Responsable with double-booking lock."""

    __tablename__ = "slot"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="slot_time_window_valid"),
        UniqueConstraint("responsable_id", "start_time", name="uq_slot_responsable_start"),
        Index("idx_slot_school_status_start", "school_code", "status", "start_time"),
        {"schema": "core"},
    )

    responsable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    school_code: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    room_or_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        SLOT_STATUS_ENUM,
        nullable=False,
        server_default=text("'FREE'"),
    )
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    held_by_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="SET NULL"),
        nullable=True,
    )


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Candidate interview appointments bounded to a maximum of 2 automated reschedules."""

    __tablename__ = "appointment"
    __table_args__ = (
        CheckConstraint("reschedules BETWEEN 0 AND 2", name="appointment_max_reschedules"),
        Index(
            "uq_appointment_active_slot",
            "slot_id",
            unique=True,
            postgresql_where=text(
                "slot_id IS NOT NULL AND state IN ('INVITED', 'BOOKED', 'REMINDED')"
            ),
        ),
        Index("idx_appointment_candidate_state", "candidate_id", "state"),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.slot.id", ondelete="SET NULL"),
        nullable=True,
    )
    school_code: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        APPOINTMENT_STATUS_ENUM,
        nullable=False,
        server_default=text("'AWAITING_SLOT'"),
    )
    reschedules: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppointmentEvent(UUIDPrimaryKeyMixin, Base):
    """Idempotent state transitions and reminder trace per appointment."""

    __tablename__ = "appointment_event"
    __table_args__ = (
        Index("idx_appointment_event_appt", "appointment_id", "created_at"),
        {"schema": "core"},
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.appointment.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_state: Mapped[str | None] = mapped_column(APPOINTMENT_STATUS_ENUM, nullable=True)
    to_state: Mapped[str | None] = mapped_column(APPOINTMENT_STATUS_ENUM, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
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


class RhTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Human RH exception tickets sharing single-owner governance (JARVIS | RH:<id>)."""

    __tablename__ = "rh_ticket"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="rh_ticket_priority_valid",
        ),
        CheckConstraint(
            "owner = 'JARVIS' OR owner LIKE 'RH:%'",
            name="rh_ticket_one_owner_valid",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'CLAIMED', 'RESOLVED', 'CLOSED')",
            name="rh_ticket_status_valid",
        ),
        Index("idx_rh_ticket_status_owner", "status", "owner"),
        Index("idx_rh_ticket_candidate", "candidate_id"),
        {"schema": "core"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.candidate.id", ondelete="CASCADE"),
        nullable=False,
    )
    school_code: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey("core.school.code", ondelete="RESTRICT"),
        nullable=True,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'NORMAL'"),
    )
    owner: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'RH:unassigned'"),
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'OPEN'"),
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
