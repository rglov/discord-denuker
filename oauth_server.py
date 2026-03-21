"""
oauth_server.py — Local Flask server + cloudflared tunnel for member registration.

Why cloudflared:
  localhost redirects go to the MEMBER's computer, not yours.
  cloudflared creates a public HTTPS URL that tunnels to your machine,
  so Discord can redirect members back to your local server.
"""

from flask import Flask, request, Response
import urllib.request
import urllib.parse
import json
import os
import re
import sys
import stat
import platform
import subprocess
import threading
import time

PORT         = 5173
TOKEN_FILE   = os.path.join(os.path.expanduser("~"), ".denuker_tokens.json")

_DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
_DISCORD_USER_URL  = "https://discord.com/api/users/@me"

# Mutable — updated to the cloudflared tunnel URL once the tunnel starts
_redirect_uri:  str = f"http://localhost:{PORT}/callback"
_client_id:     str = ""
_client_secret: str = ""
_log_callback        = None

_tunnel_proc: subprocess.Popen | None = None

app = Flask(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def start(client_id: str, client_secret: str, log_fn=None) -> threading.Thread:
    """Start the Flask registration server in a background thread."""
    global _client_id, _client_secret, _log_callback
    _client_id      = client_id.strip()
    _client_secret  = client_secret.strip()
    _log_callback   = log_fn

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False),
        daemon=True,
        name="denuker-oauth",
    )
    t.start()
    return t


def start_tunnel(log_fn=None) -> str | None:
    """
    Start a cloudflared quick tunnel pointing at localhost:PORT.
    Returns the public HTTPS URL (e.g. https://xyz.trycloudflare.com), or None.
    Auto-downloads cloudflared if not found.
    """
    global _tunnel_proc, _redirect_uri

    cf = _find_cloudflared()
    if not cf:
        if log_fn:
            log_fn("  cloudflared not found — downloading...")
        cf = _download_cloudflared(log_fn)
        if not cf:
            return None

    if log_fn:
        log_fn("  Starting tunnel...")

    _tunnel_proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Read output until we see the public URL (usually within ~5 s)
    public_url = None
    deadline = time.time() + 30
    for line in _tunnel_proc.stdout:
        if time.time() > deadline:
            break
        m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
        if m:
            public_url = m.group(0)
            break

    if public_url:
        _redirect_uri = f"{public_url}/callback"
        if log_fn:
            log_fn(f"  Tunnel active: {public_url}")
    else:
        stop_tunnel()

    return public_url


def stop_tunnel():
    global _tunnel_proc, _redirect_uri
    if _tunnel_proc:
        _tunnel_proc.terminate()
        _tunnel_proc = None
    _redirect_uri = f"http://localhost:{PORT}/callback"


def get_auth_url(client_id: str) -> str:
    params = urllib.parse.urlencode({
        "client_id":     client_id,
        "redirect_uri":  _redirect_uri,
        "response_type": "code",
        "scope":         "guilds.join identify",
        "prompt":        "none",
    })
    return f"https://discord.com/api/oauth2/authorize?{params}"


def get_redirect_uri() -> str:
    return _redirect_uri


def get_registered_count() -> int:
    return len(load_tokens())


def load_tokens() -> dict:
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_tokens(tokens: dict) -> None:
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass


def refresh_token_sync(token_data: dict, client_id: str, client_secret: str) -> dict | None:
    try:
        result = _http_post(_DISCORD_TOKEN_URL, {
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "refresh_token",
            "refresh_token": token_data["refresh_token"],
        })
        if "error" in result:
            return None
        return {
            **token_data,
            "access_token":  result["access_token"],
            "refresh_token": result.get("refresh_token", token_data["refresh_token"]),
            "expires_at":    time.time() + result.get("expires_in", 604800),
        }
    except Exception:
        return None


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/callback")
def callback():
    error = request.args.get("error")
    code  = request.args.get("code", "").strip()

    if error == "access_denied":
        return _page("⚠️ Cancelled",
                     "You cancelled the authorization. Ask your server owner for the link if you change your mind.",
                     success=False)

    if not code:
        return _page("❌ Error",
                     "No authorization code in the URL. Please use the link your server owner shared.",
                     success=False)

    try:
        token_data = _http_post(_DISCORD_TOKEN_URL, {
            "client_id":     _client_id,
            "client_secret": _client_secret,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  _redirect_uri,
        })

        if "error" in token_data:
            return _page("❌ Discord Error",
                         f"Discord returned: {token_data['error']}. Try clicking the link again.",
                         success=False)

        user     = _http_get(_DISCORD_USER_URL, token_data["access_token"])
        user_id  = user["id"]
        username = user.get("username", "Unknown")

        tokens = load_tokens()
        tokens[user_id] = {
            "access_token":  token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at":    time.time() + token_data.get("expires_in", 604800),
            "username":      username,
        }
        save_tokens(tokens)

        if _log_callback:
            _log_callback(f"  ✅ Registered: {username}")

        return _page(
            f"✅ You're in, {username}!",
            "If this server ever gets nuked, you'll be automatically re-added with your original roles. You can close this tab.",
            success=True,
        )

    except Exception as exc:
        if _log_callback:
            _log_callback(f"  ⚠ Registration error: {exc}")
        return _page("❌ Error", f"Something went wrong: {exc}", success=False)


@app.route("/health")
def health():
    return Response('{"ok":true}', mimetype="application/json")


# ── cloudflared helpers ───────────────────────────────────────────────────────

def _find_cloudflared() -> str | None:
    import shutil
    found = shutil.which("cloudflared")
    if found:
        return found
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ["cloudflared", "cloudflared.exe"]:
        p = os.path.join(script_dir, name)
        if os.path.isfile(p):
            return p
    return None


def _download_cloudflared(log_fn=None) -> str | None:
    """Download the cloudflared binary for this OS/arch into the app folder."""
    import tarfile, tempfile
    system  = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        url  = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    elif system == "Darwin":
        arch = "arm64" if "arm" in machine else "amd64"
        url  = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{arch}.tgz"
        dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared")
    else:
        arch = "arm64" if ("arm" in machine or "aarch" in machine) else "amd64"
        url  = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared")

    try:
        if log_fn:
            log_fn(f"  Downloading cloudflared ({system}/{arch if system != 'Windows' else 'amd64'})...")

        if system == "Darwin":
            with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
                urllib.request.urlretrieve(url, tmp.name)
                with tarfile.open(tmp.name) as tf:
                    # The tarball contains a single file named 'cloudflared'
                    member = next(m for m in tf.getmembers() if m.name == "cloudflared")
                    member.name = os.path.basename(dest)
                    tf.extract(member, path=os.path.dirname(dest))
            os.unlink(tmp.name)
        else:
            urllib.request.urlretrieve(url, dest)

        os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        if log_fn:
            log_fn("  cloudflared downloaded ✅")
        return dest

    except Exception as exc:
        if log_fn:
            log_fn(f"  ⚠ Download failed: {exc}")
        return None


# ── HTML page helper ──────────────────────────────────────────────────────────

def _page(title: str, message: str, success: bool) -> str:
    color = "#43B581" if success else "#F04747"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Denuker — {title}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#2C2F33;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
          display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
    .card{{background:#23272A;border-radius:12px;padding:40px 48px;max-width:460px;width:100%;
           text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.4);border-top:4px solid {color}}}
    h1{{font-size:20px;color:{color};margin-bottom:16px}}
    p{{color:#B9BBBE;line-height:1.6;font-size:15px}}
    .brand{{color:#7289DA;font-size:13px;margin-top:24px}}
  </style>
</head>
<body>
  <div class="card">
    <div style="font-size:48px;margin-bottom:12px">🛡</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <p class="brand">DENUKER — Discord Server Recovery</p>
  </div>
</body>
</html>"""


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http_post(url: str, data: dict) -> dict:
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _http_get(url: str, bearer: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {bearer}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())
