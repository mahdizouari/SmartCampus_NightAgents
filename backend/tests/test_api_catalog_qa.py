"""QA automated tests for Catalog Consultation endpoints (SA-21 & SA-22).

Verifies public endpoints, query filters, search, program comparisons, and edge cases.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status

from app.core.database import get_db
from app.main import app
from app.schemas.catalog import (
    ProgramCompareItem,
    PublicCampaignItem,
    PublicFeeItem,
    PublicPrerequisiteItem,
    PublicProgramDetail,
    PublicProgramSummary,
    PublicSchoolResponse,
)

SAMPLE_PROGRAM_ID = UUID("11111111-1111-1111-1111-111111111111")

MOCK_SCHOOLS = [
    PublicSchoolResponse(
        code="iit",
        name="Institut International de Technologie",
        is_extension=False,
        is_active=True,
    ),
    PublicSchoolResponse(
        code="isb",
        name="International School of Business",
        is_extension=False,
        is_active=True,
    ),
    PublicSchoolResponse(
        code="isa",
        name="Institut Supérieur d'Architecture",
        is_extension=True,
        is_active=True,
    ),
]

MOCK_PROGRAM_SUMMARY = PublicProgramSummary(
    id=SAMPLE_PROGRAM_ID,
    code="IIT-ING-GLID",
    school_code="iit",
    title="Cycle Ingénieur — Génie Logiciel & Informatique Décisionnelle",
    degree_level="cycle_ingenieur",
    degree_title="Ingénieur en Génie Logiciel",
    duration_years=3,
    study_modes=["PRESENTIEL"],
    description="Formation ingénieur en génie logiciel et BI.",
    source_document="TODO(corpus): IIT_catalogue_2026.pdf",
    is_active=True,
)

MOCK_PROGRAM = PublicProgramDetail(
    id=SAMPLE_PROGRAM_ID,
    code="IIT-ING-GLID",
    school_code="iit",
    title="Cycle Ingénieur — Génie Logiciel & Informatique Décisionnelle",
    degree_level="cycle_ingenieur",
    degree_title="Ingénieur en Génie Logiciel",
    duration_years=3,
    study_modes=["PRESENTIEL"],
    description="Formation ingénieur en génie logiciel et BI.",
    source_document="TODO(corpus): IIT_catalogue_2026.pdf",
    is_active=True,
    fees=[],
    prerequisites=[],
    active_campaigns=[],
)


@pytest.fixture(autouse=True)
def _mock_db_dependency():
    """Ensure catalog test queries never touch external databases."""

    async def _dummy_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _dummy_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


def test_list_schools_public_access(client):
    """GET /api/v1/catalog/schools should be publicly accessible without authentication."""
    with patch(
        "app.services.catalog_service.list_schools",
        new=AsyncMock(return_value=MOCK_SCHOOLS),
    ):
        response = client.get("/api/v1/catalog/schools")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 3
        codes = [s["code"] for s in data]
        assert "iit" in codes
        assert "isb" in codes
        assert "isa" in codes


def test_list_programs_filtering(client):
    """GET /api/v1/catalog/programs supports filtering by school and degree level."""
    with patch(
        "app.services.catalog_service.list_programs",
        new=AsyncMock(return_value=(1, [MOCK_PROGRAM_SUMMARY])),
    ):
        response = client.get(
            "/api/v1/catalog/programs",
            params={
                "school_code": "iit",
                "degree_level": "cycle_ingenieur",
                "search": "logiciel",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "total" in data
        assert "items" in data
        assert data["total"] == 1
        assert data["items"][0]["code"] == "IIT-ING-GLID"


def test_list_programs_invalid_degree_level_fails(client):
    """GET /api/v1/catalog/programs rejects invalid degree_level with 422."""
    response = client.get(
        "/api/v1/catalog/programs",
        params={"degree_level": "doctorat_inconnu"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_get_program_detail_success(client):
    """GET /api/v1/catalog/programs/{code} returns full details for a valid program."""
    with patch(
        "app.services.catalog_service.get_program_detail",
        new=AsyncMock(return_value=MOCK_PROGRAM),
    ):
        response = client.get("/api/v1/catalog/programs/IIT-ING-GLID")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["code"] == "IIT-ING-GLID"
        assert data["school_code"] == "iit"
        assert data["duration_years"] == 3


def test_get_program_detail_not_found(client):
    """GET /api/v1/catalog/programs/{code} returns 404 for nonexistent program."""
    with patch(
        "app.services.catalog_service.get_program_detail",
        new=AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Formation introuvable")
        ),
    ):
        response = client.get("/api/v1/catalog/programs/UNKNOWN-CODE-999")
        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_compare_programs_valid(client):
    """GET /api/v1/catalog/programs/compare compares valid programs side by side."""
    compare_mock = [
        ProgramCompareItem(
            program=MOCK_PROGRAM_SUMMARY,
            prerequisites=[],
            fees=[],
        )
    ]
    with patch(
        "app.services.catalog_service.compare_programs",
        new=AsyncMock(return_value=compare_mock),
    ):
        response = client.get(
            "/api/v1/catalog/programs/compare",
            params={"program_ids": ["IIT-ING-GLID"]},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert len(data["programs"]) == 1
        assert data["programs"][0]["program"]["code"] == "IIT-ING-GLID"


def test_get_program_prerequisites(client):
    """GET /api/v1/catalog/programs/{id}/prerequisites returns admission rules."""
    prereq_mock = [
        PublicPrerequisiteItem(
            id=uuid4(),
            program_id=SAMPLE_PROGRAM_ID,
            entry_track="bac",
            required_diploma="Baccalauréat Scientifique",
            min_average=Decimal("12.0"),
            yaml_rule_key="IIT_BAC_MATH",
            notes="Minimum 12/20 au Baccalauréat",
            source_document="TODO(corpus): IIT_catalogue_2026.pdf",
        )
    ]
    with (
        patch(
            "app.services.catalog_service.get_program_by_id_or_code",
            new=AsyncMock(return_value=MOCK_PROGRAM),
        ),
        patch(
            "app.services.catalog_service.get_program_prerequisites",
            new=AsyncMock(return_value=prereq_mock),
        ),
    ):
        response = client.get("/api/v1/catalog/programs/IIT-ING-GLID/prerequisites")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["entry_track"] == "bac"
        assert float(data[0]["min_average"]) == 12.0


def test_get_program_fees(client):
    """GET /api/v1/catalog/programs/{id}/fees returns official tuition schedules."""
    fee_mock = [
        PublicFeeItem(
            id=uuid4(),
            program_id=SAMPLE_PROGRAM_ID,
            academic_year="2026-2027",
            tuition_amount=Decimal("7500.00"),
            currency="TND",
            payment_schedule="3 tranches",
            effective_from=date(2026, 9, 1),
            effective_to=None,
            is_current_valid=True,
            source_document="TODO(corpus): IIT_tarifs_2026.pdf",
        )
    ]
    with (
        patch(
            "app.services.catalog_service.get_program_by_id_or_code",
            new=AsyncMock(return_value=MOCK_PROGRAM),
        ),
        patch(
            "app.services.catalog_service.get_program_fees",
            new=AsyncMock(return_value=fee_mock),
        ),
    ):
        response = client.get("/api/v1/catalog/programs/IIT-ING-GLID/fees")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert float(data[0]["tuition_amount"]) == 7500.0
        assert data[0]["currency"] == "TND"


def test_list_campaigns(client):
    """GET /api/v1/catalog/campaigns returns active admission campaigns."""
    campaign_mock = [
        PublicCampaignItem(
            id=uuid4(),
            school_code="iit",
            program_id=None,
            academic_year="2026-2027",
            phase_name="Phase Principale",
            opens_at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            closes_at=datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC),
            is_open=True,
            source_document="TODO(corpus): Calendrier_2026.pdf",
        )
    ]
    with patch(
        "app.services.catalog_service.list_campaigns",
        new=AsyncMock(return_value=campaign_mock),
    ):
        response = client.get("/api/v1/catalog/campaigns")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["school_code"] == "iit"
        assert data[0]["is_open"] is True
