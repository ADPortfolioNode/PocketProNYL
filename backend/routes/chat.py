"""
Chat API routes with RAG support.
"""
from io import BytesIO

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
from config import settings
from services.lm_router import lm_router
from services.rag_service import rag_service
from services.gemini_client import LM_UNAVAILABLE_PREFIX
from services.llm_common import strip_api_keys
from utils.chat_tools import (
    _render_tool_response,
    detect_chat_tool,
    execute_chat_tool,
    is_all_games_scope,
    resolve_tool_game,
)


router = APIRouter()


@router.get("/api/llm/status")
async def llm_status():
    """Provider-chain status: chain order, per-provider configured /
    available / model / last error, and whether retrieved text is the
    last rung. Never returns any API key value."""
    # Audit lazily so a fresh process reports live provider state, but
    # do not force a re-probe on every call (audit_connections caches).
    await lm_router.audit_connections()
    return lm_router.status()


@router.get("/api/tuning_status")
async def tuning_status():
    """Return current background per-game suggestion-tuning activity."""
    from services.game_tuner import game_tuner

    return game_tuner.status()


@router.get("/api/tuning_chart")
def tuning_chart(game: Optional[str] = None):
    """Render current tuning telemetry as a matplotlib PNG for the tuning section."""
    from services.game_tuner import game_tuner
    from services.tuning_chart import render_tuning_chart

    image = BytesIO(render_tuning_chart(game_tuner.status(), game=game))
    return StreamingResponse(image, media_type="image/png", headers={"Cache-Control": "no-store"})


class ChatRequest(BaseModel):
    text: str = ""
    game: str = None
    use_rag: bool = True
    lm_provider: str = "auto"
    tool: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    response: str
    sources: list = []
    context_used: bool = False
    sources_count: int = 0
    lm_provider: Optional[str] = None
    # Joined, key-stripped chain error (capped at 1000 chars) when every
    # LM rung failed; None when a provider (or retrieved text) answered.
    lm_error: Optional[str] = None
    tool_name: Optional[str] = None
    tool_result: Optional[Dict[str, Any]] = None


def _surface_lm_error(lm_response: str, chain_error: Optional[str] = None) -> str:
    """Replace the old generic apology with the surfaced chain error.

    Previously /api/chat hid every LM failure behind "I apologize, but
    I'm currently unable to connect...", which made provider failures
    undiagnosable. Now the caller sees which rungs failed and why
    (keys already stripped by the router/clients).
    """
    detail = chain_error
    if not detail and lm_response.startswith(LM_UNAVAILABLE_PREFIX):
        detail = lm_response[len(LM_UNAVAILABLE_PREFIX):].lstrip(":")
    if detail:
        return f"All language-model providers failed: {detail}"
    return lm_response


@router.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    AI chat endpoint with optional RAG context and tool calling.
    """
    try:
        tool = request.tool
        if not tool:
            inferred = detect_chat_tool(request.text)
            if inferred:
                tool = {
                    "name": inferred,
                    "params": {"game": resolve_tool_game(request.text, request.game)},
                }

        if tool:
            tool_name = tool.get("name")
            if not tool_name:
                raise HTTPException(status_code=400, detail="Tool name is required")

            tool_params = tool.get("params") or {}
            tool_result = await execute_chat_tool(tool_name, tool_params)
            response_text = _render_tool_response(tool_name, tool_result)

            return ChatResponse(
                response=response_text,
                sources=[],
                context_used=False,
                sources_count=0,
                lm_provider=None,
                tool_name=tool_name,
                tool_result=tool_result,
            )

        # Prepare context if RAG is enabled
        context_docs = []
        if request.use_rag and request.game and not is_all_games_scope(request.game):
            try:
                from utils.validation import _require_game_key
                game_key = _require_game_key(request.game)
                context_docs = rag_service.retrieve_context(
                    query=request.text,
                    game=game_key,
                    top_k=3,
                )
            except (ValueError, Exception):
                # Invalid game key or retrieval failure — continue without RAG
                context_docs = []

        # Build prompt with context if available
        if context_docs:
            context_text = "\n".join([doc.get("content", "") for doc in context_docs])
            augmented_prompt = f"Context:\n{context_text}\n\nUser Question: {request.text}"
            context_used = True
        else:
            augmented_prompt = request.text
            context_used = False

        # Get response from language model via router (fixed-order chain:
        # gemini -> groq -> ollama -> grok -> chatgpt, then retrieved text)
        selected_provider = request.lm_provider or "auto"
        lm_error = None
        try:
            result = await lm_router.generate_with_provider(
                augmented_prompt,
                preferred_provider=request.lm_provider,
            )
            selected_provider = result.get("provider", selected_provider)
            lm_response = result.get("response", "")
            lm_error = result.get("error")

            if lm_response.startswith(LM_UNAVAILABLE_PREFIX):
                # Auto mode only: the retrieved-text rung is the last
                # rung of the chain. An explicit lm_provider means that
                # provider only, so its failure is surfaced, not papered
                # over with retrieved text.
                if (request.lm_provider or "auto").strip().lower() in ("auto", ""):
                    lm_response = rag_service._build_local_fallback_response(
                        request.text, context_docs
                    )
                    selected_provider = "retrieved_text"
                else:
                    lm_response = _surface_lm_error(lm_response, lm_error)

        except Exception as e:
            lm_error = strip_api_keys(str(e), [
                k for k in (
                    settings.GEMINI_API_KEY,
                    settings.GROK_API_KEY,
                    settings.GROQ_API_KEY,
                    settings.CHAT_GPT_API_KEY,
                    settings.OPENAI_API_KEY,
                ) if k
            ])
            lm_response = _surface_lm_error("", lm_error)
            selected_provider = "fallback"

        return ChatResponse(
            response=lm_response,
            sources=context_docs,
            context_used=context_used,
            sources_count=len(context_docs),
            lm_provider=selected_provider,
            lm_error=lm_error if selected_provider in ("fallback", "retrieved_text") else None,
            tool_name=None,
            tool_result=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))