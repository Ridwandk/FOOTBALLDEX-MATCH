"""
Shared football-domain constants for the XI builder and match simulator.

Nothing in this file talks to Discord or the database - it's pure data so both
`xi_cog.py` and `match_cog.py` (and `render.py` / `sim.py`) can import it
without circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Position(str, Enum):
    GK = "GK"
    CB = "CB"
    LB = "LB"
    RB = "RB"
    LWB = "LWB"
    RWB = "RWB"
    CDM = "CDM"
    CM = "CM"
    CAM = "CAM"
    LM = "LM"
    RM = "RM"
    LW = "LW"
    RW = "RW"
    ST = "ST"
    CF = "CF"


POSITION_CHOICES: list[tuple[str, str]] = [
    (Position.GK.value, "Goalkeeper"),
    (Position.CB.value, "Centre-Back"),
    (Position.LB.value, "Left-Back"),
    (Position.RB.value, "Right-Back"),
    (Position.LWB.value, "Left Wing-Back"),
    (Position.RWB.value, "Right Wing-Back"),
    (Position.CDM.value, "Defensive Midfielder"),
    (Position.CM.value, "Central Midfielder"),
    (Position.CAM.value, "Attacking Midfielder"),
    (Position.LM.value, "Left Midfielder"),
    (Position.RM.value, "Right Midfielder"),
    (Position.LW.value, "Left Winger"),
    (Position.RW.value, "Right Winger"),
    (Position.ST.value, "Striker"),
    (Position.CF.value, "Centre-Forward"),
]

# A card doesn't have to be an EXACT match for a slot, just a reasonable fit.
# Keys are slot codes, values are the set of card positions accepted there.
SLOT_COMPATIBILITY: dict[str, set[str]] = {
    "GK": {"GK"},
    "CB": {"CB"},
    "LB": {"LB", "LWB", "CB"},
    "RB": {"RB", "RWB", "CB"},
    "LWB": {"LWB", "LB", "LM", "LW"},
    "RWB": {"RWB", "RB", "RM", "RW"},
    "CDM": {"CDM", "CM"},
    "CM": {"CM", "CDM", "CAM"},
    "CAM": {"CAM", "CM"},
    "LM": {"LM", "LW", "LWB"},
    "RM": {"RM", "RW", "RWB"},
    "LW": {"LW", "LM", "ST", "CF"},
    "RW": {"RW", "RM", "ST", "CF"},
    "ST": {"ST", "CF"},
    "CF": {"CF", "ST"},
}

# Broad position groups, used by the match simulator to decide who's allowed
# to take a shot / who's a "defender" for the purpose of the sim.
ATTACKING_POSITIONS = {"LW", "RW", "ST", "CF", "CAM"}
MIDFIELD_POSITIONS = {"CDM", "CM", "LM", "RM"}
DEFENDING_POSITIONS = {"CB", "LB", "RB", "LWB", "RWB"}

# Formation definitions: each is a list of "lines" from defense to attack.
# The GK is implicit and NOT included here - it's always its own line.
FORMATIONS: dict[str, list[list[str]]] = {
    "4-3-3": [
        ["LB", "CB", "CB", "RB"],
        ["CM", "CM", "CM"],
        ["LW", "ST", "RW"],
    ],
    "4-4-2": [
        ["LB", "CB", "CB", "RB"],
        ["LM", "CM", "CM", "RM"],
        ["ST", "ST"],
    ],
    "3-5-2": [
        ["CB", "CB", "CB"],
        ["LM", "CM", "CM", "CM", "RM"],
        ["ST", "ST"],
    ],
    "5-3-2": [
        ["LWB", "CB", "CB", "CB", "RWB"],
        ["CM", "CM", "CM"],
        ["ST", "ST"],
    ],
    "3-4-3": [
        ["CB", "CB", "CB"],
        ["LM", "CM", "CM", "RM"],
        ["LW", "ST", "RW"],
    ],
}

DEFAULT_FORMATION = "4-3-3"


@dataclass(frozen=True)
class FormationSlot:
    """A single slot in a formation, in build order."""

    index: int  # 0-10, stable slot index used as the storage key
    code: str  # position code, e.g. "CB"
    line: int  # 0 = GK, higher = further forward
    line_size: int  # how many slots share this line
    line_position: int  # 0-indexed position within the line, left to right


def get_formation_slots(formation: str) -> list[FormationSlot]:
    """Flatten a formation into an ordered list of 11 slots (GK first)."""
    lines = FORMATIONS[formation]
    slots: list[FormationSlot] = [FormationSlot(0, "GK", 0, 1, 0)]
    idx = 1
    for line_no, line in enumerate(lines, start=1):
        for pos_in_line, code in enumerate(line):
            slots.append(FormationSlot(idx, code, line_no, len(line), pos_in_line))
            idx += 1
    return slots


class Tactic(str, Enum):
    BALANCED = "Balanced"
    ATTACKING = "Attacking"
    DEFENSIVE = "Defensive"


# Light-touch modifiers only, per design: these nudge chance generation and
# shot quality, they don't dominate the OVR-based strength calculation.
TACTIC_MODIFIERS: dict[str, dict[str, float]] = {
    Tactic.BALANCED.value: {"attack": 1.0, "defense": 1.0},
    Tactic.ATTACKING.value: {"attack": 1.15, "defense": 0.9},
    Tactic.DEFENSIVE.value: {"attack": 0.88, "defense": 1.15},
}

TACTIC_CHOICES: list[tuple[str, str]] = [(t.value, t.value) for t in Tactic]