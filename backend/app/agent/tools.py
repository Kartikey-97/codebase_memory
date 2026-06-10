from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.config import get_settings
from app.db.mcp_mongo import create_mcp_client


class CodebaseAgentTools:
    """Stateless tool facade for Agent Builder.

    A new instance should be created per request so no conversational state is retained.
    """

    def __init__(self, repo_id: str) -> None:
        self.repo_id = repo_id
        self._db_name = get_settings().mongodb_db_name
        self._mcp_client = create_mcp_client()
        self.last_searched_paths: list[str] = []

    def search_codebase(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Vector search code chunks for this repo and return top matches."""
        if not query.strip():
            return []

        response = _run_async(
            self._mcp_client.vector_search(
                database=self._db_name,
                collection="chunks",
                index_name="chunk_embeddings",
                query_text=query,
                limit=limit,
                filter_query={"repo_id": self.repo_id, "_deleted": {"$ne": True}},
            )
        )

        documents = _extract_documents(response)
        results: list[dict[str, Any]] = []
        for doc in documents:
            path = doc.get("path")
            results.append(
                {
                    "path": path,
                    "content": doc.get("content"),
                    "start_line": doc.get("start_line"),
                    "end_line": doc.get("end_line"),
                    "score": doc.get("score") or doc.get("similarity") or doc.get("_score"),
                }
            )
            if path and path not in self.last_searched_paths:
                self.last_searched_paths.append(path)
        return results

    def get_file_relationships(self, file_path: str) -> list[dict[str, Any]]:
        """Get import/call/extends edges to or from a file."""
        if not file_path.strip():
            return []

        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="relationships",
                filter_query={
                    "repo_id": self.repo_id,
                    "_deleted": {"$ne": True},
                    "$or": [{"from_file": file_path}, {"to_file": file_path}],
                },
                limit=200,
            )
        )
        documents = _extract_documents(response)
        return [
            {
                "from_file": doc.get("from_file"),
                "to_file": doc.get("to_file"),
                "type": doc.get("type"),
                "weight": doc.get("weight", 1),
            }
            for doc in documents
        ]

    def get_file_metadata(self, file_path: str) -> dict[str, Any]:
        """Fetch file-level metadata for a given path."""
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="files",
                filter_query={"repo_id": self.repo_id, "path": file_path, "_deleted": {"$ne": True}},
                limit=1,
            )
        )
        documents = _extract_documents(response)
        return documents[0] if documents else {}

    def list_high_risk_files(
        self, min_dependents: int = 5, max_doc_coverage: float = 0.4
    ) -> list[dict[str, Any]]:
        """List files with many dependents and weak documentation."""
        relationship_response = _run_async(
            self._mcp_client.aggregate(
                database=self._db_name,
                collection="relationships",
                pipeline=[
                    {"$match": {"repo_id": self.repo_id, "_deleted": {"$ne": True}}},
                    {"$group": {"_id": "$to_file", "dependent_weight": {"$sum": "$weight"}}},
                    {"$match": {"dependent_weight": {"$gte": min_dependents}}},
                    {"$sort": {"dependent_weight": -1}},
                    {"$limit": 100},
                ],
            )
        )
        dependent_docs = _extract_documents(relationship_response)

        metadata_response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="files",
                filter_query={"repo_id": self.repo_id, "doc_coverage": {"$lte": max_doc_coverage}, "_deleted": {"$ne": True}},
                limit=500,
            )
        )
        metadata_docs = _extract_documents(metadata_response)
        metadata_by_path = {doc.get("path"): doc for doc in metadata_docs if doc.get("path")}

        risky: list[dict[str, Any]] = []
        for dep in dependent_docs:
            path = dep.get("_id")
            meta = metadata_by_path.get(path)
            if not path or not meta:
                continue
            risky.append(
                {
                    "path": path,
                    "dependent_weight": dep.get("dependent_weight", 0),
                    "doc_coverage": meta.get("doc_coverage", 0.0),
                    "owner": meta.get("owner", ""),
                    "language": meta.get("language", ""),
                }
            )
        return risky

    def get_high_complexity_files(self, min_complexity: int = 10) -> list[dict[str, Any]]:
        """List files whose max cyclomatic complexity meets or exceeds the threshold."""
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="files",
                filter_query={
                    "repo_id": self.repo_id,
                    "max_complexity": {"$gte": min_complexity},
                    "_deleted": {"$ne": True},
                },
                sort=[{"max_complexity": -1}],
                limit=100,
            )
        )
        documents = _extract_documents(response)
        return [
            {
                "path": doc.get("path"),
                "max_complexity": doc.get("max_complexity", 0),
                "doc_coverage": doc.get("doc_coverage", 0.0),
                "owner": doc.get("owner", ""),
                "language": doc.get("language", ""),
            }
            for doc in documents
        ]

    def write_insight(self, insight: dict[str, Any]) -> dict[str, Any]:
        """Persist an insight document for this repo."""
        payload = {
            "_id": str(ObjectId()),
            "repo_id": self.repo_id,
            "type": insight.get("type", "dependency_risk"),
            "severity": insight.get("severity", "info"),
            "title": insight.get("title", "Generated insight"),
            "description": insight.get("description", ""),
            "affected_files": insight.get("affected_files", []),
            "created_at": datetime.now(UTC).isoformat(),
            "resolved": False,
            "snapshot_id": insight.get("snapshot_id", str(ObjectId())),
        }

        response = _run_async(
            self._mcp_client.insert_many(
                database=self._db_name,
                collection="insights",
                documents=[payload],
            )
        )
        return {"inserted": True, "result": response, "insight_id": payload["_id"]}


def build_tool_callables(
    repo_id: str,
) -> tuple[list[Any], CodebaseAgentTools]:
    """Return (callable list, tools instance).

    The tools instance is returned so callers can read per-request
    accumulators (e.g. last_searched_paths) after the agent finishes.
    """
    tools = CodebaseAgentTools(repo_id=repo_id)
    callables = [
        tools.search_codebase,
        tools.get_file_relationships,
        tools.get_file_metadata,
        tools.list_high_risk_files,
        tools.get_high_complexity_files,
        tools.write_insight,
    ]
    return callables, tools


def _extract_documents(response: dict[str, Any]) -> list[dict[str, Any]]:
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


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync tool callables in both sync and async contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")
