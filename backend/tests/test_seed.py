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
    """Verify in-memory seed catalogs conform to DB constraints and requirements."""
    # 3 official NAU schools
    assert len(SCHOOLS) == 3
    school_codes = {s["code"] for s in SCHOOLS}
    assert school_codes == {"iit", "isb", "isa"}

    # 5 roles
    assert len(ROLES) == 5
    role_codes = {r["code"] for r in ROLES}
    assert role_codes == {"admin", "responsable", "rh", "direction", "student"}

    # 10 programs with valid levels and duration
    assert len(PROGRAMS) == 10
    valid_levels = {"licence", "prepa", "cycle_ingenieur", "master"}
    for p in PROGRAMS:
        assert p["school_code"] in school_codes
        assert p["degree_level"] in valid_levels
        assert 1 <= p["duration_years"] <= 6
        assert len(p["study_modes"]) > 0

    # 6 demo accounts
    assert len(DEMO_ACCOUNTS) == 6
    account_roles = {a["role_code"] for a in DEMO_ACCOUNTS}
    assert "admin" in account_roles
    assert "responsable" in account_roles
    assert "rh" in account_roles
    assert "student" in account_roles


@pytest.mark.asyncio
async def test_seed_execution_and_idempotency():
    """Verify seed execution succeeds and running it twice does not duplicate rows."""
    engine = create_async_engine(settings.async_database_url, echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # Run 1: execute all seed steps
        await seed_schools(session)
        await seed_roles(session)
        prog_ids = await seed_programs(session)
        assert len(prog_ids) == 10

        await seed_program_fees(session, prog_ids)
        await seed_program_prerequisites(session, prog_ids)
        await seed_admission_campaigns(session)
        account_ids = await seed_accounts_and_roles(session)
        assert len(account_ids) == 6

        cand_id = await seed_candidate_and_grades(session, account_ids)
        assert cand_id is not None
        await seed_slots(session, account_ids)

        # Count records after Run 1
        res_schools = await session.execute(text("SELECT count(*) FROM core.school"))
        assert res_schools.scalar() == 3

        res_roles = await session.execute(text("SELECT count(*) FROM core.roles"))
        assert res_roles.scalar() == 5

        res_programs = await session.execute(text("SELECT count(*) FROM core.program"))
        assert res_programs.scalar() == 10

        res_accounts = await session.execute(text("SELECT count(*) FROM core.accounts"))
        assert res_accounts.scalar() == 6

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
        assert res_programs_2.scalar() == 10

        res_accounts_2 = await session.execute(text("SELECT count(*) FROM core.accounts"))
        assert res_accounts_2.scalar() == 6

        res_slots_2 = await session.execute(text("SELECT count(*) FROM core.slot"))
        assert res_slots_2.scalar() == count_slots_1

        res_grades_2 = await session.execute(text("SELECT count(*) FROM core.declared_grades"))
        assert res_grades_2.scalar() == count_grades_1

    await engine.dispose()
