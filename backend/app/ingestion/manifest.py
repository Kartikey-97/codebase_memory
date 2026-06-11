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


async def generate_architecture_summary_async(repo_id: str, manifest_id: str, manifest_doc: dict[str, Any]) -> None:
    from app.config import get_settings
    from app.db.mcp_mongo import create_mcp_client
    from app.agent.builder import initialize_vertex_ai
    from vertexai.generative_models import GenerativeModel
    import json

    try:
        initialize_vertex_ai()
        model = GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""
Analyze the following repository manifest and generate an architecture summary.
Do NOT use markdown outside of the requested JSON structure.

Manifest:
Languages: {manifest_doc.get('detected_languages', [])}
Frameworks: {[f['name'] for f in manifest_doc.get('frameworks', [])]}
Entry Points: {manifest_doc.get('entry_points', [])}
Major Directories: {manifest_doc.get('major_directories', [])}
Important Files: {manifest_doc.get('important_files', [])}

Task:
1. Write a 3-4 sentence project overview explaining the core product/domain (What does this project actually do? e.g., 'An e-commerce platform', 'A ridesharing app').
2. Write a 2-3 sentence architecture summary explaining the technical structure (e.g., 'React frontend communicating with a FastAPI backend').
3. Group the files into 'architectural_roles' (frontend, backend_api, database).
4. Extract 5-10 human-readable 'business_concepts' that this codebase implements.

Output exactly this JSON format:
{{
    "architecture_summary": "Project Overview: ...\\n\\nArchitecture: ...",
    "architectural_roles": {{
        "frontend": ["file1", "file2"],
        "backend_api": ["file3"],
        "database": []
    }},
    "business_concepts": ["concept1", "concept2"]
}}
"""
        for attempt in range(3):
            try:
                result = await asyncio.to_thread(model.generate_content, prompt)
                break
            except Exception as exc:
                if "429" in str(exc) and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        
        text = result.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        
        parsed = json.loads(text.strip())
        
        settings = get_settings()
        mcp_client = create_mcp_client()
        
        await mcp_client.update_many(
            database=settings.mongodb_db_name,
            collection="repo_manifests",
            filter_query={"_id": manifest_id},
            update_query={
                "$set": {
                    "architecture_summary": parsed.get("architecture_summary", ""),
                    "architectural_roles": parsed.get("architectural_roles", {}),
                    "business_concepts": parsed.get("business_concepts", [])
                }
            }
        )
        
        from datetime import datetime, UTC
        from bson import ObjectId
        await mcp_client.insert_many(
            database=settings.mongodb_db_name,
            collection="insights",
            documents=[{
                "_id": str(ObjectId()),
                "repo_id": repo_id,
                "type": "repo_overview",
                "severity": "info",
                "title": "Architecture Overview",
                "description": parsed.get("architecture_summary", ""),
                "affected_files": [],
                "created_at": datetime.now(UTC).isoformat(),
                "resolved": False
            }]
        )
    except Exception as exc:
        print(f"Failed to generate architecture summary asynchronously: {exc}")
