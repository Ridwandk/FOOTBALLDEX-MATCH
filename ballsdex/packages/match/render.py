"""
Renders the "Starting XI" pitch graphic. Empty slots show a plain circular
placeholder with the position code. Filled slots show the footballer's
Discord emoji fused directly into a plate with the position code underneath -
one seamless card, no separate border ring.
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
FONT_PATH = ASSETS_PATH / "Inter_24pt-SemiBold.ttf"

# Measured precisely from the asset itself (pixel-sampled): where the header
# separator ends and the outer card border begins.
PHYSICAL_PITCH_TOP = 196
PHYSICAL_PITCH_BOTTOM = 672

PITCH_X_MIN = 155
PITCH_X_MAX = 970

# --- filled slot: one seamless card (emoji fused directly to a position plate) ---
CARD_W = 74
FACE_H = 74
NAME_H = 24
CARD_RADIUS = 12
CARD_BORDER = 1
CARD_TEXT_PAD_X = 4

# --- empty slot: circular placeholder, unchanged shape, separate label pill ---
CIRCLE_D = 74
CIRCLE_BORDER = 3
PILL_H = 24
PILL_MIN_W = 60
PILL_MAX_W = 140
PILL_PAD_X = 8
GAP = 5  # space between the placeholder circle and its label pill

NAME_FONT_SIZE = 12

TEXT_COLOR = (255, 255, 255, 255)
NAME_PLATE_BG = (17, 24, 39, 235)  # dark navy, near-opaque
EMPTY_FACE_BG = (55, 55, 58, 205)
EMPTY_TEXT = (215, 215, 215, 255)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


NAME_FONT = _load_font(NAME_FONT_SIZE)

# Combined top/bottom reach of whichever element (card or placeholder) is
# larger, used to place slot centers so nothing clips the header/border.
CARD_HALF = (FACE_H + NAME_H) // 2
PLACEHOLDER_HALF = CIRCLE_D // 2
PLACEHOLDER_BOTTOM_EXTENT = PLACEHOLDER_HALF + GAP + PILL_H

TOP_EXTENT = max(CARD_HALF, PLACEHOLDER_HALF)
BOTTOM_EXTENT = max(CARD_HALF, PLACEHOLDER_BOTTOM_EXTENT)


def _y_bounds() -> tuple[int, int]:
    """Safe range for slot centers so nothing clips the header or the border."""
    top = PHYSICAL_PITCH_TOP + TOP_EXTENT
    bottom = PHYSICAL_PITCH_BOTTOM - BOTTOM_EXTENT
    return top, bottom


def _slot_position(
    slot: FormationSlot, total_lines: int, y_top: int, y_bottom: int
) -> tuple[int, int]:
    """Compute the pixel (x, y) center for a formation slot."""
    if total_lines <= 1:
        y = y_bottom
    else:
        step = (y_bottom - y_top) / (total_lines - 1)
        y = int(y_bottom - slot.line * step)

    k = slot.line_size
    span = PITCH_X_MAX - PITCH_X_MIN
    x = int(PITCH_X_MIN + (slot.line_position + 0.5) * span / k)
    return x, y


def _fit_text(font: ImageFont.FreeTypeFont, draw: ImageDraw.ImageDraw, text: str, max_w: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
        return text
    truncated = text
    while truncated and draw.textbbox((0, 0), truncated + "\u2026", font=font)[2] > max_w:
        truncated = truncated[:-1]
    return (truncated + "\u2026") if truncated else text[:1]


def _draw_player_card(base: Image.Image, x: int, y: int, face: Image.Image, code: str) -> None:
    """The filled-slot look: emoji fused directly to a position plate, one unit, no ring."""
    total_h = FACE_H + NAME_H

    card = Image.new("RGBA", (CARD_W, total_h), (0, 0, 0, 0))
    fitted_face = ImageOps.fit(face.convert("RGBA"), (CARD_W, FACE_H))
    card.paste(fitted_face, (0, 0))

    cdraw = ImageDraw.Draw(card)
    cdraw.rectangle((0, FACE_H, CARD_W - 1, total_h - 1), fill=NAME_PLATE_BG)

    text = _fit_text(NAME_FONT, cdraw, code, CARD_W - CARD_TEXT_PAD_X * 2)
    cdraw.text((CARD_W / 2, FACE_H + NAME_H / 2), text, font=NAME_FONT, fill=TEXT_COLOR, anchor="mm")

    mask = Image.new("L", (CARD_W, total_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, total_h - 1), radius=CARD_RADIUS, fill=255)
    orig_alpha = card.split()[3]
    card.putalpha(Image.composite(orig_alpha, Image.new("L", card.size, 0), mask))

    base.alpha_composite(card, (x - CARD_W // 2, y - total_h // 2))

    outline = Image.new("RGBA", (CARD_W, total_h), (0, 0, 0, 0))
    ImageDraw.Draw(outline).rounded_rectangle(
        (0, 0, CARD_W - 1, total_h - 1),
        radius=CARD_RADIUS,
        outline=(255, 255, 255, 90),
        width=CARD_BORDER,
    )
    base.alpha_composite(outline, (x - CARD_W // 2, y - total_h // 2))


def _draw_pill(base: Image.Image, x: int, top_y: int, text: str) -> None:
    draw = ImageDraw.Draw(base)
    max_text_w = PILL_MAX_W - PILL_PAD_X * 2
    fitted = _fit_text(NAME_FONT, draw, text, max_text_w)
    bbox = draw.textbbox((0, 0), fitted, font=NAME_FONT)
    text_w = bbox[2] - bbox[0]
    pill_w = max(PILL_MIN_W, min(PILL_MAX_W, text_w + PILL_PAD_X * 2))

    pill = Image.new("RGBA", (pill_w, PILL_H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    pdraw.rounded_rectangle(
        (0, 0, pill_w - 1, PILL_H - 1),
        radius=6,
        fill=NAME_PLATE_BG,
        outline=(255, 255, 255, 90),
        width=1,
    )
    pdraw.text((pill_w / 2, PILL_H / 2), fitted, font=NAME_FONT, fill=TEXT_COLOR, anchor="mm")

    base.alpha_composite(pill, (x - pill_w // 2, top_y))


def _empty_slot(base: Image.Image, x: int, y: int, code: str) -> None:
    half = CIRCLE_D // 2

    circle = Image.new("RGBA", (CIRCLE_D, CIRCLE_D), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(circle)
    cdraw.ellipse(
        (0, 0, CIRCLE_D - 1, CIRCLE_D - 1),
        fill=EMPTY_FACE_BG,
        outline=(255, 255, 255, 200),
        width=CIRCLE_BORDER,
    )
    base.alpha_composite(circle, (x - half, y - half))

    _draw_pill(base, x, y + half + GAP, code)


def draw_starting_xi(
    formation: str,
    slot_artwork: dict[int, tuple["BallInstance", Image.Image | None]],
    media_path: str = "./admin_panel/media/",
) -> Image.Image:
    """
    Draw the Starting XI pitch graphic.

    `slot_artwork` maps formation slot index (0-10) -> (ball_instance, PIL Image or
    None), where the image is the footballer's Discord emoji face. A None image
    means the emoji couldn't be fetched/decoded and a plain placeholder circle is
    drawn instead. Slots not present in the dict are drawn as empty/unfilled.
    Every slot - filled or empty - is labeled with its position code, not the
    player's name.
    """
    base = Image.open(PITCH_BACKGROUND).convert("RGBA")

    slots = get_formation_slots(formation)
    total_lines = 1 + len(set(s.line for s in slots if s.line > 0))
    y_top, y_bottom = _y_bounds()

    for slot in slots:
        x, y = _slot_position(slot, total_lines, y_top, y_bottom)
        entry = slot_artwork.get(slot.index)

        if entry is None or entry[1] is None:
            _empty_slot(base, x, y, slot.code)
            continue

        _instance, face = entry
        _draw_player_card(base, x, y, face, slot.code)

    return base


def render_to_buffer(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="WEBP", quality=90)
    buffer.seek(0)
    return buffer