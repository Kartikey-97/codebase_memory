import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv("backend/.env")
api_key = os.getenv("VOYAGE_API_KEY")

async def test():
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"input": ["def foo(): pass"] * 100, "model": "voyage-code-3"}
    async with httpx.AsyncClient() as client:
        # Send a few concurrent or sequential requests
        for i in range(5):
            print(f"Request {i}")
            resp = await client.post("https://api.voyageai.com/v1/embeddings", json=payload, headers=headers)
            print(resp.status_code, resp.text)

asyncio.run(test())
