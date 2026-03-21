# 🛡 Denuker — Discord Server Backup & Recovery

Denuker lets you back up your entire Discord server and restore it if someone nukes it (deletes all channels, roles, etc.).

---

## What It Backs Up

- ✅ All roles (names, colors, permissions, hierarchy)
- ✅ All categories and channels (text, voice, stage, forum)
- ✅ Channel permissions
- ✅ Messages (configurable limit per channel)
- ✅ Member list with their roles

## What It Restores

- ✅ Roles (recreated with correct hierarchy)
- ✅ Categories and channels (with permissions)
- ✅ Messages (via webhook — original author names and avatars are preserved)
- ✅ Invite link for members to rejoin
- ❌ Members **cannot** be force-rejoined — Discord doesn't allow it. The app generates a permanent invite link that you share with your community so they can click to rejoin.

---

## Requirements

- **Python 3.10 or newer** — download from [python.org](https://www.python.org/downloads/)
  - Windows: check **"Add Python to PATH"** during installation
  - Mac: the python.org installer includes Tkinter (required for the GUI)

---

## Installation (one time only)

### Windows
1. Double-click **`install.bat`**
2. Wait for it to finish
3. Done!

### Mac / Linux
1. Open Terminal in the `denuker` folder
2. Run: `./install.sh`
3. Done!

---

## Running the App

### Windows
Double-click **`run.bat`**

### Mac / Linux
Double-click **`run.sh`** or run `python3 denuker.py` in Terminal

---

## First-Time Setup (inside the app)

Click **"? Help"** in the app for a full guide. Here's the summary:

### 1. Create a Discord Bot
1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **"New Application"** → give it a name (e.g. "Denuker")
3. Click **"Bot"** in the left menu → **"Add Bot"**
4. Click **"Reset Token"** → copy the token
5. Paste it into the **Bot Token** field in the app

### 2. Enable Privileged Intents
On the Bot page, scroll down to **"Privileged Gateway Intents"** and enable:
- **Server Members Intent**
- **Message Content Intent**

### 3. Invite the Bot to Your Server
1. Go to **OAuth2 → URL Generator**
2. Check **"bot"** under Scopes
3. Check **"Administrator"** under Bot Permissions
4. Copy the URL → open in browser → select your server → Authorize

---

## Creating a Backup

1. Connect your bot token and select your server
2. Choose how many messages to save per channel (500 is a good default)
3. Click **"CREATE BACKUP"**
4. A `.json` file is saved to your Desktop

**Store your backup somewhere safe** — email it to yourself, save to Google Drive/iCloud, or keep on a USB drive. Create a new backup whenever your server changes significantly.

---

## Restoring After a Nuke

1. Open Denuker and connect your bot token
2. Select the nuked server from the dropdown
3. Click **"Open Backup File"** → select your `.json` backup
4. *(Optional)* Check **"Dry Run"** first to preview what will be restored without changing anything
5. Click **"RESTORE SERVER"**
6. Copy the **invite link** printed in the Activity Log and share it with your members

> ⚠️ **About Members:**
> Discord does not allow bots to force users to rejoin a server. The app generates a permanent invite link — share it in your community's DMs, other social media, or wherever your members can see it. Once they click it they're back, and their roles will be waiting for them.

---

## Auto-Backup Schedule

### While the App is Open
Use the **⏰ AUTO-BACKUP SCHEDULE** section at the bottom of the app:
- Enable the checkbox
- Choose an interval (e.g. every 24 hours)
- The app will back up automatically while it's running

### Without the App Open (Recommended)
Click **"Set Up Background Schedule"** for step-by-step instructions to set up:
- **Mac/Linux**: a cron job that runs even when the computer is idle
- **Windows**: a Task Scheduler entry that runs on a schedule automatically

### Headless Mode (Advanced)
You can also run a backup from the command line, no GUI:

```bash
python3 denuker.py --headless
python3 denuker.py --headless --guild-id 123456789012345678 --msg-limit 500
python3 denuker.py --headless --help
```

---

## Dry Run / Test Mode

Before doing a real restore, you can preview exactly what will happen:

1. Load a backup file
2. Check the **"Dry Run"** checkbox in the Restore panel
3. Click **"RESTORE SERVER"**

The Activity Log will show every role, category, and channel that *would* be created — without actually touching your server. This is useful for verifying the backup is complete before you actually need it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Invalid token" | Reset the token in the Developer Portal and paste it again |
| Bot not showing servers | Make sure the bot is invited to the server (Step 3 above) |
| "Missing permissions" | Re-invite the bot with Administrator permission |
| Member list not saved | Enable "Server Members Intent" in the Developer Portal |
| Messages not backed up | Enable "Message Content Intent" in the Developer Portal |
| Tkinter not found (Mac) | Use the python.org installer, not Homebrew Python |

---

## File Structure

```
denuker/
├── denuker.py       — the app (run this)
├── backup.py        — backup logic
├── restore.py       — restore logic
├── requirements.txt — dependencies
├── install.sh       — Mac/Linux installer
├── install.bat      — Windows installer
├── run.sh           — Mac/Linux launcher
└── run.bat          — Windows launcher
```

Backups are saved as `.json` files on your Desktop, named like:
`denuker_MyServer_2026-03-21_09-00.json`
