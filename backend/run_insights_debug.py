import asyncio
from app.config import get_settings
from app.agent.builder import initialize_vertex_ai
from app.api.insights import build_insight_task_prompt, _run_insight_agent
from app.db.mcp_mongo import create_mcp_client

async def main():
    repo_id = "fairaid" # from screenshot
    # find the actual repo id
    mcp = create_mcp_client()
    repos = await mcp.find(database=get_settings().mongodb_db_name, collection="repos", filter_query={}, limit=10)
    for r in repos.get('data', []):
        if 'fairaid' in r.get('name', '').lower() or 'fairaid' in r.get('_id', '').lower():
            repo_id = r['_id']
            break
    print(f"Using repo_id: {repo_id}")
    task_prompt = build_insight_task_prompt(repo_id=repo_id)
    print("Running agent...")
    try:
        res = _run_insight_agent(repo_id=repo_id, task_prompt=task_prompt)
        print("Agent output:")
        print(res)
    except Exception as e:
        print("Error:")
        import traceback
        traceback.print_exc()

asyncio.run(main())
