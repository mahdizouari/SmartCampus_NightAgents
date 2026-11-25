"""VISION LangGraph Supervisor & Multi-Agent Orchestration Service (SA-69 / GitHub #73).

Implements the formal compiled LangGraph `StateGraph` supervisor for **VISION**
(front-office conversational orientation, eligibility, VLM transcript reading,
track recommendation, pre-inscription & QCM orchestration):

    START
      -> input_guardrail  (rules: prompt injection / out-of-corpus / off-topic)
          |-- [blocked] --> END
          `-- [passed]  --> listener_1_1 (dialect LID, Arabizi norm, confidence, intent, memory)
                              |-- [needs_reformulation (< 0.45)] --> output_guardrail --> END
                              `-- [confident] --> supervisor_router
                                                    |-- librarian_1_2   (LLM+RAG, any step)
                                                    |-- gatekeeper_1_3  (LLM+rules)
                                                    |-- reader_1_4      (VLM, auth-gated)
                                                    |-- advisor_1_5     (code+LLM)
                                                    |-- registrar_1_6   (LLM+DB, auth-gated)
                                                    `-- output_guardrail (1.1_Listener direct)
                                                          --> output_guardrail
                                                                --> persist_memory --> END

Non-negotiable SmartCampus AI (SC V3 / V4 & U6) invariants:
1. Local-first open-source model compatibility (Ollama / local vLLM / QLoRA — zero
   hardcoded external cloud API dependency; 100% offline/CI-safe fallback mode).
2. Human-in-the-Loop (HITL) admissions: no node, tool, or helper may transition a
   candidate to `ADMITTED`, `WAITLIST`, or `REJECTED`.
3. Strict VISION <-> JARVIS decoupling: zero imports or calls to scheduling/appointment
   or JARVIS modules; VISION communicates asynchronously solely by writing `OutboxEvent`
   rows to `core.outbox` on `PREINSCRIBED` (`"pre-inscription submitted"`) and
   `QCM_DONE` (`"qcm_completed"`).
4. Genuine LangGraph `interrupt()` + `Command(resume=...)` HITL checkpoints backed by
   `MemorySaver` (injectable `BaseCheckpointSaver` seam for `AsyncPostgresSaver` in Sprint 3).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import CandidateProfile, OutboxEvent
from app.schemas.agent import (
    FORBIDDEN_AGENT_STATUSES,
    OUTBOX_EVENT_BY_STATUS,
    RESERVED_CATALOG_TOOL_NAMES,
    ActionGuardDecision,
    ActionGuardProtocol,
    AgentToolSpec,
    CandidateStatus,
    HitlInterruptSpec,
    OutboxHandoffSpec,
    SchoolCodeLower,
    SubAgentConfig,
    ToolCallRecord,
    ToolDispatcherProtocol,
    ToolExecutionContext,
    VisionAgentState,
    VisionTurnInput,
    VisionTurnOutput,
    assert_vision_status_transition_allowed,
)
from app.schemas.chat import (
    ActionPrompt,
    ChatCitation,
    ChatMessageRequest,
    ChatMessageResponse,
    VisionSubAgent,
)
from app.services.arabizi_normalizer import normalize_arabizi_to_french
from app.services.guardrail_service import (
    check_input_guardrail,
    check_output_guardrail,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical LangGraph Node Identifiers
# ---------------------------------------------------------------------------

NODE_INPUT_GUARDRAIL = "input_guardrail"
NODE_LISTENER = "listener_1_1"
NODE_SUPERVISOR_ROUTER = "supervisor_router"
NODE_LIBRARIAN = "librarian_1_2"
NODE_GATEKEEPER = "gatekeeper_1_3"
NODE_READER = "reader_1_4"
NODE_ADVISOR = "advisor_1_5"
NODE_REGISTRAR = "registrar_1_6"
NODE_OUTPUT_GUARDRAIL = "output_guardrail"
NODE_PERSIST_MEMORY = "persist_memory"

VISION_GRAPH_NODES: tuple[str, ...] = (
    NODE_INPUT_GUARDRAIL,
    NODE_LISTENER,
    NODE_SUPERVISOR_ROUTER,
    NODE_LIBRARIAN,
    NODE_GATEKEEPER,
    NODE_READER,
    NODE_ADVISOR,
    NODE_REGISTRAR,
    NODE_OUTPUT_GUARDRAIL,
    NODE_PERSIST_MEMORY,
)

SUBAGENT_TO_NODE: dict[VisionSubAgent, str] = {
    "1.1_Listener": NODE_LISTENER,
    "1.2_Librarian": NODE_LIBRARIAN,
    "1.3_Gatekeeper": NODE_GATEKEEPER,
    "1.4_Reader": NODE_READER,
    "1.5_Advisor": NODE_ADVISOR,
    "1.6_Registrar": NODE_REGISTRAR,
}

NODE_TO_SUBAGENT: dict[str, VisionSubAgent] = {v: k for k, v in SUBAGENT_TO_NODE.items()}

# Forbidden tool-name fragments that could violate HITL admission governance.
_FORBIDDEN_TOOL_NAME_KEYWORDS: tuple[str, ...] = (
    "admit",
    "reject",
    "waitlist",
    "decide_admission",
    "sign_decision",
)

# Explicit cues distinguishing VLM/OCR transcript reading (1.4_Reader) from
# general pre-inscription form/document workflows (1.6_Registrar).
_READER_EXPLICIT_CUES: tuple[str, ...] = (
    "vlm",
    "confiance ocr",
    "extraction ocr",
    "extraire mes notes",
    "extraire les notes",
    "notes extraites",
    "lecture ocr",
    "scanner mon bulletin",
    "analyser mon bulletin",
)

# Custom node handler signature for Sprint 2 teammates (SA-74, SA-82, SA-84).
SubAgentHandler = Callable[
    [VisionAgentState, ToolExecutionContext, SubAgentConfig],
    Awaitable[dict[str, Any]],
]


# ---------------------------------------------------------------------------
# Default Sub-Agent Configuration Registry (baseline seam for SA-75 / Melek)
# ---------------------------------------------------------------------------


def build_default_subagent_configs() -> dict[VisionSubAgent, SubAgentConfig]:
    """Return canonical SC V3 §5 configurations for all 6 VISION sub-agents."""
    return {
        "1.1_Listener": SubAgentConfig(
            sub_agent="1.1_Listener",
            component_type="LLM",
            description="Multilingual voice/text NLU, Arabizi normalization, intent detection.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.1_Listener] Accueille l'étudiant dans son registre exact "
                "(FR / Fusha / Derja / Arabizi), clarifie toute demande ambiguë sans jamais "
                "deviner, et rappelle la gouvernance HITL ou la règle Give-or-Keep si demandé."
            ),
            allowed_tools=(),
            requires_auth=False,
            temperature=0.2,
            max_tokens=512,
            output_constraints=(
                "Match student dialect/language register",
                "Request French reformulation when comprehension confidence < 0.45",
            ),
        ),
        "1.2_Librarian": SubAgentConfig(
            sub_agent="1.2_Librarian",
            component_type="LLM+RAG",
            description="Official IIT/ISB/ISA corpus Q&A with mandatory source citations.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.2_Librarian] Réponds exclusivement à partir des extraits "
                "officiels RAG et des données du catalogue PostgreSQL NAU. Cite systématiquement "
                "les documents sources et refuse explicitement si aucune source n'est disponible."
            ),
            allowed_tools=(
                "rag_hybrid_search_tool",
                *RESERVED_CATALOG_TOOL_NAMES,
            ),
            requires_auth=False,
            temperature=0.1,
            max_tokens=1024,
            output_constraints=(
                "Cite official source document for every academic fact",
                "Never invent tuition fees, deadlines, thresholds, or non-existent programs",
                "Available at every pipeline step",
            ),
        ),
        "1.3_Gatekeeper": SubAgentConfig(
            sub_agent="1.3_Gatekeeper",
            component_type="LLM+rules",
            description="Declared profile extraction + deterministic YAML/DB eligibility rules.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.3_Gatekeeper] Collecte les faits académiques déclarés "
                "(diplôme, section Bac, moyenne, spécialité) et explique l'éligibilité ou les "
                "passerelles selon les règles officielles. Signale tout cas limite comme "
                "'à confirmer par le Responsable NAU'."
            ),
            allowed_tools=(
                "evaluate_eligibility_tool",
                "get_program_prerequisites_and_fees_tool",
            ),
            requires_auth=False,
            temperature=0.1,
            max_tokens=768,
            output_constraints=(
                "Deterministic rules decide eligibility; LLM explains only",
                "Flag borderline equivalences for NAU Responsable confirmation",
            ),
        ),
        "1.4_Reader": SubAgentConfig(
            sub_agent="1.4_Reader",
            component_type="VLM",
            description="Authenticated relevé de notes VLM extraction + confidence cross-check.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.4_Reader] Accompagne le candidat authentifié dans la "
                "vérification des notes extraites de son relevé officiel. Signale tout champ "
                "sous le seuil de confiance ou tout écart de moyenne pour confirmation explicite."
            ),
            allowed_tools=("get_transcript_extraction_status_tool",),
            requires_auth=True,
            temperature=0.0,
            max_tokens=768,
            output_constraints=(
                "Requires authentication (Give or Keep rule)",
                "Flag low-confidence fields (< 0.85) or discrepancies (> 0.25) for confirmation",
            ),
        ),
        "1.5_Advisor": SubAgentConfig(
            sub_agent="1.5_Advisor",
            component_type="code+LLM",
            description="Compatibility scoring (code) + explained 2-3 track shortlist.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.5_Advisor] Présente 2 à 3 parcours IIT / ISB / ISA côte "
                "à côte avec la décomposition transparente du score de compatibilité (critères "
                "académiques et intérêts déclarés uniquement, sans biais) et sources officielles."
            ),
            allowed_tools=(
                "compute_compatibility_score_tool",
                *RESERVED_CATALOG_TOOL_NAMES,
            ),
            requires_auth=False,
            temperature=0.2,
            max_tokens=1024,
            output_constraints=(
                "Explain 2-3 recommended tracks with transparent score breakdown",
                "Use academic and stated-interest features only",
            ),
        ),
        "1.6_Registrar": SubAgentConfig(
            sub_agent="1.6_Registrar",
            component_type="LLM",
            description="Authenticated pre-inscription pre-fill, vetted QCM, 1-page brief, outbox.",
            role_prompt_addon=(
                "[SOUS-AGENT ACTIF : 1.6_Registrar] Guide le candidat authentifié pour le dépôt "
                "du relevé, le consentement INPDP (Loi 2004-63), la préinscription, le QCM et la "
                "préparation d'entretien. Ne promets jamais l'admission : seul le Responsable NAU "
                "humain décide."
            ),
            allowed_tools=(
                "prefill_preinscription_tool",
                "submit_preinscription_tool",
                "start_qcm_session_tool",
                "list_available_slots_tool",
            ),
            requires_auth=True,
            temperature=0.1,
            max_tokens=1024,
            output_constraints=(
                "Requires authentication (Give or Keep rule) and INPDP consent before submit",
                "Emit core.outbox event on PREINSCRIBED / QCM_DONE; never call JARVIS directly",
                "Never admit, waitlist, reject, or promise admission",
            ),
        ),
    }


DEFAULT_SUBAGENT_CONFIGS: dict[VisionSubAgent, SubAgentConfig] = build_default_subagent_configs()


# ---------------------------------------------------------------------------
# Built-In Baseline Action Guard (seam for SA-71 / Mehdi)
# ---------------------------------------------------------------------------


class DefaultActionGuard:
    """Baseline `ActionGuardProtocol` enforcing Give-or-Keep, anti-IDOR, and HITL locks."""

    async def authorize(
        self,
        tool: AgentToolSpec,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ActionGuardDecision:
        lower_name = tool.name.lower()
        if any(kw in lower_name for kw in _FORBIDDEN_TOOL_NAME_KEYWORDS):
            return ActionGuardDecision(
                allowed=False,
                reason=(
                    "HITL governance block: no agent tool may admit, waitlist, reject, "
                    "or sign an admission decision."
                ),
                audit_required=True,
            )

        for key in ("target_status", "status", "decision", "new_status"):
            raw_val = arguments.get(key)
            if isinstance(raw_val, str) and raw_val.upper() in FORBIDDEN_AGENT_STATUSES:
                return ActionGuardDecision(
                    allowed=False,
                    reason=(
                        f"HITL governance block: forbidden admission status '{raw_val}' "
                        f"(field '{key}') cannot be set by an agent."
                    ),
                    audit_required=True,
                )

        if tool.permission_level in {"AUTH_READ", "AUTH_STATE_CHANGE"} and (
            not context.is_authenticated
        ):
            return ActionGuardDecision(
                allowed=False,
                reason="Give-or-Keep auth gate: authentication required for this tool.",
                action_prompt="AUTH_REQUIRED",
                audit_required=False,
            )

        requested_candidate_id = arguments.get("candidate_id")
        if (
            context.actor_role == "student"
            and requested_candidate_id is not None
            and context.candidate_id is not None
            and str(requested_candidate_id) != str(context.candidate_id)
        ):
            return ActionGuardDecision(
                allowed=False,
                reason="Anti-IDOR violation: students may only access their own candidate dossier.",
                audit_required=True,
            )

        return ActionGuardDecision(
            allowed=True,
            audit_required=(tool.permission_level == "AUTH_STATE_CHANGE"),
        )

    async def record_audit(
        self,
        tool: AgentToolSpec,
        record: ToolCallRecord,
        context: ToolExecutionContext,
    ) -> None:
        logger.info(
            "VISION tool audit: tool=%s sub_agent=%s status=%s role=%s session=%s",
            tool.name,
            record.sub_agent,
            record.status,
            context.actor_role,
            context.session_id,
        )


# ---------------------------------------------------------------------------
# Strict VISION -> JARVIS Outbox Handoff Helpers (`core.outbox`)
# ---------------------------------------------------------------------------


def build_outbox_handoff_spec(
    *,
    candidate_id: UUID,
    target_status: str,
    school_code: SchoolCodeLower,
    payload: dict[str, Any] | None = None,
) -> OutboxHandoffSpec:
    """Create a validated VISION -> JARVIS outbox specification for PREINSCRIBED / QCM_DONE.

    Raises:
        PermissionError: If `target_status` is a forbidden human-only admission decision.
        ValueError: If `target_status` is not an outbox-emitting milestone.
    """
    assert_vision_status_transition_allowed(target_status)

    event_type = OUTBOX_EVENT_BY_STATUS.get(target_status)
    if event_type is None:
        raise ValueError(
            f"Status '{target_status}' does not emit a VISION -> JARVIS outbox event. "
            f"Allowed outbox trigger statuses: {sorted(OUTBOX_EVENT_BY_STATUS)}."
        )

    enriched_payload = dict(payload or {})
    enriched_payload.setdefault("candidate_id", str(candidate_id))
    enriched_payload["school_code"] = school_code
    enriched_payload["target_status"] = target_status

    return OutboxHandoffSpec(
        event_type=event_type,
        aggregate_type="candidate",
        aggregate_id=candidate_id,
        target_status=target_status,  # type: ignore[arg-type]
        school_code=school_code,
        payload=enriched_payload,
    )


async def enqueue_jarvis_outbox_event(
    db: AsyncSession,
    spec: OutboxHandoffSpec,
    *,
    commit: bool = False,
) -> OutboxEvent:
    """Persist an asynchronous `OutboxEvent` into `core.outbox` for JARVIS workers.

    VISION never invokes JARVIS directly. Leaving `commit=False` by default allows
    `1.6_Registrar` (`SA-82` / `SA-88`) to commit `core` + `<school>.dossiers` +
    `core.outbox` inside a single atomic PostgreSQL transaction.
    """
    assert_vision_status_transition_allowed(spec.target_status)

    outbox_row = OutboxEvent(
        event_type=spec.event_type,
        aggregate_type=spec.aggregate_type,
        aggregate_id=spec.aggregate_id,
        payload={
            **spec.payload,
            "school_code": spec.school_code,
            "target_status": spec.target_status,
        },
    )
    db.add(outbox_row)
    await db.flush()
    if commit:
        await db.commit()
    return outbox_row


# ---------------------------------------------------------------------------
# HITL Checkpoint Subgraph State (Real LangGraph interrupt() / Command(resume))
# ---------------------------------------------------------------------------


class _HitlCheckpointState(VisionAgentState, total=False):
    """State for the dedicated LangGraph `interrupt()` HITL confirmation subgraph."""

    interrupt_spec: dict[str, Any]
    human_response: dict[str, Any] | None
    checkpoint_status: str
    checkpoint_approved: bool


def _build_hitl_checkpoint_graph(checkpointer: BaseCheckpointSaver) -> Any:
    """Compile a genuine LangGraph HITL interrupt/resume checkpoint graph."""

    async def await_human_confirmation(state: _HitlCheckpointState) -> dict[str, Any]:
        spec = state.get("interrupt_spec") or {}
        # Real LangGraph interrupt(): suspends execution and persists checkpoint under thread_id
        human_response = interrupt(spec)
        return {
            "human_response": human_response if isinstance(human_response, dict) else {
                "value": human_response
            },
            "checkpoint_status": "RESUMED",
        }

    async def apply_human_confirmation(state: _HitlCheckpointState) -> dict[str, Any]:
        response = state.get("human_response") or {}
        proposed_status = response.get("target_status")
        if isinstance(proposed_status, str):
            assert_vision_status_transition_allowed(
                proposed_status,
                current_status=state.get("candidate_status"),
            )

        approved = bool(response.get("confirmed", response.get("approved", False)))
        updates: dict[str, Any] = {
            "checkpoint_approved": approved,
            "checkpoint_status": "CONFIRMED" if approved else "DECLINED",
            "hitl_resume_payload": response,
        }
        if isinstance(proposed_status, str) and approved:
            updates["candidate_status"] = proposed_status
            if (
                proposed_status in OUTBOX_EVENT_BY_STATUS
                and response.get("candidate_id")
                and response.get("school_code")
            ):
                handoff = build_outbox_handoff_spec(
                    candidate_id=UUID(str(response["candidate_id"])),
                    target_status=proposed_status,
                    school_code=response["school_code"],
                    payload=dict(response.get("outbox_payload") or {}),
                )
                updates["outbox_events"] = [handoff.model_dump(mode="json")]
        return updates

    builder: StateGraph = StateGraph(_HitlCheckpointState)
    builder.add_node("await_human_confirmation", await_human_confirmation)
    builder.add_node("apply_human_confirmation", apply_human_confirmation)
    builder.add_edge(START, "await_human_confirmation")
    builder.add_edge("await_human_confirmation", "apply_human_confirmation")
    builder.add_edge("apply_human_confirmation", END)
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------


def _serialize_turn(turn: Any) -> dict[str, Any]:
    role = getattr(turn, "role", None) or (turn.get("role") if isinstance(turn, dict) else "user")
    content = getattr(turn, "content", None) or (
        turn.get("content") if isinstance(turn, dict) else ""
    )
    ts = getattr(turn, "timestamp", None) or (
        turn.get("timestamp") if isinstance(turn, dict) else None
    )
    data: dict[str, Any] = {"role": str(role), "content": str(content)}
    if ts is not None:
        data["timestamp"] = ts
    return data


def _append_visited(state: VisionAgentState, node_name: str) -> list[str]:
    visited = list(state.get("nodes_visited") or [])
    visited.append(node_name)
    return visited


def _should_route_to_reader(text: str, current_intent: str) -> bool:
    """Detect explicit VLM/OCR transcript reading turns without breaking Registrar intents."""
    lower = text.lower()
    if any(k in lower for k in ("préinscription", "preinscription", "admission", "dossier")):
        return False
    if current_intent == "TRANSCRIPT_OCR_EXTRACTION":
        return True
    return any(cue in lower for cue in _READER_EXPLICIT_CUES)


def _derive_next_candidate_status(
    current_status: CandidateStatus,
    *,
    is_authenticated: bool,
    sub_agent: VisionSubAgent,
    intent: str,
) -> CandidateStatus:
    """Compute a safe, HITL-compliant candidate status update for the current VISION turn."""
    candidate_next: CandidateStatus = current_status
    if is_authenticated and current_status == "ANONYMOUS":
        candidate_next = "AUTHENTICATED"
    elif not is_authenticated and current_status == "ANONYMOUS" and sub_agent in {
        "1.2_Librarian",
        "1.3_Gatekeeper",
        "1.5_Advisor",
    }:
        candidate_next = "PROFILING"
    elif intent == "TRACK_RECOMMENDATION" and current_status in {"ANONYMOUS", "PROFILING"}:
        candidate_next = "PROFILING"

    assert_vision_status_transition_allowed(candidate_next, current_status=current_status)
    return candidate_next


# ---------------------------------------------------------------------------
# VISION LangGraph Supervisor
# ---------------------------------------------------------------------------


class VisionSupervisor:
    """Compiled LangGraph `StateGraph` supervisor for SmartCampus AI VISION (SA-69).

    Coordinates all 6 VISION sub-agents (`1.1_Listener` .. `1.6_Registrar`) bracketed
    by deterministic `input_guardrail` and `output_guardrail` nodes, backed by an
    injectable LangGraph checkpointer (`MemorySaver` default; `AsyncPostgresSaver`
    ready for Sprint 3) and clean extension seams for `SA-70`, `SA-71`, `SA-73`,
    `SA-74`, and `SA-75`.
    """

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        subagent_configs: dict[VisionSubAgent, SubAgentConfig] | None = None,
        tool_dispatcher: ToolDispatcherProtocol | None = None,
        action_guard: ActionGuardProtocol | None = None,
    ) -> None:
        self.checkpointer: BaseCheckpointSaver = checkpointer or MemorySaver()
        self._subagent_configs: dict[VisionSubAgent, SubAgentConfig] = (
            dict(subagent_configs)
            if subagent_configs is not None
            else build_default_subagent_configs()
        )
        self._subagent_handlers: dict[VisionSubAgent, SubAgentHandler] = {}
        self.tool_dispatcher: ToolDispatcherProtocol | None = tool_dispatcher
        self.action_guard: ActionGuardProtocol = action_guard or DefaultActionGuard()

        self.compiled_graph = self._compile_supervisor_graph()
        self.hitl_checkpoint_graph = _build_hitl_checkpoint_graph(self.checkpointer)

    # ── Extension Seams (SA-70, SA-71, SA-73, SA-74, SA-75) ──────────────────

    def get_subagent_config(self, sub_agent: VisionSubAgent) -> SubAgentConfig:
        """Return the active `SubAgentConfig` for a VISION sub-agent."""
        return self._subagent_configs[sub_agent]

    def list_subagent_configs(self) -> dict[VisionSubAgent, SubAgentConfig]:
        """Return a shallow copy of all 6 VISION sub-agent configurations."""
        return dict(self._subagent_configs)

    def register_subagent_configs(
        self,
        configs: dict[VisionSubAgent, SubAgentConfig],
    ) -> None:
        """Register or override sub-agent prompts/tool allowlists (SA-75 hook)."""
        for sub_agent, cfg in configs.items():
            if cfg.sub_agent != sub_agent:
                raise ValueError(
                    f"SubAgentConfig key mismatch: key='{sub_agent}' vs cfg='{cfg.sub_agent}'."
                )
            self._subagent_configs[sub_agent] = cfg

    def register_subagent_handler(
        self,
        sub_agent: VisionSubAgent,
        handler: SubAgentHandler,
    ) -> None:
        """Register a custom async handler for a specialized sub-agent node (SA-74 / SA-82 hook)."""
        self._subagent_handlers[sub_agent] = handler

    def set_tool_dispatcher(self, dispatcher: ToolDispatcherProtocol | None) -> None:
        """Attach a `ToolDispatcherProtocol` implementation (SA-70 hook)."""
        self.tool_dispatcher = dispatcher

    def set_action_guard(self, guard: ActionGuardProtocol) -> None:
        """Attach an `ActionGuardProtocol` security/audit wrapper (SA-71 hook)."""
        self.action_guard = guard

    def get_graph_topology(self) -> dict[str, Any]:
        """Return the real compiled LangGraph nodes and directed edges for inspection."""
        graph_repr = self.compiled_graph.get_graph()
        return {
            "nodes": sorted(graph_repr.nodes.keys()),
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "conditional": bool(edge.conditional),
                }
                for edge in graph_repr.edges
            ],
        }

    # ── Graph Compilation ────────────────────────────────────────────────────

    def _compile_supervisor_graph(self) -> Any:
        builder: StateGraph = StateGraph(VisionAgentState)

        builder.add_node(NODE_INPUT_GUARDRAIL, self._node_input_guardrail)
        builder.add_node(NODE_LISTENER, self._node_listener_1_1)
        builder.add_node(NODE_SUPERVISOR_ROUTER, self._node_supervisor_router)
        builder.add_node(NODE_LIBRARIAN, self._make_subagent_node("1.2_Librarian", NODE_LIBRARIAN))
        builder.add_node(
            NODE_GATEKEEPER, self._make_subagent_node("1.3_Gatekeeper", NODE_GATEKEEPER)
        )
        builder.add_node(NODE_READER, self._make_subagent_node("1.4_Reader", NODE_READER))
        builder.add_node(NODE_ADVISOR, self._make_subagent_node("1.5_Advisor", NODE_ADVISOR))
        builder.add_node(NODE_REGISTRAR, self._make_subagent_node("1.6_Registrar", NODE_REGISTRAR))
        builder.add_node(NODE_OUTPUT_GUARDRAIL, self._node_output_guardrail)
        builder.add_node(NODE_PERSIST_MEMORY, self._node_persist_memory)

        builder.add_edge(START, NODE_INPUT_GUARDRAIL)

        builder.add_conditional_edges(
            NODE_INPUT_GUARDRAIL,
            self._route_after_input_guardrail,
            {
                END: END,
                NODE_LISTENER: NODE_LISTENER,
            },
        )

        builder.add_conditional_edges(
            NODE_LISTENER,
            self._route_after_listener,
            {
                NODE_OUTPUT_GUARDRAIL: NODE_OUTPUT_GUARDRAIL,
                NODE_SUPERVISOR_ROUTER: NODE_SUPERVISOR_ROUTER,
            },
        )

        builder.add_conditional_edges(
            NODE_SUPERVISOR_ROUTER,
            self._route_after_supervisor,
            {
                NODE_LIBRARIAN: NODE_LIBRARIAN,
                NODE_GATEKEEPER: NODE_GATEKEEPER,
                NODE_READER: NODE_READER,
                NODE_ADVISOR: NODE_ADVISOR,
                NODE_REGISTRAR: NODE_REGISTRAR,
                NODE_OUTPUT_GUARDRAIL: NODE_OUTPUT_GUARDRAIL,
            },
        )

        for sub_node in (
            NODE_LIBRARIAN,
            NODE_GATEKEEPER,
            NODE_READER,
            NODE_ADVISOR,
            NODE_REGISTRAR,
        ):
            builder.add_edge(sub_node, NODE_OUTPUT_GUARDRAIL)

        builder.add_conditional_edges(
            NODE_OUTPUT_GUARDRAIL,
            self._route_after_output_guardrail,
            {
                END: END,
                NODE_PERSIST_MEMORY: NODE_PERSIST_MEMORY,
            },
        )

        builder.add_edge(NODE_PERSIST_MEMORY, END)
        return builder.compile(checkpointer=self.checkpointer)

    # ── Conditional Edge Functions ───────────────────────────────────────────

    @staticmethod
    def _route_after_input_guardrail(state: VisionAgentState) -> str:
        if state.get("input_blocked"):
            return END
        return NODE_LISTENER

    @staticmethod
    def _route_after_listener(state: VisionAgentState) -> str:
        if state.get("needs_reformulation"):
            return NODE_OUTPUT_GUARDRAIL
        return NODE_SUPERVISOR_ROUTER

    @staticmethod
    def _route_after_supervisor(state: VisionAgentState) -> str:
        routed = state.get("routed_node")
        if routed in {
            NODE_LIBRARIAN,
            NODE_GATEKEEPER,
            NODE_READER,
            NODE_ADVISOR,
            NODE_REGISTRAR,
            NODE_OUTPUT_GUARDRAIL,
        }:
            return routed
        return NODE_OUTPUT_GUARDRAIL

    @staticmethod
    def _route_after_output_guardrail(state: VisionAgentState) -> str:
        if state.get("needs_reformulation"):
            return END
        return NODE_PERSIST_MEMORY

    # ── Runtime Helpers ──────────────────────────────────────────────────────

    def _build_tool_context(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> ToolExecutionContext:
        configurable = config.get("configurable") or {}
        return ToolExecutionContext(
            session_id=str(state.get("session_id") or ""),
            candidate_id=state.get("candidate_id"),
            is_authenticated=bool(state.get("is_authenticated", False)),
            actor_role=state.get("actor_role", "guest"),
            campus_scope=state.get("campus_scope", "all"),
            candidate_status=state.get("candidate_status", "ANONYMOUS"),
            case_owner=str(state.get("case_owner") or "JARVIS"),
            db=configurable.get("db"),
            redis=configurable.get("redis_client"),
        )

    async def _run_subagent_tools(
        self,
        *,
        sub_agent: VisionSubAgent,
        state: VisionAgentState,
        context: ToolExecutionContext,
        config: RunnableConfig,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], ActionPrompt | None]:
        """Run optional tool calls through `ActionGuardProtocol` + `ToolDispatcherProtocol`."""
        configurable = config.get("configurable") or {}
        dispatcher: ToolDispatcherProtocol | None = (
            configurable.get("tool_dispatcher") or self.tool_dispatcher
        )
        guard: ActionGuardProtocol = configurable.get("action_guard") or self.action_guard
        requested_tools: list[dict[str, Any]] = list(configurable.get("requested_tool_calls") or [])

        if dispatcher is None or not requested_tools:
            return [], [], None

        sub_cfg = self.get_subagent_config(sub_agent)
        available = {spec.name: spec for spec in dispatcher.list_tools_for_subagent(sub_agent)}

        records: list[dict[str, Any]] = []
        collected_chunks: list[dict[str, Any]] = []
        gate_action_prompt: ActionPrompt | None = None

        for call_req in requested_tools:
            tool_name = str(call_req.get("tool_name") or "")
            tool_args = dict(call_req.get("arguments") or {})
            spec = available.get(tool_name)

            if spec is None or tool_name not in sub_cfg.allowed_tools:
                records.append(
                    ToolCallRecord(
                        tool_name=tool_name or "unknown",
                        sub_agent=sub_agent,
                        permission_level="PUBLIC_READ",
                        arguments=tool_args,
                        status="BLOCKED_RBAC",
                        error=f"Tool '{tool_name}' is not allowed for sub-agent '{sub_agent}'.",
                    ).model_dump()
                )
                continue

            t0 = time.perf_counter_ns()
            decision = await guard.authorize(spec, tool_args, context)
            if not decision.allowed:
                block_status: str = (
                    "BLOCKED_AUTH"
                    if decision.action_prompt == "AUTH_REQUIRED"
                    else (
                        "BLOCKED_HITL"
                        if decision.reason and "HITL" in decision.reason
                        else "BLOCKED_RBAC"
                    )
                )
                rec = ToolCallRecord(
                    tool_name=spec.name,
                    sub_agent=sub_agent,
                    permission_level=spec.permission_level,
                    arguments=tool_args,
                    status=block_status,  # type: ignore[arg-type]
                    error=decision.reason,
                    source_attribution=spec.source_attribution,
                )
                records.append(rec.model_dump())
                if decision.audit_required:
                    await guard.record_audit(spec, rec, context)
                if decision.action_prompt is not None:
                    gate_action_prompt = decision.action_prompt
                continue

            exec_result = await dispatcher.dispatch(sub_agent, spec.name, tool_args, context)
            elapsed = max(0, int((time.perf_counter_ns() - t0) / 1_000_000))
            rec = ToolCallRecord(
                tool_name=spec.name,
                sub_agent=sub_agent,
                permission_level=spec.permission_level,
                arguments=tool_args,
                status="EXECUTED" if exec_result.ok else "ERROR",
                result_summary=(
                    str(exec_result.data)[:240] if exec_result.data is not None else None
                ),
                source_attribution=exec_result.source_attribution or spec.source_attribution,
                latency_ms=elapsed,
                error=exec_result.error,
            )
            records.append(rec.model_dump())
            if decision.audit_required:
                await guard.record_audit(spec, rec, context)

            if exec_result.ok:
                for cit in exec_result.citations:
                    collected_chunks.append(cit.model_dump())
                if not exec_result.citations and exec_result.data is not None:
                    collected_chunks.append(
                        {
                            "id": f"tool-{spec.name}",
                            "documentTitle": (
                                exec_result.source_attribution
                                or spec.source_attribution
                                or "Catalogue PostgreSQL NAU"
                            ),
                            "source_document": (
                                exec_result.source_attribution
                                or spec.source_attribution
                                or "Catalogue PostgreSQL NAU"
                            ),
                            "snippet": str(exec_result.data)[:500],
                            "chunk_id": f"tool-{spec.name}",
                        }
                    )

        return records, collected_chunks, gate_action_prompt

    async def _generate_reply_with_provider(
        self,
        *,
        sub_agent: VisionSubAgent,
        state: VisionAgentState,
        config: RunnableConfig,
        extra_chunks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute local-first LLM provider or offline deterministic fallback cleanly."""
        import app.services.llm_service as llm_mod

        configurable = config.get("configurable") or {}
        req_payload: ChatMessageRequest = configurable["request_payload"]
        sub_cfg = self.get_subagent_config(sub_agent)

        base_chunks = list(state.get("retrieved_chunks") or [])
        if extra_chunks:
            base_chunks.extend(extra_chunks)

        citations_models = llm_mod._build_citations(base_chunks or None)
        citations_dicts = [c.model_dump() for c in citations_models]

        effective_payload = (
            req_payload.model_copy(update={"context_chunks": base_chunks})
            if base_chunks
            else req_payload
        )
        messages = llm_mod._build_messages(
            effective_payload,
            str(state.get("detected_language") or "fr"),
            state.get("detected_dialect", "French"),
            str(state.get("intent") or "GENERAL_ORIENTATION"),
            history_turns=state.get("recent_turns"),
            rolling_summary=state.get("rolling_summary"),
            declared_facts=state.get("declared_facts"),
            sub_agent_addon=sub_cfg.role_prompt_addon,
        )

        active_settings = llm_mod.settings
        provider = str(active_settings.LLM_PROVIDER).lower().strip()
        api_key_set = bool(str(active_settings.LLM_API_KEY).strip())
        # Local-first open-source compatibility (U6): Ollama needs no cloud key;
        # OpenAI-compatible servers (e.g. local vLLM or configured endpoint) use API key if set.
        can_call_remote = provider == "ollama" or (
            provider == "openai_compatible" and api_key_set
        )

        if provider == "mock" or (
            not can_call_remote and bool(active_settings.LLM_FALLBACK_TO_MOCK)
        ):
            reply_text = llm_mod._generate_deterministic_fallback_reply(
                lang_code=str(state.get("detected_language") or "fr"),
                dialect=state.get("detected_dialect", "French"),
                intent=str(state.get("intent") or "GENERAL_ORIENTATION"),
                is_authenticated=bool(state.get("is_authenticated", False)),
                citations=citations_models,
            )
            return {
                "draft_reply": reply_text,
                "citations": citations_dicts,
                "retrieved_chunks": base_chunks,
                "provider": provider,
                "model_used": "smartcampus-fallback-v1",
                "used_fallback": True,
            }

        if not can_call_remote:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM_API_KEY is not configured and LLM_FALLBACK_TO_MOCK is disabled.",
            )

        reply_text, model_used = await llm_mod._call_openai_compatible_api(messages)
        return {
            "draft_reply": reply_text,
            "citations": citations_dicts,
            "retrieved_chunks": base_chunks,
            "provider": provider,
            "model_used": model_used,
            "used_fallback": False,
        }

    # ── LangGraph Nodes ──────────────────────────────────────────────────────

    async def _node_input_guardrail(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Node 1 (Rules): Prompt-injection, out-of-corpus trap, and off-topic pre-filter."""
        import app.services.llm_service as llm_mod

        visited = _append_visited(state, NODE_INPUT_GUARDRAIL)
        user_message = str(state.get("user_message") or "")
        lang_code, dialect = llm_mod.detect_dialect(
            user_message,
            language_hint=state.get("language_hint", "auto"),
            explicit_dialect=state.get("explicit_dialect"),
        )

        input_guard = check_input_guardrail(
            user_message,
            lang_code=lang_code,
            dialect=dialect,
        )

        if input_guard.is_blocked:
            refusal = input_guard.refusal_message or llm_mod.FRENCH_REFORMULATION_FALLBACK_FR
            guard_action = (
                input_guard.guardrail_type.value
                if input_guard.guardrail_type
                else "INPUT_BLOCKED"
            )
            elapsed = max(
                1,
                int((time.perf_counter_ns() - int(state.get("started_at_ns", 0))) / 1_000_000),
            )
            return {
                "nodes_visited": visited,
                "detected_language": lang_code,
                "detected_dialect": dialect,
                "input_blocked": True,
                "guardrail_triggered": True,
                "guardrail_action": guard_action,
                "draft_reply": refusal,
                "final_reply": refusal,
                "intent": "OUT_OF_CORPUS_TRAP",
                "active_sub_agent": "1.1_Listener",
                "action_prompt": None,
                "suggested_actions": [
                    "Découvrir les vraies filières IIT / ISB / ISA",
                    "Parler à un conseiller NAU",
                ],
                "provider": "guardrail_node",
                "model_used": "smartcampus-guardrail-v1",
                "used_fallback": True,
                "comprehension_confidence": 1.0,
                "needs_reformulation": False,
                "reformulation_prompt": None,
                "recent_turns": [],
                "declared_facts": {},
                "rolling_summary": None,
                "history_turn_count": None,
                "retrieved_chunks": [],
                "citations": [],
                "tool_calls": [],
                "hitl_interrupt": None,
                "outbox_events": [],
                "latency_ms": elapsed,
            }

        return {
            "nodes_visited": visited,
            "detected_language": lang_code,
            "detected_dialect": dialect,
            "input_blocked": False,
            "guardrail_triggered": False,
            "guardrail_action": None,
        }

    async def _node_listener_1_1(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Node 2 (`1.1_Listener`): Load 3-tier memory, normalize Arabizi, detect intent."""
        import app.services.llm_service as llm_mod

        visited = _append_visited(state, NODE_LISTENER)
        configurable = config.get("configurable") or {}
        req_payload: ChatMessageRequest = configurable["request_payload"]
        redis_client = configurable.get("redis_client")
        db: AsyncSession | None = configurable.get("db")

        session_id = str(state.get("session_id") or "")
        candidate_id = state.get("candidate_id")
        is_authenticated = bool(state.get("is_authenticated", False))

        # 1. Load Hot Redis conversation history (last 6-8 turns, bounded <= 16 messages)
        hot_svc = llm_mod.HotConversationService(redis_client=redis_client)
        guest_svc = llm_mod.GuestSessionService(redis_client=redis_client)

        history_turns: list[Any] = []
        if req_payload.history:
            history_turns = list(req_payload.history)
        else:
            try:
                cached_turns = await hot_svc.get_history(session_id)
                if cached_turns:
                    history_turns = list(cached_turns)
            except Exception as exc:
                logger.debug("Unable to read hot history from Redis for %s: %s", session_id, exc)

        # 2. Load Warm declared facts & rolling summary (Redis guest + PG CandidateProfile if auth)
        declared_facts: dict[str, Any] = dict(state.get("declared_facts") or {})
        existing_summary: str | None = state.get("rolling_summary") or None

        try:
            guest_data = await guest_svc.get_session(session_id)
            if guest_data:
                if isinstance(guest_data.get("declared_facts"), dict):
                    declared_facts.update(guest_data["declared_facts"])
                for field_name in (
                    "school_target",
                    "current_specialty",
                    "bac_section",
                    "declared_overall_avg",
                    "preferred_language",
                ):
                    if guest_data.get(field_name) is not None:
                        declared_facts[field_name] = guest_data[field_name]
                if guest_data.get("rolling_summary"):
                    existing_summary = str(guest_data["rolling_summary"])
        except Exception as exc:
            logger.debug("Unable to read guest session from Redis for %s: %s", session_id, exc)

        # Strict Give-or-Keep + anti-IDOR separation: ONLY read CandidateProfile when
        # authenticated with a verified JWT candidate_id (never fall back to session_id).
        if is_authenticated and candidate_id and db is not None:
            try:
                cand_uuid = UUID(str(candidate_id))
                cand_profile = await db.get(CandidateProfile, cand_uuid)
                if cand_profile:
                    if cand_profile.facts and isinstance(cand_profile.facts, dict):
                        declared_facts.update(cand_profile.facts)
                    if cand_profile.current_specialty:
                        declared_facts["current_specialty"] = cand_profile.current_specialty
                    if cand_profile.preferred_language:
                        declared_facts["preferred_language"] = cand_profile.preferred_language
                    if cand_profile.declared_overall_avg is not None:
                        declared_facts["declared_overall_avg"] = float(
                            cand_profile.declared_overall_avg
                        )
                    if cand_profile.summary and not existing_summary:
                        existing_summary = cand_profile.summary
            except Exception as exc:
                logger.debug(
                    "Unable to read CandidateProfile for %s: %s",
                    candidate_id,
                    exc,
                )

        user_message = str(state.get("user_message") or "")
        rolling_summary = llm_mod.build_rolling_summary(
            declared_facts=declared_facts,
            recent_turns=history_turns,
            existing_summary=existing_summary,
            latest_query=user_message,
        )

        # 3. Arabizi pre-normalization + intent & confidence assessment
        lang_code = str(state.get("detected_language") or "fr")
        dialect = state.get("detected_dialect", "French")
        arabizi_norm = normalize_arabizi_to_french(user_message)

        intent, sub_agent, action_prompt, suggested_actions = llm_mod.classify_intent_and_subagent(
            user_message,
            is_authenticated=is_authenticated,
            lang_code=lang_code,
        )

        if intent == "CLARIFICATION_REQUIRED":
            confidence, needs_reformulation = 0.25, True
        else:
            confidence, needs_reformulation = llm_mod.assess_comprehension_confidence(
                user_message,
                lang_code=lang_code,
                intent=intent,
            )

        reformulation_msg = (
            llm_mod._build_reformulation_fallback_reply(lang_code, dialect)
            if needs_reformulation
            else None
        )

        updates: dict[str, Any] = {
            "nodes_visited": visited,
            "recent_turns": [_serialize_turn(t) for t in history_turns],
            "history_turn_count": len(history_turns),
            "declared_facts": declared_facts,
            "rolling_summary": rolling_summary,
            "normalized_french_query": (
                arabizi_norm.normalized_french
                if (arabizi_norm.is_arabizi or dialect == "Arabizi")
                else None
            ),
            "intent": intent,
            "active_sub_agent": sub_agent,
            "action_prompt": action_prompt,
            "suggested_actions": suggested_actions,
            "comprehension_confidence": confidence,
            "needs_reformulation": needs_reformulation,
            "reformulation_prompt": reformulation_msg,
        }

        if needs_reformulation:
            provider = str(llm_mod.settings.LLM_PROVIDER).lower().strip()
            updates.update(
                {
                    "draft_reply": reformulation_msg or llm_mod.FRENCH_REFORMULATION_FALLBACK_FR,
                    "provider": provider,
                    "model_used": "smartcampus-clarification-fallback-v1",
                    "used_fallback": True,
                    "citations": [
                        c.model_dump()
                        for c in llm_mod._build_citations(state.get("retrieved_chunks") or None)
                    ],
                }
            )

        return updates

    async def _node_supervisor_router(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Node 3 (`VISION` Supervisor): Route intent to specialized sub-agent 1.2..1.6."""
        visited = _append_visited(state, NODE_SUPERVISOR_ROUTER)
        user_message = str(state.get("user_message") or "")
        intent = str(state.get("intent") or "GENERAL_ORIENTATION")
        sub_agent: VisionSubAgent = state.get("active_sub_agent", "1.1_Listener")
        is_authenticated = bool(state.get("is_authenticated", False))

        # Detect explicit VLM/OCR extraction requests -> 1.4_Reader
        if _should_route_to_reader(user_message, intent):
            sub_agent = "1.4_Reader"
            intent = "TRANSCRIPT_OCR_EXTRACTION"

        next_status = _derive_next_candidate_status(
            state.get("candidate_status", "ANONYMOUS"),
            is_authenticated=is_authenticated,
            sub_agent=sub_agent,
            intent=intent,
        )

        # Direct 1.1_Listener intents (greeting, governance, privacy, guest policy)
        if sub_agent == "1.1_Listener":
            gen = await self._generate_reply_with_provider(
                sub_agent="1.1_Listener",
                state=state,
                config=config,
            )
            return {
                "nodes_visited": visited,
                "active_sub_agent": "1.1_Listener",
                "routed_node": NODE_OUTPUT_GUARDRAIL,
                "candidate_status": next_status,
                **gen,
            }

        target_node = SUBAGENT_TO_NODE[sub_agent]
        return {
            "nodes_visited": visited,
            "intent": intent,
            "active_sub_agent": sub_agent,
            "routed_node": target_node,
            "candidate_status": next_status,
        }

    def _make_subagent_node(
        self,
        sub_agent: VisionSubAgent,
        node_name: str,
    ) -> Callable[[VisionAgentState, RunnableConfig], Awaitable[dict[str, Any]]]:
        """Factory building each specialized VISION sub-agent LangGraph node (1.2..1.6)."""

        async def _subagent_node(
            state: VisionAgentState,
            config: RunnableConfig,
        ) -> dict[str, Any]:
            visited = _append_visited(state, node_name)
            sub_cfg = self.get_subagent_config(sub_agent)
            context = self._build_tool_context(state, config)

            # 1. Enforce "Give or Keep" auth gate on protected sub-agents (1.4 / 1.6)
            if sub_cfg.requires_auth and not context.is_authenticated:
                interrupt_spec = HitlInterruptSpec(
                    reason="AUTH_REQUIRED",
                    sub_agent=sub_agent,
                    prompt_message=(
                        "Authentification requise (règle Give or Keep) avant le dépôt d'un "
                        "relevé officiel, la préinscription ou la réservation d'un entretien."
                    ),
                    action_prompt="AUTH_REQUIRED",
                    required_confirmation_fields=["authentication"],
                    payload={
                        "sub_agent": sub_agent,
                        "intent": state.get("intent"),
                        "session_id": context.session_id,
                    },
                )
                gen = await self._generate_reply_with_provider(
                    sub_agent=sub_agent,
                    state={**state, "action_prompt": "AUTH_REQUIRED"},
                    config=config,
                )
                return {
                    "nodes_visited": visited,
                    "active_sub_agent": sub_agent,
                    "action_prompt": "AUTH_REQUIRED",
                    "hitl_interrupt": interrupt_spec.model_dump(),
                    **gen,
                }

            # 2. Invoke registered custom handler if plugged in by SA-74 / SA-82 / SA-84
            custom_handler = self._subagent_handlers.get(sub_agent)
            if custom_handler is not None:
                custom_out = await custom_handler(state, context, sub_cfg)
                proposed_status = custom_out.get("candidate_status")
                if isinstance(proposed_status, str):
                    assert_vision_status_transition_allowed(
                        proposed_status,
                        current_status=state.get("candidate_status"),
                    )
                custom_out["nodes_visited"] = visited
                custom_out.setdefault("active_sub_agent", sub_agent)
                return custom_out

            # 3. Execute optional sub-agent tools through ActionGuard + ToolDispatcher
            tool_records, tool_chunks, gate_prompt = await self._run_subagent_tools(
                sub_agent=sub_agent,
                state=state,
                context=context,
                config=config,
            )

            # 4. Surface HITL confirmation metadata for 1.4_Reader low-confidence / mismatch
            hitl_dict = state.get("hitl_interrupt")
            action_prompt = gate_prompt or state.get("action_prompt")
            if sub_agent == "1.4_Reader" and context.is_authenticated:
                action_prompt = action_prompt or "UPLOAD_RELEVE"
                transcript_meta = (state.get("declared_facts") or {}).get(
                    "transcript_extraction"
                ) or {}
                if transcript_meta.get("discrepancy_detected"):
                    hitl_dict = HitlInterruptSpec(
                        reason="RH_MISMATCH_ESCALATION",
                        sub_agent="1.4_Reader",
                        prompt_message=(
                            "Écart détecté entre les notes déclarées et le relevé extrait ; "
                            "confirmation candidat / vérification RH requise."
                        ),
                        action_prompt="UPLOAD_RELEVE",
                        required_confirmation_fields=list(
                            transcript_meta.get("low_confidence_fields") or ["general_average"]
                        ),
                        payload={"transcript_extraction": transcript_meta},
                    ).model_dump()
                elif transcript_meta.get("needs_manual_review"):
                    hitl_dict = HitlInterruptSpec(
                        reason="RELEVE_CONFIRMATION",
                        sub_agent="1.4_Reader",
                        prompt_message=(
                            "Certains champs extraits du relevé ont un score de confiance "
                            "inférieur au seuil ; veuillez confirmer ces valeurs."
                        ),
                        action_prompt="UPLOAD_RELEVE",
                        required_confirmation_fields=list(
                            transcript_meta.get("low_confidence_fields") or []
                        ),
                        payload={"transcript_extraction": transcript_meta},
                    ).model_dump()

            gen = await self._generate_reply_with_provider(
                sub_agent=sub_agent,
                state=state,
                config=config,
                extra_chunks=tool_chunks or None,
            )
            return {
                "nodes_visited": visited,
                "active_sub_agent": sub_agent,
                "action_prompt": action_prompt,
                "tool_calls": [*(state.get("tool_calls") or []), *tool_records],
                "hitl_interrupt": hitl_dict,
                **gen,
            }

        _subagent_node.__name__ = f"node_{node_name}"
        return _subagent_node

    async def _node_output_guardrail(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Cross-cutting Output Guardrail: HITL no-promise lock + ungrounded fee caveat."""
        visited = _append_visited(state, NODE_OUTPUT_GUARDRAIL)
        draft_reply = str(state.get("draft_reply") or "")
        lang_code = str(state.get("detected_language") or "fr")
        dialect = state.get("detected_dialect", "French")

        # Defense in depth: verify current state never carries a forbidden admission status
        current_status = state.get("candidate_status")
        if isinstance(current_status, str) and current_status in FORBIDDEN_AGENT_STATUSES:
            raise PermissionError(
                f"HITL governance violation detected in OutputGuardrail: status='{current_status}'."
            )

        output_guard = check_output_guardrail(
            draft_reply,
            context_chunks=state.get("retrieved_chunks") or None,
            lang_code=lang_code,
            dialect=dialect,
        )

        final_reply = output_guard.sanitized_text if output_guard.was_modified else draft_reply
        guardrail_triggered = bool(state.get("guardrail_triggered", False))
        guardrail_action = state.get("guardrail_action")

        if output_guard.was_modified:
            guardrail_triggered = True
            guardrail_action = (
                output_guard.guardrail_type.value
                if output_guard.guardrail_type
                else "OUTPUT_MODIFIED"
            )

        elapsed = max(
            1,
            int((time.perf_counter_ns() - int(state.get("started_at_ns", 0))) / 1_000_000),
        )
        return {
            "nodes_visited": visited,
            "final_reply": final_reply,
            "guardrail_triggered": guardrail_triggered,
            "guardrail_action": guardrail_action,
            "latency_ms": elapsed,
        }

    async def _node_persist_memory(
        self,
        state: VisionAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Persist new turns to Hot Redis memory and rolling summary to Warm Postgres."""
        import app.services.llm_service as llm_mod

        visited = _append_visited(state, NODE_PERSIST_MEMORY)
        configurable = config.get("configurable") or {}
        redis_client = configurable.get("redis_client")
        db: AsyncSession | None = configurable.get("db")

        session_id = str(state.get("session_id") or "")
        candidate_id = state.get("candidate_id")
        is_authenticated = bool(state.get("is_authenticated", False))

        user_message = str(state.get("user_message") or "")
        final_reply = str(state.get("final_reply") or "")
        rolling_summary = str(state.get("rolling_summary") or "")

        hot_svc = llm_mod.HotConversationService(redis_client=redis_client)
        guest_svc = llm_mod.GuestSessionService(redis_client=redis_client)

        try:
            await hot_svc.add_turn(session_id, role="user", content=user_message)
            await hot_svc.add_turn(session_id, role="assistant", content=final_reply)
            await guest_svc.update_fields(
                session_id,
                {"rolling_summary": rolling_summary},
            )
        except Exception as exc:
            logger.debug("Unable to persist turn to Redis for %s: %s", session_id, exc)

        # Anti-IDOR: persist CandidateProfile ONLY using the authenticated JWT candidate_id.
        if is_authenticated and candidate_id and db is not None:
            try:
                cand_uuid = UUID(str(candidate_id))
                cand_profile = await db.get(CandidateProfile, cand_uuid)
                if cand_profile:
                    cand_profile.summary = rolling_summary[:1200]
                    await db.commit()
            except Exception as exc:
                logger.debug("Unable to update CandidateProfile summary in DB: %s", exc)

        elapsed = max(
            1,
            int((time.perf_counter_ns() - int(state.get("started_at_ns", 0))) / 1_000_000),
        )
        return {
            "nodes_visited": visited,
            "latency_ms": elapsed,
        }

    # ── Public Turn & HITL Checkpoint Execution APIs ─────────────────────────

    async def run_turn(
        self,
        turn_input: VisionTurnInput,
        *,
        redis_client: Any | None = None,
        db: AsyncSession | None = None,
        tool_dispatcher: ToolDispatcherProtocol | None = None,
        action_guard: ActionGuardProtocol | None = None,
        requested_tool_calls: list[dict[str, Any]] | None = None,
    ) -> VisionTurnOutput:
        """Execute one full VISION supervisor turn through the compiled LangGraph graph."""
        start_ns = time.perf_counter_ns()
        payload = turn_input.payload
        resolved_session_id = turn_input.session_id or payload.session_id or str(uuid4())
        resolved_thread_id = turn_input.thread_id or f"vision:{resolved_session_id}"

        initial_status: CandidateStatus = turn_input.candidate_status
        if turn_input.is_authenticated and initial_status == "ANONYMOUS":
            initial_status = "AUTHENTICATED"
        assert_vision_status_transition_allowed(initial_status)

        initial_state: VisionAgentState = {
            "thread_id": resolved_thread_id,
            "session_id": resolved_session_id,
            "candidate_id": turn_input.candidate_id,
            "is_authenticated": turn_input.is_authenticated,
            "actor_role": turn_input.actor_role,
            "campus_scope": payload.campus_scope or "all",
            "candidate_status": initial_status,
            "case_owner": turn_input.case_owner,
            "user_message": payload.message,
            "is_voice": payload.isVoice,
            "language_hint": payload.language_hint,
            "explicit_dialect": payload.detectedDialect,
            # Explicitly reset all per-turn transient keys so MemorySaver never
            # leaks Turn N-1 state into Turn N (e.g., when Turn N blocks at input_guardrail).
            "normalized_french_query": None,
            "intent": "GENERAL_ORIENTATION",
            "comprehension_confidence": 1.0,
            "needs_reformulation": False,
            "reformulation_prompt": None,
            "recent_turns": [],
            "declared_facts": {},
            "rolling_summary": None,
            "history_turn_count": None,
            "active_sub_agent": "1.1_Listener",
            "routed_node": "",
            "nodes_visited": [],
            "tool_calls": [],
            "retrieved_chunks": list(payload.context_chunks or []),
            "citations": [],
            "action_prompt": None,
            "suggested_actions": [],
            "input_blocked": False,
            "guardrail_triggered": False,
            "guardrail_action": None,
            "hitl_interrupt": None,
            "hitl_resume_payload": None,
            "outbox_events": [],
            "draft_reply": "",
            "final_reply": "",
            "provider": "",
            "model_used": "",
            "used_fallback": False,
            "started_at_ns": start_ns,
            "latency_ms": 0,
        }

        graph_config: dict[str, Any] = {
            "configurable": {
                "thread_id": resolved_thread_id,
                "request_payload": payload,
                "redis_client": redis_client,
                "db": db,
                "tool_dispatcher": tool_dispatcher,
                "action_guard": action_guard,
                "requested_tool_calls": requested_tool_calls or [],
            }
        }

        final_state: VisionAgentState = await self.compiled_graph.ainvoke(
            initial_state,
            config=graph_config,
        )

        reply_text = str(final_state.get("final_reply") or final_state.get("draft_reply") or "")
        sub_agent: VisionSubAgent = final_state.get("active_sub_agent", "1.1_Listener")
        citations = [
            ChatCitation.model_validate(c) for c in (final_state.get("citations") or [])
        ]
        elapsed_ms = int(
            final_state.get("latency_ms")
            or max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))
        )

        response = ChatMessageResponse(
            session_id=resolved_session_id,
            sessionId=resolved_session_id,
            reply_text=reply_text,
            reply=reply_text,
            sub_agent=sub_agent,
            subAgent=sub_agent,
            detected_language=str(final_state.get("detected_language") or "fr"),
            detectedDialect=final_state.get("detected_dialect", "French"),
            intent=str(final_state.get("intent") or "GENERAL_ORIENTATION"),
            citations=citations,
            suggested_actions=list(final_state.get("suggested_actions") or []),
            actionPrompt=final_state.get("action_prompt"),
            provider=str(final_state.get("provider") or "mock"),
            model_used=str(final_state.get("model_used") or "smartcampus-fallback-v1"),
            latency_ms=elapsed_ms,
            used_fallback=bool(final_state.get("used_fallback", False)),
            comprehension_confidence=float(final_state.get("comprehension_confidence", 1.0)),
            needs_reformulation=bool(final_state.get("needs_reformulation", False)),
            reformulation_prompt=final_state.get("reformulation_prompt"),
            rolling_summary=final_state.get("rolling_summary"),
            history_turn_count=final_state.get("history_turn_count"),
            guardrail_triggered=bool(final_state.get("guardrail_triggered", False)),
            guardrail_action=final_state.get("guardrail_action"),
        )

        raw_hitl = final_state.get("hitl_interrupt")
        hitl_spec = HitlInterruptSpec.model_validate(raw_hitl) if raw_hitl else None
        tool_call_models = [
            ToolCallRecord.model_validate(rec) for rec in (final_state.get("tool_calls") or [])
        ]
        outbox_models = [
            OutboxHandoffSpec.model_validate(evt)
            for evt in (final_state.get("outbox_events") or [])
        ]

        return VisionTurnOutput(
            response=response,
            thread_id=resolved_thread_id,
            candidate_status=final_state.get("candidate_status", initial_status),
            case_owner=str(final_state.get("case_owner") or turn_input.case_owner),
            nodes_visited=list(final_state.get("nodes_visited") or []),
            tool_calls=tool_call_models,
            hitl_interrupt=hitl_spec,
            outbox_events=outbox_models,
        )

    async def run_hitl_checkpoint(
        self,
        *,
        thread_id: str,
        interrupt_spec: HitlInterruptSpec,
        candidate_status: CandidateStatus = "AUTHENTICATED",
    ) -> dict[str, Any]:
        """Start a real LangGraph `interrupt()` HITL pause checkpoint under `thread_id`.

        Suspends execution at `await_human_confirmation` and returns the interrupt
        payload persisted in `self.checkpointer` so the UI / student / RH can confirm
        and resume via `resume_hitl_turn(thread_id=..., resume_payload=...)`.
        """
        assert_vision_status_transition_allowed(candidate_status)
        cfg = {"configurable": {"thread_id": thread_id}}
        result = await self.hitl_checkpoint_graph.ainvoke(
            {
                "thread_id": thread_id,
                "candidate_status": candidate_status,
                "interrupt_spec": interrupt_spec.model_dump(),
                "checkpoint_status": "PENDING",
                "checkpoint_approved": False,
                "outbox_events": [],
            },
            config=cfg,
        )
        snapshot = await self.hitl_checkpoint_graph.aget_state(cfg)
        interrupts = result.get("__interrupt__") or []
        return {
            "thread_id": thread_id,
            "interrupted": bool(interrupts),
            "interrupt_value": interrupts[0].value if interrupts else None,
            "next_nodes": list(snapshot.next),
            "checkpoint_status": "AWAITING_HUMAN",
        }

    async def resume_hitl_turn(
        self,
        *,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resume a paused LangGraph HITL checkpoint using `Command(resume=...)`."""
        proposed_status = resume_payload.get("target_status")
        if isinstance(proposed_status, str):
            assert_vision_status_transition_allowed(proposed_status)

        cfg = {"configurable": {"thread_id": thread_id}}
        resumed = await self.hitl_checkpoint_graph.ainvoke(
            Command(resume=resume_payload),
            config=cfg,
        )
        return {
            "thread_id": thread_id,
            "interrupted": False,
            "checkpoint_status": resumed.get("checkpoint_status"),
            "checkpoint_approved": bool(resumed.get("checkpoint_approved", False)),
            "candidate_status": resumed.get("candidate_status"),
            "human_response": resumed.get("human_response"),
            "outbox_events": list(resumed.get("outbox_events") or []),
        }


# ---------------------------------------------------------------------------
# Singleton Supervisor Accessor
# ---------------------------------------------------------------------------

_default_vision_supervisor: VisionSupervisor | None = None


def get_default_vision_supervisor() -> VisionSupervisor:
    """Return the process-wide compiled `VisionSupervisor` singleton."""
    global _default_vision_supervisor
    if _default_vision_supervisor is None:
        _default_vision_supervisor = VisionSupervisor()
    return _default_vision_supervisor


def reset_default_vision_supervisor() -> None:
    """Reset the singleton `VisionSupervisor` (test helper)."""
    global _default_vision_supervisor
    _default_vision_supervisor = None
