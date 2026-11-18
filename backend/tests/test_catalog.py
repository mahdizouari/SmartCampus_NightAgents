"""Unit and endpoint tests for SA-21 public catalog consultation API."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.catalog import (
    AdmissionCampaign,
    Program,
    ProgramFee,
    ProgramPrerequisite,
    School,
)


class _MockResult:
    def __init__(self, items: list[Any] | None = None, scalar_val: Any = None):
        self._items = items or []
        self._scalar_val = scalar_val

    def scalars(self) -> "_MockResult":
        return self

    def all(self) -> list[Any]:
        return self._items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one(self) -> Any:
        if self._scalar_val is not None:
            return self._scalar_val
        return len(self._items)

    def scalar_one_or_none(self) -> Any:
        if self._scalar_val is not None:
            return self._scalar_val
        return self._items[0] if self._items else None


class FakeCatalogAsyncSession:
    """In-memory AsyncSession double for testing catalog consultation endpoints."""

    def __init__(self) -> None:
        self.schools: dict[str, School] = {}
        self.programs: dict[UUID, Program] = {}
        self.prerequisites: dict[UUID, ProgramPrerequisite] = {}
        self.fees: dict[UUID, ProgramFee] = {}
        self.campaigns: dict[UUID, AdmissionCampaign] = {}

    def seed_default_data(self) -> None:
        now = datetime.now(UTC)

        # Schools
        iit = School(
            code="iit",
            name="Institut International de Technologie",
            is_extension=False,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        isb = School(
            code="isb",
            name="International School of Business",
            is_extension=False,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        isa = School(
            code="isa",
            name="Institut Supérieur Agronomique",
            is_extension=True,
            is_active=False,  # Inactive for testing filtering
            created_at=now,
            updated_at=now,
        )
        self.schools["iit"] = iit
        self.schools["isb"] = isb
        self.schools["isa"] = isa

        # Programs
        p1 = Program(
            id=uuid4(),
            code="IIT-ING-GL",
            school_code="iit",
            title="Cycle Ingénieur en Génie Logiciel",
            degree_level="cycle_ingenieur",
            degree_title="Diplôme National d'Ingénieur",
            duration_years=3,
            study_modes=["plein_temps", "alternance"],
            description="Formation d'ingénieurs spécialisés en développement logiciel et cloud.",
            source_document="Catalogue_IIT_2026.pdf",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        p2 = Program(
            id=uuid4(),
            code="ISB-LIC-BA",
            school_code="isb",
            title="Licence Business Administration",
            degree_level="licence",
            degree_title="Licence Nationale",
            duration_years=3,
            study_modes=["plein_temps"],
            description="Management et gestion d'entreprise internationale.",
            source_document="Catalogue_ISB_2026.pdf",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        p3_inactive = Program(
            id=uuid4(),
            code="IIT-ING-OLD",
            school_code="iit",
            title="Ancienne Filière Désactivée",
            degree_level="cycle_ingenieur",
            degree_title="Diplôme",
            duration_years=3,
            study_modes=["plein_temps"],
            description="Archive",
            source_document="Old.pdf",
            is_active=False,
            created_at=now,
            updated_at=now,
        )
        self.programs[p1.id] = p1
        self.programs[p2.id] = p2
        self.programs[p3_inactive.id] = p3_inactive

        # Prerequisites for p1
        pr1 = ProgramPrerequisite(
            id=uuid4(),
            program_id=p1.id,
            entry_track="prepa",
            required_diploma="Cycle Préparatoire Scientifique / MP / PT",
            min_average=Decimal("10.00"),
            yaml_rule_key="rule_prepa_admissibility",
            notes="Admission directe sur concours ou dossier.",
            source_document="Catalogue_IIT_2026.pdf",
        )
        pr2 = ProgramPrerequisite(
            id=uuid4(),
            program_id=p1.id,
            entry_track="licence",
            required_diploma="Licence Informatique ou équivalent",
            min_average=Decimal("12.50"),
            yaml_rule_key="rule_passerelle_licence",
            notes="Passerelle en 1ère année d'ingénieur.",
            source_document="Catalogue_IIT_2026.pdf",
        )
        self.prerequisites[pr1.id] = pr1
        self.prerequisites[pr2.id] = pr2

        # Fees for p1
        f1 = ProgramFee(
            id=uuid4(),
            program_id=p1.id,
            academic_year="2026/2027",
            tuition_amount=Decimal("7800.00"),
            currency="TND",
            payment_schedule="Paiement en 3 tranches (Septembre, Décembre, Février).",
            effective_from=date(2026, 1, 1),
            effective_to=date(2027, 8, 31),
            source_document="Grille_Tarifs_2026_2027.pdf",
        )
        self.fees[f1.id] = f1

        # Campaign for iit
        c1 = AdmissionCampaign(
            id=uuid4(),
            school_code="iit",
            program_id=None,
            academic_year="2026/2027",
            phase_name="Session Principale Automne",
            opens_at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            closes_at=datetime(2026, 9, 15, 23, 59, tzinfo=UTC),
            is_open=True,
            source_document="Calendrier_Admissions_2026.pdf",
        )
        c2_closed = AdmissionCampaign(
            id=uuid4(),
            school_code="iit",
            program_id=None,
            academic_year="2025/2026",
            phase_name="Session Clôturée",
            opens_at=datetime(2025, 6, 1, 0, 0, tzinfo=UTC),
            closes_at=datetime(2025, 9, 15, 23, 59, tzinfo=UTC),
            is_open=False,
            source_document="Calendrier_Admissions_2025.pdf",
        )
        self.campaigns[c1.id] = c1
        self.campaigns[c2_closed.id] = c2_closed

    async def execute(self, stmt: Any) -> _MockResult:
        compiled = str(stmt).lower()

        # Count on programs
        if "count" in compiled and "core.program" in compiled:
            active_progs = [p for p in self.programs.values() if p.is_active]
            return _MockResult(scalar_val=len(active_progs))

        # Query on School
        if "core.school" in compiled:
            active_only = "is_active = true" in compiled or "is_active is true" in compiled
            items = [s for s in self.schools.values() if not active_only or s.is_active]
            return _MockResult(items=sorted(items, key=lambda s: s.code))

        # Query on ProgramPrerequisite
        if "core.program_prerequisite" in compiled:
            params = stmt.compile().params
            prog_id = params.get("program_id_1")
            entry_tr = params.get("entry_track_1")
            items = list(self.prerequisites.values())
            if prog_id:
                items = [p for p in items if str(p.program_id) == str(prog_id)]
            if entry_tr:
                items = [p for p in items if p.entry_track == entry_tr]
            return _MockResult(items=items)

        # Query on ProgramFee
        if "core.program_fee" in compiled:
            params = stmt.compile().params
            prog_id = params.get("program_id_1")
            year = params.get("academic_year_1")
            items = list(self.fees.values())
            if prog_id:
                items = [f for f in items if str(f.program_id) == str(prog_id)]
            if year:
                items = [f for f in items if f.academic_year == year]
            return _MockResult(items=items)

        # Query on AdmissionCampaign
        if "core.admission_campaign" in compiled:
            params = stmt.compile().params
            school_code = params.get("school_code_1")
            year = params.get("academic_year_1")
            items = list(self.campaigns.values())
            if "is_open = true" in compiled or "is_open is true" in compiled:
                items = [c for c in items if c.is_open]
            if school_code:
                items = [c for c in items if c.school_code == school_code]
            if year:
                items = [c for c in items if c.academic_year == year]
            return _MockResult(items=items)

        # Query on Program
        if "core.program" in compiled:
            params = stmt.compile().params
            prog_id = params.get("id_1")
            code_param = params.get("code_1")
            school_code = params.get("school_code_1")
            degree_level = params.get("degree_level_1")

            items = list(self.programs.values())
            if "is_active = true" in compiled or "is_active is true" in compiled:
                items = [p for p in items if p.is_active]
            if prog_id:
                items = [p for p in items if str(p.id) == str(prog_id)]
            if code_param:
                items = [p for p in items if p.code == code_param]
            if school_code:
                items = [p for p in items if p.school_code == school_code]
            if degree_level:
                items = [p for p in items if p.degree_level == degree_level]

            return _MockResult(items=items)

        return _MockResult([])


@pytest.fixture
def fake_catalog_db() -> FakeCatalogAsyncSession:
    db = FakeCatalogAsyncSession()
    db.seed_default_data()
    return db


@pytest.fixture
def client(fake_catalog_db: FakeCatalogAsyncSession):
    async def _override_get_db():
        yield fake_catalog_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


# ── School Endpoints Tests ────────────────────────────────────────────────────


def test_list_schools_public_access(client: TestClient):
    """GET /api/v1/catalog/schools should be publicly accessible without auth."""
    resp = client.get("/api/v1/catalog/schools")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Only active schools by default
    codes = [s["code"] for s in data]
    assert "iit" in codes
    assert "isb" in codes
    assert "isa" not in codes  # isa was seeded inactive


def test_list_schools_include_inactive(client: TestClient):
    resp = client.get("/api/v1/catalog/schools?is_active=false")
    assert resp.status_code == 200
    data = resp.json()
    codes = [s["code"] for s in data]
    assert "isa" in codes


# ── Program Consultation & Filter Tests ───────────────────────────────────────


def test_list_programs_public(client: TestClient):
    """GET /api/v1/catalog/programs lists active programs without auth."""
    resp = client.get("/api/v1/catalog/programs")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert data["total"] >= 2
    # Inactive program is excluded
    titles = [item["title"] for item in data["items"]]
    assert "Cycle Ingénieur en Génie Logiciel" in titles
    assert "Licence Business Administration" in titles
    assert "Ancienne Filière Désactivée" not in titles


def test_list_programs_filter_by_school(client: TestClient):
    resp = client.get("/api/v1/catalog/programs?school_code=iit")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["school_code"] == "iit"


def test_list_programs_filter_by_degree_level(client: TestClient):
    resp = client.get("/api/v1/catalog/programs?degree_level=cycle_ingenieur")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["degree_level"] == "cycle_ingenieur"


# ── Single Program Detail Tests ───────────────────────────────────────────────


def test_get_program_by_code(client: TestClient):
    """GET /api/v1/catalog/programs/{program_id} resolves readable code."""
    resp = client.get("/api/v1/catalog/programs/IIT-ING-GL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "IIT-ING-GL"
    assert data["school_code"] == "iit"
    assert data["degree_level"] == "cycle_ingenieur"
    # Detailed sub-resources are embedded
    assert len(data["prerequisites"]) == 2
    assert len(data["fees"]) == 1
    assert data["fees"][0]["academic_year"] == "2026/2027"
    assert float(data["fees"][0]["tuition_amount"]) == 7800.0


def test_get_program_by_uuid(client: TestClient, fake_catalog_db: FakeCatalogAsyncSession):
    prog_id = [p for p in fake_catalog_db.programs.values() if p.code == "ISB-LIC-BA"][0].id
    resp = client.get(f"/api/v1/catalog/programs/{prog_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "ISB-LIC-BA"


def test_get_program_not_found(client: TestClient):
    resp = client.get("/api/v1/catalog/programs/NON-EXISTENT-CODE")
    assert resp.status_code == 404
    assert "introuvable" in resp.json()["detail"].lower()


# ── Prerequisites & Fees Endpoints ───────────────────────────────────────────


def test_get_program_prerequisites(client: TestClient):
    resp = client.get("/api/v1/catalog/programs/IIT-ING-GL/prerequisites")
    assert resp.status_code == 200
    prereqs = resp.json()
    assert len(prereqs) == 2
    tracks = [p["entry_track"] for p in prereqs]
    assert "prepa" in tracks
    assert "licence" in tracks


def test_get_program_fees(client: TestClient):
    resp = client.get("/api/v1/catalog/programs/IIT-ING-GL/fees")
    assert resp.status_code == 200
    fees = resp.json()
    assert len(fees) == 1
    fee = fees[0]
    assert fee["academic_year"] == "2026/2027"
    assert fee["currency"] == "TND"
    assert fee["source_document"] == "Grille_Tarifs_2026_2027.pdf"


# ── Compare Programs Tests (SA-19) ───────────────────────────────────────────


def test_compare_programs(client: TestClient):
    resp = client.get(
        "/api/v1/catalog/programs/compare?program_ids=IIT-ING-GL&program_ids=ISB-LIC-BA"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    codes = [item["program"]["code"] for item in data["programs"]]
    assert "IIT-ING-GL" in codes
    assert "ISB-LIC-BA" in codes


# ── Admission Campaigns Tests ────────────────────────────────────────────────


def test_list_campaigns(client: TestClient):
    resp = client.get("/api/v1/catalog/campaigns")
    assert resp.status_code == 200
    campaigns = resp.json()
    assert len(campaigns) >= 1
    phases = [c["phase_name"] for c in campaigns]
    assert "Session Principale Automne" in phases
    assert "Session Clôturée" not in phases  # is_open=False excluded by default
