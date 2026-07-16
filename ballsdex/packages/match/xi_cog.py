from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import BallInstance, Player, XiTeam
from ballsdex.core.utils.transformers import BallInstanceTransform
from ballsdex.packages.match.formations import (
    DEFAULT_FORMATION,
    FORMATIONS,
    SLOT_COMPATIBILITY,
    TACTIC_CHOICES,
    get_formation_slots,
)
from ballsdex.packages.match.render import draw_starting_xi, render_to_buffer
from ballsdex.packages.match.teams import build_render_payload, compute_ovr, load_instances

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.match.xi")

FORMATION_NAMES = tuple(FORMATIONS.keys())
TACTIC_NAMES = tuple(t[0] for t in TACTIC_CHOICES)


@dataclass
class XiSession:
    formation: str
    tactic: str = "Balanced"
    slots: dict[int, int] = field(default_factory=dict)  # slot_index -> ball_instance id


# In-memory building sessions, mirrors the pattern used by the picks package.
xi_sessions: dict[int, XiSession] = {}


def _slot_label(slot, filled: bool) -> str:
    from ballsdex.packages.match.formations import get_formation_slots as _gfs  # local, avoid cycle

    label = slot.code
    return f"{label} ({'filled' if filled else 'empty'})"


async def slot_autocomplete(
    interaction: discord.Interaction["BallsDexBot"], current: str
) -> list[app_commands.Choice[str]]:
    session = xi_sessions.get(interaction.user.id)
    if not session:
        return []
    slots = get_formation_slots(session.formation)
    choices: list[app_commands.Choice[str]] = []
    for slot in slots:
        same_code = [s for s in slots if s.code == slot.code]
        label = slot.code
        if len(same_code) > 1:
            label = f"{slot.code} #{same_code.index(slot) + 1}"
        filled = slot.index in session.slots
        label += " - filled" if filled else " - empty"
        if current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label, value=str(slot.index)))
    return choices[:25]


async def team_name_autocomplete(
    interaction: discord.Interaction["BallsDexBot"], current: str
) -> list[app_commands.Choice[str]]:
    teams = await XiTeam.filter(
        player__discord_id=interaction.user.id, name__icontains=current
    ).limit(25)
    return [
        app_commands.Choice(name=f"{t.name} ({t.formation}){' [active]' if t.is_active else ''}", value=t.name)
        for t in teams
    ]


class XI(commands.GroupCog, name="xi"):
    """
    Build and save Starting XI teams for the match system.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        super().__init__()

    # ---------------------------------------------------------------- begin

    @app_commands.command(name="begin", description="Start building a new Starting XI team.")
    @app_commands.choices(
        formation=[app_commands.Choice(name=f, value=f) for f in FORMATION_NAMES]
    )
    async def begin(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        formation: app_commands.Choice[str],
        tactic: Literal["Balanced", "Attacking", "Defensive"] = "Balanced",
    ):
        if interaction.user.id in xi_sessions:
            await interaction.response.send_message(
                "You already have a session in progress. Use `/xi cancel` to discard it, "
                "or `/xi save` to finish it first.",
                ephemeral=True,
            )
            return
        xi_sessions[interaction.user.id] = XiSession(formation=formation.value, tactic=tactic)
        slots = get_formation_slots(formation.value)
        embed = discord.Embed(
            title="Starting XI session started",
            description=(
                f"**Formation:** {formation.value}\n"
                f"**Tactic:** {tactic}\n"
                f"**Slots:** {', '.join(s.code for s in slots)}\n\n"
                "Use `/xi add` to fill each slot, then `/xi save <name>` when you're done."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------------------- suggest

    @app_commands.command(
        name="suggest",
        description="Auto-fill a new session with your best available cards for a formation.",
    )
    @app_commands.choices(
        formation=[app_commands.Choice(name=f, value=f) for f in FORMATION_NAMES]
    )
    async def suggest(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        formation: app_commands.Choice[str],
        tactic: Literal["Balanced", "Attacking", "Defensive"] = "Balanced",
    ):
        if interaction.user.id in xi_sessions:
            await interaction.response.send_message(
                "You already have a session in progress. Use `/xi cancel` to discard it, "
                "or `/xi save` to finish it first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        owned = await BallInstance.filter(player=player)
        if not owned:
            await interaction.followup.send("You don't own any footballers yet.")
            return

        slots = get_formation_slots(formation.value)

        def eligible_count(slot) -> int:
            allowed = SLOT_COMPATIBILITY.get(slot.code, {slot.code})
            return sum(1 for inst in owned if inst.countryball.position in allowed)

        # fill the scarcest slots first so a rare position doesn't get starved by a
        # flexible one (e.g. a CB-eligible LB slot shouldn't eat the only real CB)
        ordered_slots = sorted(slots, key=eligible_count)

        used_ids: set[int] = set()
        assignment: dict[int, int] = {}
        for slot in ordered_slots:
            allowed = SLOT_COMPATIBILITY.get(slot.code, {slot.code})
            candidates = [
                inst
                for inst in owned
                if inst.pk not in used_ids and inst.countryball.position in allowed
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda inst: inst.countryball.rarity)
            assignment[slot.index] = best.pk
            used_ids.add(best.pk)

        xi_sessions[interaction.user.id] = XiSession(
            formation=formation.value, tactic=tactic, slots=assignment
        )

        by_id = {inst.pk: inst for inst in owned}
        lines = []
        missing = []
        for slot in slots:  # formation order for readability
            inst_id = assignment.get(slot.index)
            if inst_id:
                inst = by_id[inst_id]
                lines.append(
                    f"**{slot.code}** \u2014 {inst.countryball.country} "
                    f"(OVR {inst.countryball.rarity})"
                )
            else:
                lines.append(f"**{slot.code}** \u2014 *(none available)*")
                missing.append(slot.code)

        embed = discord.Embed(
            title="Suggested Starting XI",
            description=f"**Formation:** {formation.value}\n**Tactic:** {tactic}\n\n"
            + "\n".join(lines),
            color=discord.Color.green(),
        )
        embed.add_field(name="Players", value=f"{len(assignment)}/11", inline=False)

        if missing:
            note = (
                f"\u26a0\ufe0f No eligible card owned for: {', '.join(missing)}. Fill these "
                "with `/xi add`, tweak anything else with `/xi edit`, then `/xi save <name>`."
            )
        else:
            note = (
                "Review with `/xi view`, tweak with `/xi edit`, or save it with "
                "`/xi save <name>`."
            )
        await interaction.followup.send(content=note, embed=embed)

    # ------------------------------------------------------------ add/edit

    async def _set_slot(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        slot: str,
        footballer: BallInstance,
    ):
        session = xi_sessions.get(interaction.user.id)
        if not session:
            await interaction.response.send_message(
                "You don't have a session in progress. Start one with `/xi begin`.",
                ephemeral=True,
            )
            return

        try:
            slot_index = int(slot)
        except ValueError:
            await interaction.response.send_message(
                "Invalid slot, please pick one from the autocomplete list.", ephemeral=True
            )
            return

        formation_slots = {s.index: s for s in get_formation_slots(session.formation)}
        target = formation_slots.get(slot_index)
        if target is None:
            await interaction.response.send_message(
                "That slot doesn't exist in your current formation.", ephemeral=True
            )
            return

        card_position = getattr(footballer.countryball, "position", None)
        allowed = SLOT_COMPATIBILITY.get(target.code, {target.code})
        if card_position not in allowed:
            await interaction.response.send_message(
                f"{footballer.countryball.country} plays **{card_position}**, which doesn't "
                f"fit the **{target.code}** slot. Accepted positions there: "
                f"{', '.join(sorted(allowed))}.",
                ephemeral=True,
            )
            return

        session.slots[slot_index] = footballer.pk
        filled = len(session.slots)
        await interaction.response.send_message(
            f"Placed **{footballer.countryball.country}** at **{target.code}**. "
            f"({filled}/11 slots filled)"
        )

    @app_commands.command(name="add", description="Add a footballer to a slot in your current session.")
    @app_commands.autocomplete(slot=slot_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        slot: str,
        footballer: BallInstanceTransform,
    ):
        await self._set_slot(interaction, slot, footballer)

    @app_commands.command(name="edit", description="Replace a footballer in a slot.")
    @app_commands.autocomplete(slot=slot_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        slot: str,
        footballer: BallInstanceTransform,
    ):
        await self._set_slot(interaction, slot, footballer)

    # ------------------------------------------------------------- cancel

    @app_commands.command(name="cancel", description="Discard your current XI session.")
    async def cancel(self, interaction: discord.Interaction["BallsDexBot"]):
        if xi_sessions.pop(interaction.user.id, None) is None:
            await interaction.response.send_message(
                "You don't have a session in progress.", ephemeral=True
            )
            return
        await interaction.response.send_message("Session discarded.")

    # --------------------------------------------------------------- save

    @app_commands.command(name="save", description="Save your current XI session as a named team.")
    async def save(self, interaction: discord.Interaction["BallsDexBot"], name: str):
        session = xi_sessions.get(interaction.user.id)
        if not session:
            await interaction.response.send_message(
                "You don't have a session in progress. Start one with `/xi begin`.",
                ephemeral=True,
            )
            return

        name = name.strip()[:32]
        if not name:
            await interaction.response.send_message("Please give your team a name.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        slots_payload = {str(k): v for k, v in session.slots.items()}

        existing = await XiTeam.get_or_none(player=player, name=name)
        team_count = await XiTeam.filter(player=player).count()
        if existing:
            existing.formation = session.formation
            existing.tactic = session.tactic
            existing.slots = slots_payload
            await existing.save(update_fields=["formation", "tactic", "slots", "updated_at"])
        else:
            new_team = await XiTeam.create(
                player=player,
                name=name,
                formation=session.formation,
                tactic=session.tactic,
                slots=slots_payload,
                is_active=team_count == 0,  # first team a player saves becomes active
            )
            existing = new_team

        del xi_sessions[interaction.user.id]

        filled = len(session.slots)
        note = "" if filled == 11 else f"\n\u26a0\ufe0f Only {filled}/11 slots filled - fill the rest before using this in a match."
        await interaction.response.send_message(
            f"Saved **{name}** ({session.formation}, {session.tactic}).{note}"
        )

    # ------------------------------------------------------------- delete

    @app_commands.command(name="delete", description="Delete a saved team.")
    @app_commands.autocomplete(name=team_name_autocomplete)
    async def delete(self, interaction: discord.Interaction["BallsDexBot"], name: str):
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        team = await XiTeam.get_or_none(player=player, name=name)
        if not team:
            await interaction.response.send_message(f"No saved team named **{name}**.", ephemeral=True)
            return
        await team.delete()
        await interaction.response.send_message(f"Deleted **{name}**.")

    # --------------------------------------------------------------- list

    @app_commands.command(name="list", description="List all your saved teams.")
    async def list_teams(self, interaction: discord.Interaction["BallsDexBot"]):
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        teams = await XiTeam.filter(player=player).order_by("-is_active", "name")
        if not teams:
            await interaction.response.send_message(
                "You don't have any saved teams yet. Use `/xi begin` to start one.", ephemeral=True
            )
            return

        embed = discord.Embed(title="Your Starting XI Teams", color=discord.Color.blurple())
        for team in teams:
            entries = await load_instances(team.slots)
            ovr = compute_ovr(
                [type("E", (), {"instance": inst})() for inst in entries.values()]
            ) if entries else 0.0
            filled = len(entries)
            marker = " \u2b50 active" if team.is_active else ""
            embed.add_field(
                name=f"{team.name}{marker}",
                value=(
                    f"Formation: {team.formation} | Tactic: {team.tactic}\n"
                    f"OVR: {ovr} | Players: {filled}/11"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------- active

    @app_commands.command(name="active", description="Set a saved team as your active match team.")
    @app_commands.autocomplete(name=team_name_autocomplete)
    async def active(self, interaction: discord.Interaction["BallsDexBot"], name: str):
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        team = await XiTeam.get_or_none(player=player, name=name)
        if not team:
            await interaction.response.send_message(f"No saved team named **{name}**.", ephemeral=True)
            return

        entries = await load_instances(team.slots)
        if len(entries) < 11:
            await interaction.response.send_message(
                f"**{name}** only has {len(entries)}/11 slots filled. Fill it out with `/xi begin` "
                "+ `/xi add` (same name to overwrite) before setting it active.",
                ephemeral=True,
            )
            return

        await XiTeam.filter(player=player, is_active=True).update(is_active=False)
        team.is_active = True
        await team.save(update_fields=["is_active"])
        await interaction.response.send_message(f"**{name}** is now your active match team.")

    # --------------------------------------------------------------- tactic

    @app_commands.command(name="tactic", description="Change tactic for your active team or session.")
    @app_commands.choices(
        tactic=[app_commands.Choice(name=t, value=t) for t in TACTIC_NAMES]
    )
    async def tactic(
        self, interaction: discord.Interaction["BallsDexBot"], tactic: app_commands.Choice[str]
    ):
        session = xi_sessions.get(interaction.user.id)
        if session:
            session.tactic = tactic.value
            await interaction.response.send_message(
                f"Session tactic set to **{tactic.value}**."
            )
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        team = await XiTeam.get_or_none(player=player, is_active=True)
        if not team:
            await interaction.response.send_message(
                "You don't have a session in progress or an active saved team. "
                "Use `/xi begin` or `/xi active`.",
                ephemeral=True,
            )
            return
        team.tactic = tactic.value
        await team.save(update_fields=["tactic"])
        await interaction.response.send_message(
            f"**{team.name}**'s tactic set to **{tactic.value}**."
        )

    # ----------------------------------------------------------------- view

    @app_commands.command(name="view", description="View your Starting XI card.")
    @app_commands.autocomplete(name=team_name_autocomplete)
    async def view(self, interaction: discord.Interaction["BallsDexBot"], name: str | None = None):
        await interaction.response.defer(thinking=True)

        formation: str
        slots: dict[str, int]
        tactic: str
        title: str

        if name:
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            team = await XiTeam.get_or_none(player=player, name=name)
            if not team:
                await interaction.followup.send(f"No saved team named **{name}**.")
                return
            formation, slots, tactic, title = team.formation, team.slots, team.tactic, team.name
        elif interaction.user.id in xi_sessions:
            session = xi_sessions[interaction.user.id]
            formation = session.formation
            slots = {str(k): v for k, v in session.slots.items()}
            tactic = session.tactic
            title = "Current session"
        else:
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            team = await XiTeam.get_or_none(player=player, is_active=True)
            if not team:
                await interaction.followup.send(
                    "You don't have an active saved team or a session in progress. "
                    "Use `/xi begin` or `/xi active`."
                )
                return
            formation, slots, tactic, title = team.formation, team.slots, team.tactic, team.name

        entries = await load_instances(slots)
        ovr = compute_ovr(
            [type("E", (), {"instance": inst})() for inst in entries.values()]
        ) if entries else 0.0

        payload = await build_render_payload(formation, slots)
        image = draw_starting_xi(formation, payload)
        buffer = render_to_buffer(image)

        embed = discord.Embed(title=title, color=discord.Color.gold())
        embed.add_field(name="Formation", value=formation)
        embed.add_field(name="Tactic", value=tactic)
        embed.add_field(name="OVR", value=str(ovr))
        embed.add_field(name="Players", value=f"{len(entries)}/11", inline=False)
        embed.set_image(url="attachment://starting_xi.webp")

        await interaction.followup.send(embed=embed, file=discord.File(buffer, "starting_xi.webp"))


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(XI(bot))
