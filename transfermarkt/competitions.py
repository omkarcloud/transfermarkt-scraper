"""Competition endpoints. `competition` = a Transfermarkt competition code
(GB1, ES1, L1, CL, FAC) or a transfermarkt.com competition link; `season` =
the start year (2025 = 25/26).

  list            HTML /wettbewerbe/<region> (leagues & cups by tier)
  overview        tmapi /competition/{id} + /competition/{id}/regulation +
                  the HTML startseite (clubs of the season, champion facts)
  standings       tmapi /competition/{id}/table?season= (also cup league phases)
  top-scorers     HTML torschuetzenliste (paged, position / age-group filters)
  matchday        HTML spieltag (every match of one round, with match ids)
  fixtures        HTML gesamtspielplan (the full season schedule)
  transfers       HTML transfers tab (every club's arrivals & departures)
  market-values   HTML marktwerte (most valuable players of the competition)
  club-values     HTML marktwerteverein (clubs by squad value + change)
  rumours         HTML geruechte
Cups use the pokalwettbewerb controller; the site accepts either for reads,
so paths here always use `wettbewerb`.
"""
import re

from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_page, get_api, get_api_optional, run_parallel

TOP_SCORERS_PAGE_SIZE = 25
MARKET_VALUES_PAGE_SIZE = 25
RUMOURS_PAGE_SIZE = 25


def _comp(competition):
    return refs.resolve_competition(competition)


def _controller(code):
    """Leagues live under /wettbewerb/, cups under /pokalwettbewerb/ — a cup
    page requested through the league controller silently redirects to the
    JS-rendered cup homepage, so the controller is read off the competition's
    canonical link (tmapi, memoized per process)."""
    ref = lookup.competition(code)
    if ref is None:
        from .fetch import TransfermarktNotFound
        raise TransfermarktNotFound(f"competition {code} not found")
    return "pokalwettbewerb" if "pokalwettbewerb" in (ref.get("link") or "") else "wettbewerb"


def _comp_ref(code):
    ref = lookup.competition(code) or {}
    return {"id": code, "name": ref.get("name"), "link": ref.get("link") or P.entity_link("competition", code),
            "logo": ref.get("logo")}


def _path(code, tab, suffix=""):
    return f"/-/{tab}/{_controller(code)}/{code}{suffix}"


# ---- list ------------------------------------------------------------------------------

def get_list(region="europe"):
    slug = refs.REGIONS[region]
    doc = P.soup(get_page(f"/wettbewerbe/{slug}"))
    table = doc.select_one("table.items")
    tiers, current = [], None
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        if len(tds) == 1 or (len(tds) < 4 and not tr.select_one("a[href*='wettbewerb/']")):
            current = {"tier": P.text(tds[0]), "competitions": []}
            tiers.append(current)
            continue
        comp = P.competition_ref(tds[0])
        if not comp:
            continue
        if current is None:
            current = {"tier": None, "competitions": []}
            tiers.append(current)
        current["competitions"].append({
            **comp,
            "country": P.flag_country(tds[1].select_one("img")) if len(tds) > 1 else None,
            "clubs_count": P.to_int(P.text(tds[2])) if len(tds) > 2 else None,
            "players_count": P.to_int(P.text(tds[3])) if len(tds) > 3 else None,
            "average_age": P.to_float(P.text(tds[4])) if len(tds) > 4 else None,
            "foreigners_percent": P.percent(P.text(tds[5])) if len(tds) > 5 else None,
            "total_market_value": P.money_obj(P.text(tds[-1])),
        })
    count = sum(len(t["competitions"]) for t in tiers)
    return {"region": region, "count": count, "tiers": tiers}


# ---- overview -------------------------------------------------------------------------

def _clubs_table(doc):
    table = P.find_table(doc, "Clubs")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        club = P.club_ref(row.get("name") or row.get("Club"))
        if not club:
            continue
        club["crest"] = club.get("crest") or P.crest((row.get("Club") or row.get("col0")).select_one("img"))
        rows.append({
            "club": club,
            "squad_size": P.to_int(P.text(row.get("Squad"))),
            "average_age": P.to_float(P.text(row.get("ø age"))),
            "foreigners_count": P.to_int(P.text(row.get("Foreigners"))),
            "average_market_value": P.money_obj(P.text(row.get("ø market value"))),
            "total_market_value": P.money_obj(P.text(row.get("Total market value"))),
        })
    return rows


def get_overview(competition, season=None):
    code = _comp(competition)
    suffix = f"/saison_id/{season}" if season else ""
    api, regulation, html = run_parallel([
        lambda: get_api(f"/competition/{code}"),
        lambda: get_api_optional(f"/competition/{code}/regulation"),
        lambda: get_page(_path(code, "startseite", suffix), expect=f"wettbewerb/{code}"),
    ])
    doc = P.soup(html)
    hero = P.data_header(doc)
    base = api.get("baseDetails") or {}
    origin = api.get("originDetails") or {}
    shown = season or api.get("currentSeasonId")
    reg = None
    for r in regulation or []:
        if (r.get("season") or {}).get("id") == shown:
            reg = r
            break
    if reg is None and regulation:
        reg = next((r for r in regulation if r.get("isCurrentSeason")), regulation[0])
    winner = lookup.club(reg["winnerClubId"]) if reg and reg.get("winnerClubId") else None

    def hv(label):
        el = hero.get(label)
        return el

    champion = hv("Reigning champion")
    record = hv("Record-holding champions")
    mvp = hv("Most valuable player")
    coefficient = P.text(hv("UEFA coefficient")) if hv("UEFA coefficient") is not None else None
    coef_rank = re.search(r"(\d+)\.\s*Pos", coefficient or "")
    coef_points = re.search(r"([\d.,]+)\s*Points", coefficient or "")
    return {
        "id": api["id"],
        "name": api.get("name"),
        "link": P.link(api.get("relativeUrl")) or P.entity_link("competition", code),
        "logo": api.get("logoUrl") or None,
        "country": refs.country(origin.get("countryId")),
        "confederation": refs.confederation(origin.get("confederationId")),
        "tier": (refs.lookup("competition_types", api.get("typeId")) or {}).get("name"),
        "is_cup": bool(base.get("isTournament")) or "pokalwettbewerb" in (api.get("relativeUrl") or ""),
        "current_season": P.api_season(api.get("currentSeason")),
        "season": {"id": shown, "name": refs.season_name(shown)} if shown else None,
        "season_dates": {"start": reg.get("tournamentStart"), "end": reg.get("tournamentEnd")} if reg else None,
        "matchdays": base.get("gameDayCount") or None,
        "current_matchday": base.get("closestGameDay") or None,
        "is_ongoing": bool(base.get("isOngoing")),
        "reigning_champion": P.club_ref(champion) if champion is not None else winner,
        "record_champion": {**P.club_ref(record), "titles": P.to_int(re.sub(r".*?(\d+)\s*time.*", r"\1", P.text(record) or ""))}
        if record is not None and P.club_ref(record) else None,
        "uefa_coefficient": {"rank": int(coef_rank.group(1)) if coef_rank else None,
                             "points": P.to_float(coef_points.group(1)) if coef_points else None} if coefficient else None,
        "clubs_count": P.to_int(P.text(hv("Number of teams"))) if hv("Number of teams") is not None else None,
        "players_count": P.to_int(P.text(hv("Players"))) if hv("Players") is not None else None,
        "foreigners_count": P.to_int(P.text(hv("Foreigners"))) if hv("Foreigners") is not None else None,
        "foreigners_percent": P.percent(P.text(hv("Foreigners"))) if hv("Foreigners") is not None else None,
        "average_age": P.to_float(P.text(hv("ø-Age"))) if hv("ø-Age") is not None else None,
        "total_market_value": P.api_money(api.get("totalMarketValue")),
        "average_market_value": P.money_obj(P.text(hv("ø-Market value"))) if hv("ø-Market value") is not None else None,
        "most_valuable_player": {**P.player_ref(mvp), "market_value": P.money_obj(P.text(mvp))}
        if mvp is not None and P.player_ref(mvp) else None,
        "clubs": _clubs_table(doc),
    }


# ---- standings (tmapi) ----------------------------------------------------------------

def get_standings(competition, season=None):
    code = _comp(competition)
    params = {"season": season} if season else None
    data = get_api(f"/competition/{code}/table", params)
    tables = data.get("tables") or []
    club_ids = [c.get("clubId") for t in tables for c in (t.get("clubs") or [])]
    clubs = lookup.clubs(club_ids)
    meta = data.get("meta") or {}
    groups = []
    for t in tables:
        rows = []
        for c in t.get("clubs") or []:
            game, goal, rank, pos = (c.get(k) or {} for k in ("game", "goal", "ranking", "positioning"))
            rows.append({
                "position": rank.get("current"),
                "previous_position": rank.get("previous"),
                "movement": (rank.get("shift") or "").lower() or None,
                "club": clubs.get(str(c.get("clubId"))) or {"id": int(c["clubId"]), "name": None},
                "matches": game.get("totalCount"), "wins": game.get("winCount"),
                "draws": game.get("drawCount"), "losses": game.get("lossCount"),
                "goals_for": goal.get("totalCount"), "goals_against": goal.get("concededCount"),
                "goal_difference": goal.get("differenceCount"),
                "points": game.get("points"), "points_deducted": game.get("pointsMinus"),
                "is_playing_now": bool(game.get("isPlaying")),
                "qualification": pos.get("description") or None,
                "qualification_color": pos.get("color") or None,
            })
        groups.append({"name": (t.get("meta") or {}).get("name") or None, "count": len(rows), "table": rows})
    season_info = P.api_season(meta.get("season")) or ({"id": season, "name": refs.season_name(season)} if season else None)
    return {"competition_id": code, "season": season_info,
            "qualification_spots": [{"name": s.get("name"), "color": s.get("color")} for s in meta.get("leagueSpots") or []],
            "groups": groups}


# ---- top scorers -----------------------------------------------------------------------

def get_top_scorers(competition, season=None, page=1, position_id=None, age_group=None):
    code = _comp(competition)
    suffix = f"/saison_id/{season}" if season else ""
    suffix += f"/altersklasse/{age_group or 'alle'}/detailpos/{position_id or ''}/plus/1"
    if page and page > 1:
        suffix += f"/page/{page}"
    doc = P.soup(get_page(_path(code, "torschuetzenliste", suffix), expect=f"wettbewerb/{code}"))
    table = doc.select_one("table.items")
    rows = []
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        player = P.player_ref(tr)
        if not player or len(tds) < 6:
            continue
        nums = [P.to_int(P.text(td)) for td in tds[5:]]
        club_td = tds[4]
        rows.append({
            "rank": P.to_int(P.text(tds[0])),
            "player": player,
            "position": player.pop("position", None),
            "nationalities": P.flags(tds[2]),
            "age": P.to_int(P.text(tds[3])),
            "club": P.club_ref(club_td),
            "appearances": nums[0] if nums else None,
            "assists": nums[1] if len(nums) > 2 else None,
            "penalty_goals": nums[2] if len(nums) > 3 else None,
            "minutes_played": P.minutes(P.text(tds[-2])) if len(nums) > 3 else None,
            "goals": nums[-1] if nums else None,
        })
    return {"competition_id": code, "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "pagination": P.pagination(page, TOP_SCORERS_PAGE_SIZE, total_pages_=P.total_pages(doc)),
            "scorers": rows}


# ---- matchday / fixtures ------------------------------------------------------------------

def _score(text):
    m = re.match(r"^(\d+):(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _match_from_table(table, competition):
    """One matchday box: row 1 = clubs + result, row 2 = date/time."""
    links = [a for a in table.select("a[href*='/verein/']")]
    if not links:
        return None
    clubs = []
    for a in links:
        ref = P.club_ref(a)
        if ref and (not clubs or clubs[-1]["id"] != ref["id"]):
            clubs.append(ref)
    if len(clubs) < 2:
        return None
    home, away = clubs[0], clubs[-1]
    for ref, td in ((home, links[0]), (away, links[-1])):
        ref["crest"] = ref.get("crest") or P.crest(table.select_one(f"a[href*='/verein/{ref['id']}'] img"))
        cell = td.find_parent("td") or td
        m = re.search(r"\((\d+)\.\)", P.text(cell) or "")
        ref["table_position"] = int(m.group(1)) if m else None
        ref["name"] = re.sub(r"\s*\(\d+\.\)\s*", "", ref["name"] or "").strip() or ref["name"]
    result_a = table.select_one("a[href*='/spielbericht/']")
    result = P.text(result_a) if result_a is not None else None
    hg, ag = _score(result)
    date_a = table.select_one("a[href*='/datum/']")
    date_text = P.text(date_a) if date_a is not None else None
    date_row = date_a.find_parent("tr") if date_a is not None else None
    time_m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", P.text(date_row) or date_text or "")
    return {
        "match_id": P.id_in(result_a.get("href"), "match") if result_a is not None else None,
        "link": P.link(result_a.get("href")) if result_a is not None else None,
        "competition": competition,
        "date": P.iso_date(date_text),
        "time": time_m.group(1) if time_m else None,
        "home_club": home, "away_club": away,
        "home_goals": hg, "away_goals": ag,
        "result": result if result and result != "-:-" else None,
        "note": result if result and hg is None else (result.split(" ", 1)[1] if result and " " in result and hg is not None else None),
    }


def get_matchday(competition, season=None, matchday=None):
    code = _comp(competition)
    suffix = f"/saison_id/{season}" if season else ""
    if matchday:
        suffix += f"/spieltag/{matchday}"
    doc = P.soup(get_page(_path(code, "spieltag", suffix), expect=f"wettbewerb/{code}"))
    comp = _comp_ref(code)
    title = P.text(doc.select_one("h1"))
    matches = []
    for table in doc.select(".box table"):
        if table.select_one("thead"):
            continue
        row = _match_from_table(table, comp)
        if row and row["match_id"]:
            matches.append(row)
    selected = doc.select_one("select[name='spieltag'] option[selected]")
    shown_day = P.to_int(selected.get("value")) if selected is not None else matchday
    summary = {}
    table = P.find_table(doc, "Matchday summary", css="table")
    if table is not None:
        heads = P.header_labels(table)
        rows = P.table_rows(table)
        if rows:
            values = [P.text(td) for td in P.cells(rows[0])]
            # header_labels() yields the icon titles ("Goals", "Own goals"...);
            # the short letters are kept for a layout without icon titles.
            keymap = {"Matches": "matches", "Goals": "goals", "G": "goals", "Own goals": "own_goals",
                      "O": "own_goals", "Yellow cards": "yellow_cards", "Y": "yellow_cards",
                      "Second yellow cards": "second_yellow_cards", "S": "second_yellow_cards",
                      "Red cards": "red_cards", "R": "red_cards", "Penalty kicks": "penalties",
                      "Attendance": "attendance", "ø-Attendance": "average_attendance",
                      "Sold-out matches": "sold_out_matches"}
            for h, v in zip(heads, values):
                key = keymap.get(h)
                if key:
                    summary[key] = v if key == "penalties" else (P.to_int(v) if v else 0)
    return {"competition_id": code, "title": title,
            "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "matchday": shown_day, "summary": summary or None, "count": len(matches), "matches": matches}


def get_fixtures(competition, season=None):
    code = _comp(competition)
    suffix = f"/saison_id/{season}" if season else ""
    doc = P.soup(get_page(_path(code, "gesamtspielplan", suffix), expect=f"wettbewerb/{code}"))
    comp = _comp_ref(code)
    rounds = []
    for box in doc.select(".box"):
        h = box.select_one("h2, .content-box-headline")
        table = box.select_one("table")
        if h is None or table is None:
            continue
        heads = P.header_labels(table)
        if "Home team" not in heads and "Result" not in heads:
            continue
        name = P.text(h)
        matches, last_date, last_time = [], None, None
        for tr in P.table_rows(table):
            tds = P.cells(tr)
            if len(tds) < 5:
                date_a = tr.select_one("a[href*='/datum/']")
                if date_a is not None:
                    last_date = P.iso_date(P.text(date_a))
                    tm = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", P.text(date_a) or "")
                    last_time = tm.group(1) if tm else None
                continue
            club_links = tr.select("a[href*='/verein/']")
            if len(club_links) < 2:
                continue
            home = P.club_ref(club_links[0])
            away = P.club_ref(club_links[-1])
            for ref, a in ((home, club_links[0]), (away, club_links[-1])):
                m = re.search(r"\((\d+)\.\)", P.text(a) or "")
                ref["table_position"] = int(m.group(1)) if m else None
                ref["name"] = re.sub(r"\s*\(\d+\.\)\s*", "", ref["name"] or "").strip() or ref["name"]
            home["crest"] = P.crest(tr.select_one(f"a[href*='/verein/{home['id']}'] img"))
            away["crest"] = P.crest(tr.select_one(f"a[href*='/verein/{away['id']}'] img"))
            result_a = tr.select_one("a[href*='/spielbericht/']")
            result = P.text(result_a) if result_a is not None else None
            hg, ag = _score(result)
            date_text = P.text(tds[0])
            if P.iso_date(date_text):
                last_date = P.iso_date(date_text)
            time_text = P.text(tds[1]) if len(tds) > 1 else None
            if time_text and re.search(r"\d{1,2}:\d{2}", time_text):
                last_time = time_text
            matches.append({
                "match_id": P.id_in(result_a.get("href"), "match") if result_a is not None else None,
                "link": P.link(result_a.get("href")) if result_a is not None else None,
                "competition": comp, "round": name,
                "date": last_date, "time": last_time,
                "home_club": home, "away_club": away,
                "home_goals": hg, "away_goals": ag,
                "result": result if result and result != "-:-" else None,
            })
        if matches:
            rounds.append({"round": name, "count": len(matches), "matches": matches})
    return {"competition_id": code,
            "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "count": sum(r["count"] for r in rounds), "rounds": rounds}


# ---- transfers ----------------------------------------------------------------------------

def _comp_transfer_row(tr, direction):
    tds = P.cells(tr)
    if len(tds) < 8:
        return None
    a = tds[0].select_one("a[href*='/spieler/']")
    if a is None:
        return None
    pid = P.id_in(a.get("href"), "player")
    other_a = tr.select_one("a[href*='/verein/']")
    other = P.club_ref(other_a) if other_a is not None else None
    if other is not None:
        flag = tds[6].select_one("img[src*='/flagge/']") or tds[7].select_one("img[src*='/flagge/']")
        other["country"] = P.flag_country(flag)
        name_td = tds[7] if len(tds) > 8 else None
        if name_td is not None and P.text(name_td):
            other["name"] = other["name"] or P.text(name_td)
    return {
        "player": {"id": pid, "name": P.clean(a.get("title")) or P.text(a),
                   "link": P.entity_link("player", pid, P.slug_of(a.get("href")))},
        "age": P.to_int(P.text(tds[1])),
        "nationalities": P.flags(tds[2]),
        "position": P.text(tds[3]),
        "position_short": P.text(tds[4]),
        "market_value": P.money_obj(P.text(tds[5])),
        ("from_club" if direction == "arrivals" else "to_club"): other,
        "fee": P.fee(P.text(tds[-1])),
    }


def get_transfers(competition, season=None):
    code = _comp(competition)
    suffix = f"/saison_id/{season}" if season else ""
    doc = P.soup(get_page(_path(code, "transfers", suffix), expect=f"wettbewerb/{code}"))
    clubs = []
    for box in doc.select(".box"):
        h = box.select_one("h2[id^='to-']")
        if h is None:
            continue
        club = P.club_ref(h)
        tables = box.select("table")
        arrivals, departures = [], []
        for t in tables:
            heads = P.header_labels(t)
            direction = "arrivals" if heads and heads[0] == "In" else "departures" if heads and heads[0] == "Out" else None
            if not direction:
                continue
            for tr in P.table_rows(t):
                row = _comp_transfer_row(tr, direction)
                if row:
                    (arrivals if direction == "arrivals" else departures).append(row)
        spend = sum((r["fee"]["amount"] or 0) for r in arrivals)
        income = sum((r["fee"]["amount"] or 0) for r in departures)
        clubs.append({"club": club, "arrivals_count": len(arrivals), "departures_count": len(departures),
                      "expenditure": {"amount": spend, "currency": "EUR"},
                      "income": {"amount": income, "currency": "EUR"},
                      "balance": {"amount": income - spend, "currency": "EUR"},
                      "arrivals": arrivals, "departures": departures})
    record = {}
    box = P.box_by_headline(doc, "Transfer record")
    if box is not None:
        flat = box.get_text(" ", strip=True)
        for label in ("Departures", "Arrivals", "Income", "Income per club", "Income per player",
                      "Transfer expenses", "Expenditures per club", "Expenditures per player",
                      "Total balance", "Balance per club", "Balance per player"):
            m = re.search(rf"(?<![A-Za-z ]){re.escape(label)}:\s*(€?-?[\d.,]+)", flat)
            if m:
                record[label] = m.group(1)
    summary = {
        "arrivals_count": P.to_int(record.get("Arrivals")),
        "departures_count": P.to_int(record.get("Departures")),
        "expenditure": P.money_obj(record.get("Transfer expenses")),
        "income": P.money_obj(record.get("Income")),
        "balance": P.money_obj(record.get("Total balance")),
        "expenditure_per_club": P.money_obj(record.get("Expenditures per club")),
        "income_per_club": P.money_obj(record.get("Income per club")),
    } if record else None
    shown = season
    sel = doc.select_one("select[name='saison_id'] option[selected]")
    if sel is not None:
        shown = P.to_int(sel.get("value")) or season
    return {"competition_id": code, "season": {"id": shown, "name": refs.season_name(shown)} if shown else None,
            "summary": summary, "clubs_count": len(clubs), "clubs": clubs}


# ---- market values -------------------------------------------------------------------------

def get_market_values(competition, page=1, position_group=None, position_id=None, age_group=None,
                      only_loans=None):
    code = _comp(competition)
    params = {"ausrichtung": refs.POSITION_GROUPS.get(position_group, ""),
              "spielerposition_id": position_id or "", "altersklasse": age_group or "alle",
              "plus": 1, "galerie": 0}
    if only_loans:
        params["only_loans"] = 1
    if page and page > 1:
        params["page"] = page
    doc = P.soup(get_page(_path(code, "marktwerte"), params, expect=f"wettbewerb/{code}"))
    table = doc.select_one("table.items")
    rows = []
    for tr in P.table_rows(table) if table is not None else []:
        tds = P.cells(tr)
        player = P.player_ref(tr)
        if not player or len(tds) < 6:
            continue
        rows.append({"rank": P.to_int(P.text(tds[0])), "player": player,
                     "position": player.pop("position", None), "nationalities": P.flags(tds[2]),
                     "age": P.to_int(P.text(tds[3])), "club": P.club_ref(tds[4]),
                     "market_value": P.money_obj(P.text(tds[-1]))})
    return {"competition_id": code,
            "pagination": P.pagination(page, MARKET_VALUES_PAGE_SIZE, total_pages_=P.total_pages(doc)),
            "players": rows}


def get_club_values(competition):
    code = _comp(competition)
    doc = P.soup(get_page(_path(code, "marktwerteverein"), expect=f"wettbewerb/{code}"))
    table = doc.select_one("table.items")
    heads = P.header_labels(table) if table is not None else []
    prev_label = next((h for h in heads if h.startswith("Value")), None)
    prev_date = P.iso_date(prev_label) if prev_label else None
    rows = []
    for row in P.items_table(table) if table is not None else []:
        club = P.club_ref(row.get("Club"))
        if not club:
            continue
        club["crest"] = club.get("crest") or P.crest((row.get("wappen") or row.get("col1")).select_one("img"))
        rows.append({"rank": P.to_int(P.text(row.get("#"))), "club": club,
                     "competition_name": P.text(row.get("League")),
                     "previous_value": P.money_obj(P.text(row.get(prev_label))) if prev_label else None,
                     "current_value": P.money_obj(P.text(row.get("Current value"))),
                     "change_percent": P.to_float(P.text(row.get("%")))})
    return {"competition_id": code, "previous_value_date": prev_date, "count": len(rows), "clubs": rows}


# ---- rumours ---------------------------------------------------------------------------------

def parse_rumour_rows(table):
    rows = []
    for row in P.items_table(table) if table is not None else []:
        player = P.player_ref(row.get("Player"))
        if not player:
            continue
        interested = row.get("Interested club")
        interested_club = P.club_ref(interested) if interested is not None else None
        if interested_club and interested is not None:
            comp = interested.select_one("a[href*='wettbewerb/']")
            interested_club["competition"] = P.competition_ref(comp) if comp is not None else None
        source = row.get("Most recent source") or row.get("Last reply")
        a = source.select_one("a[href]") if source is not None else None
        rows.append({
            "player": player, "position": player.pop("position", None),
            "nationalities": P.flags(row.get("Nation") or row.get("Nat.")),
            "age": P.to_int(P.text(row.get("Age"))),
            "current_club": P.club_ref(row.get("Club") or row.get("Current club")),
            "interested_club": interested_club,
            "last_source_date": P.iso_date(P.text(row.get("Most recent source"))),
            "last_reply_date": P.iso_date(P.text(row.get("Last reply"))),
            "probability_percent": P.percent(P.text(row.get("Assessment") or row.get("User assessment"))),
            "thread_link": P.link(a.get("href")) if a is not None else None,
        })
    return rows


def get_rumours(competition, page=1):
    code = _comp(competition)
    suffix = f"/page/{page}" if page and page > 1 else ""
    doc = P.soup(get_page(_path(code, "geruechte", suffix), expect=f"wettbewerb/{code}"))
    rows = parse_rumour_rows(P.find_table(doc, "Latest rumours") or doc.select_one("table.items"))
    return {"competition_id": code,
            "pagination": P.pagination(page, RUMOURS_PAGE_SIZE, total_pages_=P.total_pages(doc)),
            "rumours": rows}
