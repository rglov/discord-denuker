"""
oauth_server.py — Local Flask server for Discord OAuth2 member registration.

How it works:
  1. Server owner clicks "Start Registration Server" in the app
  2. Share the registration link with members (posted in Discord)
  3. Members click the link → authorize on Discord's website
  4. Discord redirects directly to http://localhost:5173/callback
     (this server handles it — no GitHub Pages needed)
  5. Token is stored locally; member sees a success page
  6. On restore, stored tokens re-add members via Discord API
"""

from flask import Flask, request, Response
import urllib.request
import urllib.parse
import json
import os
import time
import threading

PORT         = 5173
REDIRECT_URI = f"http://localhost:{PORT}/callback"
TOKEN_FILE   = os.path.join(os.path.expanduser("~"), ".denuker_tokens.json")

_DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
_DISCORD_USER_URL  = "https://discord.com/api/users/@me"

_client_id:    str = ""
_client_secret: str = ""
_log_callback       = None   # fn(str) — sends messages to the GUI log

app = Flask(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def start(client_id: str, client_secret: str, log_fn=None) -> threading.Thread:
    """Start the OAuth2 registration server in a background daemon thread."""
    global _client_id, _client_secret, _log_callback
    _client_id      = client_id.strip()
    _client_secret  = client_secret.strip()
    _log_callback   = log_fn

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    t = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=PORT, debug=False, use_reloader=False
        ),
        daemon=True,
        name="denuker-oauth",
    )
    t.start()
    return t


def get_auth_url(client_id: str) -> str:
    """Return the Discord authorization URL to share with members."""
    params = urllib.parse.urlencode({
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         "guilds.join identify",
        "prompt":        "none",   # skip re-consent if already authorized
    })
    return f"https://discord.com/api/oauth2/authorize?{params}"


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
    """Synchronously refresh an expired access token. Returns updated dict or None."""
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
    """
    Discord redirects here after the user authorizes.
    Exchanges the code, stores the token, returns a styled HTML page.
    """
    error = request.args.get("error")
    code  = request.args.get("code", "").strip()

    if error == "access_denied":
        return _page("⚠️ Cancelled",
                     "You cancelled the authorization. "
                     "Ask your server owner for the link if you change your mind.",
                     success=False)

    if not code:
        return _page("❌ Error",
                     "No authorization code found. "
                     "Please use the link your server owner shared.",
                     success=False)

    try:
        # Exchange code → access token
        token_data = _http_post(_DISCORD_TOKEN_URL, {
            "client_id":     _client_id,
            "client_secret": _client_secret,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
        })

        if "error" in token_data:
            return _page("❌ Discord Error",
                         f"Discord returned: {token_data['error']}. "
                         "Try clicking the link again.",
                         success=False)

        # Fetch username
        user     = _http_get(_DISCORD_USER_URL, token_data["access_token"])
        user_id  = user["id"]
        username = user.get("username", "Unknown")

        # Persist
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
            f"✅ You're registered, {username}!",
            "If this server ever gets nuked, you'll be automatically re-added "
            "with your original roles. You can close this tab.",
            success=True,
        )

    except Exception as exc:
        if _log_callback:
            _log_callback(f"  ⚠ Registration error: {exc}")
        return _page("❌ Error", f"Something went wrong: {exc}", success=False)


@app.route("/status")
def status():
    tokens = load_tokens()
    data = json.dumps({
        "registered": len(tokens),
        "members": [{"id": k, "username": v.get("username")} for k, v in tokens.items()],
    })
    return Response(data, mimetype="application/json")


@app.route("/health")
def health():
    return Response('{"ok":true}', mimetype="application/json")


# ── HTML response helper ──────────────────────────────────────────────────────

def _page(title: str, message: str, success: bool) -> str:
    color  = "#43B581" if success else "#F04747"
    border = "#43B581" if success else "#F04747"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Denuker — {title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #2C2F33; color: #fff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; padding: 20px;
    }}
    .card {{
      background: #23272A; border-radius: 12px;
      padding: 40px 48px; max-width: 460px; width: 100%;
      text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,.4);
      border-top: 4px solid {border};
    }}
    h1 {{ font-size: 20px; color: {color}; margin-bottom: 16px; }}
    p  {{ color: #B9BBBE; line-height: 1.6; font-size: 15px; }}
    .shield {{ font-size: 48px; margin-bottom: 12px; }}
    .brand {{ color: #7289DA; font-size: 13px; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="shield">🛡</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <p class="brand">DENUKER — Discord Server Recovery</p>
  </div>
</body>
</html>"""


# ── Private helpers ───────────────────────────────────────────────────────────

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
