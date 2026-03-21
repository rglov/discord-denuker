"""
restore.py — Recreates a Discord server from a Denuker backup.
"""

import discord
import asyncio
from typing import Optional

# Seconds to sleep between most create calls to reduce hitting rate limits.
# discord.py handles 429s automatically, but sleeping proactively is friendlier.
_DELAY = 0.5
_MSG_DELAY = 0.8  # webhook posts have a tighter sub-bucket


async def dry_run(backup: dict, callback) -> None:
    """
    Simulate a restore without touching Discord at all.
    Reads only the backup file and reports what *would* happen.
    """
    callback("=== DRY RUN — nothing will be changed on Discord ===\n")

    meta = backup.get("meta", {})
    callback(f"Backup info:")
    callback(f"  Server : {meta.get('server_name', 'Unknown')}")
    callback(f"  Date   : {str(meta.get('backup_date', ''))[:19]}")
    callback(f"  Members: {meta.get('member_count', '?')}")

    roles = [r for r in backup.get("roles", []) if not r.get("managed")]
    callback(f"\n[1/5] Roles to create: {len(roles)}")
    for r in sorted(roles, key=lambda x: x["position"]):
        hoist = " (hoisted)" if r.get("hoist") else ""
        callback(f"  + {r['name']}{hoist}")

    cats = backup.get("categories", [])
    callback(f"\n[2/5] Categories to create: {len(cats)}")
    for c in sorted(cats, key=lambda x: x["position"]):
        callback(f"  + {c['name']}")

    channels = backup.get("channels", [])
    callback(f"\n[3/5] Channels to create: {len(channels)}")
    total_msgs = 0
    for ch in sorted(channels, key=lambda x: x["position"]):
        msgs = len(ch.get("messages", []))
        total_msgs += msgs
        cat = ch.get("category_id") or "no category"
        callback(f"  + #{ch['name']} ({ch['type']})  [{msgs} msgs]  → {cat}")

    callback(f"\n[4/5] Messages to restore: {total_msgs} total")

    members = backup.get("members", [])
    callback(f"\n[5/5] Members in backup: {len(members)}")
    callback(f"  (An invite link will be generated — members must click it to rejoin)")

    callback("\n=== Dry run complete — no changes were made ===")
    callback("If everything looks right, uncheck 'Dry Run' and click RESTORE SERVER.")


async def restore_server(
    token: str,
    guild_id: int,
    backup: dict,
    restore_messages: bool,
    callback,
) -> None:
    """
    Restore a server from a backup dict.

    NOTE: This *adds* channels and roles — it does not delete existing ones.
    Run on a freshly nuked (empty) server for best results.
    """
    intents = discord.Intents.default()
    intents.members = True

    client = discord.Client(intents=intents)
    result: dict = {"error": None, "invite_url": None}

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(guild_id)
            if not guild:
                result["error"] = (
                    "Bot is not in this server.\n"
                    "Invite the bot with Administrator permission first."
                )
                return

            callback(f"Connected — restoring to: {guild.name}")

            # ── Step 1: Roles ─────────────────────────────────────────────
            callback("\n[1/5] Recreating roles...")
            # Maps old_role_id (str) → new discord.Role
            role_map: dict[str, discord.Role] = {
                str(guild.id): guild.default_role  # @everyone
            }

            # Sort ascending by position so hierarchy is correct
            creatable = [r for r in backup.get("roles", [])
                         if not r.get("managed")]
            created_roles: list[tuple[int, discord.Role]] = []

            for role_data in sorted(creatable, key=lambda r: r["position"]):
                try:
                    new_role = await guild.create_role(
                        name=role_data["name"],
                        color=discord.Color(role_data["color"]),
                        permissions=discord.Permissions(
                            int(role_data["permissions"])
                        ),
                        hoist=role_data["hoist"],
                        mentionable=role_data["mentionable"],
                        reason="Denuker restore",
                    )
                    role_map[role_data["id"]] = new_role
                    created_roles.append((role_data["position"], new_role))
                    callback(f"  + Role: {role_data['name']}")
                    await asyncio.sleep(_DELAY)
                except discord.Forbidden:
                    callback(f"  ⚠ Cannot create role '{role_data['name']}' — no permission")
                except Exception as exc:
                    callback(f"  ⚠ Error creating role '{role_data['name']}': {exc}")

            # Reorder roles to match backup positions
            if created_roles:
                try:
                    positions = {role: pos for pos, role in created_roles}
                    await guild.edit_role_positions(positions, reason="Denuker restore")
                except Exception as exc:
                    callback(f"  ⚠ Could not set role order: {exc}")

            callback(f"  → {len(role_map) - 1} roles created")

            # ── Step 2: Categories ────────────────────────────────────────
            callback("\n[2/5] Recreating categories...")
            # Maps old_category_id (str) → new discord.CategoryChannel
            cat_map: dict[str, discord.CategoryChannel] = {}

            for cat_data in sorted(
                backup.get("categories", []), key=lambda c: c["position"]
            ):
                try:
                    overwrites = _build_overwrites(
                        cat_data["overwrites"], guild, role_map
                    )
                    new_cat = await guild.create_category(
                        name=cat_data["name"],
                        overwrites=overwrites,
                        reason="Denuker restore",
                    )
                    cat_map[cat_data["id"]] = new_cat
                    callback(f"  + Category: {cat_data['name']}")
                    await asyncio.sleep(_DELAY)
                except Exception as exc:
                    callback(
                        f"  ⚠ Error creating category '{cat_data['name']}': {exc}"
                    )

            callback(f"  → {len(cat_map)} categories created")

            # ── Step 3: Channels ──────────────────────────────────────────
            callback("\n[3/5] Recreating channels...")
            # Maps old_channel_id (str) → new channel
            ch_map: dict[str, discord.abc.GuildChannel] = {}

            for ch_data in sorted(
                backup.get("channels", []), key=lambda c: c["position"]
            ):
                try:
                    category = cat_map.get(ch_data.get("category_id") or "")
                    overwrites = _build_overwrites(
                        ch_data["overwrites"], guild, role_map
                    )
                    new_ch = await _create_channel(
                        guild, ch_data, category, overwrites
                    )
                    if new_ch:
                        ch_map[ch_data["id"]] = new_ch
                        callback(f"  + #{ch_data['name']} ({ch_data['type']})")
                    await asyncio.sleep(_DELAY)
                except discord.Forbidden:
                    callback(
                        f"  ⚠ No permission to create #{ch_data['name']}"
                    )
                except Exception as exc:
                    callback(f"  ⚠ Error creating #{ch_data['name']}: {exc}")

            callback(f"  → {len(ch_map)} channels created")

            # ── Step 4: Messages ──────────────────────────────────────────
            if restore_messages:
                callback("\n[4/5] Restoring messages via webhooks...")
                for ch_data in backup.get("channels", []):
                    messages = ch_data.get("messages", [])
                    if not messages:
                        continue
                    new_ch = ch_map.get(ch_data["id"])
                    if not isinstance(new_ch, discord.TextChannel):
                        continue

                    callback(
                        f"  Restoring #{ch_data['name']} "
                        f"({len(messages)} messages)..."
                    )
                    webhook = None
                    try:
                        webhook = await new_ch.create_webhook(
                            name="Denuker", reason="Denuker message restore"
                        )
                        posted = 0
                        for msg in messages:
                            content = msg.get("content") or ""

                            # Append attachment links since we can't re-upload files
                            for att in msg.get("attachments", []):
                                line = f"📎 [{att['filename']}]({att['url']})"
                                content = (
                                    (content + "\n" + line).strip()
                                    if content else line
                                )

                            # Build embed objects (skip malformed ones)
                            embeds = []
                            for e_dict in msg.get("embeds", [])[:10]:
                                try:
                                    embeds.append(discord.Embed.from_dict(e_dict))
                                except Exception:
                                    pass

                            if not content and not embeds:
                                continue

                            # Truncate to Discord limits
                            if len(content) > 2000:
                                content = content[:1990] + "… [truncated]"

                            try:
                                await webhook.send(
                                    content=content or None,
                                    username=msg["author_name"][:80],
                                    avatar_url=msg.get("author_avatar"),
                                    embeds=embeds,
                                    allowed_mentions=discord.AllowedMentions.none(),
                                )
                                posted += 1
                                await asyncio.sleep(_MSG_DELAY)
                            except discord.HTTPException as exc:
                                callback(f"    ⚠ Message skipped: {exc}")
                                await asyncio.sleep(2)

                        callback(f"    → {posted} messages restored")

                    except discord.Forbidden:
                        callback(
                            f"  ⚠ Can't create webhook in #{ch_data['name']}"
                        )
                    except Exception as exc:
                        callback(f"  ⚠ Webhook error in #{ch_data['name']}: {exc}")
                    finally:
                        if webhook:
                            try:
                                await webhook.delete()
                            except Exception:
                                pass
            else:
                callback("\n[4/5] Skipping message restore (not selected)")

            # ── Step 5: Invite link ───────────────────────────────────────
            callback("\n[5/5] Generating invite link for your members...")
            for ch in guild.text_channels:
                try:
                    invite = await ch.create_invite(
                        max_age=0,
                        max_uses=0,
                        reason="Denuker member invite",
                    )
                    result["invite_url"] = invite.url
                    callback(f"  Invite link: {invite.url}")
                    break
                except Exception:
                    continue

            if not result["invite_url"]:
                callback("  ⚠ Could not generate invite link — create one manually")

            callback("\n✅ Restore complete!")

        except Exception as exc:
            result["error"] = str(exc)
            callback(f"❌ Fatal restore error: {exc}")
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

    return result.get("invite_url")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_overwrites(
    overwrites_data: list,
    guild: discord.Guild,
    role_map: dict,
) -> dict:
    """Convert saved overwrite dicts back to discord.py PermissionOverwrite objects."""
    overwrites = {}
    for ow in overwrites_data:
        target = None
        if ow["type"] == "role":
            target = role_map.get(ow["id"])
            # Fallback: @everyone by guild ID
            if target is None and ow["id"] == str(guild.id):
                target = guild.default_role
        elif ow["type"] == "member":
            target = guild.get_member(int(ow["id"]))

        if target:
            perm = discord.PermissionOverwrite.from_pair(
                discord.Permissions(int(ow["allow"])),
                discord.Permissions(int(ow["deny"])),
            )
            overwrites[target] = perm
    return overwrites


async def _create_channel(
    guild: discord.Guild,
    ch_data: dict,
    category: Optional[discord.CategoryChannel],
    overwrites: dict,
) -> Optional[discord.abc.GuildChannel]:
    """Create the right type of channel based on saved type string."""
    ch_type = ch_data["type"]
    name = ch_data["name"]
    pos = ch_data["position"]
    topic = ch_data.get("topic", "")

    if "text" in ch_type or "announcement" in ch_type or "news" in ch_type:
        return await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            topic=topic[:1024] if topic else None,
            nsfw=ch_data.get("nsfw", False),
            slowmode_delay=ch_data.get("slowmode", 0),
            reason="Denuker restore",
        )
    elif "voice" in ch_type:
        return await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            bitrate=min(ch_data.get("bitrate", 64000), 96000),
            user_limit=ch_data.get("user_limit", 0),
            reason="Denuker restore",
        )
    elif "stage" in ch_type:
        return await guild.create_stage_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason="Denuker restore",
        )
    elif "forum" in ch_type:
        try:
            return await guild.create_forum(
                name=name,
                category=category,
                overwrites=overwrites,
                topic=topic[:1024] if topic else None,
                reason="Denuker restore",
            )
        except AttributeError:
            # Older discord.py — fall back to text channel
            return await guild.create_text_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                reason="Denuker restore (forum→text fallback)",
            )
    else:
        return None  # Unknown type — skip silently
