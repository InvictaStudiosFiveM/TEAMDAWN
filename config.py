"""
Team Dawn bot settings.
Your token and server ID go in the .env file (or your host's variables), not here.

NOTE: You normally don't need to edit this file. Use /setup in Discord to link
your existing roles, categories and permission levels. The values below are only
used as defaults for anything you haven't set with /setup.
"""

# ---- Rank chain (lowest -> highest). Link your real roles with /setup rank.
RANKS = ["AFL", "S.LD", "LD", "AUX", "RESP", "MST"]

# Other spellings that may show up in old nicknames, mapped to a real rank.
RANK_ALIASES = {"SUB.LD": "S.LD", "SUBLD": "S.LD", "SLD": "S.LD"}

# Minimum rank needed to use each staff command ("SUB LEAD+" = S.LD).
# Server administrators can always use every command.
RECRUIT_MIN_RANK = "S.LD"
PROMOTE_MIN_RANK = "RESP"   # used by /promote and /demote
REMOVE_MIN_RANK = "RESP"
LEADERBOARD_MIN_RANK = "MST"  # used by /leaderboard (posting the board)
CLEAR_MIN_RANK = "RESP"        # used by /clear

# ---- Recruiting
# Roles offered in the dropdown after /recruit.
RECRUIT_ROLE_CHOICES = ["TEAMDAWN", "MEMBER"]
# Every recruit also gets the lowest rank role (AFL) if it exists.
GIVE_START_RANK_ROLE = True
# Category that new 🦋city-id channels are created in (created if missing).
RECRUIT_CATEGORY_NAME = "👥・MEMBERS"
# Category that /remove moves channels into (created if missing).
REMOVED_CATEGORY_NAME = "❌・REMOVED"

NICK_EMOJI = "🦋"

# ---- Sales
# Role required to use /sale. Set to None to let anyone use it.
SALE_REQUIRED_ROLE = "・TeamDawn"
CURRENCY = "£"
SALE_TITLE = "💰 Sale Logged"

# ---- Leaderboard
LEADERBOARD_TITLE = "🏆 Team Dawn Leaderboard"
LEADERBOARD_SIZE = 10

# ---- Colours
SALE_COLOR = 0x57F287       # green bar like the example
LEADERBOARD_COLOR = 0xF1C40F
INFO_COLOR = 0x9B59B6

DATABASE_FILE = "teamdawn.db"
BOT_STATUS = "KNG ESTATE sales"
