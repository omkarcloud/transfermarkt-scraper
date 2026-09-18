"""Club endpoints. `club` = numeric id or a transfermarkt.com club link.
National teams are clubs too (Norway = 3440).

  profile          tmapi /club/{id} + the HTML club page (league facts, stadium,
                   transfer record, top arrivals/departures/scorers, facts box)
  squad            HTML detailed squad (kader/plus/1: number, age, nationality,
                   height, foot, joined, signed from, market value) merged with
                   tmapi /club/{id}/squad (captain) and /players?ids[] (position,
                   contract until)
  transfers        HTML transfers tab, detailed view (arrivals / departures per
                   season with market values + the season's transfer record;
                   the detailed view drops the site's "season record" box)
  fixtures         HTML schedule tab (every competition of a season)
  squad-stats      HTML squad statistics tab (per player, per competition/season)
  staff / stadium / achievements / history / transfer-records / national-players
                   HTML tabs
"""
import re

from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_page, get_api, get_api_optional, run_parallel


def _cid(club):
    return refs.resolve_club(club)


def _club_path(cid, tab, suffix=""):
    return f"/-/{tab}/verein/{cid}{suffix}"


def _current_season(doc):
    """Season the page is showing (from the season filter's selected option)."""
    sel = doc.select_one("select[name='saison_id'] option[selected]")
    if sel is not None:
        return P.to_int(sel.get("value"))
    h = doc.select_one(".box h2, .box .content-box-headline")
    season = P.season_obj(P.text(h)) if h is not None else None
    return season["id"] if season else None


# ---- profile ---------------------------------------------------------------------------

def _mini_player_table(doc, headline):
    """The 'Top arrivals' / 'Top departures' / 'Top goalscorers' boxes."""
    table = P.find_table(doc, headline, css="table")
    rows = []
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        player = P.player_ref(tr)
        if not player:
            continue
        row = {"player": player}
        other = tr.select_one("a[href*='/verein/']")
        if other is not None:
            row["club"] = P.club_ref(other)
        last = P.text(tds[-1])
        if last and "€" in last:
            row["fee"] = P.fee(last)
        else:
            row["count"] = P.to_int(last)
        rows.append(row)
    return rows


def _facts_box(doc):
    return P.info_spans(P.box_by_headline(doc, "Stats & facts"))


def _header_value(hero, label):
    el = hero.get(label)
    return P.text(el) if el is not None else None


def get_profile(club):
    cid = _cid(club)
    api = get_api(f"/club/{cid}")            # cheap and 404s fast for unknown ids
    html = get_page(_club_path(cid, "startseite"), expect=f"/verein/{cid}")
    doc = P.soup(html)
    hero = P.data_header(doc)
    base = api.get("baseDetails") or {}
    squad = api.get("squadDetails") or {}
    superior = base.get("superiorClub") or {}
    location = superior.get("location") or {}
    colors = superior.get("colors") or {}
    facts = _facts_box(doc)
    website_a = None
    for el in facts.get("Website") or []:
        website_a = el.select_one("a[href]") or website_a
    comp = lookup.competition(base.get("primaryCompetitionId")) if base.get("primaryCompetitionId") else None

    stadium_el = hero.get("Stadium")
    stadium = None
    if stadium_el is not None:
        a = stadium_el.select_one("a")
        m = re.search(r"([\d.,]+)\s*Seats", P.text(stadium_el) or "")
        stadium = {"name": P.text(a) if a is not None else (P.text(stadium_el) or "").split(" ")[0],
                   "capacity": P.to_int(m.group(1)) if m else None,
                   "link": P.link(a.get("href")) if a is not None else None}
    foreigners = _header_value(hero, "Foreigners")
    transfer_record = P.find_table(doc, "Transfer record", css="table")
    record = {}
    for tr in P.table_rows(transfer_record) if transfer_record is not None else []:
        tds = P.cells(tr)
        key = (P.text(tds[0]) or "").lower()
        if key in ("income", "expenditure") and len(tds) >= 3:
            record[key] = {"players": P.to_int(P.text(tds[1])), "total": P.money_obj(P.text(tds[2]))}
    def fact(label):
        return P.joined_text(facts.get(label))

    return {
        "id": int(api["id"]),
        "name": api.get("name"),
        "short_name": base.get("shortName") or None,
        "abbreviation": base.get("abbreviation") or None,
        "official_name": superior.get("name") or fact("Official club name"),
        "link": P.link(api.get("relativeUrl")) or P.entity_link("club", cid),
        "crest": api.get("crestUrl") or None,
        "country": refs.country(base.get("countryId")),
        "is_national_team": bool(base.get("isNationalTeam")),
        "competition": comp,
        "league_tier": _header_value(hero, "League level") or (comp or {}).get("tier"),
        "table_position": P.to_int(_header_value(hero, "Table position")),
        "in_league_since": _header_value(hero, "In league since"),
        "founded": P.iso_date(fact("Founded")) or fact("Founded"),
        "members": P.to_int(fact("Members")),
        "colors": [c for c in (colors.get("firstColor"), colors.get("secondColor"), colors.get("thirdColor")) if c] or None,
        "address": {"street": location.get("street") or None, "postcode": location.get("postcode") or None,
                    "city": location.get("city") or None, "country": refs.country(location.get("countryId")),
                    "latitude": location.get("latitude"), "longitude": location.get("longitude")} if location else None,
        "address_text": fact("Address"),
        "phone": fact("Tel"),
        "fax": fact("Fax"),
        "website": P.link(website_a.get("href")) if website_a is not None else None,
        "stadium": stadium,
        "squad": {
            "size": squad.get("squadSize"),
            "average_age": squad.get("averageAge"),
            "foreigners_count": P.to_int(foreigners),
            "foreigners_percent": P.percent(foreigners),
            "national_team_players": P.to_int(_header_value(hero, "National team players")),
            "total_market_value": P.api_money(squad.get("currentMarketValue")),
            "average_market_value": P.api_money(squad.get("averageMarketValue")),
            "acquisition_value": P.api_money(squad.get("acquisitionValue")),
            "top_18_market_value": P.api_money(squad.get("top18PlayersMarketValue")),
            "top_18_share_percent": (squad.get("top18SharePercentage") or {}).get("value"),
        },
        "transfer_record": {
            "current_balance": P.money_obj(_header_value(hero, "Current transfer record")),
            "income": record.get("income"),
            "expenditure": record.get("expenditure"),
        },
        "top_arrivals": _mini_player_table(doc, "Top arrivals"),
        "top_departures": _mini_player_table(doc, "Top departures"),
        "top_goalscorers": _mini_player_table(doc, "Top goalscorers"),
        "most_assists": _mini_player_table(doc, "Most assists"),
        "historical_names": [{"name": n.get("name"), "short_name": n.get("shortName"), "season": P.api_season({"id": n.get("seasonId")})}
                             for n in (api.get("historical") or {}).get("names") or []],
        "historical_crests": [{"image": i.get("url"), "season": P.api_season({"id": i.get("seasonId")})}
                              for i in (api.get("historical") or {}).get("images") or []],
    }


# ---- squad ------------------------------------------------------------------------------

def _squad_row(row):
    player = P.player_ref(row.get("Player") or row.get("col1"))
    dob, age = P.date_and_age(P.text(row.get("Date of birth/Age")))
    if age is None:
        age = P.to_int(P.text(row.get("Age")))
    signed = row.get("Signed from")
    signed_from = P.club_ref(signed) if signed is not None else None
    if signed_from and signed is not None:
        a = signed.select_one("a[title]")
        title = P.clean(a.get("title")) if a is not None else None
        if title and ":" in title:
            fee = P.money_obj(title)
            if fee:
                signed_from["fee"] = fee
    current = row.get("Current club") or row.get("Club")
    number_cell = row.get("#") or row.get("col0")
    return {
        "player": player,
        "shirt_number": P.to_int(P.text(number_cell)),
        "position": (player or {}).get("position"),
        "date_of_birth": dob,
        "age": age,
        "nationalities": P.flags(row.get("Nat.")),
        "height_m": P.height_m(P.text(row.get("Height"))),
        "preferred_foot": P.text(row.get("Foot")),
        "joined": P.iso_date(P.text(row.get("Joined"))),
        "signed_from": signed_from,
        "current_club": P.club_ref(current) if current is not None else None,
        "market_value": P.money_obj(P.text(row.get("Market value"))),
        "is_captain": bool(row.get("_captain")),
    }


def get_squad(club, season=None):
    cid = _cid(club)
    suffix = f"/saison_id/{season}" if season else ""
    html, api_squad = run_parallel([
        lambda: get_page(_club_path(cid, "kader", suffix + "/plus/1"), expect=f"/verein/{cid}"),
        lambda: get_api_optional(f"/club/{cid}/squad", {"season": season} if season else None),
    ])
    doc = P.soup(html)
    table = P.find_table(doc, "Squad") or doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        if not row.get("Player") and not row.get("col1"):
            continue
        parsed = _squad_row(row)
        if parsed["player"]:
            rows.append(parsed)
    api_rows = {str(p.get("playerId")): p for p in ((api_squad or {}).get("squad") or [])}
    ids = [r["player"]["id"] for r in rows if r["player"].get("id")]
    hydrated = lookup.players(ids) if ids else {}
    for r in rows:
        pid = str(r["player"].get("id"))
        extra = hydrated.get(pid) or {}
        r["position"] = extra.get("position") or (
            {"name": r["position"]} if r["position"] else None)
        r["contract_until"] = extra.get("contract_until")
        r["is_captain"] = bool((api_rows.get(pid) or {}).get("isCaptain"))
        r["player"].pop("position", None)
        if r["height_m"] is None:
            r["height_m"] = extra.get("height_m")
        if r["preferred_foot"] is None:
            r["preferred_foot"] = extra.get("preferred_foot")
    for r in rows:
        r.update(P.ordered(r, ("player", "shirt_number", "position", "date_of_birth", "age", "nationalities",
                               "height_m", "preferred_foot", "joined", "contract_until", "signed_from",
                               "current_club", "market_value", "is_captain")))
    total = sum((r["market_value"] or {}).get("amount") or 0 for r in rows)
    shown = _current_season(doc) or season
    return {"club_id": cid,
            "season": {"id": shown, "name": refs.season_name(shown)} if shown else None,
            "count": len(rows),
            "total_market_value": {"amount": total, "currency": "EUR"} if rows else None,
            "players": rows}


# ---- transfers ------------------------------------------------------------------------

def _transfer_row(row, direction):
    player = P.player_ref(row.get("Player") or row.get("col1"))
    other_key = "Left" if direction == "arrivals" else "Joined"
    other = row.get(other_key) or row.get("col4")
    other_club = P.club_ref(other) if other is not None else None
    if other_club and other is not None:
        comp = other.select_one("a[href*='wettbewerb/']")
        other_club["competition"] = P.competition_ref(comp) if comp is not None else None
        flag = other.select_one("img[src*='/flagge/']")
        other_club["country"] = P.flag_country(flag)
    fee_cell = row.get("Fee") or row.get(f"col{len(row) - 1}")
    fee_link = fee_cell.select_one("a[href]") if fee_cell is not None else None
    transfer_id = None
    if fee_link is not None:
        m = re.search(r"/transfer_id/(\d+)", fee_link.get("href") or "")
        transfer_id = int(m.group(1)) if m else None
    out = {
        "transfer_id": transfer_id,
        "player": player,
        "position": (player or {}).pop("position", None) if player else None,
        "age": P.to_int(P.text(row.get("Age"))),
        "nationalities": P.flags(row.get("Nat.")),
        "market_value": P.money_obj(P.text(row.get("Market value"))),
        "date": P.iso_date(P.text(row.get("Transfer date") or row.get("Date"))),
        ("from_club" if direction == "arrivals" else "to_club"): other_club,
        "fee": P.fee(P.text(fee_cell)),
    }
    return out


def _season_record(doc, headline="Season record"):
    table = P.find_table(doc, headline, css="table")
    rows = []
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        comp = P.competition_ref(tr)
        result_td = tds[-1]
        img = result_td.select_one("img")
        rows.append({"competition": comp,
                     "result": P.text(result_td) or (P.clean(img.get("title")) if img is not None else None)})
    return rows


def _transfer_record(doc, headline="Transfer record"):
    table = P.find_table(doc, headline, css="table")
    out = {}
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        key = (P.text(tds[0]) or "").lower()
        if key in ("income", "expenditure") and len(tds) >= 3:
            out[key] = {"players": P.to_int(P.text(tds[1])), "total": P.money_obj(P.text(tds[2]))}
    inc = (out.get("income") or {}).get("total") or {}
    exp = (out.get("expenditure") or {}).get("total") or {}
    if inc.get("amount") is not None and exp.get("amount") is not None:
        out["balance"] = {"amount": inc["amount"] - exp["amount"], "currency": "EUR"}
    return out or None


def get_transfers(club, season=None, window=None, position_group=None, position_id=None,
                  skip_loans=None, skip_youth=None):
    cid = _cid(club)
    suffix = f"/saison_id/{season}" if season else ""
    suffix += f"/pos/{refs.POSITION_GROUPS.get(position_group, '')}/detailpos/{position_id or 0}"
    suffix += f"/w_s/{refs.TRANSFER_WINDOWS.get(window, '')}"
    suffix += f"/skip_loans/{'1' if skip_loans else ''}/skip_youth/{'1' if skip_youth else ''}/plus/1"
    doc = P.soup(get_page(_club_path(cid, "transfers", suffix), expect=f"/verein/{cid}"))
    arrivals = [_transfer_row(r, "arrivals") for r in P.items_table(P.find_table(doc, "Arrivals") or P.soup("<table></table>").table)]
    departures = [_transfer_row(r, "departures") for r in P.items_table(P.find_table(doc, "Departures") or P.soup("<table></table>").table)]
    arrivals = [a for a in arrivals if a["player"]]
    departures = [d for d in departures if d["player"]]
    shown = _current_season(doc) or season
    return {"club_id": cid,
            "season": {"id": shown, "name": refs.season_name(shown)} if shown else None,
            "transfer_record": _transfer_record(doc),
            "arrivals_count": len(arrivals), "departures_count": len(departures),
            "arrivals": arrivals, "departures": departures}


# ---- fixtures --------------------------------------------------------------------------

def _fixture_row(tr, competition):
    tds = P.cells(tr)
    if len(tds) < 9:
        return None
    opp_td = tds[5]
    opp_name_td = tds[6]
    opponent = P.club_ref(opp_name_td) or P.club_ref(opp_td)
    if opponent:
        opponent["crest"] = opponent.get("crest") or P.crest(opp_td.select_one("img"))
        m = re.search(r"\((\d+)\.\)", P.text(opp_name_td) or "")
        opponent["table_position"] = int(m.group(1)) if m else None
        opponent["name"] = re.sub(r"\s*\(\d+\.\)", "", opponent["name"] or "").strip() or opponent["name"]
    result_td = tds[-1]
    result = P.text(result_td)
    own_rank = re.search(r"\((\d+)\.\)", P.text(tds[4]) or "")
    round_td = tds[0]
    home_goals = away_goals = None
    if result and re.match(r"^\d+:\d+", result):
        home_goals, away_goals = (int(x) for x in re.match(r"^(\d+):(\d+)", result).groups())
    venue = (P.text(tds[3]) or "").upper()
    venue = {"H": "home", "A": "away", "N": "neutral"}.get(venue, venue.lower() or None)
    return {
        "match_id": P.match_id_in(result_td),
        "link": P.link(result_td.select_one("a").get("href")) if result_td.select_one("a") else None,
        "competition": competition,
        "matchday": P.to_int(P.text(round_td)) if (P.text(round_td) or "").isdigit() else None,
        "round": None if (P.text(round_td) or "").isdigit() else P.text(round_td),
        "date": P.iso_date(P.text(tds[1])),
        "time": P.text(tds[2]),
        "venue": venue,
        "own_table_position": int(own_rank.group(1)) if own_rank else None,
        "opponent": opponent,
        "formation": P.text(tds[7]),
        "attendance": P.to_int(P.text(tds[8])) if len(tds) > 9 else None,
        "result": result,
        "own_goals_scored": (home_goals if venue == "home" else away_goals) if home_goals is not None else None,
        "opponent_goals": (away_goals if venue == "home" else home_goals) if home_goals is not None else None,
        "outcome": _outcome(result_td),
    }


def _outcome(td):
    classes = " ".join(td.get("class") or []) + " " + " ".join(
        " ".join(s.get("class") or []) for s in td.select("span"))
    if "greentext" in classes:
        return "win"
    if "redtext" in classes:
        return "loss"
    if "text" in classes or td.select_one("span"):
        return "draw" if P.text(td) and re.match(r"^\d+:\d+", P.text(td)) else None
    return None


def get_fixtures(club, season=None):
    cid = _cid(club)
    suffix = f"/saison_id/{season}" if season else ""
    doc = P.soup(get_page(_club_path(cid, "spielplan", suffix), expect=f"/verein/{cid}"))
    competitions, matches = [], []
    for box in doc.select(".box"):
        h = box.select_one("h2, .content-box-headline")
        table = box.select_one("table")
        if h is None or table is None:
            continue
        heads = P.header_labels(table)
        if "Opponent" not in heads:
            continue
        comp = P.competition_ref(h) or {"id": None, "name": P.text(h), "link": None, "logo": None}
        competitions.append(comp)
        for tr in P.table_rows(table):
            row = _fixture_row(tr, comp)
            if row:
                matches.append(row)
    record = {}
    table = P.find_table(doc, "Record", css="table")
    section = None
    for tr in (table.find_all("tr") if table is not None else []):
        if tr.find_parent("table") is not table:
            continue
        cells_ = tr.find_all(["th", "td"], recursive=False)
        if not cells_:
            continue
        if len(cells_) == 1:
            # section label row ("Premier League", "Overall balance"); the
            # site renders these as a lone <th> or a colspan <td>
            section = (P.text(cells_[0]) or "").lower().replace(" balance", "").strip()
            continue
        if cells_[0].name == "th":
            continue   # the column header row
        label = (P.text(cells_[0]) or "").lower()
        if label in ("matches", ""):
            key = section
        else:
            key = label.replace(" record", "")
        key = re.sub(r"[^a-z0-9]+", "_", key or "").strip("_")
        if key and len(cells_) >= 7:
            goals = (P.text(cells_[6]) or "").split(":")
            record[key] = {"matches": P.to_int(P.text(cells_[1])), "wins": P.to_int(P.text(cells_[2])),
                           "draws": P.to_int(P.text(cells_[3])), "losses": P.to_int(P.text(cells_[4])),
                           "points_per_match": P.to_float(P.text(cells_[5])),
                           "goals_for": P.to_int(goals[0]) if len(goals) == 2 else None,
                           "goals_against": P.to_int(goals[1]) if len(goals) == 2 else None,
                           "average_attendance": P.to_int(P.text(cells_[7])) if len(cells_) > 7 else None}
    shown = _current_season(doc) or season
    return {"club_id": cid, "season": {"id": shown, "name": refs.season_name(shown)} if shown else None,
            "record": record or None, "competitions": competitions, "count": len(matches), "matches": matches}


# ---- squad statistics ----------------------------------------------------------------------

_STAT_COLUMNS = ["in_squad", "appearances", "goals", "assists", "yellow_cards", "second_yellow_cards",
                 "red_cards", "substituted_on", "substituted_off", "points_per_match", "minutes_played"]


def get_squad_stats(club, season=None, competition=None):
    cid = _cid(club)
    reldata = f"{competition or ''}%26{season}" if season else (f"{competition}%26" if competition else None)
    suffix = f"/reldata/{reldata}/plus/1" if reldata else "/plus/1"
    doc = P.soup(get_page(_club_path(cid, "leistungsdaten", suffix), expect=f"/verein/{cid}"))
    table = doc.select_one("table.items")
    rows = []
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        player = P.player_ref(tr)
        if not player or len(tds) < 5:
            continue
        stats = {}
        values = tds[4:]
        for key, td in zip(_STAT_COLUMNS, values):
            t = P.text(td)
            if key == "points_per_match":
                stats[key] = P.to_float(t)
            elif key == "minutes_played":
                stats[key] = P.minutes(t)
            else:
                stats[key] = P.to_int(t) if t else 0
        row = {"player": player, "shirt_number": P.to_int(P.text(tds[0])),
               "position": player.pop("position", None), "age": P.to_int(P.text(tds[2])),
               "nationalities": P.flags(tds[3])}
        row.update(stats)
        rows.append(row)
    selected = doc.select_one("select[name='reldata'] option[selected]")
    scope = P.text(selected) if selected is not None else None
    return {"club_id": cid, "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "competition_id": competition, "scope": scope, "count": len(rows), "players": rows}


# ---- staff / stadium / achievements / history / records / national players -----------------------

def get_staff(club):
    cid = _cid(club)
    doc = P.soup(get_page(_club_path(cid, "mitarbeiter"), expect=f"/verein/{cid}"))
    sections = []
    for box in doc.select(".box"):
        h = box.select_one("h2, .content-box-headline")
        table = box.select_one("table")
        if h is None or table is None or "Name/Position" not in P.header_labels(table):
            continue
        people = []
        for row in P.items_table(table):
            cell = row.get("Name/Position") or row.get("col0")
            person = P.manager_ref(cell)
            if not person:
                continue
            inline = cell.select_one("table.inline-table")
            role = P.text(inline.select("tr")[1]) if inline is not None and len(inline.select("tr")) > 1 else None
            last = row.get("Last club")
            people.append({"person": person, "role": role,
                           "age": P.to_int(P.text(row.get("Age"))),
                           "nationalities": P.flags(row.get("Nat.")),
                           "appointed": P.iso_date(P.text(row.get("Appointed"))),
                           "contract_until": P.iso_date(P.text(row.get("Contract expires"))),
                           "last_club": P.club_ref(last) if last is not None and last.select_one("a") else None})
        sections.append({"department": P.text(h), "count": len(people), "people": people})
    return {"club_id": cid, "departments": sections}


def get_stadium(club):
    cid = _cid(club)
    doc = P.soup(get_page(_club_path(cid, "stadion"), expect=f"/verein/{cid}"))
    main = contact = pricing = owner = None
    for box in doc.select(".box"):
        h = P.text(box.select_one("h2, .content-box-headline")) or ""
        if h == "Contact":
            contact = box
        elif h.startswith("Pricing"):
            pricing = box
        elif h.startswith("Owner"):
            owner = box
        elif main is None and "Name of stadium" in box.get_text():
            main = box
    facts, cont, price, own = (P.profile_rows(b) for b in (main, contact, pricing, owner))

    def val(d, key):
        return P.joined_text(d.get(key))

    stadiums = []
    for opt in doc.select("select[name='stadion_id'] option, .inline-select select option"):
        if (opt.get("value") or "").isdigit():
            stadiums.append({"id": int(opt["value"]), "name": P.text(opt)})
    images = [P.image(i.get("src") or i.get("data-src")) for i in doc.select("#main img[src*='/foto/'], #main img[data-src*='/foto/']")]
    site_a = None
    for el in cont.get("Website") or []:
        site_a = el.select_one("a[href]") or site_a
    capacity = val(facts, "Total capacity") or ""
    intl = re.search(r"([\d.,]+)\s*at international", capacity)
    return {
        "club_id": cid,
        "name": val(facts, "Name of stadium"),
        "images": [i for i in images if i],
        "total_capacity": P.to_int(capacity.split(" ")[0]) if capacity else None,
        "international_capacity": P.to_int(intl.group(1)) if intl else None,
        "seats": P.to_int((val(facts, "Seats") or "").split(" ")[0]),
        "standing": P.to_int((val(facts, "Standing") or "").split(" ")[0]),
        "built": P.to_int(val(facts, "Built")),
        "renovated": P.to_int(val(facts, "Renovated") or val(facts, "Last renovation")),
        "construction_cost": P.money_obj(val(facts, "Construction costs")),
        "former_name": val(facts, "Formerly"),
        "has_undersoil_heating": (val(facts, "Undersoil heating") or "").lower() == "yes" if facts.get("Undersoil heating") else None,
        "has_running_track": (val(facts, "Running track") or "").lower() == "yes" if facts.get("Running track") else None,
        "surface": val(facts, "Surface"),
        "pitch_size": val(facts, "Pitch size"),
        "address": val(cont, "Address"),
        "website": P.link(site_a.get("href")) if site_a is not None else None,
        "pricing": {k: P.joined_text(v) for k, v in price.items()} or None,
        "owner": val(own, "Owner"),
        "naming_rights": val(own, "Name rights"),
        "other_stadiums": stadiums,
    }


def parse_club_titles(doc):
    """Club achievements: rows of season | icon | title -> grouped by title."""
    box = P.box_by_headline(doc, "All titles")
    table = box.select_one("table") if box is not None else None
    groups = {}
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        if len(tds) < 2:
            continue
        title = P.text(tds[-1])
        season = P.season_obj(P.text(tds[0]))
        if not title:
            continue
        g = groups.setdefault(title, {"title": title, "count": 0, "seasons": []})
        g["count"] += 1
        if season:
            g["seasons"].append(season)
    return sorted(groups.values(), key=lambda g: -g["count"])


def get_achievements(club):
    cid = _cid(club)
    doc = P.soup(get_page(_club_path(cid, "erfolge"), expect=f"/verein/{cid}"))
    titles = parse_club_titles(doc)
    return {"club_id": cid, "count": len(titles), "total_titles": sum(t["count"] for t in titles), "titles": titles}


def get_history(club):
    cid = _cid(club)
    doc = P.soup(get_page(_club_path(cid, "platzierungen"), expect=f"/verein/{cid}"))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        goals = (P.text(row.get("Goals")) or "").split(":")
        comp_cell = row.get("League_2") or row.get("League")
        rows.append({
            "season": P.season_obj(P.text(row.get("Season"))),
            "competition": P.competition_ref(comp_cell) if comp_cell is not None else None,
            "tier": P.text(row.get("League Level")),
            "wins": P.to_int(P.text(row.get("W"))), "draws": P.to_int(P.text(row.get("D"))),
            "losses": P.to_int(P.text(row.get("L"))),
            "goals_for": P.to_int(goals[0]) if len(goals) == 2 else None,
            "goals_against": P.to_int(goals[1]) if len(goals) == 2 else None,
            "goal_difference": P.to_int(P.text(row.get("+/-"))),
            "points": P.to_int(P.text(row.get("Points"))),
            "position": P.to_int(P.text(row.get("Rank"))),
            "manager": P.manager_ref(row.get("Manager")),
        })
    return {"club_id": cid, "count": len(rows), "seasons": rows}


def get_transfer_records(club, type="arrivals", season=None):
    cid = _cid(club)
    tab = "transferrekorde" if type == "arrivals" else "rekordabgaenge"
    suffix = f"/saison_id/{season}" if season else ""     # no season = all-time records
    doc = P.soup(get_page(_club_path(cid, tab, suffix), expect=f"/verein/{cid}"))
    table = doc.select_one("table.items")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        other = row.get("Left") or row.get("Joined")
        other_club = P.club_ref(other) if other is not None else None
        if other_club and other is not None:
            comp = other.select_one("a[href*='wettbewerb/']")
            other_club["competition"] = P.competition_ref(comp) if comp is not None else None
        rows.append({
            "rank": P.to_int(P.text(row.get("#"))),
            "player": player,
            "position": player.pop("position", None),
            "age": P.to_int(P.text(row.get("Age"))),
            "nationalities": P.flags(row.get("Nat.")),
            "season": P.season_obj(P.text(row.get("Season"))),
            ("from_club" if type == "arrivals" else "to_club"): other_club,
            "fee": P.fee(P.text(row.get("Fee"))),
        })
    return {"club_id": cid, "type": type,
            "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "count": len(rows), "transfers": rows}


def get_national_players(club):
    cid = _cid(club)
    doc = P.soup(get_page(_club_path(cid, "nationalspieler"), expect=f"/verein/{cid}"))
    out = {"club_id": cid, "current": [], "youth": [], "former": []}
    for box in doc.select(".box"):
        h = P.text(box.select_one("h2, .content-box-headline")) or ""
        table = box.select_one("table.items")
        if table is None:
            continue
        key = "youth" if "youth" in h.lower() else "former" if "previous" in h.lower() or "former" in h.lower() \
            else "current" if "national" in h.lower() else None
        if key is None:
            continue
        for tr in P.table_rows(table):
            tds = P.cells(tr)
            player = P.player_ref(tr)
            if not player:
                continue
            team_a = tr.select_one("a[href*='/verein/']")
            counts = [P.to_int(P.text(td)) for td in tds[-2:]]
            out[key].append({"player": player, "position": player.pop("position", None),
                             "age": P.to_int(P.text(tds[1])) if key != "former" else None,
                             "national_team": P.club_ref(team_a) if team_a is not None else None,
                             "caps": counts[-2] if len(counts) == 2 and key != "former" else counts[-1],
                             "goals": counts[-1] if key != "former" else None,
                             "is_captain": bool(tr.select_one("span[title='Team captain']"))})
    return out
