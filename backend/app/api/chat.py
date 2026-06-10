"""Chat API endpoint.

POST /api/chat accepts a user message and conversation history, runs the
Gemini chat agent (gemini-2.5-pro-preview-05-06) with the same tool suite,
and streams the response back via SSE. Conversation history is maintained
client-side — the backend is stateless.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agent.builder import AgentBuilderError, build_chat_agent, initialize_vertex_ai
from app.config import get_settings
from app.db.mcp_mongo import MongoMCPError

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str  # "user" or "agent"
    content: str


class ChatRequest(BaseModel):
    repo_id: str
    message: str
    history: list[ChatMessage] = []


@router.post("")
async def chat(payload: ChatRequest) -> EventSourceResponse:
    """Stream a chat response from the Gemini agent via SSE."""

    async def event_stream():
        try:
            yield _sse("status", {"phase": "thinking", "message": "Thinking..."})

            # Build the full prompt with conversation history for context.
            full_prompt = _build_prompt_with_history(
                message=payload.message,
                history=payload.history,
            )

            # Run agent in a thread to avoid blocking the event loop.
            agent_result = await asyncio.to_thread(
                _run_chat_agent,
                repo_id=payload.repo_id,
                prompt=full_prompt,
            )

            output = agent_result.get("output", "")
            response_text = ""
            if isinstance(output, str):
                response_text = output
            elif isinstance(output, list):
                # Concatenate text from list of blocks returned by ReasoningEngine
                texts = []
                for block in output:
                    if isinstance(block, dict) and "text" in block:
                        texts.append(block["text"])
                    elif isinstance(block, str):
                        texts.append(block)
                response_text = "".join(texts) if texts else str(output)
            elif isinstance(output, dict):
                response_text = output.get("response", output.get("output", str(output)))
            else:
                response_text = str(output) if output else "I couldn't generate a response."

            # Sources are populated from tools_instance.last_searched_paths
            # by _run_chat_agent — these are the actual files that
            # search_codebase() retrieved during this request.
            sources = agent_result.get("sources", [])

            yield _sse(
                "message",
                {
                    "role": "agent",
                    "content": response_text,
                    "sources": sources,
                    "message": response_text,
                },
            )
        except AgentBuilderError as exc:
            logger.exception("Agent builder error during chat")
            yield _sse(
                "error",
                {
                    "message": f"Chat agent error: {exc}",
                    "code": "agent_builder_error",
                },
            )
        except MongoMCPError as exc:
            logger.exception("MongoDB MCP error during chat")
            yield _sse(
                "error",
                {
                    "message": f"Database error: {exc}",
                    "code": "mcp_error",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error during chat")
            yield _sse(
                "error",
                {
                    "message": f"Chat failed: {exc}",
                    "code": "chat_failed",
                },
            )
        finally:
            yield _sse("done", {"ok": True, "message": "SSE stream closed."})

    return EventSourceResponse(event_stream())


# ── Private helpers ──────────────────────────────────────────────────


def _run_chat_agent(*, repo_id: str, prompt: str) -> dict[str, Any]:
    """Run the chat agent synchronously (called via asyncio.to_thread).

    Returns a dict with "output" (the agent response) and "sources"
    (file paths accumulated by search_codebase during tool execution).
    """
    agent, tools_instance = build_chat_agent(repo_id=repo_id)

    response = agent.query(input=prompt)

    # Read the per-instance accumulator — these are the actual file paths
    # that search_codebase() returned during this request.
    sources = list(tools_instance.last_searched_paths)

    if isinstance(response, dict):
        response["sources"] = sources
        return response
    return {"output": response, "sources": sources}


def _build_prompt_with_history(
    *,
    message: str,
    history: list[ChatMessage],
) -> str:
    """Build a single prompt string incorporating conversation history.

    The agent is stateless — history is serialised into the prompt so
    the model can maintain conversational context.
    """
    if not history:
        return message

    parts: list[str] = ["Previous conversation for context:"]
    for msg in history[-10:]:  # Keep last 10 messages to stay within context window.
        role_label = "User" if msg.role == "user" else "Assistant"
        parts.append(f"{role_label}: {msg.content}")

    parts.append("")
    parts.append(f"User: {message}")
    parts.append("")
    parts.append("Respond to the latest user message above.")
    return "\n".join(parts)


def _sse(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a uniform SSE payload with type + message on every event."""
    import json
    payload = {
        "type": event,
        "message": data.get("message", ""),
        **data,
    }
    return {"event": event, "data": json.dumps(payload)}
