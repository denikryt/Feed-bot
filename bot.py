import asyncio
import sys
import discord

from config import load_settings
from db import close_db, get_mapping_collection, init_db
from handlers import handle_message, handle_message_delete, handle_message_edit, handle_raw_message_delete

settings = load_settings()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

feed_channel_cache: dict[int, discord.abc.GuildChannel] = {}


@client.event
async def on_ready():
    print(f"Feed bot connected as {client.user}")


@client.event
async def on_message(message: discord.Message):
    await handle_message(
        client=client,
        message=message,
        feed_channel_id=settings.feed_channel_id,
        mapping_collection=get_mapping_collection(),
        allowed_guild_ids=settings.allowed_guild_ids,
        feed_channel_cache=feed_channel_cache,
    )


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    await handle_message_edit(
        client=client,
        _before=before,
        after=after,
        feed_channel_id=settings.feed_channel_id,
        mapping_collection=get_mapping_collection(),
        allowed_guild_ids=settings.allowed_guild_ids,
        feed_channel_cache=feed_channel_cache,
    )


@client.event
async def on_message_delete(message: discord.Message):
    await handle_message_delete(
        client=client,
        message=message,
        feed_channel_id=settings.feed_channel_id,
        mapping_collection=get_mapping_collection(),
        allowed_guild_ids=settings.allowed_guild_ids,
        feed_channel_cache=feed_channel_cache,
    )


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await handle_raw_message_delete(
        client=client,
        payload=payload,
        feed_channel_id=settings.feed_channel_id,
        mapping_collection=get_mapping_collection(),
        allowed_guild_ids=settings.allowed_guild_ids,
        feed_channel_cache=feed_channel_cache,
    )


async def main():
    await init_db(settings)
    try:
        await client.start(settings.token)
    finally:
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Bot failed: {exc}")
