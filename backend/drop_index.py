import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
import sys
from dotenv import load_dotenv

load_dotenv()

async def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("MONGODB_URI not found in environment.")
        return
    client = AsyncIOMotorClient(uri)
    db = client["codebase_memory"]
    try:
        await db.command({
            "dropSearchIndex": "chunks",
            "name": "chunk_embeddings"
        })
        print("Successfully dropped 'chunk_embeddings' index.")
    except Exception as e:
        print(f"Error dropping index (it might not exist): {e}")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(main())
