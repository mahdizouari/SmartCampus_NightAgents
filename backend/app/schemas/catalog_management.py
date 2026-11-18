"""Pydantic v2 request and response schemas for catalog management (SA-20)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SchoolCode = Literal["iit", "isb", "isa"]
DegreeLevel = Literal["licence", "prepa", "cycle_ingenieur", "master"]
EntryTrack = Literal["bac", "licence", "prepa", "passerelle"]


# ── Schools ──────────────────────────────────────────────────────────────────


class SchoolCreate(BaseModel):
    code: SchoolCode
    name: str = Field(..., min_length=2, max_length=150)
    is_extension: bool = False
    is_active: bool = True


class SchoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    is_extension: bool | None = None
    is_active: bool | None = None


class SchoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: SchoolCode
    name: str
    is_extension: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ── Programs ─────────────────────────────────────────────────────────────────


class ProgramCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=40)
    school_code: SchoolCode
    title: str = Field(..., min_length=3, max_length=200)
    degree_level: DegreeLevel
    degree_title: str = Field(..., min_length=2, max_length=150)
    duration_years: int = Field(..., ge=1, le=6)
    study_modes: list[str] = Field(default_factory=list)
    description: str = ""
    source_document: str = Field(..., min_length=2, max_length=255)
    is_active: bool = True


class ProgramUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=40)
    school_code: SchoolCode | None = None
    title: str | None = Field(default=None, min_length=3, max_length=200)
    degree_level: DegreeLevel | None = None
    degree_title: str | None = Field(default=None, min_length=2, max_length=150)
    duration_years: int | None = Field(default=None, ge=1, le=6)
    study_modes: list[str] | None = None
    description: str | None = None
    source_document: str | None = Field(default=None, min_length=2, max_length=255)
    is_active: bool | None = None


class ProgramResponse(BaseModel):
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
    created_at: datetime
    updated_at: datetime


# ── Prerequisites ────────────────────────────────────────────────────────────


class PrerequisiteCreate(BaseModel):
    entry_track: EntryTrack
    required_diploma: str = Field(..., min_length=2, max_length=150)
    min_average: Decimal | None = Field(default=None, ge=0, le=20)
    yaml_rule_key: str | None = Field(default=None, max_length=100)
    notes: str = ""
    source_document: str = Field(..., min_length=2, max_length=255)


class PrerequisiteUpdate(BaseModel):
    entry_track: EntryTrack | None = None
    required_diploma: str | None = Field(default=None, min_length=2, max_length=150)
    min_average: Decimal | None = Field(default=None, ge=0, le=20)
    yaml_rule_key: str | None = Field(default=None, max_length=100)
    notes: str | None = None
    source_document: str | None = Field(default=None, min_length=2, max_length=255)


class PrerequisiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    program_id: UUID
    entry_track: EntryTrack
    required_diploma: str
    min_average: Decimal | None
    yaml_rule_key: str | None
    notes: str
    source_document: str


# ── Program Fees ─────────────────────────────────────────────────────────────


class FeeCreate(BaseModel):
    academic_year: str = Field(..., min_length=4, max_length=16)
    tuition_amount: Decimal = Field(..., ge=0)
    currency: str = Field(default="TND", min_length=2, max_length=8)
    payment_schedule: str = ""
    effective_from: date
    effective_to: date | None = None
    source_document: str = Field(..., min_length=2, max_length=255)

    @model_validator(mode="after")
    def validate_effective_dates(self) -> "FeeCreate":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from.")
        return self


class FeeUpdate(BaseModel):
    academic_year: str | None = Field(default=None, min_length=4, max_length=16)
    tuition_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=2, max_length=8)
    payment_schedule: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_document: str | None = Field(default=None, min_length=2, max_length=255)


class FeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    program_id: UUID
    academic_year: str
    tuition_amount: Decimal
    currency: str
    payment_schedule: str
    effective_from: date
    effective_to: date | None
    source_document: str


# ── Admission Campaigns ──────────────────────────────────────────────────────


class CampaignCreate(BaseModel):
    school_code: SchoolCode
    program_id: UUID | None = None
    academic_year: str = Field(..., min_length=4, max_length=16)
    phase_name: str = Field(..., min_length=2, max_length=100)
    opens_at: datetime
    closes_at: datetime
    is_open: bool = True
    source_document: str = Field(..., min_length=2, max_length=255)

    @model_validator(mode="after")
    def validate_window(self) -> "CampaignCreate":
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be strictly after opens_at.")
        return self


class CampaignUpdate(BaseModel):
    school_code: SchoolCode | None = None
    program_id: UUID | None = None
    academic_year: str | None = Field(default=None, min_length=4, max_length=16)
    phase_name: str | None = Field(default=None, min_length=2, max_length=100)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    is_open: bool | None = None
    source_document: str | None = Field(default=None, min_length=2, max_length=255)


class CampaignResponse(BaseModel):
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

