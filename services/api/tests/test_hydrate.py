"""Regression: ``headshot_url_for`` URL construction.

The browser uses this URL directly for every avatar in the lineup; a
silent regression here renders broken images for the whole league
without any console error. DEF gets a team-logo URL; everyone else gets
the player CDN URL.
"""

from __future__ import annotations

from decision_engine.types import Player

from api.hydrate import TEAM_LOGO_BASE, headshot_url_for, player_to_wire

HEADSHOT_BASE = "https://sleepercdn.com/content/nfl/players"


def _player(
    *,
    pid: str = "4881",
    position: str = "WR",
    team: str | None = "LAR",
) -> Player:
    return Player(
        player_id=pid,
        full_name="Test Player",
        position=position,
        fantasy_positions=(position,),
        team=team,
        status="Active",
        injury_status=None,
    )


def test_headshot_returns_player_cdn_for_numeric_ids() -> None:
    url = headshot_url_for(_player(pid="4881"), HEADSHOT_BASE)
    assert url == f"{HEADSHOT_BASE}/4881.jpg"


def test_headshot_returns_team_logo_for_def() -> None:
    url = headshot_url_for(_player(position="DEF", team="KC"), HEADSHOT_BASE)
    assert url == f"{TEAM_LOGO_BASE}/kc.png"


def test_headshot_returns_none_for_non_numeric_player_id() -> None:
    # Sleeper IDs for DEF entries are tri-letter codes like ``LAR`` —
    # only the numeric (player) IDs map to a player headshot.
    assert headshot_url_for(_player(pid="LAR", position="WR"), HEADSHOT_BASE) is None


def test_headshot_def_without_team_falls_through_to_player_cdn() -> None:
    # The DEF -> team-logo path requires both position=DEF *and* team set.
    # Without a team, a numeric id still gets a player-CDN URL — this just
    # documents the current behaviour so future changes are deliberate.
    url = headshot_url_for(_player(pid="4881", position="DEF", team=None), HEADSHOT_BASE)
    assert url == f"{HEADSHOT_BASE}/4881.jpg"


def test_player_to_wire_round_trips_fields() -> None:
    player = Player(
        player_id="4881",
        full_name="Puka Nacua",
        position="WR",
        fantasy_positions=("WR",),
        team="LAR",
        status="Active",
        injury_status="Questionable",
    )
    out = player_to_wire(player, headshot_base=HEADSHOT_BASE)
    assert out.player_id == "4881"
    assert out.full_name == "Puka Nacua"
    assert out.position == "WR"
    assert out.fantasy_positions == ["WR"]
    assert out.team == "LAR"
    assert out.injury_status == "Questionable"
    assert out.headshot_url == f"{HEADSHOT_BASE}/4881.jpg"
