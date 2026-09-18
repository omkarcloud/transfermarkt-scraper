"""Offline tests for the marshmallow schemas and the endpoint functions with
the fetch layer monkeypatched to serve fixtures — no network.

    python -m pytest transfermarkt/test_endpoints.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema_fields import load_query  # noqa: E402
from transfermarkt import schemas, players, clubs, competitions, matches, lookup  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


# ---- schemas ---------------------------------------------------------------------------------

def test_schema_refs_and_seasons():
    data, err = load_query(schemas.PlayerMatchLogSchema, {
        "player": "https://www.transfermarkt.com/erling-haaland/profil/spieler/418560",
        "season": "25/26", "competition": "gb1"})
    assert err is None and data == {"player": 418560, "season": 2025, "competition": "GB1", "page": 1}


def test_schema_rejects_unknown_and_bad_values():
    _, err = load_query(schemas.ClubSeasonSchema, {"club": "281", "bogus": "1"})
    assert err and "bogus" in err["errors"]
    _, err = load_query(schemas.CompetitionMatchdaySchema, {"competition": "CL", "matchday": "x"})
    assert err and "matchday" in err["errors"]
    _, err = load_query(schemas.LatestTransfersSchema, {"country": "Narnia"})
    assert err and "country" in err["errors"]


def test_advanced_schema_lists_and_ranges():
    data, err = load_query(schemas.AdvancedSearchSchema, {
        "nationality": "Norway", "position_groups": "goalkeeper,defender", "min_age": "18",
        "max_age": "30", "contract_expires": "2026,2027", "is_active": "yes"})
    assert err is None
    assert data["nationality"] == 125 and data["position_groups"] == ["goalkeeper", "defender"]
    assert data["contract_expires"] == ["2026", "2027"] and data["is_active"] is True
    _, err = load_query(schemas.AdvancedSearchSchema, {"min_age": "30", "max_age": "18"})
    assert err and "min_age" in err["errors"]


# ---- endpoints over fixtures ------------------------------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    player = fixture("tmapi_player.json")["data"]
    game = fixture("tmapi_game.json")["data"]
    perf = fixture("tmapi_performance_game.json")["data"]
    table = fixture("tmapi_competition_table.json")["data"]

    def get_api(path, params=None):
        if path.startswith("/player/") and path.endswith("/performance-game"):
            return perf
        if path.startswith("/player/"):
            return player
        if path.startswith("/game/"):
            return game
        if path.startswith("/competition/") and "/table" in path:
            return table
        if path in ("/clubs", "/competitions", "/players", "/coaches"):
            ids = [v for k, v in (params or []) if k == "ids[]"]
            if path == "/clubs":
                return [{"id": i, "name": f"Club {i}", "relativeUrl": f"/club-{i}/startseite/verein/{i}",
                         "baseDetails": {"shortName": f"C{i}", "countryId": 189}, "crestUrl": None} for i in ids]
            if path == "/competitions":
                return [{"id": i, "name": f"Competition {i}", "relativeUrl": f"/c/startseite/wettbewerb/{i}",
                         "originDetails": {"countryId": 189, "confederationId": 6}, "typeId": 1} for i in ids]
            if path == "/players":
                return [{"id": i, "name": f"Player {i}", "relativeUrl": f"/p/profil/spieler/{i}",
                         "lifeDates": {}, "attributes": {"positionId": 1}} for i in ids]
            return [{"id": i, "name": f"Coach {i}", "lifeDates": {}, "attributes": {}} for i in ids]
        raise AssertionError(f"unexpected tmapi path {path}")

    def get_api_optional(path, params=None):
        return None

    def get_page(path, params=None, expect=None):
        if "/profil/spieler/" in path:
            return fixture("player_profile.html")
        if "/verletzungen/" in path:
            return fixture("player_injuries.html")
        if "/kader/verein/" in path:
            return fixture("club_squad.html")
        if "/startseite/verein/" in path:
            return fixture("club_profile.html")
        if "/spielplan/verein/" in path:
            return fixture("club_fixtures.html")
        if "/spieltag/" in path:
            return fixture("competition_matchday.html")
        raise AssertionError(f"unexpected page {path}")

    for mod in (players, clubs, competitions, matches, lookup):
        for name, fn in (("get_api", get_api), ("get_api_optional", get_api_optional), ("get_page", get_page)):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
    monkeypatch.setattr(competitions, "_controller", lambda code: "wettbewerb")
    lookup._memo.update({"clubs": {}, "competitions": {}, "players": {}, "coaches": {}})
    yield


def test_player_profile(offline):
    out = players.get_profile("418560")
    assert out["name"] == "Erling Haaland" and out["shirt_number"] == 9
    assert out["market_value"]["current"]["amount"] == 220000000
    assert out["caps"] == 55 and out["international_goals"] == 62
    assert out["national_team"]["id"] == 3440
    assert out["agent"]["name"] == "Rafaela Pimenta" and out["agent"]["is_verified"] is True
    assert out["status"] == "active" and out["is_retired"] is False
    assert [s["name"] for s in out["social_links"]] == ["Twitter", "Facebook", "Instagram"]


def test_player_stats_and_log(offline):
    out = players.get_stats("418560", season=2026)
    assert out["totals"]["matches"] > 0 and out["stats"]
    log = players.get_match_log("418560", season=2026)
    assert log["pagination"]["total_count"] == out["totals"]["matches"]
    assert log["matches"][0]["match"]["competition"]["name"].startswith("Competition")


def test_player_injuries(offline):
    out = players.get_injuries("418560")
    assert out["injuries"][0]["injury"] == "Knock" and out["injuries"][0]["days"] == 4
    assert out["pagination"]["total_pages"] == 2


def test_club_squad(offline):
    out = clubs.get_squad("281", 2025)
    assert out["count"] == 43 and out["players"][0]["player"]["name"] == "Gianluigi Donnarumma"
    assert out["players"][0]["contract_until"] is None   # fixture hydration carries no contract
    assert out["players"][0]["signed_from"]["name"] == "Paris Saint-Germain"
    assert out["total_market_value"]["amount"] > 1_000_000_000


def test_club_fixtures(offline):
    out = clubs.get_fixtures("281", 2025)
    assert out["count"] == 60 and out["record"]["home"]["wins"] == 14
    assert out["matches"][0]["opponent"]["name"] == "Wolverhampton Wanderers"


def test_competition_standings_and_matchday(offline):
    out = competitions.get_standings("GB1", 2025)
    table = out["groups"][0]["table"]
    assert table[0]["position"] == 1 and table[0]["points"] == 85 and table[0]["club"]["name"] == "Club 11"
    md = competitions.get_matchday("GB1", 2025, 5)
    assert md["count"] == 10 and md["summary"]["matches"] == 10


def test_match_details(offline):
    out = matches.get_details("4361261")
    assert out["home_goals"] == 1 and out["away_goals"] == 0 and out["attendance"] == 73297
    assert len(out["home"]["starting_lineup"]) == 11 and out["home"]["formation"] == "4-2-3-1"
    assert out["events"][0]["type"] in ("goal", "card", "substitution")
    assert out["home"]["statistics"]["possession_percent"] == 55.4
    lineups = matches.get_lineups("4361261")
    assert "statistics" not in lineups["home"]
