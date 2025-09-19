# Follow-Check API + Discord Bot

This FastAPI app provides:
* `GET /checkfollow` – verifies if a Roblox user follows specific accounts
* `GET /whereami` – shows the public base URL (use this after deploy)
* `GET /health` – basic health check

It can also run a Discord bot with `!ping` and `/endpoint`.

## Environment Variables (set these in Render)
- DISCORD_TOKEN : Discord bot token (optional)
- ROBLOX_COOKIE : Roblox .ROBLOSECURITY cookie (for follow checks)
- BOT_PREFIX    : Command prefix for Discord text commands (default `!`)
- GUILD_ID      : (optional) Discord server ID for instant slash command registration
