"""Async CRUD and audit service for academic catalog administration (SA-20)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActor
from app.models.catalog import (
    AdmissionCampaign,
    Program,
    ProgramFee,
    ProgramPrerequisite,
    School,
)
from app.models.users import AuditLog
from app.schemas.catalog_management import (
    CampaignCreate,
    CampaignUpdate,
    FeeCreate,
    FeeUpdate,
    PrerequisiteCreate,
    PrerequisiteUpdate,
    ProgramCreate,
    ProgramUpdate,
    SchoolCreate,
    SchoolUpdate,
)


async def _get_or_404[ModelT](
    db: AsyncSession,
    model: type[ModelT],
    pk: Any,
    label: str,
) -> ModelT:
    obj = await db.get(model, pk)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} '{pk}' was not found.",
        )
    return obj


def _record_audit(
    db: AsyncSession,
    actor: CurrentActor,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="ADMIN",
            actor_id=actor.account_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=metadata or {},
        )
    )


async def _commit_and_refresh[ModelT](
    db: AsyncSession,
    obj: ModelT,
    conflict_msg: str,
) -> ModelT:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_msg,
        ) from exc
    await db.refresh(obj)
    return obj


async def _delete_and_commit(
    db: AsyncSession,
    obj: Any,
    actor: CurrentActor,
    action: str,
    entity_type: str,
    entity_id: UUID,
    conflict_msg: str,
) -> None:
    await db.delete(obj)
    _record_audit(db, actor, action, entity_type, entity_id=entity_id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_msg,
        ) from exc


# ── Schools ──────────────────────────────────────────────────────────────────


async def create_school(
    db: AsyncSession,
    payload: SchoolCreate,
    actor: CurrentActor,
) -> School:
    existing = await db.get(School, payload.code)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"School '{payload.code}' already exists.",
        )
    school = School(**payload.model_dump())
    db.add(school)
    _record_audit(
        db,
        actor,
        "catalog.school.create",
        "school",
        metadata={"school_code": school.code},
    )
    return await _commit_and_refresh(db, school, f"School '{payload.code}' already exists.")


async def update_school(
    db: AsyncSession,
    school_code: str,
    payload: SchoolUpdate,
    actor: CurrentActor,
) -> School:
    school = await _get_or_404(db, School, school_code, "School")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(school, field, value)
    school.updated_at = datetime.now(UTC)
    _record_audit(
        db,
        actor,
        "catalog.school.update",
        "school",
        metadata={"school_code": school.code, "fields": sorted(updates.keys())},
    )
    return await _commit_and_refresh(db, school, f"Conflict updating school '{school_code}'.")


# ── Programs ─────────────────────────────────────────────────────────────────


async def create_program(
    db: AsyncSession,
    payload: ProgramCreate,
    actor: CurrentActor,
) -> Program:
    await _get_or_404(db, School, payload.school_code, "School")
    dup = await db.execute(select(Program.id).where(Program.code == payload.code))
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Program code '{payload.code}' already exists.",
        )
    program = Program(**payload.model_dump())
    db.add(program)
    await db.flush()
    _record_audit(
        db,
        actor,
        "catalog.program.create",
        "program",
        entity_id=program.id,
        metadata={"code": program.code, "school_code": program.school_code},
    )
    return await _commit_and_refresh(
        db,
        program,
        f"Program code '{payload.code}' already exists.",
    )


async def update_program(
    db: AsyncSession,
    program_id: UUID,
    payload: ProgramUpdate,
    actor: CurrentActor,
) -> Program:
    program = await _get_or_404(db, Program, program_id, "Program")
    updates = payload.model_dump(exclude_unset=True)
    if "school_code" in updates:
        await _get_or_404(db, School, updates["school_code"], "School")
    for field, value in updates.items():
        setattr(program, field, value)
    program.updated_at = datetime.now(UTC)
    _record_audit(
        db,
        actor,
        "catalog.program.update",
        "program",
        entity_id=program.id,
        metadata={"fields": sorted(updates.keys())},
    )
    return await _commit_and_refresh(
        db,
        program,
        "Program code conflicts with an existing program.",
    )


async def delete_program(
    db: AsyncSession,
    program_id: UUID,
    actor: CurrentActor,
    hard: bool = False,
) -> None:
    program = await _get_or_404(db, Program, program_id, "Program")
    if not hard:
        program.is_active = False
        program.updated_at = datetime.now(UTC)
        _record_audit(
            db,
            actor,
            "catalog.program.deactivate",
            "program",
            entity_id=program.id,
            metadata={"code": program.code},
        )
        await db.commit()
        return

    await _delete_and_commit(
        db,
        program,
        actor,
        "catalog.program.delete",
        "program",
        program.id,
        f"Cannot hard-delete program '{program.code}' while referenced by dossiers or shortlists.",
    )


# ── Prerequisites ────────────────────────────────────────────────────────────


async def create_prerequisite(
    db: AsyncSession,
    program_id: UUID,
    payload: PrerequisiteCreate,
    actor: CurrentActor,
) -> ProgramPrerequisite:
    await _get_or_404(db, Program, program_id, "Program")
    prereq = ProgramPrerequisite(program_id=program_id, **payload.model_dump())
    db.add(prereq)
    await db.flush()
    _record_audit(
        db,
        actor,
        "catalog.prerequisite.create",
        "program_prerequisite",
        entity_id=prereq.id,
        metadata={"program_id": str(program_id), "entry_track": prereq.entry_track},
    )
    return await _commit_and_refresh(db, prereq, "Conflict creating program prerequisite.")


async def update_prerequisite(
    db: AsyncSession,
    prerequisite_id: UUID,
    payload: PrerequisiteUpdate,
    actor: CurrentActor,
) -> ProgramPrerequisite:
    prereq = await _get_or_404(db, ProgramPrerequisite, prerequisite_id, "Prerequisite")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(prereq, field, value)
    _record_audit(
        db,
        actor,
        "catalog.prerequisite.update",
        "program_prerequisite",
        entity_id=prereq.id,
        metadata={"fields": sorted(updates.keys())},
    )
    return await _commit_and_refresh(db, prereq, "Conflict updating program prerequisite.")


async def delete_prerequisite(
    db: AsyncSession,
    prerequisite_id: UUID,
    actor: CurrentActor,
) -> None:
    prereq = await _get_or_404(db, ProgramPrerequisite, prerequisite_id, "Prerequisite")
    await _delete_and_commit(
        db,
        prereq,
        actor,
        "catalog.prerequisite.delete",
        "program_prerequisite",
        prereq.id,
        "Cannot delete prerequisite due to database constraints.",
    )


# ── Program Fees ─────────────────────────────────────────────────────────────


async def create_fee(
    db: AsyncSession,
    program_id: UUID,
    payload: FeeCreate,
    actor: CurrentActor,
) -> ProgramFee:
    await _get_or_404(db, Program, program_id, "Program")
    fee = ProgramFee(program_id=program_id, **payload.model_dump())
    db.add(fee)
    await db.flush()
    _record_audit(
        db,
        actor,
        "catalog.fee.create",
        "program_fee",
        entity_id=fee.id,
        metadata={"program_id": str(program_id), "academic_year": fee.academic_year},
    )
    return await _commit_and_refresh(
        db,
        fee,
        "A fee schedule already exists for this program, academic year, and effective_from date.",
    )


async def update_fee(
    db: AsyncSession,
    fee_id: UUID,
    payload: FeeUpdate,
    actor: CurrentActor,
) -> ProgramFee:
    fee = await _get_or_404(db, ProgramFee, fee_id, "ProgramFee")
    updates = payload.model_dump(exclude_unset=True)
    new_from = updates.get("effective_from", fee.effective_from)
    new_to = updates.get("effective_to", fee.effective_to)
    if new_to is not None and new_to < new_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="effective_to must be on or after effective_from.",
        )
    for field, value in updates.items():
        setattr(fee, field, value)
    _record_audit(
        db,
        actor,
        "catalog.fee.update",
        "program_fee",
        entity_id=fee.id,
        metadata={"fields": sorted(updates.keys())},
    )
    return await _commit_and_refresh(
        db,
        fee,
        "Fee update conflicts with an existing academic year and effective_from entry.",
    )


async def delete_fee(
    db: AsyncSession,
    fee_id: UUID,
    actor: CurrentActor,
) -> None:
    fee = await _get_or_404(db, ProgramFee, fee_id, "ProgramFee")
    await _delete_and_commit(
        db,
        fee,
        actor,
        "catalog.fee.delete",
        "program_fee",
        fee.id,
        "Cannot delete fee schedule due to database constraints.",
    )


# ── Admission Campaigns ──────────────────────────────────────────────────────


async def _validate_campaign_relations(
    db: AsyncSession,
    school_code: str,
    program_id: UUID | None,
) -> None:
    await _get_or_404(db, School, school_code, "School")
    if program_id is not None:
        program = await _get_or_404(db, Program, program_id, "Program")
        if program.school_code != school_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Program '{program_id}' belongs to school '{program.school_code}', "
                    f"not '{school_code}'."
                ),
            )


async def create_campaign(
    db: AsyncSession,
    payload: CampaignCreate,
    actor: CurrentActor,
) -> AdmissionCampaign:
    await _validate_campaign_relations(db, payload.school_code, payload.program_id)
    campaign = AdmissionCampaign(**payload.model_dump())
    db.add(campaign)
    await db.flush()
    _record_audit(
        db,
        actor,
        "catalog.campaign.create",
        "admission_campaign",
        entity_id=campaign.id,
        metadata={"school_code": campaign.school_code, "academic_year": campaign.academic_year},
    )
    return await _commit_and_refresh(db, campaign, "Conflict creating admission campaign.")


async def update_campaign(
    db: AsyncSession,
    campaign_id: UUID,
    payload: CampaignUpdate,
    actor: CurrentActor,
) -> AdmissionCampaign:
    campaign = await _get_or_404(db, AdmissionCampaign, campaign_id, "AdmissionCampaign")
    updates = payload.model_dump(exclude_unset=True)
    new_school = updates.get("school_code", campaign.school_code)
    new_program = updates.get("program_id", campaign.program_id)
    new_opens = updates.get("opens_at", campaign.opens_at)
    new_closes = updates.get("closes_at", campaign.closes_at)

    if new_closes <= new_opens:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="closes_at must be strictly after opens_at.",
        )
    if "school_code" in updates or "program_id" in updates:
        await _validate_campaign_relations(db, new_school, new_program)

    for field, value in updates.items():
        setattr(campaign, field, value)
    _record_audit(
        db,
        actor,
        "catalog.campaign.update",
        "admission_campaign",
        entity_id=campaign.id,
        metadata={"fields": sorted(updates.keys())},
    )
    return await _commit_and_refresh(db, campaign, "Conflict updating admission campaign.")


async def delete_campaign(
    db: AsyncSession,
    campaign_id: UUID,
    actor: CurrentActor,
) -> None:
    campaign = await _get_or_404(db, AdmissionCampaign, campaign_id, "AdmissionCampaign")
    await _delete_and_commit(
        db,
        campaign,
        actor,
        "catalog.campaign.delete",
        "admission_campaign",
        campaign.id,
        "Cannot delete admission campaign due to database constraints.",
    )

