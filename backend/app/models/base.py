"""SQLAlchemy 2.0 DeclarativeBase, naming conventions, and shared PostgreSQL enums."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SCHOOL_CODES: tuple[str, ...] = ("iit", "isb", "isa")

CANDIDATE_STATUS_VALUES: tuple[str, ...] = (
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

SLOT_STATUS_VALUES: tuple[str, ...] = ("FREE", "HELD", "BOOKED")

APPOINTMENT_STATUS_VALUES: tuple[str, ...] = (
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

HUMAN_DECISION_VALUES: tuple[str, ...] = ("ADMITTED", "WAITLIST", "REJECTED")

CANDIDATE_STATUS_ENUM = ENUM(
    *CANDIDATE_STATUS_VALUES,
    name="candidate_status",
    schema="core",
    create_type=False,
)

SLOT_STATUS_ENUM = ENUM(
    *SLOT_STATUS_VALUES,
    name="slot_status",
    schema="core",
    create_type=False,
)

APPOINTMENT_STATUS_ENUM = ENUM(
    *APPOINTMENT_STATUS_VALUES,
    name="appointment_status",
    schema="core",
    create_type=False,
)

HUMAN_DECISION_ENUM = ENUM(
    *HUMAN_DECISION_VALUES,
    name="human_decision",
    schema="core",
    create_type=False,
)


class Base(DeclarativeBase):
    """Shared declarative base carrying deterministic PostgreSQL constraint naming."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """Standard UUIDv4 primary key generated server-side by PostgreSQL."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """Standard UTC creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
