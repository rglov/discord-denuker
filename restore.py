"""
restore.py — Recreates a Discord server from a Denuker backup.
"""

import discord
import asyncio
import aiohttp
import time
from typing import Optional

import oauth_server as oa

_DELAY     = 0.5   # seconds between most create calls
_MSG_DELAY = 0.8   # webhook posts have a tighter bucket
_REJOIN_DELAY = 1.0  # PUT /members rate limit ~10/10s per guild


# ── Dry run (no network calls) ────────────────────────────────────────────────

async def dry_run(backup: dict, callback) -> None:
    """Preview what a restore would do — touches nothing on Discord."""
    callback("=== DRY RUN — nothing will be changed on Discord ===\n")

    meta = backup.get("meta", {})
    callback("Backup info:")
    callback(f"  Server : {meta.get('server_name', 'Unknown')}")
    callback(f"  Date   : {str(meta.get('backup_date', ''))[:19]}")
    callback(f"  Members: {meta.get('member_count', '?')}")

    callback("\n[0/6] Wipe existing channels first: YES")
    callback("  All current channels and categories will be deleted before restore.")

    roles = [r for r in backup.get("roles", []) if not r.get("managed")]
    callback(f"\n[1/6] Roles to create: {len(roles)}")
    for r in sorted(roles, key=lambda x: x["position"]):
        callback(f"  + {r['name']}" + (" (hoisted)" if r.get("hoist") else ""))

    cats = backup.get("categories", [])
    callback(f"\n[2/6] Categories to create: {len(cats)}")
    for c in sorted(cats, key=lambda x: x["position"]):
        callback(f"  + {c['name']}")

    channels = backup.get("channels", [])
    callback(f"\n[3/6] Channels to create: {len(channels)}")
    total_msgs = 0
    for ch in sorted(channels, key=lambda x: x["position"]):
        msgs = len(ch.get("messages", []))
        total_msgs += msgs
        callback(f"  + #{ch['name']} ({ch['type']})  [{msgs} msgs]")

    callback(f"\n[4/6] Messages to restore: {total_msgs} total")

    members = backup.get("members", [])
    registered = len(oa.load_tokens())
    callback(f"\n[5/6] Members in backup: {len(members)}")
    callback(f"  Members with saved tokens (auto-rejoin): {registered}")
    callback(f"  Members without tokens (need invite link): {len(members) - registered}")

    callback("\n[6/6] Invite link will be generated for any remaining members.")

    callback("\n=== Dry run complete — no changes were made ===")
    callback("Uncheck 'Dry Run' and click RESTORE SERVER when ready.")


# ── Full restore ──────────────────────────────────────────────────────────────

async def restore_server(
    token: str,
    guild_id: int,
    backup: dict,
    restore_messages: bool,
    rejoin_members: bool,
    client_id: str,
    client_secret: str,
    callback,
) -> None:
    """
    Restore a server from a backup dict.

    Steps:
      0. Delete all existing channels and categories (clean slate)
      1. Recreate roles
      2. Recreate categories
      3. Recreate channels
      4. Restore messages (optional)
      5. Re-add registered members with their roles (optional)
      6. Generate invite link for anyone not auto-rejoined
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

            callback(f"Connected — restoring: {guild.name}")

            # ── Step 0: Wipe existing channels & categories ───────────────
            callback("\n[0/6] Clearing existing channels and categories...")
            deleted = 0
            # Delete non-category channels first, then categories
            for ch in list(guild.channels):
                if isinstance(ch, discord.CategoryChannel):
                    continue
                try:
                    await ch.delete(reason="Denuker: clearing before restore")
                    deleted += 1
                    await asyncio.sleep(_DELAY)
                except discord.Forbidden:
                    callback(f"  ⚠ Cannot delete #{ch.name} — no permission")
                except Exception as exc:
                    callback(f"  ⚠ Error deleting #{ch.name}: {exc}")

            for ch in list(guild.channels):
                if not isinstance(ch, discord.CategoryChannel):
                    continue
                try:
                    await ch.delete(reason="Denuker: clearing before restore")
                    deleted += 1
                    await asyncio.sleep(_DELAY)
                except discord.Forbidden:
                    callback(f"  ⚠ Cannot delete category '{ch.name}' — no permission")
                except Exception as exc:
                    callback(f"  ⚠ Error deleting category '{ch.name}': {exc}")

            callback(f"  → {deleted} channels/categories cleared")

            # ── Step 1: Roles ─────────────────────────────────────────────
            callback("\n[1/6] Recreating roles...")
            role_map: dict[str, discord.Role] = {
                str(guild.id): guild.default_role
            }
            creatable = [r for r in backup.get("roles", []) if not r.get("managed")]
            created_roles: list[tuple[int, discord.Role]] = []

            for role_data in sorted(creatable, key=lambda r: r["position"]):
                try:
                    new_role = await guild.create_role(
                        name=role_data["name"],
                        color=discord.Color(role_data["color"]),
                        permissions=discord.Permissions(int(role_data["permissions"])),
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

            if created_roles:
                try:
                    await guild.edit_role_positions(
                        {role: pos for pos, role in created_roles},
                        reason="Denuker restore",
                    )
                except Exception as exc:
                    callback(f"  ⚠ Could not set role order: {exc}")

            callback(f"  → {len(role_map) - 1} roles created")

            # ── Step 2: Categories ────────────────────────────────────────
            callback("\n[2/6] Recreating categories...")
            cat_map: dict[str, discord.CategoryChannel] = {}

            for cat_data in sorted(backup.get("categories", []), key=lambda c: c["position"]):
                try:
                    new_cat = await guild.create_category(
                        name=cat_data["name"],
                        overwrites=_build_overwrites(cat_data["overwrites"], guild, role_map),
                        reason="Denuker restore",
                    )
                    cat_map[cat_data["id"]] = new_cat
                    callback(f"  + Category: {cat_data['name']}")
                    await asyncio.sleep(_DELAY)
                except Exception as exc:
                    callback(f"  ⚠ Error creating category '{cat_data['name']}': {exc}")

            callback(f"  → {len(cat_map)} categories created")

            # ── Step 3: Channels ──────────────────────────────────────────
            callback("\n[3/6] Recreating channels...")
            ch_map: dict[str, discord.abc.GuildChannel] = {}

            for ch_data in sorted(backup.get("channels", []), key=lambda c: c["position"]):
                try:
                    category = cat_map.get(ch_data.get("category_id") or "")
                    new_ch = await _create_channel(
                        guild, ch_data, category,
                        _build_overwrites(ch_data["overwrites"], guild, role_map),
                    )
                    if new_ch:
                        ch_map[ch_data["id"]] = new_ch
                        callback(f"  + #{ch_data['name']} ({ch_data['type']})")
                    await asyncio.sleep(_DELAY)
                except discord.Forbidden:
                    callback(f"  ⚠ No permission to create #{ch_data['name']}")
                except Exception as exc:
                    callback(f"  ⚠ Error creating #{ch_data['name']}: {exc}")

            callback(f"  → {len(ch_map)} channels created")

            # ── Step 4: Messages ──────────────────────────────────────────
            if restore_messages:
                callback("\n[4/6] Restoring messages via webhooks...")
                for ch_data in backup.get("channels", []):
                    messages = ch_data.get("messages", [])
                    if not messages:
                        continue
                    new_ch = ch_map.get(ch_data["id"])
                    if not isinstance(new_ch, discord.TextChannel):
                        continue

                    callback(f"  Restoring #{ch_data['name']} ({len(messages)} messages)...")
                    webhook = None
                    try:
                        webhook = await new_ch.create_webhook(
                            name="Denuker", reason="Denuker message restore"
                        )
                        posted = 0
                        for msg in messages:
                            content = msg.get("content") or ""
                            for att in msg.get("attachments", []):
                                line = f"📎 [{att['filename']}]({att['url']})"
                                content = (content + "\n" + line).strip() if content else line
                            embeds = []
                            for e_dict in msg.get("embeds", [])[:10]:
                                try:
                                    embeds.append(discord.Embed.from_dict(e_dict))
                                except Exception:
                                    pass
                            if not content and not embeds:
                                continue
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
                        callback(f"  ⚠ Can't create webhook in #{ch_data['name']}")
                    except Exception as exc:
                        callback(f"  ⚠ Webhook error in #{ch_data['name']}: {exc}")
                    finally:
                        if webhook:
                            try:
                                await webhook.delete()
                            except Exception:
                                pass
            else:
                callback("\n[4/6] Skipping message restore (not selected)")

            # ── Step 5: Re-add members ────────────────────────────────────
            if rejoin_members:
                callback("\n[5/6] Re-adding registered members...")
                await _rejoin_members(
                    token, guild_id, guild,
                    backup.get("members", []),
                    role_map, client_id, client_secret, callback,
                )
            else:
                callback("\n[5/6] Skipping member re-add (not selected)")

            # ── Step 6: Invite link ───────────────────────────────────────
            callback("\n[6/6] Generating invite link for remaining members...")
            for ch in guild.text_channels:
                try:
                    invite = await ch.create_invite(
                        max_age=0, max_uses=0, reason="Denuker member invite"
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


# ── Member rejoin ─────────────────────────────────────────────────────────────

async def _rejoin_members(
    bot_token: str,
    guild_id: int,
    guild: discord.Guild,
    backup_members: list,
    role_map: dict,
    client_id: str,
    client_secret: str,
    callback,
) -> None:
    """Use stored OAuth2 tokens to re-add each registered member with their roles."""
    tokens = oa.load_tokens()
    if not tokens:
        callback("  No registered members found. Have members use the registration link first.")
        return

    callback(f"  {len(tokens)} registered members found")
    added = skipped = failed = 0

    async with aiohttp.ClientSession() as session:
        for member in backup_members:
            user_id   = member["id"]
            username  = member.get("username", user_id)
            token_data = tokens.get(user_id)

            if not token_data:
                skipped += 1
                continue

            # Refresh token if it expires within 24 h
            if token_data.get("expires_at", 0) - time.time() < 86_400:
                callback(f"  Refreshing token for {username}...")
                refreshed = await asyncio.to_thread(
                    oa.refresh_token_sync, token_data, client_id, client_secret
                )
                if refreshed:
                    tokens[user_id] = refreshed
                    token_data = refreshed
                    oa.save_tokens(tokens)
                else:
                    callback(f"  ⚠ Could not refresh token for {username} — skipped")
                    failed += 1
                    continue

            # Map old role IDs → new role IDs
            new_role_ids = [
                str(role_map[r].id)
                for r in member.get("roles", [])
                if r in role_map
            ]

            # PUT /guilds/{guild_id}/members/{user_id}
            try:
                async with session.put(
                    f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}",
                    headers={
                        "Authorization":  f"Bot {bot_token}",
                        "Content-Type":   "application/json",
                    },
                    json={
                        "access_token": token_data["access_token"],
                        "roles":        new_role_ids,
                    },
                ) as resp:
                    if resp.status in (201, 204):
                        verb = "rejoined" if resp.status == 201 else "already here"
                        callback(f"  ✅ {username} — {verb} ({len(new_role_ids)} roles)")
                        added += 1
                    elif resp.status == 403:
                        callback(f"  ⚠ {username} — token expired or scope revoked")
                        failed += 1
                    else:
                        text = await resp.text()
                        callback(f"  ⚠ {username} — HTTP {resp.status}: {text[:120]}")
                        failed += 1
            except Exception as exc:
                callback(f"  ⚠ {username} — error: {exc}")
                failed += 1

            await asyncio.sleep(_REJOIN_DELAY)

    callback(f"  → {added} re-added, {skipped} not registered, {failed} failed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_overwrites(overwrites_data: list, guild: discord.Guild, role_map: dict) -> dict:
    overwrites = {}
    for ow in overwrites_data:
        target = None
        if ow["type"] == "role":
            target = role_map.get(ow["id"])
            if target is None and ow["id"] == str(guild.id):
                target = guild.default_role
        elif ow["type"] == "member":
            target = guild.get_member(int(ow["id"]))
        if target:
            overwrites[target] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(int(ow["allow"])),
                discord.Permissions(int(ow["deny"])),
            )
    return overwrites


async def _create_channel(
    guild: discord.Guild,
    ch_data: dict,
    category: Optional[discord.CategoryChannel],
    overwrites: dict,
) -> Optional[discord.abc.GuildChannel]:
    ch_type = ch_data["type"]
    name    = ch_data["name"]
    topic   = ch_data.get("topic", "")

    if "text" in ch_type or "announcement" in ch_type or "news" in ch_type:
        return await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            topic=topic[:1024] if topic else None,
            nsfw=ch_data.get("nsfw", False),
            slowmode_delay=ch_data.get("slowmode", 0),
            reason="Denuker restore",
        )
    elif "voice" in ch_type:
        return await guild.create_voice_channel(
            name=name, category=category, overwrites=overwrites,
            bitrate=min(ch_data.get("bitrate", 64000), 96000),
            user_limit=ch_data.get("user_limit", 0),
            reason="Denuker restore",
        )
    elif "stage" in ch_type:
        return await guild.create_stage_channel(
            name=name, category=category, overwrites=overwrites,
            reason="Denuker restore",
        )
    elif "forum" in ch_type:
        try:
            return await guild.create_forum(
                name=name, category=category, overwrites=overwrites,
                topic=topic[:1024] if topic else None,
                reason="Denuker restore",
            )
        except AttributeError:
            return await guild.create_text_channel(
                name=name, category=category, overwrites=overwrites,
                reason="Denuker restore (forum→text fallback)",
            )
    return None
