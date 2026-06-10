import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import sys

async def main():
    uri = "mongodb+srv://kartikeygupta_db_user:cozh6pFNPKEnlQu9@codebase-memory.ww0plhy.mongodb.net/?appName=codebase-memory"
    client = AsyncIOMotorClient(uri)
    db = client["codebase_memory"]
    
    vector_index_definition = {
        "name": "chunk_embeddings",
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 1024,
                    "similarity": "cosine",
                },
                {
                    "type": "filter",
                    "path": "repo_id"
                },
                {
                    "type": "filter",
                    "path": "_deleted"
                }
            ]
        },
    }

    try:
        await db.command({
            "createSearchIndexes": "chunks",
            "indexes": [vector_index_definition]
        })
        print("Successfully created 'chunk_embeddings' index.")
    except Exception as e:
        print(f"Error creating index: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(main())
