from __future__ import annotations

import io

import discord
from motor.motor_asyncio import AsyncIOMotorCollection

AllowedMentions = discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False)
_last_feed_state: dict[str, int] | None = None


def is_allowed_guild(guild_id: int | None, allowed_guild_ids: set[int]) -> bool:
    return guild_id is not None and guild_id in allowed_guild_ids


def _should_include_header(source_channel_id: int, author_id: int, is_reply: bool) -> bool:
    # Replies always render a header regardless of block/grouping.
    if is_reply:
        return True

    if _last_feed_state is None:
        return True

    return (
        _last_feed_state.get("source_channel_id") != source_channel_id
        or _last_feed_state.get("author_id") != author_id
    )


def _update_last_feed_state(source_channel_id: int, author_id: int) -> None:
    global _last_feed_state
    _last_feed_state = {"author_id": author_id, "source_channel_id": source_channel_id}


def build_content(message: discord.Message, include_header: bool) -> str | None:
    # Prefer the author's display name when available, but keep a mention for clarity.
    parts: list[str] = []
    if include_header:
        if getattr(message.author, "bot", False):
            author_header = None
        else:
            display_name = getattr(message.author, "display_name", None)
            if display_name:
                author_header = f"**⬥ {display_name}**"
            else:
                author_header = "**⬥ Unknown User**"

        header = f"-# {author_header} |{message.jump_url}" if author_header else f"-# 🔗 {message.jump_url}"
        parts.append(header)

    if message.content:
        parts.append(message.content)

    if not parts:
        return None

    return "\n".join(parts)


async def _sticker_to_file(sticker: discord.StickerItem) -> discord.File | None:
    try:
        content = await sticker.read()
    except TypeError:
        # Lottie stickers cannot be rendered as files; skip them.
        print(f"Skipping unsupported lottie sticker: {sticker.name} ({sticker.id})")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to download sticker {sticker.id}: {exc}")
        return None

    filename = f"sticker-{sticker.id}.{sticker.format.file_extension}"
    return discord.File(io.BytesIO(content), filename=filename)


async def build_attachment_files(message: discord.Message) -> list[discord.File]:
    files: list[discord.File] = []
    for attachment in message.attachments:
        files.append(await attachment.to_file())

    return files


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

    files: list[discord.File] = []
    fallback_sticker_files: list[discord.File] = []
    stickers = list(message.stickers)
    is_reply = bool(message.reference and message.reference.message_id)
    try:
        files = await build_attachment_files(message)

        include_header = _should_include_header(message.channel.id, message.author.id, is_reply=is_reply)

        parent_reference = None
        if is_reply:
            parent_source_id = message.reference.message_id
            parent_mapping = await mapping_collection.find_one({"_id": str(parent_source_id)})
            if parent_mapping:
                try:
                    parent_reference = await feed_channel.fetch_message(parent_mapping["feed_message_id"])
                except discord.NotFound:
                    parent_reference = None
                except discord.HTTPException as exc:
                    print(
                        f"Failed to fetch parent feed message for {message.id} "
                        f"(reply target {parent_mapping['feed_message_id']}): {exc}"
                    )
                    parent_reference = None

        existing_mapping = await mapping_collection.find_one({"_id": str(message.id)})
        if existing_mapping:
            return

        content = build_content(message, include_header=include_header)
        send_kwargs = {
            "files": files,
            "allowed_mentions": AllowedMentions,
            "reference": parent_reference,
        }
        if content is not None:
            send_kwargs["content"] = content
        if stickers:
            send_kwargs["stickers"] = stickers

        try:
            feed_message = await feed_channel.send(**send_kwargs)
        except discord.HTTPException as exc:
            # If Discord rejects the reply reference (e.g., deleted/invalid parent), retry without it once.
            if send_kwargs.get("reference"):
                send_kwargs["reference"] = None
                try:
                    feed_message = await feed_channel.send(**send_kwargs)
                except discord.HTTPException as retry_exc:
                    exc = retry_exc
                else:
                    _update_last_feed_state(source_channel_id=message.channel.id, author_id=message.author.id)
                    await mapping_collection.insert_one(
                        {
                            "_id": str(message.id),
                            "source_message_id": message.id,
                            "feed_message_id": feed_message.id,
                        }
                    )
                    return

            # If stickers failed (e.g., missing permissions), fall back to mirroring as files.
            if stickers:
                for sticker in stickers:
                    sticker_file = await _sticker_to_file(sticker)
                    if sticker_file:
                        fallback_sticker_files.append(sticker_file)

            if not fallback_sticker_files:
                print(f"Failed to mirror message {message.id}: {exc}")
                raise

            send_kwargs.pop("stickers", None)
            send_kwargs["files"] = [*files, *fallback_sticker_files]
            feed_message = await feed_channel.send(**send_kwargs)

        _update_last_feed_state(source_channel_id=message.channel.id, author_id=message.author.id)
        await mapping_collection.insert_one(
            {
                "_id": str(message.id),
                "source_message_id": message.id,
                "feed_message_id": feed_message.id,
            }
        )
    finally:
        for f in [*files, *fallback_sticker_files]:
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

    include_header = feed_message.content.startswith("-# ") if feed_message.content else False
    await feed_message.edit(
        content=build_content(after, include_header=include_header),
        allowed_mentions=AllowedMentions,
    )


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
