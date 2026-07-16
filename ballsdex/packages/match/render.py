"""
Renders the "Starting XI" pitch graphic: player card art dropped into circular
nodes at each formation slot, on top of the FootballDex pitch background.
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ballsdex.packages.match.formations import FormationSlot, get_formation_slots

if TYPE_CHECKING:
    from ballsdex.core.models import BallInstance

ASSETS_PATH = Path(os.path.dirname(os.path.abspath(__file__))) / "assets"
PITCH_BACKGROUND = ASSETS_PATH / "pitch.webp"

# Bounding box of the usable playing area inside pitch.webp (measured from the
# asset itself), plus a small inset so nodes never touch the pitch markings.
PITCH_X_MIN = 140
PITCH_X_MAX = 964
PITCH_Y_TOP = 245  # nearest the box / attacking end
PITCH_Y_BOTTOM = 615  # nearest the goalkeeper / defending end

NODE_RADIUS = 30
NODE_BORDER = 3
CODE_FONT_SIZE = 18

try:
    CODE_FONT = ImageFont.truetype(
        str(ASSETS_PATH.parent.parent.parent / "core/image_generator/src/demarunregular-ovpgo.ttf"),
        CODE_FONT_SIZE,
    )
except OSError:
    CODE_FONT = ImageFont.load_default()


def _slot_position(slot: FormationSlot, total_lines: int) -> tuple[int, int]:
    """Compute the pixel (x, y) center for a formation slot."""
    if total_lines <= 1:
        y = PITCH_Y_BOTTOM
    else:
        step = (PITCH_Y_BOTTOM - PITCH_Y_TOP) / (total_lines - 1)
        y = int(PITCH_Y_BOTTOM - slot.line * step)

    k = slot.line_size
    span = PITCH_X_MAX - PITCH_X_MIN
    x = int(PITCH_X_MIN + (slot.line_position + 0.5) * span / k)
    return x, y


def _circular_thumbnail(image: Image.Image, size: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGBA"), (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(fitted, (0, 0), mask=mask)
    return out


def _empty_node(draw: ImageDraw.ImageDraw, x: int, y: int, code: str) -> None:
    r = NODE_RADIUS
    draw.ellipse(
        (x - r, y - r, x + r, y + r),
        fill=(60, 60, 60, 200),
        outline=(255, 255, 255, 255),
        width=NODE_BORDER,
    )
    draw.text((x, y), code, font=CODE_FONT, fill=(220, 220, 220, 255), anchor="mm")


def draw_starting_xi(
    formation: str,
    slot_artwork: dict[int, tuple["BallInstance", Image.Image | None]],
    media_path: str = "./admin_panel/media/",
) -> Image.Image:
    """
    Draw the Starting XI pitch graphic.

    `slot_artwork` maps formation slot index (0-10) -> (ball_instance, PIL Image or
    None). A None image means the artwork couldn't be loaded and an empty node
    with just the position code is drawn instead. Slots not present in the dict
    are drawn as empty/unfilled nodes.
    """
    base = Image.open(PITCH_BACKGROUND).convert("RGBA")
    draw = ImageDraw.Draw(base)

    slots = get_formation_slots(formation)
    total_lines = 1 + len(set(s.line for s in slots if s.line > 0))

    for slot in slots:
        x, y = _slot_position(slot, total_lines)
        entry = slot_artwork.get(slot.index)

        if entry is None or entry[1] is None:
            _empty_node(draw, x, y, slot.code)
            continue

        instance, artwork = entry
        thumb = _circular_thumbnail(artwork, NODE_RADIUS * 2)
        base.alpha_composite(thumb, (x - NODE_RADIUS, y - NODE_RADIUS))
        draw.ellipse(
            (x - NODE_RADIUS, y - NODE_RADIUS, x + NODE_RADIUS, y + NODE_RADIUS),
            outline=(255, 255, 255, 255),
            width=NODE_BORDER,
        )

        # Position code just under the node
        code_y = y + NODE_RADIUS + 12
        draw.text(
            (x, code_y),
            slot.code,
            font=CODE_FONT,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
            anchor="mm",
        )

    return base


def load_artwork(instance: "BallInstance", media_path: str = "./admin_panel/media/") -> Image.Image | None:
    """Load a BallInstance's collection card artwork for use as a node thumbnail."""
    try:
        path = media_path + instance.countryball.collection_card
        return Image.open(path).convert("RGBA")
    except (FileNotFoundError, OSError):
        return None


def render_to_buffer(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="WEBP", quality=90)
    buffer.seek(0)
    return buffer
