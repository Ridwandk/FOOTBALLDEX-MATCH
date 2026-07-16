from typing import TYPE_CHECKING

from ballsdex.packages.match.match_cog import Match
from ballsdex.packages.match.xi_cog import XI

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(XI(bot))
    await bot.add_cog(Match(bot))
