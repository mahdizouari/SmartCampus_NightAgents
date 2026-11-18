"""Public read-only catalog consultation endpoints (SA-21)."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.catalog import (
    DegreeLevel,
    EntryTrack,
    ProgramCompareResponse,
    ProgramListResponse,
    PublicCampaignItem,
    PublicFeeItem,
    PublicPrerequisiteItem,
    PublicProgramDetail,
    PublicProgramSummary,
    PublicSchoolResponse,
    SchoolCode,
)
from app.services import catalog_service as svc

router = APIRouter()


# ── Schools ───────────────────────────────────────────────────────────────────


@router.get(
    "/schools",
    response_model=list[PublicSchoolResponse],
    status_code=status.HTTP_200_OK,
    summary="Consulter les écoles officielles NAU (IIT, ISB, extension ISA)",
)
async def list_schools(
    is_active: bool = Query(True, description="Filtrer les établissements actifs"),
    db: AsyncSession = Depends(get_db),
) -> list[PublicSchoolResponse]:
    return await svc.list_schools(db, is_active=is_active)


# ── Programs List & Filter ────────────────────────────────────────────────────


@router.get(
    "/programs",
    response_model=ProgramListResponse,
    status_code=status.HTTP_200_OK,
    summary="Consulter et filtrer le catalogue officiel des formations",
)
async def list_programs(
    school_code: SchoolCode | None = Query(
        None, description="Filtrer par code école (iit, isb, isa)"
    ),
    degree_level: DegreeLevel | None = Query(
        None, description="Filtrer par niveau de diplôme (licence, prepa, cycle_ingenieur, master)"
    ),
    study_mode: str | None = Query(
        None, description="Filtrer par modalité d'études (ex: plein_temps, alternance)"
    ),
    search: str | None = Query(
        None, description="Recherche textuelle par intitulé, code ou description"
    ),
    is_active: bool = Query(True, description="Afficher uniquement les formations actives"),
    limit: int = Query(50, ge=1, le=100, description="Nombre de formations par page"),
    offset: int = Query(0, ge=0, description="Décalage pour pagination"),
    db: AsyncSession = Depends(get_db),
) -> ProgramListResponse:
    total, programs = await svc.list_programs(
        db=db,
        school_code=school_code,
        degree_level=degree_level,
        study_mode=study_mode,
        search=search,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ProgramListResponse(
        total=total,
        items=[PublicProgramSummary.model_validate(p) for p in programs],
    )


# ── Compare Programs (SA-19) ──────────────────────────────────────────────────


@router.get(
    "/programs/compare",
    response_model=ProgramCompareResponse,
    status_code=status.HTTP_200_OK,
    summary="Comparer 2 à 4 formations côte à côte (prérequis, tarifs, diplômes)",
)
async def compare_programs(
    program_ids: list[str] = Query(
        ...,
        description="Identifiants (UUID ou code, ex: IIT-ING-GL) à comparer (max 4)",
    ),
    db: AsyncSession = Depends(get_db),
) -> ProgramCompareResponse:
    results = await svc.compare_programs(db, program_ids)
    return ProgramCompareResponse(
        count=len(results),
        programs=results,
    )


# ── Single Program Detail ─────────────────────────────────────────────────────


@router.get(
    "/programs/{program_id}",
    response_model=PublicProgramDetail,
    status_code=status.HTTP_200_OK,
    summary="Consulter le détail complet d’une formation (avec prérequis et tarifs officiels)",
)
async def get_program(
    program_id: str,
    db: AsyncSession = Depends(get_db),
) -> PublicProgramDetail:
    return await svc.get_program_detail(db, identifier=program_id, only_active=True)


# ── Prerequisites Consultation ────────────────────────────────────────────────


@router.get(
    "/programs/{program_id}/prerequisites",
    response_model=list[PublicPrerequisiteItem],
    status_code=status.HTTP_200_OK,
    summary="Consulter les conditions d'admission et prérequis d'une formation",
)
async def get_program_prerequisites(
    program_id: str,
    entry_track: EntryTrack | None = Query(
        None, description="Filtrer par filière d'accès (bac, licence, prepa, passerelle)"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[PublicPrerequisiteItem]:
    prog = await svc.get_program_by_id_or_code(db, program_id, only_active=True)
    items = await svc.get_program_prerequisites(db, prog.id, entry_track=entry_track)
    return [PublicPrerequisiteItem.model_validate(i) for i in items]


# ── Tuition Fees & Payment Schedules Consultation ─────────────────────────────


@router.get(
    "/programs/{program_id}/fees",
    response_model=list[PublicFeeItem],
    status_code=status.HTTP_200_OK,
    summary="Consulter la grille tarifaire officielle et modalités de paiement d'une formation",
)
async def get_program_fees(
    program_id: str,
    academic_year: str | None = Query(
        None, description="Filtrer par année universitaire (ex: 2026/2027)"
    ),
    only_currently_valid: bool = Query(
        False,
        description="Restreindre aux tarifs dont la période de validité est actuellement en cours",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[PublicFeeItem]:
    prog = await svc.get_program_by_id_or_code(db, program_id, only_active=True)
    raw_fees = await svc.get_program_fees(
        db,
        prog.id,
        academic_year=academic_year,
        only_currently_valid=only_currently_valid,
    )
    return [PublicFeeItem.model_validate(f) for f in raw_fees]


# ── Admission Campaigns ───────────────────────────────────────────────────────


@router.get(
    "/campaigns",
    response_model=list[PublicCampaignItem],
    status_code=status.HTTP_200_OK,
    summary="Consulter les périodes et campagnes officielles d’admission ouvertes",
)
async def list_campaigns(
    school_code: SchoolCode | None = Query(
        None, description="Filtrer par code établissement (iit, isb, isa)"
    ),
    academic_year: str | None = Query(
        None, description="Filtrer par année universitaire (ex: 2026/2027)"
    ),
    is_open: bool = Query(True, description="Afficher uniquement les campagnes ouvertes"),
    db: AsyncSession = Depends(get_db),
) -> list[PublicCampaignItem]:
    campaigns = await svc.list_campaigns(
        db,
        school_code=school_code,
        academic_year=academic_year,
        is_open=is_open,
    )
    return [PublicCampaignItem.model_validate(c) for c in campaigns]
