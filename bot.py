"""
Team Dawn — KNG ESTATE sales bot
Commands: /sale  /recruit  /promote  /demote  /remove  /clear  /leaderboard  /setup ...
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import config
from database import Database

load_dotenv()  # reads .env if there is one; host variables (e.g. RocketNode) work too
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("teamdawn")

db = Database(config.DATABASE_FILE)
RANKS = config.RANKS
ADMIN_LEVEL = len(RANKS)  # administrators outrank everyone
RANK_CHOICES = [app_commands.Choice(name=r, value=r) for r in RANKS]

# Commands whose minimum rank can be changed with /setup permission
PERMISSION_DEFAULTS = {
    "recruit": config.RECRUIT_MIN_RANK,
    "promote": config.PROMOTE_MIN_RANK,  # also /demote
    "remove": config.REMOVE_MIN_RANK,
    "clear": config.CLEAR_MIN_RANK,
    "leaderboard": config.LEADERBOARD_MIN_RANK,
}
PERMISSION_LABELS = {
    "recruit": "/recruit",
    "promote": "/promote & /demote",
    "remove": "/remove",
    "clear": "/clear",
    "leaderboard": "/leaderboard",
}


# ======================================================================
# Settings (saved with /setup)
# ======================================================================
def setting_id(key: str) -> Optional[int]:
    value = db.get_setting(key)
    return int(value) if value and value.isdigit() else None


def rank_role(guild: discord.Guild, rank: str) -> Optional[discord.Role]:
    """The server role linked to a rank (set with /setup rank), or a role with the same name."""
    role_id = setting_id(f"rank_role:{rank}")
    if role_id:
        return guild.get_role(role_id)
    return discord.utils.get(guild.roles, name=rank)


def rank_role_levels(guild: discord.Guild) -> dict[int, int]:
    """{role_id: rank level}"""
    levels = {}
    for i, rank in enumerate(RANKS):
        role = rank_role(guild, rank)
        if role:
            levels[role.id] = i
    return levels


def recruit_roles(guild: discord.Guild) -> list[discord.Role]:
    raw = db.get_setting("recruit_roles")
    if raw is not None:
        roles = (guild.get_role(int(x)) for x in raw.split(",") if x.isdigit())
    else:
        roles = (discord.utils.get(guild.roles, name=n) for n in config.RECRUIT_ROLE_CHOICES)
    return [r for r in roles if r]


def required_rank(command: str) -> str:
    saved = db.get_setting(f"min_rank:{command}")
    return saved if saved in RANKS else PERMISSION_DEFAULTS[command]


async def get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name, reason="Team Dawn bot")
    return category


async def category_for(guild: discord.Guild, key: str, default_name: str) -> discord.CategoryChannel:
    """Category chosen with /setup, otherwise one found/created by name."""
    cat_id = setting_id(key)
    if cat_id:
        category = guild.get_channel(cat_id)
        if isinstance(category, discord.CategoryChannel):
            return category
    return await get_or_create_category(guild, default_name)


# ======================================================================
# Helpers
# ======================================================================
def discord_len(text: str) -> int:
    """Discord counts some emoji as 2 characters, so measure in UTF-16 units."""
    return len(text.encode("utf-16-le")) // 2


def fit(text: str, limit: int) -> str:
    while text and discord_len(text) > limit:
        text = text[:-1]
    return text


def normalise_rank(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = name.strip().upper()
    name = config.RANK_ALIASES.get(name, name)
    return name if name in RANKS else None


def rank_index(rank: str) -> int:
    return RANKS.index(rank)


def rank_from_roles(member: discord.Member) -> int:
    """Highest rank role the member has, or -1."""
    levels = rank_role_levels(member.guild)
    return max((levels[r.id] for r in member.roles if r.id in levels), default=-1)


def rank_from_nick(member: discord.Member) -> int:
    nick = member.nick or ""
    if "|" not in nick:
        return -1
    head = nick.split("|", 1)[0].replace(config.NICK_EMOJI, "")
    rank = normalise_rank(head)
    return rank_index(rank) if rank else -1


def current_rank(member: discord.Member) -> int:
    """A member's rank: role first, then nickname, else AFL."""
    level = rank_from_roles(member)
    if level < 0:
        level = rank_from_nick(member)
    return max(level, 0)


def authority(member: discord.Member) -> int:
    """What a staff member is allowed to do. Uses roles only (nicknames can be faked)."""
    if member.guild_permissions.administrator:
        return ADMIN_LEVEL
    return rank_from_roles(member)


def build_nick(rank: str, city_name: str, city_id: str) -> str:
    head = f"{config.NICK_EMOJI}{rank} | "
    tail = f" | {city_id}"
    room = 32 - discord_len(head) - discord_len(tail)
    return fit(head + fit(city_name, max(room, 1)) + tail, 32)


def nick_with_rank(member: discord.Member, rank: str) -> str:
    """Swap the rank part of '🦋AFL | city | id' for the new rank."""
    nick = member.nick or ""
    if "|" in nick:
        rest = nick.split("|", 1)[1].strip()
        return fit(f"{config.NICK_EMOJI}{rank} | {rest}", 32)
    row = db.get_member(member.id)
    if row and row["city_name"] and row["city_id"]:
        return build_nick(rank, row["city_name"], row["city_id"])
    return fit(f"{config.NICK_EMOJI}{rank} | {member.display_name}", 32)


def channel_name(city_name: str, city_id: str) -> str:
    raw = f"{config.NICK_EMOJI}{city_name}-{city_id}".lower()
    return "-".join(raw.split())[:100]


def money(amount: float) -> str:
    return f"{config.CURRENCY}{amount:,.2f}"


async def set_rank_roles(member: discord.Member, rank: str, reason: str) -> list[str]:
    """Give the member the new rank role and take away their other rank roles. Returns warnings."""
    warnings = []
    new_role = rank_role(member.guild, rank)
    levels = rank_role_levels(member.guild)
    old_roles = [r for r in member.roles if r.id in levels and r != new_role]
    try:
        if old_roles:
            await member.remove_roles(*old_roles, reason=reason)
        if new_role and new_role not in member.roles:
            await member.add_roles(new_role, reason=reason)
    except discord.Forbidden:
        warnings.append("I couldn't change their rank roles (move my role higher in Server Settings → Roles).")
    if new_role is None:
        warnings.append(f"No role is linked to **{rank}** — use `/setup rank`. Only the nickname was changed.")
    return warnings


async def set_nick(member: discord.Member, nick: str, reason: str) -> list[str]:
    try:
        await member.edit(nick=nick, reason=reason)
        return []
    except discord.Forbidden:
        return ["I couldn't change their nickname (they may be the server owner or above my role)."]


def min_rank(command: str):
    """Only allow people at or above the rank set for this command (admins always allowed)."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("This command only works inside the server.")
        rank = required_rank(command)
        if authority(interaction.user) >= rank_index(rank):
            return True
        raise app_commands.CheckFailure(f"You need **{rank}** or higher to use this command.")

    return app_commands.check(predicate)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.CheckFailure("Only server administrators can use /setup.")

    return app_commands.check(predicate)


def outranks(actor: discord.Member, target: discord.Member) -> bool:
    if actor.guild_permissions.administrator:
        return True
    return authority(actor) > current_rank(target)


# ======================================================================
# Leaderboard
# ======================================================================
leaderboard_lock = asyncio.Lock()


def build_leaderboard_embed() -> discord.Embed:
    rows = db.leaderboard(config.LEADERBOARD_SIZE)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        place = medals[i] if i < 3 else f"**{i + 1}.**"
        label = row["city_name"] or row["display_name"] or "Unknown"
        tag = f" `{row['city_id']}`" if row["city_id"] else ""
        lines.append(f"{place} <@{row['user_id']}> — **{money(row['total'])}** ({label}{tag})")

    embed = discord.Embed(
        title=config.LEADERBOARD_TITLE,
        description="\n".join(lines) or "No sales logged yet.",
        color=config.LEADERBOARD_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Top {config.LEADERBOARD_SIZE} • Updates automatically whenever a sale is logged")
    return embed


async def refresh_leaderboard(guild: discord.Guild):
    """Edit the leaderboard message in place (or re-post it if it was deleted)."""
    channel_id = setting_id("leaderboard_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return
    async with leaderboard_lock:
        embed = build_leaderboard_embed()
        message_id = setting_id("leaderboard_message_id")
        try:
            if message_id:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
                return
        except (discord.NotFound, discord.Forbidden):
            pass
        message = await channel.send(embed=embed)
        db.set_setting("leaderboard_message_id", message.id)


async def post_leaderboard(guild: discord.Guild, channel: discord.TextChannel):
    """Post a fresh leaderboard in `channel`, deleting the old one."""
    old_channel_id = setting_id("leaderboard_channel_id")
    old_message_id = setting_id("leaderboard_message_id")
    if old_channel_id and old_message_id:
        old_channel = guild.get_channel(old_channel_id)
        if old_channel:
            try:
                old = await old_channel.fetch_message(old_message_id)
                await old.delete()
            except discord.HTTPException:
                pass
    message = await channel.send(embed=build_leaderboard_embed())
    db.set_setting("leaderboard_channel_id", channel.id)
    db.set_setting("leaderboard_message_id", message.id)


# ======================================================================
# Bot
# ======================================================================
class TeamDawn(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=config.BOT_STATUS))
        for guild in self.guilds:
            await refresh_leaderboard(guild)


bot = TeamDawn()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        message = str(error)
    else:
        log.exception("Command error", exc_info=error)
        message = "Something went wrong running that command. Check the bot's console for details."
    if interaction.response.is_done():
        await interaction.followup.send(f"❌ {message}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class ConfirmView(discord.ui.View):
    """Yes / Cancel buttons that only the command user can press."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, clear it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.edit_message(content="⏳ Clearing…", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.edit_message(content="Cancelled — nothing was changed.", view=None)
        self.stop()


# ----------------------------------------------------------------------
# /sale
# ----------------------------------------------------------------------
@bot.tree.command(name="sale", description="Log a sale in KNG ESTATE")
@app_commands.guild_only()
@app_commands.describe(
    client_id="Client ID / reference for this sale (free text — for record keeping only)",
    item="What was sold",
    sale_amount="Sale amount (numbers only, e.g. 51.12)",
    proof="Screenshot proof of the sale",
)
async def sale(
    interaction: discord.Interaction,
    client_id: app_commands.Range[str, 1, 100],
    item: app_commands.Range[str, 1, 200],
    sale_amount: app_commands.Range[float, 0.01, 1_000_000.0],
    proof: discord.Attachment,
):
    member = interaction.user
    if not member.guild_permissions.administrator:
        raw = db.get_setting("sale_role")
        if raw == "anyone":
            role, needed = None, False
        elif raw:
            role, needed = interaction.guild.get_role(int(raw)), True
        else:
            name = config.SALE_REQUIRED_ROLE
            role, needed = (discord.utils.get(interaction.guild.roles, name=name) if name else None), bool(name)
        if needed and (role is None or role not in member.roles):
            label = role.mention if role else "the sales role"
            raise app_commands.CheckFailure(f"You need {label} to log sales.")
    if proof.content_type and not proof.content_type.startswith("image/"):
        raise app_commands.CheckFailure("Proof must be an image (PNG, JPG, GIF or WEBP).")

    await interaction.response.defer()

    ext = os.path.splitext(proof.filename)[1].lower() or ".png"
    file = await proof.to_file(filename=f"proof{ext}")

    embed = discord.Embed(title=config.SALE_TITLE, color=config.SALE_COLOR, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Client ID", value=client_id, inline=True)
    embed.add_field(name="Item", value=item, inline=True)
    embed.add_field(name="Amount", value=money(sale_amount), inline=True)
    embed.add_field(name="Logged by", value=member.mention, inline=False)
    embed.set_image(url=f"attachment://{file.filename}")

    message = await interaction.followup.send(embed=embed, file=file, wait=True)

    db.ensure_member(member.id, member.display_name)
    db.add_sale(member.id, client_id, item, float(sale_amount), message.id)
    await refresh_leaderboard(interaction.guild)


# ----------------------------------------------------------------------
# /recruit
# ----------------------------------------------------------------------
class RecruitRoleSelect(discord.ui.Select):
    def __init__(self, roles: list[discord.Role]):
        options = [discord.SelectOption(label=r.name[:100], value=str(r.id)) for r in roles]
        super().__init__(
            placeholder="Choose the roles to give…",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish(interaction, [int(v) for v in self.values])


class RecruitView(discord.ui.View):
    def __init__(self, recruiter, person, city_name, city_id, roles):
        super().__init__(timeout=180)
        self.recruiter, self.person = recruiter, person
        self.city_name, self.city_id = city_name, city_id
        self.add_item(RecruitRoleSelect(roles))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.recruiter.id:
            await interaction.response.send_message("Only the person who ran /recruit can pick roles.", ephemeral=True)
            return False
        return True

    async def finish(self, interaction: discord.Interaction, chosen_ids: list[int]):
        await interaction.response.edit_message(content="⏳ Setting up recruit…", view=None)
        self.stop()

        guild, person = interaction.guild, self.person
        reason = f"Recruited by {self.recruiter}"
        warnings, given = [], []

        # 1) roles (chosen roles + the lowest rank role)
        roles = [r for r in (guild.get_role(i) for i in chosen_ids) if r]
        if config.GIVE_START_RANK_ROLE:
            start_role = rank_role(guild, RANKS[0])
            if start_role:
                roles.append(start_role)
            else:
                warnings.append(f"No role is linked to **{RANKS[0]}** — use `/setup rank`.")
        try:
            if roles:
                await person.add_roles(*roles, reason=reason)
                given = [r.mention for r in roles]
        except discord.Forbidden:
            warnings.append("I couldn't give roles (move my role higher in Server Settings → Roles).")

        # 2) nickname
        nick = build_nick(RANKS[0], self.city_name, self.city_id)
        warnings += await set_nick(person, nick, reason)

        # 3) channel
        channel = None
        try:
            category = await category_for(guild, "recruit_category_id", config.RECRUIT_CATEGORY_NAME)
            name = channel_name(self.city_name, self.city_id)
            channel = discord.utils.get(category.text_channels, name=name)
            if channel is None:
                channel = await category.create_text_channel(name, reason=reason)
            await channel.set_permissions(
                person, view_channel=True, send_messages=True, attach_files=True, read_message_history=True
            )
            await channel.send(f"🦋 Welcome to **Team Dawn**, {person.mention}! This is your channel.")
        except discord.Forbidden:
            warnings.append("I couldn't create their channel (I need the Manage Channels permission).")

        # 4) save + leaderboard
        db.recruit_member(person.id, person.display_name, self.city_name, self.city_id, channel.id if channel else None)
        await refresh_leaderboard(guild)

        embed = discord.Embed(title="🦋 New Recruit", color=config.INFO_COLOR)
        embed.add_field(name="Member", value=person.mention, inline=True)
        embed.add_field(name="Nickname", value=nick, inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "—", inline=True)
        embed.add_field(name="Roles", value=", ".join(given) or "—", inline=False)
        embed.add_field(name="Recruited by", value=self.recruiter.mention, inline=False)
        if warnings:
            embed.add_field(name="⚠️ Warnings", value="\n".join(warnings), inline=False)

        await interaction.edit_original_response(content="✅ Done!")
        await interaction.followup.send(embed=embed)


@bot.tree.command(name="recruit", description="Recruit someone into Team Dawn")
@app_commands.guild_only()
@min_rank("recruit")
@app_commands.describe(person="Tag the person to recruit", city_name="Their city name", city_id="Their city ID")
async def recruit(
    interaction: discord.Interaction,
    person: discord.Member,
    city_name: app_commands.Range[str, 1, 32],
    city_id: app_commands.Range[str, 1, 16],
):
    if person.bot:
        raise app_commands.CheckFailure("You can't recruit a bot.")
    roles = recruit_roles(interaction.guild)
    if not roles:
        raise app_commands.CheckFailure("No recruit roles are set up yet — an admin needs to run `/setup recruit_roles`.")
    view = RecruitView(interaction.user, person, city_name.strip(), city_id.strip(), roles)
    await interaction.response.send_message(f"Pick the roles to give {person.mention}:", view=view, ephemeral=True)


# ----------------------------------------------------------------------
# /promote and /demote
# ----------------------------------------------------------------------
async def change_rank(interaction: discord.Interaction, person: discord.Member, chosen: Optional[str], step: int):
    actor = interaction.user
    old = current_rank(person)
    new = rank_index(chosen) if chosen else old + step
    word = "promote" if step > 0 else "demote"

    if person.id == actor.id and not actor.guild_permissions.administrator:
        raise app_commands.CheckFailure(f"You can't {word} yourself.")
    if not 0 <= new < len(RANKS):
        raise app_commands.CheckFailure(
            f"{person.mention} is already **{RANKS[old]}** — they can't go any {'higher' if step > 0 else 'lower'}."
        )
    if step > 0 and new <= old:
        raise app_commands.CheckFailure(f"**{RANKS[new]}** isn't higher than their current rank (**{RANKS[old]}**).")
    if step < 0 and new >= old:
        raise app_commands.CheckFailure(f"**{RANKS[new]}** isn't lower than their current rank (**{RANKS[old]}**).")
    if not actor.guild_permissions.administrator:
        mine = authority(actor)
        if mine <= old or mine <= new:
            raise app_commands.CheckFailure("You can only change the rank of people below you, to a rank below yours.")

    await interaction.response.defer()
    reason = f"{word.title()}d by {actor}"
    warnings = await set_rank_roles(person, RANKS[new], reason)
    nick = nick_with_rank(person, RANKS[new])
    warnings += await set_nick(person, nick, reason)

    arrow = "⬆️" if step > 0 else "⬇️"
    embed = discord.Embed(
        title=f"{arrow} {word.title()}d",
        description=f"{person.mention}: **{RANKS[old]}** → **{RANKS[new]}**",
        color=config.SALE_COLOR if step > 0 else 0xED4245,
    )
    embed.add_field(name="New nickname", value=nick, inline=False)
    embed.add_field(name="By", value=actor.mention, inline=False)
    if warnings:
        embed.add_field(name="⚠️ Warnings", value="\n".join(warnings), inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="promote", description="Promote someone up the chain of command")
@app_commands.guild_only()
@min_rank("promote")
@app_commands.describe(person="Who to promote", rank="Rank to promote to (leave empty for the next rank up)")
@app_commands.choices(rank=RANK_CHOICES)
async def promote(interaction: discord.Interaction, person: discord.Member, rank: Optional[app_commands.Choice[str]] = None):
    await change_rank(interaction, person, rank.value if rank else None, +1)


@bot.tree.command(name="demote", description="Demote someone down the chain of command")
@app_commands.guild_only()
@min_rank("promote")
@app_commands.describe(person="Who to demote", rank="Rank to demote to (leave empty for the next rank down)")
@app_commands.choices(rank=RANK_CHOICES)
async def demote(interaction: discord.Interaction, person: discord.Member, rank: Optional[app_commands.Choice[str]] = None):
    await change_rank(interaction, person, rank.value if rank else None, -1)


# ----------------------------------------------------------------------
# /remove
# ----------------------------------------------------------------------
@bot.tree.command(name="remove", description="Remove someone from Team Dawn (kicks them from the server)")
@app_commands.guild_only()
@min_rank("remove")
@app_commands.describe(person="Who to remove", reason="Why they are being removed")
async def remove(interaction: discord.Interaction, person: discord.Member, reason: Optional[str] = None):
    actor, guild = interaction.user, interaction.guild
    if person.id == actor.id:
        raise app_commands.CheckFailure("You can't remove yourself.")
    if person.bot:
        raise app_commands.CheckFailure("You can't remove a bot with this command.")
    if not outranks(actor, person):
        raise app_commands.CheckFailure("You can only remove people ranked below you.")

    await interaction.response.defer()
    reason = reason or "No reason given"
    audit = f"Removed by {actor}: {reason}"
    steps, warnings = [], []

    # 1) move their channel to the removed category
    row = db.get_member(person.id)
    channel = guild.get_channel(row["channel_id"]) if row and row["channel_id"] else None
    if channel is None and row and row["city_name"] and row["city_id"]:
        channel = discord.utils.get(guild.text_channels, name=channel_name(row["city_name"], row["city_id"]))
    if channel:
        try:
            removed_cat = await category_for(guild, "removed_category_id", config.REMOVED_CATEGORY_NAME)
            await channel.edit(category=removed_cat, reason=audit)
            await channel.set_permissions(person, overwrite=None, reason=audit)
            steps.append(f"Moved {channel.mention} to **{removed_cat.name}**")
        except discord.Forbidden:
            warnings.append("I couldn't move their channel (I need Manage Channels).")
    else:
        warnings.append("I couldn't find a channel for them.")

    # 2) take them off the leaderboard
    db.deactivate_member(person.id)
    steps.append("Removed from the leaderboard")

    # 3) kick
    try:
        try:
            await person.send(f"You have been removed from **Team Dawn** in **{guild.name}**.\nReason: {reason}")
        except discord.HTTPException:
            pass  # DMs closed
        await person.kick(reason=audit)
        steps.append("Kicked from the server")
    except discord.Forbidden:
        warnings.append("I couldn't kick them (they may be above my role, or I lack Kick Members).")

    await refresh_leaderboard(guild)

    embed = discord.Embed(title="🚫 Member Removed", color=0xED4245, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Member", value=f"{person.mention} ({person})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Done", value="\n".join(f"✅ {s}" for s in steps) or "—", inline=False)
    if warnings:
        embed.add_field(name="⚠️ Warnings", value="\n".join(warnings), inline=False)
    embed.set_footer(text=f"Removed by {actor.display_name}")
    await interaction.followup.send(embed=embed)


# ----------------------------------------------------------------------
# /clear
# ----------------------------------------------------------------------
@bot.tree.command(name="clear", description="Clear someone's sales and remove them from the leaderboard")
@app_commands.guild_only()
@min_rank("clear")
@app_commands.describe(person="Tag the person to clear")
async def clear(interaction: discord.Interaction, person: discord.User):
    count, total = db.sales_summary(person.id)
    row = db.get_member(person.id)
    if count == 0 and not (row and row["active"]):
        raise app_commands.CheckFailure(f"{person.mention} has no sales and isn't on the leaderboard.")

    view = ConfirmView(interaction.user.id)
    await interaction.response.send_message(
        f"⚠️ This will **delete {count} sale(s) totalling {money(total)}** for {person.mention} "
        "and remove them from the leaderboard. This can't be undone.",
        view=view,
        ephemeral=True,
    )
    await view.wait()
    if view.value is None:
        await interaction.edit_original_response(content="Timed out — nothing was changed.", view=None)
        return
    if not view.value:
        return

    db.clear_member(person.id)
    await refresh_leaderboard(interaction.guild)
    await interaction.edit_original_response(content="✅ Cleared.")

    embed = discord.Embed(title="🧹 Sales Cleared", color=0xED4245, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Member", value=person.mention, inline=True)
    embed.add_field(name="Sales deleted", value=f"{count} ({money(total)})", inline=True)
    embed.add_field(name="By", value=interaction.user.mention, inline=False)
    await interaction.followup.send(embed=embed)


# ----------------------------------------------------------------------
# /leaderboard
# ----------------------------------------------------------------------
@bot.tree.command(name="leaderboard", description="Post the live sales leaderboard in a channel")
@app_commands.guild_only()
@min_rank("leaderboard")
@app_commands.describe(channel="Leaderboard channel (defaults to this channel)")
async def leaderboard(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    await interaction.response.defer(ephemeral=True)
    await post_leaderboard(interaction.guild, channel)
    await interaction.followup.send(f"✅ Leaderboard posted in {channel.mention}. It will update on every /sale.", ephemeral=True)


# ======================================================================
# /setup — admins link the bot to their existing roles, categories & permissions
# ======================================================================
setup_group = app_commands.Group(
    name="setup",
    description="Configure Team Dawn (admins only)",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)


def hierarchy_warning(guild: discord.Guild, role: discord.Role) -> str:
    if role >= guild.me.top_role:
        return (f"\n⚠️ {role.mention} is above my highest role, so I can't give or remove it. "
                "Drag the Team Dawn bot role above it in Server Settings → Roles.")
    return ""


@setup_group.command(name="rank", description="Link one of your existing roles to a rank")
@admin_only()
@app_commands.describe(rank="The rank in the chain of command", role="Your server role for that rank")
@app_commands.choices(rank=RANK_CHOICES)
async def setup_rank(interaction: discord.Interaction, rank: app_commands.Choice[str], role: discord.Role):
    db.set_setting(f"rank_role:{rank.value}", role.id)
    await interaction.response.send_message(
        f"✅ **{rank.value}** is now linked to {role.mention}." + hierarchy_warning(interaction.guild, role),
        ephemeral=True,
    )


@setup_group.command(name="recruit_roles", description="Choose the roles offered in the /recruit dropdown")
@admin_only()
@app_commands.describe(
    role1="Role to offer", role2="Role to offer", role3="Role to offer", role4="Role to offer", role5="Role to offer"
)
async def setup_recruit_roles(
    interaction: discord.Interaction,
    role1: discord.Role,
    role2: Optional[discord.Role] = None,
    role3: Optional[discord.Role] = None,
    role4: Optional[discord.Role] = None,
    role5: Optional[discord.Role] = None,
):
    roles = list(dict.fromkeys(r for r in (role1, role2, role3, role4, role5) if r))
    db.set_setting("recruit_roles", ",".join(str(r.id) for r in roles))
    warnings = "".join(hierarchy_warning(interaction.guild, r) for r in roles)
    await interaction.response.send_message(
        "✅ /recruit will offer: " + ", ".join(r.mention for r in roles) + warnings, ephemeral=True
    )


@setup_group.command(name="sale_role", description="Choose who can use /sale (leave empty to let anyone)")
@admin_only()
@app_commands.describe(role="Role needed to log sales — leave empty so anyone can")
async def setup_sale_role(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    db.set_setting("sale_role", role.id if role else "anyone")
    text = f"Only members with {role.mention} can use /sale." if role else "Anyone can use /sale."
    await interaction.response.send_message(f"✅ {text}", ephemeral=True)


@setup_group.command(name="recruit_category", description="Category where /recruit creates new channels")
@admin_only()
async def setup_recruit_category(interaction: discord.Interaction, category: discord.CategoryChannel):
    db.set_setting("recruit_category_id", category.id)
    await interaction.response.send_message(f"✅ New recruit channels will go in **{category.name}**.", ephemeral=True)


@setup_group.command(name="removed_category", description="Category that /remove moves channels into")
@admin_only()
async def setup_removed_category(interaction: discord.Interaction, category: discord.CategoryChannel):
    db.set_setting("removed_category_id", category.id)
    await interaction.response.send_message(f"✅ Removed members' channels will go in **{category.name}**.", ephemeral=True)


@setup_group.command(name="leaderboard_channel", description="Choose the leaderboard channel and post the board there")
@admin_only()
async def setup_leaderboard_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    await post_leaderboard(interaction.guild, channel)
    await interaction.followup.send(f"✅ Leaderboard posted in {channel.mention}.", ephemeral=True)


@setup_group.command(name="permission", description="Set the lowest rank that can use a staff command")
@admin_only()
@app_commands.describe(command="Which command", min_rank="Lowest rank allowed to use it")
@app_commands.choices(
    command=[app_commands.Choice(name=label, value=key) for key, label in PERMISSION_LABELS.items()],
    min_rank=RANK_CHOICES,
)
async def setup_permission(
    interaction: discord.Interaction, command: app_commands.Choice[str], min_rank: app_commands.Choice[str]
):
    db.set_setting(f"min_rank:{command.value}", min_rank.value)
    await interaction.response.send_message(
        f"✅ **{command.name}** can now be used by **{min_rank.value}** and above (plus admins).", ephemeral=True
    )


@setup_group.command(name="view", description="Show the current Team Dawn settings")
@admin_only()
async def setup_view(interaction: discord.Interaction):
    guild = interaction.guild

    def show_role(role):
        return role.mention if role else "❌ not set"

    ranks = "\n".join(f"**{r}** → {show_role(rank_role(guild, r))}" for r in RANKS)
    recruit = ", ".join(r.mention for r in recruit_roles(guild)) or "❌ not set"

    raw = db.get_setting("sale_role")
    if raw == "anyone":
        sale_text = "Anyone"
    elif raw:
        sale_text = show_role(guild.get_role(int(raw)))
    else:
        sale_text = show_role(discord.utils.get(guild.roles, name=config.SALE_REQUIRED_ROLE)) if config.SALE_REQUIRED_ROLE else "Anyone"

    def show_cat(key, default):
        cat = guild.get_channel(setting_id(key) or 0)
        return f"**{cat.name}**" if cat else f"**{default}** (default — made automatically)"

    lb = guild.get_channel(setting_id("leaderboard_channel_id") or 0)
    perms = "\n".join(f"{PERMISSION_LABELS[k]} → **{required_rank(k)}+**" for k in PERMISSION_DEFAULTS)

    embed = discord.Embed(title="⚙️ Team Dawn Settings", color=config.INFO_COLOR)
    embed.add_field(name="Rank roles  (/setup rank)", value=ranks, inline=False)
    embed.add_field(name="Recruit roles  (/setup recruit_roles)", value=recruit, inline=False)
    embed.add_field(name="Sale role  (/setup sale_role)", value=sale_text, inline=False)
    embed.add_field(name="Recruit category", value=show_cat("recruit_category_id", config.RECRUIT_CATEGORY_NAME), inline=True)
    embed.add_field(name="Removed category", value=show_cat("removed_category_id", config.REMOVED_CATEGORY_NAME), inline=True)
    embed.add_field(name="Leaderboard channel", value=lb.mention if lb else "❌ not set", inline=True)
    embed.add_field(name="Permissions  (/setup permission)", value=perms, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(setup_group)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing — add it to your .env file or your host's Startup variables.")
    bot.run(TOKEN)
