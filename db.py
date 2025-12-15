from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from config import Settings

mongo_client: Optional[AsyncIOMotorClient] = None
mapping_collection: Optional[AsyncIOMotorCollection] = None


async def init_db(settings: Settings) -> None:
    """Initialize the Mongo client and mapping collection once."""
    global mongo_client, mapping_collection

    if mongo_client is not None:
        return

    client = AsyncIOMotorClient(
        settings.mongo_uri,
        # Fail fast if the MongoDB service is unavailable.
        serverSelectionTimeoutMS=5000,
    )
    db = client[settings.mongo_db_name]
    collection = db[settings.mongo_collection_name]

    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        raise

    mongo_client = client
    mapping_collection = collection


def get_mapping_collection() -> AsyncIOMotorCollection:
    if mapping_collection is None:
        raise RuntimeError("MongoDB is not initialized")
    return mapping_collection


async def close_db() -> None:
    global mongo_client, mapping_collection

    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None

    mapping_collection = None
