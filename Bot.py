import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import json
import os
import re
import aiohttp
import aiosqlite
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

COLOR_MAIN    = 0x0D2137
COLOR_SUCCESS = 0x1565A8
COLOR_DANGER  = 0xA63228
COLOR_WARNING = 0xC47D19
COLOR_INFO    = 0x1A4F82
COLOR_DARK    = 0x060F1C
COLOR_GOLD    = 0xAB8A0D
COLOR_PURPLE  = 0x2E1A6E
COLOR_LEAVE   = 0x3D5470
COLOR_TIKTOK  = 0xEE1D52
COLOR_OFFLINE = 0x5F5E5A

BOT_NAME   = "DLM Bot"
BOT_FOOTER = "DLM Corporation • Système de Gestion"

POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

ALLOWED_SETTINGS_COLUMNS = {
    "log_channel_id", "ban_log_channel_id", "welcome_channel_id",
    "leave_channel_id", "report_channel_id", "archive_channel_id",
    "antiraid_enabled", "antiraid_log_id", "mod_roles",
    "antiraid_threshold", "lockdown_active", "warn_log_channel_id",
    "live_notif_channel_id", "live_notif_role_id",
}

def sep():     return "━" * 32
def ts():      return f"<t:{int(datetime.now().timestamp())}:F>"
def ch(cid):   return f"<#{cid}>" if cid else "❌ Non configuré"
def role(rid): return f"<@&{rid}>"


def init_system():
    if not os.path.exists('config.json'):
        with open('config.json', 'w') as f:
            json.dump({
                "token": "000000000000000000",
                "prefix": "/",
                "twitch_client_id": "",
                "twitch_client_secret": ""
            }, f, indent=4)

    conn = sqlite3.connect('database.db')
    c    = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id              INTEGER PRIMARY KEY,
        log_channel_id        INTEGER,
        ban_log_channel_id    INTEGER,
        welcome_channel_id    INTEGER,
        leave_channel_id      INTEGER,
        report_channel_id     INTEGER,
        archive_channel_id    INTEGER,
        antiraid_enabled      INTEGER DEFAULT 0,
        antiraid_log_id       INTEGER,
        mod_roles             TEXT,
        antiraid_threshold    INTEGER DEFAULT 10,
        lockdown_active       INTEGER DEFAULT 0,
        warn_log_channel_id   INTEGER,
        live_notif_channel_id INTEGER,
        live_notif_role_id    INTEGER
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS pending_reports (
        message_id      INTEGER PRIMARY KEY,
        channel_id      INTEGER,
        guild_id        INTEGER,
        target_user_id  INTEGER,
        reporter_id     INTEGER,
        reason          TEXT,
        proof           TEXT,
        claimed_by      INTEGER DEFAULT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS warns (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      INTEGER,
        user_id       INTEGER,
        moderator_id  INTEGER,
        reason        TEXT,
        timestamp     TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS poll_votes (
        message_id  INTEGER,
        user_id     INTEGER,
        choice      INTEGER,
        PRIMARY KEY (message_id, user_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS live_streamers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER,
        platform    TEXT,
        username    TEXT,
        is_live     INTEGER DEFAULT 0,
        stream_id   TEXT,
        UNIQUE(guild_id, platform, username)
    )''')

    existing = [col[1] for col in c.execute("PRAGMA table_info(guild_settings)")]
    needed   = [
        ('log_channel_id',        'INTEGER'), ('ban_log_channel_id',  'INTEGER'),
        ('welcome_channel_id',    'INTEGER'), ('leave_channel_id',    'INTEGER'),
        ('report_channel_id',     'INTEGER'), ('archive_channel_id',  'INTEGER'),
        ('antiraid_enabled',      'INTEGER DEFAULT 0'), ('antiraid_log_id', 'INTEGER'),
        ('mod_roles',             'TEXT'),
        ('antiraid_threshold',    'INTEGER DEFAULT 10'),
        ('lockdown_active',       'INTEGER DEFAULT 0'),
        ('warn_log_channel_id',   'INTEGER'),
        ('live_notif_channel_id', 'INTEGER'),
        ('live_notif_role_id',    'INTEGER'),
    ]
    for col_name, col_type in needed:
        if col_name not in existing:
            c.execute(f"ALTER TABLE guild_settings ADD COLUMN {col_name} {col_type}")

    conn.commit()
    conn.close()

init_system()

with open('config.json', 'r') as f:
    config = json.load(f)


async def db():
    return await aiosqlite.connect('database.db')

async def get_settings(guild_id):
    conn = await db()
    c = await conn.execute(
        """
        SELECT guild_id, log_channel_id, ban_log_channel_id, welcome_channel_id,
               leave_channel_id, report_channel_id, archive_channel_id,
               antiraid_enabled, antiraid_log_id, mod_roles,
               antiraid_threshold, lockdown_active, warn_log_channel_id,
               live_notif_channel_id, live_notif_role_id
        FROM guild_settings WHERE guild_id = ?
        """, (guild_id,))
    row = await c.fetchone()
    await conn.close()
    if not row:
        return None
    keys = [
        "guild_id", "log_channel_id", "ban_log_channel_id", "welcome_channel_id",
        "leave_channel_id", "report_channel_id", "archive_channel_id",
        "antiraid_enabled", "antiraid_log_id", "mod_roles",
        "antiraid_threshold", "lockdown_active", "warn_log_channel_id",
        "live_notif_channel_id", "live_notif_role_id",
    ]
    return dict(zip(keys, row))

async def set_setting(guild_id, column, value):
    if column not in ALLOWED_SETTINGS_COLUMNS:
        raise ValueError(f"Colonne non autorisée : {column}")
    conn = await db()
    await conn.execute(f"UPDATE guild_settings SET {column} = ? WHERE guild_id = ?", (value, guild_id))
    await conn.commit()
    await conn.close()


async def is_premium(interaction: discord.Interaction) -> bool:
    try:
        for entitlement in interaction.entitlements:
            if entitlement.sku_id == PREMIUM_SKU_ID:
                return True
    except Exception:
        pass
    try:
        async for entitlement in bot.entitlements(guild=interaction.guild, skus=[PREMIUM_SKU_ID]):
            if not entitlement.is_expired():
                return True
    except Exception:
        pass
    return False


class DLMbot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix=config['prefix'], intents=intents)
        self.join_cache = {}

    async def setup_hook(self):
        self.add_view(ReportControlView(persistent_only=True))
        self.add_view(ReportDecisionView(persistent_only=True))
        await self.tree.sync()

    async def ensure_guild(self, guild_id):
        conn = await db()
        await conn.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
        await conn.commit()
        await conn.close()

bot = DLMbot()


async def check_is_staff(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if member and member.guild_permissions.administrator:
        return True
    s = await get_settings(interaction.guild_id)
    if s and s.get("mod_roles") and member:
        try:
            allowed    = json.loads(s["mod_roles"])
            user_roles = [r.id for r in member.roles]
            if any(rid in user_roles for rid in allowed):
                return True
        except json.JSONDecodeError:
            pass
    if not interaction.response.is_done():
        embed = discord.Embed(
            title="🔒Accès Refusé🔒",
            description="Vous ne possédez pas les permissions nécessaires.",
            color=COLOR_DANGER
        )
        embed.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    return False


async def send_log(guild: discord.Guild, embed: discord.Embed):
    s = await get_settings(guild.id)
    if s and s.get("log_channel_id"):
        chan = guild.get_channel(s["log_channel_id"])
        if isinstance(chan, discord.TextChannel):
            try:
                import asyncio
                asyncio.create_task(chan.send(embed=embed))
            except Exception:
                pass


async def build_config_embed(guild: discord.Guild) -> discord.Embed:
    try:
        await bot.ensure_guild(guild.id)
        s = await get_settings(guild.id)
        embed = discord.Embed(title="📊 Configuration Actuelle", color=COLOR_MAIN, timestamp=datetime.now())
        if not s:
            embed.description = "❌Aucune configuration trouvée❌"
            embed.set_footer(text=BOT_FOOTER)
            return embed

        embed.add_field(
            name="📍 Salons Système",
            value=(
                f"💎 **Logs :** {ch(s.get('log_channel_id'))}\n"
                f"🎉 **Bienvenue :** {ch(s.get('welcome_channel_id'))}\n"
                f"👋 **Départs :** {ch(s.get('leave_channel_id'))}"
            ), inline=True
        )
        embed.add_field(
            name="🔨 Salons Modération",
            value=(
                f"🔨 **Logs Bans :** {ch(s.get('ban_log_channel_id'))}\n"
                f"🚨 **Signalements :** {ch(s.get('report_channel_id'))}\n"
                f"⚠️ **Warns :** {ch(s.get('warn_log_channel_id'))}\n"
                f"📁 **Archives :** {ch(s.get('archive_channel_id'))}"
            ), inline=True
        )

        threshold     = s.get('antiraid_threshold') or 10
        raid_status   = "🟢 **Activé**" if s.get('antiraid_enabled') else "🔴 **Désactivé**"
        lockdown_st   = "⚠️ **ACTIF**"  if s.get('lockdown_active') else "✅ Normal"
        embed.add_field(
            name="🛡️ Anti-Raid",
            value=(
                f"**Statut :** {raid_status}\n"
                f"**Seuil :** {threshold} membres/s\n"
                f"**Lockdown :** {lockdown_st}\n"
                f"🚨 **Alertes :** {ch(s.get('antiraid_log_id'))}"
            ), inline=True
        )

        live_role_str = f"<@&{s.get('live_notif_role_id')}>" if s.get("live_notif_role_id") else "❌ Non configuré"
        embed.add_field(
            name="🔴 Notifications Live",
            value=(
                f"📺 **Salon :** {ch(s.get('live_notif_channel_id'))}\n"
                f"🔔 **Rôle   :** {live_role_str}"
            ), inline=True
        )

        nb_roles   = 0
        roles_text = "❌ Aucun rôle configuré"
        if s.get("mod_roles"):
            try:
                role_ids = json.loads(s.get("mod_roles"))
                nb_roles = len(role_ids)
                if role_ids:
                    lines = []
                    for i, rid in enumerate(role_ids):
                        r      = guild.get_role(rid)
                        prefix = "┗" if i == len(role_ids) - 1 else "┣"
                        lines.append(f"{prefix} {r.mention if r else f'`ID:{rid}` *(supprimé)*'}")
                    roles_text = "\n".join(lines)
            except Exception:
                roles_text = "❌ Erreur de lecture"
        embed.add_field(name=f"🛡️ Rôles Modérateurs ({nb_roles})", value=roles_text, inline=False)

        conn = await db()
        c = await conn.execute("SELECT COUNT(*) FROM pending_reports WHERE guild_id = ?", (guild.id,))
        pending_row = await c.fetchone()
        pending = pending_row[0] if pending_row else 0
        c = await conn.execute("SELECT COUNT(*) FROM warns WHERE guild_id = ?", (guild.id,))
        total_warns_row = await c.fetchone()
        total_warns = total_warns_row[0] if total_warns_row else 0
        c = await conn.execute(
            "SELECT COUNT(*) FROM live_streamers WHERE guild_id = ?", (guild.id,)
        )
        live_count_row = await c.fetchone()
        live_count = live_count_row[0] if live_count_row else 0
        await conn.close()

        embed.add_field(
            name="📋Statistiques📋",
            value=(
                f"**{pending}** signalement(s) en attente\n"
                f"**{total_warns}** avertissement(s) enregistré(s)\n"
                f"**{live_count}** streamer(s) surveillé(s)"
            ), inline=False
        )

        embed.set_footer(text=f"Serveur : {guild.name} • {BOT_FOOTER}")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed
    except Exception as e:
        embed = discord.Embed(title="❌ Erreur", description=f"```{e}```", color=COLOR_DANGER)
        embed.set_footer(text=BOT_FOOTER)
        return embed


class ReportVerdictModal(ui.Modal, title="⚖️ Décision de Modération"):
    verdict_input = ui.TextInput(
        label="Note officielle du modérateur",
        style=discord.TextStyle.paragraph,
        placeholder="Détaillez ici la raison de votre décision...",
        required=True, min_length=10, max_length=500
    )

    def __init__(self, target_user_id, reporter_id, status, reason, proof, message_id):
        super().__init__()
        self.target_user_id    = target_user_id
        self.reporter_id       = reporter_id
        self.status            = status
        self.original_reason   = reason
        self.original_proof    = proof
        self.report_message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        s             = await get_settings(interaction.guild_id)
        is_sanction   = (self.status == "Sanctionné")
        color_verdict = COLOR_SUCCESS if is_sanction else COLOR_DANGER
        status_icon   = "✅" if is_sanction else "❌"
        status_label  = "**VALIDÉ — Sanction Appliquée**" if is_sanction else "**REFUSÉ — Dossier Clos**"
        target_user   = await bot.fetch_user(self.target_user_id)

        try:
            reporter  = await bot.fetch_user(self.reporter_id)
            dm_embed  = discord.Embed(title=f"{status_icon} Verdict de votre signalement", color=color_verdict, timestamp=datetime.now())
            dm_embed.description = (
                f"Votre signalement concernant **{target_user.display_name}** a été traité.\n\n"
                f"{sep()}\n**Décision finale**\n{status_label}\n\n"
                f"**Note du modérateur**\n> {self.verdict_input.value}"
            )
            dm_embed.set_thumbnail(url=target_user.display_avatar.url)
            dm_embed.set_footer(text=BOT_FOOTER, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            await reporter.send(embed=dm_embed)
        except Exception:
            pass

        if s and s.get("archive_channel_id") and interaction.guild:
            channel = interaction.guild.get_channel(s["archive_channel_id"])
            if isinstance(channel, discord.TextChannel):
                arch = discord.Embed(color=color_verdict, timestamp=datetime.now())
                arch.set_author(name=f"Verdict rendu par {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
                arch.title       = f"📁Dossier📁{self.status} — Archivé"
                arch.description = (
                    f"{sep()}\n**📋 Informations du Dossier**\n"
                    f"┣ 👤 **Signalé par :** <@{self.reporter_id}>\n"
                    f"┣ 🎯 **Membre visé :** {target_user.mention}\n"
                    f"┗ 📅 **Traité le :** {ts()}\n\n"
                    f"**💬 Motif**\n```{self.original_reason}```\n"
                    f"**📸 Preuve**\n> {self.original_proof}\n\n"
                    f"{sep()}\n**{status_icon} Décision : {status_label}**\n"
                    f"**Modérateur :** {interaction.user.mention}\n"
                    f"**Note :**\n> {self.verdict_input.value}"
                )
                arch.set_thumbnail(url=target_user.display_avatar.url)
                arch.set_footer(text=BOT_FOOTER)
                await channel.send(embed=arch)

        conn = await db()
        await conn.execute("DELETE FROM pending_reports WHERE message_id = ?", (self.report_message_id,))
        await conn.commit()
        await conn.close()

        try:
            s_data = await get_settings(interaction.guild_id)
            if s_data and s_data.get("report_channel_id") and interaction.guild:
                report_chan = interaction.guild.get_channel(s_data["report_channel_id"])
                if isinstance(report_chan, discord.TextChannel):
                    msg_to_delete = await report_chan.fetch_message(self.report_message_id)
                    await msg_to_delete.delete()
        except Exception:
            pass

        ok = discord.Embed(title="✅ Dossier Traité", description="Clos, archivé, plaignant notifié.", color=COLOR_SUCCESS)
        ok.set_footer(text=BOT_FOOTER)
        await interaction.followup.send(embed=ok, ephemeral=True)


class ReportDecisionView(ui.View):
    def __init__(self, persistent_only=False):
        super().__init__(timeout=None)
        self._persistent_only = persistent_only

    def _load_from_db(self, message_id):
        conn = sqlite3.connect('database.db'); c = conn.cursor()
        c.execute("SELECT target_user_id, reporter_id, reason, proof, claimed_by FROM pending_reports WHERE message_id = ?", (message_id,))
        row = c.fetchone(); conn.close()
        if row:
            return {"target_user_id": row[0], "reporter_id": row[1],
                    "reason": row[2], "proof": row[3], "claimed_by": row[4]}
        return None

    async def _handle_decision(self, interaction: discord.Interaction, status: str):
        data = self._load_from_db(interaction.message.id)
        if not data:
            e = discord.Embed(title="❌ Dossier Introuvable", description="Ce signalement n'existe plus en base de données.", color=COLOR_DANGER)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        if interaction.user.id != data["claimed_by"]:
            e = discord.Embed(title="🔒 Non Autorisé", description="Seul le modérateur assigné peut clore ce dossier.", color=COLOR_DANGER)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        await interaction.response.send_modal(
            ReportVerdictModal(
                data["target_user_id"], data["reporter_id"],
                status, data["reason"], data["proof"],
                interaction.message.id
            )
        )

    @ui.button(label="Valider & Sanctionner", style=discord.ButtonStyle.success, emoji="✅", custom_id="report:validate")
    async def validate(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_decision(interaction, "Sanctionné")

    @ui.button(label="Refuser & Clore", style=discord.ButtonStyle.danger, emoji="❌", custom_id="report:refuse")
    async def refuse(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_decision(interaction, "Refusé")


class ReportControlView(ui.View):
    def __init__(self, target_user_id=None, reporter_id=None, reason=None, proof=None, claimed_by=None, persistent_only=False):
        super().__init__(timeout=None)
        self.target_user_id   = target_user_id
        self.reporter_id      = reporter_id
        self.reason           = reason
        self.proof            = proof
        self.claimed_by       = claimed_by
        self._persistent_only = persistent_only

    def _load_from_db(self, message_id):
        conn = sqlite3.connect('database.db'); c = conn.cursor()
        c.execute("SELECT target_user_id, reporter_id, reason, proof, claimed_by FROM pending_reports WHERE message_id = ?", (message_id,))
        row = c.fetchone(); conn.close()
        if row:
            self.target_user_id, self.reporter_id, self.reason, self.proof, self.claimed_by = row

    @ui.button(label="Prendre en charge", style=discord.ButtonStyle.primary, emoji="🛡️", custom_id="report:claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: ui.Button):
        self._load_from_db(interaction.message.id)
        if self.claimed_by is not None:
            e = discord.Embed(title="⚠️ Déjà Pris en Charge", description=f"Traité par <@{self.claimed_by}>.", color=COLOR_WARNING)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        if not await check_is_staff(interaction): return

        self.claimed_by = interaction.user.id
        conn = await db()
        await conn.execute("UPDATE pending_reports SET claimed_by = ? WHERE message_id = ?", (interaction.user.id, interaction.message.id))
        await conn.commit(); await conn.close()

        try:
            reporter = await bot.fetch_user(self.reporter_id)
            notif    = discord.Embed(title="🔔Signalement en Cours🔔", color=COLOR_INFO)
            notif.description = f"Un modérateur a pris en charge votre signalement.\n**Modérateur :** {interaction.user.mention}"
            notif.set_footer(text=BOT_FOOTER)
            await reporter.send(embed=notif)
        except Exception:
            pass

        embed       = interaction.message.embeds[0]
        embed.color = COLOR_WARNING
        embed.set_footer(text=f"⚡Pris en charge par {interaction.user.display_name} • {BOT_FOOTER}")
        await interaction.response.edit_message(embed=embed, view=ReportDecisionView())


def build_report_embed(reporter, target, reason, proof):
    embed             = discord.Embed(title="📋Nouveau Signalement📋", color=COLOR_PURPLE, timestamp=datetime.now())
    embed.description = (
        f"{sep()}\n**👤 Parties Impliquées**\n"
        f"┣ **Lanceur :** {reporter.mention} (`{reporter.name}`)\n"
        f"┗ **Cible :** {target.mention} (`{target.name}`)\n\n"
        f"**💬 Motif**\n```{reason}```\n"
        f"**📸 Preuve**\n> {proof}\n{sep()}"
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_author(name=f"Signalement de {reporter.display_name}", icon_url=reporter.display_avatar.url)
    embed.set_footer(text=f"En attente de traitement • {BOT_FOOTER}")
    return embed


class PollView(ui.View):
    def __init__(self, message_id: int, question: str, options: list, author_name: str, author_avatar: str):
        super().__init__(timeout=None)
        self.message_id    = message_id
        self.question      = question
        self.options       = options
        self.author_name   = author_name
        self.author_avatar = author_avatar
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for i, opt in enumerate(self.options):
            btn = ui.Button(
                label=f"Option {i+1} : {opt[:40]}",
                style=discord.ButtonStyle.primary,
                emoji=POLL_EMOJIS[i],
                custom_id=f"poll:{self.message_id}:{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, choice_idx: int):
        async def callback(interaction: discord.Interaction):
            conn = await db()
            c = await conn.execute("SELECT choice FROM poll_votes WHERE message_id = ? AND user_id = ?",
                      (self.message_id, interaction.user.id))
            existing = await c.fetchone()
            if existing:
                await conn.close()
                e = discord.Embed(title="⚠️ Déjà Voté", description="Vous avez déjà participé à ce sondage.", color=COLOR_WARNING)
                e.set_footer(text=BOT_FOOTER)
                return await interaction.response.send_message(embed=e, ephemeral=True)
            await conn.execute("INSERT INTO poll_votes (message_id, user_id, choice) VALUES (?,?,?)",
                      (self.message_id, interaction.user.id, choice_idx))
            await conn.commit()
            await conn.close()
            await interaction.response.edit_message(embed=self._build_embed())
        return callback

    def _get_votes(self) -> dict:
        conn = sqlite3.connect('database.db'); c = conn.cursor()
        c.execute("SELECT choice, COUNT(*) FROM poll_votes WHERE message_id = ? GROUP BY choice", (self.message_id,))
        rows = c.fetchall(); conn.close()
        return {r[0]: r[1] for r in rows}

    def _bar(self, pct: float, n: int = 16) -> str:
        f = round(pct / 100 * n)
        return "█" * f + "░" * (n - f)

    def _build_embed(self) -> discord.Embed:
        votes = self._get_votes()
        total = sum(votes.values())
        embed = discord.Embed(title="🗳️ Sondage Actif", color=COLOR_MAIN, timestamp=datetime.now())
        lines = [f"**{self.question}**\n{sep()}\n"]
        for i, opt in enumerate(self.options):
            v   = votes.get(i, 0)
            pct = (v / total * 100) if total else 0
            lines.append(f"**{POLL_EMOJIS[i]}  {opt}**\n`{self._bar(pct)}` **{pct:.1f}%** ({v} vote{'s' if v!=1 else ''})\n")
        lines.append(f"\n{sep()}\n📊 **Total :** {total} vote{'s' if total!=1 else ''}")
        embed.description = "\n".join(lines)
        embed.set_author(name=f"Sondage par {self.author_name}", icon_url=self.author_avatar)
        embed.set_footer(text=f"Un seul vote par membre • {BOT_FOOTER}")
        return embed


class ThresholdModal(ui.Modal, title="⚙️ Seuil Anti-Raid"):
    value = ui.TextInput(
        label="Membres max par seconde avant déclenchement",
        placeholder="Ex: 10",
        required=True, min_length=1, max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value.value.strip()
        if not raw.isdigit():
            e = discord.Embed(title="❌ Valeur Invalide", description="Entrez un **nombre entier** (ex: `10`).", color=COLOR_DANGER)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        n = int(raw)
        if not 2 <= n <= 100:
            e = discord.Embed(title="❌ Valeur Hors Plage", description="Entrez un nombre entre **2** et **100**.", color=COLOR_DANGER)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        await set_setting(interaction.guild_id, "antiraid_threshold", n)
        e = discord.Embed(title="✅ Seuil Mis à Jour", description=f"Le seuil Anti-Raid est maintenant de **{n} membres/seconde**.", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)


def build_settings_main_embed() -> discord.Embed:
    embed             = discord.Embed(title="⚙️ Panneau de Configuration DLM", color=COLOR_MAIN, timestamp=datetime.now())
    embed.description = (
        f"Bienvenue dans le panneau de configuration.\n\n"
        f"{sep()}\n"
        f"🔨 **Modération** — Rôles staff, signalements, archives, logs bans\n"
        f"📍 **Système** — Logs généraux, bienvenue & départs\n"
        f"🛡️ **Anti-Raid** — Protection + seuil configurable + lockdown\n"
        f"🔴 **Notifications Live** — Twitch & TikTok\n"
        f"📊 **Voir la Config** — Affiche la configuration actuelle\n"
        f"{sep()}"
    )
    embed.set_footer(text=f"Accès restreint aux Administrateurs • {BOT_FOOTER}")
    return embed


class SettingsMainView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Modération", style=discord.ButtonStyle.primary, emoji="🔨", row=0)
    async def go_mod(self, interaction, button):
        e = discord.Embed(title="🔨 Configuration — Modération", color=COLOR_MAIN)
        e.description = f"Configurez les outils de modération.\n\n**Disponible :**\n┣ 🛡️ Rôles modérateurs\n┣ ⚠️ Salon logs warns\n┣ 🚨 Salon signalements\n┣ 📁 Salon archives\n┗ 🔨 Logs bans\n\n{sep()}"
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embed=e, view=SettingsModerationView())

    @ui.button(label="Système", style=discord.ButtonStyle.primary, emoji="📍", row=0)
    async def go_sys(self, interaction, button):
        e = discord.Embed(title="📍 Configuration — Système", color=COLOR_MAIN)
        e.description = f"Configurez les salons système.\n\n**Disponible :**\n┣ 💎 Logs généraux\n┣ 🎉 Bienvenue\n┗ 👋 Départs\n\n{sep()}"
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embed=e, view=SettingsSystemView())

    @ui.button(label="Anti-Raid", style=discord.ButtonStyle.danger, emoji="🛡️", row=0)
    async def go_raid(self, interaction, button):
        e = discord.Embed(title="🛡️ Configuration — Anti-Raid", color=COLOR_DANGER)
        e.description = (
            f"Protégez votre serveur contre les raids.\n\n"
            f"> Kick automatique si **X membres** rejoignent en **< 1 seconde**.\n"
            f"> Le lockdown bloque **tous** les nouveaux arrivants.\n\n"
            f"**Disponible :**\n┣ ✅ Activer / Désactiver\n┣ 🔢 Configurer le seuil\n┣ 🔒 Lockdown manuel\n┗ 🚨 Salon d'alertes\n\n{sep()}"
        )
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embed=e, view=SettingsRaidView())

    @ui.button(label="Notifications Live", style=discord.ButtonStyle.success, emoji="🔴", row=0)
    async def go_live(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            e = discord.Embed(title="🔒 Accès Refusé", description="Réservé aux **Administrateurs**.", color=COLOR_DANGER)
            e.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        s = await get_settings(interaction.guild_id)
        chan_str = ch(s.get("live_notif_channel_id")) if s else "❌ Non configuré"
        role_str = f"<@&{s.get('live_notif_role_id')}>" if s and s.get("live_notif_role_id") else "❌ Non configuré"
        e = discord.Embed(title="🔴 Configuration — Notifications Live", color=COLOR_MAIN)
        e.description = (
            f"Configurez les alertes Twitch & TikTok.\n\n"
            f"**Configuration actuelle :**\n"
            f"┣ 📺 **Salon :** {chan_str}\n"
            f"┗ 🔔 **Rôle  :** {role_str}\n\n"
            f"**Gestion des streamers :**\n"
            f"┣ `/twitch` — Ajouter/Supprimer/Lister\n"
            f"┗ `/tiktok` — Ajouter/Supprimer/Lister\n\n"
            f"{sep()}"
        )
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embed=e, view=SettingsLiveView())

    @ui.button(label="Voir la Config", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def view_cfg(self, interaction, button):
        embed = await build_config_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=SettingsConfigView())


class SettingsConfigView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="← Retour", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())

    @ui.button(label="Actualiser", style=discord.ButtonStyle.primary, emoji="🔄")
    async def refresh(self, interaction, button):
        embed = await build_config_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=SettingsConfigView())


class SettingsModerationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(cls=ui.RoleSelect, placeholder="🛡️ Rôles modérateurs (max 10)", min_values=1, max_values=10, row=0)
    async def set_mod_roles(self, interaction, select):
        await set_setting(interaction.guild_id, "mod_roles", json.dumps([r.id for r in select.values]))
        e = discord.Embed(title="✅ Rôles Staff", description=" • ".join(r.mention for r in select.values), color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="⚠️ Salon logs des warns", row=1)
    async def set_warn_log(self, interaction, select):
        await set_setting(interaction.guild_id, "warn_log_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Salon Warns", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="🚨 Salon signalements", row=2)
    async def set_report(self, interaction, select):
        await set_setting(interaction.guild_id, "report_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Salon Signalements", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="← Retour", style=discord.ButtonStyle.secondary, emoji="↩️", row=3)
    async def back(self, i, b):
        await i.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())

    @ui.button(label="Suite →", style=discord.ButtonStyle.primary, emoji="➡️", row=3)
    async def next_page(self, i, b):
        e = discord.Embed(title="🔨 Configuration — Modération (2/2)", color=COLOR_MAIN)
        e.description = f"Suite de la configuration modération.\n\n**Disponible :**\n┣ 📁 Salon archives\n┗ 🔨 Logs bans\n\n{sep()}"
        e.set_footer(text=BOT_FOOTER)
        await i.response.edit_message(embed=e, view=SettingsModerationView2())


class SettingsModerationView2(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(cls=ui.ChannelSelect, placeholder="📁 Salon archives", row=0)
    async def set_archive(self, interaction, select):
        await set_setting(interaction.guild_id, "archive_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Salon Archives", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="🔨 Logs de bans/débans", row=1)
    async def set_ban_log(self, interaction, select):
        await set_setting(interaction.guild_id, "ban_log_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Logs Bans", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="← Page précédente", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
    async def back_page(self, i, b):
        e = discord.Embed(title="🔨 Configuration — Modération (1/2)", color=COLOR_MAIN)
        e.description = f"Configurez les outils de modération.\n\n{sep()}"
        e.set_footer(text=BOT_FOOTER)
        await i.response.edit_message(embed=e, view=SettingsModerationView())

    @ui.button(label="↩️ Menu principal", style=discord.ButtonStyle.danger, row=2)
    async def back_main(self, i, b):
        await i.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())


class SettingsSystemView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(cls=ui.ChannelSelect, placeholder="💎 Logs généraux", row=0)
    async def set_logs(self, interaction, select):
        await set_setting(interaction.guild_id, "log_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Logs Généraux", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="🎉 Salon bienvenue", row=1)
    async def set_welcome(self, interaction, select):
        await set_setting(interaction.guild_id, "welcome_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Bienvenue", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="👋 Salon départs", row=2)
    async def set_leave(self, interaction, select):
        await set_setting(interaction.guild_id, "leave_channel_id", select.values[0].id)
        e = discord.Embed(title="✅ Départs", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="← Retour", style=discord.ButtonStyle.secondary, emoji="↩️", row=3)
    async def back(self, i, b):
        await i.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())


class SettingsRaidView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Activer",    style=discord.ButtonStyle.success,   emoji="🟢", row=0)
    async def on(self, interaction, button):
        await set_setting(interaction.guild_id, "antiraid_enabled", 1)
        e = discord.Embed(title="🛡️ Anti-Raid Activé",    description="Protection **active**.",     color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="Désactiver", style=discord.ButtonStyle.danger,    emoji="🔴", row=0)
    async def off(self, interaction, button):
        await set_setting(interaction.guild_id, "antiraid_enabled", 0)
        e = discord.Embed(title="🔓 Anti-Raid Désactivé", description="Protection **désactivée**.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="Configurer le seuil", style=discord.ButtonStyle.secondary, emoji="🔢", row=0)
    async def set_threshold(self, interaction, button):
        await interaction.response.send_modal(ThresholdModal())

    @ui.button(label="🔒 Activer Lockdown",    style=discord.ButtonStyle.danger,  emoji="🔒", row=1)
    async def lockdown_on(self, interaction, button):
        await set_setting(interaction.guild_id, "lockdown_active", 1)
        e = discord.Embed(title="🔒 Lockdown Activé", description="Tout nouveau membre sera **immédiatement expulsé**.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="🔓 Désactiver Lockdown", style=discord.ButtonStyle.success, emoji="🔓", row=1)
    async def lockdown_off(self, interaction, button):
        await set_setting(interaction.guild_id, "lockdown_active", 0)
        e = discord.Embed(title="🔓 Lockdown Désactivé", description="Le serveur est de nouveau **ouvert**.", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, placeholder="🚨 Salon d'alertes Anti-Raid", row=2)
    async def set_raid_log(self, interaction, select):
        await set_setting(interaction.guild_id, "antiraid_log_id", select.values[0].id)
        e = discord.Embed(title="✅ Alertes Raid", description=f"Défini sur {select.values[0].mention}", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="← Retour", style=discord.ButtonStyle.secondary, emoji="↩️", row=3)
    async def back(self, i, b):
        await i.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())


class SettingsLiveView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(cls=ui.ChannelSelect, placeholder="📺 Salon de notifications live", row=0)
    async def set_live_channel(self, interaction, select):
        await set_setting(interaction.guild_id, "live_notif_channel_id", select.values[0].id)
        e = discord.Embed(
            title       = "✅ Salon Live Configuré",
            description = f"Les notifications seront envoyées dans {select.values[0].mention}.",
            color       = COLOR_SUCCESS
        )
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.select(cls=ui.RoleSelect, placeholder="🔔 Rôle à mentionner lors d'un live (optionnel)", row=1)
    async def set_live_role(self, interaction, select):
        await set_setting(interaction.guild_id, "live_notif_role_id", select.values[0].id)
        e = discord.Embed(
            title       = "✅ Rôle Live Configuré",
            description = f"{select.values[0].mention} sera mentionné lors des lives.",
            color       = COLOR_SUCCESS
        )
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="🗑️ Retirer le rôle", style=discord.ButtonStyle.secondary, row=2)
    async def remove_role(self, interaction, button):
        await set_setting(interaction.guild_id, "live_notif_role_id", None)
        e = discord.Embed(title="✅ Rôle Retiré", description="Aucun rôle ne sera mentionné.", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="← Retour", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
    async def back(self, i, b):
        await i.response.edit_message(embed=build_settings_main_embed(), view=SettingsMainView())


@bot.tree.command(name="settings", description="Ouvrir le panneau de configuration")
async def settings(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        e = discord.Embed(title="🔒 Accès Refusé", description="Réservé aux **Administrateurs**.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    await bot.ensure_guild(interaction.guild_id)
    await interaction.response.send_message(embed=build_settings_main_embed(), view=SettingsMainView(), ephemeral=True)


@bot.tree.command(name="config", description="Afficher la configuration actuelle du bot")
async def config_cmd(interaction: discord.Interaction):
    if not await check_is_staff(interaction): return
    await bot.ensure_guild(interaction.guild_id)
    embed = await build_config_embed(interaction.guild)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ban", description="Bannir un membre du serveur")
async def ban(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison spécifiée"):
    if not await check_is_staff(interaction): return
    if membre.top_role >= interaction.guild.me.top_role:
        e = discord.Embed(title="❌ Impossible", description="Ce membre a un rôle supérieur ou égal au bot.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)

    class ConfirmBanView(ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            confirm_btn = ui.Button(label="Oui, bannir", style=discord.ButtonStyle.danger)
            confirm_btn.callback = self.confirm_callback
            cancel_btn = ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
            cancel_btn.callback = self.cancel_callback
            self.add_item(confirm_btn)
            self.add_item(cancel_btn)

        async def confirm_callback(self, interaction_confirm):
            try:
                await membre.ban(reason=raison)
                e = discord.Embed(title="🔨 Bannissement Effectué", description=f"**{membre.display_name}** a été banni.", color=COLOR_SUCCESS)
                e.set_footer(text=BOT_FOOTER)
                await interaction_confirm.response.send_message(embed=e, ephemeral=True)
                s = await get_settings(interaction.guild_id)
                if s and s["ban_log_channel_id"]:
                    chan = interaction.guild.get_channel(s["ban_log_channel_id"])
                    if chan:
                        log = discord.Embed(title="🔨 Bannissement", color=COLOR_DANGER, timestamp=datetime.now())
                        log.description = (
                            f"{sep()}\n**👤 Membre banni**\n"
                            f"┣ **Nom :** {membre.mention} (`{membre.name}`)\n"
                            f"┣ **ID :** `{membre.id}`\n"
                            f"┗ **Compte créé :** <t:{int(membre.created_at.timestamp())}:R>\n\n"
                            f"**🔨 Action**\n┣ **Modérateur :** {interaction.user.mention}\n"
                            f"┣ **Raison :** {raison}\n┗ **Date :** {ts()}\n{sep()}"
                        )
                        log.set_thumbnail(url=membre.display_avatar.url)
                        log.set_footer(text=BOT_FOOTER)
                        await chan.send(embed=log)
            except discord.Forbidden:
                e = discord.Embed(title="❌ Permission Refusée", description="Je n'ai pas la permission de bannir ce membre.", color=COLOR_DANGER)
                e.set_footer(text=BOT_FOOTER)
                await interaction_confirm.response.send_message(embed=e, ephemeral=True)
            except discord.HTTPException as ex:
                e = discord.Embed(title="❌ Erreur", description=f"Le bannissement a échoué.\n```{ex}```", color=COLOR_DANGER)
                e.set_footer(text=BOT_FOOTER)
                await interaction_confirm.response.send_message(embed=e, ephemeral=True)

        async def cancel_callback(self, interaction_cancel):
            await interaction_cancel.response.send_message("Bannissement annulé.", ephemeral=True)

    embed = discord.Embed(title="Confirmation Bannissement", description=f"Voulez-vous vraiment bannir **{membre.display_name}** (`{membre.id}`) ?\n> {raison}", color=COLOR_WARNING)
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed, view=ConfirmBanView(), ephemeral=True)


@bot.tree.command(name="unban", description="Débannir un utilisateur par son ID")
async def unban(interaction: discord.Interaction, user_id: str):
    if not await check_is_staff(interaction): return
    try:
        uid  = int(user_id)
        user = await bot.fetch_user(uid)
        await interaction.guild.unban(user)
        e = discord.Embed(title="🔓 Débannissement Effectué", description=f"**{user.name}** débanni.", color=COLOR_SUCCESS)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)
        s = await get_settings(interaction.guild_id)
        if s and s["ban_log_channel_id"]:
            chan = interaction.guild.get_channel(s["ban_log_channel_id"])
            if chan:
                log = discord.Embed(title="🔓 Débannissement", color=COLOR_SUCCESS, timestamp=datetime.now())
                log.description = (
                    f"{sep()}\n**👤 Membre débanni**\n"
                    f"┣ **Nom :** `{user.name}`\n┗ **ID :** `{user.id}`\n\n"
                    f"**🛡️ Action**\n┣ **Modérateur :** {interaction.user.mention}\n"
                    f"┗ **Date :** {ts()}\n{sep()}"
                )
                log.set_thumbnail(url=user.display_avatar.url)
                log.set_footer(text=BOT_FOOTER)
                await chan.send(embed=log)
    except ValueError:
        e = discord.Embed(title="❌ ID Invalide", description="L'ID fourni n'est pas un nombre valide.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)
    except discord.NotFound:
        e = discord.Embed(title="❌ Utilisateur Introuvable", description="Cet utilisateur est introuvable ou n'est pas banni.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)
    except Exception as ex:
        e = discord.Embed(title="❌ Erreur", description=f"Impossible de débannir.\n```{ex}```", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="kick", description="Expulser un membre du serveur")
async def kick(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison spécifiée"):
    if not await check_is_staff(interaction): return
    if membre.top_role >= interaction.guild.me.top_role:
        e = discord.Embed(title="❌ Impossible", description="Ce membre a un rôle supérieur ou égal au bot.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    await membre.kick(reason=raison)
    e = discord.Embed(title="👢 Expulsion Effectuée", description=f"**{membre.display_name}** a été expulsé.", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)
    log = discord.Embed(title="👢 Expulsion", color=COLOR_WARNING, timestamp=datetime.now())
    log.description = (
        f"{sep()}\n**👤 Membre expulsé**\n"
        f"┣ **Nom :** {membre.mention} (`{membre.name}`)\n"
        f"┣ **ID :** `{membre.id}`\n\n"
        f"**🔨 Action**\n┣ **Modérateur :** {interaction.user.mention}\n"
        f"┣ **Raison :** {raison}\n┗ **Date :** {ts()}\n{sep()}"
    )
    log.set_thumbnail(url=membre.display_avatar.url)
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="mute", description="Mettre un membre en timeout")
@app_commands.describe(duree="Durée en minutes (max 10080 = 7 jours)")
async def mute(interaction: discord.Interaction, membre: discord.Member, duree: int, raison: str = "Aucune raison spécifiée"):
    if not await check_is_staff(interaction): return
    if duree < 1 or duree > 10080:
        e = discord.Embed(title="⚠️ Durée Invalide", description="Entre **1** et **10080** minutes (7 jours).", color=COLOR_WARNING)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    until = datetime.now() + timedelta(minutes=duree)
    await membre.timeout(until, reason=raison)
    h, m  = divmod(duree, 60)
    duree_str = f"{h}h {m}m" if h else f"{m}m"
    e = discord.Embed(title="🔇 Timeout Appliqué", description=f"**{membre.display_name}** muté pour **{duree_str}**.", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)
    log = discord.Embed(title="🔇 Timeout", color=COLOR_WARNING, timestamp=datetime.now())
    log.description = (
        f"{sep()}\n**👤 Membre muté**\n"
        f"┣ **Nom :** {membre.mention} (`{membre.name}`)\n\n"
        f"**🔇 Action**\n┣ **Modérateur :** {interaction.user.mention}\n"
        f"┣ **Durée :** {duree_str}\n┣ **Raison :** {raison}\n"
        f"┗ **Fin :** <t:{int(until.timestamp())}:R>\n{sep()}"
    )
    log.set_thumbnail(url=membre.display_avatar.url)
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="unmute", description="Retirer le timeout d'un membre")
async def unmute(interaction: discord.Interaction, membre: discord.Member):
    if not await check_is_staff(interaction): return
    await membre.timeout(None)
    e = discord.Embed(title="🔊 Timeout Retiré", description=f"**{membre.display_name}** peut de nouveau parler.", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)
    log = discord.Embed(title="🔊 Unmute", color=COLOR_SUCCESS, timestamp=datetime.now())
    log.description = f"**{membre.mention}** a été démute par {interaction.user.mention}."
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="warn", description="Avertir un membre")
async def warn(interaction: discord.Interaction, membre: discord.Member, raison: str):
    if not await check_is_staff(interaction): return
    conn = await db()
    await conn.execute("INSERT INTO warns (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?,?,?,?,?)",
                      (interaction.guild_id, membre.id, interaction.user.id, raison, datetime.now().isoformat()))
    await conn.commit()
    c = await conn.execute("SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, membre.id))
    total_row = await c.fetchone()
    total = total_row[0] if total_row else 0
    await conn.close()

    e = discord.Embed(title="⚠️Avertissement Enregistré⚠️", color=COLOR_INFO, timestamp=datetime.now())
    e.description = (
        f"**{membre.mention}** a reçu un avertissement.\n\n"
        f"**💬 Raison :** {raison}\n"
        f"**📋 Total warnings :** {total}"
    )
    e.set_thumbnail(url=membre.display_avatar.url)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)

    try:
        dm = discord.Embed(title="⚠️ Vous avez reçu un avertissement", color=COLOR_WARNING)
        dm.description = (
            f"**Serveur :** {interaction.guild.name}\n"
            f"**Modérateur :** {interaction.user.display_name}\n"
            f"**Raison :** {raison}\n"
            f"**Total warns :** {total}"
        )
        dm.set_footer(text=BOT_FOOTER)
        await membre.send(embed=dm)
    except Exception:
        pass

    log = discord.Embed(title="⚠️ Warn", color=COLOR_WARNING, timestamp=datetime.now())
    log.description = (
        f"{sep()}\n"
        f"**👤 Membre :** {membre.mention} (`{membre.name}`)\n"
        f"**🛡️ Modérateur :** {interaction.user.mention}\n"
        f"**💬 Raison :** {raison}\n"
        f"**📋 Total :** {total} warn(s)\n"
        f"{sep()}"
    )
    log.set_thumbnail(url=membre.display_avatar.url)
    log.set_footer(text=BOT_FOOTER)
    s_log = await get_settings(interaction.guild_id)
    if s_log and s_log.get("warn_log_channel_id"):
        warn_chan = interaction.guild.get_channel(s_log["warn_log_channel_id"])
        if isinstance(warn_chan, discord.TextChannel):
            await warn_chan.send(embed=log)
        else:
            await send_log(interaction.guild, log)
    else:
        await send_log(interaction.guild, log)


@bot.tree.command(name="infractions", description="Voir l'historique des avertissements d'un membre")
async def infractions(interaction: discord.Interaction, membre: discord.Member):
    if not await check_is_staff(interaction): return
    conn = await db()
    c = await conn.execute("SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, membre.id))
    total_count_row = await c.fetchone()
    total_count = total_count_row[0] if total_count_row else 0
    c = await conn.execute("SELECT id, moderator_id, reason, timestamp FROM warns WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 10",
              (interaction.guild_id, membre.id))
    rows = await c.fetchall()
    await conn.close()

    embed = discord.Embed(title=f"📋 Infractions — {membre.display_name}", color=COLOR_PURPLE, timestamp=datetime.now())
    embed.set_thumbnail(url=membre.display_avatar.url)
    if not rows:
        embed.description = "✅ Aucun avertissement enregistré."
        embed.set_footer(text=BOT_FOOTER)
    else:
        lines = []
        for row in rows:
            wid, mod_id, reason, ts_str = row
            try:
                dt  = datetime.fromisoformat(ts_str)
                tst = f"<t:{int(dt.timestamp())}:d>"
            except Exception:
                tst = ts_str
            lines.append(f"**#{wid}** — {tst} par <@{mod_id}>\n> {reason}\n")
        embed.description = "\n".join(lines)
        if total_count > 10:
            embed.set_footer(text=f"10 derniers affichés sur {total_count} total • {BOT_FOOTER}")
        else:
            embed.set_footer(text=f"{total_count} avertissement(s) • {BOT_FOOTER}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear", description="Supprimer des messages")
async def clear(interaction: discord.Interaction, nombre: int):
    if not await check_is_staff(interaction): return
    if not 1 <= nombre <= 100:
        e = discord.Embed(title="⚠️ Valeur Invalide", description="Entre **1** et **100** messages.", color=COLOR_WARNING)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = []
    if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
        deleted = await interaction.channel.purge(limit=nombre)
    e = discord.Embed(title="🧹 Nettoyage Effectué", description=f"**{len(deleted)}** message(s) supprimé(s).", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="clearmsg", description="Supprimer tous les messages d'un utilisateur dans tous les salons")
@app_commands.describe(cible="Mentionnez un membre présent OU collez l'ID / le nom d'un ancien membre")
async def clearmsg(interaction: discord.Interaction, cible: str):
    if not await check_is_staff(interaction): return
    await interaction.response.defer(ephemeral=True)

    target_user  = None
    target_id    = None
    display_name = cible
    cleaned = cible.strip().lstrip("<@!").rstrip(">")

    if cleaned.isdigit():
        target_id = int(cleaned)
        try:
            target_user = await bot.fetch_user(target_id)
            display_name = f"{target_user.name} (`{target_user.id}`)"
        except discord.NotFound:
            display_name = f"ID `{target_id}` (utilisateur introuvable)"

    if target_id is None:
        for m in interaction.guild.members:
            if m.name.lower() == cible.lower() or (m.display_name and m.display_name.lower() == cible.lower()):
                target_id    = m.id
                target_user  = m
                display_name = f"{m.display_name} (`{m.id}`)"
                break

    if target_id is None:
        e = discord.Embed(title="❌ Utilisateur Introuvable", description=f"Impossible de trouver **`{cible}`**.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.followup.send(embed=e, ephemeral=True)

    total_deleted  = 0
    salons_touches = 0
    erreurs        = 0

    for salon in interaction.guild.channels:
        if not isinstance(salon, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
            continue
        try:
            perms = salon.permissions_for(interaction.guild.me)
            if not perms.read_message_history or not perms.manage_messages:
                continue
            deleted_in_salon = 0
            deleted = await salon.purge(limit=1000, check=lambda m, tid=target_id: m.author.id == tid, bulk=True)
            deleted_in_salon += len(deleted)
            async for message in salon.history(limit=500):
                if message.author.id == target_id:
                    try:
                        await message.delete()
                        deleted_in_salon += 1
                    except (discord.NotFound, discord.Forbidden):
                        pass
            if deleted_in_salon > 0:
                total_deleted  += deleted_in_salon
                salons_touches += 1
        except (discord.Forbidden, discord.HTTPException):
            erreurs += 1
            continue

    log = discord.Embed(title="🧹 Purge Complète", color=COLOR_INFO, timestamp=datetime.now())
    log.description = (
        f"{sep()}\n**👤 Cible :** {display_name}\n"
        f"**🛡️ Modérateur :** {interaction.user.mention}\n\n"
        f"**📊 Résultat**\n"
        f"┣ **Messages supprimés :** {total_deleted}\n"
        f"┣ **Salons touchés :** {salons_touches}\n"
        f"┗ **Salons ignorés :** {erreurs}\n{sep()}"
    )
    if target_user:
        log.set_thumbnail(url=target_user.display_avatar.url)
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)

    e = discord.Embed(title="🧹 Purge Terminée", color=COLOR_SUCCESS, timestamp=datetime.now())
    e.description = (
        f"Tous les messages de **{display_name}** ont été supprimés.\n\n"
        f"{sep()}\n┣ **{total_deleted}** message(s) supprimé(s)\n"
        f"┣ **{salons_touches}** salon(s) nettoyé(s)\n"
        f"┗ **{erreurs}** salon(s) ignoré(s)\n{sep()}"
    )
    e.set_footer(text=BOT_FOOTER)
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="lock", description="Verrouiller un salon")
async def lock(interaction: discord.Interaction, salon: discord.TextChannel = None):
    if not await check_is_staff(interaction): return
    chan = salon or interaction.channel
    if chan and isinstance(chan, discord.TextChannel) and interaction.guild and interaction.guild.default_role:
        await chan.set_permissions(interaction.guild.default_role, send_messages=False)
    e = discord.Embed(title="🔒 Salon Verrouillé", description=f"{chan.mention} est maintenant **verrouillé**.", color=COLOR_DANGER)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e)
    log = discord.Embed(title="🔒 Lock", color=COLOR_DANGER, timestamp=datetime.now())
    log.description = f"{chan.mention} verrouillé par {interaction.user.mention}."
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="unlock", description="Déverrouiller un salon")
async def unlock(interaction: discord.Interaction, salon: discord.TextChannel = None):
    if not await check_is_staff(interaction): return
    chan = salon or interaction.channel
    if chan and isinstance(chan, discord.TextChannel) and interaction.guild and interaction.guild.default_role:
        await chan.set_permissions(interaction.guild.default_role, send_messages=None)
    e = discord.Embed(title="🔓 Salon Déverrouillé", description=f"{chan.mention} est de nouveau **ouvert**.", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e)
    log = discord.Embed(title="🔓 Unlock", color=COLOR_SUCCESS, timestamp=datetime.now())
    log.description = f"{chan.mention} déverrouillé par {interaction.user.mention}."
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="slowmode", description="Définir le slowmode d'un salon")
@app_commands.describe(secondes="0 = désactiver, max 21600 (6h)")
async def slowmode(interaction: discord.Interaction, secondes: int, salon: discord.TextChannel = None):
    if not await check_is_staff(interaction): return
    if not 0 <= secondes <= 21600:
        e = discord.Embed(title="⚠️ Valeur Invalide", description="Entre **0** (désactivé) et **21600** secondes.", color=COLOR_WARNING)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    chan = salon or interaction.channel
    await chan.edit(slowmode_delay=secondes)
    if secondes == 0:
        txt = "Slowmode **désactivé**."
    elif secondes < 60:
        txt = f"Slowmode : **{secondes}s**"
    elif secondes < 3600:
        txt = f"Slowmode : **{secondes//60}m {secondes%60}s**"
    else:
        txt = f"Slowmode : **{secondes//3600}h {(secondes%3600)//60}m**"
    e = discord.Embed(title="⏱️ Slowmode Mis à Jour", description=f"{chan.mention} — {txt}", color=COLOR_SUCCESS)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="userinfo", description="Informations complètes sur un membre")
async def userinfo(interaction: discord.Interaction, membre: discord.Member = None):
    if not await check_is_staff(interaction): return
    m = membre or interaction.user
    conn = await db()
    c = await conn.execute("SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, m.id))
    nb_warns_row = await c.fetchone()
    nb_warns = nb_warns_row[0] if nb_warns_row else 0
    await conn.close()

    roles     = [r.mention for r in reversed(m.roles) if r.name != "@everyone"]
    roles_str = " ".join(roles) if roles else "Aucun rôle"

    embed = discord.Embed(title=f"👤 {m.display_name}", color=m.color if m.color.value != 0 else COLOR_MAIN, timestamp=datetime.now())
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.description = (
        f"{sep()}\n**🪪 Identité**\n"
        f"┣ **Pseudo :** `{m.name}`\n"
        f"┣ **ID :** `{m.id}`\n"
        f"┣ **Compte créé :** <t:{int(m.created_at.timestamp())}:R>\n"
        + (f"┗ **A rejoint :** <t:{int(m.joined_at.timestamp())}:R>\n\n" if m.joined_at else "┗ **A rejoint :** Inconnu\n\n")
    )
    embed.add_field(name="🏷️ Rôles", value=roles_str[:1024], inline=False)
    embed.add_field(name="⚠️ Avertissements", value=str(nb_warns), inline=True)
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="serverinfo", description="Informations complètes sur le serveur")
async def serverinfo(interaction: discord.Interaction):
    g      = interaction.guild
    bots   = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    online = sum(1 for m in g.members if m.status != discord.Status.offline)
    txt_ch = sum(1 for c in g.channels if isinstance(c, discord.TextChannel))
    voc_ch = sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))

    embed = discord.Embed(title=f"🏠 {g.name}", color=COLOR_MAIN, timestamp=datetime.now())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    if g.banner:
        embed.set_image(url=g.banner.url)
    embed.description = (
        f"{sep()}\n**🪪 Informations**\n"
        f"┣ **ID :** `{g.id}`\n"
        f"┣ **Propriétaire :** <@{g.owner_id}>\n"
        f"┗ **Créé le :** <t:{int(g.created_at.timestamp())}:R>\n\n"
        f"**👥 Membres ({g.member_count})**\n"
        f"┣ **Humains :** {humans}\n┣ **Bots :** {bots}\n┗ **En ligne :** {online}\n\n"
        f"**💬 Salons ({len(g.channels)})**\n"
        f"┣ **Texte :** {txt_ch}\n┣ **Vocal :** {voc_ch}\n┗ **Catégories :** {len(g.categories)}\n\n"
        f"**🏅 Boosts**\n┣ **Niveau :** {g.premium_tier}\n┗ **Boosts :** {g.premium_subscription_count}\n\n"
        f"**🛡️ Rôles :** {len(g.roles)}\n**😀 Emojis :** {len(g.emojis)}/{g.emoji_limit}\n{sep()}"
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="report", description="Signaler un membre au staff")
async def report(interaction: discord.Interaction, membre: discord.Member, raison: str, preuve: str = "Aucune preuve fournie"):
    await bot.ensure_guild(interaction.guild_id)
    if membre.id == interaction.user.id:
        e = discord.Embed(title="❌ Action Impossible", description="Vous ne pouvez pas vous signaler vous-même.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)
    s = await get_settings(interaction.guild_id)
    if s and s["report_channel_id"]:
        channel = interaction.guild.get_channel(s["report_channel_id"])
        if channel:
            embed = build_report_embed(interaction.user, membre, raison, preuve)
            view  = ReportControlView(target_user_id=membre.id, reporter_id=interaction.user.id, reason=raison, proof=preuve)
            msg   = await channel.send(embed=embed, view=view)
            conn  = await db()
            await conn.execute(
                "INSERT INTO pending_reports (message_id, channel_id, guild_id, target_user_id, reporter_id, reason, proof) VALUES (?,?,?,?,?,?,?)",
                (msg.id, channel.id, interaction.guild_id, membre.id, interaction.user.id, raison, preuve)
            )
            await conn.commit()
            await conn.close()
            ok = discord.Embed(title="✅ Signalement Transmis", description=f"Signalement contre **{membre.display_name}** envoyé.", color=COLOR_SUCCESS)
            ok.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=ok, ephemeral=True)
    e = discord.Embed(title="❌ Config Manquante", description="Aucun salon de signalement configuré. Faites `/settings`.", color=COLOR_DANGER)
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="poll", description="Créer un sondage (2 à 5 options)")
@app_commands.describe(
    question="La question du sondage",
    option1="Option 1 (obligatoire)", option2="Option 2 (obligatoire)",
    option3="Option 3 (optionnel)",   option4="Option 4 (optionnel)",
    option5="Option 5 (optionnel)"
)
async def poll(interaction: discord.Interaction, question: str,
               option1: str, option2: str,
               option3: str = None, option4: str = None, option5: str = None):
    options = [o for o in [option1, option2, option3, option4, option5] if o]
    await interaction.response.defer()
    placeholder_view = PollView(0, question, options, interaction.user.display_name, str(interaction.user.display_avatar.url))
    msg  = await interaction.followup.send(embed=placeholder_view._build_embed())
    view = PollView(msg.id, question, options, interaction.user.display_name, str(interaction.user.display_avatar.url))
    await msg.edit(embed=view._build_embed(), view=view)


@bot.tree.command(name="unwarn", description="Retirer un ou plusieurs avertissements d'un membre")
@app_commands.describe(membre="Le membre concerné", mode="Que souhaitez-vous supprimer ?")
@app_commands.choices(mode=[
    app_commands.Choice(name="Dernier warn uniquement", value="dernier"),
    app_commands.Choice(name="Tout le casier (Reset)", value="tout")
])
async def unwarn(interaction: discord.Interaction, membre: discord.Member, mode: str):
    if not await check_is_staff(interaction): return
    conn = await db()

    if mode == "dernier":
        c = await conn.execute(
            "SELECT id, reason FROM warns WHERE user_id = ? AND guild_id = ? ORDER BY id DESC LIMIT 1", (membre.id, interaction.guild_id)
        )
        row = await c.fetchone()
        if not row:
            await conn.close()
            return await interaction.response.send_message(f"❌ {membre.mention} n'a aucun avertissement.", ephemeral=True)
        warn_id, reason = row
        await conn.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
        message_confirm = f"Le dernier avertissement de {membre.mention} a été supprimé (Raison : *{reason}*)."
        log_title = "🗑️ Unwarn (Dernier)"
    else:
        c2 = await conn.execute("SELECT COUNT(*) FROM warns WHERE user_id = ? AND guild_id = ?", (membre.id, interaction.guild_id))
        count_row = await c2.fetchone()
        count = count_row[0] if count_row else 0
        if count == 0:
            await conn.close()
            return await interaction.response.send_message(f"❌ {membre.mention} a déjà un casier vierge.", ephemeral=True)
        await conn.execute("DELETE FROM warns WHERE user_id = ? AND guild_id = ?", (membre.id, interaction.guild_id))
        message_confirm = f"Le casier de {membre.mention} a été effacé (**{count}** warn(s) retiré(s))."
        log_title = "🔥 Unwarn Global (Reset)"

    await conn.commit()
    await conn.close()

    e = discord.Embed(title="✅ Action effectuée", description=message_confirm, color=COLOR_SUCCESS, timestamp=datetime.now())
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e)

    log = discord.Embed(title=log_title, color=COLOR_WARNING, timestamp=datetime.now())
    log.description = (
        f"{sep()}\n**👤 Membre :** {membre.mention}\n"
        f"**🛡️ Modérateur :** {interaction.user.mention}\n"
        f"**⚙️ Mode :** `{mode}`\n{sep()}"
    )
    log.set_footer(text=BOT_FOOTER)
    await send_log(interaction.guild, log)


@bot.tree.command(name="ping", description="Latence du bot")
async def ping(interaction: discord.Interaction):
    lat    = round(bot.latency * 1000)
    color  = COLOR_SUCCESS if lat < 100 else (COLOR_WARNING if lat < 200 else COLOR_DANGER)
    status = "Excellente 🟢" if lat < 100 else ("Correcte 🟡" if lat < 200 else "Dégradée 🔴")
    e = discord.Embed(title="🛰️ Latence du Bot", color=color, timestamp=datetime.now())
    e.description = f"**Ping :** `{lat}ms`\n**Qualité :** {status}"
    e.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="me", description="Afficher votre photo de profil")
async def me(interaction: discord.Interaction):
    await bot.ensure_guild(interaction.guild_id)
    user  = interaction.user
    embed = discord.Embed(title="⭐ Photo de Profil", color=user.color if user.color.value != 0 else COLOR_MAIN, timestamp=datetime.now())
    embed.description = f"**Membre :** {user.mention}\n**Pseudo :** `{user.name}`\n**ID :** `{user.id}`"
    embed.set_image(url=user.display_avatar.url)
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="banlist", description="Afficher la liste des utilisateurs bannis")
async def banlist(interaction: discord.Interaction):
    s = await get_settings(interaction.guild_id)
    allowed = []
    if s and s["mod_roles"]:
        try:
            allowed = json.loads(s["mod_roles"])
        except Exception:
            allowed = []
    user_roles = [r.id for r in interaction.user.roles]
    member     = interaction.guild.get_member(interaction.user.id)
    is_admin   = member and member.guild_permissions.administrator
    if not is_admin and not any(rid in user_roles for rid in allowed):
        embed = discord.Embed(title="🔒 Accès Refusé", description="Seuls les modérateurs staff peuvent utiliser cette commande.", color=COLOR_DANGER)
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    bans = [ban async for ban in interaction.guild.bans()]
    if not bans:
        embed = discord.Embed(title="🔨 Banlist", description="Aucun utilisateur n'est banni.", color=COLOR_INFO)
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    class BanListView(ui.View):
        def __init__(self, bans, page=0):
            super().__init__(timeout=120)
            self.bans     = bans
            self.page     = page
            self.per_page = 5
            self.max_page = (len(bans) - 1) // self.per_page
            self.update_buttons()

        def update_buttons(self):
            self.clear_items()
            start = self.page * self.per_page
            end   = start + self.per_page
            for entry in self.bans[start:end]:
                user   = entry.user
                reason = entry.reason or "Aucune raison spécifiée"
                btn    = ui.Button(label=f"Unban {user.name}", style=discord.ButtonStyle.success, custom_id=f"unban:{user.id}")
                btn.callback = self.make_unban_callback(user, reason)
                self.add_item(btn)
            if self.page > 0:
                prev_btn          = ui.Button(label="← Page précédente", style=discord.ButtonStyle.secondary)
                prev_btn.callback = self.make_page_callback(self.page - 1)
                self.add_item(prev_btn)
            if self.page < self.max_page:
                next_btn          = ui.Button(label="Page suivante →", style=discord.ButtonStyle.secondary)
                next_btn.callback = self.make_page_callback(self.page + 1)
                self.add_item(next_btn)

        def make_page_callback(self, new_page):
            async def callback(interaction):
                self.page = new_page
                self.update_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)
            return callback

        def make_unban_callback(self, user, reason):
            async def callback(interaction):
                confirm_view = ConfirmUnbanView(user, self, self.page)
                embed = discord.Embed(title="Confirmation", description=f"Voulez-vous vraiment débannir **{user.name}** (`{user.id}`) ?\n> {reason}", color=COLOR_WARNING)
                embed.set_footer(text=BOT_FOOTER)
                await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)
            return callback

        def build_embed(self):
            start = self.page * self.per_page
            end   = start + self.per_page
            lines = []
            for entry in self.bans[start:end]:
                user   = entry.user
                reason = entry.reason or "Aucune raison spécifiée"
                lines.append(f"**{user.name}** (`{user.id}`)\n> {reason}")
            embed = discord.Embed(title=f"🔨 Banlist ({len(self.bans)})", description="\n\n".join(lines), color=COLOR_DANGER)
            embed.set_footer(text=f"Page {self.page+1}/{self.max_page+1} • {BOT_FOOTER}")
            return embed

    class ConfirmUnbanView(ui.View):
        def __init__(self, user, parent_view, page):
            super().__init__(timeout=30)
            self.user        = user
            self.parent_view = parent_view
            self.page        = page
            confirm_btn          = ui.Button(label="Oui, débannir", style=discord.ButtonStyle.danger)
            confirm_btn.callback = self.confirm_callback
            cancel_btn           = ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
            cancel_btn.callback  = self.cancel_callback
            self.add_item(confirm_btn)
            self.add_item(cancel_btn)

        async def confirm_callback(self, interaction):
            try:
                await interaction.guild.unban(self.user)
                msg = f"✅ **{self.user.name}** a été débanni."
            except Exception as e:
                msg = f"❌ Erreur : {e}"
            await interaction.response.send_message(msg, ephemeral=True)
            bans     = [ban async for ban in interaction.guild.bans()]
            new_view = BanListView(bans, self.page if self.page <= (len(bans)-1)//5 else 0)
            await interaction.message.edit(embed=new_view.build_embed(), view=new_view)

        async def cancel_callback(self, interaction):
            await interaction.response.send_message("Débannissement annulé.", ephemeral=True)

    view = BanListView(bans)
    await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

@bot.tree.command(name="warnlist", description="Voir tous les avertissements enregistrés sur le serveur")
@app_commands.describe(membre="Filtrer par membre (optionnel)")
async def warnlist(interaction: discord.Interaction, membre: discord.Member = None):
    if not await check_is_staff(interaction): return

    conn = await db()
    if membre:
        c = await conn.execute(
            "SELECT id, user_id, moderator_id, reason, timestamp FROM warns WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (interaction.guild_id, membre.id)
        )
    else:
        c = await conn.execute(
            "SELECT id, user_id, moderator_id, reason, timestamp FROM warns WHERE guild_id = ? ORDER BY id DESC",
            (interaction.guild_id,)
        )
    rows = await c.fetchall()
    await conn.close()

    if not rows:
        e = discord.Embed(
            title       = "📋 Aucun Avertissement",
            description = f"Aucun warn enregistré{f' pour {membre.mention}' if membre else ' sur ce serveur'}.",
            color       = COLOR_SUCCESS
        )
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)

    per_page = 8
    pages    = [rows[i:i+per_page] for i in range(0, len(rows), per_page)]

    def build_page(page_idx: int) -> discord.Embed:
        title = f"📋 Warnlist{f' — {membre.display_name}' if membre else ''}"
        embed = discord.Embed(title=title, color=COLOR_PURPLE, timestamp=datetime.now())
        lines = []
        for row in pages[page_idx]:
            wid, uid, mod_id, reason, ts_str = row
            try:
                dt  = datetime.fromisoformat(ts_str)
                tst = f"<t:{int(dt.timestamp())}:d>"
            except Exception:
                tst = ts_str
            lines.append(f"**#{wid}** • <@{uid}> — {tst} par <@{mod_id}>\n> {reason}")
        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Page {page_idx+1}/{len(pages)} • {len(rows)} warn(s) • {BOT_FOOTER}")
        if membre:
            embed.set_thumbnail(url=membre.display_avatar.url)
        return embed

    class WarnListView(ui.View):
        def __init__(self, page=0):
            super().__init__(timeout=120)
            self.page = page
            self._update_buttons()

        def _update_buttons(self):
            self.prev_btn.disabled = self.page == 0
            self.next_btn.disabled = self.page >= len(pages) - 1
            self.page_btn.label    = f"Page {self.page+1}/{len(pages)}"

        @ui.button(label="←", style=discord.ButtonStyle.secondary)
        async def prev_btn(self, inter, button):
            self.page -= 1; self._update_buttons()
            await inter.response.edit_message(embed=build_page(self.page), view=self)

        @ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
        async def page_btn(self, inter, button):
            pass

        @ui.button(label="→", style=discord.ButtonStyle.secondary)
        async def next_btn(self, inter, button):
            self.page += 1; self._update_buttons()
            await inter.response.edit_message(embed=build_page(self.page), view=self)

    view = WarnListView()
    view._update_buttons()
    await interaction.response.send_message(embed=build_page(0), view=view, ephemeral=True)


HELP_CATEGORIES = {
    "🔨 Modération": {
        "description": "Outils de modération pour gérer les membres et le serveur.",
        "commands": [
            ("/ban",        "Bannir un membre avec confirmation et log automatique."),
            ("/unban",      "Débannir un utilisateur via son ID Discord."),
            ("/banlist",    "Afficher la liste des bannis avec boutons de déban."),
            ("/kick",       "Expulser un membre du serveur."),
            ("/mute",       "Mettre un membre en timeout (1 min → 7 jours)."),
            ("/unmute",     "Retirer le timeout d'un membre."),
            ("/warn",       "Avertir un membre et notifier par MP."),
            ("/unwarn",     "Retirer le dernier warn ou reset complet du casier."),
            ("/infractions","Consulter l'historique des warns d'un membre."),
            ("/warnlist",   "Voir tous les warns du serveur, filtrable par membre."),
            ("/clear",      "Supprimer entre 1 et 100 messages dans le salon."),
            ("/clearmsg",   "Supprimer tous les messages d'un utilisateur (tous salons)."),
            ("/lock",       "Verrouiller un salon (écriture désactivée)."),
            ("/unlock",     "Déverrouiller un salon."),
            ("/slowmode",   "Définir le délai de slowmode d'un salon."),
        ]
    },
    "📋 Signalements": {
        "description": "Système de signalement intégré avec workflow de modération.",
        "commands": [
            ("/report", "Signaler un membre au staff avec motif et preuve."),
        ]
    },
    "🗳️ Sondages": {
        "description": "Créer des sondages interactifs avec votes en temps réel.",
        "commands": [
            ("/poll", "Créer un sondage de 2 à 5 options avec barres de progression."),
        ]
    },
    "📊 Informations": {
        "description": "Commandes d'information sur les membres et le serveur.",
        "commands": [
            ("/userinfo",   "Fiche complète d'un membre (rôles, warns, dates...)."),
            ("/serverinfo", "Statistiques détaillées du serveur."),
            ("/ping",       "Afficher la latence actuelle du bot."),
            ("/me",         "Afficher votre photo de profil en grand format."),
        ]
    },
    "⚙️ Administration": {
        "description": "Configuration et administration du bot (réservé aux admins/staff).",
        "commands": [
            ("/settings",  "Ouvrir le panneau de configuration complet (admin uniquement)."),
            ("/config",    "Afficher la configuration actuelle du bot."),
            ("/announce",  "Envoyer une annonce officielle avec aperçu avant envoi (staff)."),
            ("/twitch",    "Ajouter/Supprimer/Lister les streamers Twitch surveillés (admin)."),
            ("/tiktok",    "Ajouter/Supprimer/Lister les streamers TikTok surveillés (admin)."),
        ]
    },
    "🔴 Lives": {
        "description": "Gestion des notifications de live TikTok.",
        "commands": [
            ("/tiktok action:➕ Ajouter lien:...", "Surveiller un streamer TikTok (5 gratuit / 10 premium)."),
            ("/tiktok action:➖ Supprimer lien:...","Arrêter la surveillance d'un streamer TikTok."),
            ("/tiktok action:📋 Liste",             "Voir tous les streamers TikTok et leur statut."),
        ]
    },

def build_help_home_embed() -> discord.Embed:
    embed = discord.Embed(title="📚 Aide — DLM Bot", color=COLOR_MAIN, timestamp=datetime.now())
    embed.description = (
        f"Bienvenue dans le centre d'aide de **{BOT_NAME}**.\n\n"
        f"{sep()}\n"
        f"Utilisez le menu déroulant ci-dessous pour explorer les commandes par catégorie.\n\n"
        + "\n".join(f"**{cat}** — {len(data['commands'])} commande(s)" for cat, data in HELP_CATEGORIES.items())
        + f"\n{sep()}\n"
        f"📌 **Total :** {sum(len(d['commands']) for d in HELP_CATEGORIES.values())} commandes disponibles"
    )
    embed.set_footer(text=f"Sélectionnez une catégorie • {BOT_FOOTER}")
    return embed


def build_help_category_embed(category: str) -> discord.Embed:
    data  = HELP_CATEGORIES[category]
    embed = discord.Embed(
        title       = f"{category}",
        description = f"*{data['description']}*\n\n{sep()}",
        color       = COLOR_MAIN,
        timestamp   = datetime.now()
    )
    for cmd, desc in data["commands"]:
        embed.add_field(name=cmd, value=desc, inline=False)
    embed.set_footer(text=f"{len(data['commands'])} commande(s) • {BOT_FOOTER}")
    return embed


class HelpSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label       = cat.split(" ", 1)[1],
                emoji       = cat.split(" ", 1)[0],
                description = data["description"][:50],
                value       = cat
            )
            for cat, data in HELP_CATEGORIES.items()
        ]
        super().__init__(placeholder="📂 Choisissez une catégorie...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        embed    = build_help_category_embed(selected)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpSelect())

    @ui.button(label="🏠 Accueil", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_help_home_embed(), view=HelpView())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.tree.command(name="help", description="Afficher toutes les commandes disponibles")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_home_embed(), view=HelpView(), ephemeral=True)


class AnnounceModal(ui.Modal, title="📣 Créer une Annonce"):
    titre_input = ui.TextInput(label="Titre de l'annonce", placeholder="Ex : Mise à jour du serveur", required=True, max_length=256)
    contenu_input = ui.TextInput(label="Contenu de l'annonce", style=discord.TextStyle.paragraph, placeholder="Rédigez votre annonce ici...", required=True, max_length=2000)
    couleur_input = ui.TextInput(label="Couleur (optionnel)", placeholder="blue / red / green / gold / purple — ou laisser vide", required=False, max_length=20)

    def __init__(self, salon: discord.TextChannel, roles: list):
        super().__init__()
        self.salon = salon
        self.roles = roles

    async def on_submit(self, interaction: discord.Interaction):
        couleurs  = {"blue": COLOR_INFO, "red": COLOR_DANGER, "green": COLOR_SUCCESS, "gold": COLOR_GOLD, "purple": COLOR_PURPLE}
        raw_color = self.couleur_input.value.strip().lower() if self.couleur_input.value else ""
        color     = couleurs.get(raw_color, COLOR_MAIN)
        embed     = discord.Embed(title=self.titre_input.value, description=self.contenu_input.value, color=color, timestamp=datetime.now())
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text=f"Annonce officielle • {BOT_FOOTER}")

        mentions_str = " ".join(r.mention for r in self.roles) if self.roles else "`Aucune`"
        preview = discord.Embed(title="👁️ Aperçu de votre annonce", description=f"**Salon cible :** {self.salon.mention}\n**Mentions :** {mentions_str}\n\nVérifiez l'aperçu ci-dessous puis confirmez.", color=COLOR_WARNING)
        preview.set_footer(text=f"Confirmez ou annulez l'envoi • {BOT_FOOTER}")
        view = AnnounceConfirmView(embed, self.salon, self.roles)
        await interaction.response.send_message(embeds=[preview, embed], view=view, ephemeral=True)


class AnnounceConfirmView(ui.View):
    def __init__(self, embed, salon, roles):
        super().__init__(timeout=120)
        self.embed = embed
        self.salon = salon
        self.roles = roles

    @ui.button(label="✅ Envoyer", style=discord.ButtonStyle.success)
    async def confirmer(self, interaction, button):
        content = " ".join(r.mention for r in self.roles) if self.roles else None
        await self.salon.send(content=content, embed=self.embed)
        ok = discord.Embed(title="✅ Annonce Publiée", description=f"Votre annonce a été envoyée dans {self.salon.mention}.", color=COLOR_SUCCESS)
        ok.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embeds=[ok], view=None)
        mentions_str = " ".join(r.mention for r in self.roles) if self.roles else "`Aucune`"
        log = discord.Embed(title="📣 Annonce Publiée", color=COLOR_INFO, timestamp=datetime.now())
        log.description = f"{sep()}\n**✍️ Auteur :** {interaction.user.mention}\n**📍 Salon :** {self.salon.mention}\n**📌 Titre :** {self.embed.title}\n**🔔 Mentions :** {mentions_str}\n{sep()}"
        log.set_footer(text=BOT_FOOTER)
        await send_log(interaction.guild, log)

    @ui.button(label="❌ Annuler", style=discord.ButtonStyle.danger)
    async def annuler(self, interaction, button):
        e = discord.Embed(title="🚫 Annonce Annulée", description="L'annonce n'a pas été envoyée.", color=COLOR_DANGER)
        e.set_footer(text=BOT_FOOTER)
        await interaction.response.edit_message(embeds=[e], view=None)


@bot.tree.command(name="announce", description="Envoyer une annonce officielle dans un salon")
@app_commands.describe(salon="Salon où publier l'annonce", role1="Rôle à mentionner (optionnel)", role2="2ème rôle (optionnel)", role3="3ème rôle (optionnel)")
async def announce(interaction: discord.Interaction, salon: discord.TextChannel, role1: discord.Role = None, role2: discord.Role = None, role3: discord.Role = None):
    if not await check_is_staff(interaction): return
    roles = [r for r in [role1, role2, role3] if r is not None]
    await interaction.response.send_modal(AnnounceModal(salon, roles))


@bot.event
async def on_guild_join(guild):
    await bot.ensure_guild(guild.id)


@bot.event
async def on_member_join(member):
    now = datetime.now()
    gid = member.guild.id
    await bot.ensure_guild(gid)
    if gid not in bot.join_cache: bot.join_cache[gid] = []
    bot.join_cache[gid] = [t for t in bot.join_cache[gid] if now - t < timedelta(seconds=1)]
    bot.join_cache[gid].append(now)

    s = await get_settings(gid)
    if not s: return

    if s.get("lockdown_active"):
        await member.kick(reason="Lockdown actif")
        if s["antiraid_log_id"]:
            chan = member.guild.get_channel(s["antiraid_log_id"])
            if chan:
                e = discord.Embed(title="🔒 Lockdown — Membre Expulsé", color=COLOR_DANGER, timestamp=datetime.now())
                e.description = f"**{member.mention}** expulsé (lockdown actif)."
                e.set_footer(text=BOT_FOOTER)
                await chan.send(embed=e)
        return

    threshold = s.get("antiraid_threshold") or 10
    if s["antiraid_enabled"] == 1 and len(bot.join_cache[gid]) > threshold:
        await member.kick(reason="Anti-Raid automatique")
        if s["antiraid_log_id"]:
            chan = member.guild.get_channel(s["antiraid_log_id"])
            if chan:
                e = discord.Embed(title="⚠️ RAID DÉTECTÉ & STOPPÉ", color=COLOR_DANGER, timestamp=datetime.now())
                e.description = (
                    f"**Membre expulsé :** {member.mention} (`{member.name}`)\n"
                    f"**Détection :** {len(bot.join_cache[gid])} membres en < 1s (seuil : {threshold})"
                )
                e.set_footer(text=BOT_FOOTER)
                await chan.send(embed=e)
        return

    if s["welcome_channel_id"]:
        chan = member.guild.get_channel(s["welcome_channel_id"])
        if chan:
            welcome = discord.Embed(title=f"🎉 Bienvenue sur {member.guild.name} !", color=COLOR_MAIN, timestamp=datetime.now())
            welcome.description = (
                f"Bienvenue parmi nous, {member.mention} !\n\n{sep()}\n"
                f"**👤 Profil**\n┣ **Pseudo :** `{member.name}`\n┣ **ID :** `{member.id}`\n"
                f"┗ **Compte créé :** <t:{int(member.created_at.timestamp())}:R>\n\n"
                f"**🏠 Serveur**\n┗ Tu es notre **{member.guild.member_count}ème** membre !\n{sep()}"
            )
            welcome.set_thumbnail(url=member.display_avatar.url)
            welcome.set_footer(text=BOT_FOOTER)
            await chan.send(embed=welcome)


@bot.event
async def on_member_remove(member):
    gid = member.guild.id
    await bot.ensure_guild(gid)
    s   = await get_settings(gid)
    if s and s["leave_channel_id"]:
        chan = member.guild.get_channel(s["leave_channel_id"])
        if chan and chan.permissions_for(member.guild.me).send_messages:
            joined_str = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Inconnu"
            leave = discord.Embed(title="👋 Un membre a quitté le serveur", color=COLOR_LEAVE, timestamp=datetime.now())
            leave.description = (
                f"**{member.display_name}** nous a quittés.\n\n{sep()}\n"
                f"**👤 Profil**\n┣ **Pseudo :** `{member.name}`\n┣ **ID :** `{member.id}`\n┗ **A rejoint :** {joined_str}\n\n"
                f"**🏠 Serveur**\n┗ Il nous reste **{member.guild.member_count}** membres.\n{sep()}"
            )
            leave.set_thumbnail(url=member.display_avatar.url)
            leave.set_footer(text=BOT_FOOTER)
            await chan.send(embed=leave)


@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild: return
    embed = discord.Embed(title="🗑️ Message Supprimé", color=COLOR_WARNING, timestamp=datetime.now())
    embed.description = (
        f"**Auteur :** {message.author.mention} (`{message.author.name}`)\n"
        f"**Salon :** {message.channel.mention}\n\n"
        f"**Contenu :**\n```{message.content[:1000] if message.content else 'Vide (media/embed)'}```"
    )
    embed.set_footer(text=BOT_FOOTER)
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild: return
    if before.content == after.content:        return
    embed = discord.Embed(title="✏️ Message Modifié", color=COLOR_INFO, timestamp=datetime.now())
    embed.description = (
        f"**Auteur :** {before.author.mention} (`{before.author.name}`)\n"
        f"**Salon :** {before.channel.mention}\n**[Lien]({after.jump_url})**\n\n"
        f"**Avant :**\n```{before.content[:500] if before.content else '—'}```\n"
        f"**Après :**\n```{after.content[:500] if after.content else '—'}```"
    )
    embed.set_footer(text=BOT_FOOTER)
    await send_log(before.guild, embed)


@bot.event
async def on_member_update(before, after):
    if not after.guild: return
    added   = [r for r in after.roles  if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    if added or removed:
        embed = discord.Embed(title="🏷️ Rôles Modifiés", color=COLOR_INFO, timestamp=datetime.now())
        embed.description = f"**Membre :** {after.mention} (`{after.name}`)\n"
        if added:   embed.description += f"**➕ Ajoutés :** {' '.join(r.mention for r in added)}\n"
        if removed: embed.description += f"**➖ Retirés :** {' '.join(r.mention for r in removed)}"
        embed.set_footer(text=BOT_FOOTER)
        await send_log(after.guild, embed)
    if before.nick != after.nick:
        embed = discord.Embed(title="📝 Pseudo Modifié", color=COLOR_INFO, timestamp=datetime.now())
        embed.description = (
            f"**Membre :** {after.mention}\n"
            f"**Avant :** `{before.nick or before.name}`\n"
            f"**Après :** `{after.nick or after.name}`"
        )
        embed.set_footer(text=BOT_FOOTER)
        await send_log(after.guild, embed)


@bot.event
async def on_guild_channel_create(channel):
    embed = discord.Embed(title="📂 Salon Créé", color=COLOR_SUCCESS, timestamp=datetime.now())
    embed.description = (
        f"**Nom :** {channel.mention}\n"
        f"**Type :** {'Texte' if isinstance(channel, discord.TextChannel) else 'Vocal' if isinstance(channel, discord.VoiceChannel) else 'Catégorie'}\n"
        f"**ID :** `{channel.id}`"
    )
    embed.set_footer(text=BOT_FOOTER)
    await send_log(channel.guild, embed)


@bot.event
async def on_guild_channel_delete(channel):
    embed = discord.Embed(title="🗑️ Salon Supprimé", color=COLOR_DANGER, timestamp=datetime.now())
    embed.description = (
        f"**Nom :** `{channel.name}`\n"
        f"**Type :** {'Texte' if isinstance(channel, discord.TextChannel) else 'Vocal' if isinstance(channel, discord.VoiceChannel) else 'Catégorie'}\n"
        f"**ID :** `{channel.id}`"
    )
    embed.set_footer(text=BOT_FOOTER)
    await send_log(channel.guild, embed)


@bot.event
async def on_ready():
    print(f"✅ {BOT_NAME} prêt sur {len(bot.guilds)} serveur(s).")
    conn = await db()
    c    = await conn.execute("SELECT COUNT(*) FROM pending_reports")
    row  = await c.fetchone()
    count = row[0] if row else 0
    await conn.close()
    if count > 0:
        print(f"📋 {count} signalement(s) en attente rechargé(s).")
    if not rotating_status.is_running():
        rotating_status.start()
    if not live_check_task.is_running():
        live_check_task.start()


_status_index = 0

@tasks.loop(minutes=10)
async def rotating_status():
    global _status_index
    nb_serveurs = len(bot.guilds)
    statuses = [
        discord.Activity(type=discord.ActivityType.playing,  name="🛡️ DLM Corporation"),
        discord.Activity(type=discord.ActivityType.playing,  name="⚡ /help pour les commandes"),
        discord.Activity(type=discord.ActivityType.watching, name=f"🌍 {nb_serveurs} serveur(s)"),
    ]
    await bot.change_presence(activity=statuses[_status_index % len(statuses)])
    _status_index += 1

@rotating_status.before_loop
async def before_status():
    await bot.wait_until_ready()


async def check_tiktok_live(username: str) -> bool:
    if not TIKTOK_AVAILABLE:
        return False
    try:
        client = TikTokLiveClient(unique_id=f"@{username}")
        return await client.is_live()
    except Exception:
        return False


@bot.tree.command(name="tiktok", description="Gérer la surveillance des streamers TikTok")
@app_commands.describe(
    action = "Action à effectuer",
    lien   = "Lien ou pseudo TikTok (ex: @pseudo ou tiktok.com/@pseudo)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="➕ Ajouter un streamer",   value="add"),
    app_commands.Choice(name="➖ Supprimer un streamer", value="remove"),
    app_commands.Choice(name="📋 Voir la liste",         value="list"),
])
async def tiktok_cmd(interaction: discord.Interaction, action: str, lien: str = None):
    if not interaction.user.guild_permissions.administrator:
        e = discord.Embed(
            title       = "🔒 Accès Refusé",
            description = "Seuls les **Administrateurs** peuvent gérer les streamers.",
            color       = COLOR_DANGER
        )
        e.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=e, ephemeral=True)

    await bot.ensure_guild(interaction.guild_id)
    s       = await get_settings(interaction.guild_id)
    premium = await is_premium(interaction)
    limit   = 10 if premium else 5

    if action == "list":
        conn = await db()
        c    = await conn.execute(
            "SELECT username, is_live FROM live_streamers WHERE guild_id=? AND platform='tiktok' ORDER BY is_live DESC, username ASC",
            (interaction.guild_id,)
        )
        rows = await c.fetchall()
        await conn.close()

        chan_str = ch(s.get("live_notif_channel_id")) if s else "❌ Non configuré"
        role_str = f"<@&{s.get('live_notif_role_id')}>" if s and s.get("live_notif_role_id") else "`Aucun`"
        badge    = "👑 **Premium** — jusqu'à 10 streamers" if premium else "🆓 **Gratuit** — 5 streamers max"
        count    = len(rows)
        bar_fill = round(count / limit * 10) if count > 0 else 0
        bar      = "█" * bar_fill + "░" * (10 - bar_fill)

        embed = discord.Embed(title="🎵 Surveillance TikTok", color=0xEE1D52, timestamp=datetime.now())

        if not rows:
            embed.description = (
                f"**Aucun streamer configuré** pour l'instant.\n\n"
                f"Utilise `/tiktok action:➕ Ajouter lien:@pseudo` pour commencer !\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📺 **Salon :** {chan_str}\n"
                f"🔔 **Rôle  :** {role_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Plan :** {badge}"
            )
        else:
            live_lines    = []
            offline_lines = []
            for username, is_live in rows:
                line = f"[`@{username}`](https://tiktok.com/@{username})"
                if is_live:
                    live_lines.append(f"🔴 {line}  •  [Regarder](https://tiktok.com/@{username}/live)")
                else:
                    offline_lines.append(f"⚫ {line}")

            streamer_block = ""
            if live_lines:
                streamer_block += "**— En live maintenant —**\n" + "\n".join(live_lines) + "\n\n"
            if offline_lines:
                streamer_block += "**— Hors ligne —**\n" + "\n".join(offline_lines)

            embed.description = (
                f"{streamer_block}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📺 **Salon notifications :** {chan_str}\n"
                f"🔔 **Rôle mention :** {role_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Quota :** `{bar}` **{count}/{limit}**\n"
                f"🏷️ **Plan :** {badge}"
            )

        embed.set_footer(text=f"🔴 En live  •  ⚫ Hors ligne  •  {BOT_FOOTER}")
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    if not s or not s.get("live_notif_channel_id"):
        embed = discord.Embed(
            title       = "⚠️ Configuration Requise",
            description = (
                "Aucun salon de notifications n'est configuré.\n\n"
                "**Comment faire :**\n"
                "┗ `/settings` → **🔴 Notifications Live** → sélectionner un salon"
            ),
            color = COLOR_WARNING
        )
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    if not lien:
        embed = discord.Embed(
            title       = "❌ Pseudo ou lien manquant",
            description = (
                "Tu dois renseigner un pseudo ou un lien TikTok.\n\n"
                "**Formats acceptés :**\n"
                "┣ `@pseudo`\n"
                "┗ `https://tiktok.com/@pseudo`"
            ),
            color = COLOR_DANGER
        )
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    username = lien.strip().replace("https://", "").replace("http://", "").replace("www.tiktok.com/", "").replace("tiktok.com/", "")
    if "@" in username:
        username = username.split("@")[1]
    username = username.split("?")[0].split("/")[0].strip()

    if not username:
        embed = discord.Embed(
            title       = "❌ Format Invalide",
            description = "Impossible de lire ce lien ou pseudo.",
            color = COLOR_DANGER
        )
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    conn = await db()

    if action == "add":
        c = await conn.execute(
            "SELECT id FROM live_streamers WHERE guild_id=? AND platform='tiktok' AND LOWER(username)=LOWER(?)",
            (interaction.guild_id, username)
        )
        if await c.fetchone():
            await conn.close()
            embed = discord.Embed(
                title       = "⚠️ Déjà surveillé",
                description = f"[`@{username}`](https://tiktok.com/@{username}) est déjà dans ta liste.",
                color       = COLOR_WARNING
            )
            embed.set_footer(text=BOT_FOOTER)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        c2        = await conn.execute(
            "SELECT COUNT(*) FROM live_streamers WHERE guild_id=? AND platform='tiktok'",
            (interaction.guild_id,)
        )
        count_row = await c2.fetchone()
        count     = count_row[0] if count_row else 0
        
        await conn.execute(
            "INSERT INTO live_streamers (guild_id, platform, username) VALUES (?,?,?)",
            (interaction.guild_id, "tiktok", username)
        )
        await conn.commit()
        await conn.close()

        notif_chan = interaction.guild.get_channel(s["live_notif_channel_id"])
        role_str   = f"<@&{s.get('live_notif_role_id')}>" if s.get("live_notif_role_id") else "`Aucun`"
        bar_fill   = round((count + 1) / limit * 10)
        bar        = "█" * bar_fill + "░" * (10 - bar_fill)

        embed = discord.Embed(title="✅ Streamer TikTok Ajouté", color=0xEE1D52, timestamp=datetime.now())
        embed.description = (
            f"**[@{username}](https://tiktok.com/@{username})** est maintenant surveillé !\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📺 **Salon :** {notif_chan.mention if notif_chan else '❌ Non trouvé'}\n"
            f"🔔 **Rôle  :** {role_str}\n"
            f"⏱️ **Vérification :** toutes les 5 minutes\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Quota :** `{bar}` **{count+1}/{limit}**"
        )
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    if action == "remove":
        c = await conn.execute(
            "DELETE FROM live_streamers WHERE guild_id=? AND platform='tiktok' AND LOWER(username)=LOWER(?)",
            (interaction.guild_id, username)
        )
        await conn.commit()
        await conn.close()

        if c.rowcount > 0:
            embed = discord.Embed(
                title       = "🗑️ Streamer Retiré",
                description = f"[`@{username}`](https://tiktok.com/@{username}) ne sera plus surveillé.",
                color       = COLOR_SUCCESS
            )
        else:
            embed = discord.Embed(
                title       = "❌ Introuvable",
                description = f"`@{username}` n'est pas dans ta liste de surveillance.",
                color       = COLOR_DANGER
            )
        embed.set_footer(text=BOT_FOOTER)
        return await interaction.response.send_message(embed=embed, ephemeral=True)


@tasks.loop(minutes=5)
async def live_check_task():
    conn = await db()
    c    = await conn.execute("SELECT id, guild_id, platform, username, is_live, stream_id FROM live_streamers")
    rows = await c.fetchall()
    await conn.close()

    for row_id, guild_id, platform, username, was_live, last_stream_id in rows:
        guild = bot.get_guild(guild_id)
        if not guild: continue
        s = await get_settings(guild_id)
        if not s or not s.get("live_notif_channel_id"): continue
        notif_chan = guild.get_channel(s["live_notif_channel_id"])
        if not isinstance(notif_chan, discord.TextChannel): continue
        mention = f"<@&{s['live_notif_role_id']}> " if s.get("live_notif_role_id") else ""

        if platform == "tiktok":
            is_live        = await check_tiktok_live(username)
            conn2          = await db()
            tiktok_url     = f"https://www.tiktok.com/@{username}/live"
            tiktok_profile = f"https://www.tiktok.com/@{username}"
            now_ts         = int(datetime.now().timestamp())

            if is_live and not was_live:
                embed = discord.Embed(
                    title     = f"🎵 @{username} est en live !",
                    url       = tiktok_url,
                    color     = COLOR_TIKTOK,
                    timestamp = datetime.now()
                )
                embed.set_author(
                    name     = "TikTok Live — Notification automatique",
                    icon_url = 
                )
                embed.description = (
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Rejoins le live de **@{username}** maintenant et ne rate rien !\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                embed.add_field(name="🎵 Plateforme",        value="TikTok",                                        inline=True)
                embed.add_field(name="⏰ Démarré",            value=f"<t:{now_ts}:R>",                               inline=True)
                embed.add_field(name="👤 Profil",             value=f"[@{username}]({tiktok_profile})",              inline=True)
                embed.add_field(name="🔗 Regarder en direct", value=f"[▶ Rejoindre le live]({tiktok_url})",          inline=True)
                embed.add_field(name="📌 Page profil",        value=f"[tiktok.com/@{username}]({tiktok_profile})",   inline=True)
                embed.add_field(name="🔴 Statut",             value="**EN DIRECT**",                                  inline=True)
                embed.set_footer(
                    text     = f"TikTok Live • {BOT_FOOTER}",
                    icon_url = "https://cdn-icons-png.flaticon.com/512/3046/3046121.png"
                )
                try:
                    await notif_chan.send(
                        content = f"{mention}🔴 **@{username}** vient de commencer un live TikTok !",
                        embed   = embed
                    )
                except Exception:
                    pass

            elif not is_live and was_live:
                embed_end = discord.Embed(
                    title     = f"📴 @{username} a terminé son live",
                    url       = tiktok_profile,
                    color     = COLOR_OFFLINE,
                    timestamp = datetime.now()
                )
                embed_end.set_author(
                    name     = "TikTok Live — Fin de session",
                    icon_url = 
                )
                embed_end.description = (
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Le live de **@{username}** est maintenant terminé.\n"
                    f"Rendez-vous au prochain live !\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                embed_end.add_field(name="🎵 Plateforme", value="TikTok",                           inline=True)
                embed_end.add_field(name="⏱️ Terminé",    value=f"<t:{now_ts}:R>",                  inline=True)
                embed_end.add_field(name="👤 Profil",     value=f"[@{username}]({tiktok_profile})", inline=True)
                embed_end.set_footer(
                    text     = f"TikTok Live • {BOT_FOOTER}",
                    icon_url =
                )
                try:
                    await notif_chan.send(
                        content = f"⚫ **@{username}** a terminé son live TikTok.",
                        embed   = embed_end
                    )
                except Exception:
                    pass

            await conn2.execute("UPDATE live_streamers SET is_live=? WHERE id=?", (1 if is_live else 0, row_id))
            await conn2.commit()
            await conn2.close()


@live_check_task.before_loop
async def before_live_check():
    await bot.wait_until_ready()


bot.run(config['0000000000000000000000000000000000000000000000'])
