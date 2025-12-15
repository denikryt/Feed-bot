from __future__ import annotations

import discord
from motor.motor_asyncio import AsyncIOMotorCollection

AllowedMentions = discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False)


def is_allowed_guild(guild_id: int | None, allowed_guild_ids: set[int]) -> bool:
    return guild_id is not None and guild_id in allowed_guild_ids


def build_content(message: discord.Message) -> str:
    # Prefer the author's display name when available, but keep a mention for clarity.
    if getattr(message.author, "bot", False):
        author_header = None
    else:
        display_name = getattr(message.author, "display_name", None)
        if display_name:
            author_header = f"**{display_name}**"
        else:
            author_header = "**Unknown User**"

    header = f"{author_header} 🔗 {message.jump_url}" if author_header else f"🔗 {message.jump_url}"
    parts = [header]
    if message.content:
        parts.append(message.content)
    return "\n".join(parts)


async def get_feed_channel(
    client: discord.Client, feed_channel_id: int, feed_channel_cache: dict[int, discord.abc.GuildChannel]
) -> discord.abc.GuildChannel | None:
    channel = feed_channel_cache.get(feed_channel_id)
    if channel:
        return channel

    channel = client.get_channel(feed_channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(feed_channel_id)
        except Exception as exc:
            print(f"Failed to fetch feed channel: {exc}")
            return None

    feed_channel_cache[feed_channel_id] = channel
    return channel


async def handle_message(
    client: discord.Client,
    message: discord.Message,
    feed_channel_id: int,
    mapping_collection: AsyncIOMotorCollection,
    allowed_guild_ids: set[int],
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    if not is_allowed_guild(getattr(message.guild, "id", None), allowed_guild_ids):
        return

    # Skip the feed bot itself and the feed channel to avoid loops.
    if (client.user and message.author.id == client.user.id) or message.channel.id == feed_channel_id:
        return

    feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
    if feed_channel is None:
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
            content=build_content(message),
            files=files,
            allowed_mentions=AllowedMentions,
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


async def handle_message_edit(
    client: discord.Client,
    _before: discord.Message,
    after: discord.Message,
    feed_channel_id: int,
    mapping_collection: AsyncIOMotorCollection,
    allowed_guild_ids: set[int],
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    if not is_allowed_guild(getattr(after.guild, "id", None), allowed_guild_ids):
        return

    if (client.user and after.author.id == client.user.id) or after.channel.id == feed_channel_id:
        return

    mapping = await mapping_collection.find_one({"_id": str(after.id)})
    if not mapping:
        return

    feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
    if feed_channel is None:
        return

    try:
        feed_message = await feed_channel.fetch_message(mapping["feed_message_id"])
    except discord.NotFound:
        return

    await feed_message.edit(content=build_content(after), allowed_mentions=AllowedMentions)


async def handle_message_delete(
    client: discord.Client,
    message: discord.Message,
    feed_channel_id: int,
    mapping_collection: AsyncIOMotorCollection,
    allowed_guild_ids: set[int],
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    if not is_allowed_guild(getattr(message.guild, "id", None), allowed_guild_ids):
        return

    if message.channel.id == feed_channel_id or (client.user and message.author and message.author.id == client.user.id):
        return

    mapping = await mapping_collection.find_one({"_id": str(message.id)})
    if not mapping:
        return

    feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
    if feed_channel is None:
        return

    feed_message_id = mapping["feed_message_id"]
    try:
        await client.http.delete_message(feed_channel.id, feed_message_id, reason="Source deleted")
    except discord.NotFound:
        pass
    except discord.Forbidden as exc:
        print(f"Failed to delete mirrored message (forbidden): {exc}")
    except Exception as exc:
        print(f"Failed to delete mirrored message: {exc}")
    finally:
        await mapping_collection.delete_one({"_id": str(message.id)})


async def handle_raw_message_delete(
    client: discord.Client,
    payload: discord.RawMessageDeleteEvent,
    feed_channel_id: int,
    mapping_collection: AsyncIOMotorCollection,
    allowed_guild_ids: set[int],
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    if not is_allowed_guild(payload.guild_id, allowed_guild_ids):
        return

    # Skip deletions that happen inside the feed channel.
    if payload.channel_id == feed_channel_id:
        return

    mapping = await mapping_collection.find_one({"_id": str(payload.message_id)})
    if not mapping:
        return

    feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
    if feed_channel is None:
        return

    feed_message_id = mapping["feed_message_id"]
    try:
        await client.http.delete_message(feed_channel.id, feed_message_id, reason="Source deleted")
    except discord.NotFound:
        pass
    except discord.Forbidden as exc:
        print(f"Failed to delete mirrored message (forbidden): {exc}")
    except Exception as exc:
        print(f"Failed to delete mirrored message: {exc}")
    finally:
        await mapping_collection.delete_one({"_id": str(payload.message_id)})
