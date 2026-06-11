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
        print(f"Error creating vector index: {e}")
        
    try:
        from pymongo import ASCENDING, DESCENDING
        await db.query_telemetry.create_index(
            [("repo_id", ASCENDING), ("timestamp", DESCENDING)],
            name="repo_id_timestamp_idx"
        )
        await db.query_telemetry.create_index(
            "timestamp",
            expireAfterSeconds=2592000,
            name="timestamp_ttl_idx"
        )
        print("Successfully created 'query_telemetry' standard indexes.")
    except Exception as e:
        print(f"Error creating telemetry indexes: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(main())
