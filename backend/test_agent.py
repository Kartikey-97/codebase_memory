import asyncio
from app.api.insights import build_insight_task_prompt, _run_insight_agent

async def main():
    repo_id = "fairaid"  # Assuming fairaid is the repo
    task_prompt = build_insight_task_prompt(repo_id=repo_id)
    try:
        _run_insight_agent(repo_id=repo_id, task_prompt=task_prompt)
        print("Success")
    except Exception as e:
        print(f"Failed: {e}")

asyncio.run(main())
