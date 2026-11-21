"""Pydantic v2 request and response schemas for LLM chat and VISION dialogue (SA-36)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LanguageHint = Literal["auto", "fr", "derja", "arabizi"]
DetectedDialect = Literal["French", "Tunisian_Derja", "Arabizi"]
VisionSubAgent = Literal[
    "1.1_Listener",
    "1.2_Librarian",
    "1.3_Gatekeeper",
    "1.4_Reader",
    "1.5_Advisor",
    "1.6_Registrar",
]
ActionPrompt = Literal["AUTH_REQUIRED", "UPLOAD_RELEVE", "CONFIRM_SHORTLIST", "BOOK_SLOT"]


class ChatCitation(BaseModel):
    """Unified citation schema compatible with both api_contract.md and frontend TrackCard."""

    id: str
    documentTitle: str
    source_document: str
    school: str | None = None
    program: str | None = None
    section: str | None = None
    page: int | None = None
    snippet: str | None = None
    chunk_id: str | None = None


class ChatTurnInput(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatMessageRequest(BaseModel):
    """Supports both api_contract.md (§2.1) and frontend chat-service.ts payload fields."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "3aslema, chnowa les filières d'ingénieur fel IIT ?",
                "language_hint": "auto",
            }
        }
    )

    message: str = Field(..., min_length=1, max_length=4000)
    language_hint: LanguageHint = "auto"
    session_id: str | None = Field(default=None, max_length=128)
    sessionId: str | None = Field(default=None, max_length=128)
    isVoice: bool = False
    detectedDialect: DetectedDialect | None = None
    campus_scope: Literal["all", "IIT", "ISB", "ISA"] | None = Field(
        default="all",
        description="Target campus school scope (all, IIT, ISB, ISA)",
    )
    history: list[ChatTurnInput] | None = Field(
        default=None,
        description="Optional recent conversation turns (ready for SA-37 Redis injection).",
    )
    context_chunks: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional retrieved RAG / catalog passages with official citations.",
    )

    @model_validator(mode="after")
    def normalize_message_and_session(self) -> "ChatMessageRequest":
        cleaned = self.message.strip()
        if not cleaned:
            raise ValueError("message must not be blank or whitespace only.")
        self.message = cleaned
        resolved_sid = self.session_id or self.sessionId
        self.session_id = resolved_sid
        self.sessionId = resolved_sid
        return self


class ChatMessageResponse(BaseModel):
    """Unified response compatible with both api_contract.md and frontend ChatResponse."""

    session_id: str
    sessionId: str
    reply_text: str
    reply: str
    sub_agent: VisionSubAgent
    subAgent: VisionSubAgent
    detected_language: str
    detectedDialect: DetectedDialect
    intent: str
    citations: list[ChatCitation] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    actionPrompt: ActionPrompt | None = None
    provider: str
    model_used: str
    latency_ms: int
    used_fallback: bool = False
    comprehension_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_reformulation: bool = False
    reformulation_prompt: str | None = None


class LlmHealthResponse(BaseModel):
    status: Literal["ready", "fallback_only", "misconfigured"]
    provider: str
    model: str
    base_url: str
    api_key_configured: bool
    fallback_enabled: bool

