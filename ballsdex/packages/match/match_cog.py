from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import MatchEvent, MatchGame, Player, XiTeam
from ballsdex.packages.match.sim import TeamInput, simulate_match
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
}


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
                    description=self._describe_event(e, team),
                )
            )
        if events_to_create:
            await MatchEvent.bulk_create(events_to_create)

        final_embed = self._build_result_embed(team1, team2, result, match.id)
        await self._animate_match(interaction, team1, team2, result, final_embed)

    async def _animate_match(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        team1: XiTeam,
        team2: XiTeam,
        result,
        final_embed: discord.Embed,
    ) -> None:
        """Reveal key events one by one on a single message, then swap in the full result."""
        key_events = [e for e in result.events if e.event_type in ("goal", "save", "card")]

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

            team = team1 if e.team_index == 0 else team2
            if e.event_type == "goal":
                score[e.team_index] += 1

            lines.append(
                f"{e.minute}' {EVENT_ICONS.get(e.event_type, '')} "
                f"{self._describe_event(e, team)}"
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
    def _describe_event(e, team: XiTeam) -> str:
        scorer = e.instance.countryball.country if e.instance else "Unknown"
        if e.event_type == "goal":
            if e.related_instance:
                assist = e.related_instance.countryball.country
                return f"**{scorer}** ({team.name}) scores! Assisted by {assist}."
            return f"**{scorer}** ({team.name}) scores!"
        if e.event_type == "save":
            keeper = e.related_instance.countryball.country if e.related_instance else "The keeper"
            return f"{scorer} ({team.name}) is denied by {keeper}!"
        if e.event_type == "miss":
            return f"{scorer} ({team.name}) puts it wide!"
        if e.event_type == "card":
            return f"{scorer} ({team.name}) picks up a yellow card."
        return ""

    def _build_result_embed(
        self, team1: XiTeam, team2: XiTeam, result, match_id: int
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"Full Time: {team1.name} {result.score[0]} - {result.score[1]} {team2.name}",
            description="The referee blows the final whistle \U0001f3c1",
            color=discord.Color.dark_green(),
        )

        key_events = [e for e in result.events if e.event_type in ("goal", "save", "card")]
        if key_events:
            lines = []
            for e in key_events:
                team = team1 if e.team_index == 0 else team2
                lines.append(
                    f"{e.minute}' {EVENT_ICONS.get(e.event_type, '')} "
                    f"{self._describe_event(e, team)}"
                )
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
            counts: dict[str, int] = {}
            for e in scorers:
                name = e.instance.countryball.country if e.instance else "Unknown"
                counts[name] = counts.get(name, 0) + 1
            lines = [f"{n} {name}" if n > 1 else name for name, n in counts.items()]
            embed.add_field(name="Goal Scorers", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Match #{match_id}")
        return embed


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Match(bot))