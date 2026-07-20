"""
The match simulator. Pure logic, no Discord/DB imports - takes two rosters and
spits out a deterministic-ish (seeded by `random`) MatchResult.

Team strength is simply the average `rarity` (== OVR, per project convention)
of the 11 starters. Tactic gives a light attack/defense multiplier on top.
Formation itself doesn't affect strength - only which slot a scorer/keeper is
drawn from for narration.

On top of the base model, a few things nudge individual players into
mattering more than "just part of the average":
  - the shooter's own rarity swings their shot quality more than before
  - a keeper who's notably stronger (or weaker) than their own team's
    average strength shifts save odds accordingly
  - a second yellow (or rare straight red) puts a team down a player for
    the rest of the match, denting their attack/defense
  - the occasional last-ditch tackle is logged as a flavor event
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

# Multiplier applied to a team's attack/defense once they go down a player.
MAN_DOWN_MULT = 0.87
# Odds, per foul, that it's a straight red rather than a yellow (or nothing).
STRAIGHT_RED_CHANCE = 0.03
YELLOW_CHANCE = 0.16
# Odds a chance that *didn't* produce a foul instead produces a flavor tackle.
TACKLE_CHANCE = 0.08


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
    event_type: str  # goal / miss / save / card / tackle
    team_index: int  # 0 or 1
    instance: "BallInstance | None" = None
    related_instance: "BallInstance | None" = None
    description: str = ""
    card_type: str | None = None  # "yellow" or "red", only set when event_type == "card"


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

    # Mutable per-match attack/defense, so a red card can knock a team down
    # for the remainder without touching the base numbers above.
    current_attack = [attack_a, attack_b]
    current_defense = [defense_a, defense_b]
    red_applied = [False, False]
    yellow_counts: dict[int, int] = {}

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
    # Chances are already in chronological (minute) order, so state we build
    # up as we go - like a red card - correctly only affects later chances.
    for minute, team_index in zip(minutes, chance_teams):
        attacking_team = teams[team_index]
        defending_team = teams[1 - team_index]
        result.shots[team_index] += 1

        shooter = _weighted_shooter(attacking_team.roster)
        keeper = defending_team.gk

        # A keeper notably above/below their own team's average shifts the
        # odds on top of the raw team defense number.
        keeper_factor = 0.0
        if keeper:
            keeper_factor = (keeper.instance.countryball.rarity - defending_team.strength) * 0.25

        atk = current_attack[team_index] + shooter.instance.countryball.rarity * 0.45
        dfc = current_defense[1 - team_index] + keeper_factor
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
            fouling_idx = 1 - team_index
            result.fouls[fouling_idx] += 1

            card_type: str | None = None
            card_roll = random.random()
            if card_roll < STRAIGHT_RED_CHANCE:
                card_type = "red"
            elif card_roll < STRAIGHT_RED_CHANCE + YELLOW_CHANCE:
                card_type = "yellow"
                fouler_id = id(fouler.instance)
                yellow_counts[fouler_id] = yellow_counts.get(fouler_id, 0) + 1
                if yellow_counts[fouler_id] >= 2:
                    card_type = "red"  # second yellow

            if card_type:
                if card_type == "red" and not red_applied[fouling_idx]:
                    red_applied[fouling_idx] = True
                    current_attack[fouling_idx] *= MAN_DOWN_MULT
                    current_defense[fouling_idx] *= MAN_DOWN_MULT
                result.events.append(
                    MatchEventData(
                        minute=minute,
                        order=order_counter,
                        event_type="card",
                        team_index=fouling_idx,
                        instance=fouler.instance,
                        card_type=card_type,
                    )
                )
        elif random.random() < TACKLE_CHANCE:
            # no foul this time around - occasionally log a clean, last-ditch
            # tackle instead, just for flavor. Doesn't touch the score/stats.
            order_counter += 1
            tackle_pool = [
                e for e in defending_team.roster if e.slot.code in DEFENDING_POSITIONS
            ] or defending_team.roster
            tackler = random.choice(tackle_pool)
            result.events.append(
                MatchEventData(
                    minute=minute,
                    order=order_counter,
                    event_type="tackle",
                    team_index=1 - team_index,
                    instance=tackler.instance,
                    related_instance=shooter.instance,
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


def determine_man_of_the_match(
    result: MatchResult,
) -> tuple["BallInstance", int] | None:
    """Simple heuristic MOTM: goals/assists/saves/tackles score points, cards cost them."""
    scores: dict[int, float] = {}
    info: dict[int, tuple["BallInstance", int]] = {}

    def bump(instance, team_index: int, amount: float) -> None:
        if instance is None:
            return
        key = id(instance)
        scores[key] = scores.get(key, 0) + amount
        info[key] = (instance, team_index)

    for e in result.events:
        if e.event_type == "goal":
            bump(e.instance, e.team_index, 4)
            if e.related_instance:
                bump(e.related_instance, e.team_index, 2)
        elif e.event_type == "save":
            if e.related_instance:  # credit the keeper, not the shooter
                bump(e.related_instance, 1 - e.team_index, 2)
        elif e.event_type == "tackle":
            bump(e.instance, e.team_index, 1)
        elif e.event_type == "card":
            bump(e.instance, e.team_index, -2 if e.card_type == "red" else -1)

    if not scores:
        return None
    best_key = max(scores, key=lambda k: scores[k])
    if scores[best_key] <= 0:
        return None
    return info[best_key]