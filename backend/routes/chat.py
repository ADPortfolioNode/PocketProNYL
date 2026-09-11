"""
Chat API routes with RAG support.
"""
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
from services.lm_router import lm_router
from services.rag_service import rag_service
from services.gemini_client import LM_UNAVAILABLE_PREFIX
from utils.chat_tools import _render_tool_response, execute_chat_tool


router = APIRouter()


@router.get("/api/tuning_status")
async def tuning_status():
    """Return current background per-game suggestion-tuning activity."""
    from services.game_tuner import game_tuner

    return game_tuner.status()


@router.get("/api/tuning_chart")
async def tuning_chart():
    """Render current tuning telemetry as a small cacheable PNG for the hero panel."""
    from services.game_tuner import game_tuner

    status = game_tuner.status()
    total = max(int(status.get("games_total") or 0), 0)
    completed = min(max(int(status.get("games_completed") or 0), 0), total) if total else 0
    relative_progress = completed / total if total else 0
    state = str(status.get("status") or "idle").upper()
    active_game = str(status.get("current_game") or status.get("game") or "all games")
    task = str(status.get("current_task") or "waiting")

    figure, axis = plt.subplots(figsize=(7.2, 2.25), dpi=140)
    figure.patch.set_facecolor("#10182b")
    axis.set_facecolor("#10182b")
    axis.barh([0], [1], color="#273552", height=0.22)
    axis.barh([0], [relative_progress], color="#d38b52" if state == "RUNNING" else "#6ea8fe", height=0.22)
    axis.set_xlim(0, 1)
    axis.set_yticks([])
    axis.set_xticks([0, 0.25, 0.5, 0.75, 1])
    axis.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], color="#b7c6df", fontsize=8)
    axis.tick_params(axis="x", length=0, pad=5)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.text(0, 0.42, "LIVE TUNING TELEMETRY", color="#dbe9ff", fontsize=9, fontweight="bold", transform=axis.transAxes)
    axis.text(0, 0.23, f"{active_game.upper()}  /  {task.replace('_', ' ').upper()}", color="#b7c6df", fontsize=8, transform=axis.transAxes)
    axis.text(1, 0.42, f"{completed}/{total}  {state}", color="#d38b52" if state == "RUNNING" else "#dbe9ff", fontsize=9, fontweight="bold", ha="right", transform=axis.transAxes)
    figure.tight_layout(pad=0.8)

    image = BytesIO()
    figure.savefig(image, format="png", transparent=False)
    plt.close(figure)
    image.seek(0)
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
    tool_name: Optional[str] = None
    tool_result: Optional[Dict[str, Any]] = None


def _chat_fallback_for_lm_unavailable(user_text: str, lm_response: str, raw_game: Optional[str] = None) -> str:
    """
    Generate a helpful fallback response when LLM is unavailable.
    """
    if lm_response.startswith(LM_UNAVAILABLE_PREFIX):
        game_context = f" for {raw_game}" if raw_game else ""
        return (
            f"I apologize, but I'm currently unable to connect to the AI language model{game_context}. "
            f"This might be due to API key issues or service unavailability. "
            f"Your message was: \"{user_text}\". "
            f"Please check your API key configuration or try again later."
        )
    return lm_response


@router.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    AI chat endpoint with optional RAG context and tool calling.
    """
    try:
        if request.tool:
            tool_name = request.tool.get("name")
            if not tool_name:
                raise HTTPException(status_code=400, detail="Tool name is required")

            tool_params = request.tool.get("params") or {}
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
        if request.use_rag and request.game:
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

        # Get response from language model via router (auto-failover across providers)
        selected_provider = request.lm_provider or "auto"
        try:
            result = await lm_router.generate_with_provider(
                augmented_prompt,
                preferred_provider=request.lm_provider,
            )
            selected_provider = result.get("provider", selected_provider)
            lm_response = result.get("response", "")

            if lm_response.startswith(LM_UNAVAILABLE_PREFIX):
                lm_response = _chat_fallback_for_lm_unavailable(
                    request.text, lm_response, request.game
                )

        except Exception as e:
            lm_response = _chat_fallback_for_lm_unavailable(
                request.text, f"Error: {str(e)}", request.game
            )

        return ChatResponse(
            response=lm_response,
            sources=context_docs,
            context_used=context_used,
            sources_count=len(context_docs),
            lm_provider=selected_provider,
            tool_name=None,
            tool_result=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))