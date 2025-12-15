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


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    feed_channel_id = os.getenv("FEED_CHANNEL_ID")
    mongo_uri = os.getenv("MONGO_URI")
    mongo_db_name = os.getenv("MONGO_DB")
    mongo_collection_name = os.getenv("MONGO_COLLECTION")

    if not token or not feed_channel_id or not mongo_uri or not mongo_db_name or not mongo_collection_name:
        sys.exit("DISCORD_TOKEN, FEED_CHANNEL_ID, MONGO_URI, MONGO_DB, and MONGO_COLLECTION must be set")

    try:
        feed_channel_id_int = int(feed_channel_id)
    except ValueError:
        sys.exit("FEED_CHANNEL_ID must be an integer")

    return Settings(
        token=token,
        feed_channel_id=feed_channel_id_int,
        mongo_uri=mongo_uri,
        mongo_db_name=mongo_db_name,
        mongo_collection_name=mongo_collection_name,
    )
