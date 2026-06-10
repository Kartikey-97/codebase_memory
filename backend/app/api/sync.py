"""Sync / re-index API.

POST /api/sync re-clones a repo, diffs against the previous state,
re-indexes only changed files, updates the relationship graph, re-runs
insight generation for affected files + their dependents, and resolves
old insights whose triggering conditions no longer hold.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db.mcp_mongo import MongoMCPError, create_mcp_client
from app.ingestion.chunker import create_chunks_for_file
from app.ingestion.clone import cleanup_clone_path, clone_repo_to_temp
from app.ingestion.graph import build_relationship_documents
from app.ingestion.parser import parse_file

router = APIRouter(prefix="/api/sync", tags=["sync"])
logger = logging.getLogger(__name__)


class SyncRequest(BaseModel):
    repo_id: str


@router.post("")
async def sync_repo(payload: SyncRequest) -> EventSourceResponse:
    """Re-index a previously ingested repo and reconcile insights."""
    mcp = create_mcp_client()
    settings = get_settings()

    async def event_stream():
        clone_path: str | None = None
        try:
            yield _sse("status", {"phase": "start", "message": "Starting sync..."})

            # ── 1. Fetch existing repo ──────────────────────────────
            repo_response = await mcp.find(
                database=settings.mongodb_db_name,
                collection="repos",
                filter_query={"_id": payload.repo_id},
                limit=1,
            )
            repo_docs = _extract_docs(repo_response)
            if not repo_docs:
                yield _sse("error", {"message": "Repository not found.", "code": "repo_not_found"})
                return
            repo_doc = repo_docs[0]
            repo_url = repo_doc["url"]

            # ── 2. Re-clone ─────────────────────────────────────────
            yield _sse("status", {"phase": "clone", "message": "Cloning latest version..."})
            clone_result = await asyncio.to_thread(
                clone_repo_to_temp, repo_url, settings.ingestion_tmp_dir
            )
            if not clone_result.get("ok"):
                raise _SyncError(
                    clone_result["error"]["detail"], code=clone_result["error"]["code"]
                )
            clone_path = clone_result["clone_path"]
            clone_root = Path(clone_path)
            tracked_files = clone_result["repo"]["tracked_files"]

            # ── 3. Diff against previous file hashes ────────────────
            yield _sse("status", {"phase": "diff", "message": "Computing file diffs..."})

            old_files_response = await mcp.find(
                database=settings.mongodb_db_name,
                collection="files",
                filter_query={"repo_id": payload.repo_id},
                limit=50_000,
            )
            old_files = _extract_docs(old_files_response)
            old_hash_by_path: dict[str, str] = {}
            old_file_id_by_path: dict[str, str] = {}
            for f in old_files:
                path = f.get("path", "")
                old_file_id_by_path[path] = f.get("_id", "")
                # Use content_hash if available, fallback to size + mtime for backwards compatibility
                if "content_hash" in f:
                    old_hash_by_path[path] = f["content_hash"]
                else:
                    old_hash_by_path[path] = f"{f.get('size_bytes', 0)}:{f.get('last_modified', '')}"

            new_hash_by_path: dict[str, str] = {}
            import hashlib
            for rel_path in tracked_files:
                abs_path = clone_root / rel_path
                if abs_path.is_file():
                    try:
                        content = abs_path.read_bytes()
                        content_hash = hashlib.sha256(content).hexdigest()
                        new_hash_by_path[rel_path] = content_hash
                    except Exception:
                        stat = abs_path.stat()
                        new_hash_by_path[rel_path] = (
                            f"{stat.st_size}:{datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()}"
                        )

            changed_files = [
                p
                for p in new_hash_by_path
                if p not in old_hash_by_path or old_hash_by_path[p] != new_hash_by_path[p]
            ]
            deleted_files = [p for p in old_hash_by_path if p not in new_hash_by_path]

            yield _sse(
                "status",
                {
                    "phase": "diff",
                    "message": (
                        f"Found {len(changed_files)} changed, "
                        f"{len(deleted_files)} deleted files."
                    ),
                },
            )

            if not changed_files and not deleted_files:
                yield _sse(
                    "complete", {"message": "No changes detected.", "repo_id": payload.repo_id}
                )
                return

            # ── 4. Delete old data for changed/deleted files ────────
            paths_to_reindex = changed_files
            paths_to_remove = list(set(changed_files + deleted_files))

            for path in paths_to_remove:
                file_id = old_file_id_by_path.get(path)
                if file_id:
                    await mcp.update_many(
                        database=settings.mongodb_db_name,
                        collection="chunks",
                        filter_query={"file_id": file_id, "repo_id": payload.repo_id},
                        update_query={"$set": {"_deleted": True}},
                    )
            # Remove old file docs.
            if paths_to_remove:
                await mcp.update_many(
                    database=settings.mongodb_db_name,
                    collection="files",
                    filter_query={"repo_id": payload.repo_id, "path": {"$in": paths_to_remove}},
                    update_query={"$set": {"_deleted": True}},
                )

            # ── 5. Re-parse and re-chunk changed files ──────────────
            yield _sse(
                "status",
                {"phase": "parse", "message": f"Parsing {len(paths_to_reindex)} changed files..."},
            )

            new_file_docs: list[dict[str, Any]] = []
            new_chunk_docs: list[dict[str, Any]] = []
            parsed_by_path: dict[str, Any] = {}
            source_by_path: dict[str, str] = {}

            for rel_path in paths_to_reindex:
                abs_path = clone_root / rel_path
                if not abs_path.is_file():
                    continue

                source_text = await asyncio.to_thread(abs_path.read_text, "utf-8", "ignore")
                parsed = await asyncio.to_thread(parse_file, abs_path, clone_root)
                chunks = await asyncio.to_thread(create_chunks_for_file, parsed, source_text)

                parsed_by_path[rel_path] = parsed
                source_by_path[rel_path] = source_text

                fn_count = len(parsed.functions)
                doc_count = sum(1 for f in parsed.functions if f.has_docstring)
                doc_coverage = (doc_count / fn_count) if fn_count else 0.0
                max_complexity = (
                    max((f.cyclomatic_complexity for f in parsed.functions), default=0)
                    if fn_count
                    else 0
                )

                import hashlib
                content_hash = hashlib.sha256(source_text.encode("utf-8", errors="replace")).hexdigest()
                file_id = str(ObjectId())
                stat = abs_path.stat()
                new_file_docs.append(
                    {
                        "_id": file_id,
                        "repo_id": payload.repo_id,
                        "path": rel_path,
                        "language": parsed.language,
                        "size_bytes": int(stat.st_size),
                        "last_modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "content_hash": content_hash,
                        "owner": "",  # Ownership not re-extracted during sync for speed.
                        "doc_coverage": doc_coverage,
                        "max_complexity": max_complexity,
                        "indexed_at": datetime.now(UTC).isoformat(),
                    }
                )
                for chunk in chunks:
                    new_chunk_docs.append(
                        {
                            "file_id": file_id,
                            "repo_id": payload.repo_id,
                            "content": chunk.content,
                            "path": chunk.path,
                            "chunk_index": chunk.chunk_index,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                        }
                    )

            # ── 6. Insert new file + chunk docs ─────────────────────
            yield _sse("status", {"phase": "insert", "message": "Writing updated files..."})
            if new_file_docs:
                await mcp.insert_many(
                    database=settings.mongodb_db_name,
                    collection="files",
                    documents=new_file_docs,
                )
            if new_chunk_docs:

                await mcp.insert_many(
                    database=settings.mongodb_db_name,
                    collection="chunks",
                    documents=new_chunk_docs,
                    enable_embeddings=True,
                )

            # ── 7. Rebuild relationship graph for affected files ────
            yield _sse("status", {"phase": "graph", "message": "Updating relationship graph..."})

            # Remove old relationships involving changed/deleted files.
            for path in paths_to_remove:
                await mcp.update_many(
                    database=settings.mongodb_db_name,
                    collection="relationships",
                    filter_query={
                        "repo_id": payload.repo_id,
                        "$or": [{"from_file": path}, {"to_file": path}],
                    },
                    update_query={"$set": {"_deleted": True}},
                )

            # Rebuild relationships from newly parsed files.
            if parsed_by_path:
                rel_docs = await asyncio.to_thread(
                    build_relationship_documents,
                    repo_id=payload.repo_id,
                    parsed_files=list(parsed_by_path.values()),
                    source_by_path=source_by_path,
                )
                if rel_docs:
                    await mcp.insert_many(
                        database=settings.mongodb_db_name,
                        collection="relationships",
                        documents=rel_docs,
                    )

            # ── 8. Resolve stale insights by re-checking conditions ─
            yield _sse("status", {"phase": "resolve", "message": "Reconciling insights..."})

            resolved_count = await _resolve_stale_insights(
                mcp=mcp,
                db_name=settings.mongodb_db_name,
                repo_id=payload.repo_id,
            )

            # ── 9. Write snapshot ───────────────────────────────────
            snapshot_doc = {
                "_id": str(ObjectId()),
                "repo_id": payload.repo_id,
                "commit_hash": "",
                "timestamp": datetime.now(UTC).isoformat(),
                "files_changed": changed_files + deleted_files,
                "insights_generated": 0,
            }
            await mcp.insert_many(
                database=settings.mongodb_db_name,
                collection="snapshots",
                documents=[snapshot_doc],
            )

            # ── 10. Update repo record ──────────────────────────────
            await mcp.update_many(
                database=settings.mongodb_db_name,
                collection="repos",
                filter_query={"_id": payload.repo_id},
                update_query={
                    "$set": {
                        "last_synced": datetime.now(UTC).isoformat(),
                        "status": "ready",
                    }
                },
            )

            yield _sse(
                "complete",
                {
                    "repo_id": payload.repo_id,
                    "files_changed": len(changed_files),
                    "files_deleted": len(deleted_files),
                    "insights_resolved": resolved_count,
                    "message": (
                        f"Sync complete: {len(changed_files)} changed, "
                        f"{len(deleted_files)} deleted, "
                        f"{resolved_count} insights resolved."
                    ),
                },
            )
        except (_SyncError, MongoMCPError) as exc:
            logger.exception("Sync error")
            yield _sse("error", {"message": str(exc), "code": getattr(exc, "code", "sync_error")})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected sync error")
            yield _sse("error", {"message": f"Sync failed: {exc}", "code": "sync_failed"})
        finally:
            if clone_path:
                await asyncio.to_thread(cleanup_clone_path, clone_path)
            yield _sse("done", {"ok": True, "message": "SSE stream closed."})

    return EventSourceResponse(event_stream())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Insight resolution — checks whether the TRIGGERING CONDITION still
# holds, not just whether the file changed.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _resolve_stale_insights(
    *,
    mcp: MongoMCPClient,
    db_name: str,
    repo_id: str,
) -> int:
    """Re-evaluate every unresolved insight against current file data.

    Each insight type has a concrete triggering condition defined in
    prompts.py. This function applies the INVERSE of that condition:
    if the condition no longer holds, the insight is marked resolved.

    Returns the number of insights resolved.
    """

    # ── Gather current state ────────────────────────────────────────
    file_response = await mcp.find(
        database=db_name,
        collection="files",
        filter_query={"repo_id": repo_id, "_deleted": {"$ne": True}},
        limit=50_000,
    )
    current_files = _extract_docs(file_response)
    file_meta: dict[str, dict[str, Any]] = {f["path"]: f for f in current_files if f.get("path")}

    # Count inbound dependents per file (how many other files point TO it).
    dep_response = await mcp.aggregate(
        database=db_name,
        collection="relationships",
        pipeline=[
            {"$match": {"repo_id": repo_id, "_deleted": {"$ne": True}}},
            {"$group": {"_id": "$to_file", "inbound_count": {"$sum": "$weight"}}},
        ],
    )
    dep_docs = _extract_docs(dep_response)
    inbound_dependents: dict[str, int] = {
        d["_id"]: d.get("inbound_count", 0) for d in dep_docs if d.get("_id")
    }

    # ── Fetch all unresolved insights ───────────────────────────────
    insight_response = await mcp.find(
        database=db_name,
        collection="insights",
        filter_query={"repo_id": repo_id, "resolved": False},
        limit=500,
    )
    unresolved = _extract_docs(insight_response)

    # ── Check each insight against its triggering condition ─────────
    ids_to_resolve: list[str] = []

    for insight in unresolved:
        insight_id = insight.get("_id")
        insight_type = insight.get("type", "")
        affected = insight.get("affected_files", [])

        if not insight_id or not affected:
            continue

        should_resolve = _check_condition_cleared(
            insight_type=insight_type,
            affected_files=affected,
            file_meta=file_meta,
            inbound_dependents=inbound_dependents,
        )

        if should_resolve:
            ids_to_resolve.append(insight_id)

    # ── Bulk-resolve ────────────────────────────────────────────────
    if ids_to_resolve:
        await mcp.update_many(
            database=db_name,
            collection="insights",
            filter_query={"_id": {"$in": ids_to_resolve}, "repo_id": repo_id},
            update_query={"$set": {"resolved": True}},
        )

    return len(ids_to_resolve)


def _check_condition_cleared(
    *,
    insight_type: str,
    affected_files: list[str],
    file_meta: dict[str, dict[str, Any]],
    inbound_dependents: dict[str, int],
) -> bool:
    """Return True if the triggering condition NO LONGER HOLDS for any
    of the affected files — meaning the insight should be resolved.

    Each rule mirrors the exact detection condition from prompts.py,
    inverted:

    ┌─────────────────────┬──────────────────────────────────────────────┐
    │ Insight type         │ Resolve when…                               │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ stale_docs           │ ALL affected files have                     │
    │                      │ doc_coverage >= 0.3 OR inbound deps < 3     │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ dependency_risk      │ ALL affected files have inbound deps < 5    │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ duplicate_logic      │ ANY affected file was deleted (deduplicated) │
    │                      │ or no longer exists in the index            │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ ownership_gap        │ ALL affected files have owner != ""          │
    │                      │ OR inbound deps < 2                         │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ complexity_spike     │ ALL affected files have max_complexity < 10  │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ breaking_change_risk │ ALL affected files have inbound deps < 5    │
    │                      │ OR doc_coverage >= 0.4                      │
    └─────────────────────┴──────────────────────────────────────────────┘
    """

    if insight_type == "stale_docs":
        # Triggered: doc_coverage < 0.3 AND inbound_dependents >= 3
        # Resolve: for ALL affected files, condition no longer holds.
        for path in affected_files:
            meta = file_meta.get(path)
            if meta is None:
                continue  # File deleted → condition cleared for this file.
            doc_cov = meta.get("doc_coverage", 0.0)
            deps = inbound_dependents.get(path, 0)
            if doc_cov < 0.3 and deps >= 3:
                return False  # Condition still holds for this file.
        return True

    if insight_type == "dependency_risk":
        # Triggered: inbound_dependents >= 5
        # Resolve: ALL affected files have < 5 inbound dependents.
        for path in affected_files:
            if path not in file_meta:
                continue  # Deleted → resolved.
            if inbound_dependents.get(path, 0) >= 5:
                return False
        return True

    if insight_type == "duplicate_logic":
        # Triggered: 2+ files with overlapping code.
        # Resolve: ANY affected file no longer exists (was deleted or
        # refactored away). If all files still exist, we cannot cheaply
        # re-check vector similarity, so the insight persists until the
        # next full insight generation pass.
        for path in affected_files:
            if path not in file_meta:
                return True  # At least one file gone → deduplicated.
        return False  # All files still exist → keep the insight.

    if insight_type == "ownership_gap":
        # Triggered: owner == "" AND inbound_dependents >= 2
        # Resolve: ALL affected files have a non-empty owner OR < 2 deps.
        for path in affected_files:
            meta = file_meta.get(path)
            if meta is None:
                continue  # Deleted → cleared.
            owner = meta.get("owner", "")
            deps = inbound_dependents.get(path, 0)
            if owner == "" and deps >= 2:
                return False
        return True

    if insight_type == "complexity_spike":
        # Triggered: max_complexity >= 10
        # Resolve: ALL affected files have max_complexity < 10.
        for path in affected_files:
            meta = file_meta.get(path)
            if meta is None:
                continue  # Deleted → cleared.
            if meta.get("max_complexity", 0) >= 10:
                return False
        return True

    if insight_type == "breaking_change_risk":
        # Triggered: inbound_dependents >= 5 AND doc_coverage < 0.4
        # Resolve: ALL affected files have < 5 deps OR doc_coverage >= 0.4.
        for path in affected_files:
            meta = file_meta.get(path)
            if meta is None:
                continue  # Deleted → cleared.
            deps = inbound_dependents.get(path, 0)
            doc_cov = meta.get("doc_coverage", 0.0)
            if deps >= 5 and doc_cov < 0.4:
                return False
        return True

    # Unknown insight type — don't resolve.
    return False


# ── Shared helpers ──────────────────────────────────────────────────


class _SyncError(Exception):
    def __init__(self, detail: str, *, code: str = "sync_error") -> None:
        self.code = code
        super().__init__(detail)


def _extract_docs(response: dict[str, Any]) -> list[dict[str, Any]]:
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
    import json
    payload = {
        "type": event,
        "message": data.get("message", ""),
        **data,
    }
    return {"event": event, "data": json.dumps(payload)}
