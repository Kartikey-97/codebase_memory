import asyncio
from app.agent.builder import build_local_agent
from app.agent.prompts import build_insight_task_prompt

def test_insights():
    repo_id = "fairaid"
    prompt = build_insight_task_prompt(repo_id)
    agent = build_local_agent(repo_id)
    
    print(f"Running agent for repo: {repo_id}")
    response = agent.query(input=prompt)
    print("\n--- AGENT RESPONSE ---")
    print(response)

if __name__ == "__main__":
    test_insights()
