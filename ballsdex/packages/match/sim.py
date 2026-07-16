"""
The match simulator. Pure logic, no Discord/DB imports - takes two rosters and
spits out a deterministic-ish (seeded by `random`) MatchResult.

Team strength is simply the average `rarity` (== OVR, per project convention)
of the 11 starters. Tactic gives a light attack/defense multiplier on top.
Formation itself doesn't affect strength - only which slot a scorer/keeper is
drawn from for narration.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ballsdex.packages.match.formations import (
    ATTACKING_POSITIONS,
    DEFENDING_POSITIONS,
    MIDFIELD_POSITIONS,
    TACTIC_MODIFIERS,
    FormationSlot,
)

if TYPE_CHECKING:
    from ballsdex.core.models import BallInstance


@dataclass
class RosterEntry:
    slot: FormationSlot
    instance: "BallInstance"


@dataclass
class TeamInput:
    label: str  # display name, e.g. team name or player mention
    tactic: str
    roster: list[RosterEntry]  # 11 entries, slot 0 is always GK

    @property
    def gk(self) -> RosterEntry | None:
        for entry in self.roster:
            if entry.slot.code == "GK":
                return entry
        return None

    @property
    def strength(self) -> float:
        if not self.roster:
            return 0.0
        return sum(e.instance.countryball.rarity for e in self.roster) / len(self.roster)


@dataclass
class MatchEventData:
    minute: int
    order: int
    event_type: str  # goal / miss / save / card / foul
    team_index: int  # 0 or 1
    instance: "BallInstance | None" = None
    related_instance: "BallInstance | None" = None
    description: str = ""


@dataclass
class MatchResult:
    score: list[int] = field(default_factory=lambda: [0, 0])
    events: list[MatchEventData] = field(default_factory=list)
    possession: list[float] = field(default_factory=lambda: [50.0, 50.0])
    shots: list[int] = field(default_factory=lambda: [0, 0])
    shots_on_target: list[int] = field(default_factory=lambda: [0, 0])
    fouls: list[int] = field(default_factory=lambda: [0, 0])


def _weighted_shooter(roster: list[RosterEntry]) -> RosterEntry:
    weights = []
    for entry in roster:
        code = entry.slot.code
        if code in ATTACKING_POSITIONS:
            w = 6.0
        elif code in MIDFIELD_POSITIONS:
            w = 2.5
        elif code in DEFENDING_POSITIONS:
            w = 0.6
        else:  # GK
            w = 0.02
        weights.append(w)
    return random.choices(roster, weights=weights, k=1)[0]


def _effective(team: TeamInput) -> tuple[float, float]:
    mod = TACTIC_MODIFIERS.get(team.tactic, TACTIC_MODIFIERS["Balanced"])
    return team.strength * mod["attack"], team.strength * mod["defense"]


def simulate_match(team_a: TeamInput, team_b: TeamInput) -> MatchResult:
    attack_a, defense_a = _effective(team_a)
    attack_b, defense_b = _effective(team_b)
    teams = [team_a, team_b]

    # Total chances this match, and how they split between the two sides,
    # weighted by relative attacking strength.
    total_chances = random.randint(9, 19)
    weight_a = max(attack_a, 1.0)
    weight_b = max(attack_b, 1.0)
    share_a = weight_a / (weight_a + weight_b)
    chances_a = sum(1 for _ in range(total_chances) if random.random() < share_a)
    chances_b = total_chances - chances_a

    result = MatchResult()
    minute_pool_regulation = sorted(random.sample(range(1, 91), min(total_chances, 90)))
    # a couple of the chances can land in stoppage time
    extra_minutes = sorted(random.sample(range(90, 96), max(0, total_chances - 90)))
    minutes = sorted(minute_pool_regulation + extra_minutes)[:total_chances]

    chance_teams = [0] * chances_a + [1] * chances_b
    random.shuffle(chance_teams)

    order_counter = 0
    for minute, team_index in zip(minutes, chance_teams):
        attacking_team = teams[team_index]
        defending_team = teams[1 - team_index]
        result.shots[team_index] += 1

        shooter = _weighted_shooter(attacking_team.roster)

        atk = (attack_a if team_index == 0 else attack_b) + shooter.instance.countryball.rarity * 0.3
        dfc = defense_b if team_index == 0 else defense_a
        # logistic-ish goal probability, capped to keep games from being auto-blowouts
        diff = atk - dfc
        goal_chance = 0.18 + max(-0.13, min(0.32, diff * 0.012))

        roll = random.random()
        order_counter += 1
        if roll < goal_chance:
            result.score[team_index] += 1
            result.shots_on_target[team_index] += 1
            assist = None
            if random.random() < 0.55:
                candidates = [
                    e for e in attacking_team.roster if e.instance is not shooter.instance
                ]
                if candidates:
                    assist = _weighted_shooter(candidates).instance
            result.events.append(
                MatchEventData(
                    minute=minute,
                    order=order_counter,
                    event_type="goal",
                    team_index=team_index,
                    instance=shooter.instance,
                    related_instance=assist,
                )
            )
        elif roll < goal_chance + 0.30:
            # on target but saved
            result.shots_on_target[team_index] += 1
            keeper = defending_team.gk
            result.events.append(
                MatchEventData(
                    minute=minute,
                    order=order_counter,
                    event_type="save",
                    team_index=team_index,
                    instance=shooter.instance,
                    related_instance=keeper.instance if keeper else None,
                )
            )
        else:
            result.events.append(
                MatchEventData(
                    minute=minute,
                    order=order_counter,
                    event_type="miss",
                    team_index=team_index,
                    instance=shooter.instance,
                )
            )

        # small independent chance of a foul from the defending side around this passage of play
        if random.random() < 0.12:
            order_counter += 1
            defender_pool = [
                e for e in defending_team.roster if e.slot.code in DEFENDING_POSITIONS
            ] or defending_team.roster
            fouler = random.choice(defender_pool)
            result.fouls[1 - team_index] += 1
            if random.random() < 0.18:
                result.events.append(
                    MatchEventData(
                        minute=minute,
                        order=order_counter,
                        event_type="card",
                        team_index=1 - team_index,
                        instance=fouler.instance,
                    )
                )

    # possession is a light function of shot share plus some noise, always sums to 100
    if total_chances:
        base_a = 100 * chances_a / total_chances
    else:
        base_a = 50.0
    noise = random.uniform(-8, 8)
    poss_a = min(78.0, max(22.0, base_a + noise))
    result.possession = [round(poss_a, 1), round(100 - poss_a, 1)]

    result.events.sort(key=lambda e: (e.minute, e.order))
    return result
