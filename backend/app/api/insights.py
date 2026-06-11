"""Insight generation API.

POST /api/insights/generate triggers the Gemini agent to analyze the
repository data and produce actionable insights via the write_insight() tool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agent.builder import AgentBuilderError, build_local_agent, initialize_vertex_ai
from app.agent.prompts import INSIGHT_SYSTEM_PROMPT, build_insight_task_prompt
from app.config import get_settings
from app.db.mcp_mongo import MongoMCPError, create_mcp_client

router = APIRouter(prefix="/api/insights", tags=["insights"])
logger = logging.getLogger(__name__)

MAX_INSIGHTS = 30


class GenerateRequest(BaseModel):
    repo_id: str


class InsightListRequest(BaseModel):
    repo_id: str
    severity: str | None = None
    insight_type: str | None = None


@router.post("/generate")
async def generate_insights(payload: GenerateRequest) -> EventSourceResponse:
    """Run the insight-generation agent and stream progress via SSE."""
    mcp_client = create_mcp_client()
    settings = get_settings()

    async def event_stream():
        try:
            yield _sse("status", {"phase": "init", "message": "Initializing insight engine..."})

            # Verify the repo exists and is ready.
            repo_response = await mcp_client.find(
                database=settings.mongodb_db_name,
                collection="repos",
                filter_query={"_id": payload.repo_id},
                limit=1,
            )
            repo_docs = _extract_documents(repo_response)
            if not repo_docs:
                yield _sse(
                    "error",
                    {
                        "message": f"Repository {payload.repo_id} not found.",
                        "code": "repo_not_found",
                    },
                )
                return
            repo_doc = repo_docs[0]
            if repo_doc.get("status") != "ready":
                yield _sse(
                    "error",
                    {
                        "message": f"Repository is not ready (status: {repo_doc.get('status')}).",
                        "code": "repo_not_ready",
                    },
                )
                return

            yield _sse("status", {"phase": "agent", "message": "Running insight analysis agent..."})

            # Run agent in a thread to avoid blocking the event loop.
            task_prompt = build_insight_task_prompt(repo_id=payload.repo_id)
            agent_result = await asyncio.to_thread(
                _run_insight_agent,
                repo_id=payload.repo_id,
                task_prompt=task_prompt,
            )

            yield _sse("status", {"phase": "verify", "message": "Verifying generated insights..."})

            # Count how many insights were written for this repo.
            insight_count_response = await mcp_client.aggregate(
                database=settings.mongodb_db_name,
                collection="insights",
                pipeline=[
                    {"$match": {"repo_id": payload.repo_id, "resolved": False}},
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "critical": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "critical"]}, 1, 0]}
                            },
                            "warning": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "warning"]}, 1, 0]}
                            },
                            "info": {"$sum": {"$cond": [{"$eq": ["$severity", "info"]}, 1, 0]}},
                        }
                    },
                ],
            )
            count_docs = _extract_documents(insight_count_response)
            counts = count_docs[0] if count_docs else {"total": 0}

            # Enforce cap — if agent overshot, mark excess as resolved.
            total_generated = counts.get("total", 0)
            if total_generated > MAX_INSIGHTS:
                await _cap_insights(
                    mcp_client=mcp_client,
                    db_name=settings.mongodb_db_name,
                    repo_id=payload.repo_id,
                    max_count=MAX_INSIGHTS,
                )
                total_generated = MAX_INSIGHTS

            yield _sse(
                "complete",
                {
                    "repo_id": payload.repo_id,
                    "total_insights": total_generated,
                    "critical": counts.get("critical", 0),
                    "warning": counts.get("warning", 0),
                    "info": counts.get("info", 0),
                    "agent_summary": agent_result.get("output", ""),
                    "message": f"Generated {total_generated} insights.",
                },
            )
        except AgentBuilderError as exc:
            logger.exception("Agent builder error during insight generation")
            yield _sse(
                "error",
                {"message": str(exc), "code": "agent_builder_error"},
            )
        except MongoMCPError as exc:
            logger.exception("MongoDB MCP error during insight generation")
            yield _sse(
                "error",
                {"message": str(exc), "code": "mcp_error"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error during insight generation")
            yield _sse(
                "error",
                {"message": f"Insight generation failed: {exc}", "code": "insight_gen_failed"},
            )
        finally:
            yield _sse("done", {"ok": True, "message": "SSE stream closed."})

    return EventSourceResponse(event_stream())


@router.get("")
async def list_insights(
    repo_id: str,
    severity: str | None = None,
    insight_type: str | None = None,
) -> dict[str, Any]:
    """Fetch insights for a repo with optional severity/type filters."""
    mcp_client = create_mcp_client()
    settings = get_settings()

    filter_query: dict[str, Any] = {"repo_id": repo_id, "resolved": False}
    if severity:
        filter_query["severity"] = severity
    if insight_type:
        filter_query["type"] = insight_type

    response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="insights",
        filter_query=filter_query,
        sort=[{"created_at": -1}],
        limit=100,
    )
    documents = _extract_documents(response)
    return {"insights": documents, "total": len(documents)}


# ── Private helpers ──────────────────────────────────────────────────


def _run_insight_agent(*, repo_id: str, task_prompt: str) -> dict[str, Any]:
    """Run the insight agent synchronously (called from asyncio.to_thread)."""
    initialize_vertex_ai()
    from vertexai.preview import reasoning_engines  # noqa: F811 — re-import is intentional

    from app.agent.prompts import INSIGHT_SYSTEM_PROMPT as system_prompt
    from app.agent.tools import build_tool_callables

    tools, _ = build_tool_callables(repo_id=repo_id)

    agent = reasoning_engines.LangchainAgent(
        model=get_settings().vertex_ai_model_ingest,
        tools=tools,
        system_instruction=system_prompt,
    )

    import time
    for attempt in range(3):
        try:
            response = agent.query(input=task_prompt)
            break
        except Exception as exc:
            if "429" in str(exc) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise

    if isinstance(response, dict):
        return response
    return {"output": response}


async def _cap_insights(
    *,
    mcp_client: MongoMCPClient,
    db_name: str,
    repo_id: str,
    max_count: int,
) -> None:
    """If more than max_count unresolved insights exist, resolve the excess (lowest severity first)."""
    severity_order = {"info": 0, "warning": 1, "critical": 2}

    response = await mcp_client.find(
        database=db_name,
        collection="insights",
        filter_query={"repo_id": repo_id, "resolved": False},
        sort=[{"created_at": -1}],
        limit=500,
    )
    all_insights = _extract_documents(response)

    # Sort by severity descending so we keep the highest-severity ones.
    all_insights.sort(
        key=lambda doc: severity_order.get(doc.get("severity", "info"), 0), reverse=True
    )

    keep_ids = {doc.get("_id") for doc in all_insights[:max_count] if doc.get("_id")}
    excess_ids = [
        doc.get("_id") for doc in all_insights if doc.get("_id") and doc["_id"] not in keep_ids
    ]

    if excess_ids:
        await mcp_client.update_many(
            database=db_name,
            collection="insights",
            filter_query={"_id": {"$in": excess_ids}, "repo_id": repo_id},
            update_query={"$set": {"resolved": True}},
        )


def _extract_documents(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a list of documents from various MCP response shapes."""
    for key in ("documents", "results", "items"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    result_value = response.get("result")
    if isinstance(result_value, dict):
        for key in ("documents", "results", "items"):
            value = result_value.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

    if isinstance(response.get("data"), list):
        return [item for item in response["data"] if isinstance(item, dict)]

    return []


def _sse(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a uniform SSE payload with type + message on every event."""
    import json
    payload = {
        "type": event,
        "message": data.get("message", ""),
        **data,
    }
    return {"event": event, "data": json.dumps(payload)}
