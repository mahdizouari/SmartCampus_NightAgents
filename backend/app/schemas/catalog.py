"""Pydantic v2 schemas for public catalog consultation (SA-21)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

SchoolCode = Literal["iit", "isb", "isa"]
DegreeLevel = Literal["licence", "prepa", "cycle_ingenieur", "master"]
EntryTrack = Literal["bac", "licence", "prepa", "passerelle"]


# ── School Consultation ───────────────────────────────────────────────────────


class PublicSchoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: SchoolCode
    name: str
    is_extension: bool
    is_active: bool


# ── Prerequisite / Admission Conditions ───────────────────────────────────────


class PublicPrerequisiteItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    program_id: UUID
    entry_track: EntryTrack
    required_diploma: str
    min_average: Decimal | None
    yaml_rule_key: str | None
    notes: str
    source_document: str


# ── Tuition Fees & Payment Schedules ──────────────────────────────────────────


class PublicFeeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    program_id: UUID
    academic_year: str
    tuition_amount: Decimal
    currency: str
    payment_schedule: str
    effective_from: date
    effective_to: date | None
    is_current_valid: bool = True
    source_document: str


# ── Admission Campaigns ───────────────────────────────────────────────────────


class PublicCampaignItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_code: SchoolCode
    program_id: UUID | None
    academic_year: str
    phase_name: str
    opens_at: datetime
    closes_at: datetime
    is_open: bool
    source_document: str


# ── Program Summary & Detail ──────────────────────────────────────────────────


class PublicProgramSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    school_code: SchoolCode
    title: str
    degree_level: DegreeLevel
    degree_title: str
    duration_years: int
    study_modes: list[str]
    description: str
    source_document: str
    is_active: bool


class PublicProgramDetail(PublicProgramSummary):
    prerequisites: list[PublicPrerequisiteItem] = []
    fees: list[PublicFeeItem] = []
    active_campaigns: list[PublicCampaignItem] = []


class ProgramListResponse(BaseModel):
    total: int
    items: list[PublicProgramSummary]


# ── Program Comparison (SA-19 / v3 side-by-side) ─────────────────────────────


class ProgramCompareItem(BaseModel):
    program: PublicProgramSummary
    prerequisites: list[PublicPrerequisiteItem] = []
    fees: list[PublicFeeItem] = []


class ProgramCompareResponse(BaseModel):
    count: int
    programs: list[ProgramCompareItem]

