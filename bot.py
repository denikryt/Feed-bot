import asyncio
import os
import sys

import discord
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

# Validate required environment variables.
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

mongo_client: AsyncIOMotorClient | None = None
mapping_collection = None


async def init_mongo():
    global mongo_client, mapping_collection
    if mongo_client is not None:
        return

    client = AsyncIOMotorClient(
        mongo_uri,
        # Fail fast if the MongoDB service is unavailable.
        serverSelectionTimeoutMS=5000,
    )
    db = client[mongo_db_name]
    collection = db[mongo_collection_name]

    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        raise

    mongo_client = client
    mapping_collection = collection


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Feed bot connected as {client.user}")


@client.event
async def on_message(message: discord.Message):
    # Skip bots and the feed channel itself.
    if message.author.bot or message.channel.id == feed_channel_id_int:
        return

    is_thread = isinstance(message.channel, discord.Thread)
    thread_suffix = f" → <#{message.channel.id}>" if is_thread else ""
    header = f"<@{message.author.id}> 🔗 {message.jump_url}{thread_suffix}"

    parts = [header]
    if message.content:
        parts.append(message.content)
    content = "\n".join(parts)

    feed_channel = client.get_channel(feed_channel_id_int)
    if feed_channel is None:
        try:
            feed_channel = await client.fetch_channel(feed_channel_id_int)
        except Exception as exc:
            print(f"Failed to fetch feed channel: {exc}")
            return

    files = []
    try:
        for attachment in message.attachments:
            files.append(await attachment.to_file())
        parent_reference = None
        if message.reference and message.reference.message_id:
            parent_source_id = message.reference.message_id
            parent_mapping = await mapping_collection.find_one({"_id": str(parent_source_id)})
            if parent_mapping:
                try:
                    parent_reference = await feed_channel.fetch_message(parent_mapping["feed_message_id"])
                except discord.NotFound:
                    parent_reference = None
        existing_mapping = await mapping_collection.find_one({"_id": str(message.id)})
        if existing_mapping:
            return

        feed_message = await feed_channel.send(
            content=content,
            files=files,
            # Show the mention but do not ping the user.
            allowed_mentions=discord.AllowedMentions(
                users=False, roles=False, everyone=False, replied_user=False
            ),
            silent=True,
            reference=parent_reference,
        )
        await mapping_collection.insert_one(
            {
                "_id": str(message.id),
                "source_message_id": message.id,
                "feed_message_id": feed_message.id,
            }
        )
    finally:
        for f in files:
            try:
                f.close()
            except Exception:
                pass


async def main():
    await init_mongo()
    try:
        await client.start(token)
    finally:
        if mongo_client is not None:
            mongo_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"MongoDB connection failed: {exc}")
