"""
Shared helpers for turning a saved/in-progress XI (formation + slot->instance
mapping) into loaded BallInstance objects, artwork, and OVR - used by both the
XI builder commands and the match simulator so the two never drift apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ballsdex.core.models import BallInstance
from ballsdex.packages.match.formations import get_formation_slots
from ballsdex.packages.match.render import load_artwork
from ballsdex.packages.match.sim import RosterEntry

if TYPE_CHECKING:
    from PIL.Image import Image


async def load_instances(slots: dict[str, int]) -> dict[int, BallInstance]:
    """slots maps slot_index (as stored, may be str keys from JSON) -> ball_instance id."""
    ids = [int(v) for v in slots.values() if v is not None]
    if not ids:
        return {}
    instances = await BallInstance.filter(id__in=ids).prefetch_related("player")
    by_id = {i.pk: i for i in instances}
    return {int(k): by_id[int(v)] for k, v in slots.items() if v is not None and int(v) in by_id}


async def build_roster_entries(formation: str, slots: dict[str, int]) -> list[RosterEntry]:
    """Returns RosterEntry list (only for filled slots) in formation slot order."""
    instances_by_slot = await load_instances(slots)
    formation_slots = {s.index: s for s in get_formation_slots(formation)}
    entries = []
    for slot_index, instance in instances_by_slot.items():
        slot = formation_slots.get(slot_index)
        if slot is None:
            continue
        entries.append(RosterEntry(slot=slot, instance=instance))
    entries.sort(key=lambda e: e.slot.index)
    return entries


def compute_ovr(entries: list[RosterEntry]) -> float:
    if not entries:
        return 0.0
    return round(sum(e.instance.countryball.rarity for e in entries) / len(entries), 1)


async def build_render_payload(
    formation: str, slots: dict[str, int]
) -> dict[int, tuple[BallInstance, "Image | None"]]:
    instances_by_slot = await load_instances(slots)
    payload: dict[int, tuple[BallInstance, "Image | None"]] = {}
    for slot_index, instance in instances_by_slot.items():
        artwork = load_artwork(instance)
        payload[slot_index] = (instance, artwork)
    return payload
