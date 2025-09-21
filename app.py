import os, threading, logging, requests, uvicorn
from typing import List, Set, Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("follow-api")

# SET THESE ENV VARS IN YOUR HOST:
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")  # change: set your Discord Bot Token
ROBLOX_COOKIE   = os.getenv("ROBLOX_COOKIE", "")  # change: set .ROBLOSECURITY if required by your use
PORT            = int(os.getenv("PORT", "8000"))  # change: set listening port if needed
BOT_PREFIX      = os.getenv("BOT_PREFIX", "!")    # change: set bot prefix if desired
GUILD_ID_RAW    = os.getenv("GUILD_ID", "")       # change: optionally pin slash sync to a guild ID
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "1")  # change: set to "0" to disable Discord entirely

GUILD_ID: Optional[int] = int(GUILD_ID_RAW) if GUILD_ID_RAW.isdigit() else None

app = FastAPI(title="Follow Check API", version="1.0.0")
CANONICAL_BASE: Optional[str] = None

def derive_base(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host   = request.headers.get("x-forwarded-host")  or request.headers.get("host") or request.url.netloc
    prefix = (request.headers.get("x-forwarded-prefix") or "").rstrip("/")
    base   = f"{scheme}://{host}"
    if prefix:
        base += prefix if prefix.startswith("/") else f"/{prefix}"
    return base.rstrip("/")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/whereami")
def whereami(request: Request):
    global CANONICAL_BASE
    CANONICAL_BASE = derive_base(request)
    return {
        "derived_base_url": CANONICAL_BASE,
        "checkfollow": f"{CANONICAL_BASE}/checkfollow?userId=<id>&targets=<id1,id2>"
    }

def _rbx_headers():
    h = {"Accept": "application/json", "User-Agent": "follow-check/1.0"}
    if ROBLOX_COOKIE:
        h["Cookie"] = f".ROBLOSECURITY={ROBLOX_COOKIE}"
    return h

def follows_all_targets(user_id: int, targets: List[int]) -> bool:
    remaining: Set[int] = set(targets)
    cursor: Optional[str] = None
    for _ in range(50):
        r = requests.get(
            f"https://friends.roblox.com/v1/users/{user_id}/followings",
            headers=_rbx_headers(),
            params={"cursor": cursor} if cursor else {},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        for u in data.get("data", []):
            uid = int(u.get("id", -1))
            if uid in remaining:
                remaining.discard(uid)
                if not remaining:
                    return True
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    return not remaining

@app.get("/checkfollow")
def check_follow(userId: int = Query(...), targets: str = Query(...)):
    if not ROBLOX_COOKIE:
        return JSONResponse({"ok": False, "error": "ROBLOX_COOKIE missing"}, status_code=500)
    try:
        ids = [int(x) for x in targets.split(",") if x.strip().isdigit()]
        if not ids:
            return JSONResponse({"ok": False, "error": "No valid targets"}, status_code=400)
        ok = follows_all_targets(userId, ids)
        return {"ok": True, "followsAll": ok}
    except Exception as e:
        log.exception("Follow check failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ---------- Discord (robust start; won't crash API on bad token) ----------
try:
    import discord
    from discord.ext import commands
    from discord import app_commands
    discord_ok = True
except ImportError:
    discord_ok = False
    log.warning("discord.py not installed; Discord disabled.")

def _sanitize_token(v: str) -> str:
    if not v:
        return ""
    v = v.strip().strip('"').strip("'")
    if v.startswith("Bot "):
        v = v[4:]
    return "".join(v.split())

def _token_looks_plausible(v: str) -> bool:
    return len(v) >= 50 and "." in v

def _discord_token_valid_live(v: str) -> bool:
    try:
        r = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {v}"},
            timeout=10,
        )
        return r.status_code == 200 and isinstance(r.json().get("id"), str)
    except Exception:
        return False

def start_discord_if_valid():
    if not (discord_ok and DISCORD_ENABLED != "0"):
        log.info("Discord disabled or not installed; API-only mode.")
        return

    token = _sanitize_token(DISCORD_TOKEN)
    if not _token_looks_plausible(token):
        log.error("Discord token missing or implausible; skipping Discord startup.")
        return

    if not _discord_token_valid_live(token):
        log.error("Discord token rejected by API preflight; skipping Discord startup.")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

    @bot.event
    async def on_ready():
        try:
            if GUILD_ID:
                await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            else:
                await bot.tree.sync()
        except Exception:
            log.exception("Slash command sync failed")
        log.info("[Discord] Logged in as %s", bot.user)

    @bot.command()
    async def ping(ctx):
        await ctx.send("Pong!")

    @bot.tree.command(name="endpoint", description="Show the public API endpoint")
    async def endpoint(interaction: discord.Interaction):
        if CANONICAL_BASE:
            await interaction.response.send_message(
                f"Verify endpoint:\n`{CANONICAL_BASE}/checkfollow`",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Call `/whereami` in a browser first to establish the public base URL.",
                ephemeral=True
            )

    def _runner():
        try:
            bot.run(token)
        except Exception:
            log.exception("Discord bot stopped unexpectedly")

    threading.Thread(target=_runner, name="discord-bot", daemon=True).start()

def run_api():
    uvicorn.run(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_api, name="uvicorn", daemon=True).start()
    start_discord_if_valid()
    threading.Event().wait()
