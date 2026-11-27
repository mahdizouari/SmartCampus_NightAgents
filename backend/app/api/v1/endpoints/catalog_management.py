"""Catalog management REST endpoints for Responsable (school-scoped) & Admin (SA-20)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentActor, require_catalog_admin, require_catalog_manager
from app.schemas.catalog_management import (
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    FeeCreate,
    FeeResponse,
    FeeUpdate,
    PrerequisiteCreate,
    PrerequisiteResponse,
    PrerequisiteUpdate,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    SchoolCode,
    SchoolCreate,
    SchoolResponse,
    SchoolUpdate,
)
from app.services import catalog_management_service as svc

router = APIRouter()


# ── Schools ──────────────────────────────────────────────────────────────────


@router.post(
    "/schools",
    response_model=SchoolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a school in the catalog",
)
async def create_school(
    payload: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_admin),
) -> SchoolResponse:
    return await svc.create_school(db, payload, actor)


@router.patch(
    "/schools/{school_code}",
    response_model=SchoolResponse,
    summary="Update school metadata or activation status",
)
async def update_school(
    school_code: SchoolCode,
    payload: SchoolUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_admin),
) -> SchoolResponse:
    return await svc.update_school(db, school_code, payload, actor)


# ── Programs ─────────────────────────────────────────────────────────────────


@router.post(
    "/programs",
    response_model=ProgramResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an academic program",
)
async def create_program(
    payload: ProgramCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> ProgramResponse:
    return await svc.create_program(db, payload, actor)


@router.patch(
    "/programs/{program_id}",
    response_model=ProgramResponse,
    summary="Update an academic program",
)
async def update_program(
    program_id: UUID,
    payload: ProgramUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> ProgramResponse:
    return await svc.update_program(db, program_id, payload, actor)


@router.delete(
    "/programs/{program_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate (default) or hard-delete an academic program",
)
async def delete_program(
    program_id: UUID,
    hard: bool = Query(
        default=False,
        description="Set true to permanently delete instead of soft-deactivating.",
    ),
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> None:
    await svc.delete_program(db, program_id, actor, hard=hard)


# ── Prerequisites ────────────────────────────────────────────────────────────


@router.post(
    "/programs/{program_id}/prerequisites",
    response_model=PrerequisiteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an eligibility prerequisite to a program",
)
async def create_prerequisite(
    program_id: UUID,
    payload: PrerequisiteCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> PrerequisiteResponse:
    return await svc.create_prerequisite(db, program_id, payload, actor)


@router.patch(
    "/prerequisites/{prerequisite_id}",
    response_model=PrerequisiteResponse,
    summary="Update an eligibility prerequisite",
)
async def update_prerequisite(
    prerequisite_id: UUID,
    payload: PrerequisiteUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> PrerequisiteResponse:
    return await svc.update_prerequisite(db, prerequisite_id, payload, actor)


@router.delete(
    "/prerequisites/{prerequisite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an eligibility prerequisite",
)
async def delete_prerequisite(
    prerequisite_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> None:
    await svc.delete_prerequisite(db, prerequisite_id, actor)


# ── Program Fees ─────────────────────────────────────────────────────────────


@router.post(
    "/programs/{program_id}/fees",
    response_model=FeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an official fee schedule to a program",
)
async def create_fee(
    program_id: UUID,
    payload: FeeCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> FeeResponse:
    return await svc.create_fee(db, program_id, payload, actor)


@router.patch(
    "/fees/{fee_id}",
    response_model=FeeResponse,
    summary="Update an official fee schedule",
)
async def update_fee(
    fee_id: UUID,
    payload: FeeUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> FeeResponse:
    return await svc.update_fee(db, fee_id, payload, actor)


@router.delete(
    "/fees/{fee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a fee schedule",
)
async def delete_fee(
    fee_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> None:
    await svc.delete_fee(db, fee_id, actor)


# ── Admission Campaigns ──────────────────────────────────────────────────────


@router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an admission campaign window",
)
async def create_campaign(
    payload: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> CampaignResponse:
    return await svc.create_campaign(db, payload, actor)


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    summary="Update an admission campaign window",
)
async def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> CampaignResponse:
    return await svc.update_campaign(db, campaign_id, payload, actor)


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an admission campaign window",
)
async def delete_campaign(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: CurrentActor = Depends(require_catalog_manager),
) -> None:
    await svc.delete_campaign(db, campaign_id, actor)

