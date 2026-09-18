"""Site-wide statistic lists (the "Statistics" menu): latest transfers,
transfer records, most valuable players / clubs, latest market value
changes, expiring contracts, free agents, the FIFA world ranking and the
latest rumours. All server-rendered HTML with GET filter forms and
?page=N pagination (25 rows a page, 50 on the free-agents list).
"""
from . import parsers as P
from . import refs
from .competitions import parse_rumour_rows
from .fetch import get_page

PAGE_SIZE = 25


def _club_with_league(td):
    club = P.club_ref(td) if td is not None else None
    if club and td is not None:
        comp = td.select_one("a[href*='wettbewerb/']")
        club["competition"] = P.competition_ref(comp) if comp is not None else None
        flag = td.select_one("img[src*='/flagge/']")
        club["country"] = P.flag_country(flag)
    return club


def _list_result(doc, page, rows, key, per_page=PAGE_SIZE, **extra):
    out = {**extra, "pagination": P.pagination(page, per_page, total_pages_=P.total_pages(doc)), key: rows}
    return out


# ---- transfers -------------------------------------------------------------------------

def get_latest_transfers(page=1, country=None, competition=None, club_country=None,
                         min_market_value=None, max_market_value=None, min_fee=None, max_fee=None):
    params = {"plus": 1, "land_id": country or "", "wettbewerb_id": competition or "alle",
              "verein_land_id": club_country or "",
              "minMarktwert": min_market_value if min_market_value is not None else 0,
              "maxMarktwert": max_market_value if max_market_value is not None else 500000000,
              "minAbloese": min_fee if min_fee is not None else 0,
              "maxAbloese": max_fee if max_fee is not None else 500000000}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/statistik/neuestetransfers", params))
    table = P.find_table(doc, "Latest transfers") or doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        rows.append({
            "player": player, "position": player.pop("position", None),
            "age": P.to_int(P.text(row.get("Age"))),
            "nationalities": P.flags(row.get("Nat.")),
            "from_club": _club_with_league(row.get("Left")),
            "to_club": _club_with_league(row.get("Joined")),
            "date": P.iso_date(P.text(row.get("Transfer date"))),
            "market_value": P.money_obj(P.text(row.get("Market value"))),
            "fee": P.fee(P.text(row.get("Fee"))),
        })
    return _list_result(doc, page, rows, "transfers")


def get_transfer_records(page=1, season=None, country=None, position_group=None, position_id=None,
                         age_group=None, window=None, only_loans=None):
    params = {"saison_id": season if season else "alle", "land_id": country or "",
              "ausrichtung": refs.POSITION_GROUPS.get(position_group, ""),
              "spielerposition_id": position_id or "", "altersklasse": age_group or "",
              "leihe": "1" if only_loans else "", "w_s": refs.TRANSFER_WINDOWS.get(window, ""), "plus": 1}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/transfers/transferrekorde/statistik", params))
    table = P.find_table(doc, "Transfer records") or doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        rows.append({
            "rank": P.to_int(P.text(row.get("#"))),
            "player": player, "position": player.pop("position", None),
            "age": P.to_int(P.text(row.get("Age"))),
            "nationalities": P.flags(row.get("Nat.")),
            "season": P.season_obj(P.text(row.get("Season"))),
            "from_club": _club_with_league(row.get("Left")),
            "to_club": _club_with_league(row.get("Joined")),
            "market_value": P.money_obj(P.text(row.get("Market value"))),
            "fee": P.fee(P.text(row.get("Fee"))),
        })
    return _list_result(doc, page, rows, "transfers",
                        season={"id": season, "name": refs.season_name(season)} if season else None)


# ---- market values --------------------------------------------------------------------

def get_most_valuable_players(page=1, position_group=None, position_id=None, age_group=None,
                              birth_year=None, continent=None, country=None, year=None, only_loans=None):
    params = {"land_id": country or 0, "ausrichtung": refs.POSITION_GROUPS.get(position_group, "alle"),
              "spielerposition_id": position_id or "alle", "altersklasse": age_group or "alle",
              "jahrgang": birth_year or 0, "kontinent_id": continent or 0, "jahr": year or "",
              "only_loans": "1" if only_loans else "", "plus": 1}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/spieler-statistik/wertvollstespieler/marktwertetop", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        rows.append({
            "rank": P.to_int(P.text(row.get("#"))),
            "player": player, "position": player.pop("position", None),
            "age": P.to_int(P.text(row.get("Age"))),
            "nationalities": P.flags(row.get("Nat.")),
            "club": P.club_ref(row.get("Club")),
            "market_value": P.money_obj(P.text(row.get("Market value"))),
        })
    return _list_result(doc, page, rows, "players")


def get_most_valuable_clubs(page=1, country=None, continent=None):
    params = {"land_id": country or 0, "kontinent_id": continent or 0, "plus": 1}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/spieler-statistik/wertvollstemannschaften/marktwertetop", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        club = P.club_ref(row.get("Club"))
        if not club:
            continue
        crest_cell = row.get("col1")
        club["crest"] = club.get("crest") or (P.crest(crest_cell.select_one("img")) if crest_cell is not None else None)
        comp_cell = row.get("Competition")
        rows.append({
            "rank": P.to_int(P.text(row.get("#"))),
            "club": club,
            "competition": P.competition_ref(comp_cell) if comp_cell is not None else None,
            "country": P.flag_country(comp_cell.select_one("img[src*='/flagge/']")) if comp_cell is not None else None,
            "squad_size": P.to_int(P.text(row.get("Squad"))),
            "average_age": P.to_float(P.text(row.get("ø age") or row.get("Avg. age"))),
            "market_value": P.money_obj(P.text(row.get("Market Value") or row.get("Market value") or row.get("Total value"))),
        })
    return _list_result(doc, page, rows, "clubs")


def get_market_value_changes(page=1, position_group=None, position_id=None, age_group=None,
                             country=None, competition=None):
    params = {"position": refs.POSITION_GROUPS.get(position_group, "alle"),
              "spielerposition_id": position_id or 0, "altersklasse": age_group or "alle",
              "land_id": country or 0, "wettbewerb_id": competition or "", "plus": 1, "galerie": 0}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/spieler-statistik/marktwertaenderungen/marktwertetop", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        new = row.get("New Market Value") or row.get("New market value")
        rows.append({
            "player": player, "position": player.pop("position", None),
            "nationalities": P.flags(row.get("Nat.")),
            "age": P.to_int(P.text(row.get("Age"))),
            "club": P.club_ref(row.get("Club")),
            "previous_market_value": P.money_obj(P.text(row.get("Old Market Value") or row.get("Previous market value"))),
            "market_value": P.money_obj(P.text(new)),
            "change_percent": P.to_float(P.text(row.get("%") or row.get("Change"))),
            "changed_on": P.iso_date(P.text(row.get("Changed on"))),
        })
    return _list_result(doc, page, rows, "players")


# ---- contracts -------------------------------------------------------------------------

def get_contracts_expiring(page=1, year=None, position_group=None, position_id=None, age_group=None,
                           country=None, competition=None):
    params = {"jahr": year or "", "ausrichtung": refs.POSITION_GROUPS.get(position_group, "alle"),
              "spielerposition_id": position_id or "alle", "altersklasse": age_group or "alle",
              "land_id": country or "", "wettbewerb_id": competition or "", "plus": 1}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/statistik/endendevertraege", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        rumours = row.get("Rumours")
        dob_cell = next((row[k] for k in row if k.startswith("Date of birth")), None)
        dob, age = P.date_and_age(P.text(dob_cell)) if dob_cell is not None else (None, P.to_int(P.text(row.get("Age"))))
        rows.append({
            "player": player, "position": player.pop("position", None),
            "date_of_birth": dob,
            "age": age,
            "nationalities": P.flags(row.get("Nat.")),
            "club": _club_with_league(row.get("Current club")),
            "contract_until": P.iso_date(P.text(row.get("Contract expires") or row.get("Contract until"))),
            "market_value": P.money_obj(P.text(row.get("Market value"))),
            "rumours_count": P.to_int(P.text(rumours)) if rumours is not None else None,
        })
    title = P.text(doc.select_one("h1")) or ""
    shown_year = P.to_int(title.split()[-1]) if title.split() and title.split()[-1].isdigit() else year
    return _list_result(doc, page, rows, "players", year=shown_year)


def get_free_agents(page=1, position_group=None, position_id=None, country=None):
    params = {"ausrichtung": refs.POSITION_GROUPS.get(position_group, "alle"),
              "spielerposition_id": position_id or "alle", "land_id": country or "alle", "plus": 1}
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page("/statistik/vertragslosespieler", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        since_key = next((k for k in row if k.lower().startswith("out-of-contract") or k.lower().startswith("without club")), None)
        rows.append({
            "player": player, "position": player.pop("position", None),
            "nationalities": P.flags(row.get("Nat.")),
            "age": P.to_int(P.text(row.get("Age"))),
            "free_agent_since": P.iso_date(P.text(row.get(since_key))) if since_key else None,
            "last_club": P.club_ref(row.get("Last club")) if row.get("Last club") is not None else None,
            "market_value": P.money_obj(P.text(row.get("Market value"))),
        })
    return _list_result(doc, page, rows, "players", per_page=50)


# ---- FIFA ranking + rumours -------------------------------------------------------------

def get_fifa_ranking(page=1, date=None):
    params = {"datum": date} if date else None
    if page and page > 1:
        params = dict(params or {}, page=page)
    doc = P.soup(get_page("/statistik/weltrangliste", params))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        nation = row.get("Nation")
        team = P.club_ref(nation) if nation is not None else None
        if not team:
            continue
        rank_cell = row.get("#")
        prev = rank_cell.select_one("span[title]") if rank_cell is not None else None
        import re
        prev_m = re.search(r"(\d+)", prev.get("title") or "") if prev is not None else None
        rows.append({
            "rank": P.to_int(P.text(rank_cell)),
            "previous_rank": int(prev_m.group(1)) if prev_m else None,
            "team": team,
            "country": P.flag_country(nation.select_one("img[src*='/flagge/']")),
            "squad_size": P.to_int(P.text(row.get("Squad size"))),
            "average_age": P.to_float(P.text(row.get("Avg. age"))),
            "total_market_value": P.money_obj(P.text(row.get("Total value"))),
            "confederation": P.text(row.get("Confederation")),
            "points": P.to_int(P.text(row.get("Points"))),
        })
    selected = doc.select_one("select[name='datum'] option[selected]")
    return _list_result(doc, page, rows, "ranking",
                        date=(selected.get("value") if selected is not None else date))


def get_latest_rumours(page=1):
    params = {"page": page} if page and page > 1 else None
    doc = P.soup(get_page("/geruechte/aktuellegeruechte/statistik", params))
    rows = parse_rumour_rows(doc.select_one("table.items"))
    return _list_result(doc, page, rows, "rumours", per_page=15)
