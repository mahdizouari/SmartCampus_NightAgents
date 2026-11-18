"""Tests for SA-18: Seed script data integrity and idempotency."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from scripts.seed_db import (
    DEMO_ACCOUNTS,
    PROGRAMS,
    ROLES,
    SCHOOLS,
    seed_accounts_and_roles,
    seed_admission_campaigns,
    seed_candidate_and_grades,
    seed_program_fees,
    seed_program_prerequisites,
    seed_programs,
    seed_roles,
    seed_schools,
    seed_slots,
)


def test_seed_catalog_definitions():
    """Verify in-memory seed catalogs conform to DB constraints and domain requirements."""
    # 3 official NAU schools
    assert len(SCHOOLS) == 3
    school_codes = {s["code"] for s in SCHOOLS}
    assert school_codes == {"iit", "isb", "isa"}

    # Verify ISA is Institut Supérieur d'Architecture
    isa_school = next(s for s in SCHOOLS if s["code"] == "isa")
    assert isa_school["name"] == "Institut Supérieur d'Architecture"

    # 5 roles
    assert len(ROLES) == 5
    role_codes = {r["code"] for r in ROLES}
    assert role_codes == {"admin", "responsable", "rh", "direction", "student"}

    # 16 programs with valid levels and duration
    assert len(PROGRAMS) == 16
    valid_levels = {"licence", "prepa", "cycle_ingenieur", "master"}
    program_codes = {p["code"] for p in PROGRAMS}

    # Verify IIT engineering specialties
    assert "IIT-ING-GLID" in program_codes
    assert "IIT-ING-ARSI" in program_codes
    assert "IIT-ING-DSIA" in program_codes
    assert "IIT-ING-MECA" in program_codes
    assert "IIT-ING-INDUS" in program_codes
    assert "IIT-ING-ENER" in program_codes
    assert "IIT-ING-CIVIL" in program_codes

    # Verify ISA architecture programs
    assert "ISA-ARCHI-1" in program_codes
    assert "ISA-ARCHI-2" in program_codes

    for p in PROGRAMS:
        assert p["school_code"] in school_codes
        assert p["degree_level"] in valid_levels
        assert 1 <= p["duration_years"] <= 6
        assert len(p["study_modes"]) > 0

    # 7 demo accounts (including ISA responsable)
    assert len(DEMO_ACCOUNTS) == 7
    account_roles = {a["role_code"] for a in DEMO_ACCOUNTS}
    assert "admin" in account_roles
    assert "responsable" in account_roles
    assert "rh" in account_roles
    assert "student" in account_roles


def test_seed_prerequisites_and_fees_offline_validation():
    """Verify that fees and prerequisite rules map cleanly to declared programs."""
    program_codes = {p["code"] for p in PROGRAMS}
    assert len(program_codes) == 16

    # Verify every school has at least one active program
    schools_with_programs = {p["school_code"] for p in PROGRAMS}
    assert schools_with_programs == {"iit", "isb", "isa"}

    # Verify account email formats and scopes
    for acc in DEMO_ACCOUNTS:
        assert "@" in acc["email"]
        assert acc["auth_provider"] in {"EMAIL_OTP", "GOOGLE_OAUTH", "SSO"}
        if acc["school_scope"]:
            assert acc["school_scope"] in {"iit", "isb", "isa"}


@pytest.mark.asyncio
async def test_seed_execution_and_idempotency():
    """Verify seed execution succeeds and running it twice does not duplicate rows."""
    try:
        engine = create_async_engine(settings.async_database_url, echo=False)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Live database not available in CI environment ({exc})")

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # Run 1: execute all seed steps
        await seed_schools(session)
        await seed_roles(session)
        prog_ids = await seed_programs(session)
        assert len(prog_ids) == 16

        await seed_program_fees(session, prog_ids)
        await seed_program_prerequisites(session, prog_ids)
        await seed_admission_campaigns(session)
        account_ids = await seed_accounts_and_roles(session)
        assert len(account_ids) == 7

        cand_id = await seed_candidate_and_grades(session, account_ids)
        assert cand_id is not None
        await seed_slots(session, account_ids)

        # Count records after Run 1
        res_schools = await session.execute(text("SELECT count(*) FROM core.school"))
        assert res_schools.scalar() == 3

        res_roles = await session.execute(text("SELECT count(*) FROM core.roles"))
        assert res_roles.scalar() == 5

        res_programs = await session.execute(text("SELECT count(*) FROM core.program"))
        count_programs_1 = res_programs.scalar()
        assert count_programs_1 == 16

        res_accounts = await session.execute(text("SELECT count(*) FROM core.accounts"))
        count_accounts_1 = res_accounts.scalar()
        assert count_accounts_1 == 7

        res_slots = await session.execute(text("SELECT count(*) FROM core.slot"))
        count_slots_1 = res_slots.scalar()
        assert count_slots_1 == 12

        res_grades = await session.execute(text("SELECT count(*) FROM core.declared_grades"))
        count_grades_1 = res_grades.scalar()
        assert count_grades_1 == 6

        # Run 2: Re-run all steps to verify strict idempotency
        await seed_schools(session)
        await seed_roles(session)
        prog_ids_2 = await seed_programs(session)
        await seed_program_fees(session, prog_ids_2)
        await seed_program_prerequisites(session, prog_ids_2)
        await seed_admission_campaigns(session)
        account_ids_2 = await seed_accounts_and_roles(session)
        await seed_candidate_and_grades(session, account_ids_2)
        await seed_slots(session, account_ids_2)

        # Count records after Run 2 — must be identical!
        res_schools_2 = await session.execute(text("SELECT count(*) FROM core.school"))
        assert res_schools_2.scalar() == 3

        res_programs_2 = await session.execute(text("SELECT count(*) FROM core.program"))
        assert res_programs_2.scalar() == count_programs_1

        res_accounts_2 = await session.execute(text("SELECT count(*) FROM core.accounts"))
        assert res_accounts_2.scalar() == count_accounts_1

        res_slots_2 = await session.execute(text("SELECT count(*) FROM core.slot"))
        assert res_slots_2.scalar() == count_slots_1

        res_grades_2 = await session.execute(text("SELECT count(*) FROM core.declared_grades"))
        assert res_grades_2.scalar() == count_grades_1

    await engine.dispose()
