"""Domain -> wire-shape converters.

Centralises Sleeper-CDN headshot URL construction so we can swap CDNs
or cache strategies later in one place.
"""

from __future__ import annotations

from decision_engine.types import Player

from api.schemas import PlayerOut

# Sleeper hosts player headshots at /players/<id>.jpg and team logos at
# /images/team_logos/nfl/<team>.png. The frontend can use these URLs
# directly (they support immutable caching via the player_id).
TEAM_LOGO_BASE = "https://sleepercdn.com/images/team_logos/nfl"


def headshot_url_for(player: Player, base: str) -> str | None:
    """Return a CDN URL for the player's headshot, or team logo for DEF."""

    if player.position == "DEF" and player.team:
        return f"{TEAM_LOGO_BASE}/{player.team.lower()}.png"
    if player.player_id and player.player_id.isdigit():
        return f"{base}/{player.player_id}.jpg"
    return None


def player_to_wire(player: Player, *, headshot_base: str) -> PlayerOut:
    return PlayerOut(
        player_id=player.player_id,
        full_name=player.full_name,
        position=player.position,
        fantasy_positions=list(player.fantasy_positions),
        team=player.team,
        status=player.status,
        injury_status=player.injury_status,
        headshot_url=headshot_url_for(player, headshot_base),
    )
