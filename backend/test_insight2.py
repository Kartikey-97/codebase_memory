import asyncio
import os
from dotenv import load_dotenv

load_dotenv(".env")

from app.api.insights import _run_insight_agent, build_insight_task_prompt
from app.config import get_settings
from app.db.mcp_mongo import create_mcp_client

async def main():
    settings = get_settings()
    mcp_client = create_mcp_client()
    repos = await mcp_client.find(database=settings.mongodb_db_name, collection="repos", filter_query={}, limit=1)
    
    docs = repos.get("documents", [])
    if not docs:
        print("No repos found in DB.")
        return
        
    repo_id = docs[0]["_id"]
    print(f"Testing insight generation for repo: {repo_id}")
    
    task_prompt = build_insight_task_prompt(repo_id=repo_id)
    try:
        await asyncio.to_thread(_run_insight_agent, repo_id=repo_id, task_prompt=task_prompt)
        print("Success! Agent completed without crashing.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Agent crashed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
