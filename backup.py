"""
backup.py — Creates a full JSON backup of a Discord server.
"""

import discord
import asyncio
from datetime import datetime, timezone


async def create_backup(
    token: str,
    guild_id: int,
    msg_limit: int,
    callback,
) -> dict:
    """
    Backup a Discord server to a dict.

    Args:
        token:      Bot token
        guild_id:   Server (guild) ID to back up
        msg_limit:  Max messages per channel. 0 = no messages, -1 = all.
        callback:   fn(str) called with progress messages (thread-safe via queue)

    Returns:
        Backup dict ready to be JSON-serialized.
    """
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True

    client = discord.Client(intents=intents)
    result: dict = {"data": None, "error": None}

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(guild_id)
            if not guild:
                result["error"] = (
                    "Bot is not in this server, or the server ID is wrong.\n"
                    "Make sure you've invited the bot with Administrator permission."
                )
                return

            callback(f"Connected — backing up: {guild.name}")
            data: dict = {}

            # ── Meta ─────────────────────────────────────────────────────────
            data["meta"] = {
                "schema_version": 1,
                "server_id": str(guild.id),
                "server_name": guild.name,
                "server_description": guild.description or "",
                "icon_url": str(guild.icon.url) if guild.icon else None,
                "backup_date": datetime.now(timezone.utc).isoformat(),
                "member_count": guild.member_count,
            }

            # ── Roles ─────────────────────────────────────────────────────────
            callback("  Saving roles...")
            data["roles"] = []
            for role in sorted(guild.roles, key=lambda r: r.position):
                if role.is_default():
                    continue
                data["roles"].append({
                    "id": str(role.id),
                    "name": role.name,
                    "color": role.color.value,
                    "permissions": str(role.permissions.value),
                    "position": role.position,
                    "hoist": role.hoist,
                    "mentionable": role.mentionable,
                    "managed": role.managed,  # bot/integration roles can't be created
                })
            callback(f"    → {len(data['roles'])} roles saved")

            # ── Categories ───────────────────────────────────────────────────
            callback("  Saving categories...")
            data["categories"] = []
            for cat in sorted(guild.categories, key=lambda c: c.position):
                data["categories"].append({
                    "id": str(cat.id),
                    "name": cat.name,
                    "position": cat.position,
                    "overwrites": _serialize_overwrites(cat.overwrites),
                })
            callback(f"    → {len(data['categories'])} categories saved")

            # ── Channels ─────────────────────────────────────────────────────
            callback("  Saving channels...")
            data["channels"] = []

            # Separate text channels (can have messages) from others
            non_category = [c for c in guild.channels
                            if not isinstance(c, discord.CategoryChannel)]

            for ch in sorted(non_category, key=lambda c: c.position):
                ch_data = _serialize_channel(ch)

                # Fetch messages for text-like channels
                if isinstance(ch, discord.TextChannel) and msg_limit != 0:
                    limit = None if msg_limit == -1 else msg_limit
                    callback(f"    Saving #{ch.name} messages...")
                    messages = []
                    try:
                        async for msg in ch.history(limit=limit, oldest_first=True):
                            messages.append(_serialize_message(msg))
                    except discord.Forbidden:
                        callback(f"    ⚠ No read permission in #{ch.name} — skipped")
                    except Exception as exc:
                        callback(f"    ⚠ Error in #{ch.name}: {exc}")
                    ch_data["messages"] = messages
                    callback(f"      → {len(messages)} messages")

                data["channels"].append(ch_data)

            callback(f"    → {len(data['channels'])} channels saved")

            # ── Members ──────────────────────────────────────────────────────
            callback("  Saving members (may take a moment for large servers)...")
            data["members"] = []
            try:
                async for member in guild.fetch_members(limit=None):
                    data["members"].append({
                        "id": str(member.id),
                        "username": str(member),
                        "display_name": member.display_name,
                        "bot": member.bot,
                        "roles": [str(r.id) for r in member.roles
                                  if not r.is_default()],
                        "avatar_url": (str(member.display_avatar.url)
                                       if member.display_avatar else None),
                    })
            except discord.Forbidden:
                callback("    ⚠ Missing 'Server Members Intent' — member list skipped")

            callback(f"    → {len(data['members'])} members saved")

            result["data"] = data
            callback("✅ Backup complete!")

        except Exception as exc:
            result["error"] = str(exc)
            callback(f"❌ Error during backup: {exc}")
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        result["error"] = (
            "Invalid bot token.\n"
            "Copy it again from the Discord Developer Portal."
        )

    if result["error"]:
        raise RuntimeError(result["error"])

    return result["data"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_overwrites(overwrites: dict) -> list:
    out = []
    for target, perms in overwrites.items():
        allow, deny = perms.pair()
        out.append({
            "id": str(target.id),
            "type": "role" if isinstance(target, discord.Role) else "member",
            "allow": str(allow.value),
            "deny": str(deny.value),
        })
    return out


def _serialize_channel(ch) -> dict:
    base = {
        "id": str(ch.id),
        "name": ch.name,
        "type": str(ch.type),
        "position": ch.position,
        "category_id": str(ch.category_id) if ch.category_id else None,
        "overwrites": _serialize_overwrites(ch.overwrites),
        "messages": [],
    }
    if isinstance(ch, discord.TextChannel):
        base.update({
            "topic": ch.topic or "",
            "nsfw": ch.nsfw,
            "slowmode": ch.slowmode_delay,
        })
    elif isinstance(ch, discord.VoiceChannel):
        base.update({
            "bitrate": ch.bitrate,
            "user_limit": ch.user_limit,
        })
    elif isinstance(ch, discord.ForumChannel):
        base.update({"topic": ch.topic or ""})
    return base


def _serialize_message(msg: discord.Message) -> dict:
    return {
        "id": str(msg.id),
        "content": msg.content,
        "author_id": str(msg.author.id),
        "author_name": msg.author.display_name,
        "author_avatar": (str(msg.author.display_avatar.url)
                          if msg.author.display_avatar else None),
        "timestamp": msg.created_at.isoformat(),
        "attachments": [
            {"filename": a.filename, "url": a.url}
            for a in msg.attachments
        ],
        "embeds": [e.to_dict() for e in msg.embeds],
        "pinned": msg.pinned,
    }
