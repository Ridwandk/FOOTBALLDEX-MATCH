from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import MatchEvent, MatchGame, Player, XiTeam
from ballsdex.packages.match.sim import (
    MatchResult,
    TeamInput,
    determine_man_of_the_match,
    simulate_match,
)
from ballsdex.packages.match.teams import build_roster_entries, compute_ovr

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.match.match")

CHALLENGE_TIMEOUT = 60.0

EVENT_ICONS = {
    "goal": "\u26bd",
    "save": "\U0001f9e4",
    "miss": "\u274c",
    "card": "\U0001f7e8",
    "tackle": "\U0001f6e1\ufe0f",
}
YELLOW_ICON = "\U0001f7e8"
RED_ICON = "\U0001f7e5"

# A little variety so the play-by-play doesn't read identically every match.
GOAL_TEMPLATES = [
    "**{scorer}** ({team}) scores!",
    "**{scorer}** ({team}) finds the net!",
    "**{scorer}** ({team}) buries it!",
    "GOAL! **{scorer}** ({team}) strikes!",
]
GOAL_ASSIST_TEMPLATES = [
    "**{scorer}** ({team}) scores! Assisted by {assist}.",
    "**{scorer}** ({team}) finishes it off, teed up by {assist}.",
    "**{scorer}** ({team}) taps it in after a lovely ball from {assist}.",
]
SAVE_TEMPLATES = [
    "{scorer} ({team}) is denied by {keeper}!",
    "{keeper} produces a big save to deny {scorer} ({team})!",
    "{scorer} ({team}) is thwarted by {keeper}!",
]
MISS_TEMPLATES = [
    "{scorer} ({team}) puts it wide!",
    "{scorer} ({team}) fires over the bar!",
    "{scorer} ({team}) can't find the target!",
]
TACKLE_TEMPLATES = [
    "{tackler} ({team}) makes a crucial tackle on {attacker}!",
    "{tackler} ({team}) reads it perfectly and strips {attacker}!",
    "{tackler} ({team}) slides in to win the ball off {attacker}!",
]
YELLOW_TEMPLATES = [
    "{scorer} ({team}) picks up a yellow card.",
    "{scorer} ({team}) is booked by the referee.",
]
RED_TEMPLATES = [
    "{scorer} ({team}) is sent off with a red card!",
    "Second yellow! {scorer} ({team}) is off for an early shower!",
]
BRACE_SUFFIX = " That's a brace!"
HATTRICK_SUFFIX = " Hat-trick!"


async def _get_active_team(discord_id: int) -> XiTeam | None:
    player, _ = await Player.get_or_create(discord_id=discord_id)
    return await XiTeam.get_or_none(player=player, is_active=True)


class ChallengeView(discord.ui.View):
    def __init__(
        self, challenger: discord.Member | discord.User, opponent: discord.Member | discord.User
    ):
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.challenger = challenger
        self.opponent = opponent
        self.result: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "This challenge isn't for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.result = None
        if self.message:
            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]
            try:
                await self.message.edit(
                    content=(
                        f"\u23f0 {self.opponent.mention} didn't respond in time. "
                        "Challenge expired."
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = True
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content=f"\u2705 {self.opponent.mention} accepted! Kicking off...", view=self
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = False
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content=f"\u274c {self.opponent.mention} declined the challenge.", view=self
        )
        self.stop()


class Match(commands.GroupCog, name="match"):
    """
    Challenge another player to a Starting XI match.
    """

    # Seconds to wait between revealing each key event during the live animation.
    EVENT_DELAY = 2.5

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="start", description="Challenge another player to a match.")
    async def start(
        self, interaction: discord.Interaction["BallsDexBot"], opponent: discord.Member
    ):
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("You can't challenge a bot.", ephemeral=True)
            return

        challenger_team = await _get_active_team(interaction.user.id)
        if not challenger_team:
            await interaction.response.send_message(
                "You don't have an active saved team. Use `/xi active` first.", ephemeral=True
            )
            return
        challenger_entries = await build_roster_entries(
            challenger_team.formation, challenger_team.slots
        )
        if len(challenger_entries) < 11:
            await interaction.response.send_message(
                f"Your active team **{challenger_team.name}** only has "
                f"{len(challenger_entries)}/11 slots filled.",
                ephemeral=True,
            )
            return

        opponent_team = await _get_active_team(opponent.id)
        if not opponent_team:
            await interaction.response.send_message(
                f"{opponent.mention} doesn't have an active saved team.", ephemeral=True
            )
            return
        opponent_entries = await build_roster_entries(opponent_team.formation, opponent_team.slots)
        if len(opponent_entries) < 11:
            await interaction.response.send_message(
                f"{opponent.mention}'s active team **{opponent_team.name}** only has "
                f"{len(opponent_entries)}/11 slots filled.",
                ephemeral=True,
            )
            return

        view = ChallengeView(interaction.user, opponent)
        embed = discord.Embed(
            title="\u26bd Match Challenge",
            description=(
                f"{interaction.user.mention} (**{challenger_team.name}**, "
                f"OVR {compute_ovr(challenger_entries)}) is challenging "
                f"{opponent.mention} (**{opponent_team.name}**, OVR {compute_ovr(opponent_entries)})!"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(content=opponent.mention, embed=embed, view=view)
        view.message = await interaction.original_response()
        await view.wait()

        player1, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player2, _ = await Player.get_or_create(discord_id=opponent.id)

        if view.result is not True:
            await MatchGame.create(
                guild_id=interaction.guild_id or 0,
                channel_id=interaction.channel_id,
                player1=player1,
                player2=player2,
                team1=challenger_team,
                team2=opponent_team,
                status="declined" if view.result is False else "expired",
            )
            return

        await self._play_match(
            interaction,
            player1,
            player2,
            challenger_team,
            opponent_team,
            challenger_entries,
            opponent_entries,
        )

    async def _play_match(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        player1: Player,
        player2: Player,
        team1: XiTeam,
        team2: XiTeam,
        entries1,
        entries2,
    ):
        team_a = TeamInput(label=team1.name, tactic=team1.tactic, roster=entries1)
        team_b = TeamInput(label=team2.name, tactic=team2.tactic, roster=entries2)

        result = simulate_match(team_a, team_b)
        # Build every event's narration once so the live animation and the
        # final embed always agree, instead of re-rolling flavor text twice.
        descriptions = self._build_descriptions(team1, team2, result)

        match = await MatchGame.create(
            guild_id=interaction.guild_id or 0,
            channel_id=interaction.channel_id,
            player1=player1,
            player2=player2,
            team1=team1,
            team2=team2,
            score1=result.score[0],
            score2=result.score[1],
            status="completed",
        )

        events_to_create = []
        for e in result.events:
            team = team1 if e.team_index == 0 else team2
            events_to_create.append(
                MatchEvent(
                    match=match,
                    minute=e.minute,
                    order=e.order,
                    event_type=e.event_type,
                    team=team,
                    ball_instance=e.instance,
                    related_ball_instance=e.related_instance,
                    description=descriptions[(e.minute, e.order)],
                )
            )
        if events_to_create:
            await MatchEvent.bulk_create(events_to_create)

        final_embed = self._build_result_embed(team1, team2, result, descriptions, match.id)
        await self._animate_match(interaction, team1, team2, result, descriptions, final_embed)

    async def _animate_match(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        team1: XiTeam,
        team2: XiTeam,
        result: MatchResult,
        descriptions: dict[tuple[int, int], str],
        final_embed: discord.Embed,
    ) -> None:
        """Reveal key events one by one on a single message, then swap in the full result."""
        key_events = [e for e in result.events if e.event_type in ("goal", "save", "card", "tackle")]

        live_embed = discord.Embed(
            title=f"\u26bd {team1.name} 0 - 0 {team2.name}",
            description="Kick off! \U0001f3c1",
            color=discord.Color.orange(),
        )
        message = await interaction.followup.send(embed=live_embed)

        lines: list[str] = []
        score = [0, 0]
        for e in key_events:
            await asyncio.sleep(self.EVENT_DELAY)

            if e.event_type == "goal":
                score[e.team_index] += 1

            lines.append(
                f"{e.minute}' {self._icon_for(e)} {descriptions[(e.minute, e.order)]}"
            )
            live_embed = discord.Embed(
                title=f"\u26bd {team1.name} {score[0]} - {score[1]} {team2.name}",
                description="\n".join(lines)[:4096],
                color=discord.Color.orange(),
            )
            live_embed.set_footer(text="Match in progress\u2026")
            try:
                await message.edit(embed=live_embed)
            except discord.HTTPException:
                pass

        await asyncio.sleep(self.EVENT_DELAY)
        try:
            await message.edit(embed=final_embed)
        except discord.HTTPException:
            await interaction.followup.send(embed=final_embed)

    @staticmethod
    def _icon_for(e) -> str:
        if e.event_type == "card":
            return RED_ICON if e.card_type == "red" else YELLOW_ICON
        return EVENT_ICONS.get(e.event_type, "")

    @staticmethod
    def _build_descriptions(
        team1: XiTeam, team2: XiTeam, result: MatchResult
    ) -> dict[tuple[int, int], str]:
        """Pick narration for every event once, tracking goal tallies for brace/hat-trick call-outs."""
        goal_counts: dict[int, int] = {}
        out: dict[tuple[int, int], str] = {}

        for e in result.events:
            team = team1 if e.team_index == 0 else team2
            key = (e.minute, e.order)
            scorer = e.instance.countryball.country if e.instance else "Unknown"

            if e.event_type == "goal":
                pid = id(e.instance)
                goal_counts[pid] = goal_counts.get(pid, 0) + 1
                streak = goal_counts[pid]
                if e.related_instance:
                    assist = e.related_instance.countryball.country
                    text = random.choice(GOAL_ASSIST_TEMPLATES).format(
                        scorer=scorer, team=team.name, assist=assist
                    )
                else:
                    text = random.choice(GOAL_TEMPLATES).format(scorer=scorer, team=team.name)
                if streak == 2:
                    text += BRACE_SUFFIX
                elif streak >= 3:
                    text += HATTRICK_SUFFIX
                out[key] = text
            elif e.event_type == "save":
                keeper = e.related_instance.countryball.country if e.related_instance else "The keeper"
                out[key] = random.choice(SAVE_TEMPLATES).format(
                    scorer=scorer, team=team.name, keeper=keeper
                )
            elif e.event_type == "miss":
                out[key] = random.choice(MISS_TEMPLATES).format(scorer=scorer, team=team.name)
            elif e.event_type == "tackle":
                attacker = e.related_instance.countryball.country if e.related_instance else "the attacker"
                out[key] = random.choice(TACKLE_TEMPLATES).format(
                    tackler=scorer, team=team.name, attacker=attacker
                )
            elif e.event_type == "card":
                templates = RED_TEMPLATES if e.card_type == "red" else YELLOW_TEMPLATES
                out[key] = random.choice(templates).format(scorer=scorer, team=team.name)
            else:
                out[key] = ""

        return out

    def _build_result_embed(
        self,
        team1: XiTeam,
        team2: XiTeam,
        result: MatchResult,
        descriptions: dict[tuple[int, int], str],
        match_id: int,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"Full Time: {team1.name} {result.score[0]} - {result.score[1]} {team2.name}",
            description="The referee blows the final whistle \U0001f3c1",
            color=discord.Color.dark_green(),
        )

        key_events = [e for e in result.events if e.event_type in ("goal", "save", "card", "tackle")]
        if key_events:
            lines = [
                f"{e.minute}' {self._icon_for(e)} {descriptions[(e.minute, e.order)]}"
                for e in key_events
            ]
            embed.add_field(
                name="\U0001f4dd Key Events", value="\n".join(lines)[:1024], inline=False
            )

        embed.add_field(
            name="Stats",
            value=(
                f"Possession: {result.possession[0]}% - {result.possession[1]}%\n"
                f"Shots: {result.shots[0]} - {result.shots[1]}\n"
                f"On Target: {result.shots_on_target[0]} - {result.shots_on_target[1]}\n"
                f"Fouls: {result.fouls[0]} - {result.fouls[1]}"
            ),
            inline=False,
        )

        scorers = [e for e in result.events if e.event_type == "goal"]
        if scorers:
            # Group by (team, player) - not just player name - so two teams
            # fielding the same country ball never get merged together.
            counts: dict[tuple[int, str], int] = {}
            for e in scorers:
                name = e.instance.countryball.country if e.instance else "Unknown"
                key = (e.team_index, name)
                counts[key] = counts.get(key, 0) + 1
            lines = []
            for (team_index, name), n in counts.items():
                team = team1 if team_index == 0 else team2
                suffix = f" \u00d7{n}" if n > 1 else ""
                lines.append(f"{name} ({team.name}){suffix}")
            embed.add_field(name="Goal Scorers", value="\n".join(lines), inline=False)

        motm = determine_man_of_the_match(result)
        if motm:
            instance, team_index = motm
            team = team1 if team_index == 0 else team2
            name = instance.countryball.country
            embed.add_field(
                name="\u2b50 Man of the Match", value=f"{name} ({team.name})", inline=False
            )

        embed.set_footer(text=f"Fulltime U+1F6A8")
        return embed


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Match(bot))