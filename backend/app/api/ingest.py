from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db.mcp_mongo import MongoMCPError, create_mcp_client
from app.ingestion.chunker import CodeChunk, create_chunks_for_file
from app.ingestion.clone import cleanup_clone_path, clone_repo, clone_repo_to_temp
from app.ingestion.graph import build_relationship_documents
from app.ingestion.parser import ParsedFile, parse_file

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    url: str


@router.post("")
async def ingest_repo(payload: IngestRequest) -> EventSourceResponse:
    settings = get_settings()
    mcp_client = create_mcp_client()

    async def event_stream():
        repo_id = str(ObjectId())
        clone_path: str | None = None
        try:
            yield _sse("status", {"phase": "start", "message": "Starting ingestion pipeline..."})

            yield _sse("status", {"phase": "clone", "message": "Cloning repository metadata..."})
            metadata_result = await asyncio.to_thread(
                clone_repo, str(payload.url), settings.ingestion_tmp_dir
            )
            if not metadata_result.get("ok"):
                raise IngestionError(
                    metadata_result["error"]["detail"], code=metadata_result["error"]["code"]
                )

            file_summaries = metadata_result["files"]
            owner_by_path = {
                file_doc["path"]: file_doc.get("owner", "") for file_doc in file_summaries
            }

            repo_doc = {
                "_id": repo_id,
                "url": str(payload.url),
                "name": metadata_result["repo"]["name"],
                "default_branch": metadata_result["repo"]["default_branch"],
                "last_synced": datetime.now(UTC).isoformat(),
                "total_files": len(file_summaries),
                "total_chunks": 0,
                "status": "indexing",
            }
            await mcp_client.insert_many(
                database=settings.mongodb_db_name,
                collection="repos",
                documents=[repo_doc],
            )

            yield _sse(
                "status",
                {"phase": "clone", "message": "Cloning repository source for parsing..."},
            )
            source_clone_result = await asyncio.to_thread(
                clone_repo_to_temp, str(payload.url), settings.ingestion_tmp_dir
            )
            if not source_clone_result.get("ok"):
                raise IngestionError(
                    source_clone_result["error"]["detail"],
                    code=source_clone_result["error"]["code"],
                )

            clone_path = source_clone_result["clone_path"]
            clone_root = Path(clone_path)

            parse_targets = [path for path in owner_by_path if (clone_root / path).is_file()]
            yield _sse(
                "status",
                {"phase": "parse", "message": f"Parsing {len(parse_targets)} files..."},
            )

            files_collection_docs: list[dict[str, Any]] = []
            chunks_collection_docs: list[dict[str, Any]] = []
            skeletons_collection_docs: list[dict[str, Any]] = []
            parsed_by_path: dict[str, ParsedFile] = {}
            source_by_path: dict[str, str] = {}

            sem = asyncio.Semaphore(15)

            async def process_file_target(rel_path: str):
                async with sem:
                    absolute_path = clone_root / rel_path
                    source_text = await asyncio.to_thread(absolute_path.read_text, "utf-8", "ignore")
                    parsed_file = await asyncio.to_thread(parse_file, absolute_path, clone_root)
                    chunks = await asyncio.to_thread(create_chunks_for_file, parsed_file, source_text)
                    return rel_path, source_text, parsed_file, chunks

            tasks = [process_file_target(p) for p in parse_targets]
            for i in range(0, len(tasks), 50):
                batch = tasks[i:i+50]
                results = await asyncio.gather(*batch)
                for rel_path, source_text, parsed_file, chunks in results:
                    parsed_by_path[rel_path] = parsed_file
                    source_by_path[rel_path] = source_text

                    function_count = len(parsed_file.functions)
                    documented_count = sum(1 for function in parsed_file.functions if function.has_docstring)
                    doc_coverage = (documented_count / function_count) if function_count else 0.0
                    max_complexity = max((f.cyclomatic_complexity for f in parsed_file.functions), default=0) if function_count else 0

                    import hashlib
                    content_hash = hashlib.sha256(source_text.encode("utf-8", errors="replace")).hexdigest()
                    file_id = str(ObjectId())
                    stat_result = (clone_root / rel_path).stat()
                    file_doc = {
                        "_id": file_id,
                        "repo_id": repo_id,
                        "path": rel_path,
                        "language": parsed_file.language,
                        "size_bytes": int(stat_result.st_size),
                        "last_modified": datetime.fromtimestamp(stat_result.st_mtime, UTC).isoformat(),
                        "content_hash": content_hash,
                        "owner": owner_by_path.get(rel_path, ""),
                        "doc_coverage": doc_coverage,
                        "max_complexity": max_complexity,
                        "indexed_at": datetime.now(UTC).isoformat(),
                    }
                    files_collection_docs.append(file_doc)

                    chunk_docs = _build_chunk_documents(repo_id=repo_id, file_id=file_id, chunks=chunks)
                    chunks_collection_docs.extend(chunk_docs)

                    defined_symbols = [{"name": cls.name, "type": "class"} for cls in parsed_file.classes]
                    defined_symbols.extend([{"name": func.name, "type": "function"} for func in parsed_file.functions])

                    skeleton_doc = {
                        "_id": str(ObjectId()),
                        "repo_id": repo_id,
                        "file_id": file_id,
                        "path": rel_path,
                        "file_purpose": parsed_file.file_purpose,
                        "has_docs": doc_coverage > 0,
                        "complexity_score": max_complexity,
                        "defined_symbols": defined_symbols,
                        "exported_symbols": parsed_file.exported_symbols,
                        "imported_modules": [{"module": imp.raw, "symbols": imp.symbols} for imp in parsed_file.imports],
                        "classes": [{"name": cls.name, "base_classes": cls.base_classes, "methods": [{"name": m.name, "signature": m.signature, "is_public": m.is_public} for m in cls.methods]} for cls in parsed_file.classes],
                        "functions": [{"name": func.name, "signature": func.signature, "is_public": func.is_public, "is_async": False} for func in parsed_file.functions]
                    }
                    skeletons_collection_docs.append(skeleton_doc)

                yield _sse("status", {"phase": "parse", "message": f"Parsed {min(i+50, len(parse_targets))}/{len(parse_targets)} files..."})

            yield _sse(
                "status", {"phase": "insert", "message": "Writing files metadata to MongoDB MCP..."}
            )
            if files_collection_docs:
                await mcp_client.insert_many(
                    database=settings.mongodb_db_name,
                    collection="files",
                    documents=files_collection_docs,
                )
                
            if skeletons_collection_docs:
                await mcp_client.insert_many(
                    database=settings.mongodb_db_name,
                    collection="file_skeletons",
                    documents=skeletons_collection_docs,
                )

            yield _sse(
                "status",
                {"phase": "insert", "message": "Generating embeddings and writing chunks..."},
            )
            if chunks_collection_docs:

                await mcp_client.insert_many(
                    database=settings.mongodb_db_name,
                    collection="chunks",
                    documents=chunks_collection_docs,
                    enable_embeddings=True,
                )

            yield _sse("status", {"phase": "graph", "message": "Building relationship graph..."})
            relationships_docs = await asyncio.to_thread(
                build_relationship_documents,
                repo_id=repo_id,
                parsed_files=list(parsed_by_path.values()),
                source_by_path=source_by_path,
            )
            if relationships_docs:
                await mcp_client.insert_many(
                    database=settings.mongodb_db_name,
                    collection="relationships",
                    documents=relationships_docs,
                )

            yield _sse("status", {"phase": "manifest", "message": "Generating repository manifest..."})
            from app.ingestion.manifest import (
                detect_frameworks,
                detect_entry_points,
                extract_major_directories,
                identify_key_files,
                generate_architecture_summary_async
            )
            
            manifest_id = str(ObjectId())
            detected_languages = list(set([doc.get("language") for doc in files_collection_docs if doc.get("language")]))
            file_paths = [doc.get("path") for doc in files_collection_docs]
            
            frameworks = await asyncio.to_thread(detect_frameworks, source_by_path)
            entry_points = await asyncio.to_thread(detect_entry_points, file_paths)
            major_directories = await asyncio.to_thread(extract_major_directories, file_paths)
            important_files = await asyncio.to_thread(identify_key_files, file_paths)
            
            manifest_doc = {
                "_id": manifest_id,
                "repo_id": repo_id,
                "detected_languages": detected_languages,
                "frameworks": frameworks,
                "entry_points": entry_points,
                "major_directories": major_directories,
                "important_files": important_files,
                "architectural_roles": {},
                "architecture_summary": "Generating...",
                "graph_rag_ready": False
            }
            
            await mcp_client.insert_many(
                database=settings.mongodb_db_name,
                collection="repo_manifests",
                documents=[manifest_doc],
            )
            
            # Insert pending placeholder insight
            await mcp_client.insert_many(
                database=settings.mongodb_db_name,
                collection="insights",
                documents=[{
                    "_id": str(ObjectId()),
                    "repo_id": repo_id,
                    "type": "repo_overview",
                    "severity": "info",
                    "title": "Architecture Overview",
                    "description": "Generating repository overview...",
                    "affected_files": [],
                    "created_at": datetime.now(UTC).isoformat(),
                    "resolved": False,
                    "status": "pending"
                }]
            )
            
            # Fire and forget
            asyncio.create_task(generate_architecture_summary_async(repo_id, manifest_id, manifest_doc, parsed_by_path, source_by_path))

            yield _sse("status", {"phase": "insights", "message": "Queueing insights for background generation..."})

            insight_failed = False
            try:
                from app.api.insights import build_insight_task_prompt, _run_insight_agent
                
                async def fire_and_forget_insights():
                    try:
                        task_prompt = build_insight_task_prompt(repo_id=repo_id)
                        await asyncio.to_thread(_run_insight_agent, repo_id=repo_id, task_prompt=task_prompt)
                    except Exception as e:
                        print(f"Background insight generation failed: {e}")
                        
                asyncio.create_task(fire_and_forget_insights())
            except Exception as exc:
                insight_failed = True
                yield _sse("error", {"message": f"Insight queueing failed: {exc}"})

            final_status = "insight_failed" if insight_failed else "ready"
            final_repo_doc = {
                "last_synced": datetime.now(UTC).isoformat(),
                "total_files": len(files_collection_docs),
                "total_chunks": len(chunks_collection_docs),
                "status": final_status,
            }
            await mcp_client.update_many(
                database=settings.mongodb_db_name,
                collection="repos",
                filter_query={"_id": repo_id},
                update_query={"$set": final_repo_doc},
            )

            if insight_failed:
                return

            yield _sse(
                "complete",
                {
                    "repo_id": repo_id,
                    "repo_name": repo_doc.get("name", ""),
                    "total_files": len(files_collection_docs),
                    "total_chunks": len(chunks_collection_docs),
                    "total_relationships": len(relationships_docs),
                    "message": "Ingestion completed successfully.",
                },
            )
        except (IngestionError, MongoMCPError) as exc:
            try:
                await mcp_client.update_many(
                    database=settings.mongodb_db_name,
                    collection="repos",
                    filter_query={"_id": repo_id},
                    update_query={
                        "$set": {"status": "error", "last_synced": datetime.now(UTC).isoformat()}
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            error_message = str(exc)
            yield _sse(
                "error",
                {
                    "message": error_message,
                    "detail": error_message,
                    "code": getattr(exc, "code", "ingest_failed"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            try:
                await mcp_client.update_many(
                    database=settings.mongodb_db_name,
                    collection="repos",
                    filter_query={"_id": repo_id},
                    update_query={
                        "$set": {"status": "error", "last_synced": datetime.now(UTC).isoformat()}
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            error_message = f"Ingestion failed: {exc}"
            yield _sse(
                "error",
                {"message": error_message, "detail": error_message, "code": "ingest_failed"},
            )
        finally:
            if clone_path:
                await asyncio.to_thread(cleanup_clone_path, clone_path)
            yield _sse("done", {"ok": True, "message": "SSE stream closed."})

    return EventSourceResponse(event_stream())


class IngestionError(Exception):
    def __init__(self, detail: str, *, code: str = "ingest_error") -> None:
        self.code = code
        super().__init__(detail)


def _build_chunk_documents(
    *, repo_id: str, file_id: str, chunks: list[CodeChunk]
) -> list[dict[str, Any]]:
    chunk_docs: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_docs.append(
            {
                "file_id": file_id,
                "repo_id": repo_id,
                "content": chunk.content,
                "path": chunk.path,
                "chunk_index": chunk.chunk_index,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            }
        )
    return chunk_docs


def _sse(event: str, data: dict[str, Any]) -> dict[str, Any]:
    import json
    payload = {
        "type": event,
        "message": data.get("message", ""),
        **data,
    }
    return {"event": event, "data": json.dumps(payload)}
