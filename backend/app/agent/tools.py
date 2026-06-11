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
        self.telemetry = {
            "tools_used": {},
            "graph_nodes_traversed": 0
        }
        
    def _track_tool(self, tool_name: str, nodes_added: int = 0) -> None:
        if tool_name not in self.telemetry["tools_used"]:
            self.telemetry["tools_used"][tool_name] = {"invocations": 0, "nodes_traversed": 0}
        self.telemetry["tools_used"][tool_name]["invocations"] += 1
        if nodes_added > 0:
            self.telemetry["tools_used"][tool_name]["nodes_traversed"] += nodes_added
            self.telemetry["graph_nodes_traversed"] += nodes_added

    def search_codebase(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Vector search code chunks for this repo and return top matches."""
        self._track_tool("search_codebase")
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

    def get_repo_manifest(self) -> dict[str, Any]:
        """Fetch the high-level repository manifest containing architecture, frameworks, and entry points. Use this tool FIRST when asked for a repository overview."""
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="repo_manifests",
                filter_query={"repo_id": self.repo_id},
                limit=1,
            )
        )
        documents = _extract_documents(response)
        if not documents:
            return {"error": "Manifest not found. You must rely on vector search."}
        
        doc = documents[0]
        # Remove mongo id
        doc.pop("_id", None)
        return doc

    def get_file_skeleton(self, file_path: str) -> dict[str, Any]:
        """Fetch the architectural skeleton of a file (signatures, imports, exports) without loading the full source code. Use this when you need to know what a file exposes or imports."""
        self._track_tool("get_file_skeleton")
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="file_skeletons",
                filter_query={"repo_id": self.repo_id, "path": file_path},
                limit=1,
            )
        )
        documents = _extract_documents(response)
        if not documents:
            return {"error": "Skeleton not found."}
        
        doc = documents[0]
        # Remove mongo id
        doc.pop("_id", None)
        return doc

    def find_subsystem_entrypoint(self, concept: str) -> dict[str, Any]:
        """Identify the most likely entry point file for a specific architectural concept (e.g., 'authentication', 'database')."""
        manifest_response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="repo_manifests",
                filter_query={"repo_id": self.repo_id},
                limit=1,
            )
        )
        manifests = _extract_documents(manifest_response)
        if manifests:
            manifest = manifests[0]
            roles = manifest.get("architectural_roles", {})
            for role_name, files in roles.items():
                if concept.lower() in role_name.lower() and files:
                    return {"entry_point": files[0], "reason": f"Matched architectural role '{role_name}'"}

        skel_response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="file_skeletons",
                filter_query={"repo_id": self.repo_id},
                limit=1000,
            )
        )
        skeletons = _extract_documents(skel_response)
        
        best_match = None
        highest_score = 0
        concept_lower = concept.lower()
        
        for skel in skeletons:
            score = 0
            path = skel.get("path", "").lower()
            purpose = skel.get("file_purpose", "").lower()
            
            if concept_lower in path:
                score += 5
            if concept_lower in purpose:
                score += 3
            for sym in skel.get("exported_symbols", []):
                if concept_lower in sym.lower():
                    score += 2
            
            if score > highest_score:
                highest_score = score
                best_match = skel.get("path")
                
        if best_match:
            return {"entry_point": best_match, "reason": "Matched via file purpose and exported symbols."}
        return {"error": "Could not identify an entry point for this concept. Try search_symbols."}

    def analyze_subsystem(self, entry_file: str, max_depth: int = 2) -> str:
        """Perform downstream traversal (Subsystem Analysis) starting from an entry point. Uses batch fetching to prevent N+1 Mongo queries."""
        self._track_tool("analyze_subsystem")
        visited = set([entry_file])
        frontier = [entry_file]
        
        summary_lines = [f"# Subsystem Analysis: `{entry_file}` (Max Depth: {max_depth})\n"]
        current_tokens = 0
        MAX_TOKENS = 20000
        
        for depth in range(max_depth + 1):
            if not frontier:
                break
            if current_tokens > MAX_TOKENS:
                summary_lines.append("\n> [!WARNING]\n> Token budget exceeded. Traversal truncated.")
                break
                
            summary_lines.append(f"## Level {depth} Dependencies")
            
            # Batch fetch skeletons
            skel_resp = _run_async(
                self._mcp_client.find(
                    database=self._db_name,
                    collection="file_skeletons",
                    filter_query={"repo_id": self.repo_id, "path": {"$in": frontier}},
                    limit=len(frontier)
                )
            )
            skeletons = _extract_documents(skel_resp)
            next_frontier = []
            
            for skel in skeletons:
                path = skel.get("path")
                purpose = skel.get("file_purpose", "No purpose extracted.")
                exports = skel.get("exported_symbols", [])
                
                block = f"**File:** `{path}`\n**Purpose:** {purpose}\n**Exported Symbols:** {', '.join(exports) if exports else 'None'}\n"
                classes = skel.get("classes", [])
                functions = skel.get("functions", [])
                
                if classes or functions:
                    block += "**Classes/Functions:**\n"
                    for cls in classes:
                        block += f"- `class {cls.get('name')}`\n"
                        for method in cls.get("methods", []):
                            block += f"  - `{method.get('name')}{method.get('signature', '()')}`\n"
                    for func in functions:
                        block += f"- `{func.get('name')}{func.get('signature', '()')}`\n"
                        
                summary_lines.append(block)
                current_tokens += len(block) // 4
                if current_tokens > MAX_TOKENS:
                    break
            
            if current_tokens > MAX_TOKENS or depth == max_depth:
                break
                
            # Batch fetch outward edges
            rel_resp = _run_async(
                self._mcp_client.find(
                    database=self._db_name,
                    collection="relationships",
                    filter_query={"repo_id": self.repo_id, "from_file": {"$in": frontier}, "_deleted": {"$ne": True}},
                    limit=500
                )
            )
            edges = _extract_documents(rel_resp)
            
            edge_map = {}
            for edge in edges:
                ff = edge.get("from_file")
                tf = edge.get("to_file")
                if not tf or tf in visited:
                    continue
                if ff not in edge_map:
                    edge_map[ff] = []
                edge_map[ff].append(edge)
                
            for ff, f_edges in edge_map.items():
                f_edges = sorted(f_edges, key=lambda e: e.get("weight", 0), reverse=True)[:5]
                for edge in f_edges:
                    tf = edge.get("to_file")
                    if tf and tf not in visited:
                        visited.add(tf)
                        next_frontier.append(tf)
            
            frontier = next_frontier
            
        self._track_tool("analyze_subsystem", nodes_added=len(visited))
        return "\n".join(summary_lines)

    def search_symbols(self, symbol_name: str) -> list[dict[str, Any]]:
        """Search the entire repository to find exactly which file defines a specific symbol (class or function)."""
        self._track_tool("search_symbols")
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="file_skeletons",
                filter_query={
                    "repo_id": self.repo_id,
                    "defined_symbols.name": symbol_name
                },
                limit=10,
            )
        )
        documents = _extract_documents(response)
        results = []
        for doc in documents:
            results.append({
                "path": doc.get("path"),
                "file_purpose": doc.get("file_purpose"),
                "matches": [s for s in doc.get("defined_symbols", []) if s.get("name") == symbol_name]
            })
        return results

    def find_related_files(self, file_path: str, limit: int = 20) -> dict[str, Any]:
        """Perform upstream traversal (Impact Analysis) to find files that depend on the target file. Incorporates Hub Detection."""
        self._track_tool("find_related_files")
        response = _run_async(
            self._mcp_client.find(
                database=self._db_name,
                collection="relationships",
                filter_query={"repo_id": self.repo_id, "to_file": file_path, "_deleted": {"$ne": True}},
                limit=limit + 200,  # Query more to detect hubs
            )
        )
        edges = _extract_documents(response)
        
        if len(edges) > 200:
             return {
                 "hub_detected": True,
                 "message": f"Global Hub Detected: '{file_path}' has {len(edges)} inbound dependencies. Upstream traversal halted to prevent graph explosion.",
                 "dependents_count": len(edges)
             }
             
        dependents = [edge.get("from_file") for edge in edges[:limit]]
        self._track_tool("find_related_files", nodes_added=len(dependents))
        return {
            "target": file_path,
            "dependents": dependents,
            "total_found": len(dependents)
        }

    def impact_analysis(self, file_path: str) -> dict[str, Any]:
        """Predict the blast radius of modifying a file or symbol using Upstream BFS, Hub Detection, and Logarithmic Scoring."""
        self._track_tool("impact_analysis")
        import math
        import re
        from collections import Counter
        from app.agent.classifier import RepoContextCache
        
        # Get Repo Context and File Count
        context = _run_async(RepoContextCache().get_context(self.repo_id))
        
        count_resp = _run_async(
            self._mcp_client.aggregate(
                database=self._db_name,
                collection="file_skeletons",
                pipeline=[{"$count": "total_files"}]
            )
        )
        total_files = 1000 # fallback
        try:
            docs = _extract_documents(count_resp)
            if docs:
                total_files = docs[0].get("total_files", 1000)
        except Exception:
            pass
            
        max_depth = 3
        hub_threshold = max(50, int(total_files * 0.05))
        
        visited = set()
        frontier = [file_path]
        
        direct_dependents = []
        indirect_dependents = []
        
        for depth in range(max_depth):
            if not frontier:
                break
                
            rel_resp = _run_async(
                self._mcp_client.find(
                    database=self._db_name,
                    collection="relationships",
                    filter_query={"repo_id": self.repo_id, "to_file": {"$in": frontier}, "_deleted": {"$ne": True}},
                    limit=10000
                )
            )
            edges = _extract_documents(rel_resp)
            
            # Hub Detection per node
            edge_counts = Counter(edge.get("to_file") for edge in edges)
            
            # Check if target is a global hub immediately
            if depth == 0 and edge_counts.get(file_path, 0) > hub_threshold:
                return {
                    "severity": "Critical",
                    "impact_score": 100,
                    "affected_files": [],
                    "affected_subsystems": ["Global"],
                    "critical_symbols": [],
                    "explanation": f"Target file '{file_path}' is a Global Hub with >{hub_threshold} direct dependents ({edge_counts[file_path]} found). Any modification carries extreme global risk."
                }
                    
            next_frontier = []
            for edge in edges:
                ff = edge.get("from_file")
                tf = edge.get("to_file")
                
                # If the node we are traversing FROM is a hub, don't fan out its dependents
                if edge_counts.get(tf, 0) > hub_threshold:
                    continue
                    
                if ff and ff not in visited and ff != file_path:
                    visited.add(ff)
                    next_frontier.append(ff)
                    if depth == 0:
                        direct_dependents.append(ff)
                    else:
                        indirect_dependents.append(ff)
                        
            frontier = next_frontier
            
        # Identify Critical Symbols
        critical_symbols = set()
        if direct_dependents:
            skel_resp = _run_async(
                self._mcp_client.find(
                    database=self._db_name,
                    collection="file_skeletons",
                    filter_query={"repo_id": self.repo_id, "path": {"$in": direct_dependents[:50]}},
                    limit=50
                )
            )
            docs = _extract_documents(skel_resp)
            for doc in docs:
                for imp in doc.get("imported_modules", []):
                    # Check if this import resolves to our target file
                    if file_path.endswith(imp.get("module_name", "") + ".py") or file_path.endswith(imp.get("module_name", "") + ".ts"):
                        for sym in imp.get("symbols", []):
                            critical_symbols.add(sym)

        # Logarithmic Scoring (Relative to Repo Size)
        total_dependents = len(direct_dependents) + len(indirect_dependents)
        dependency_percentile = (total_dependents / total_files) * 100 if total_files > 0 else 0
        
        impact_score = min(100, int((dependency_percentile / 10.0) * 100)) if dependency_percentile < 10 else 100
        # If it impacts >10% of the repo, it's 100.
        
        if impact_score < 15:
            severity = "Low"
        elif impact_score < 40:
            severity = "Medium"
        elif impact_score < 75:
            severity = "High"
        else:
            severity = "Critical"
            
        # Subsystem Detection using precise matching
        affected_subsystems = set()
        concepts = context.get("business_concepts", [])
        
        for f in direct_dependents + indirect_dependents:
            for c in concepts:
                # Use regex boundaries to prevent substring matching
                if re.search(r'\b' + re.escape(c) + r'\b', f, re.IGNORECASE):
                    affected_subsystems.add(c)
                    
        if not affected_subsystems:
            affected_subsystems.add("Unknown Domain")

        self._track_tool("impact_analysis", nodes_added=len(visited))
        return {
            "severity": severity,
            "impact_score": impact_score,
            "dependency_percentile": round(dependency_percentile, 2),
            "affected_files": direct_dependents[:15] + ["..."] if len(direct_dependents) > 15 else direct_dependents,
            "affected_subsystems": list(affected_subsystems),
            "critical_symbols": list(critical_symbols)[:10],
            "explanation": f"Found {len(direct_dependents)} direct and {len(indirect_dependents)} indirect dependents. Blast radius reaches {len(affected_subsystems)} semantic subsystems, affecting {round(dependency_percentile, 2)}% of the repository."
        }

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
        tools.get_repo_manifest,
        tools.get_file_skeleton,
        tools.search_symbols,
        tools.find_subsystem_entrypoint,
        tools.analyze_subsystem,
        tools.find_related_files,
        tools.impact_analysis,
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
