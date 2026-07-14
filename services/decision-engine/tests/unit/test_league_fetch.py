"""Contracts for ``fetch_league_context_by_roster`` — the username-free
context builder the eval harness uses (a league_id + roster_id is all
the crawler records)."""

from __future__ import annotations

import pytest

from decision_engine.clients.http import NotFoundError
from decision_engine.core.league_fetch import (
    UserInputError,
    fetch_league_context_by_roster,
)
from tests.unit.fakes import FakeHttp, league_routes


def test_builds_context_from_roster_id() -> None:
    http = FakeHttp(league_routes())
    ctx = fetch_league_context_by_roster(http, league_id="L1", roster_id=2)

    assert ctx.league.league_id == "L1"
    assert ctx.user_roster.roster_id == 2
    # User synthesised from the roster's owner — no /v1/user round trip.
    assert ctx.user.user_id == "U2"
    assert not any(c.startswith("/v1/user/") for c in http.calls)
    assert set(ctx.user_roster.players) == {"p3", "p4"}
    assert len(ctx.rosters) == 2


def test_ownerless_roster_gets_synthetic_user_id() -> None:
    routes = league_routes()
    routes["/v1/league/L1/rosters"][1]["owner_id"] = None
    ctx = fetch_league_context_by_roster(FakeHttp(routes), league_id="L1", roster_id=2)
    assert ctx.user.user_id == "roster-2"


def test_unknown_roster_id_is_user_input_error() -> None:
    with pytest.raises(UserInputError, match="roster_id=9"):
        fetch_league_context_by_roster(
            FakeHttp(league_routes()), league_id="L1", roster_id=9
        )


def test_unknown_league_is_user_input_error() -> None:
    routes = league_routes()
    routes["/v1/league/L1"] = NotFoundError("/v1/league/L1: 404")
    with pytest.raises(UserInputError, match="unknown league"):
        fetch_league_context_by_roster(FakeHttp(routes), league_id="L1", roster_id=1)
