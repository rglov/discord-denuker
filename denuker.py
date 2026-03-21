"""
denuker.py — Discord Server Backup & Recovery Tool

Usage:
  python3 denuker.py                          # open the GUI
  python3 denuker.py --headless               # silent backup (for cron / Task Scheduler)
  python3 denuker.py --headless --guild-id ID --msg-limit 500
"""

import sys
import os
import json
import asyncio
import threading
import queue
import platform
import argparse
from datetime import datetime, timedelta

import backup as bk
import restore as rs


# ── Color theme (Discord-inspired dark) ──────────────────────────────────────
BG      = "#2C2F33"
BG2     = "#23272A"
BG3     = "#36393F"
ACCENT  = "#7289DA"
GREEN   = "#43B581"
RED     = "#F04747"
YELLOW  = "#FAA61A"
TEXT    = "#FFFFFF"
TEXT2   = "#B9BBBE"
TEXT3   = "#72767D"

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".denuker_config.json")


# ── Headless mode (no GUI) ────────────────────────────────────────────────────

def _load_config_raw() -> dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def run_headless(args) -> None:
    """Run a backup with no GUI — suitable for cron / Task Scheduler."""
    cfg = _load_config_raw()
    token = cfg.get("token", "").strip()
    guild_id = args.guild_id or cfg.get("schedule_guild_id")
    msg_limit = args.msg_limit if args.msg_limit is not None else cfg.get("schedule_msg_limit", 500)

    if not token:
        print("ERROR: No bot token found. Open the Denuker app and enter your token first.")
        sys.exit(1)
    if not guild_id:
        print(
            "ERROR: No server ID specified.\n"
            "Use --guild-id YOUR_SERVER_ID  or set it in the app's schedule section."
        )
        sys.exit(1)

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Denuker headless backup starting...")
    print(f"  Server ID  : {guild_id}")
    print(f"  Msg limit  : {'all' if msg_limit == -1 else msg_limit} per channel")

    def cb(msg):
        print(f"  {msg}")

    try:
        data = asyncio.run(bk.create_backup(token, guild_id, msg_limit, cb))
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)

    server_name = data["meta"]["server_name"].replace(" ", "_")
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"denuker_{server_name}_{date_str}.json"

    backup_dir = args.backup_dir or cfg.get("backup_dir") or _default_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    save_path = os.path.join(backup_dir, filename)

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Backup saved: {save_path}")
    sys.exit(0)


def _default_backup_dir() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else os.path.expanduser("~")


# ── Main App ─────────────────────────────────────────────────────────────────

class DenukerApp:

    def __init__(self, root):
        self.root = root
        self.root.title("Denuker — Discord Backup & Recovery")
        self.root.geometry("760x760")
        self.root.minsize(680, 660)
        self.root.configure(bg=BG)

        # State
        self._log_queue: queue.Queue = queue.Queue()
        self._guilds: list[tuple[str, int]] = []
        self._selected_guild: tuple[str, int] | None = None
        self._backup_data: dict | None = None
        self._busy = False

        # Schedule state
        self._schedule_timer: threading.Timer | None = None
        self._next_backup_time: datetime | None = None
        self._schedule_running = False   # True while a scheduled backup is in progress

        # OAuth server state
        self._oauth_server_thread = None

        # Tk vars — created before _load_config reads into them
        import tkinter as tk
        self.token_var              = tk.StringVar()
        self.msg_limit_var          = tk.StringVar(value="500")
        self.restore_msgs           = tk.BooleanVar(value=True)
        self.restore_dry_run        = tk.BooleanVar(value=False)
        self.restore_rejoin         = tk.BooleanVar(value=True)
        self.file_label_var         = tk.StringVar(value="No backup file selected")
        self.schedule_enabled       = tk.BooleanVar(value=False)
        self.schedule_interval_var  = tk.StringVar(value="24")
        self.schedule_msg_limit_var = tk.StringVar(value="500")
        self.oauth_client_id_var    = tk.StringVar()
        self.oauth_client_secret_var = tk.StringVar()
        self._reg_count_var         = tk.StringVar(value="0 members registered")

        self._load_config()
        self._build_ui()
        self._poll_log()
        self._update_countdown()   # start the countdown ticker

        if self.token_var.get():
            self.root.after(400, self._connect)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_config(self):
        cfg = _load_config_raw()
        self.token_var.set(cfg.get("token", ""))
        self.schedule_enabled.set(cfg.get("schedule_enabled", False))
        self.schedule_interval_var.set(str(cfg.get("schedule_interval_hours", 24)))
        self.schedule_msg_limit_var.set(str(cfg.get("schedule_msg_limit", 500)))
        self.oauth_client_id_var.set(cfg.get("oauth_client_id", ""))
        self.oauth_client_secret_var.set(cfg.get("oauth_client_secret", ""))

    def _save_config(self):
        cfg = _load_config_raw()
        cfg.update({
            "token": self.token_var.get(),
            "schedule_enabled": self.schedule_enabled.get(),
            "schedule_interval_hours": self._schedule_hours(),
            "schedule_msg_limit": self._schedule_msg_limit(),
            "schedule_guild_id": (
                self._selected_guild[1] if self._selected_guild else
                cfg.get("schedule_guild_id")
            ),
            "backup_dir": _default_backup_dir(),
            "oauth_client_id": self.oauth_client_id_var.get(),
            "oauth_client_secret": self.oauth_client_secret_var.get(),
        })
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        import tkinter as tk
        from tkinter import ttk, scrolledtext
        self._style()

        # ── Header ──────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG2, pady=14)
        header.pack(fill=tk.X)
        tk.Label(header, text="🛡  DENUKER", font=("Helvetica", 22, "bold"),
                 bg=BG2, fg=ACCENT).pack()
        tk.Label(header, text="Discord Server Backup & Recovery",
                 font=("Helvetica", 10), bg=BG2, fg=TEXT2).pack()

        # ── Connection bar ───────────────────────────────────────────────────
        conn = tk.Frame(self.root, bg=BG3, pady=10, padx=16)
        conn.pack(fill=tk.X)

        tk.Label(conn, text="Bot Token:", bg=BG3, fg=TEXT2,
                 width=12, anchor="w").grid(row=0, column=0, sticky="w")
        self._token_entry = tk.Entry(
            conn, textvariable=self.token_var, show="•",
            bg=BG2, fg=TEXT, insertbackground=TEXT, width=52, relief="flat",
            highlightbackground=ACCENT, highlightthickness=1,
        )
        self._token_entry.grid(row=0, column=1, padx=6, pady=3, sticky="ew")
        self._connect_btn = self._btn(conn, "Connect", self._connect, ACCENT)
        self._connect_btn.grid(row=0, column=2, padx=4)
        self._btn(conn, "? Help", self._show_help, BG2).grid(row=0, column=3, padx=2)

        tk.Label(conn, text="Server:", bg=BG3, fg=TEXT2,
                 width=12, anchor="w").grid(row=1, column=0, sticky="w", pady=6)
        self._guild_combo = ttk.Combobox(conn, width=50, state="disabled",
                                         font=("Helvetica", 10))
        self._guild_combo.grid(row=1, column=1, padx=6, sticky="ew")
        self._guild_combo.bind("<<ComboboxSelected>>", self._on_guild_select)
        self._status_lbl = tk.Label(conn, text="⬤ Not connected",
                                    bg=BG3, fg=RED, font=("Helvetica", 9))
        self._status_lbl.grid(row=1, column=2, columnspan=2, padx=4)
        conn.columnconfigure(1, weight=1)

        # ── Backup / Restore panels ───────────────────────────────────────────
        panels = tk.Frame(self.root, bg=BG, padx=12, pady=8)
        panels.pack(fill=tk.BOTH, expand=False)
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        bframe = tk.LabelFrame(panels, text="  💾  BACKUP  ",
                               font=("Helvetica", 11, "bold"),
                               bg=BG, fg=GREEN, padx=12, pady=8)
        bframe.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_backup_panel(bframe)

        rframe = tk.LabelFrame(panels, text="  🔄  RESTORE  ",
                               font=("Helvetica", 11, "bold"),
                               bg=BG, fg=RED, padx=12, pady=8)
        rframe.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._build_restore_panel(rframe)

        # ── Schedule section ──────────────────────────────────────────────────
        self._build_schedule_section()
        self._build_member_rejoin_section()

        # ── Log ──────────────────────────────────────────────────────────────
        log_outer = tk.Frame(self.root, bg=BG, padx=12)
        log_outer.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        log_hdr = tk.Frame(log_outer, bg=BG)
        log_hdr.pack(fill=tk.X)
        tk.Label(log_hdr, text="Activity Log", font=("Helvetica", 10, "bold"),
                 bg=BG, fg=TEXT2).pack(side=tk.LEFT)
        self._btn(log_hdr, "Clear", self._clear_log, BG2).pack(side=tk.RIGHT, pady=2)

        self._log_box = scrolledtext.ScrolledText(
            log_outer, height=8,
            bg=BG2, fg=TEXT2, font=("Courier", 9),
            relief="flat", state="disabled",
        )
        self._log_box.pack(fill=tk.BOTH, expand=True, pady=4)

    def _build_backup_panel(self, parent):
        import tkinter as tk
        tk.Label(parent, text="How many messages to save per channel?",
                 bg=BG, fg=TEXT2, anchor="w").pack(fill=tk.X)
        opts = tk.Frame(parent, bg=BG)
        opts.pack(fill=tk.X, pady=4)
        for label, val in [("500  (fast)", "500"), ("2 000", "2000"), ("All  (slow)", "-1")]:
            tk.Radiobutton(opts, text=label, variable=self.msg_limit_var, value=val,
                           bg=BG, fg=TEXT2, selectcolor=BG2,
                           activebackground=BG, activeforeground=TEXT).pack(side=tk.LEFT, padx=4)
        tk.Label(parent, text="or enter a custom number:", bg=BG, fg=TEXT3,
                 font=("Helvetica", 9)).pack(anchor="w", pady=(6, 2))
        tk.Entry(parent, textvariable=self.msg_limit_var,
                 bg=BG2, fg=TEXT, insertbackground=TEXT, width=10, relief="flat",
                 highlightbackground=TEXT3, highlightthickness=1).pack(anchor="w")
        tk.Label(parent, text="\nBackup will be saved to your Desktop.",
                 bg=BG, fg=TEXT3, font=("Helvetica", 9), justify="left").pack(anchor="w")
        self._backup_btn = self._btn(parent, "CREATE BACKUP", self._do_backup,
                                     GREEN, big=True, state="disabled")
        self._backup_btn.pack(fill=tk.X, pady=(10, 0))

    def _build_restore_panel(self, parent):
        import tkinter as tk
        tk.Label(parent, text="Step 1 — Load your backup file:",
                 bg=BG, fg=TEXT2, anchor="w").pack(fill=tk.X)
        self._btn(parent, "📂  Open Backup File…", self._load_backup,
                  BG3).pack(anchor="w", pady=(4, 2))
        tk.Label(parent, textvariable=self.file_label_var,
                 bg=BG, fg=TEXT3, wraplength=300, justify="left",
                 font=("Helvetica", 9)).pack(anchor="w", pady=2)
        tk.Label(parent, text="\nStep 2 — Options:", bg=BG, fg=TEXT2,
                 anchor="w").pack(fill=tk.X)
        tk.Checkbutton(parent, text="Restore messages (takes longer)",
                       variable=self.restore_msgs,
                       bg=BG, fg=TEXT2, selectcolor=BG2,
                       activebackground=BG, activeforeground=TEXT).pack(anchor="w", pady=2)
        tk.Checkbutton(parent, text="Re-add registered members automatically",
                       variable=self.restore_rejoin,
                       bg=BG, fg=GREEN, selectcolor=BG2,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Helvetica", 9, "bold")).pack(anchor="w", pady=2)
        tk.Checkbutton(parent,
                       text="Dry Run — preview only, nothing will change",
                       variable=self.restore_dry_run,
                       bg=BG, fg=YELLOW, selectcolor=BG2,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Helvetica", 9, "bold")).pack(anchor="w", pady=2)
        tk.Label(parent,
                 text="\n⚠  Make sure the bot is still in the\nserver before clicking Restore.",
                 bg=BG, fg=YELLOW, justify="left", font=("Helvetica", 9)).pack(anchor="w")
        self._restore_btn = self._btn(parent, "RESTORE SERVER", self._do_restore,
                                      RED, big=True, state="disabled")
        self._restore_btn.pack(fill=tk.X, pady=(10, 0))

    def _build_schedule_section(self):
        import tkinter as tk
        from tkinter import ttk

        frame = tk.LabelFrame(
            self.root,
            text="  ⏰  AUTO-BACKUP SCHEDULE  ",
            font=("Helvetica", 10, "bold"),
            bg=BG, fg=ACCENT,
            padx=12, pady=10,
        )
        frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        # Row 1 — enable + interval
        row1 = tk.Frame(frame, bg=BG)
        row1.pack(fill=tk.X)

        tk.Checkbutton(
            row1, text="Auto-backup while app is open   every",
            variable=self.schedule_enabled,
            command=self._on_schedule_toggle,
            bg=BG, fg=TEXT2, selectcolor=BG2,
            activebackground=BG, activeforeground=TEXT,
        ).pack(side=tk.LEFT)

        interval_combo = ttk.Combobox(
            row1,
            textvariable=self.schedule_interval_var,
            values=["1", "2", "4", "6", "8", "12", "24", "48", "168"],
            width=5,
            state="readonly",
        )
        interval_combo.pack(side=tk.LEFT, padx=4)
        interval_combo.bind("<<ComboboxSelected>>", lambda _: self._on_schedule_toggle())

        tk.Label(row1, text="hours", bg=BG, fg=TEXT2).pack(side=tk.LEFT)

        self._next_lbl = tk.Label(row1, text="", bg=BG, fg=TEXT3,
                                  font=("Helvetica", 9))
        self._next_lbl.pack(side=tk.LEFT, padx=16)

        # Row 2 — msg limit for auto-backup + system schedule button
        row2 = tk.Frame(frame, bg=BG)
        row2.pack(fill=tk.X, pady=(6, 0))

        tk.Label(row2, text="Messages per channel (auto):", bg=BG, fg=TEXT3,
                 font=("Helvetica", 9)).pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.schedule_msg_limit_var,
                 bg=BG2, fg=TEXT, insertbackground=TEXT, width=7, relief="flat",
                 highlightbackground=TEXT3, highlightthickness=1).pack(side=tk.LEFT, padx=6)

        self._btn(
            row2, "📅  Set Up Background Schedule (runs without the app)",
            self._show_system_schedule, BG2,
        ).pack(side=tk.RIGHT)

    def _build_member_rejoin_section(self):
        import tkinter as tk
        import oauth_server as oa

        frame = tk.LabelFrame(
            self.root,
            text="  👥  MEMBER AUTO-REJOIN  ",
            font=("Helvetica", 10, "bold"),
            bg=BG, fg=ACCENT,
            padx=12, pady=10,
        )
        frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        # Row 1 — credentials
        row1 = tk.Frame(frame, bg=BG)
        row1.pack(fill=tk.X)

        # Row 1 — credentials
        tk.Label(row1, text="OAuth2 Client ID:", bg=BG, fg=TEXT2,
                 font=("Helvetica", 9), width=18, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row1, textvariable=self.oauth_client_id_var,
                 bg=BG2, fg=TEXT, insertbackground=TEXT, width=22, relief="flat",
                 highlightbackground=TEXT3, highlightthickness=1).pack(side=tk.LEFT, padx=4)

        tk.Label(row1, text="  Client Secret:", bg=BG, fg=TEXT2,
                 font=("Helvetica", 9)).pack(side=tk.LEFT)
        tk.Entry(row1, textvariable=self.oauth_client_secret_var,
                 show="•", bg=BG2, fg=TEXT, insertbackground=TEXT, width=28,
                 relief="flat", highlightbackground=TEXT3,
                 highlightthickness=1).pack(side=tk.LEFT, padx=4)

        self._btn(row1, "? Setup", self._show_oauth_help, BG2).pack(side=tk.LEFT, padx=4)

        # Row 2 — registration link (always visible once Client ID is set)
        row2 = tk.Frame(frame, bg=BG2, padx=10, pady=8)
        row2.pack(fill=tk.X, pady=(8, 0))

        tk.Label(row2, text="📋  Member Registration Link:",
                 bg=BG2, fg=TEXT2, font=("Helvetica", 9, "bold")).pack(anchor="w")

        link_row = tk.Frame(row2, bg=BG2)
        link_row.pack(fill=tk.X, pady=(4, 0))

        self._reg_link_var = tk.StringVar(value="Enter Client ID above to generate link")
        self._reg_link_display = tk.Entry(
            link_row, textvariable=self._reg_link_var,
            bg=BG3, fg=ACCENT, readonlybackground=BG3,
            font=("Courier", 9), relief="flat", state="readonly",
        )
        self._reg_link_display.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self._copy_btn = self._btn(link_row, "📋 Copy", self._copy_reg_link, ACCENT)
        self._copy_btn.pack(side=tk.LEFT)

        tk.Label(row2,
                 text="Share this link in your server. Members click it once to register.",
                 bg=BG2, fg=TEXT3, font=("Helvetica", 8)).pack(anchor="w", pady=(4, 0))

        # Row 3 — server controls + count
        row3 = tk.Frame(frame, bg=BG)
        row3.pack(fill=tk.X, pady=(8, 0))

        self._start_reg_btn = self._btn(
            row3, "▶  Start Server", self._start_oauth_server, GREEN
        )
        self._start_reg_btn.pack(side=tk.LEFT)

        self._stop_reg_btn = self._btn(
            row3, "■  Stop", self._stop_oauth_server, BG3, state="disabled"
        )
        self._stop_reg_btn.pack(side=tk.LEFT, padx=6)

        self._reg_server_lbl = tk.Label(
            row3, text="", bg=BG, fg=TEXT3, font=("Helvetica", 9)
        )
        self._reg_server_lbl.pack(side=tk.LEFT, padx=4)

        self._reg_count_lbl = tk.Label(
            row3, textvariable=self._reg_count_var,
            bg=BG, fg=GREEN, font=("Helvetica", 9, "bold")
        )
        self._reg_count_lbl.pack(side=tk.RIGHT)

        # Update link whenever Client ID changes
        self.oauth_client_id_var.trace_add("write", self._on_client_id_change)

        # Refresh the count from file on startup
        self._refresh_reg_count()
        # Show link if client_id already loaded from config
        self._on_client_id_change()

    def _refresh_reg_count(self):
        import oauth_server as oa
        n = oa.get_registered_count()
        self._reg_count_var.set(
            f"{n} member{'s' if n != 1 else ''} registered"
            + (" ✅" if n > 0 else "")
        )
        self.root.after(10_000, self._refresh_reg_count)  # refresh every 10 s

    def _on_client_id_change(self, *_):
        import oauth_server as oa
        client_id = self.oauth_client_id_var.get().strip()
        if client_id:
            self._reg_link_var.set(oa.get_auth_url(client_id))
        else:
            self._reg_link_var.set("Enter Client ID above to generate link")

    def _start_oauth_server(self):
        import oauth_server as oa
        client_id     = self.oauth_client_id_var.get().strip()
        client_secret = self.oauth_client_secret_var.get().strip()

        if not client_id or not client_secret:
            from tkinter import messagebox
            messagebox.showwarning(
                "OAuth2 Credentials Required",
                "Enter your OAuth2 Client ID and Client Secret first.\n\n"
                "Click '? Setup' for instructions.",
            )
            return

        self._save_config()
        self._oauth_server_thread = oa.start(client_id, client_secret, log_fn=self._log)
        auth_url = oa.get_auth_url(client_id)

        self._reg_server_lbl.configure(text="🟢 Server running", fg=GREEN)
        self._log(f"\n── Registration server started on port 5173 ──")
        self._log(f"  Link: {auth_url}")
        self._log(f"  Members click it, authorize, and they're registered.")
        self._log(f"  Keep the app open while members are registering.")

        self._start_reg_btn.configure(state="disabled")
        self._stop_reg_btn.configure(state="normal")

    def _stop_oauth_server(self):
        self._oauth_server_thread = None
        self._reg_server_lbl.configure(text="⚫ Server stopped", fg=TEXT3)
        self._log("  Registration server stopped.")
        self._start_reg_btn.configure(state="normal")
        self._stop_reg_btn.configure(state="disabled")

    def _copy_reg_link(self, _event=None):
        import oauth_server as oa
        client_id = self.oauth_client_id_var.get().strip()
        if not client_id:
            from tkinter import messagebox
            messagebox.showwarning("No Client ID", "Enter your OAuth2 Client ID first.")
            return
        url = oa.get_auth_url(client_id)
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        # Flash confirmation
        self._copy_btn.configure(text="✅ Copied!", bg=GREEN)
        self.root.after(2000, lambda: self._copy_btn.configure(text="📋 Copy", bg=ACCENT))

    def _show_oauth_help(self):
        import tkinter as tk
        from tkinter import scrolledtext
        win = tk.Toplevel(self.root)
        win.title("Member Auto-Rejoin Setup")
        win.geometry("560x540")
        win.configure(bg=BG)
        win.grab_set()
        tk.Label(win, text="Member Auto-Rejoin Setup",
                 font=("Helvetica", 13, "bold"), bg=BG, fg=ACCENT).pack(pady=(16, 4))
        box = scrolledtext.ScrolledText(win, bg=BG2, fg=TEXT2, font=("Courier", 10),
                                        relief="flat", padx=14, pady=10, wrap=tk.WORD)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        box.insert("end", OAUTH_HELP_TEXT)
        box.configure(state="disabled")
        self._btn(win, "Close", win.destroy, ACCENT).pack(pady=(0, 12))

    def _style(self):
        from tkinter import ttk
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox",
                        fieldbackground=BG2, background=BG2, foreground=TEXT,
                        selectbackground=BG3, selectforeground=TEXT, arrowcolor=TEXT2)

    @staticmethod
    def _btn(parent, text, cmd, color, big=False, state="normal"):
        import tkinter as tk
        font = ("Helvetica", 12, "bold") if big else ("Helvetica", 9)
        pady = 10 if big else 4
        return tk.Button(
            parent, text=text, command=cmd,
            bg=color, fg=TEXT, font=font, pady=pady,
            relief="flat", activebackground=_darken(color), activeforeground=TEXT,
            cursor="hand2", state=state, disabledforeground="#666",
        )

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log(self):
        while not self._log_queue.empty():
            msg = self._log_queue.get_nowait()
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.root.after(100, self._poll_log)

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── Busy state ────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy = busy
        import tkinter as tk
        state = "disabled" if busy else "normal"
        self._connect_btn.configure(state=state)
        self._token_entry.configure(state=state)
        if not busy:
            self._refresh_buttons()
        else:
            self._backup_btn.configure(state="disabled")
            self._restore_btn.configure(state="disabled")

    def _refresh_buttons(self):
        has_guild = self._selected_guild is not None
        has_backup = self._backup_data is not None
        self._backup_btn.configure(state="normal" if has_guild else "disabled")
        self._restore_btn.configure(
            state="normal" if (has_guild and has_backup) else "disabled"
        )

    # ── Connect ───────────────────────────────────────────────────────────────

    def _connect(self):
        token = self.token_var.get().strip()
        if not token:
            from tkinter import messagebox
            messagebox.showwarning("Token Required",
                "Please enter your bot token first.\n\nClick '? Help' for setup instructions.")
            return
        self._save_config()
        self._set_busy(True)
        self._log("Connecting to Discord...")
        self._status_lbl.configure(text="⬤ Connecting…", fg=YELLOW)

        def run():
            import discord as _discord
            intents = _discord.Intents.default()
            intents.guilds = True
            client = _discord.Client(intents=intents)
            guilds = []

            @client.event
            async def on_ready():
                for g in client.guilds:
                    guilds.append((g.name, g.id))
                await client.close()

            try:
                asyncio.run(client.start(token))
                self.root.after(0, lambda: self._on_connected(guilds))
            except _discord.LoginFailure:
                self.root.after(0, lambda: self._on_connect_error(
                    "Invalid token — please check it in the Discord Developer Portal."
                ))
            except Exception as exc:
                self.root.after(0, lambda: self._on_connect_error(str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def _on_connected(self, guilds: list):
        self._guilds = guilds
        self._log(f"✅ Connected!  Found {len(guilds)} server(s).")
        self._status_lbl.configure(text="⬤ Connected", fg=GREEN)
        names = [g[0] for g in guilds]
        self._guild_combo["values"] = names
        self._guild_combo.configure(state="readonly")
        if guilds:
            self._guild_combo.current(0)
            self._selected_guild = guilds[0]
        self._set_busy(False)
        # Kick off schedule if it was already enabled before connect
        if self.schedule_enabled.get():
            self._on_schedule_toggle()

    def _on_connect_error(self, msg: str):
        from tkinter import messagebox
        self._log(f"❌ {msg}")
        self._status_lbl.configure(text="⬤ Not connected", fg=RED)
        messagebox.showerror("Connection Failed", msg)
        self._set_busy(False)

    def _on_guild_select(self, _event=None):
        idx = self._guild_combo.current()
        if 0 <= idx < len(self._guilds):
            self._selected_guild = self._guilds[idx]
            self._save_config()   # persist selected guild for headless mode
        self._refresh_buttons()

    # ── Manual Backup ─────────────────────────────────────────────────────────

    def _do_backup(self):
        if not self._selected_guild:
            from tkinter import messagebox
            messagebox.showwarning("No Server", "Please connect and select a server first.")
            return
        try:
            msg_limit = int(self.msg_limit_var.get().strip() or "500")
        except ValueError:
            from tkinter import messagebox
            messagebox.showerror("Invalid Number",
                f"'{self.msg_limit_var.get()}' is not a valid number.")
            return
        guild_name, guild_id = self._selected_guild
        self._set_busy(True)
        self._log(f"\n── Manual backup of '{guild_name}' ──")
        self._run_backup_thread(guild_id, msg_limit, on_done=self._on_manual_backup_done)

    def _run_backup_thread(self, guild_id: int, msg_limit: int, on_done):
        token = self.token_var.get().strip()

        def run():
            try:
                data = asyncio.run(bk.create_backup(token, guild_id, msg_limit, self._log))
                self.root.after(0, lambda: on_done(data))
            except Exception as exc:
                self.root.after(0, lambda: self._on_error("Backup Failed", str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def _save_backup_file(self, data: dict) -> str:
        server_name = data["meta"]["server_name"].replace(" ", "_")
        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"denuker_{server_name}_{date_str}.json"
        save_dir = _default_backup_dir()
        save_path = os.path.join(save_dir, filename)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return save_path

    def _on_manual_backup_done(self, data: dict):
        from tkinter import messagebox
        path = self._save_backup_file(data)
        self._log(f"💾 Saved to: {path}")
        messagebox.showinfo("Backup Saved!",
            f"Backup saved to your Desktop:\n\n{os.path.basename(path)}\n\n"
            "Keep this file somewhere safe (cloud storage, email it to yourself, etc.)")
        self._set_busy(False)

    # ── Scheduled Backup ──────────────────────────────────────────────────────

    def _on_schedule_toggle(self):
        if self.schedule_enabled.get() and self._selected_guild:
            self._schedule_next()
        else:
            self._cancel_schedule()
        self._save_config()

    def _schedule_hours(self) -> float:
        try:
            return max(0.1, float(self.schedule_interval_var.get()))
        except ValueError:
            return 24.0

    def _schedule_msg_limit(self) -> int:
        try:
            return int(self.schedule_msg_limit_var.get())
        except ValueError:
            return 500

    def _schedule_next(self):
        """Arm the next scheduled backup timer."""
        self._cancel_schedule()
        if not self.schedule_enabled.get():
            return
        hours = self._schedule_hours()
        secs = hours * 3600
        self._next_backup_time = datetime.now() + timedelta(seconds=secs)
        self._schedule_timer = threading.Timer(secs, self._fire_scheduled_backup)
        self._schedule_timer.daemon = True
        self._schedule_timer.start()
        self._log(
            f"⏰ Auto-backup scheduled — next run at "
            f"{self._next_backup_time:%H:%M:%S} (every {hours:g}h)"
        )

    def _cancel_schedule(self):
        if self._schedule_timer:
            self._schedule_timer.cancel()
            self._schedule_timer = None
        self._next_backup_time = None

    def _fire_scheduled_backup(self):
        """Called by the background timer — post back to the GUI thread."""
        self.root.after(0, self._run_scheduled_backup)

    def _run_scheduled_backup(self):
        if self._busy or not self._selected_guild:
            # Retry in 5 minutes if app is currently busy
            self._next_backup_time = datetime.now() + timedelta(minutes=5)
            self._schedule_timer = threading.Timer(300, self._fire_scheduled_backup)
            self._schedule_timer.daemon = True
            self._schedule_timer.start()
            return

        guild_name, guild_id = self._selected_guild
        msg_limit = self._schedule_msg_limit()
        self._schedule_running = True
        self._set_busy(True)
        self._log(f"\n── Auto-backup: '{guild_name}' ──")
        self._run_backup_thread(guild_id, msg_limit, on_done=self._on_scheduled_backup_done)

    def _on_scheduled_backup_done(self, data: dict):
        path = self._save_backup_file(data)
        self._log(f"💾 Auto-backup saved: {path}")
        self._schedule_running = False
        self._set_busy(False)
        self._schedule_next()   # arm the next one

    def _update_countdown(self):
        """Tick every 30 s — updates the 'next backup in' label."""
        if self._next_backup_time and self.schedule_enabled.get():
            remaining = self._next_backup_time - datetime.now()
            secs = int(remaining.total_seconds())
            if secs > 0:
                h, rem = divmod(secs, 3600)
                m = rem // 60
                label = f"next in {h}h {m}m" if h else f"next in {m}m"
                self._next_lbl.configure(text=f"✅ enabled — {label}", fg=GREEN)
            else:
                self._next_lbl.configure(text="⚡ running now…", fg=YELLOW)
        elif self.schedule_enabled.get():
            self._next_lbl.configure(text="waiting for connection…", fg=TEXT3)
        else:
            self._next_lbl.configure(text="", fg=TEXT3)

        self.root.after(30_000, self._update_countdown)

    # ── System schedule popup ─────────────────────────────────────────────────

    def _show_system_schedule(self):
        import tkinter as tk
        from tkinter import scrolledtext

        py   = sys.executable
        script = os.path.abspath(__file__)
        guild_id = (
            str(self._selected_guild[1]) if self._selected_guild else "YOUR_GUILD_ID"
        )
        msg_limit = self._schedule_msg_limit()
        os_name = platform.system()

        if os_name == "Windows":
            instructions = _windows_schedule_text(py, script, guild_id, msg_limit)
        else:
            instructions = _unix_schedule_text(py, script, guild_id, msg_limit)

        win = tk.Toplevel(self.root)
        win.title("Background Schedule Setup")
        win.geometry("600x500")
        win.configure(bg=BG)
        win.grab_set()

        tk.Label(win, text="Run Denuker Automatically (no app needed)",
                 font=("Helvetica", 13, "bold"), bg=BG, fg=ACCENT).pack(pady=(16, 4))

        box = scrolledtext.ScrolledText(win, bg=BG2, fg=TEXT2,
                                        font=("Courier", 10), relief="flat",
                                        padx=14, pady=10, wrap=tk.WORD)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        box.insert("end", instructions)
        box.configure(state="disabled")

        self._btn(win, "Close", win.destroy, ACCENT).pack(pady=(0, 12))

    # ── Restore ───────────────────────────────────────────────────────────────

    def _load_backup(self):
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            title="Open Denuker Backup File",
            filetypes=[("Denuker Backup", "*.json"), ("All Files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("meta", {})
            self._backup_data = data
            self.file_label_var.set(
                f"✅  {os.path.basename(path)}\n"
                f"Server: {meta.get('server_name', 'Unknown')}\n"
                f"Date: {str(meta.get('backup_date', ''))[:10]}   "
                f"Members: {meta.get('member_count', '?')}"
            )
            self._log(f"Loaded backup: {meta.get('server_name')} "
                      f"({str(meta.get('backup_date', ''))[:10]})")
            self._refresh_buttons()
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not open backup file:\n{exc}")

    def _do_restore(self):
        from tkinter import messagebox
        if not self._backup_data:
            messagebox.showwarning("No Backup", "Please load a backup file first.")
            return
        if not self._selected_guild:
            messagebox.showwarning("No Server", "Please select a target server first.")
            return

        guild_name, guild_id = self._selected_guild
        backup_server = self._backup_data["meta"].get("server_name", "Unknown")
        restore_msgs   = self.restore_msgs.get()
        do_rejoin      = self.restore_rejoin.get()
        dry_run        = self.restore_dry_run.get()
        client_id      = self.oauth_client_id_var.get().strip()
        client_secret  = self.oauth_client_secret_var.get().strip()

        if dry_run:
            self._set_busy(True)
            self._log("\n── Dry Run: previewing restore ──")

            def run_dry():
                try:
                    asyncio.run(rs.dry_run(self._backup_data, self._log))
                    self.root.after(0, lambda: self._set_busy(False))
                except Exception as exc:
                    self.root.after(0, lambda: self._on_error("Dry Run Error", str(exc)))

            threading.Thread(target=run_dry, daemon=True).start()
            return

        import oauth_server as oa
        registered = oa.get_registered_count()
        rejoin_note = (
            f"• Re-add {registered} registered members: YES\n"
            if (do_rejoin and registered > 0) else
            "• Member re-add: NO (no registered members)\n"
            if do_rejoin else
            "• Member re-add: NO\n"
        )

        if not messagebox.askyesno(
            "Confirm Restore",
            f"This will WIPE and recreate your server from the backup.\n\n"
            f"📦  Backup:  {backup_server}\n"
            f"🎯  Server:  {guild_name}\n\n"
            "⚠  ALL existing channels and categories will be deleted first!\n\n"
            f"• Roles recreated: YES\n"
            f"• Channels recreated: YES\n"
            f"• Messages: {'YES (slower)' if restore_msgs else 'NO'}\n"
            f"{rejoin_note}\n"
            "Proceed?"
        ):
            return

        token = self.token_var.get().strip()
        self._set_busy(True)
        self._log(f"\n── Restoring into '{guild_name}' ──")

        def run():
            try:
                asyncio.run(rs.restore_server(
                    token, guild_id, self._backup_data,
                    restore_msgs, do_rejoin,
                    client_id, client_secret,
                    self._log,
                ))
                self.root.after(0, self._on_restore_done)
            except Exception as exc:
                self.root.after(0, lambda: self._on_error("Restore Failed", str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def _on_restore_done(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "Restore Complete!",
            "Your server has been restored! 🎉\n\n"
            "✅  Roles recreated\n"
            "✅  Channels recreated\n\n"
            "An invite link has been printed in the Activity Log.\n"
            "Share it with your members so they can rejoin.\n\n"
            "(Discord does not allow forcing members to rejoin automatically.)",
        )
        self._set_busy(False)

    def _on_error(self, title: str, msg: str):
        from tkinter import messagebox
        self._log(f"❌  {title}: {msg}")
        messagebox.showerror(title, msg)
        self._schedule_running = False
        self._set_busy(False)
        if self.schedule_enabled.get():
            self._schedule_next()

    # ── Help dialog ──────────────────────────────────────────────────────────

    def _show_help(self):
        import tkinter as tk
        from tkinter import scrolledtext
        win = tk.Toplevel(self.root)
        win.title("Setup Guide")
        win.geometry("520x600")
        win.configure(bg=BG)
        win.grab_set()
        tk.Label(win, text="How to Set Up Denuker",
                 font=("Helvetica", 14, "bold"), bg=BG, fg=ACCENT).pack(pady=(16, 4))
        box = scrolledtext.ScrolledText(win, bg=BG2, fg=TEXT2, font=("Courier", 10),
                                        relief="flat", padx=14, pady=10, wrap=tk.WORD)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        box.insert("end", HELP_TEXT)
        box.configure(state="disabled")
        self._btn(win, "Close", win.destroy, ACCENT).pack(pady=(0, 12))


# ── System schedule text generators ──────────────────────────────────────────

def _unix_schedule_text(py: str, script: str, guild_id: str, msg_limit: int) -> str:
    cron_daily = (
        f"0 9 * * * "
        f"'{py}' '{script}' --headless --guild-id {guild_id} --msg-limit {msg_limit} "
        f">> ~/denuker_log.txt 2>&1"
    )
    cron_6h = (
        f"0 */6 * * * "
        f"'{py}' '{script}' --headless --guild-id {guild_id} --msg-limit {msg_limit} "
        f">> ~/denuker_log.txt 2>&1"
    )
    return f"""
HOW TO RUN DENUKER AUTOMATICALLY (Mac / Linux)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This uses cron to run Denuker in the background, even
when the app is closed.

STEP 1 — Open Terminal and run:

  crontab -e

(This opens your scheduled-task list in a text editor.)


STEP 2 — Add one of these lines at the bottom:

  Daily at 9 AM:
  {cron_daily}

  Every 6 hours:
  {cron_6h}


STEP 3 — Save and exit the editor.
  (In nano: press Ctrl+O, then Ctrl+X)
  (In vim:  press Esc, then type :wq and Enter)


STEP 4 — Verify it was saved:

  crontab -l

You should see your line listed.


NOTES
━━━━━
• Backups are saved to your Desktop (or home folder).
• Logs are written to: ~/denuker_log.txt
• Your bot token is read automatically from your saved config.
• To remove the schedule: run  crontab -e  and delete the line.
"""


def _windows_schedule_text(py: str, script: str, guild_id: str, msg_limit: int) -> str:
    ps = (
        f'$action  = New-ScheduledTaskAction `\n'
        f'    -Execute "{py}" `\n'
        f'    -Argument "{script} --headless --guild-id {guild_id} --msg-limit {msg_limit}" `\n'
        f'    -WorkingDirectory "{os.path.dirname(script)}"\n'
        f'\n'
        f'$trigger = New-ScheduledTaskTrigger -Daily -At "09:00AM"\n'
        f'\n'
        f'Register-ScheduledTask `\n'
        f'    -Action  $action `\n'
        f'    -Trigger $trigger `\n'
        f'    -TaskName "Denuker Daily Backup" `\n'
        f'    -Description "Automatic Discord server backup" `\n'
        f'    -Force\n'
    )
    return f"""
HOW TO RUN DENUKER AUTOMATICALLY (Windows)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This uses Windows Task Scheduler to run Denuker daily,
even when the app is closed.

STEP 1 — Open PowerShell as Administrator
  (Right-click the Start button → "Windows PowerShell (Admin)")


STEP 2 — Paste and run this command:

{ps}

STEP 3 — Done! Denuker will now run every day at 9 AM.


TO CHANGE THE TIME
━━━━━━━━━━━━━━━━━━
Change  -At "09:00AM"  to whatever time you prefer,
e.g. "11:00PM" or "06:00AM".


TO REMOVE THE SCHEDULE
━━━━━━━━━━━━━━━━━━━━━━
  Unregister-ScheduledTask -TaskName "Denuker Daily Backup" -Confirm:$false

Or open Task Scheduler from the Start Menu, find
"Denuker Daily Backup", and delete it.


NOTES
━━━━━
• Backups are saved to your Desktop.
• Your bot token is read from the saved config automatically.
"""


# ── Help text ─────────────────────────────────────────────────────────────────

OAUTH_HELP_TEXT = """
HOW MEMBER AUTO-REJOIN WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Members authorize your Discord app once.
If the server ever gets nuked, Denuker re-adds them
automatically with their original roles — no invite needed.


STEP 1 — Get Your OAuth2 Credentials
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Go to discord.com/developers/applications
2. Open your Denuker bot application
3. Click "OAuth2" in the left menu
4. Copy the "Client ID"  →  paste into Denuker
5. Click "Reset Secret"  →  copy it  →  paste into Denuker


STEP 2 — Register the Redirect URI (one time)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Still on the OAuth2 page, under "Redirects":
  1. Click "Add Redirect"
  2. Enter exactly:   http://localhost:5173/callback
  3. Click "Save Changes"

That's it — no hosting or GitHub Pages required.


STEP 3 — Get Members to Register
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. In Denuker, enter Client ID + Secret, then click
   "Start Registration Server"
2. A registration link will appear — share it in your server
   (pin it in #rules, #welcome, or #verification)
3. Members click it, Discord asks permission, they click Authorize
4. They see a success page and are immediately registered
5. The counter in Denuker updates automatically

⚠  IMPORTANT: The registration server must be running on
   YOUR computer when members click the link. The link only
   works while "Start Registration Server" is active.


STEP 4 — Restore With Auto-Rejoin
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When restoring after a nuke, make sure:
  ✅  "Re-add registered members" is checked
  ✅  Client ID and Secret are filled in
  ✅  The registration server does NOT need to be running

Denuker re-adds every registered member with their original
roles. Anyone not registered still gets the invite link.


NOTES
━━━━
• Tokens stored in  ~/.denuker_tokens.json  (owner-only)
• Tokens expire after 7 days but auto-refresh on use
• Members only need to authorize once — ever
• Check the registered count in the main window anytime
"""

HELP_TEXT = """
STEP 1 — Create a Discord Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Open discord.com/developers/applications in your browser
2. Click "New Application" → give it a name (e.g. "Denuker")
3. In the left menu, click "Bot"
4. Click "Add Bot"  →  "Yes, do it!"
5. Under "Token", click "Reset Token"
6. Copy the token and paste it into Denuker's Token field


STEP 2 — Enable Privileged Intents
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Still on the Bot page, scroll down to
"Privileged Gateway Intents" and turn ON:

  ✅  Server Members Intent
  ✅  Message Content Intent

(These let the bot read your member list and messages.)


STEP 3 — Invite the Bot to Your Server
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. In the left menu, click "OAuth2" → "URL Generator"
2. Under "Scopes", tick  bot
3. Under "Bot Permissions", tick  Administrator
4. Copy the generated URL and open it in your browser
5. Select your server → click "Authorize"


STEP 4 — Create Regular Backups
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After connecting your token, click CREATE BACKUP.
A .json file will be saved to your Desktop.

Store backup files somewhere safe:
  • Email them to yourself
  • Save to Google Drive / iCloud / Dropbox
  • Keep on a USB stick


STEP 5 — Recovering After a Nuke
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If your server gets nuked (all channels deleted):

1. Open Denuker and connect your bot token
2. Select the affected server
3. Click "Open Backup File" and choose your .json backup
4. Click RESTORE SERVER
5. Copy the invite link from the Activity Log
6. Share that link with your members

⚠  Note: Discord does not allow bots to force members to
   rejoin — they must click the invite link themselves.


AUTO-BACKUP (Schedule)
━━━━━━━━━━━━━━━━━━━━━━
While the app is open:
  Use the ⏰ AUTO-BACKUP SCHEDULE section to enable
  automatic backups every N hours while the app is running.

Without the app open:
  Click "Set Up Background Schedule" to get step-by-step
  instructions for cron (Mac/Linux) or Task Scheduler
  (Windows) so backups run automatically in the background.
"""


# ── Utility ───────────────────────────────────────────────────────────────────

def _darken(hex_color: str) -> str:
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        f = 0.75
        return "#{:02x}{:02x}{:02x}".format(int(r*f), int(g*f), int(b*f))
    except Exception:
        return hex_color


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Denuker — Discord Server Backup & Recovery"
    )
    p.add_argument("--headless", action="store_true",
                   help="Run a backup silently (no GUI). For cron / Task Scheduler.")
    p.add_argument("--guild-id", type=int, metavar="ID",
                   help="Server ID to back up (headless mode)")
    p.add_argument("--msg-limit", type=int, metavar="N", default=None,
                   help="Max messages per channel (default: from config or 500; -1 = all)")
    p.add_argument("--backup-dir", metavar="PATH",
                   help="Directory to save backup file (default: Desktop)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.headless:
        run_headless(args)   # exits with sys.exit()
    else:
        import tkinter as tk
        root = tk.Tk()
        app = DenukerApp(root)
        root.mainloop()
