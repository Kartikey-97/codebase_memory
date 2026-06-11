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

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from app.agent.builder import AgentBuilderError, build_chat_agent, initialize_vertex_ai
from app.agent.classifier import classify_query
from app.config import get_settings
from app.db.mcp_mongo import MongoMCPError
from app.telemetry import record_telemetry

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str  # "user" or "agent"
    content: str


class ChatRequest(BaseModel):
    repo_id: str
    message: str
    history: list[ChatMessage] = []
    active_document: str | None = None


@router.post("")
async def chat(payload: ChatRequest) -> EventSourceResponse:
    """Stream a chat response from the Gemini agent via SSE."""
    
    # Payload box allows the generator to pass data back out to the BackgroundTask
    telemetry_box = []

    async def event_stream():
        start_time = asyncio.get_event_loop().time()
        classification = {}
        class_latency_ms = 0
        telemetry_status = "success"
        telemetry_tools = {}
        telemetry_nodes = 0
        telemetry_tokens = {}
        
        try:
            yield _sse("status", {"phase": "thinking", "message": "Thinking..."})

            # Pre-flight Scope Enforcement
            c_start = asyncio.get_event_loop().time()
            classification = await classify_query(
                repo_id=payload.repo_id, 
                message=payload.message,
                active_document=payload.active_document,
                history=payload.history
            )
            class_latency_ms = int((asyncio.get_event_loop().time() - c_start) * 1000)
            
            if not classification.get("is_repo_related", True):
                telemetry_status = "rejected_scope"
                reason = classification.get("reason", "")
                msg = "Scope verification is currently unavailable. Please try again later." if "unavailable" in reason else "I can only answer questions about the indexed repository."
                yield _sse(
                    "message",
                    {
                        "role": "agent",
                        "content": msg,
                        "sources": [],
                        "message": msg,
                    },
                )
                return

            # Check if this is an overview query
            is_overview_query = any(kw in payload.message.lower() for kw in ["what does this repo do", "tell me about this repo", "overview", "explain the architecture", "what is this project", "summarize the repo"])
            
            repo_context_str = ""
            if is_overview_query:
                try:
                    from app.db.mcp_mongo import create_mcp_client
                    mcp_client_instance = create_mcp_client()
                    manifest = await mcp_client_instance.find_one(
                        database=settings.mongodb_db_name,
                        collection="repo_manifests",
                        filter_query={"repo_id": payload.repo_id}
                    )
                    if manifest and manifest.get("confidence_score", 0.0) >= 0.2:
                        repo_context_str = f"Repository Context:\nDomain: {manifest.get('domain', '')}\nOverview: {manifest.get('architecture_summary', '')}\nBusiness Concepts: {manifest.get('business_concepts', [])}\n\nNote: If this context does not fully answer the user's question, use your tools to perform a vector search fallback.\n\n"
                except Exception as e:
                    import logging
                    logging.warning(f"Failed to fetch manifest for chat: {e}")

            # Build the full prompt with conversation history for context.
            full_prompt = _build_prompt_with_history(
                message=payload.message,
                history=payload.history,
                repo_context=repo_context_str
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

            sources = agent_result.get("sources", [])
            telemetry_data = agent_result.get("telemetry", {})
            telemetry_tools = telemetry_data.get("tools_used", {})
            telemetry_nodes = telemetry_data.get("graph_nodes_traversed", 0)
            
            # Exact Token Accounting from Vertex/Langchain
            prompt_tokens = 0
            completion_tokens = 0
            
            # ReasoningEngine often returns usage_metadata internally
            if "usage_metadata" in agent_result and agent_result["usage_metadata"] is not None:
                usage = agent_result["usage_metadata"]
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_token_count", len(full_prompt) // 4)
                    completion_tokens = usage.get("candidates_token_count", len(response_text) // 4)
                else:
                    prompt_tokens = getattr(usage, "prompt_token_count", len(full_prompt) // 4)
                    completion_tokens = getattr(usage, "candidates_token_count", len(response_text) // 4)
            else:
                # Fallback to naive if metadata is completely stripped
                prompt_tokens = len(full_prompt) // 4
                completion_tokens = len(response_text) // 4
                
            telemetry_tokens = {
                "prompt_tokens": prompt_tokens, 
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }

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
            telemetry_status = "error"
        finally:
            end_time = asyncio.get_event_loop().time()
            total_latency_ms = int((end_time - start_time) * 1000)
            
            telemetry_payload = {
                "repo_id": payload.repo_id,
                "session_id": "anon", # Can be extracted from headers if auth is added
                "query": {
                    "text_scrubbed": payload.message,
                    "has_ide_context": bool(payload.active_document)
                },
                "classification": {
                    "is_repo_related": classification.get("is_repo_related", False),
                    "confidence": classification.get("confidence", 0.0),
                    "reason": classification.get("reason", ""),
                    "latency_ms": class_latency_ms,
                    "mode": "deterministic" if classification.get("confidence") == 1.0 else "llm"
                },
                "execution": {
                    "total_latency_ms": total_latency_ms,
                    "tools_used": list(telemetry_tools.keys()) if telemetry_tools else [],
                    "tool_stats": telemetry_tools,
                    "graph_nodes_traversed": telemetry_nodes,
                    "token_usage": telemetry_tokens,
                    "status": telemetry_status
                }
            }
            telemetry_box.append(telemetry_payload)
            yield _sse("done", {"ok": True, "message": "SSE stream closed."})

    async def dispatch_telemetry():
        if telemetry_box:
            await record_telemetry(telemetry_box[0])

    return EventSourceResponse(event_stream(), background=BackgroundTask(dispatch_telemetry))

# ── Private helpers ──────────────────────────────────────────────────

def _run_chat_agent(*, repo_id: str, prompt: str) -> dict[str, Any]:
    """Run the chat agent synchronously (called via asyncio.to_thread).

    Returns a dict with "output" (the agent response), "sources"
    (file paths accumulated by search_codebase), and "telemetry".
    """
    agent, tools_instance = build_chat_agent(repo_id=repo_id)

    response = agent.query(input=prompt)

    sources = list(tools_instance.last_searched_paths)

    if isinstance(response, dict):
        response["sources"] = sources
        response["telemetry"] = tools_instance.telemetry
        return response
        
    # Attempt to extract usage from Langchain's RunTree or metadata if attached
    usage = getattr(response, "usage_metadata", None)
    return {"output": response, "sources": sources, "telemetry": tools_instance.telemetry, "usage_metadata": usage}


def _build_prompt_with_history(
    *,
    message: str,
    history: list[ChatMessage],
    repo_context: str = ""
) -> str:
    """Build a single prompt string incorporating conversation history.

    The agent is stateless — history is serialised into the prompt so
    the model can maintain conversational context.
    """
    parts = []
    if repo_context:
        parts.append(repo_context)
        
    if not history:
        parts.append(message)
        return "\n".join(parts)

    parts.append("Previous conversation for context:")
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
