"""
Team Dawn — KNG ESTATE sales bot
Commands: /sale  /recruit  /promote  /demote  /remove  /leaderboard  /setup
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

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("teamdawn")

db = Database(config.DATABASE_FILE)
RANKS = config.RANKS
ADMIN_LEVEL = len(RANKS)  # administrators outrank everyone


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
    levels = [rank_index(r.name) for r in member.roles if r.name in RANKS]
    return max(levels, default=-1)


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


async def get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name, reason="Team Dawn bot")
    return category


async def set_rank_roles(member: discord.Member, rank: str, reason: str) -> list[str]:
    """Give the member the new rank role and take away the old ones. Returns warnings."""
    warnings = []
    new_role = discord.utils.get(member.guild.roles, name=rank)
    old_roles = [r for r in member.roles if r.name in RANKS and r.name != rank]
    try:
        if old_roles:
            await member.remove_roles(*old_roles, reason=reason)
        if new_role and new_role not in member.roles:
            await member.add_roles(new_role, reason=reason)
    except discord.Forbidden:
        warnings.append("I couldn't change their rank roles (move my role higher in Server Settings → Roles).")
    if new_role is None:
        warnings.append(f"There is no role called **{rank}** in this server, so only the nickname was changed.")
    return warnings


async def set_nick(member: discord.Member, nick: str, reason: str) -> list[str]:
    try:
        await member.edit(nick=nick, reason=reason)
        return []
    except discord.Forbidden:
        return ["I couldn't change their nickname (they may be the server owner or above my role)."]


def min_rank(rank: str):
    needed = rank_index(rank)

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("This command only works inside the server.")
        if authority(interaction.user) >= needed:
            return True
        raise app_commands.CheckFailure(f"You need **{rank}** or higher to use this command.")

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
    channel_id = db.get_setting("leaderboard_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return
    async with leaderboard_lock:
        embed = build_leaderboard_embed()
        message_id = db.get_setting("leaderboard_message_id")
        try:
            if message_id:
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=embed)
                return
        except (discord.NotFound, discord.Forbidden):
            pass
        message = await channel.send(embed=embed)
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
    if config.SALE_REQUIRED_ROLE and not member.guild_permissions.administrator:
        if not any(r.name == config.SALE_REQUIRED_ROLE for r in member.roles):
            raise app_commands.CheckFailure(f"You need the **{config.SALE_REQUIRED_ROLE}** role to log sales.")
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
    def __init__(self):
        options = [discord.SelectOption(label=name, value=name) for name in config.RECRUIT_ROLE_CHOICES]
        super().__init__(
            placeholder="Choose the roles to give…",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.finish(interaction, self.values)


class RecruitView(discord.ui.View):
    def __init__(self, recruiter: discord.Member, person: discord.Member, city_name: str, city_id: str):
        super().__init__(timeout=180)
        self.recruiter, self.person = recruiter, person
        self.city_name, self.city_id = city_name, city_id
        self.add_item(RecruitRoleSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.recruiter.id:
            await interaction.response.send_message("Only the person who ran /recruit can pick roles.", ephemeral=True)
            return False
        return True

    async def finish(self, interaction: discord.Interaction, chosen: list[str]):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⏳ Setting up recruit…", view=self)
        self.stop()

        guild, person = interaction.guild, self.person
        reason = f"Recruited by {self.recruiter}"
        warnings, given = [], []

        # 1) roles
        role_names = list(chosen)
        if config.GIVE_START_RANK_ROLE:
            role_names.append(RANKS[0])
        roles = []
        for name in role_names:
            role = discord.utils.get(guild.roles, name=name)
            if role:
                roles.append(role)
            else:
                warnings.append(f"Role **{name}** doesn't exist in this server.")
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
            category = await get_or_create_category(guild, config.RECRUIT_CATEGORY_NAME)
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

        await interaction.edit_original_response(content="✅ Done!", view=None)
        await interaction.followup.send(embed=embed)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.tree.command(name="recruit", description="Recruit someone into Team Dawn")
@app_commands.guild_only()
@min_rank(config.RECRUIT_MIN_RANK)
@app_commands.describe(person="Tag the person to recruit", city_name="Their city name", city_id="Their city ID")
async def recruit(
    interaction: discord.Interaction,
    person: discord.Member,
    city_name: app_commands.Range[str, 1, 32],
    city_id: app_commands.Range[str, 1, 16],
):
    if person.bot:
        raise app_commands.CheckFailure("You can't recruit a bot.")
    view = RecruitView(interaction.user, person, city_name.strip(), city_id.strip())
    await interaction.response.send_message(
        f"Pick the roles to give {person.mention}:", view=view, ephemeral=True
    )


# ----------------------------------------------------------------------
# /promote and /demote
# ----------------------------------------------------------------------
RANK_CHOICES = [app_commands.Choice(name=r, value=r) for r in RANKS]


async def change_rank(interaction: discord.Interaction, person: discord.Member, chosen: Optional[str], step: int):
    actor = interaction.user
    old = current_rank(person)
    new = rank_index(chosen) if chosen else old + step
    word = "promote" if step > 0 else "demote"

    if person.id == actor.id and not actor.guild_permissions.administrator:
        raise app_commands.CheckFailure(f"You can't {word} yourself.")
    if not 0 <= new < len(RANKS):
        raise app_commands.CheckFailure(f"{person.mention} is already **{RANKS[old]}** — they can't go any {'higher' if step > 0 else 'lower'}.")
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
@min_rank(config.PROMOTE_MIN_RANK)
@app_commands.describe(person="Who to promote", rank="Rank to promote to (leave empty for the next rank up)")
@app_commands.choices(rank=RANK_CHOICES)
async def promote(interaction: discord.Interaction, person: discord.Member, rank: Optional[app_commands.Choice[str]] = None):
    await change_rank(interaction, person, rank.value if rank else None, +1)


@bot.tree.command(name="demote", description="Demote someone down the chain of command")
@app_commands.guild_only()
@min_rank(config.PROMOTE_MIN_RANK)
@app_commands.describe(person="Who to demote", rank="Rank to demote to (leave empty for the next rank down)")
@app_commands.choices(rank=RANK_CHOICES)
async def demote(interaction: discord.Interaction, person: discord.Member, rank: Optional[app_commands.Choice[str]] = None):
    await change_rank(interaction, person, rank.value if rank else None, -1)


# ----------------------------------------------------------------------
# /remove
# ----------------------------------------------------------------------
@bot.tree.command(name="remove", description="Remove someone from Team Dawn (kicks them from the server)")
@app_commands.guild_only()
@min_rank(config.REMOVE_MIN_RANK)
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

    # 1) move their channel to REMOVED
    row = db.get_member(person.id)
    channel = guild.get_channel(row["channel_id"]) if row and row["channel_id"] else None
    if channel is None and row and row["city_name"] and row["city_id"]:
        channel = discord.utils.get(guild.text_channels, name=channel_name(row["city_name"], row["city_id"]))
    if channel:
        try:
            removed_cat = await get_or_create_category(guild, config.REMOVED_CATEGORY_NAME)
            await channel.edit(category=removed_cat, reason=audit)
            await channel.set_permissions(person, overwrite=None, reason=audit)
            steps.append(f"Moved {channel.mention} to **{config.REMOVED_CATEGORY_NAME}**")
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
# /leaderboard
# ----------------------------------------------------------------------
@bot.tree.command(name="leaderboard", description="Post the live sales leaderboard in a channel")
@app_commands.guild_only()
@min_rank(config.LEADERBOARD_MIN_RANK)
@app_commands.describe(channel="Leaderboard channel (defaults to this channel)")
async def leaderboard(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    await interaction.response.defer(ephemeral=True)

    # delete the old board so there's only ever one
    old_channel_id = db.get_setting("leaderboard_channel_id")
    old_message_id = db.get_setting("leaderboard_message_id")
    if old_channel_id and old_message_id:
        old_channel = interaction.guild.get_channel(int(old_channel_id))
        if old_channel:
            try:
                old = await old_channel.fetch_message(int(old_message_id))
                await old.delete()
            except discord.HTTPException:
                pass

    message = await channel.send(embed=build_leaderboard_embed())
    db.set_setting("leaderboard_channel_id", channel.id)
    db.set_setting("leaderboard_message_id", message.id)
    await interaction.followup.send(f"✅ Leaderboard posted in {channel.mention}. It will update on every /sale.", ephemeral=True)


# ----------------------------------------------------------------------
# /setup  (admin only — creates any missing roles & categories)
# ----------------------------------------------------------------------
@bot.tree.command(name="setup", description="Create any missing Team Dawn roles and categories")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        raise app_commands.CheckFailure("Only server administrators can run /setup.")
    await interaction.response.defer(ephemeral=True)
    guild, created = interaction.guild, []
    for name in RANKS + config.RECRUIT_ROLE_CHOICES:
        if not discord.utils.get(guild.roles, name=name):
            await guild.create_role(name=name, reason="Team Dawn setup")
            created.append(f"role **{name}**")
    for name in (config.RECRUIT_CATEGORY_NAME, config.REMOVED_CATEGORY_NAME):
        if not discord.utils.get(guild.categories, name=name):
            await guild.create_category(name, reason="Team Dawn setup")
            created.append(f"category **{name}**")
    text = "Created: " + ", ".join(created) if created else "Everything already exists."
    await interaction.followup.send(
        f"✅ {text}\n\nReminder: drag the **Team Dawn** bot role *above* all the rank roles in "
        "Server Settings → Roles, otherwise it can't change roles or nicknames.",
        ephemeral=True,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing — copy .env.example to .env and fill it in.")
    bot.run(TOKEN)
