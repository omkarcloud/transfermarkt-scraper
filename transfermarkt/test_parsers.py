"""Offline tests for the Transfermarkt value parsers, reference resolvers,
HTML normalizers and tmapi normalizers.

    python -m pytest transfermarkt/test_parsers.py -q

Fixtures are real www.transfermarkt.com pages / tmapi payloads captured
2026-09-18 (the performance log trimmed to 40 games). No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transfermarkt import parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


# ---- refs -------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("418560", 418560),
    ("https://www.transfermarkt.com/erling-haaland/profil/spieler/418560", 418560),
    ("https://www.transfermarkt.co.uk/erling-haaland/leistungsdaten/spieler/418560/plus/0?saison=2024", 418560),
    ("www.transfermarkt.de/erling-haaland/profil/spieler/418560", 418560),
])
def test_resolve_player(value, expected):
    assert refs.resolve_player(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "https://example.com/spieler/1", "https://www.transfermarkt.com/x/y/verein/1"])
def test_resolve_player_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_player(value)


def test_resolve_others():
    assert refs.resolve_club("https://www.transfermarkt.com/manchester-city/kader/verein/281/saison_id/2025") == 281
    assert refs.resolve_manager("https://www.transfermarkt.com/pep-guardiola/profil/trainer/5672") == 5672
    assert refs.resolve_match("https://www.transfermarkt.com/manchester-united_fulham-fc/index/spielbericht/4361261") == 4361261
    assert refs.resolve_competition("gb1") == "GB1"
    assert refs.resolve_competition("https://www.transfermarkt.com/uefa-champions-league/startseite/pokalwettbewerb/CL") == "CL"
    with pytest.raises(ValueError):
        refs.resolve_competition("premier league")


@pytest.mark.parametrize("value,expected", [
    ("2025", 2025), ("25/26", 2025), ("2025/26", 2025), ("2025/2026", 2025), ("2025-26", 2025), ("99/00", 1999),
])
def test_resolve_season(value, expected):
    assert refs.resolve_season(value) == expected


def test_lookups():
    assert refs.country(189) == {"id": 189, "name": "England", "code": "ENG"}
    assert refs.position(14)["short_name"] == "CF"
    assert refs.resolve_country_id("norway") == 125
    assert refs.resolve_country_id("ENG") == 189
    assert refs.resolve_position_id("cb") == 3
    with pytest.raises(ValueError):
        refs.resolve_country_id("Narnia")


# ---- value parsers -------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("€220.00m", (220000000, "EUR")), ("€1.43bn", (1430000000, "EUR")), ("€500k", (500000, "EUR")),
    ("€ 145.00 m", (145000000, "EUR")), ("€-189.18m", (-189180000, "EUR")),
    ("€4,123,650,000", (4123650000, "EUR")), ("£12.5m", (12500000, "GBP")), ("-", (None, None)), (None, (None, None)),
])
def test_money(value, expected):
    assert P.money(value) == expected


@pytest.mark.parametrize("value,kind,amount", [
    ("free transfer", "free", 0), ("loan transfer", "loan", None), ("Loan fee: €5.00m", "loan", 5000000),
    ("End of loan", "end_of_loan", None), ("?", "undisclosed", None), ("€72.00m", "transfer", 72000000),
    ("-", "none", None),
])
def test_fee(value, kind, amount):
    out = P.fee(value)
    assert out["type"] == kind and out["amount"] == amount


@pytest.mark.parametrize("value,expected", [
    ("01/07/2022", "2022-07-01"), ("17.09.2026", "2026-09-17"), ("Sat 16/08/2025", "2025-08-16"),
    ("Fri, 16/08/24", "2024-08-16"), ("2000-07-21", "2000-07-21"), ("Sat 20/09/2025 - 1:30 PM", "2025-09-20"),
    ("-", None), ("", None),
])
def test_iso_date(value, expected):
    assert P.iso_date(value) == expected


def test_misc_values():
    assert P.date_and_age("21/07/2000 (26)") == ("2000-07-21", 26)
    assert P.season_obj("25/26") == {"id": 2025, "name": "25/26"}
    assert P.season_obj("16/17 (01/07/2016)") == {"id": 2016, "name": "16/17"}
    assert P.season_obj({"id": 2025, "display": "25/26"}) == {"id": 2025, "name": "25/26"}
    assert P.height_m("1,95 m") == 1.95
    assert P.to_int("52.640") == 52640 and P.to_int("73,297") == 73297 and P.to_int("25") == 25
    assert P.percent("17 68.0 %") == 68.0
    assert P.minutes("3.060'") == 3060
    assert P.id_in("/wettbewerb/startseite/wettbewerb/ES1", "competition") == "ES1"
    assert P.id_in("/x/startseite/pokalwettbewerb/CL?saison_id=2026", "competition") == "CL"
    assert P.slug_of("/manchester-city/startseite/verein/281") == "manchester-city"
    assert P.image("https://img.a.transfermarkt.technology/portrait/small/1.jpg", "medium").endswith("/portrait/medium/1.jpg")


# ---- HTML normalizers ----------------------------------------------------------------------

def test_player_profile_blocks():
    doc = P.soup(fixture("player_profile.html"))
    hero = P.data_header(doc)
    assert P.text(hero["Contract expires"]) == "30/06/2034"
    info = P.info_table(doc)
    assert P.text(info["Name in home country"]) == "Erling Braut Håland"
    assert [a["href"] for a in info["Social-Media"].select("a")][0].startswith("http://twitter.com")


def test_club_squad_table():
    doc = P.soup(fixture("club_squad.html"))
    table = P.find_table(doc, "Squad")
    rows = P.items_table(table)
    assert len(rows) == 43
    first = rows[0]
    player = P.player_ref(first["Player"])
    assert player["id"] == 315858 and player["name"] == "Gianluigi Donnarumma"
    assert player["position"] == "Goalkeeper"
    assert player["link"] == "https://www.transfermarkt.com/gianluigi-donnarumma/profil/spieler/315858"
    assert P.flags(first["Nat."]) == [{"id": 75, "name": "Italy", "code": "ITA"}]
    assert P.money_obj(P.text(first["Market value"])) == {"amount": 45000000, "currency": "EUR"}
    signed = P.club_ref(first["Signed from"])
    assert signed["id"] == 583 and signed["name"] == "Paris Saint-Germain"   # fee suffix stripped


def test_club_profile_blocks():
    doc = P.soup(fixture("club_profile.html"))
    facts = P.info_spans(P.box_by_headline(doc, "Stats & facts"))
    assert P.joined_text(facts["Official club name"]) == "Manchester City Football Club"
    assert P.joined_text(facts["Founded"]) == "23/11/1880"
    hero = P.data_header(doc)
    assert P.to_int(P.text(hero["Table position"])) == 2


def test_search_sections_and_hits():
    doc = P.soup(fixture("search_manchester.html"))
    assert P.hits(doc, "Clubs") == 28
    assert P.total_pages(doc, "Verein_page") >= 2


def test_matchday_tables():
    doc = P.soup(fixture("competition_matchday.html"))
    from transfermarkt.competitions import _match_from_table
    tables = [t for t in doc.select(".box table") if not t.select_one("thead")]
    rows = [r for r in (_match_from_table(t, {"id": "GB1"}) for t in tables) if r and r["match_id"]]
    assert len(rows) == 10
    assert rows[0]["home_club"]["id"] == 31 and rows[0]["away_club"]["id"] == 29
    assert rows[0]["home_goals"] == 2 and rows[0]["away_goals"] == 1
    assert rows[0]["date"] == "2025-09-20" and rows[0]["time"] == "1:30 PM"
    assert rows[0]["home_club"]["table_position"] == 1


def test_latest_transfers_rows():
    doc = P.soup(fixture("latest_transfers.html"))
    rows = P.items_table(doc.select_one("table.items"))
    assert len(rows) == 25
    first = rows[0]
    assert P.player_ref(first["Player"])["name"] == "Reiss Nelson"
    left = P.club_ref(first["Left"])
    assert left["id"] == 11 and left["name"] == "Arsenal FC"
    assert P.iso_date(P.text(first["Transfer date"]))


def test_injuries_table():
    doc = P.soup(fixture("player_injuries.html"))
    rows = P.items_table(P.find_table(doc, "Injury history"))
    assert rows[0]["Injury"].get_text(strip=True) == "Knock"
    assert P.to_int(P.text(rows[0]["Days"])) == 4
    assert P.total_pages(doc) == 2


def test_fixtures_parse():
    from transfermarkt.clubs import _fixture_row
    doc = P.soup(fixture("club_fixtures.html"))
    table = [t for t in doc.select(".box table") if "Opponent" in P.header_labels(t)][0]
    row = _fixture_row(P.table_rows(table)[0], {"id": "GB1"})
    assert row["match_id"] == 4625780 and row["venue"] == "away" and row["result"] == "0:4"
    assert row["opponent"]["id"] == 543 and row["opponent"]["table_position"] == 20
    assert row["own_goals_scored"] == 4 and row["opponent_goals"] == 0


# ---- tmapi normalizers ----------------------------------------------------------------------

def test_api_player():
    p = fixture("tmapi_player.json")["data"]
    mv = P.api_market_value(p["marketValueDetails"])
    assert mv["current"]["amount"] == 220000000 and mv["current"]["updated_at"] == "2026-07-22"
    assert mv["change"] == {"direction": "increased", "amount": 20000000, "percent": 10.0}
    assert P.api_nationalities(p["nationalityDetails"]["nationalities"]) == [{"id": 125, "name": "Norway", "code": "NOR"}]
    ref = P.api_player_ref(p)
    assert ref["id"] == 418560 and ref["link"] == "https://www.transfermarkt.com/erling-haaland/profil/spieler/418560"


def test_api_game_normalizers():
    from transfermarkt.matches import _club_stats, _goal
    g = fixture("tmapi_game.json")["data"]
    stats = _club_stats(g["homeClub"]["clubStatistics"])
    assert stats["possession_percent"] == 55.4 and stats["shots"] == 14 and stats["passes_completed"] == 408
    goal = _goal(g["homeClub"]["actions"]["goals"][0], "home")
    assert goal["player"]["name"] == "Joshua Zirkzee" and goal["assist_by"]["name"] == "Alejandro Garnacho"
    assert goal["minute"] == 87 and goal["type"] == "goal"


def test_performance_aggregation():
    from transfermarkt.players import _aggregate, _filter_games, _game_row
    perf = fixture("tmapi_performance_game.json")["data"]
    rows = _filter_games(perf, season=2026)
    agg = _aggregate(rows)
    assert agg["matches"] == len(rows) and agg["appearances"] <= agg["matches"]
    played = [e for e in rows if e["statistics"]["generalStatistics"]["participationState"] == "played"]
    assert agg["appearances"] == len(played)
    row = _game_row(played[0], {}, {})
    assert row["participation"] == "played" and row["minutes_played"] is not None
    assert row["match"]["id"] and row["match"]["date"]
