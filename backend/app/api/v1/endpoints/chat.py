"""VISION dialogue and LLM backend connection REST endpoints (SA-36)."""

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.deps import decode_hs256_jwt
from app.core.rate_limit import enforce_guest_chat_rate_limit
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse, LlmHealthResponse
from app.services import llm_service as svc

router = APIRouter()
_optional_bearer = HTTPBearer(auto_error=False)


@router.get(
    "/health",
    response_model=LlmHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Report configured LLM provider, model, and readiness status",
)
async def get_chat_llm_health() -> LlmHealthResponse:
    return svc.get_llm_health()


@router.post(
    "/message",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a student message to VISION / LLM backend (French, Derja, Arabizi)",
)
async def post_chat_message(
    payload: ChatMessageRequest,
    request: Request,
    response: Response,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> ChatMessageResponse:
    is_authenticated = False
    actor_session_id: str | None = None

    if credentials is not None and credentials.scheme.lower() == "bearer":
        claims = decode_hs256_jwt(credentials.credentials, expected_type="access")
        is_authenticated = True
        actor_session_id = str(claims.get("candidate_id") or claims["sub"])
    else:
        await enforce_guest_chat_rate_limit(request, response)

    resolved_session_id = payload.session_id or x_session_id or actor_session_id
    return await svc.generate_chat_reply(
        payload,
        session_id=resolved_session_id,
        is_authenticated=is_authenticated,
    )

