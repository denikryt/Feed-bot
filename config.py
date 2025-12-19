import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    token: str
    mongo_uri: str
    mongo_db_name: str
    mapping_collection_name: str
    guild_routes_collection_name: str
    guild_permissions_collection_name: str
    log_file_path: str


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    mongo_uri = os.getenv("MONGO_URI")
    mongo_db_name = os.getenv("MONGO_DB")
    mapping_collection_name = os.getenv("MONGO_MESSAGE_MAPPING_COLLECTION") or "message_mappings"
    guild_routes_collection_name = os.getenv("MONGO_GUILD_ROUTES_COLLECTION") or "guild_routes"
    guild_permissions_collection_name = os.getenv("MONGO_GUILD_PERMISSIONS_COLLECTION") or "guild_permissions"
    log_file_path = os.getenv("LOG_FILE") or "logs/feed_bot.log"

    if not token or not mongo_uri or not mongo_db_name:
        sys.exit("DISCORD_TOKEN, MONGO_URI, and MONGO_DB must be set")

    return Settings(
        token=token,
        mongo_uri=mongo_uri,
        mongo_db_name=mongo_db_name,
        mapping_collection_name=mapping_collection_name,
        guild_routes_collection_name=guild_routes_collection_name,
        guild_permissions_collection_name=guild_permissions_collection_name,
        log_file_path=log_file_path,
    )
