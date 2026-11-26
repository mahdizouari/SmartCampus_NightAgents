"""Pydantic v2 contracts and LangGraph TypedDict state for VISION / JARVIS orchestration (SA-69).

Defines stable, local-first, provider-agnostic interfaces consumed by:
- VISION LangGraph supervisor (`backend/app/services/vision_supervisor.py`, SA-69)
- Agent tool calling & registry (`SA-70`, Mehdi)
- Tool RBAC, Give-or-Keep, HITL, and audit security wrapper (`SA-71`, Mehdi)
- Catalog consultation tools (`SA-73`, Moataz)
- Conversational orientation journey (`SA-74`, Yassine)
- Sub-agent specialized prompt/tool registry (`SA-75`, Melek)
- Transactional pre-inscription / QCM outbox handoff (`SA-82`, `SA-83`, `SA-84`)

Non-negotiable governance invariants (SC V3):
- Humans decide admissions: no agent, node, or tool may set `ADMITTED`, `WAITLIST`, or `REJECTED`.
- Strict VISION <-> JARVIS decoupling: VISION never imports or calls JARVIS directly; it emits
  outbox specifications persisted to `core.outbox` on `PREINSCRIBED` and `QCM_DONE`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.base import CANDIDATE_STATUS_VALUES
from app.schemas.chat import (
    ActionPrompt,
    ChatCitation,
    ChatMessageRequest,
    ChatMessageResponse,
    DetectedDialect,
    LanguageHint,
    VisionSubAgent,
)

# ---------------------------------------------------------------------------
# Canonical Enums & Governance Constants (exact SC V3 / models/base.py names)
# ---------------------------------------------------------------------------

CandidateStatus = Literal[
    "ANONYMOUS",
    "PROFILING",
    "ELIGIBLE",
    "INELIGIBLE",
    "ORIENTED",
    "AUTHENTICATED",
    "PREINSCRIBED",
    "QCM_DONE",
    "SCHEDULED",
    "INTERVIEWED",
    "ADMITTED",
    "WAITLIST",
    "REJECTED",
    "DORMANT",
    "ESCALATED",
    "WITHDRAWN",
]

ActorRole = Literal["guest", "student", "rh", "responsable", "direction", "admin"]
CampusScope = Literal["all", "IIT", "ISB", "ISA"]
SchoolCodeLower = Literal["iit", "isb", "isa"]

ComponentImplementationType = Literal[
    "LLM",
    "LLM+RAG",
    "LLM+rules",
    "VLM",
    "code+LLM",
    "rules",
    "code",
]

# Reference-only canonical names for JARVIS back-office workers (SC V3 §5).
# VISION never imports or invokes JARVIS directly.
JarvisSubAgent = Literal["2.1_Scheduler", "2.2_Messenger", "2.3_Fixer"]

# Hard HITL lock: ONLY a human NAU Responsable may emit these decisions.
FORBIDDEN_AGENT_STATUSES: frozenset[str] = frozenset({"ADMITTED", "WAITLIST", "REJECTED"})

# Statuses VISION is permitted to transition a candidate INTO (excludes human-only
# decisions and JARVIS/interview-owned milestones SCHEDULED, INTERVIEWED, DORMANT, WITHDRAWN).
VISION_ASSIGNABLE_STATUSES: frozenset[str] = frozenset(
    {
        "ANONYMOUS",
        "PROFILING",
        "ELIGIBLE",
        "INELIGIBLE",
        "ORIENTED",
        "AUTHENTICATED",
        "PREINSCRIBED",
        "QCM_DONE",
        "ESCALATED",
    }
)

# Allowed candidate status transitions reachable by VISION front-office steps (1-7).
# Notice ADMITTED / WAITLIST / REJECTED are strictly absent, and SCHEDULED is owned by
# JARVIS 2.1_Scheduler after outbox handoff.
ALLOWED_VISION_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "ANONYMOUS": frozenset(
        {"ANONYMOUS", "PROFILING", "ELIGIBLE", "INELIGIBLE", "ORIENTED", "AUTHENTICATED"}
    ),
    "PROFILING": frozenset(
        {"PROFILING", "ELIGIBLE", "INELIGIBLE", "ORIENTED", "AUTHENTICATED", "ESCALATED"}
    ),
    "ELIGIBLE": frozenset({"ELIGIBLE", "ORIENTED", "AUTHENTICATED", "ESCALATED"}),
    "INELIGIBLE": frozenset({"INELIGIBLE", "PROFILING", "ORIENTED", "ESCALATED"}),
    "ORIENTED": frozenset({"ORIENTED", "AUTHENTICATED", "ESCALATED"}),
    "AUTHENTICATED": frozenset(
        {
            "AUTHENTICATED",
            "PROFILING",
            "ELIGIBLE",
            "INELIGIBLE",
            "ORIENTED",
            "PREINSCRIBED",
            "ESCALATED",
        }
    ),
    "PREINSCRIBED": frozenset({"PREINSCRIBED", "QCM_DONE", "ESCALATED"}),
    "QCM_DONE": frozenset({"QCM_DONE", "ESCALATED"}),
    "SCHEDULED": frozenset({"SCHEDULED", "ESCALATED"}),
    "INTERVIEWED": frozenset({"INTERVIEWED"}),
    "DORMANT": frozenset({"DORMANT", "PROFILING", "AUTHENTICATED", "ESCALATED"}),
    "ESCALATED": frozenset({"ESCALATED"}),
    "WITHDRAWN": frozenset({"WITHDRAWN"}),
}

# Pre-reserved SA-73 catalog tool names bound to 1.2_Librarian and 1.5_Advisor.
RESERVED_CATALOG_TOOL_NAMES: tuple[str, ...] = (
    "list_school_programs_tool",
    "get_program_details_tool",
    "compare_programs_tool",
    "get_program_prerequisites_and_fees_tool",
)


def assert_vision_status_transition_allowed(
    target_status: str,
    current_status: str | None = None,
    *,
    require_current_status: bool = False,
) -> None:
    """Enforce HITL admission locks and valid VISION front-office candidate status transitions.

    Raises:
        PermissionError: If target_status is a human-only admission decision
            (`ADMITTED`, `WAITLIST`, `REJECTED`) or a transition forbidden to VISION.
        ValueError: If target_status or current_status is not a canonical `CandidateStatus`,
            or if `require_current_status=True` and `current_status` is missing.
    """
    if target_status in FORBIDDEN_AGENT_STATUSES:
        raise PermissionError(
            f"HITL governance violation: AI agents are strictly forbidden from setting "
            f"candidate status to '{target_status}'. Only a human NAU Responsable may "
            f"sign admission decisions (ADMITTED, WAITLIST, REJECTED)."
        )

    if target_status not in CANDIDATE_STATUS_VALUES:
        raise ValueError(
            f"Unknown candidate status '{target_status}'. "
            f"Expected one of {CANDIDATE_STATUS_VALUES}."
        )

    if current_status is None:
        if require_current_status:
            raise ValueError(
                "current_status is required to validate a VISION candidate status transition."
            )
        if target_status not in VISION_ASSIGNABLE_STATUSES:
            raise PermissionError(
                f"VISION is not allowed to assign candidate status '{target_status}' "
                f"without an authorized transition from a matching current_status."
            )
        return

    if current_status not in CANDIDATE_STATUS_VALUES:
        raise ValueError(
            f"Unknown current candidate status '{current_status}'. "
            f"Expected one of {CANDIDATE_STATUS_VALUES}."
        )

    allowed_targets = ALLOWED_VISION_STATUS_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise PermissionError(
            f"VISION transition '{current_status}' -> '{target_status}' is not allowed. "
            f"Allowed targets from '{current_status}': {sorted(allowed_targets)}."
        )


# ---------------------------------------------------------------------------
# Tool Calling & Security Contracts (SA-70, SA-71, SA-73)
# ---------------------------------------------------------------------------

# Intentionally omits any admission-decision permission level.
ToolPermissionLevel = Literal["PUBLIC_READ", "AUTH_READ", "AUTH_STATE_CHANGE"]


@dataclass
class ToolExecutionContext:
    """Runtime execution context injected into tool dispatchers and action guards."""

    session_id: str
    candidate_id: str | None = None
    is_authenticated: bool = False
    actor_role: ActorRole = "guest"
    campus_scope: CampusScope = "all"
    candidate_status: CandidateStatus = "ANONYMOUS"
    case_owner: str = "JARVIS"
    db: Any | None = None
    redis: Any | None = None


class AgentToolSpec(BaseModel):
    """Typed specification for a tool callable by one or more VISION sub-agents."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(..., min_length=5, max_length=500)
    allowed_sub_agents: tuple[VisionSubAgent, ...]
    permission_level: ToolPermissionLevel
    input_schema: dict[str, Any] = Field(default_factory=dict)
    source_attribution: str | None = None
    handler: Callable[..., Awaitable[Any]] | None = Field(default=None, exclude=True)


class ToolCallRecord(BaseModel):
    """Structured audit/trace entry emitted for every tool invocation attempt in a turn."""

    tool_name: str
    sub_agent: VisionSubAgent
    permission_level: ToolPermissionLevel
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "EXECUTED",
        "BLOCKED_AUTH",
        "BLOCKED_RBAC",
        "BLOCKED_HITL",
        "ERROR",
        "SKIPPED",
    ]
    result_summary: str | None = None
    source_attribution: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None


class ToolExecutionResult(BaseModel):
    """Normalized return value produced by a `ToolDispatcherProtocol` implementation."""

    tool_name: str
    ok: bool
    data: Any = None
    citations: list[ChatCitation] = Field(default_factory=list)
    source_attribution: str | None = None
    error: str | None = None
    blocked_reason: str | None = None
    action_prompt: ActionPrompt | None = None


class ActionGuardDecision(BaseModel):
    """Pre-execution security decision returned by `ActionGuardProtocol` (SA-71)."""

    allowed: bool
    reason: str | None = None
    action_prompt: ActionPrompt | None = None
    audit_required: bool = False


@runtime_checkable
class ActionGuardProtocol(Protocol):
    """Security & audit wrapper protocol enforced around agent tool calls (SA-71)."""

    async def authorize(
        self,
        tool: AgentToolSpec,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ActionGuardDecision: ...

    async def record_audit(
        self,
        tool: AgentToolSpec,
        record: ToolCallRecord,
        context: ToolExecutionContext,
    ) -> None: ...


@runtime_checkable
class ToolDispatcherProtocol(Protocol):
    """Tool registry & dispatcher protocol plugged into VISION by SA-70."""

    def list_tools_for_subagent(self, sub_agent: VisionSubAgent) -> list[AgentToolSpec]: ...

    async def dispatch(
        self,
        sub_agent: VisionSubAgent,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult: ...


# ---------------------------------------------------------------------------
# Sub-Agent Prompt & Configuration Registry Contract (SA-75)
# ---------------------------------------------------------------------------


class SubAgentConfig(BaseModel):
    """Role configuration contract for each of the 6 VISION sub-agents (SA-75)."""

    sub_agent: VisionSubAgent
    component_type: ComponentImplementationType
    description: str
    role_prompt_addon: str
    allowed_tools: tuple[str, ...] = ()
    requires_auth: bool = False
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=64, le=4096)
    output_constraints: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# HITL Interrupt Contracts (LangGraph interrupt() / Command(resume=...))
# ---------------------------------------------------------------------------

HitlInterruptReason = Literal[
    "AUTH_REQUIRED",
    "RELEVE_CONFIRMATION",
    "CONSENT_AND_SUBMIT",
    "RH_MISMATCH_ESCALATION",
]


class HitlInterruptSpec(BaseModel):
    """Structured payload describing a human confirmation pause in VISION."""

    reason: HitlInterruptReason
    sub_agent: VisionSubAgent
    prompt_message: str
    action_prompt: ActionPrompt | None = None
    required_confirmation_fields: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strict VISION -> JARVIS Outbox Handoff Contract (`core.outbox`)
# ---------------------------------------------------------------------------

OutboxHandoffEventType = Literal["pre-inscription submitted", "qcm_completed"]

OUTBOX_EVENT_BY_STATUS: dict[str, OutboxHandoffEventType] = {
    "PREINSCRIBED": "pre-inscription submitted",
    "QCM_DONE": "qcm_completed",
}


class OutboxHandoffSpec(BaseModel):
    """Transactional outbox handoff written to `core.outbox` for JARVIS workers.

    VISION never invokes JARVIS directly; JARVIS reacts asynchronously via
    PostgreSQL `core.outbox` (`OutboxEvent`), LISTEN/NOTIFY, and 10-min reconciliation.
    """

    event_type: OutboxHandoffEventType
    aggregate_type: Literal["candidate"] = "candidate"
    aggregate_id: UUID
    target_status: Literal["PREINSCRIBED", "QCM_DONE"]
    school_code: SchoolCodeLower
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_status")
    @classmethod
    def validate_handoff_status(cls, value: str) -> str:
        assert_vision_status_transition_allowed(value)
        return value


# ---------------------------------------------------------------------------
# Turn Input / Output & LangGraph StateGraph Schema
# ---------------------------------------------------------------------------


class VisionTurnInput(BaseModel):
    """Normalized turn input entering `VisionSupervisor.run_turn()`."""

    payload: ChatMessageRequest
    session_id: str | None = None
    candidate_id: str | None = None
    account_id: str | None = None
    is_authenticated: bool = False
    actor_role: ActorRole = "guest"
    candidate_status: CandidateStatus = "ANONYMOUS"
    case_owner: str = "JARVIS"
    thread_id: str | None = None


class VisionTurnOutput(BaseModel):
    """Full structured orchestration result produced by `VisionSupervisor.run_turn()`."""

    response: ChatMessageResponse
    thread_id: str
    candidate_status: CandidateStatus
    case_owner: str
    nodes_visited: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    hitl_interrupt: HitlInterruptSpec | None = None
    outbox_events: list[OutboxHandoffSpec] = Field(default_factory=list)


class VisionAgentState(TypedDict, total=False):
    """JSON-serializable LangGraph `StateGraph` state for VISION front-office turns.

    Non-serializable runtime handles (`db`, `redis_client`, `request_payload`,
    `tool_dispatcher`, `action_guard`) are injected via LangGraph's
    `RunnableConfig["configurable"]` so checkpoints serialize cleanly across both
    `MemorySaver` (tests/dev) and `AsyncPostgresSaver` (Sprint 3 production).
    """

    # Identity, Threading & Give-or-Keep Auth
    thread_id: str
    session_id: str
    memory_scope_key: str
    candidate_id: str | None
    account_id: str | None
    is_authenticated: bool
    actor_role: ActorRole
    campus_scope: CampusScope

    # Lifecycle & Single-Owner Governance
    candidate_status: CandidateStatus
    case_owner: str

    # Raw Turn Input & 1.1_Listener NLU
    user_message: str
    is_voice: bool
    language_hint: LanguageHint
    explicit_dialect: DetectedDialect | None
    detected_language: str
    detected_dialect: DetectedDialect
    normalized_french_query: str | None
    intent: str
    comprehension_confidence: float
    needs_reformulation: bool
    reformulation_prompt: str | None

    # 3-Tier Conversation Memory Snapshot
    recent_turns: list[dict[str, Any]]
    declared_facts: dict[str, Any]
    rolling_summary: str | None
    history_turn_count: int | None

    # Routing, Tool Execution & Citations
    active_sub_agent: VisionSubAgent
    routed_node: str
    nodes_visited: list[str]
    tool_calls: list[dict[str, Any]]
    retrieved_chunks: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    action_prompt: ActionPrompt | None
    suggested_actions: list[str]

    # Guardrail & HITL State
    input_blocked: bool
    guardrail_triggered: bool
    guardrail_action: str | None
    hitl_interrupt: dict[str, Any] | None
    hitl_resume_payload: dict[str, Any] | None
    outbox_events: list[dict[str, Any]]

    # Generation & Telemetry
    draft_reply: str
    final_reply: str
    provider: str
    model_used: str
    used_fallback: bool
    started_at_ns: int
    latency_ms: int
