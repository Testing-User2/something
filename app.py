import os, threading, logging, requests, uvicorn
from typing import List, Set, Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("follow-api")

# Environment variables (set these in Render → Environment)
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
ROBLOX_COOKIE   = os.getenv("ROBLOX_COOKIE", "")
PORT            = int(os.getenv("PORT", "8000"))
BOT_PREFIX      = os.getenv("BOT_PREFIX", "!")
GUILD_ID_RAW    = os.getenv("GUILD_ID", "")
GUILD_ID        = int(GUILD_ID_RAW) if GUILD_ID_RAW.isdigit() else None

app = FastAPI(title="Follow Check API", version="1.0.0")
CANONICAL_BASE: Optional[str] = None  # set after first /whereami call

def derive_base(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host   = request.headers.get("x-forwarded-host")  or request.headers.get("host") or request.url.netloc
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
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
            headers=_rbx_headers(), params={"cursor": cursor} if cursor else {}, timeout=10
        )
        r.raise_for_status()
        data = r.json()
        for u in data.get("data", []):
            if int(u.get("id", -1)) in remaining:
                remaining.discard(int(u["id"]))
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

# ---------- Optional Discord bot ----------
try:
    import discord
    from discord.ext import commands
    from discord import app_commands
    discord_ok = True
except ImportError:
    discord_ok = False
    log.warning("discord.py not installed; Discord disabled.")

if discord_ok and DISCORD_TOKEN:
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
    async def ping(ctx):  # !ping
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

    def run_api():
        uvicorn.run(app, host="0.0.0.0", port=PORT)

    if __name__ == "__main__":
        threading.Thread(target=run_api, daemon=True).start()
        bot.run(DISCORD_TOKEN)
else:
    # Run API only (no Discord)
    if __name__ == "__main__":
        uvicorn.run(app, host="0.0.0.0", port=PORT)
