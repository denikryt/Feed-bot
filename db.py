from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from config import Settings

mongo_client: Optional[AsyncIOMotorClient] = None
mapping_collection: Optional[AsyncIOMotorCollection] = None
guild_routes_collection: Optional[AsyncIOMotorCollection] = None
guild_permissions_collection: Optional[AsyncIOMotorCollection] = None


async def init_db(settings: Settings) -> None:
    """Initialize the Mongo client and mapping collection once."""
    global mongo_client, mapping_collection, guild_routes_collection, guild_permissions_collection

    if mongo_client is not None:
        return

    client = AsyncIOMotorClient(
        settings.mongo_uri,
        # Fail fast if the MongoDB service is unavailable.
        serverSelectionTimeoutMS=5000,
    )
    db = client[settings.mongo_db_name]
    mapping = db[settings.mapping_collection_name]
    routes = db[settings.guild_routes_collection_name]
    permissions = db[settings.guild_permissions_collection_name]

    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        raise

    mongo_client = client
    mapping_collection = mapping
    guild_routes_collection = routes
    guild_permissions_collection = permissions

    # Helpful indexes for common lookups.
    await mapping_collection.create_index(
        [("source_message_id", 1), ("feed_channel_id", 1)],
        name="source_message_feed_channel",
        unique=True,
    )
    await guild_routes_collection.create_index("source_guild_id", unique=True)
    await guild_permissions_collection.create_index("guild_id", unique=True)


def get_mapping_collection() -> AsyncIOMotorCollection:
    if mapping_collection is None:
        raise RuntimeError("MongoDB is not initialized")
    return mapping_collection


def get_guild_routes_collection() -> AsyncIOMotorCollection:
    if guild_routes_collection is None:
        raise RuntimeError("MongoDB is not initialized")
    return guild_routes_collection


def get_guild_permissions_collection() -> AsyncIOMotorCollection:
    if guild_permissions_collection is None:
        raise RuntimeError("MongoDB is not initialized")
    return guild_permissions_collection


async def close_db() -> None:
    global mongo_client, mapping_collection, guild_routes_collection, guild_permissions_collection

    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None

    mapping_collection = None
    guild_routes_collection = None
    guild_permissions_collection = None
