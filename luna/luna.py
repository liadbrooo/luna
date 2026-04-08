"""
Luna FiveM Server Integration Cog für Red (Discord Bot)
========================================================
Verbindet deinen Redbot mit der Luna Server-Management API.

Installation:
    1. Datei nach cogs/ kopieren
    2. [p]load luna
    3. [p]lunaset apikey luna_pk_dein_key_here

API Base URL: https://api.luna.veryinsanee.space/api/public/v1
"""

import logging
from typing import Optional

import aiohttp
import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import (
    bold,
    humanize_list,
    inline,
    pagify,
)

log = logging.getLogger("red.luna")


class LunaAPIError(Exception):
    """Wird bei API-Fehlern geworfen."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}")


class LunaAPI:
    """Wrapper-Klasse für die Luna REST-API."""

    BASE_URL = "https://api.luna.veryinsanee.space/api/public/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                }
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(
        self, endpoint: str, params: Optional[dict] = None
    ) -> dict | list:
        url = f"{self.BASE_URL}{endpoint}"
        session = await self._get_session()

        log.debug("Luna API Call: GET %s params=%s", url, params)

        async with session.get(url, params=params) as resp:
            if resp.status == 401:
                raise LunaAPIError(
                    401, "Ungültiger oder fehlender API-Key."
                )
            if resp.status == 403:
                raise LunaAPIError(403, "Kein Zugriff auf diesen Endpoint.")
            if resp.status == 404:
                raise LunaAPIError(404, "Ressource nicht gefunden.")
            if resp.status == 429:
                raise LunaAPIError(429, "Zu viele Anfragen – bitte warte kurz.")
            if resp.status >= 500:
                raise LunaAPIError(
                    resp.status, "Luna-Serverfehler – bitte später erneut versuchen."
                )
            if resp.status != 200:
                text = await resp.text()
                raise LunaAPIError(resp.status, text[:500])

            return await resp.json()

    # ── Endpoint-Methoden ───────────────────────────────────────────

    async def server_status(self) -> dict | list:
        return await self.request("/server/status")

    async def players(
        self, online: Optional[bool] = None, search: Optional[str] = None, limit: int = 50
    ) -> dict | list:
        params = {"limit": min(limit, 200)}
        if online is not None:
            params["online"] = "true" if online else "false"
        if search:
            params["search"] = search
        return await self.request("/players", params=params)

    async def player_detail(self, player_id: str) -> dict | list:
        return await self.request(f"/players/{player_id}")

    async def player_bans(self, player_id: str) -> dict | list:
        return await self.request(f"/players/{player_id}/bans")

    async def player_cases(self, player_id: str) -> dict | list:
        return await self.request(f"/players/{player_id}/cases")

    async def player_gamedata(self, player_id: str, category: str) -> dict | list:
        return await self.request(f"/players/{player_id}/gamedata/{category}")

    async def bans(self, active: Optional[bool] = None) -> dict | list:
        params = {}
        if active is not None:
            params["active"] = "true" if active else "false"
        return await self.request("/bans", params=params)

    async def cases(self, case_type: Optional[str] = None) -> dict | list:
        params = {}
        if case_type:
            params["type"] = case_type
        return await self.request("/cases", params=params)

    async def staff(self) -> dict | list:
        return await self.request("/staff")

    async def gamedata(
        self, category: str, search: Optional[str] = None, limit: int = 50
    ) -> dict | list:
        params = {"limit": min(limit, 200)}
        if search:
            params["search"] = search
        return await self.request(f"/gamedata/{category}", params=params)

    async def gamedata_entry(self, category: str, entry_id: str) -> dict | list:
        return await self.request(f"/gamedata/{category}/{entry_id}")

    async def crashes(self) -> dict | list:
        return await self.request("/crashes")


# ══════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════

LUNA_EMOJI = "\U0001f319"  # 🌙


def _bool_emoji(val) -> str:
    if isinstance(val, bool):
        return "\u2705 Ja" if val else "\u274c Nein"
    if val is None:
        return "\u2014"
    return str(val)


def _fmt_timestamp(val) -> str:
    """Versucht einen Timestamp leslich zu formatieren."""
    if val is None:
        return "\u2014"
    s = str(val)
    # Falls ISO-String mit T → leslich kürzen
    if "T" in s:
        s = s.replace("T", " ").split(".")[0]
    return s


def _safe_get(d: dict, *keys, default=None):
    """Gettet verschachtelt aus einem Dict mit mehreren Key-Namen."""
    for key in keys:
        if key in d:
            return d[key]
    return default


class Luna(commands.Cog):
    """
    Luna FiveM Server-Management Integration.
    """

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5432167890)
        self.config.register_global(api_key=None)
        self._api: Optional[LunaAPI] = None

    # ── Hilfsmethoden ───────────────────────────────────────────────

    async def _get_api(self) -> LunaAPI:
        key = await self.config.api_key()
        if not key:
            raise commands.UserFeedbackCheckFailure(
                "Es wurde noch kein API-Key gesetzt. "
                f"Nutze `{inline('[p]lunaset apikey <dein_key>')}` um ihn zu konfigurieren."
            )
        if self._api is None or self._api.api_key != key:
            self._api = LunaAPI(key)
        return self._api

    def _embed(self, title: str, color: int = None) -> discord.Embed:
        color = color or discord.Color.dark_embed()
        return discord.Embed(title=f"{LUNA_EMOJI} {title}", color=color)

    @staticmethod
    def _extract_list(data) -> tuple[list, int]:
        """
    Nimm ein API-Ergebnis und gib (items_list, total_count) zurück.
    Unterstützt sowohl {"data": [...]} als auch direkte Listen.
    """
        if isinstance(data, list):
            return data, len(data)
        if isinstance(data, dict):
            items = data.get("data", data.get("players", data.get("bans", data.get("cases", data.get("crashes", data.get("staff", [])))))
            total = data.get("total", data.get("count", len(items) if isinstance(items, list) else 0))
            return items, total
        return [data], 1

    def _add_kv_fields(self, embed: discord.Embed, d: dict, skip: set = None, rename: dict = None):
        """Fügt Key-Value-Paare als Embed-Fields hinzu."""
        skip = skip or set()
        rename = rename or {}
        for k, v in d.items():
            if k in skip:
                continue
            label = rename.get(k, k.replace("_", " ").replace("-", " ").title())
            if isinstance(v, dict):
                val = "\n".join(f"{bold(k2)}: {v2}" for k2, v2 in v.items() if v2 is not None)
            elif isinstance(v, list):
                val = humanize_list([str(i) for i in v[:8]]) if v else "\u2014"
                if len(v) > 8:
                    val += f" (+{len(v) - 8} mehr)"
            else:
                val = _bool_emoji(v) if not isinstance(v, str) else v
            embed.add_field(name=label, value=val or "\u2014", inline=False)

    async def _send_error(self, ctx: commands.Context, error: LunaAPIError):
        color = discord.Color.red()
        embed = discord.Embed(
            title=f"\u274c Luna API Fehler (HTTP {error.status})",
            description=error.message,
            color=color,
        )
        await ctx.send(embed=embed)

    # ── Kommando-Gruppen ────────────────────────────────────────────

    @commands.group(name="luna", invoke_without_command=True)
    async def luna_group(self, ctx: commands.Context):
        """Zeigt den aktuellen Server-Status an."""
        await ctx.send_help()

    # ── Server Status ───────────────────────────────────────────────

    @luna_group.command(name="status")
    async def luna_status(self, ctx: commands.Context):
        """Zeigt den aktuellen Server-Status an."""
        try:
            data = await (await self._get_api()).server_status()
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        embed = self._embed("Server-Status", color=discord.Color.green())

        if isinstance(data, dict):
            # Online-Status bestimmen
            online_val = _safe_get(data, "online", "status", "server_status")
            if isinstance(online_val, str):
                is_online = online_val.lower() in ("online", "true", "1", "running")
            else:
                is_online = bool(online_val)
            status_emoji = "\u2705 Online" if is_online else "\u274c Offline"
            embed.description = bold(status_emoji)

            # Spielerzahl
            players = _safe_get(data, "players", "player_count", "playerCount", "players_online")
            max_players = _safe_get(data, "max_players", "maxPlayers", "slots", "max_slots")
            if players is not None:
                text = f"{players}"
                if max_players is not None:
                    text += f" / {max_players}"
                embed.add_field(name="Spieler", value=text, inline=True)

            self._add_kv_fields(
                embed,
                data,
                skip={"online", "status", "server_status", "players", "player_count", "playerCount", "max_players", "maxPlayers", "slots", "max_slots", "players_online"},
            )
        else:
            embed.description = f"```json\n{data}\n```"

        await ctx.send(embed=embed)

    # ── Spieler ─────────────────────────────────────────────────────

    @luna_group.group(name="players", invoke_without_command=True, aliases=["playerlist", "player"])
    async def luna_players_group(self, ctx: commands.Context):
        """Zeigt die Spielerliste an."""
        await ctx.send_help()

    @luna_players_group.command(name="list", aliases=["all"])
    async def luna_players_list(
        self,
        ctx: commands.Context,
        search: Optional[str] = None,
        limit: int = 50,
    ):
        """
        Listet alle (oder gefilterte) Spieler auf.

        **Beispiele:**
        - `[p]luna players list` – Alle Spieler
        - `[p]luna players list Max` – Suche nach "Max"
        - `[p]luna players list 20` – Maximal 20 Spieler
        """
        try:
            data = await (await self._get_api()).players(search=search, limit=limit)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Spielerliste ({total})")

        if not items:
            embed.description = "\u2014 Keine Spieler gefunden."
            return await ctx.send(embed=embed)

        for p in items[:25]:
            name = _safe_get(p, "name", "playerName", "username", default="Unbekannt")
            pid = _safe_get(p, "id", "identifier", "steam", "license", default="\u2014")
            ping = _safe_get(p, "ping")
            line = f"**{name}** ({pid})"
            if ping is not None:
                line += f" \u2022 Ping: {ping}ms"
            embed.add_field(name="\u200b", value=line, inline=False)

        if len(items) > 25:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 25} weitere Spieler nicht angezeigt.", inline=False)

        await ctx.send(embed=embed)

    @luna_players_group.command(name="online")
    async def luna_players_online(
        self,
        ctx: commands.Context,
        search: Optional[str] = None,
        limit: int = 50,
    ):
        """
        Zeigt nur online Spieler an.

        **Beispiele:**
        - `[p]luna players online`
        - `[p]luna players online Max`
        """
        try:
            data = await (await self._get_api()).players(online=True, search=search, limit=limit)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Online Spieler ({total})", color=discord.Color.green())

        if not items:
            embed.description = "\u2014 Keine Online-Spieler gefunden."
            return await ctx.send(embed=embed)

        for p in items[:25]:
            name = _safe_get(p, "name", "playerName", "username", default="Unbekannt")
            pid = _safe_get(p, "id", "identifier", "steam", "license", default="\u2014")
            ping = _safe_get(p, "ping")
            line = f"**{name}** ({pid})"
            if ping is not None:
                line += f" \u2022 Ping: {ping}ms"
            embed.add_field(name="\u200b", value=line, inline=False)

        if len(items) > 25:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 25} weitere Spieler.", inline=False)

        await ctx.send(embed=embed)

    @luna_players_group.command(name="info", aliases=["detail", "details"])
    async def luna_player_info(self, ctx: commands.Context, player_id: str):
        """
        Zeigt Details eines bestimmten Spielers.

        **Beispiel:**
        - `[p]luna players info steam:1100001abc123`
        """
        try:
            data = await (await self._get_api()).player_detail(player_id)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        embed = self._embed(f"Spieler-Details: {player_id}", color=discord.Color.blurple())

        if isinstance(data, dict):
            name = _safe_get(data, "name", "playerName", "username")
            if name:
                embed.description = bold(str(name))
            self._add_kv_fields(embed, data, skip={"name", "playerName", "username"})
        else:
            embed.description = f"```json\n{data}\n```"

        await ctx.send(embed=embed)

    # ── Spieler-Bans ────────────────────────────────────────────────

    @luna_players_group.command(name="bans")
    async def luna_player_bans(self, ctx: commands.Context, player_id: str):
        """
        Zeigt Bans eines bestimmten Spielers.

        **Beispiel:**
        - `[p]luna players bans steam:1100001abc123`
        """
        try:
            data = await (await self._get_api()).player_bans(player_id)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Bans von {player_id} ({total})", color=discord.Color.red())

        if not items:
            embed.description = "\u2705 Keine Bans für diesen Spieler gefunden."
            return await ctx.send(embed=embed)

        for i, ban in enumerate(items[:10], 1):
            reason = _safe_get(ban, "reason", default="Kein Grund angegeben")
            active = _safe_get(ban, "active", "Active")
            banned_by = _safe_get(ban, "banned_by", "staff", "staff_name", "BannedBy", default="\u2014")
            expires = _safe_get(ban, "expires", "Expires", "expire_date", default="\u2014")
            created = _safe_get(ban, "created_at", "createdAt", "created", default="\u2014")

            value = (
                f"**Grund:** {reason}\n"
                f"**Aktiv:** {_bool_emoji(active)}\n"
                f"**Von:** {banned_by}\n"
                f"**Erstellt:** {_fmt_timestamp(created)}\n"
                f"**Ablauf:** {_fmt_timestamp(expires)}"
            )
            embed.add_field(name=f"Ban #{i}", value=value, inline=False)

        if len(items) > 10:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 10} weitere Bans.", inline=False)

        await ctx.send(embed=embed)

    # ── Spieler-Cases ───────────────────────────────────────────────

    @luna_players_group.command(name="cases")
    async def luna_player_cases(self, ctx: commands.Context, player_id: str):
        """
        Zeigt Fälle (Cases) eines Spielers.

        **Beispiel:**
        - `[p]luna players cases steam:1100001abc123`
        """
        try:
            data = await (await self._get_api()).player_cases(player_id)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Fälle von {player_id} ({total})", color=discord.Color.orange())

        if not items:
            embed.description = "\u2014 Keine Fälle für diesen Spieler gefunden."
            return await ctx.send(embed=embed)

        for i, case in enumerate(items[:10], 1):
            ct = _safe_get(case, "type", "Type", default="\u2014")
            reason = _safe_get(case, "reason", "Reason", default="\u2014")
            staff = _safe_get(case, "staff", "staff_name", "Staff", default="\u2014")
            created = _safe_get(case, "created_at", "createdAt", "created", default="\u2014")

            value = (
                f"**Typ:** {ct}\n"
                f"**Grund:** {reason}\n"
                f"**Staff:** {staff}\n"
                f"**Datum:** {_fmt_timestamp(created)}"
            )
            embed.add_field(name=f"Fall #{i}", value=value, inline=False)

        if len(items) > 10:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 10} weitere Fälle.", inline=False)

        await ctx.send(embed=embed)

    # ── Spieler-Game-Data ───────────────────────────────────────────

    @luna_players_group.command(name="gamedata")
    async def luna_player_gamedata(self, ctx: commands.Context, player_id: str, category: str):
        """
        Zeigt Spieldaten eines Spielers aus einer Kategorie.

        **Beispiel:**
        - `[p]luna players gamedata steam:1100001abc123 inventory`
        """
        try:
            data = await (await self._get_api()).player_gamedata(player_id, category)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        embed = self._embed(f"Spieldaten: {player_id} [{category}]")

        if isinstance(data, dict):
            self._add_kv_fields(embed, data, skip={"id", "player_id"})
        elif isinstance(data, list):
            if not data:
                embed.description = "\u2014 Keine Daten gefunden."
            else:
                for i, item in enumerate(data[:10], 1):
                    lines = "\n".join(f"{bold(k)}: {v}" for k, v in list(item.items())[:6] if v is not None)
                    embed.add_field(name=f"#{i}", value=lines or "\u2014", inline=False)
                if len(data) > 10:
                    embed.add_field(name="Hinweis", value=f"+ {len(data) - 10} weitere Einträge.", inline=False)
        else:
            embed.description = f"```json\n{data}\n```"

        await ctx.send(embed=embed)

    # ── Bans (Global) ───────────────────────────────────────────────

    @luna_group.command(name="bans", aliases=["banlist"])
    async def luna_bans(
        self,
        ctx: commands.Context,
        active_only: bool = True,
    ):
        """
        Zeigt alle Bans an.

        **Beispiel:**
        - `[p]luna bans` – Nur aktive Bans
        - `[p]luna bans False` – Alle Bans (auch inaktive)
        """
        try:
            data = await (await self._get_api()).bans(active=active_only)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        label = "Aktive Bans" if active_only else "Alle Bans"
        embed = self._embed(f"{label} ({total})", color=discord.Color.red())

        if not items:
            embed.description = "\u2705 Keine Bans gefunden."
            return await ctx.send(embed=embed)

        for i, ban in enumerate(items[:10], 1):
            player = _safe_get(ban, "player_name", "name", "playerName", "Player", default="\u2014")
            pid = _safe_get(ban, "identifier", "id", "steam", default="\u2014")
            reason = _safe_get(ban, "reason", "Reason", default="Kein Grund")
            active = _safe_get(ban, "active", "Active")
            banned_by = _safe_get(ban, "banned_by", "staff", "staff_name", default="\u2014")
            expires = _safe_get(ban, "expires", "Expires", default="\u2014")

            value = (
                f"**Spieler:** {player} ({pid})\n"
                f"**Grund:** {reason}\n"
                f"**Aktiv:** {_bool_emoji(active)}\n"
                f"**Von:** {banned_by}\n"
                f"**Ablauf:** {_fmt_timestamp(expires)}"
            )
            embed.add_field(name=f"Ban #{i}", value=value, inline=False)

        if len(items) > 10:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 10} weitere Bans.", inline=False)

        await ctx.send(embed=embed)

    # ── Cases (Global) ──────────────────────────────────────────────

    @luna_group.command(name="cases", aliases=["caselist"])
    async def luna_cases(
        self,
        ctx: commands.Context,
        case_type: Optional[str] = None,
    ):
        """
        Zeigt alle Fälle (Cases) an.

        **Beispiele:**
        - `[p]luna cases` – Alle Fälle
        - `[p]luna cases violation` – Nur Violations
        """
        try:
            data = await (await self._get_api()).cases(case_type=case_type)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        label = f"Fälle" + (f" (Typ: {case_type})" if case_type else "")
        embed = self._embed(f"{label} ({total})", color=discord.Color.orange())

        if not items:
            embed.description = "\u2014 Keine Fälle gefunden."
            return await ctx.send(embed=embed)

        for i, case in enumerate(items[:10], 1):
            ct = _safe_get(case, "type", "Type", default="\u2014")
            player = _safe_get(case, "player_name", "name", "playerName", default="\u2014")
            reason = _safe_get(case, "reason", "Reason", default="\u2014")
            staff = _safe_get(case, "staff", "staff_name", default="\u2014")
            created = _safe_get(case, "created_at", "createdAt", default="\u2014")

            value = (
                f"**Typ:** {ct}\n"
                f"**Spieler:** {player}\n"
                f"**Grund:** {reason}\n"
                f"**Staff:** {staff}\n"
                f"**Datum:** {_fmt_timestamp(created)}"
            )
            embed.add_field(name=f"Fall #{i}", value=value, inline=False)

        if len(items) > 10:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 10} weitere Fälle.", inline=False)

        await ctx.send(embed=embed)

    # ── Staff ───────────────────────────────────────────────────────

    @luna_group.command(name="staff")
    async def luna_staff(self, ctx: commands.Context):
        """Zeigt die Team-Mitglieder an."""
        try:
            data = await (await self._get_api()).staff()
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Team-Mitglieder ({total})", color=discord.Color.blurple())

        if not items:
            embed.description = "\u2014 Kein Team gefunden."
            return await ctx.send(embed=embed)

        for i, member in enumerate(items[:25], 1):
            name = _safe_get(member, "name", "username", "staff_name", default="Unbekannt")
            role = _safe_get(member, "role", "rank", "Role", default="\u2014")
            identifier = _safe_get(member, "identifier", "steam", "id", default="\u2014")
            value = f"**Rolle:** {role}\n**Identifier:** {identifier}"
            embed.add_field(name=f"{i}. {name}", value=value, inline=False)

        if len(items) > 25:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 25} weitere Mitglieder.", inline=False)

        await ctx.send(embed=embed)

    # ── Game Data ───────────────────────────────────────────────────

    @luna_group.group(name="gamedata", invoke_without_command=True)
    async def luna_gamedata_group(self, ctx: commands.Context, category: str):
        """Zeigt Spieldaten einer Kategorie (Alias für list)."""
        await ctx.invoke(self.luna_gamedata_list, category=category)

    @luna_gamedata_group.command(name="list")
    async def luna_gamedata_list(
        self,
        ctx: commands.Context,
        category: str,
        search: Optional[str] = None,
        limit: int = 50,
    ):
        """
        Listet Spieldaten einer Kategorie.

        **Beispiele:**
        - `[p]luna gamedata list vehicles`
        - `[p]luna gamedata list money 20`
        - `[p]luna gamedata list inventory searchterm`
        """
        try:
            data = await (await self._get_api()).gamedata(category, search=search, limit=limit)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Game-Data: {category} ({total})")

        if not items:
            embed.description = "\u2014 Keine Daten gefunden."
            return await ctx.send(embed=embed)

        for i, item in enumerate(items[:15], 1):
            lines = "\n".join(f"{bold(k)}: {v}" for k, v in list(item.items())[:5] if v is not None)
            embed.add_field(name=f"#{i}", value=lines or "\u2014", inline=False)

        if len(items) > 15:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 15} weitere Einträge.", inline=False)

        await ctx.send(embed=embed)

    @luna_gamedata_group.command(name="entry", aliases=["get", "info"])
    async def luna_gamedata_entry(self, ctx: commands.Context, category: str, entry_id: str):
        """
        Zeigt einen einzelnen Game-Data-Eintrag.

        **Beispiel:**
        - `[p]luna gamedata entry vehicles 42`
        """
        try:
            data = await (await self._get_api()).gamedata_entry(category, entry_id)
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        embed = self._embed(f"Game-Data: {category} #{entry_id}")

        if isinstance(data, dict):
            self._add_kv_fields(embed, data, skip={"id"})
        else:
            embed.description = f"```json\n{data}\n```"

        await ctx.send(embed=embed)

    # ── Crashes ─────────────────────────────────────────────────────

    @luna_group.command(name="crashes")
    async def luna_crashes(self, ctx: commands.Context):
        """Zeigt die letzten Crash-Reports."""
        try:
            data = await (await self._get_api()).crashes()
        except LunaAPIError as e:
            return await self._send_error(ctx, e)

        items, total = self._extract_list(data)
        embed = self._embed(f"Crash-Reports ({total})", color=discord.Color.dark_red())

        if not items:
            embed.description = "\u2705 Keine Crash-Reports gefunden."
            return await ctx.send(embed=embed)

        for i, crash in enumerate(items[:10], 1):
            player = _safe_get(crash, "player_name", "name", default="\u2014")
            reason = _safe_get(crash, "reason", "Reason", default="\u2014")
            module = _safe_get(crash, "module", "Module", default="\u2014")
            created = _safe_get(crash, "created_at", "createdAt", default="\u2014")

            value = (
                f"**Spieler:** {player}\n"
                f"**Grund:** {reason}\n"
                f"**Modul:** {module}\n"
                f"**Zeitpunkt:** {_fmt_timestamp(created)}"
            )
            embed.add_field(name=f"Crash #{i}", value=value, inline=False)

        if len(items) > 10:
            embed.add_field(name="Hinweis", value=f"+ {len(items) - 10} weitere Reports.", inline=False)

        await ctx.send(embed=embed)

    # ── Cog Lifecycle ───────────────────────────────────────────────

    def cog_unload(self):
        if self._api:
            self.bot.loop.create_task(self._api.close())


# ══════════════════════════════════════════════════════════════════════
#  Einstellungs-Cog (LunaSet)
# ══════════════════════════════════════════════════════════════════════


class LunaSet(commands.Cog):
    """Einstellungen für die Luna API Integration."""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5432167890)
        self.config.register_global(api_key=None)

    @commands.group(name="lunaset")
    @checks.is_owner()
    async def lunaset_group(self, ctx: commands.Context):
        """Konfiguriere die Luna API."""
        pass

    @lunaset_group.command(name="apikey", aliases=["key"])
    async def lunaset_apikey(self, ctx: commands.Context, *, api_key: str):
        """
        Setzt den Luna API Key.

        **Beispiel:**
        - `[p]lunaset apikey luna_pk_your_key_here`
        """
        if not api_key.strip():
            return await ctx.send("\u274c Der API-Key darf nicht leer sein.")

        await self.config.api_key.set(api_key.strip())

        # Test-Verbindung
        try:
            api = LunaAPI(api_key.strip())
            await api.server_status()
            await api.close()
            status_msg = "\u2705 **Verbindungstest erfolgreich!**"
        except LunaAPIError as e:
            status_msg = f"\u26a0\ufe0f **Verbindungstest fehlgeschlagen:** {e.message}"

        embed = discord.Embed(
            title="\u2699\ufe0f Luna API-Key gespeichert",
            description=f"{status_msg}\n\nDer Key wurde sicher in der Bot-Konfiguration gespeichert.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @lunaset_group.command(name="show")
    async def lunaset_show(self, ctx: commands.Context):
        """Zeigt die aktuelle Konfiguration an."""
        key = await self.config.api_key()
        if key:
            masked = f"{key[:12]}{'*' * max(0, len(key) - 16)}{key[-4:]}"
        else:
            masked = "\u274c Nicht gesetzt"

        embed = discord.Embed(
            title="\u2699\ufe0f Luna Konfiguration",
            fields=[
                {"name": "API Key", "value": masked, "inline": False},
                {"name": "Base URL", "value": LunaAPI.BASE_URL, "inline": False},
            ],
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @lunaset_group.command(name="reset")
    async def lunaset_reset(self, ctx: commands.Context):
        """Löscht den gespeicherten API-Key."""
        await self.config.api_key.set(None)
        embed = discord.Embed(
            title="\u2699\ufe0f Luna Konfiguration",
            description="\u2705 API-Key wurde zurückgesetzt.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @lunaset_group.command(name="test")
    async def lunaset_test(self, ctx: commands.Context):
        """Testet die Verbindung zur Luna API."""
        key = await self.config.api_key()
        if not key:
            return await ctx.send("\u274c Kein API-Key gesetzt.")

        try:
            api = LunaAPI(key)
            data = await api.server_status()
            await api.close()

            embed = discord.Embed(
                title="\u2705 Verbindungstest erfolgreich",
                description=f"Die Luna API ist erreichbar und der Key ist gültig.",
                color=discord.Color.green(),
            )
            if isinstance(data, dict):
                online_val = _safe_get(data, "online", "status")
                if online_val is not None:
                    embed.add_field(
                        name="Server-Status",
                        value=str(online_val),
                        inline=False,
                    )
        except LunaAPIError as e:
            embed = discord.Embed(
                title="\u274c Verbindungstest fehlgeschlagen",
                description=f"HTTP {e.status}: {e.message}",
                color=discord.Color.red(),
            )

        await ctx.send(embed=embed)


async def setup(bot: Red):
    """Lädt den Luna Cog."""
    await bot.add_cog(Luna(bot))
    await bot.add_cog(LunaSet(bot))
    log.info("Luna Cog geladen – API Base: %s", LunaAPI.BASE_URL)
