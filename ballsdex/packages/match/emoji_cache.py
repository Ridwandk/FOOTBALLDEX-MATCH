"""
Fetches each footballer's Discord emoji (Ball.emoji_id) from Discord's CDN for
use as the circular face image on the Starting XI card, with a simple
in-memory cache so the same emoji isn't re-downloaded on every render.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import aiohttp
from PIL import Image

log = logging.getLogger("ballsdex.packages.match.emoji_cache")

EMOJI_URL = "https://cdn.discordapp.com/emojis/{emoji_id}.png?size=128"
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=8)

_cache: dict[int, Image.Image | None] = {}
_session: aiohttp.ClientSession | None = None
_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def get_emoji_image(emoji_id: int) -> Image.Image | None:
    """Returns a copy of the cached/fetched emoji image, or None if unavailable."""
    if emoji_id in _cache:
        cached = _cache[emoji_id]
        return cached.copy() if cached is not None else None

    async with _lock:
        # another coroutine may have populated this while we were waiting
        if emoji_id in _cache:
            cached = _cache[emoji_id]
            return cached.copy() if cached is not None else None

        try:
            session = await _get_session()
            async with session.get(EMOJI_URL.format(emoji_id=emoji_id), timeout=FETCH_TIMEOUT) as resp:
                if resp.status != 200:
                    _cache[emoji_id] = None
                    return None
                data = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Failed to fetch emoji %s: %s", emoji_id, exc)
            _cache[emoji_id] = None
            return None

        try:
            image = Image.open(BytesIO(data)).convert("RGBA")
        except Exception as exc:  # noqa: BLE001 - any decode failure just means "no face"
            log.warning("Failed to decode emoji %s: %s", emoji_id, exc)
            _cache[emoji_id] = None
            return None

        _cache[emoji_id] = image
        return image.copy()