"""Public catalog consultation service logic (SA-21)."""

from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import (
    AdmissionCampaign,
    Program,
    ProgramFee,
    ProgramPrerequisite,
    School,
)
from app.schemas.catalog import (
    DegreeLevel,
    ProgramCompareItem,
    PublicCampaignItem,
    PublicFeeItem,
    PublicPrerequisiteItem,
    PublicProgramDetail,
    PublicProgramSummary,
    PublicSchoolResponse,
    SchoolCode,
)


def _is_uuid(val: str) -> bool:
    try:
        UUID(str(val))
        return True
    except (ValueError, AttributeError):
        return False


# ── Schools ───────────────────────────────────────────────────────────────────


async def list_schools(
    db: AsyncSession,
    is_active: bool = True,
) -> list[PublicSchoolResponse]:
    stmt = select(School)
    if is_active:
        stmt = stmt.where(School.is_active.is_(True))
    stmt = stmt.order_by(School.code.asc())

    result = await db.execute(stmt)
    schools = result.scalars().all()
    return [PublicSchoolResponse.model_validate(s) for s in schools]


# ── Programs List & Search ────────────────────────────────────────────────────


async def list_programs(
    db: AsyncSession,
    school_code: SchoolCode | None = None,
    degree_level: DegreeLevel | None = None,
    study_mode: str | None = None,
    search: str | None = None,
    is_active: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[Program]]:
    query: Select[tuple[Program]] = select(Program)

    if is_active:
        query = query.where(Program.is_active.is_(True))

    if school_code:
        query = query.where(Program.school_code == school_code)

    if degree_level:
        query = query.where(Program.degree_level == degree_level)

    if study_mode:
        query = query.where(Program.study_modes.contains([study_mode]))

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Program.title.ilike(search_pattern),
                Program.code.ilike(search_pattern),
                Program.description.ilike(search_pattern),
            )
        )

    # Count total
    count_stmt = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one() or 0

    # Paginate and order
    query = (
        query.order_by(Program.school_code.asc(), Program.title.asc()).limit(limit).offset(offset)
    )
    result = await db.execute(query)
    programs = list(result.scalars().all())

    return total, programs


# ── Single Program Resolution ─────────────────────────────────────────────────


async def get_program_by_id_or_code(
    db: AsyncSession,
    identifier: str | UUID,
    only_active: bool = True,
) -> Program:
    stmt = select(Program)
    if isinstance(identifier, UUID) or _is_uuid(str(identifier)):
        stmt = stmt.where(Program.id == UUID(str(identifier)))
    else:
        stmt = stmt.where(Program.code == str(identifier))

    if only_active:
        stmt = stmt.where(Program.is_active.is_(True))

    result = await db.execute(stmt)
    program = result.scalars().first()
    if not program:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Formation '{identifier}' introuvable ou inactive.",
        )
    return program


# ── Prerequisites & Conditions ────────────────────────────────────────────────


async def get_program_prerequisites(
    db: AsyncSession,
    program_id: UUID,
    entry_track: str | None = None,
) -> list[ProgramPrerequisite]:
    stmt = select(ProgramPrerequisite).where(ProgramPrerequisite.program_id == program_id)
    if entry_track:
        stmt = stmt.where(ProgramPrerequisite.entry_track == entry_track)
    stmt = stmt.order_by(ProgramPrerequisite.entry_track.asc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Fees & Schedules ──────────────────────────────────────────────────────────


async def get_program_fees(
    db: AsyncSession,
    program_id: UUID,
    academic_year: str | None = None,
    only_currently_valid: bool = False,
) -> list[ProgramFee]:
    stmt = select(ProgramFee).where(ProgramFee.program_id == program_id)

    if academic_year:
        stmt = stmt.where(ProgramFee.academic_year == academic_year)

    today = date.today()
    if only_currently_valid:
        stmt = stmt.where(
            ProgramFee.effective_from <= today,
            or_(
                ProgramFee.effective_to.is_(None),
                ProgramFee.effective_to >= today,
            ),
        )

    stmt = stmt.order_by(ProgramFee.effective_from.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Campaigns ─────────────────────────────────────────────────────────────────


async def list_campaigns(
    db: AsyncSession,
    school_code: SchoolCode | None = None,
    academic_year: str | None = None,
    is_open: bool = True,
) -> list[AdmissionCampaign]:
    stmt = select(AdmissionCampaign)

    if school_code:
        stmt = stmt.where(AdmissionCampaign.school_code == school_code)

    if academic_year:
        stmt = stmt.where(AdmissionCampaign.academic_year == academic_year)

    if is_open:
        stmt = stmt.where(AdmissionCampaign.is_open.is_(True))

    stmt = stmt.order_by(AdmissionCampaign.closes_at.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Detailed Program View ─────────────────────────────────────────────────────


async def get_program_detail(
    db: AsyncSession,
    identifier: str | UUID,
    only_active: bool = True,
) -> PublicProgramDetail:
    program = await get_program_by_id_or_code(db, identifier, only_active=only_active)

    # Fetch prerequisites
    prereqs = await get_program_prerequisites(db, program.id)

    # Fetch fees
    today = date.today()
    raw_fees = await get_program_fees(db, program.id)
    fee_items: list[PublicFeeItem] = []
    for f in raw_fees:
        is_valid = (f.effective_from <= today) and (
            f.effective_to is None or f.effective_to >= today
        )
        fee_item = PublicFeeItem.model_validate(f)
        fee_item.is_current_valid = is_valid
        fee_items.append(fee_item)

    # Fetch campaigns for this school / program
    camp_stmt = select(AdmissionCampaign).where(
        AdmissionCampaign.school_code == program.school_code,
        AdmissionCampaign.is_open.is_(True),
        or_(
            AdmissionCampaign.program_id.is_(None),
            AdmissionCampaign.program_id == program.id,
        ),
    )
    camp_result = await db.execute(camp_stmt)
    raw_campaigns = camp_result.scalars().all()

    return PublicProgramDetail(
        id=program.id,
        code=program.code,
        school_code=program.school_code,  # type: ignore[arg-type]
        title=program.title,
        degree_level=program.degree_level,  # type: ignore[arg-type]
        degree_title=program.degree_title,
        duration_years=program.duration_years,
        study_modes=program.study_modes,
        description=program.description,
        source_document=program.source_document,
        is_active=program.is_active,
        prerequisites=[PublicPrerequisiteItem.model_validate(p) for p in prereqs],
        fees=fee_items,
        active_campaigns=[PublicCampaignItem.model_validate(c) for c in raw_campaigns],
    )


# ── Compare Programs (SA-19) ──────────────────────────────────────────────────


async def compare_programs(
    db: AsyncSession,
    identifiers: list[str],
) -> list[ProgramCompareItem]:
    if not identifiers:
        return []

    # Maximum 4 programs compared at once
    identifiers = identifiers[:4]
    comparison_items: list[ProgramCompareItem] = []

    today = date.today()
    for ident in identifiers:
        try:
            prog = await get_program_by_id_or_code(db, ident, only_active=True)
            prereqs = await get_program_prerequisites(db, prog.id)
            raw_fees = await get_program_fees(db, prog.id)

            fee_items: list[PublicFeeItem] = []
            for f in raw_fees:
                is_valid = (f.effective_from <= today) and (
                    f.effective_to is None or f.effective_to >= today
                )
                fee_item = PublicFeeItem.model_validate(f)
                fee_item.is_current_valid = is_valid
                fee_items.append(fee_item)

            comparison_items.append(
                ProgramCompareItem(
                    program=PublicProgramSummary.model_validate(prog),
                    prerequisites=[PublicPrerequisiteItem.model_validate(p) for p in prereqs],
                    fees=fee_items,
                )
            )
        except HTTPException:
            continue

    return comparison_items
