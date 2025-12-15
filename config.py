import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    token: str
    feed_channel_id: int
    mongo_uri: str
    mongo_db_name: str
    mongo_collection_name: str
    allowed_guild_ids: set[int]


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    feed_channel_id = os.getenv("FEED_CHANNEL_ID")
    mongo_uri = os.getenv("MONGO_URI")
    mongo_db_name = os.getenv("MONGO_DB")
    mongo_collection_name = os.getenv("MONGO_COLLECTION")
    allowed_guild_ids_raw = os.getenv("ALLOWED_GUILD_IDS")

    if not token or not feed_channel_id or not mongo_uri or not mongo_db_name or not mongo_collection_name or not allowed_guild_ids_raw:
        sys.exit(
            "DISCORD_TOKEN, FEED_CHANNEL_ID, MONGO_URI, MONGO_DB, MONGO_COLLECTION, and ALLOWED_GUILD_IDS must be set"
        )

    try:
        feed_channel_id_int = int(feed_channel_id)
    except ValueError:
        sys.exit("FEED_CHANNEL_ID must be an integer")

    allowed_guild_ids: set[int] = set()
    for raw_id in allowed_guild_ids_raw.split(","):
        guild_id = raw_id.strip()
        if not guild_id:
            continue
        try:
            allowed_guild_ids.add(int(guild_id))
        except ValueError:
            sys.exit("ALLOWED_GUILD_IDS must contain only integers (comma-separated)")

    if not allowed_guild_ids:
        sys.exit("ALLOWED_GUILD_IDS must include at least one guild id")

    return Settings(
        token=token,
        feed_channel_id=feed_channel_id_int,
        mongo_uri=mongo_uri,
        mongo_db_name=mongo_db_name,
        mongo_collection_name=mongo_collection_name,
        allowed_guild_ids=allowed_guild_ids,
    )
