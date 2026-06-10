import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import sys

async def main():
    uri = "mongodb+srv://kartikeygupta_db_user:cozh6pFNPKEnlQu9@codebase-memory.ww0plhy.mongodb.net/?appName=codebase-memory"
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
