from __future__ import annotations

import asyncio
from typing import Any

def detect_frameworks(source_by_path: dict[str, str]) -> list[dict[str, Any]]:
    frameworks = {
        "FastAPI": {"score": 0, "signals": []},
        "Flask": {"score": 0, "signals": []},
        "Django": {"score": 0, "signals": []},
        "React": {"score": 0, "signals": []},
        "Next.js": {"score": 0, "signals": []},
        "Express": {"score": 0, "signals": []},
        "NestJS": {"score": 0, "signals": []},
        "Vue": {"score": 0, "signals": []},
        "Angular": {"score": 0, "signals": []},
    }

    for path, content in source_by_path.items():
        lower_path = path.lower()
        if "requirements.txt" in lower_path or "pyproject.toml" in lower_path:
            if "fastapi" in content.lower():
                frameworks["FastAPI"]["score"] += 5
                frameworks["FastAPI"]["signals"].append(f"Found in {path}")
            if "flask" in content.lower():
                frameworks["Flask"]["score"] += 5
                frameworks["Flask"]["signals"].append(f"Found in {path}")
            if "django" in content.lower():
                frameworks["Django"]["score"] += 5
                frameworks["Django"]["signals"].append(f"Found in {path}")
        
        if "package.json" in lower_path:
            if "next" in content.lower():
                frameworks["Next.js"]["score"] += 5
                frameworks["Next.js"]["signals"].append("Found 'next' in package.json")
            if "react" in content.lower() and "next" not in content.lower():
                frameworks["React"]["score"] += 3
                frameworks["React"]["signals"].append("Found 'react' in package.json")
            if "express" in content.lower():
                frameworks["Express"]["score"] += 5
                frameworks["Express"]["signals"].append("Found 'express' in package.json")
            if "@nestjs/core" in content.lower():
                frameworks["NestJS"]["score"] += 5
                frameworks["NestJS"]["signals"].append("Found '@nestjs/core' in package.json")
            if "vue" in content.lower():
                frameworks["Vue"]["score"] += 5
                frameworks["Vue"]["signals"].append("Found 'vue' in package.json")
            if "@angular/core" in content.lower():
                frameworks["Angular"]["score"] += 5
                frameworks["Angular"]["signals"].append("Found '@angular/core' in package.json")

        if lower_path.endswith(".jsx") or lower_path.endswith(".tsx"):
            frameworks["React"]["score"] += 0.1
            if len(frameworks["React"]["signals"]) < 2:
                frameworks["React"]["signals"].append("JSX/TSX files present")

    results = []
    for name, data in frameworks.items():
        if data["score"] > 0:
            results.append({
                "name": name,
                "confidence_score": min(data["score"] / 5.0, 1.0),
                "signals": list(set(data["signals"]))
            })
    return sorted(results, key=lambda x: x["confidence_score"], reverse=True)


def detect_entry_points(file_paths: list[str]) -> list[str]:
    entry_points = []
    heuristics = ["main.py", "app.py", "server.ts", "index.ts", "main.ts", "server.js", "index.js"]
    for path in file_paths:
        file_name = path.split("/")[-1]
        if file_name in heuristics:
            entry_points.append(path)
    return entry_points


def identify_key_files(file_paths: list[str]) -> list[str]:
    key_files = []
    for path in file_paths:
        lower_path = path.lower()
        if any(kw in lower_path for kw in ["main.", "app.", "server.", "auth.", "database.", "db.", "routes.", "config.", "settings."]):
            key_files.append(path)
    return key_files


def extract_major_directories(file_paths: list[str]) -> list[str]:
    dirs = set()
    for path in file_paths:
        parts = path.split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
    return sorted(list(dirs))


async def generate_architecture_summary_async(repo_id: str, manifest_id: str, manifest_doc: dict[str, Any], parsed_by_path: dict[str, Any], source_by_path: dict[str, str]) -> None:
    from app.config import get_settings
    from app.db.mcp_mongo import create_mcp_client
    from app.agent.builder import initialize_vertex_ai, get_ingest_model
    from datetime import datetime, UTC
    import json
    
    settings = get_settings()
    mcp_client = create_mcp_client()

    # Priority 1: README
    readme_content = ""
    for path, content in source_by_path.items():
        if path.lower().endswith("readme.md"):
            # Limit to first 2000 chars to avoid prompt bloat
            readme_content = content[:2000]
            break

    # Priority 2: Routes, Models, Controllers, Entities, API
    important_classes = []
    important_functions = []
    
    # Priority 4: Exported symbols
    all_exported_symbols = []

    for path, parsed in parsed_by_path.items():
        lp = path.lower()
        if any(kw in lp for kw in ["route", "api", "controller", "model", "entity", "service", "schema"]):
            for cls in parsed.classes:
                important_classes.append(f"{path}: class {cls.name}")
            for func in parsed.functions:
                if func.is_public:
                    important_functions.append(f"{path}: func {func.name}")
        
        for exp in parsed.exported_symbols:
            all_exported_symbols.append(exp)

    # Bound Context
    important_classes = important_classes[:20]
    important_functions = important_functions[:20]
    all_exported_symbols = all_exported_symbols[:50]

    frameworks = manifest_doc.get("frameworks", [])

    # Deterministic Confidence Score
    confidence = 0.0
    if readme_content:
        confidence += 0.4
    if important_functions or any("route" in path.lower() or "api" in path.lower() for path in parsed_by_path.keys()):
        confidence += 0.15
    if important_classes or any("model" in path.lower() or "entity" in path.lower() for path in parsed_by_path.keys()):
        confidence += 0.15
    if frameworks:
        confidence += 0.1
    
    # Wait to add business_concepts logic until after generation
    
    prompt = f"""
Analyze the following repository signals and generate a repository overview manifest.
Do NOT use markdown outside of the requested JSON structure.

Priority 1 (README snippet):
{readme_content}

Priority 2 (Key Classes & Functions):
Classes: {important_classes}
Functions: {important_functions}

Priority 3 & 4 (Frameworks & Exports):
Frameworks: {[f['name'] for f in frameworks]}
Major Directories: {manifest_doc.get('major_directories', [])}
Top Exported Symbols: {all_exported_symbols}

Task:
1. Write a 3-4 sentence project overview explaining the core product/domain (What does this project actually do? e.g., 'An e-commerce platform', 'A ridesharing app').
2. Write a 2-3 sentence architecture summary explaining the technical structure (e.g., 'React frontend communicating with a FastAPI backend').
3. Group the files into 'architectural_roles' (frontend, backend_api, database).
4. Extract 5-10 human-readable 'business_concepts' that this codebase implements.
5. Determine the project_type (e.g., 'Web Application', 'Library', 'CLI Tool') and domain (e.g., 'Finance', 'E-commerce').

Output exactly this JSON format:
{{
    "project_type": "...",
    "domain": "...",
    "architecture_summary": "Project Overview: ...\\n\\nArchitecture: ...",
    "architectural_roles": {{
        "frontend": ["file1", "file2"],
        "backend_api": ["file3"],
        "database": []
    }},
    "business_concepts": ["concept1", "concept2"]
}}
"""
    
    parsed = {}
    last_error = ""
    retry_count = 0
    success = False

    try:
        initialize_vertex_ai()
        model = get_ingest_model()
        
        for attempt in range(3):
            retry_count = attempt
            try:
                result = await asyncio.to_thread(model.generate_content, prompt)
                text = result.text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                
                parsed = json.loads(text.strip())
                success = True
                break
            except Exception as exc:
                last_error = str(exc)
                if "429" in str(exc) and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if attempt == 2:
                    break

        if success:
            if parsed.get("business_concepts"):
                confidence += 0.2
            confidence = min(confidence, 1.0)
            
            await mcp_client.update_many(
                database=settings.mongodb_db_name,
                collection="repo_manifests",
                filter_query={"_id": manifest_id},
                update_query={
                    "$set": {
                        "project_type": parsed.get("project_type", ""),
                        "domain": parsed.get("domain", ""),
                        "architecture_summary": parsed.get("architecture_summary", ""),
                        "architectural_roles": parsed.get("architectural_roles", {}),
                        "business_concepts": parsed.get("business_concepts", []),
                        "confidence_score": confidence,
                        "extraction_timestamp": datetime.now(UTC).isoformat()
                    }
                }
            )
            
            # Update the pending insight to complete
            await mcp_client.update_many(
                database=settings.mongodb_db_name,
                collection="insights",
                filter_query={"repo_id": repo_id, "type": "repo_overview"},
                update_query={
                    "$set": {
                        "status": "complete",
                        "description": parsed.get("architecture_summary", "")
                    }
                }
            )
        else:
            # Failed
            await mcp_client.update_many(
                database=settings.mongodb_db_name,
                collection="insights",
                filter_query={"repo_id": repo_id, "type": "repo_overview"},
                update_query={
                    "$set": {
                        "status": "failed",
                        "description": f"Repository overview generation failed after {retry_count+1} attempts. Error: {last_error}",
                        "retry_count": retry_count + 1,
                        "last_error": last_error,
                        "last_attempt_at": datetime.now(UTC).isoformat()
                    }
                }
            )

    except Exception as exc:
        print(f"Failed to generate architecture summary asynchronously: {exc}")
        await mcp_client.update_many(
            database=settings.mongodb_db_name,
            collection="insights",
            filter_query={"repo_id": repo_id, "type": "repo_overview"},
            update_query={
                "$set": {
                    "status": "failed",
                    "description": f"Repository overview generation failed catastrophically: {exc}",
                    "last_error": str(exc),
                    "last_attempt_at": datetime.now(UTC).isoformat()
                }
            }
        )
