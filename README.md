# Team Dawn — KNG ESTATE Sales Bot

## 1. Token & server ID
Set these two variables in RocketNode's **Startup** page, or in a `.env` file next to `bot.py`:
```
DISCORD_TOKEN=your-bot-token
GUILD_ID=your-server-id
```

## 2. Bot permissions
When inviting the bot tick `bot` + `applications.commands` and: Manage Roles, Manage Channels,
Manage Nicknames, Kick Members, Send Messages, Embed Links, Attach Files, Read Message History.
Then drag the **Team Dawn** bot role *above* all your rank roles in Server Settings → Roles.

## 3. Link your existing roles (admins, in Discord)
| Command | What it does |
|---|---|
| `/setup rank rank:AFL role:@YourRole` | Link a rank to your role — do this for AFL, S.LD, LD, AUX, RESP, MST |
| `/setup recruit_roles role1 role2 …` | Roles shown in the /recruit dropdown (e.g. TEAMDAWN, MEMBER) — up to 5 |
| `/setup sale_role [role]` | Role needed to use /sale (leave empty = anyone) |
| `/setup recruit_category` | Category new 🦋 channels are made in |
| `/setup removed_category` | Category /remove moves channels to |
| `/setup leaderboard_channel` | Posts the live leaderboard there |
| `/setup permission command min_rank` | Lowest rank that can use /recruit, /promote & /demote, /remove, /clear, /leaderboard |
| `/setup view` | Shows all current settings |

## Commands
| Command | Default access | What it does |
|---|---|---|
| `/sale client_id item sale_amount proof` | sale role | Posts the 💰 Sale Logged embed and updates the leaderboard |
| `/recruit person city_name city_id` | S.LD+ | Pick roles from dropdown → gives roles + AFL, renames to `🦋AFL \| city \| id`, creates `🦋city-id` channel |
| `/promote person [rank]` / `/demote person [rank]` | S.LD+ | Moves along AFL → S.LD → LD → AUX → RESP → MST, swaps role + nickname |
| `/remove person [reason]` | S.LD+ | Moves channel to removed category, takes off leaderboard, kicks |
| `/clear person` | LD+ | Deletes all their sales and removes them from the leaderboard (asks to confirm) |
| `/leaderboard [channel]` | LD+ | Posts the live leaderboard |

Staff can only promote/demote/remove people below them. Admins can do everything.
