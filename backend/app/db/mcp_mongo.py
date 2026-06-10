"""MongoDB data access layer.

All operations go through the MCP HTTP bridge (MONGODB_MCP_URL).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MongoMCPError(Exception):
    def __init__(self, detail: str, *, code: str = "mcp_error") -> None:
        self.detail = detail
        self.code = code
        super().__init__(detail)


def create_mcp_client() -> "MongoMCPClient":
    """Factory: build a MongoMCPClient from the app Settings singleton."""
    from app.config import get_settings

    settings = get_settings()
    return MongoMCPClient(
        base_url=settings.mongodb_mcp_url,
        app_env=settings.app_env,
    )


class MongoMCPClient:
    """
    MongoDB client that routes all operations through the MCP server or Motor.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_seconds: float = 60.0,
        app_env: str = "development",
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.timeout_seconds = timeout_seconds
        self.app_env = app_env

    @property
    def _has_mcp(self) -> bool:
        return bool(self.base_url)

    # ── insert_many ──────────────────────────────────────────────

    async def insert_many(
        self,
        *,
        database: str,
        collection: str,
        documents: list[dict[str, Any]],
        enable_embeddings: bool = False,
    ) -> dict[str, Any]:
        if not documents:
            return {"inserted_count": 0}

        if self.app_env == "production":
            from motor.motor_asyncio import AsyncIOMotorClient
            from app.config import get_settings
            settings = get_settings()

            if enable_embeddings and collection == "chunks":
                from app.agent.builder import initialize_vertex_ai
                initialize_vertex_ai()
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-004")

                texts = []
                for doc in documents:
                    text = doc.get("content", doc.get("text", ""))
                    # Truncate text to avoid Vertex AI token limits (max 2048 tokens per instance)
                    # 1 token ~= 4 characters, so 8000 chars is a safe upper bound.
                    if len(text) > 8000:
                        text = text[:8000]
                    # Avoid completely empty strings
                    if not text.strip():
                        text = " "
                    texts.append(text)
                
                # Vertex AI allows max 20,000 tokens across all instances in a single request.
                # With max 2000 tokens per instance, a batch size of 10 safely avoids the 400 error.
                batch_size = 10
                all_embeddings = []
                
                import asyncio
                for i in range(0, len(texts), batch_size):
                    batch_texts = texts[i : i + batch_size]
                    
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            if hasattr(model, "get_embeddings_async"):
                                embeddings = await model.get_embeddings_async(batch_texts)
                            else:
                                embeddings = await asyncio.to_thread(model.get_embeddings, batch_texts)
                            all_embeddings.extend([e.values for e in embeddings])
                            break
                        except Exception as e:
                            if attempt < max_retries - 1:
                                logger.warning("Vertex AI error. Retrying... %s", e)
                                await asyncio.sleep(2 ** attempt + 1.0)
                            else:
                                logger.error("Vertex AI embedding failed: %s", e)
                                raise MongoMCPError(f"Vertex AI error: {e}", code="vertex_error")

                for idx, doc in enumerate(documents):
                    doc["embedding"] = all_embeddings[idx]

            client = AsyncIOMotorClient(settings.mongodb_uri)
            try:
                db = client[database]
                result = await db[collection].insert_many(documents)
                return {"inserted_count": len(result.inserted_ids)}
            finally:
                client.close()

        if not self._has_mcp:
            raise MongoMCPError(
                "MCP bridge URL not configured (MONGODB_MCP_URL).",
                code="mcp_not_configured",
            )

        payload: dict[str, Any] = {
            "tool": "insert-many",
            "arguments": {
                "database": database,
                "collection": collection,
                "documents": documents,
            },
        }
        if enable_embeddings:
            payload["arguments"]["enable_embeddings"] = True

        return await self._call_tool(payload)

    # ── update_many ──────────────────────────────────────────────

    async def update_many(
        self,
        *,
        database: str,
        collection: str,
        filter_query: dict[str, Any],
        update_query: dict[str, Any],
    ) -> dict[str, Any]:
        if self.app_env == "production":
            from motor.motor_asyncio import AsyncIOMotorClient
            from app.config import get_settings
            client = AsyncIOMotorClient(get_settings().mongodb_uri)
            try:
                db = client[database]
                result = await db[collection].update_many(filter_query, update_query)
                return {
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                }
            finally:
                client.close()

        payload: dict[str, Any] = {
            "tool": "update-many",
            "arguments": {
                "database": database,
                "collection": collection,
                "filter": filter_query,
                "update": update_query,
            },
        }
        return await self._call_tool(payload)

    # ── find ─────────────────────────────────────────────────────

    async def find(
        self,
        *,
        database: str,
        collection: str,
        filter_query: dict[str, Any],
        limit: int | None = None,
        sort: list[dict[str, Any]] | None = None,
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if self.app_env == "production" or not self._has_mcp:
            return await self._motor_find(database, collection, filter_query, limit, sort, projection)

        arguments: dict[str, Any] = {
            "database": database,
            "collection": collection,
            "filter": filter_query,
        }
        if limit is not None:
            arguments["limit"] = limit
        if sort is not None:
            arguments["sort"] = sort
        if projection is not None:
            arguments["projection"] = projection

        payload: dict[str, Any] = {"tool": "find", "arguments": arguments}
        try:
            return await self._call_tool(payload)
        except MongoMCPError as e:
            if e.code in ("mcp_network_error", "mcp_not_configured"):
                return await self._motor_find(database, collection, filter_query, limit, sort, projection)
            raise

    # ── aggregate ────────────────────────────────────────────────

    async def aggregate(
        self,
        *,
        database: str,
        collection: str,
        pipeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.app_env == "production" or not self._has_mcp:
            return await self._motor_aggregate(database, collection, pipeline)

        payload: dict[str, Any] = {
            "tool": "aggregate",
            "arguments": {
                "database": database,
                "collection": collection,
                "pipeline": pipeline,
            },
        }
        try:
            return await self._call_tool(payload)
        except MongoMCPError as e:
            if e.code in ("mcp_network_error", "mcp_not_configured"):
                return await self._motor_aggregate(database, collection, pipeline)
            raise

    # ── vector_search ────────────────────────────────────────────

    async def vector_search(
        self,
        *,
        database: str,
        collection: str,
        index_name: str,
        query_text: str,
        limit: int = 8,
        filter_query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.app_env == "production" or not self._has_mcp:
            return await self._motor_vector_search(database, collection, index_name, query_text, limit, filter_query)

        arguments: dict[str, Any] = {
            "database": database,
            "collection": collection,
            "index": index_name,
            "query": query_text,
            "limit": limit,
        }
        if filter_query is not None:
            arguments["filter"] = filter_query

        payload: dict[str, Any] = {"tool": "vector-search", "arguments": arguments}
        try:
            return await self._call_tool(payload)
        except MongoMCPError as e:
            if e.code in ("mcp_network_error", "mcp_not_configured"):
                return await self._motor_vector_search(database, collection, index_name, query_text, limit, filter_query)
            raise

    # ── Motor Fallback Helpers ───────────────────────────────────

    async def _motor_find(self, database: str, collection: str, filter_query: dict[str, Any], limit: int | None, sort: list[dict[str, Any]] | None, projection: dict[str, int] | None) -> dict[str, Any]:
        from motor.motor_asyncio import AsyncIOMotorClient
        from app.config import get_settings
        client = AsyncIOMotorClient(get_settings().mongodb_uri)
        try:
            db = client[database]
            cursor = db[collection].find(filter_query, projection)
            if sort:
                motor_sort = []
                for s in sort:
                    for k, v in s.items():
                        motor_sort.append((k, v))
                if motor_sort:
                    cursor = cursor.sort(motor_sort)
            if limit:
                cursor = cursor.limit(limit)
            docs = await cursor.to_list(length=limit or 1000)
            return {"documents": [_sanitize_bson(doc) for doc in docs]}
        finally:
            client.close()

    async def _motor_aggregate(self, database: str, collection: str, pipeline: list[dict[str, Any]]) -> dict[str, Any]:
        from motor.motor_asyncio import AsyncIOMotorClient
        from app.config import get_settings
        client = AsyncIOMotorClient(get_settings().mongodb_uri)
        try:
            db = client[database]
            docs = await db[collection].aggregate(pipeline).to_list(length=1000)
            return {"documents": [_sanitize_bson(doc) for doc in docs]}
        finally:
            client.close()

    async def _motor_vector_search(self, database: str, collection: str, index_name: str, query_text: str, limit: int, filter_query: dict[str, Any] | None) -> dict[str, Any]:
        from motor.motor_asyncio import AsyncIOMotorClient
        from app.config import get_settings
        settings = get_settings()
        
        from app.agent.builder import initialize_vertex_ai
        initialize_vertex_ai()
        from vertexai.language_models import TextEmbeddingModel
        import asyncio
        model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        
        try:
            if hasattr(model, "get_embeddings_async"):
                embeddings = await model.get_embeddings_async([query_text])
            else:
                embeddings = await asyncio.to_thread(model.get_embeddings, [query_text])
            embedding = embeddings[0].values
        except Exception as e:
            raise MongoMCPError(f"Vertex AI vector search embedding failed: {e}", code="vertex_error")

        client = AsyncIOMotorClient(settings.mongodb_uri)
        try:
            db = client[database]
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": index_name,
                        "path": "embedding",
                        "queryVector": embedding,
                        "numCandidates": limit * 10,
                        "limit": limit,
                    }
                }
            ]
            if filter_query:
                pipeline[0]["$vectorSearch"]["filter"] = filter_query
                
            docs = await db[collection].aggregate(pipeline).to_list(length=limit)
            return {"documents": [_sanitize_bson(doc) for doc in docs]}
        finally:
            client.close()

    # ── Internal network call ────────────────────────────────────

    async def _call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._has_mcp:
            raise MongoMCPError(
                "MCP bridge is not configured. Missing MONGODB_MCP_URL.",
                code="mcp_not_configured",
            )

        # The MCP inspector expects: {"tool": "<name>", "arguments": {...}}
        endpoint = f"{self.base_url}/api/tools/call"
        
        headers = {}
        from app.config import get_settings
        settings = get_settings()
        if settings.mcp_bridge_token:
            headers["Authorization"] = f"Bearer {settings.mcp_bridge_token}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise MongoMCPError(
                f"Failed to connect to MCP server at {endpoint}: {exc}",
                code="mcp_network_error",
            ) from exc

        if response.status_code != 200:
            error_detail = response.text
            try:
                error_data = response.json()
                if "detail" in error_data:
                    error_detail = error_data["detail"]
            except Exception:  # noqa: BLE001
                pass

            raise MongoMCPError(
                f"MCP server returned {response.status_code}: {error_detail}",
                code=f"mcp_http_{response.status_code}",
            )

        try:
            return response.json()
        except Exception as exc:
            raise MongoMCPError(
                f"Failed to parse MCP server response: {exc}",
                code="mcp_parse_error",
            ) from exc

def _sanitize_bson(obj: Any) -> Any:
    from bson import ObjectId
    from datetime import datetime
    if isinstance(obj, dict):
        return {k: _sanitize_bson(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_bson(item) for item in obj]
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj

