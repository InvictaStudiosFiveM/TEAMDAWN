# Team Dawn — KNG ESTATE Sales Bot

## 1. Create the bot
1. Go to https://discord.com/developers/applications → **New Application** → name it **Team Dawn**.
2. **Bot** tab → **Reset Token** → copy the token.
3. **OAuth2 → URL Generator**: tick `bot` and `applications.commands`. Under Bot Permissions tick:
   Manage Roles, Manage Channels, Manage Nicknames, Kick Members, Send Messages, Embed Links, Attach Files, Read Message History.
4. Open the generated link and add the bot to your server.

## 2. Run it
```bash
pip install -r requirements.txt
cp .env.example .env      # then paste your token + server ID into .env
python bot.py
```

## 3. First-time setup in Discord
1. Run **/setup** (admins only) — creates the roles `AFL, S.LD, LD, AUX, RESP, MST, TEAMDAWN, MEMBER` and the `TEAM DAWN` and `REMOVED` categories if they're missing.
2. **Server Settings → Roles**: drag the **Team Dawn** bot role *above* all the rank roles. (Discord won't let a bot edit people above its own role or the server owner.)
3. Give your leaders their rank roles (e.g. `S.LD`, `LD`, `MST`).
4. Go to your leaderboard channel and run **/leaderboard**.

## Commands
| Command | Who | What it does |
|---|---|---|
| `/sale client_id item sale_amount proof` | TEAMDAWN role | Posts the 💰 Sale Logged embed with the proof image and updates the leaderboard |
| `/recruit person city_name city_id` | S.LD+ | Pick TEAMDAWN / MEMBER from a dropdown → gives roles + AFL, renames to `🦋AFL \| city \| id`, creates `🦋city-id` channel, adds them to the leaderboard |
| `/promote person [rank]` | S.LD+ | Next rank up (or the one picked), swaps rank role + nickname prefix |
| `/demote person [rank]` | S.LD+ | Next rank down (or the one picked) |
| `/remove person [reason]` | S.LD+ | Moves their channel to REMOVED, takes them off the leaderboard, DMs + kicks them |
| `/leaderboard [channel]` | LD+ | Posts the live leaderboard (only one exists at a time) |
| `/setup` | Admins | Creates missing roles/categories |

Rank chain: AFL → S.LD → LD → AUX → RESP → MST.
You can only promote/demote/remove people **below** you, and only to a rank **below** yours. Admins can do anything.

## Changing things
Everything (who can use what, role names, category names, leaderboard title, currency, colours) is in **config.py**.
Data is stored in `teamdawn.db` (created automatically). Removed members' sales are kept for records but hidden from the leaderboard; if they're re-recruited their old total comes back.
