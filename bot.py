import asyncio
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands
from pymongo import ReturnDocument

from config import load_settings
from db import (
    close_db,
    get_guild_permissions_collection,
    get_guild_routes_collection,
    get_mapping_collection,
    init_db,
)
from handlers import (
    FeedHeaderState,
    handle_message,
    handle_message_delete,
    handle_message_edit,
    handle_raw_message_delete,
)

settings = load_settings()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

feed_channel_cache: dict[int, discord.abc.GuildChannel] = {}
header_state = FeedHeaderState()
_commands_synced = False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _require_guild(interaction: discord.Interaction) -> discord.Guild:
    if interaction.guild is None:
        raise app_commands.CheckFailure("This command can only be used inside a server.")
    return interaction.guild


def _is_admin(user: discord.abc.User) -> bool:
    if not isinstance(user, discord.Member):
        return False
    return bool(getattr(user.guild_permissions, "administrator", False))


class UserIDTransformer(app_commands.Transformer):
    """Convert an autocompleted user id into a Discord user object."""

    type = discord.AppCommandOptionType.string

    async def transform(self, interaction: discord.Interaction, value: str) -> discord.User:
        cleaned = value.strip()
        if cleaned.startswith("<@") and cleaned.endswith(">"):
            cleaned = cleaned.removeprefix("<@").removeprefix("!").removesuffix(">")

        try:
            user_id = int(cleaned)
        except ValueError as exc:
            raise app_commands.TransformError("Invalid user id provided.") from exc

        user = interaction.client.get_user(user_id)  # type: ignore[attr-defined]
        if user:
            return user

        try:
            return await interaction.client.fetch_user(user_id)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            raise app_commands.TransformError(f"Could not resolve user {value}") from exc


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = _require_guild(interaction)
        if not _is_admin(interaction.user):
            raise app_commands.CheckFailure("You must be a guild administrator to use this command.")
        return guild is not None

    return app_commands.check(predicate)


def admin_or_authorized():
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = _require_guild(interaction)
        if _is_admin(interaction.user):
            return True

        permissions_collection = get_guild_permissions_collection()
        doc = await permissions_collection.find_one({"_id": str(guild.id)})
        authorized_users = set(doc.get("authorized_users", [])) if doc else set()
        if interaction.user.id in authorized_users:
            return True

        raise app_commands.CheckFailure("You are not authorized to manage feed routing in this guild.")

    return app_commands.check(predicate)


async def _sync_commands_once() -> None:
    global _commands_synced
    if _commands_synced:
        return
    try:
        await tree.sync()
        _commands_synced = True
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to sync application commands: {exc}")


async def _resolve_user_display(client: discord.Client, user_id: int) -> str:
    user = client.get_user(user_id)
    if user is None:
        try:
            user = await client.fetch_user(user_id)
        except Exception:
            return f"User {user_id}"
    return getattr(user, "display_name", None) or getattr(user, "name", str(user_id)) or str(user_id)


async def _resolve_channel(client: discord.Client, channel_id: int) -> discord.abc.GuildChannel | None:
    channel = client.get_channel(channel_id)
    if channel:
        return channel
    try:
        return await client.fetch_channel(channel_id)
    except Exception:
        return None


@client.event
async def on_ready() -> None:
    print(f"Feed bot connected as {client.user}")
    await _sync_commands_once()


@client.event
async def on_message(message: discord.Message) -> None:
    await handle_message(
        client=client,
        message=message,
        routes_collection=get_guild_routes_collection(),
        mapping_collection=get_mapping_collection(),
        feed_channel_cache=feed_channel_cache,
        header_state=header_state,
    )


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    await handle_message_edit(
        client=client,
        _before=before,
        after=after,
        mapping_collection=get_mapping_collection(),
        feed_channel_cache=feed_channel_cache,
    )


@client.event
async def on_message_delete(message: discord.Message) -> None:
    await handle_message_delete(
        client=client,
        message=message,
        mapping_collection=get_mapping_collection(),
        feed_channel_cache=feed_channel_cache,
    )


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    await handle_raw_message_delete(
        client=client,
        payload=payload,
        mapping_collection=get_mapping_collection(),
        feed_channel_cache=feed_channel_cache,
    )


@tree.command(name="give-permissions", description="Grant feed management permissions for this guild.")
@admin_only()
@app_commands.describe(user="User to grant permissions to")
async def give_permissions(
    interaction: discord.Interaction, user: app_commands.Transform[discord.User, UserIDTransformer]
) -> None:
    guild = _require_guild(interaction)
    permissions_collection = get_guild_permissions_collection()

    await interaction.response.defer(ephemeral=True)

    existing = await permissions_collection.find_one({"_id": str(guild.id)})
    already_allowed = existing and user.id in existing.get("authorized_users", [])

    now = now_utc()
    await permissions_collection.update_one(
        {"_id": str(guild.id)},
        {
            "$setOnInsert": {"guild_id": guild.id, "created_at": now},
            "$addToSet": {"authorized_users": user.id},
            "$set": {"updated_at": now, "guild_id": guild.id},
        },
        upsert=True,
    )

    if already_allowed:
        await interaction.followup.send(f"{user.mention} already has permissions for this guild.", ephemeral=True)
    else:
        await interaction.followup.send(f"Granted permissions to {user.mention}.", ephemeral=True)


@give_permissions.autocomplete("user")
async def give_permissions_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []

    members = list(guild.members)
    if not members and not getattr(guild, "chunked", False):
        try:
            await guild.chunk(cache=True)
            members = list(guild.members)
        except Exception:
            members = []

    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for member in members:
        if member.bot:
            continue
        label = f"{member.display_name} ({member.id})"
        if current_lower in member.display_name.lower() or current in str(member.id):
            choices.append(app_commands.Choice(name=label[:100], value=str(member.id)))
        if len(choices) >= 25:
            break

    return choices


@tree.command(name="revoke-permissions", description="Revoke feed management permissions for this guild.")
@admin_only()
@app_commands.describe(user="User to revoke permissions from")
async def revoke_permissions(
    interaction: discord.Interaction, user: app_commands.Transform[discord.User, UserIDTransformer]
) -> None:
    guild = _require_guild(interaction)
    permissions_collection = get_guild_permissions_collection()

    await interaction.response.defer(ephemeral=True)

    doc = await permissions_collection.find_one({"_id": str(guild.id)})
    if not doc or user.id not in doc.get("authorized_users", []):
        await interaction.followup.send(f"{user.mention} is not currently authorized.", ephemeral=True)
        return

    now = now_utc()
    await permissions_collection.update_one(
        {"_id": str(guild.id)},
        [
            {
                "$set": {
                    "authorized_users": {
                        "$filter": {
                            "input": {"$ifNull": ["$authorized_users", []]},
                            "as": "uid",
                            "cond": {"$ne": ["$$uid", user.id]},
                        }
                    },
                    "guild_id": {"$ifNull": ["$guild_id", guild.id]},
                    "updated_at": now,
                    "created_at": {"$ifNull": ["$created_at", now]},
                }
            },
        ],
        upsert=True,
    )

    await interaction.followup.send(f"Revoked permissions from {user.mention}.", ephemeral=True)


@revoke_permissions.autocomplete("user")
async def revoke_permissions_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []

    doc = await get_guild_permissions_collection().find_one({"_id": str(guild.id)})
    authorized_users = doc.get("authorized_users", []) if doc else []

    choices: list[app_commands.Choice[str]] = []
    current_lower = current.lower()
    for user_id in authorized_users:
        try:
            name = await _resolve_user_display(client, user_id)
        except Exception:
            name = str(user_id)

        label = f"{name} ({user_id})"
        if current_lower in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=str(user_id)))
        if len(choices) >= 25:
            break

    return choices


@tree.command(name="add-feed-channel", description="Add a feed channel for a guild.")
@admin_or_authorized()
@app_commands.describe(
    channel_id="Channel ID to receive mirrored messages",
    guild_id="Source guild id (defaults to this guild)",
)
async def add_feed_channel(interaction: discord.Interaction, channel_id: str, guild_id: int | None = None) -> None:
    guild = _require_guild(interaction)
    target_guild_id = guild_id or guild.id

    # Permissions are scoped to the current guild; do not allow configuring other guilds from here.
    if target_guild_id != guild.id:
        await interaction.response.send_message(
            "You can only configure feed channels for this guild.", ephemeral=True
        )
        return

    routes_collection = get_guild_routes_collection()

    await interaction.response.defer(ephemeral=True)

    try:
        parsed_channel_id = int(channel_id)
    except ValueError:
        await interaction.followup.send("Channel id must be a number.", ephemeral=True)
        return

    channel = await _resolve_channel(client, parsed_channel_id)
    if channel is None:
        await interaction.followup.send("Channel could not be found or is not accessible by the bot.", ephemeral=True)
        return

    if not hasattr(channel, "send"):
        await interaction.followup.send("That channel type cannot receive messages.", ephemeral=True)
        return

    if getattr(channel, "guild", None) and getattr(channel.guild, "me", None):
        perms = channel.permissions_for(channel.guild.me)  # type: ignore[arg-type]
        can_send = perms.send_messages or getattr(perms, "send_messages_in_threads", False)
        if not (perms.view_channel and can_send):
            await interaction.followup.send("The bot cannot send messages to that channel.", ephemeral=True)
            return

    existing_route = await routes_collection.find_one(
        {"_id": str(guild.id), "feed_channels.channel_id": parsed_channel_id}
    )

    now = now_utc()
    await routes_collection.find_one_and_update(
        {"_id": str(guild.id)},
        [
            {
                "$set": {
                    "feed_channels": {
                        "$let": {
                            "vars": {
                                "existing": {"$ifNull": ["$feed_channels", []]},
                                "channel_ids": {
                                    "$map": {
                                        "input": {"$ifNull": ["$feed_channels", []]},
                                        "as": "fc",
                                        "in": "$$fc.channel_id",
                                    }
                                },
                            },
                            "in": {
                                "$cond": [
                                    {"$in": [parsed_channel_id, "$$channel_ids"]},
                                    "$$existing",
                                    {
                                        "$concatArrays": [
                                            "$$existing",
                                            [
                                                {
                                                    "channel_id": parsed_channel_id,
                                                    "added_by_user_id": interaction.user.id,
                                                    "added_at": now,
                                                }
                                            ],
                                        ]
                                    },
                                ]
                            },
                        }
                    },
                    "updated_at": now,
                    "created_at": {"$ifNull": ["$created_at", now]},
                    "source_guild_id": {"$ifNull": ["$source_guild_id", guild.id]},
                }
            },
        ],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    if existing_route:
        await interaction.followup.send("That feed channel is already configured for this guild.", ephemeral=True)
    else:
        await interaction.followup.send(
            f"Added <#{parsed_channel_id}> as a feed channel for this guild.", ephemeral=True
        )


@tree.command(name="remove-feed-channel", description="Remove a feed channel for this guild.")
@admin_or_authorized()
@app_commands.describe(channel="Configured feed channel to remove")
async def remove_feed_channel(interaction: discord.Interaction, channel: str) -> None:
    guild = _require_guild(interaction)
    routes_collection = get_guild_routes_collection()

    await interaction.response.defer(ephemeral=True)

    try:
        channel_id = int(channel)
    except ValueError:
        await interaction.followup.send("Invalid channel selected.", ephemeral=True)
        return

    before = await routes_collection.find_one({"_id": str(guild.id)})
    before_count = len(before.get("feed_channels", [])) if before else 0

    now = now_utc()
    updated_route = await routes_collection.find_one_and_update(
        {"_id": str(guild.id)},
        [
            {
                "$set": {
                    "feed_channels": {
                        "$filter": {
                            "input": {"$ifNull": ["$feed_channels", []]},
                            "as": "fc",
                            "cond": {"$ne": ["$$fc.channel_id", channel_id]},
                        }
                    },
                    "updated_at": now,
                    "created_at": {"$ifNull": ["$created_at", now]},
                    "source_guild_id": {"$ifNull": ["$source_guild_id", guild.id]},
                }
            },
        ],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    after_count = len(updated_route.get("feed_channels", [])) if updated_route else 0
    if before_count == after_count:
        await interaction.followup.send("That feed channel was not configured for this guild.", ephemeral=True)
    else:
        await interaction.followup.send(f"Removed <#{channel_id}> from this guild's feed list.", ephemeral=True)


@remove_feed_channel.autocomplete("channel")
async def remove_feed_channel_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []

    route = await get_guild_routes_collection().find_one({"_id": str(guild.id)})
    feed_channels = route.get("feed_channels", []) if route else []

    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for entry in feed_channels:
        channel_id = entry.get("channel_id")
        added_by = entry.get("added_by_user_id")
        if channel_id is None:
            continue

        channel = await _resolve_channel(client, channel_id)
        channel_name = f"#{channel.name}" if channel and getattr(channel, 'name', None) else f"Channel {channel_id}"
        guild_name = getattr(getattr(channel, "guild", None), "name", "Unknown Guild") if channel else "Unknown Guild"
        added_by_name = await _resolve_user_display(client, added_by) if added_by else "Unknown"

        label = f"{guild_name} → {channel_name} (added by {added_by_name})"
        if current_lower in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=str(channel_id)))
        if len(choices) >= 25:
            break

    return choices


@tree.command(name="list-authorized-users", description="List users authorized to manage feeds for this guild.")
@admin_or_authorized()
async def list_authorized_users(interaction: discord.Interaction) -> None:
    guild = _require_guild(interaction)
    permissions_collection = get_guild_permissions_collection()

    await interaction.response.defer(ephemeral=True)

    doc = await permissions_collection.find_one({"_id": str(guild.id)})
    user_ids = doc.get("authorized_users", []) if doc else []
    if not user_ids:
        await interaction.followup.send("No authorized users have been configured for this guild.", ephemeral=True)
        return

    lines = []
    for user_id in user_ids:
        name = await _resolve_user_display(client, user_id)
        lines.append(f"- {name} (`{user_id}`)")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.command(name="list-feed-channels", description="List feed channels configured for this guild.")
@admin_or_authorized()
async def list_feed_channels(interaction: discord.Interaction) -> None:
    guild = _require_guild(interaction)
    routes_collection = get_guild_routes_collection()

    await interaction.response.defer(ephemeral=True)

    route = await routes_collection.find_one({"_id": str(guild.id)})
    feed_channels = route.get("feed_channels", []) if route else []
    if not feed_channels:
        await interaction.followup.send("No feed channels configured for this guild.", ephemeral=True)
        return

    lines: list[str] = []
    for entry in feed_channels:
        channel_id = entry.get("channel_id")
        added_by = entry.get("added_by_user_id")
        if channel_id is None:
            continue

        channel = await _resolve_channel(client, channel_id)
        channel_label = channel.mention if channel else f"Channel {channel_id}"
        guild_name = ""
        if channel and channel.guild and channel.guild.id != guild.id:
            guild_name = f" ({channel.guild.name})"

        added_by_label = await _resolve_user_display(client, added_by) if added_by else "Unknown"
        lines.append(f"- {channel_label}{guild_name} — added by {added_by_label}")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    message = "Something went wrong while running this command."
    if isinstance(error, app_commands.CheckFailure):
        message = str(error)
    else:
        print(f"Unhandled command error: {error}")

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def main() -> None:
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
