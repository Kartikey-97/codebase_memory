import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, TEXT
from pymongo.errors import OperationFailure

from app.config import get_settings

_client: AsyncIOMotorClient | None = None
logger = logging.getLogger(__name__)


def get_client() -> AsyncIOMotorClient:
    global _client
    settings = get_settings()
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_database() -> AsyncIOMotorDatabase:
    settings = get_settings()
    return get_client()[settings.mongodb_db_name]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def setup_indexes() -> None:
    db = get_database()

    await db["files"].create_index([("repo_id", ASCENDING)], name="files_repo_id_idx")
    await db["chunks"].create_index([("repo_id", ASCENDING)], name="chunks_repo_id_idx")
    await db["relationships"].create_index(
        [("repo_id", ASCENDING)], name="relationships_repo_id_idx"
    )
    await db["insights"].create_index([("repo_id", ASCENDING)], name="insights_repo_id_idx")
    await db["snapshots"].create_index([("repo_id", ASCENDING)], name="snapshots_repo_id_idx")

    await db["chunks"].create_index([("content", TEXT)], name="chunks_content_text_idx")
    await db["relationships"].create_index(
        [("from_file", ASCENDING), ("to_file", ASCENDING), ("type", ASCENDING)],
        name="relationships_from_to_type_idx",
    )

    vector_index_definition = {
        "name": "chunk_embeddings",
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 768,
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
        await db.command(
            {
                "createSearchIndexes": "chunks",
                "indexes": [vector_index_definition],
            }
        )
    except OperationFailure as exc:
        error_text = str(exc).lower()
        if "already exists" in error_text or "already defined" in error_text:
            logger.debug("Vector search index 'chunk_embeddings' already exists.")
            return
        # Local MongoDB (non-Atlas) won't support search indexes.
        if "command not found" in error_text or "nosuchcommand" in error_text:
            logger.warning(
                "Skipping Atlas vector search index creation in this environment; "
                "createSearchIndexes is unsupported by local MongoDB."
            )
            return
        raise
