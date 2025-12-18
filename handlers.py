from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import discord
import emoji
from motor.motor_asyncio import AsyncIOMotorCollection

AllowedMentions = discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False)


def split_leading_emoji(name: str) -> tuple[str | None, str]:
    """Return the leading emoji (if present) and the remaining text."""
    emoji_entries = emoji.emoji_list(name)
    if not emoji_entries:
        return None, name

    first = emoji_entries[0]
    if first.get("match_start") != 0:
        return None, name

    remainder = name[first["match_end"] :].lstrip()
    return first["emoji"], remainder


def _build_channel_url(message: discord.Message, channel_id: int | None) -> str:
    guild_id = getattr(message.guild, "id", None)
    if channel_id is None:
        return message.jump_url

    guild_segment = guild_id if guild_id is not None else "@me"
    return f"https://discord.com/channels/{guild_segment}/{channel_id}"


def extract_channel_name_parts(name: str) -> tuple[str | None, str, str | None]:
    """Return leading emoji (if any), cleaned name text, and trailing emoji (if any)."""
    entries = emoji.emoji_list(name)
    if not entries:
        return None, name, None

    first = entries[0]

    # Leading-only emoji: preserve existing behavior.
    if first.get("match_start") == 0:
        idx = 0
        block_end = 0
        while idx < len(entries) and entries[idx].get("match_start") == block_end:
            block_end = entries[idx]["match_end"]
            idx += 1

        if idx == len(entries):
            return first["emoji"], name[first["match_end"] :].lstrip(), None

        cleaned = emoji.replace_emoji(name, replace="")
        return None, cleaned, None

    # Single trailing emoji.
    if len(entries) == 1 and entries[0].get("match_end") == len(name):
        text_part = name[: entries[0]["match_start"]].rstrip()
        return None, text_part, entries[0]["emoji"]

    cleaned = emoji.replace_emoji(name, replace="")
    return None, cleaned, None


class FeedHeaderState:
    """In-memory header grouping state per feed channel."""

    def __init__(self) -> None:
        self._state: dict[int, dict[str, int | datetime]] = {}

    def should_include_header(
        self,
        feed_channel_id: int,
        source_channel_id: int,
        author_id: int,
        is_reply: bool,
        now: datetime,
    ) -> bool:
        if is_reply:
            return True

        last = self._state.get(feed_channel_id)
        if last is None:
            return True

        if last.get("source_channel_id") != source_channel_id or last.get("author_id") != author_id:
            return True

        last_timestamp = last.get("timestamp")
        if last_timestamp is None:
            return True

        return now - last_timestamp >= timedelta(minutes=5)

    def update(self, feed_channel_id: int, source_channel_id: int, author_id: int, timestamp: datetime | None = None) -> None:
        recorded_at = timestamp or datetime.now(timezone.utc)
        self._state[feed_channel_id] = {
            "author_id": author_id,
            "source_channel_id": source_channel_id,
            "timestamp": recorded_at,
        }


def build_content(message: discord.Message, include_header: bool) -> str | None:
    """Build mirrored content with a linked channel/thread header."""
    parts: list[str] = []
    if include_header:
        channel_label: str
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            parent_id = parent.id if parent else None
            parent_name = parent.name if parent and getattr(parent, "name", None) else f"channel-{message.channel.id}"
            leading_emoji, text_name, trailing_emoji = extract_channel_name_parts(parent_name)
            channel_display_name = text_name or f"channel-{parent_id or message.channel.id}"
            channel_url = _build_channel_url(message, parent_id)
            if leading_emoji:
                channel_link = f"{leading_emoji} [**{channel_display_name}**]({channel_url})"
            elif trailing_emoji:
                channel_link = f"[**#{channel_display_name}**]({channel_url}) {trailing_emoji}"
            else:
                channel_link = f"[**#{channel_display_name}**]({channel_url})"

            thread_name = message.channel.name
            thread_link = f"[**{thread_name}➜**]({message.jump_url})"
            channel_label = f"{channel_link} ⤷ {thread_link}"
        else:
            channel_name = getattr(message.channel, "name", None) or f"channel-{message.channel.id}"
            leading_emoji, text_name, trailing_emoji = extract_channel_name_parts(channel_name)
            channel_display_name = text_name or f"channel-{message.channel.id}"
            if leading_emoji:
                channel_label = f"{leading_emoji}[**{channel_display_name}➜**]({message.jump_url})"
            elif trailing_emoji:
                channel_label = (
                    f"[**#{channel_display_name}**]({message.jump_url}) {trailing_emoji}[**➜**]({message.jump_url})"
                )
            else:
                channel_label = f"[**#{channel_display_name}➜**]({message.jump_url})"

        author_header = None
        if not getattr(message.author, "bot", False):
            display_name = getattr(message.author, "display_name", None) or getattr(message.author, "name", None)
            author_header = f"**⬥ {display_name}**" if display_name else ""

        if author_header:
            header = f"-# {author_header} | {channel_label}"
        else:
            header = f"-# {channel_label}"
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
    routes_collection: AsyncIOMotorCollection,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
    header_state: FeedHeaderState,
) -> None:
    guild_id = getattr(message.guild, "id", None)
    if guild_id is None:
        return

    # Skip the feed bot itself.
    if client.user and message.author.id == client.user.id:
        return

    route = await routes_collection.find_one({"_id": str(guild_id)})
    feed_channels = route.get("feed_channels") if route else None
    if not feed_channels:
        return

    feed_channel_ids = {entry["channel_id"] for entry in feed_channels if isinstance(entry, dict) and "channel_id" in entry}
    # Avoid loops: do not mirror messages that originate from a configured feed channel.
    if message.channel.id in feed_channel_ids:
        return

    for feed_entry in feed_channels:
        feed_channel_id = feed_entry.get("channel_id")
        if feed_channel_id is None:
            continue

        await mirror_message_to_feed_channel(
            client=client,
            message=message,
            feed_channel_id=feed_channel_id,
            mapping_collection=mapping_collection,
            feed_channel_cache=feed_channel_cache,
            header_state=header_state,
        )


async def handle_message_edit(
    client: discord.Client,
    _before: discord.Message,
    after: discord.Message,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    guild_id = getattr(after.guild, "id", None)
    if guild_id is None:
        return

    if client.user and after.author.id == client.user.id:
        return

    cursor = mapping_collection.find({"source_message_id": after.id})
    async for mapping in cursor:
        feed_channel_id = mapping.get("feed_channel_id")
        feed_message_id = mapping.get("feed_message_id")
        if feed_channel_id is None or feed_message_id is None:
            continue

        feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
        if feed_channel is None:
            continue

        try:
            feed_message = await feed_channel.fetch_message(feed_message_id)
        except discord.NotFound:
            continue
        except discord.HTTPException as exc:
            print(f"Failed to fetch mirrored message {feed_message_id} for edit: {exc}")
            continue

        include_header = feed_message.content.startswith("-# ") if feed_message.content else False
        try:
            await feed_message.edit(
                content=build_content(after, include_header=include_header),
                allowed_mentions=AllowedMentions,
            )
        except discord.HTTPException as exc:
            print(f"Failed to edit mirrored message {feed_message_id}: {exc}")


async def handle_message_delete(
    client: discord.Client,
    message: discord.Message,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    guild_id = getattr(message.guild, "id", None)
    if guild_id is None:
        return

    if client.user and message.author and message.author.id == client.user.id:
        return

    await _delete_mirrored_messages(
        client=client,
        source_message_id=message.id,
        mapping_collection=mapping_collection,
        feed_channel_cache=feed_channel_cache,
    )


async def handle_raw_message_delete(
    client: discord.Client,
    payload: discord.RawMessageDeleteEvent,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    if payload.guild_id is None:
        return

    await _delete_mirrored_messages(
        client=client,
        source_message_id=payload.message_id,
        mapping_collection=mapping_collection,
        feed_channel_cache=feed_channel_cache,
    )


async def mirror_message_to_feed_channel(
    client: discord.Client,
    message: discord.Message,
    feed_channel_id: int,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
    header_state: FeedHeaderState,
) -> None:
    feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
    if feed_channel is None or not hasattr(feed_channel, "send"):
        return

    # Prevent duplicates across restarts and ensure idempotent sends.
    mapping_id = f"{message.id}:{feed_channel_id}"
    existing_mapping = await mapping_collection.find_one({"_id": mapping_id})
    if existing_mapping:
        return

    # Permission check: only mirror when the bot can send to the feed channel.
    if getattr(feed_channel, "guild", None) and getattr(feed_channel.guild, "me", None):
        perms = feed_channel.permissions_for(feed_channel.guild.me)  # type: ignore[arg-type]
        can_send = perms.send_messages or getattr(perms, "send_messages_in_threads", False)
        if not (perms.view_channel and can_send):
            return

    files: list[discord.File] = []
    fallback_sticker_files: list[discord.File] = []
    stickers = list(message.stickers)
    is_reply = bool(message.reference and message.reference.message_id)
    try:
        files = await build_attachment_files(message)

        current_time = datetime.now(timezone.utc)
        include_header = header_state.should_include_header(
            feed_channel_id=feed_channel_id,
            source_channel_id=message.channel.id,
            author_id=message.author.id,
            is_reply=is_reply,
            now=current_time,
        )

        parent_reference = None
        if is_reply:
            parent_source_id = message.reference.message_id
            parent_mapping = await mapping_collection.find_one({"_id": f"{parent_source_id}:{feed_channel_id}"})
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
                    header_state.update(
                        feed_channel_id=feed_channel_id,
                        source_channel_id=message.channel.id,
                        author_id=message.author.id,
                        timestamp=current_time,
                    )
                    await _store_mapping(
                        mapping_collection=mapping_collection,
                        mapping_id=mapping_id,
                        message=message,
                        feed_message_id=feed_message.id,
                        feed_channel_id=feed_channel_id,
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

        header_state.update(
            feed_channel_id=feed_channel_id,
            source_channel_id=message.channel.id,
            author_id=message.author.id,
            timestamp=current_time,
        )
        await _store_mapping(
            mapping_collection=mapping_collection,
            mapping_id=mapping_id,
            message=message,
            feed_message_id=feed_message.id,
            feed_channel_id=feed_channel_id,
        )
    finally:
        for f in [*files, *fallback_sticker_files]:
            try:
                f.close()
            except Exception:
                pass


async def _store_mapping(
    mapping_collection: AsyncIOMotorCollection,
    mapping_id: str,
    message: discord.Message,
    feed_message_id: int,
    feed_channel_id: int,
) -> None:
    await mapping_collection.insert_one(
        {
            "_id": mapping_id,
            "source_message_id": message.id,
            "feed_message_id": feed_message_id,
            "feed_channel_id": feed_channel_id,
            "source_guild_id": getattr(message.guild, "id", None),
        }
    )


async def _delete_mirrored_messages(
    client: discord.Client,
    source_message_id: int,
    mapping_collection: AsyncIOMotorCollection,
    feed_channel_cache: dict[int, discord.abc.GuildChannel],
) -> None:
    cursor = mapping_collection.find({"source_message_id": source_message_id})
    async for mapping in cursor:
        feed_channel_id = mapping.get("feed_channel_id")
        feed_message_id = mapping.get("feed_message_id")
        if feed_channel_id is None or feed_message_id is None:
            continue

        feed_channel = await get_feed_channel(client, feed_channel_id, feed_channel_cache)
        if feed_channel is None:
            await mapping_collection.delete_one({"_id": mapping["_id"]})
            continue

        try:
            await client.http.delete_message(feed_channel.id, feed_message_id, reason="Source deleted")
        except discord.NotFound:
            pass
        except discord.Forbidden as exc:
            print(f"Failed to delete mirrored message (forbidden): {exc}")
        except Exception as exc:
            print(f"Failed to delete mirrored message: {exc}")
        finally:
            await mapping_collection.delete_one({"_id": mapping["_id"]})
