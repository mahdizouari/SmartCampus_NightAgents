"""Unit and integration tests for post-review alignment (Pillars 1, 2 & 4).

Covers:
1. Canonical ActorStage (VISITEUR -> CANDIDAT + RESPONSABLE) and transparent role aliases
   (`candidat` <-> `student`, `visiteur` <-> `guest`) across deps, auth, chat, and VISION guard.
2. Seamless Visiteur -> Candidat automatic guest session merge on `/auth/otp/verify` and
   `/auth/oauth/google` (signed cookie, X-Session-ID, cookie clearing, warning on failure).
3. School-scoped Responsable CRUD on catalog programs, prerequisites, fees, and campaigns
   with `AuditLog.actor_type == "RESPONSABLE"` while keeping `/catalog/schools` admin-only.
4. Retirement of `direction` write access on `/api/v1/knowledge/documents` and addition of
   `responsable` to `/api/v1/chat/evaluate-dialect`.
5. Latency optimization (`/no_think` injection for `smartcampus-vision-8b` and `qwen3:8b`)
   and keyless local Whisper (`WHISPER_PROVIDER="local"`).
6. `bge-m3` `1024d` vector alignment and widened PostgreSQL actor check constraints.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import (
    CurrentActor,
    create_access_token,
    is_candidate_role,
    normalize_role_alias,
    resolve_actor_stage,
)
from app.main import app
from app.models.catalog import (
    AdmissionCampaign,
    Program,
    ProgramFee,
    ProgramPrerequisite,
    School,
)
from app.models.documents_rag import (
    DEFAULT_EMBEDDING_DIMENSION,
    CorpusDocument,
    DocumentSyncJob,
    KnowledgeChunk,
)
from app.models.users import (
    Account,
    AccountRole,
    AuditLog,
    Candidate,
    CandidateProfile,
    Role,
)
from app.schemas.agent import AgentToolSpec, ToolExecutionContext
from app.schemas.session import SessionMergeResponse
from app.services import (
    auth_service,
    document_sync_service,
    llm_benchmark_service,
    llm_service,
    whisper_service,
)
from app.services.guest_session_service import create_signed_guest_cookie_token
from app.services.vision_supervisor import DefaultActionGuard

# ---------------------------------------------------------------------------
# Lightweight in-memory AsyncSession doubles
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)


class FakeAlignmentDB:
    """In-memory async DB double supporting Auth, Catalog, and Knowledge routes."""

    def __init__(self) -> None:
        self.accounts: dict[UUID, Account] = {}
        self.roles: dict[int, Role] = {}
        self.account_roles: list[AccountRole] = []
        self.candidates: dict[UUID, Candidate] = {}
        self.profiles: dict[UUID, CandidateProfile] = {}
        self.schools: dict[str, School] = {}
        self.programs: dict[UUID, Program] = {}
        self.prerequisites: dict[UUID, ProgramPrerequisite] = {}
        self.fees: dict[UUID, ProgramFee] = {}
        self.campaigns: dict[UUID, AdmissionCampaign] = {}
        self.documents: dict[UUID, CorpusDocument] = {}
        self.audit_logs: list[AuditLog] = []
        self._role_seq = 1

    def _store_for(self, model: type[Any]) -> dict[Any, Any] | None:
        return {
            Account: self.accounts,
            Role: self.roles,
            Candidate: self.candidates,
            CandidateProfile: self.profiles,
            School: self.schools,
            Program: self.programs,
            ProgramPrerequisite: self.prerequisites,
            ProgramFee: self.fees,
            AdmissionCampaign: self.campaigns,
            CorpusDocument: self.documents,
        }.get(model)

    async def get(self, model: type[Any], pk: Any) -> Any:
        store = self._store_for(model)
        return store.get(pk) if store is not None else None

    def add(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = uuid4()
            self.audit_logs.append(obj)
            return
        if isinstance(obj, Role):
            if obj.id is None:
                obj.id = self._role_seq
                self._role_seq += 1
            self.roles[obj.id] = obj
            return
        if isinstance(obj, AccountRole):
            if obj.id is None:
                obj.id = uuid4()
            self.account_roles.append(obj)
            return
        if isinstance(obj, School):
            obj.created_at = obj.created_at or now
            obj.updated_at = obj.updated_at or now
            self.schools[obj.code] = obj
            return
        if isinstance(obj, CandidateProfile):
            self.profiles[obj.candidate_id] = obj
            return

        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = now
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = now
        store = self._store_for(type(obj))
        if store is not None:
            store[obj.id] = obj

    async def delete(self, obj: Any) -> None:
        key = obj.code if isinstance(obj, School) else obj.id
        store = self._store_for(type(obj))
        if store is not None:
            store.pop(key, None)

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        params = stmt.compile().params
        if "core.roles" in sql:
            code = params.get("code_1")
            return _FakeResult([r for r in self.roles.values() if r.code == code])
        if "core.accounts" in sql:
            email = params.get("email_1")
            return _FakeResult([a for a in self.accounts.values() if a.email == email])
        if "core.account_roles" in sql:
            acc_id = params.get("account_id_1")
            return _FakeResult([ar for ar in self.account_roles if ar.account_id == acc_id])
        if "core.candidate" in sql:
            acc_id = params.get("account_id_1")
            return _FakeResult([c for c in self.candidates.values() if c.account_id == acc_id])
        if "core.program" in sql:
            code = params.get("code_1")
            return _FakeResult([p.id for p in self.programs.values() if p.code == code])
        if "knowledge.documents" in sql:
            doc_id = params.get("id_1")
            if doc_id is not None:
                found = self.documents.get(doc_id)
                return _FakeResult([found] if found is not None else [])
        return _FakeResult([])

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_otp():
    auth_service.clear_otp_store_for_tests()
    yield
    auth_service.clear_otp_store_for_tests()


@pytest.fixture
def fake_db() -> FakeAlignmentDB:
    db = FakeAlignmentDB()
    db.add(School(code="iit", name="Institut International de Technologie"))
    db.add(School(code="isb", name="International School of Business"))
    return db


@pytest.fixture
def client(fake_db: FakeAlignmentDB):
    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Pillar 1: ActorStage helpers, role aliases, and VISION DefaultActionGuard
# ---------------------------------------------------------------------------


def test_actor_stage_and_role_alias_helpers():
    assert resolve_actor_stage("guest", is_authenticated=False) == "VISITEUR"
    assert resolve_actor_stage("visiteur", is_authenticated=False) == "VISITEUR"
    assert resolve_actor_stage("student", is_authenticated=False) == "VISITEUR"
    assert resolve_actor_stage("student", is_authenticated=True) == "CANDIDAT"
    assert resolve_actor_stage("candidat", is_authenticated=True) == "CANDIDAT"
    assert resolve_actor_stage("responsable", is_authenticated=True) == "RESPONSABLE"
    assert resolve_actor_stage("admin", is_authenticated=True) == "RESPONSABLE"

    assert normalize_role_alias("candidat") == "student"
    assert normalize_role_alias("student") == "student"
    assert normalize_role_alias("visiteur") == "guest"
    assert normalize_role_alias("guest") == "guest"
    assert normalize_role_alias("responsable") == "responsable"

    assert is_candidate_role("candidat") is True
    assert is_candidate_role("student") is True
    assert is_candidate_role("responsable") is False
    assert is_candidate_role("visiteur") is False

    cand_actor = CurrentActor(account_id=str(uuid4()), role="candidat", candidate_id=str(uuid4()))
    resp_actor = CurrentActor(account_id=str(uuid4()), role="responsable", school_scope="iit")
    assert cand_actor.actor_stage == "CANDIDAT"
    assert resp_actor.actor_stage == "RESPONSABLE"


@pytest.mark.asyncio
async def test_vision_action_guard_anti_idor_for_candidat_alias():
    guard = DefaultActionGuard()
    tool = AgentToolSpec(
        name="submit_preinscription_tool",
        description="Submit authenticated candidate preinscription.",
        allowed_sub_agents=("1.6_Registrar",),
        permission_level="AUTH_STATE_CHANGE",
    )
    own_id = str(uuid4())
    other_id = str(uuid4())
    ctx = ToolExecutionContext(
        session_id=own_id,
        candidate_id=own_id,
        is_authenticated=True,
        actor_role="candidat",
        candidate_status="AUTHENTICATED",
    )

    blocked = await guard.authorize(tool, {"candidate_id": other_id}, ctx)
    assert blocked.allowed is False
    assert blocked.audit_required is True
    assert "Anti-IDOR" in (blocked.reason or "")

    allowed = await guard.authorize(tool, {"candidate_id": own_id}, ctx)
    assert allowed.allowed is True


def test_vision_system_prompt_welcomes_visiteur_candidat_and_parents():
    prompt = llm_service.VISION_SYSTEM_PROMPT
    assert "Visiteur" in prompt
    assert "Candidat" in prompt
    assert "parent" in prompt.lower()
    assert "Responsable" in prompt


# ---------------------------------------------------------------------------
# Pillar 1: Responsable school-scoped catalog CRUD & Direction write retirement
# ---------------------------------------------------------------------------


def test_responsable_school_scoped_catalog_crud_and_audit(
    client: TestClient, fake_db: FakeAlignmentDB
):
    resp_iit = create_access_token(
        account_id=str(uuid4()),
        role="responsable",
        school_scope="iit",
        email="resp.iit@nau.tn",
    )
    headers = {"Authorization": f"Bearer {resp_iit}"}

    # 1. Top-level /schools creation remains admin-only (403 for responsable)
    school_res = client.post(
        "/api/v1/catalog/schools",
        json={"code": "isa", "name": "ISA", "is_extension": True},
        headers=headers,
    )
    assert school_res.status_code == 403

    # 2. IIT Responsable CAN manage programs, prerequisites, fees, and campaigns for IIT
    prog_res = client.post(
        "/api/v1/catalog/programs",
        json={
            "school_code": "iit",
            "code": "IIT-TEST-GLID",
            "title": "Génie Logiciel Test",
            "degree_level": "cycle_ingenieur",
            "degree_title": "Diplôme National d'Ingénieur",
            "duration_years": 3,
            "source_document": "iit_official_guide_2026.md",
        },
        headers=headers,
    )
    assert prog_res.status_code == 201
    prog_id = prog_res.json()["id"]

    patch_res = client.patch(
        f"/api/v1/catalog/programs/{prog_id}",
        json={"title": "Génie Logiciel Mis à Jour"},
        headers=headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["title"] == "Génie Logiciel Mis à Jour"

    prereq_res = client.post(
        f"/api/v1/catalog/programs/{prog_id}/prerequisites",
        json={
            "entry_track": "bac",
            "required_diploma": "Baccalauréat Mathématiques",
            "source_document": "iit_official_guide_2026.md",
        },
        headers=headers,
    )
    assert prereq_res.status_code == 201
    prereq_id = prereq_res.json()["id"]

    fee_res = client.post(
        f"/api/v1/catalog/programs/{prog_id}/fees",
        json={
            "academic_year": "2026-2027",
            "tuition_amount": "7500.00",
            "currency": "TND",
            "effective_from": str(date(2026, 9, 1)),
            "source_document": "iit_official_fees_2026.md",
        },
        headers=headers,
    )
    assert fee_res.status_code == 201
    fee_id = fee_res.json()["id"]

    now = datetime.now(UTC)
    camp_res = client.post(
        "/api/v1/catalog/campaigns",
        json={
            "school_code": "iit",
            "program_id": prog_id,
            "academic_year": "2026-2027",
            "phase_name": "Session Principale IIT",
            "opens_at": now.isoformat(),
            "closes_at": (now + timedelta(days=90)).isoformat(),
            "is_open": True,
            "source_document": "iit_official_calendar_2026.md",
        },
        headers=headers,
    )
    assert camp_res.status_code == 201
    camp_id = camp_res.json()["id"]

    # Audit entries must record actor_type == "RESPONSABLE"
    assert len(fake_db.audit_logs) >= 5
    assert all(log.actor_type == "RESPONSABLE" for log in fake_db.audit_logs)

    # 3. Cross-school isolation: IIT Responsable receives 403 on ISB resources
    cross_prog = client.post(
        "/api/v1/catalog/programs",
        json={
            "school_code": "isb",
            "code": "ISB-FORBIDDEN",
            "title": "Finance ISB",
            "degree_level": "licence",
            "degree_title": "Licence Nationale",
            "duration_years": 3,
            "source_document": "isb_guide.md",
        },
        headers=headers,
    )
    assert cross_prog.status_code == 403

    move_cross = client.patch(
        f"/api/v1/catalog/programs/{prog_id}",
        json={"school_code": "isb"},
        headers=headers,
    )
    assert move_cross.status_code == 403

    isb_prog = Program(
        id=uuid4(),
        school_code="isb",
        code="ISB-FIN-1",
        title="Licence Finance",
        degree_level="licence",
        degree_title="Licence Nationale",
        duration_years=3,
        source_document="isb_guide.md",
    )
    fake_db.add(isb_prog)
    isb_prereq = ProgramPrerequisite(
        id=uuid4(),
        program_id=isb_prog.id,
        entry_track="bac",
        required_diploma="Baccalauréat Économie",
        source_document="isb_guide.md",
    )
    fake_db.add(isb_prereq)
    isb_fee = ProgramFee(
        id=uuid4(),
        program_id=isb_prog.id,
        academic_year="2026-2027",
        tuition_amount=Decimal("6000.00"),
        currency="TND",
        effective_from=date(2026, 9, 1),
        source_document="isb_fees.md",
    )
    fake_db.add(isb_fee)
    isb_camp = AdmissionCampaign(
        id=uuid4(),
        school_code="isb",
        program_id=isb_prog.id,
        academic_year="2026-2027",
        phase_name="ISB Session",
        opens_at=now,
        closes_at=now + timedelta(days=60),
        is_open=True,
        source_document="isb_calendar.md",
    )
    fake_db.add(isb_camp)

    assert (
        client.patch(
            f"/api/v1/catalog/prerequisites/{isb_prereq.id}",
            json={"required_diploma": "Bac+1"},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/catalog/fees/{isb_fee.id}", headers=headers).status_code == 403
    )
    assert (
        client.delete(f"/api/v1/catalog/campaigns/{isb_camp.id}", headers=headers).status_code
        == 403
    )

    # Cleanup own-school deletions succeed (204)
    del_prereq = client.delete(f"/api/v1/catalog/prerequisites/{prereq_id}", headers=headers)
    assert del_prereq.status_code == 204
    assert client.delete(f"/api/v1/catalog/fees/{fee_id}", headers=headers).status_code == 204
    assert client.delete(f"/api/v1/catalog/campaigns/{camp_id}", headers=headers).status_code == 204
    assert client.delete(f"/api/v1/catalog/programs/{prog_id}", headers=headers).status_code == 204


def test_direction_retired_from_knowledge_write_and_responsable_can_evaluate_dialect(
    client: TestClient, fake_db: FakeAlignmentDB
):
    doc_id = uuid4()
    fake_db.add(
        CorpusDocument(
            id=doc_id,
            code="IIT_GUIDE_2026",
            title="Guide IIT",
            school="iit",
            category="brochure",
            status="DRAFT",
            created_by=uuid4(),
        )
    )
    direction_token = create_access_token(account_id=str(uuid4()), role="direction")
    patch_res = client.patch(
        f"/api/v1/knowledge/documents/{doc_id}",
        json={"title": "Attempted Direction Edit"},
        headers={"Authorization": f"Bearer {direction_token}"},
    )
    assert patch_res.status_code == 403

    resp_token = create_access_token(
        account_id=str(uuid4()), role="responsable", school_scope="iit"
    )
    eval_res = client.get(
        "/api/v1/chat/evaluate-dialect",
        headers={"Authorization": f"Bearer {resp_token}"},
    )
    assert eval_res.status_code == 200
    assert "total_cases" in eval_res.json()
    assert eval_res.json()["total_cases"] >= 70


# ---------------------------------------------------------------------------
# Pillar 2: Seamless Visiteur -> Candidat Auth Merge & Chat ActorStage
# ---------------------------------------------------------------------------


def test_otp_verify_auto_merges_signed_guest_cookie_and_clears_cookie(
    client: TestClient, fake_db: FakeAlignmentDB
):
    send_res = client.post(
        "/api/v1/auth/otp/send",
        json={"channel": "email", "email": "visiteur.merge@nau.tn"},
    )
    assert send_res.status_code == 200
    otp_code = send_res.json()["dev_otp_code"]

    guest_sid = uuid4()
    signed_cookie, _, _ = create_signed_guest_cookie_token(guest_sid)

    async def _fake_merge(db, *, actor, payload, request=None, response=None):
        assert request is not None
        assert request.cookies.get(settings.GUEST_SESSION_COOKIE_NAME) == signed_cookie
        if response is not None:
            response.delete_cookie(key=settings.GUEST_SESSION_COOKIE_NAME, path="/")
        return SessionMergeResponse(
            candidate_id=UUID(actor.candidate_id),
            account_id=UUID(actor.account_id),
            anonymous_session_id=guest_sid,
            actor_stage="CANDIDAT",
            turns_merged=4,
            declared_profile_synced=True,
            current_status="AUTHENTICATED",
            merged_at=datetime.now(UTC),
        )

    with patch(
        "app.services.guest_session_service.merge_guest_session_to_candidate",
        side_effect=_fake_merge,
    ) as mock_merge:
        verify_res = client.post(
            "/api/v1/auth/otp/verify",
            json={
                "channel": "email",
                "email": "visiteur.merge@nau.tn",
                "code": otp_code,
            },
            cookies={settings.GUEST_SESSION_COOKIE_NAME: signed_cookie},
        )

    assert verify_res.status_code == 200
    body = verify_res.json()
    assert mock_merge.await_count == 1
    assert body["actor_stage"] == "CANDIDAT"
    assert body["user"]["actor_stage"] == "CANDIDAT"
    assert body["session_merged"] is True
    assert body["turns_merged"] == 4
    set_cookie_hdr = verify_res.headers.get("set-cookie", "")
    assert settings.GUEST_SESSION_COOKIE_NAME in set_cookie_hdr
    assert "Max-Age=0" in set_cookie_hdr or "max-age=0" in set_cookie_hdr.lower()


def test_otp_verify_without_guest_session_succeeds_without_merge(
    client: TestClient, fake_db: FakeAlignmentDB
):
    send_res = client.post(
        "/api/v1/auth/otp/send",
        json={"channel": "email", "email": "direct.candidat@nau.tn"},
    )
    otp_code = send_res.json()["dev_otp_code"]

    verify_res = client.post(
        "/api/v1/auth/otp/verify",
        json={"channel": "email", "email": "direct.candidat@nau.tn", "code": otp_code},
    )
    assert verify_res.status_code == 200
    body = verify_res.json()
    assert body["actor_stage"] == "CANDIDAT"
    assert body["session_merged"] is False
    assert body["turns_merged"] == 0


def test_chat_message_exposes_actor_stage_and_candidate_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    # 1. Unauthenticated Visiteur
    guest_res = client.post(
        "/api/v1/chat/message",
        json={"message": "Bonjour, quelles sont les filières à l'IIT ?"},
    )
    assert guest_res.status_code == 200
    assert guest_res.json()["actor_stage"] == "VISITEUR"
    assert guest_res.json()["candidate_status"] in {"ANONYMOUS", "PROFILING"}

    # 2. Authenticated Candidat (using 'candidat' role alias)
    cand_token = create_access_token(
        account_id=str(uuid4()),
        role="candidat",
        candidate_id=str(uuid4()),
        email="cand@nau.tn",
    )
    cand_res = client.post(
        "/api/v1/chat/message",
        json={"message": "Bonjour, je veux suivre mon dossier IIT."},
        headers={"Authorization": f"Bearer {cand_token}"},
    )
    assert cand_res.status_code == 200
    assert cand_res.json()["actor_stage"] == "CANDIDAT"
    assert cand_res.json()["candidate_status"] == "AUTHENTICATED"

    # 3. Authenticated Responsable
    resp_token = create_access_token(
        account_id=str(uuid4()),
        role="responsable",
        school_scope="iit",
        email="resp@nau.tn",
    )
    resp_res = client.post(
        "/api/v1/chat/message",
        json={"message": "Bonjour VISION."},
        headers={"Authorization": f"Bearer {resp_token}"},
    )
    assert resp_res.status_code == 200
    assert resp_res.json()["actor_stage"] == "RESPONSABLE"


# ---------------------------------------------------------------------------
# Pillar 4: Qwen3 /no_think on QLoRA GGUF, Keyless Whisper, and bge-m3 1024d
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_think_injected_for_smartcampus_vision_8b_qlora(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_MODEL", "smartcampus-vision-8b")
    monkeypatch.setattr(settings, "LLM_ENABLE_THINKING", False)

    captured_payloads: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200
        text = "ok"

        def json(self) -> dict[str, Any]:
            return {
                "model": "smartcampus-vision-8b",
                "choices": [
                    {"message": {"content": "<think>internal</think>Réponse concise IIT."}}
                ],
            }

    async def _fake_post(self, url, headers=None, json=None):
        captured_payloads.append(json)
        return _FakeResponse()

    with patch.object(httpx.AsyncClient, "post", new=_fake_post):
        reply, model_used = await llm_service._call_openai_compatible_api(
            [
                {"role": "system", "content": "Tu es VISION."},
                {"role": "user", "content": "Quelles sont les filières IIT ?"},
            ]
        )

    assert model_used == "smartcampus-vision-8b"
    assert reply == "Réponse concise IIT."
    assert len(captured_payloads) == 1
    assert captured_payloads[0]["messages"][0]["content"].endswith("/no_think")

    # Also verify llm_benchmark_service injects /no_think for smartcampus-vision-8b
    bench_client = MagicMock(spec=httpx.AsyncClient)
    bench_client.post = AsyncMock(return_value=_FakeResponse())
    bench_reply, bench_model, _ = await llm_benchmark_service.call_candidate_llm_api(
        "smartcampus-vision-8b",
        [
            {"role": "system", "content": "Tu es VISION."},
            {"role": "user", "content": "Filières IIT ?"},
        ],
        client=bench_client,
    )
    assert bench_reply == "Réponse concise IIT."
    assert bench_model == "smartcampus-vision-8b"
    sent_json = bench_client.post.await_args.kwargs["json"]
    assert sent_json["messages"][0]["content"].endswith("/no_think")

    modelfile = (
        Path(__file__).resolve().parents[2]
        / "ml"
        / "colab"
        / "Modelfile.smartcampus-vision-8b"
    )
    if modelfile.exists():
        assert "/no_think" in modelfile.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_whisper_local_provider_calls_server_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "WHISPER_PROVIDER", "local")
    monkeypatch.setattr(settings, "WHISPER_BASE_URL", "http://local-whisper:8001/v1")
    monkeypatch.setattr(settings, "WHISPER_API_KEY", "")
    monkeypatch.setattr(settings, "STT_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    captured: dict[str, Any] = {}

    class _FakeWhisperResp:
        status_code = 200
        text = '{"text": "Quelles sont les filières IIT ?"}'

        def json(self) -> dict[str, Any]:
            return {"text": "Quelles sont les filières IIT ?"}

    async def _fake_post(self, url, headers=None, data=None, files=None):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        return _FakeWhisperResp()

    upload = UploadFile(
        filename="voice.webm",
        file=io.BytesIO(b"RIFFfakewebmdata"),
        headers={"content-type": "audio/webm"},
    )
    with patch.object(httpx.AsyncClient, "post", new=_fake_post):
        res = await whisper_service.transcribe_audio(upload, mode="fr", campus_scope="IIT")

    assert captured["url"] == "http://local-whisper:8001/v1/audio/transcriptions"
    assert "Authorization" not in captured["headers"]
    assert res.transcript == "Quelles sont les filières IIT ?"
    assert res.requires_review is True


def test_bge_m3_1024d_and_actor_constraints_aligned():
    assert DEFAULT_EMBEDDING_DIMENSION == 1024
    assert document_sync_service.DEFAULT_EMBEDDING_MODEL == "bge-m3"
    assert KnowledgeChunk.__table__.c.embedding.type.dim == 1024
    assert "bge-m3" in str(DocumentSyncJob.__table__.c.embedding_model.server_default.arg)

    role_checks = [
        str(c.sqltext)
        for c in Role.__table__.constraints
        if "roles_code_valid" in str(getattr(c, "name", ""))
    ]
    assert role_checks and "'candidat'" in role_checks[0]

    audit_checks = [
        str(c.sqltext)
        for c in AuditLog.__table__.constraints
        if "audit_actor_type_valid" in str(getattr(c, "name", ""))
    ]
    assert audit_checks
    assert "'VISITEUR'" in audit_checks[0]
    assert "'CANDIDAT'" in audit_checks[0]
