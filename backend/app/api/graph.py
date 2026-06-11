from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.db.mcp_mongo import create_mcp_client

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/relationships")
async def get_relationships(repo_id: str) -> dict[str, Any]:
    """Return relationship graph edges for the D3 map."""
    settings = get_settings()
    mcp_client = create_mcp_client()

    response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="relationships",
        filter_query={"repo_id": repo_id, "_deleted": {"$ne": True}},
        limit=10_000,
    )
    
    docs = []
    for key in ("documents", "results", "items"):
        val = response.get(key)
        if isinstance(val, list):
            docs = val
            break
            
    return {"relationships": docs}


@router.get("/files")
async def get_files(repo_id: str) -> dict[str, Any]:
    """Return file nodes for the D3 map."""
    settings = get_settings()
    mcp_client = create_mcp_client()

    response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="files",
        filter_query={"repo_id": repo_id, "_deleted": {"$ne": True}},
        projection={"path": 1, "owner": 1, "doc_coverage": 1},
        limit=10_000,
    )
    
    docs = []
    for key in ("documents", "results", "items"):
        val = response.get(key)
        if isinstance(val, list):
            docs = val
            break
            
    return {"files": docs}


class SummarizeRequest(BaseModel):
    repo_id: str
    path: str


@router.post("/graph/summarize")
async def summarize_file(payload: SummarizeRequest) -> dict[str, Any]:
    """Generate a quick 1-2 sentence AI summary of a file based on its chunks."""
    settings = get_settings()
    mcp_client = create_mcp_client()

    response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="chunks",
        filter_query={"repo_id": payload.repo_id, "path": payload.path, "_deleted": {"$ne": True}},
        limit=50,
    )
    
    docs = []
    for key in ("documents", "results", "items"):
        val = response.get(key)
        if isinstance(val, list):
            docs = val
            break
            
    if not docs:
        return {"summary": "File content not found."}

    content = "\n\n".join(doc.get("content", "") for doc in docs)
    if len(content) > 40000:
        content = content[:40000]

    from app.agent.builder import initialize_vertex_ai
    initialize_vertex_ai()
    from vertexai.generative_models import GenerativeModel
    
    model = GenerativeModel("gemini-1.5-flash")
    prompt = f"Summarize the purpose of this file in 1 or 2 concise sentences. Do not use markdown or formatting. File path: {payload.path}\n\nCode:\n{content}"
    
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(model.generate_content, prompt)
            return {"summary": result.text.strip()}
        except Exception as exc:
            if "429" in str(exc) and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return {"summary": f"Failed to generate summary: {exc}"}

@router.post("/graph/summarize_cluster")
async def summarize_cluster(payload: SummarizeRequest) -> dict[str, Any]:
    """Generate an AI summary of a file and its immediate dependent/dependency neighbors."""
    settings = get_settings()
    mcp_client = create_mcp_client()

    # Find neighbors
    rel_response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="relationships",
        filter_query={"repo_id": payload.repo_id, "$or": [{"from_file": payload.path}, {"to_file": payload.path}], "_deleted": {"$ne": True}},
        limit=200,
    )
    
    rel_docs = []
    for key in ("documents", "results", "items"):
        val = rel_response.get(key)
        if isinstance(val, list):
            rel_docs = val
            break
            
    neighbor_paths = {payload.path}
    for r in rel_docs:
        neighbor_paths.add(r.get("from_file"))
        neighbor_paths.add(r.get("to_file"))
        
    neighbor_paths = {p for p in neighbor_paths if p} # remove Nones
    
    # Fetch chunks
    chunks_response = await mcp_client.find(
        database=settings.mongodb_db_name,
        collection="chunks",
        filter_query={"repo_id": payload.repo_id, "path": {"$in": list(neighbor_paths)}, "_deleted": {"$ne": True}},
        limit=200,
    )
    
    chunk_docs = []
    for key in ("documents", "results", "items"):
        val = chunks_response.get(key)
        if isinstance(val, list):
            chunk_docs = val
            break
            
    if not chunk_docs:
        return {"summary": "Cluster content not found."}

    content = "\n\n".join(f"--- File: {doc.get('path')} ---\n{doc.get('content', '')}" for doc in chunk_docs)
    if len(content) > 40000:
        content = content[:40000] + "\n...[truncated]"

    from app.agent.builder import initialize_vertex_ai
    initialize_vertex_ai()
    from vertexai.generative_models import GenerativeModel
    
    model = GenerativeModel("gemini-1.5-flash")
    prompt = f"Summarize the collective purpose and interaction of this cluster of connected files in 2 or 3 concise sentences. The central file is {payload.path}. Do not use markdown or formatting.\n\nCode context:\n{content}"
    
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(model.generate_content, prompt)
            return {"summary": result.text.strip()}
        except Exception as exc:
            if "429" in str(exc) and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return {"summary": f"Failed to generate cluster summary: {exc}"}

