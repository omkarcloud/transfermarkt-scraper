"""Player endpoints. `player` = numeric id or a transfermarkt.com player link.

Sources per endpoint (see fetch.py):
  profile          tmapi /player/{id} + the HTML profile page (header extras,
                   social links, notes) + tmapi clubs for club names
  market-value     /ceapi/marketValueDevelopment/graph/{id}
  transfers        /ceapi/transferHistory/list/{id}
  stats, match-log, national-team
                   tmapi /player/{id}/performance-game — every game of the
                   career with the player's per-game statistics; the site's
                   own "Stats" tabs aggregate this client-side, so we do the
                   same (one cached 1.4 MB call, no HTML)
  injuries / achievements / squad-numbers / rumours
                   HTML tabs (verletzungen, erfolge, rueckennummern, geruechte)
  upcoming-matches /ceapi/nextMatches/player/{id}
"""
import re
from collections import OrderedDict, defaultdict

from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_api, get_ceapi, get_page, run_parallel, TransfermarktNotFound

MATCH_LOG_PAGE_SIZE = 50
# Placeholder "clubs" the site files players under when they have none.
SPECIAL_CLUBS = {"123": "retired", "515": "without_club", "75": "unknown"}
INJURIES_PAGE_SIZE = 15   # the site's page size on the injuries tab


def _pid(player):
    return refs.resolve_player(player)


def _player_path(pid, tab):
    return f"/-/{tab}/spieler/{pid}"


# ---- profile -----------------------------------------------------------------------

def _header_extras(doc):
    """Bits only the HTML hero/info table carry: shirt number, caps/goals,
    national team, loan info, retirement, social links, notes, youth clubs."""
    hero = P.data_header(doc)
    info = P.info_table(doc)
    out = {}
    number = doc.select_one(".data-header__shirt-number")
    out["shirt_number"] = P.to_int(P.text(number)) if number is not None else None

    def val(label):
        el = hero.get(label) or info.get(label)
        return P.text(el) if el is not None else None

    caps = hero.get("Caps/Goals")
    if caps is not None:
        m = re.search(r"(\d+)\s*/\s*(\d+)", P.text(caps) or "")
        out["caps"] = int(m.group(1)) if m else P.to_int(P.text(caps))
        out["international_goals"] = int(m.group(2)) if m else None
    else:
        out["caps"] = out["international_goals"] = None
    nt = hero.get("Current international") or hero.get("Former International") or info.get("Current international")
    if nt is not None:
        a = nt.select_one("a[href*='/verein/']")
        out["national_team"] = {"id": P.id_in(a.get("href"), "club") if a is not None else None,
                                "name": P.text(nt),
                                "link": P.link(a.get("href")) if a is not None else None,
                                "is_current": "Current international" in hero}
    else:
        out["national_team"] = None
    out["retired_since"] = P.iso_date(val("Retired since"))
    loan = hero.get("On loan from") or info.get("On loan from")
    if loan is not None:
        out["on_loan_from"] = P.club_ref(loan)
        out["loan_contract_until"] = P.iso_date(val("Contract there expires"))
    else:
        out["on_loan_from"] = None
        out["loan_contract_until"] = None
    social = info.get("Social-Media")
    out["social_links"] = [{"name": P.clean(a.get("title")), "link": a.get("href")}
                           for a in (social.select("a[href]") if social is not None else [])]
    box = P.box_by_headline(doc, "Further information")
    out["notes"] = None
    if box is not None:
        h = box.select_one("h2, .content-box-headline")
        body = P.clean(box.get_text(" ", strip=True).replace(P.text(h) or "", "", 1))
        out["notes"] = body or None
    youth = P.box_by_headline(doc, "Youth clubs")
    out["youth_clubs"] = None
    if youth is not None:
        h = youth.select_one("h2, .content-box-headline")
        out["youth_clubs"] = P.clean(youth.get_text(" ", strip=True).replace(P.text(h) or "", "", 1))
    return out


def _status(assignments, extras):
    """active | retired | without_club from the current club assignment."""
    for a in assignments:
        if a.get("type") == "current":
            special = SPECIAL_CLUBS.get(str(a.get("clubId")))
            if special == "retired":
                return "retired"
            if special == "without_club":
                return "without_club"
            return "active"
    return "retired" if extras.get("retired_since") else None


def get_profile(player):
    pid = _pid(player)
    api = get_api(f"/player/{pid}")          # cheap and 404s fast for unknown ids
    html = get_page(_player_path(pid, "profil"), expect=f"/spieler/{pid}")
    doc = P.soup(html)
    extras = _header_extras(doc)
    life = api.get("lifeDates") or {}
    birth = api.get("birthPlaceDetails") or {}
    attrs = api.get("attributes") or {}
    nat = api.get("nationalityDetails") or {}
    agency = attrs.get("consultantAgency") or {}
    assignments = api.get("clubAssignments") or []
    club_ids = [a.get("clubId") for a in assignments] + [api.get("lastClubId")]
    club_refs = lookup.clubs([c for c in club_ids if c not in (None, 0, "0")])

    def assignment(kind):
        for a in assignments:
            if a.get("type") == kind:
                if kind == "current" and str(a.get("clubId")) in SPECIAL_CLUBS:
                    return None
                ref = dict(club_refs.get(str(a.get("clubId"))) or {"id": int(a["clubId"]), "name": None,
                                                                  "link": P.entity_link("club", a["clubId"])})
                ref.update({"shirt_number": a.get("shirtNumber"), "is_captain": bool(a.get("isCaptain")),
                            "joined": a.get("start") or None, "debut": a.get("debut") or None})
                return ref
        return None

    renewal = attrs.get("lastContractRenewal") or {}
    last_extension = (f"{renewal['year']:04d}-{renewal['month']:02d}-{renewal['day']:02d}"
                      if renewal.get("year") else None)
    side = [refs.position(attrs.get("firstSidePositionId")), refs.position(attrs.get("secondSidePositionId"))]
    out = {
        "id": int(api["id"]),
        "name": api.get("name"),
        "short_name": api.get("shortName") or None,
        "full_name": nat.get("passportName") or None,
        "link": P.link(api.get("relativeUrl")) or P.entity_link("player", pid),
        "image": api.get("portraitUrl") or None,
        "date_of_birth": life.get("dateOfBirth"),
        "age": life.get("age"),
        "date_of_death": life.get("dateOfDeath"),
        "place_of_birth": birth.get("placeOfBirth") or None,
        "country_of_birth": refs.country(birth.get("countryOfBirthId")),
        "nationalities": P.api_nationalities(nat.get("nationalities")),
        "height_m": attrs.get("height") or None,
        "preferred_foot": (attrs.get("preferredFoot") or {}).get("name"),
        "position": refs.position(attrs.get("positionId")),
        "position_group": attrs.get("positionGroupName") or None,
        "side_positions": [s for s in side if s],
        "shirt_number": extras["shirt_number"],
        "current_club": assignment("current"),
        "on_loan_from": extras["on_loan_from"],
        "loan_contract_until": extras["loan_contract_until"],
        "national_team": extras["national_team"] or assignment("nationalTeam"),
        "caps": extras["caps"],
        "international_goals": extras["international_goals"],
        "contract_until": attrs.get("contractUntil") or None,
        "contract_option": (refs.lookup("contracts", attrs.get("contractOptionId")) or {}).get("name"),
        "last_contract_extension": last_extension,
        "status": _status(assignments, extras),
        "is_retired": bool(extras["retired_since"]) or _status(assignments, extras) == "retired",
        "retired_since": extras["retired_since"],
        "market_value": P.api_market_value(api.get("marketValueDetails")),
        "agent": {"id": agency.get("id"), "name": agency.get("name"),
                  "link": P.link(agency.get("relativeUrl")),
                  "is_verified": agency.get("verificationStatus") == "verified"} if agency.get("id") else None,
        "outfitter": (attrs.get("outfitter") or {}).get("name"),
        "youth_clubs": attrs.get("formerClubsNote") or extras["youth_clubs"],
        "social_links": extras["social_links"],
        "notes": extras["notes"],
        "image_source": api.get("portraitUrlSource") or None,
    }
    return out


# ---- market value + transfers (ceapi) ------------------------------------------------

def get_market_value(player):
    pid = _pid(player)
    payload = get_ceapi(f"/ceapi/marketValueDevelopment/graph/{pid}")
    rows = payload.get("list") if isinstance(payload, dict) else None
    if rows is None:
        raise TransfermarktNotFound(f"no market value history for player {pid}")
    history = []
    for r in rows:
        history.append({
            "date": P.iso_date(r.get("datum_mw")) or _epoch_date(r.get("x")),
            "value": {"amount": r.get("y"), "currency": "EUR"} if r.get("y") is not None else None,
            "age": P.to_int(r.get("age")),
            "club": {"name": P.clean(r.get("verein")), "crest": P.image(r.get("wappen"), "head")},
        })
    values = [h["value"]["amount"] for h in history if h["value"]]
    highest = max(values) if values else None
    return {
        "player_id": pid,
        "current": history[-1]["value"] if history else None,
        "current_date": history[-1]["date"] if history else None,
        "highest": {"amount": highest, "currency": "EUR",
                    "date": next((h["date"] for h in history if h["value"] and h["value"]["amount"] == highest), None)}
        if highest is not None else None,
        "count": len(history),
        "history": history,
    }


def _epoch_date(ms):
    if not ms:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _ceapi_club(c):
    if not isinstance(c, dict):
        return None
    href = c.get("href") or ""
    cid = P.id_in(href, "club")
    return {"id": cid, "name": P.clean(c.get("clubName")),
            "link": P.entity_link("club", cid) if cid else P.link(href),
            "crest": P.image(c.get("clubEmblem-2x") or c.get("clubEmblem-1x"), "head"),
            "country": P.flag_country(_FakeImg(c.get("countryFlag")))}


class _FakeImg:
    """Adapter so flag_country can read a bare flag URL."""

    def __init__(self, src):
        self.src = src

    def get(self, key, default=None):
        return self.src if key == "src" else default


def get_transfers(player):
    pid = _pid(player)
    payload = get_ceapi(f"/ceapi/transferHistory/list/{pid}")
    rows = payload.get("transfers") if isinstance(payload, dict) else None
    if rows is None:
        raise TransfermarktNotFound(f"no transfer history for player {pid}")
    transfers = []
    for r in rows:
        transfers.append({
            "id": _transfer_id(r.get("url")),
            "date": r.get("dateUnformatted") or P.iso_date(r.get("date")),
            "season": P.season_obj(r.get("season")),
            "from_club": _ceapi_club(r.get("from")),
            "to_club": _ceapi_club(r.get("to")),
            "market_value": P.money_obj(r.get("marketValue")),
            "fee": P.fee(r.get("fee")),
            "is_upcoming": bool(r.get("upcoming") or r.get("futureTransfer")),
            "link": P.link(r.get("url")),
        })
    return {"player_id": pid, "count": len(transfers),
            "total_fees": P.money_obj(payload.get("formattedFeeSum")) or (
                {"amount": payload.get("feeSum"), "currency": "EUR"} if payload.get("feeSum") else None),
            "transfers": transfers}


def _transfer_id(url):
    m = re.search(r"/transfer_id/(\d+)", url or "")
    return int(m.group(1)) if m else None


# ---- performance log (tmapi) --------------------------------------------------------

def _performance(pid):
    data = get_api(f"/player/{pid}/performance-game")
    if not isinstance(data, dict):
        raise TransfermarktNotFound(f"no performance data for player {pid}")
    return data


def _game_row(e, clubs, comps):
    """One performance-game entry -> a public match-log row."""
    g = e.get("gameInformation") or {}
    ci = e.get("clubsInformation") or {}
    own, opp = ci.get("club") or {}, ci.get("opponent") or {}
    st = e.get("statistics") or {}
    gen, goals, cards, tm, duel, dist = (st.get(k) or {} for k in (
        "generalStatistics", "goalStatistics", "cardStatistics", "playingTimeStatistics",
        "duelStatistics", "distributionStatistics"))
    date = (g.get("date") or {}).get("dateTimeUTC")
    own_ref = clubs.get(str(own.get("clubId"))) or {"id": _int(own.get("clubId")), "name": None}
    opp_ref = clubs.get(str(opp.get("clubId"))) or {"id": _int(opp.get("clubId")), "name": None}
    home, away = (own_ref, opp_ref) if own.get("venue") == "home" else (opp_ref, own_ref)
    home_goals, away_goals = ((own.get("goalsTotal"), opp.get("goalsTotal")) if own.get("venue") == "home"
                              else (opp.get("goalsTotal"), own.get("goalsTotal")))
    state = gen.get("participationState")
    injury = refs.lookup("injuries", gen.get("injuryId"))
    absence = refs.lookup("absences", gen.get("absenceId"))
    return {
        "match": {
            "id": _int(g.get("gameId")),
            "link": P.entity_link("match", g.get("gameId")),
            "date": date[:10] if date else None,
            "datetime_utc": date,
            "competition": comps.get(str(g.get("competitionId"))) or {"id": g.get("competitionId"), "name": None},
            "season": P.api_season(g.get("season")),
            "matchday": g.get("gameDay"),
            "round": (refs.lookup("competition_groups", g.get("competitionGroupId")) or {}).get("name"),
            "home_club": home, "away_club": away,
            "home_goals": home_goals, "away_goals": away_goals,
            "state": g.get("gameState"),
            "is_national_team_match": bool(g.get("isNationalGame")),
        },
        "club": own_ref,
        "venue": own.get("venue"),
        "result": _result(own.get("goalsTotal"), opp.get("goalsTotal")),
        "participation": state,
        "absence_reason": (injury or absence or {}).get("name"),
        "position": refs.position(gen.get("positionId")),
        "shirt_number": gen.get("shirtNumber"),
        "is_captain": bool(gen.get("isCaptain")),
        "is_starter": bool(tm.get("isStarting")),
        "minutes_played": tm.get("playedMinutes"),
        "substituted_in_minute": _minute(tm.get("substitutedIn")),
        "substituted_out_minute": _minute(tm.get("substitutedOut")),
        "goals": goals.get("goalsScoredTotal"),
        "assists": goals.get("assists"),
        "own_goals": goals.get("ownGoalsScored"),
        "penalty_goals": goals.get("penaltyShooterGoalsScored"),
        "penalties_missed": goals.get("penaltyShooterMisses"),
        "yellow_cards": _count(cards.get("yellowCardNet")) if cards.get("yellowCardNet") is not None else _count(cards.get("yellowCard")) or None,
        "second_yellow_cards": _second_yellow(cards),
        "red_cards": _count(cards.get("redCard")) if cards.get("redCard") is not None else None,
        "rating": gen.get("grade"),
        "shots": goals.get("scoringAttempts"),
        "shots_on_target": goals.get("scoringAttemptsOnGoal"),
        "passes": dist.get("passes"),
        "passes_completed": dist.get("passesReached"),
        "tackles": duel.get("tackles"),
        "tackles_won": duel.get("tacklesWon"),
        "fouls_committed": duel.get("foulsCommitted"),
        "fouls_suffered": duel.get("foulsGained"),
        "offsides": duel.get("offsides"),
        "team_goals_on_pitch": goals.get("teamGoalsOnThePitch"),
        "opponent_goals_on_pitch": goals.get("opponentGoalsOnThePitch"),
    }


def _minute(value):
    """tmapi sometimes ships a card / substitution as {minute, minuteOvertime,
    actionId} instead of a bare minute (national-team games) -> the minute."""
    if isinstance(value, dict):
        return value.get("minute")
    return value or None


def _count(value):
    """Event counters that may arrive as a dict (one event) or an int."""
    if isinstance(value, dict):
        return 1
    return value or 0


def _second_yellow(cards):
    gross, net = cards.get("yellowCardGross"), cards.get("yellowCardNet")
    if not isinstance(gross, int) or not isinstance(net, int):
        return None
    return max(gross - net, 0)


def _result(own_goals, opp_goals):
    if own_goals is None or opp_goals is None:
        return None
    return "win" if own_goals > opp_goals else "loss" if own_goals < opp_goals else "draw"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _filter_games(perf, season=None, competition=None, national=None):
    rows = list(perf.get("performance") or [])
    if season is not None:
        rows = [e for e in rows if (e.get("gameInformation") or {}).get("seasonId") == season]
    if competition:
        rows = [e for e in rows if (e.get("gameInformation") or {}).get("competitionId") == competition]
    if national is not None:
        rows = [e for e in rows if bool((e.get("gameInformation") or {}).get("isNationalGame")) == national]
    return rows


def _hydrate_for(rows):
    club_ids = {(e.get("clubsInformation") or {}).get(side, {}).get("clubId")
                for e in rows for side in ("club", "opponent")}
    comp_ids = {(e.get("gameInformation") or {}).get("competitionId") for e in rows}
    clubs, comps = run_parallel([
        lambda: lookup.clubs([c for c in club_ids if c]),
        lambda: lookup.competitions([c for c in comp_ids if c]),
    ])
    return clubs, comps


def get_match_log(player, season=None, competition=None, page=1):
    pid = _pid(player)
    perf = _performance(pid)
    rows = _filter_games(perf, season, competition)
    rows.sort(key=lambda e: ((e.get("gameInformation") or {}).get("date") or {}).get("dateTimeUTC") or "",
              reverse=True)
    total = len(rows)
    start = (page - 1) * MATCH_LOG_PAGE_SIZE
    page_rows = rows[start:start + MATCH_LOG_PAGE_SIZE]
    clubs, comps = _hydrate_for(page_rows)
    return {
        "player_id": pid,
        "season": {"id": season, "name": refs.season_name(season)} if season else None,
        "competition_id": competition,
        "pagination": P.pagination(page, MATCH_LOG_PAGE_SIZE, total),
        "matches": [_game_row(e, clubs, comps) for e in page_rows],
    }


def _aggregate(rows):
    agg = {"matches": 0, "appearances": 0, "starts": 0, "minutes_played": 0, "goals": 0, "assists": 0,
           "own_goals": 0, "penalty_goals": 0, "yellow_cards": 0, "second_yellow_cards": 0, "red_cards": 0,
           "substituted_in": 0, "substituted_out": 0, "in_squad_unused": 0, "not_in_squad": 0,
           "injured": 0, "wins": 0, "draws": 0, "losses": 0, "ratings": []}
    for e in rows:
        st = e.get("statistics") or {}
        gen, goals, cards, tm = (st.get(k) or {} for k in (
            "generalStatistics", "goalStatistics", "cardStatistics", "playingTimeStatistics"))
        agg["matches"] += 1
        state = gen.get("participationState")
        if state == "played":
            agg["appearances"] += 1
            agg["starts"] += 1 if tm.get("isStarting") else 0
            agg["minutes_played"] += tm.get("playedMinutes") or 0
            agg["goals"] += goals.get("goalsScoredTotal") or 0
            agg["assists"] += goals.get("assists") or 0
            agg["own_goals"] += goals.get("ownGoalsScored") or 0
            agg["penalty_goals"] += goals.get("penaltyShooterGoalsScored") or 0
            agg["yellow_cards"] += _count(cards.get("yellowCardNet") if cards.get("yellowCardNet") is not None
                                          else cards.get("yellowCard"))
            agg["second_yellow_cards"] += _second_yellow(cards) or 0
            agg["red_cards"] += _count(cards.get("redCard"))
            agg["substituted_in"] += 1 if tm.get("substitutedIn") else 0
            agg["substituted_out"] += 1 if tm.get("substitutedOut") else 0
            ci = e.get("clubsInformation") or {}
            res = _result((ci.get("club") or {}).get("goalsTotal"), (ci.get("opponent") or {}).get("goalsTotal"))
            if res:
                agg[res + ("es" if res == "loss" else "s")] += 1
            if gen.get("grade") is not None:
                agg["ratings"].append(gen["grade"])
        elif state == "in squad":
            agg["in_squad_unused"] += 1
        elif state == "not in squad":
            agg["not_in_squad"] += 1
        elif state == "injured":
            agg["injured"] += 1
    ratings = agg.pop("ratings")
    agg["average_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else None
    agg["minutes_per_goal"] = (round(agg["minutes_played"] / agg["goals"]) if agg["goals"] else None)
    return agg


def get_stats(player, season=None, competition=None):
    """Season x competition totals (all seasons when `season` is omitted)."""
    pid = _pid(player)
    perf = _performance(pid)
    rows = _filter_games(perf, season, competition)
    groups = OrderedDict()
    for e in rows:
        g = e.get("gameInformation") or {}
        key = (g.get("seasonId"), g.get("competitionId"))
        groups.setdefault(key, []).append(e)
    club_ids, comp_ids = set(), set()
    for (_, cid), items in groups.items():
        comp_ids.add(cid)
        for e in items:
            club_ids.add((e.get("clubsInformation") or {}).get("club", {}).get("clubId"))
    clubs, comps = run_parallel([
        lambda: lookup.clubs([c for c in club_ids if c]),
        lambda: lookup.competitions([c for c in comp_ids if c]),
    ])
    out_rows = []
    for (sid, cid), items in sorted(groups.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or ""), reverse=True):
        season_info = (items[0].get("gameInformation") or {}).get("season")
        club_counts = defaultdict(int)
        for e in items:
            club_counts[(e.get("clubsInformation") or {}).get("club", {}).get("clubId")] += 1
        main_club = max(club_counts, key=club_counts.get) if club_counts else None
        row = {"season": P.api_season(season_info) or {"id": sid, "name": refs.season_name(sid)},
               "competition": comps.get(str(cid)) or {"id": cid, "name": None},
               "club": clubs.get(str(main_club)) or ({"id": _int(main_club), "name": None} if main_club else None)}
        row.update(_aggregate(items))
        out_rows.append(row)
    totals = _aggregate(rows)
    return {"player_id": pid,
            "season": {"id": season, "name": refs.season_name(season)} if season else None,
            "competition_id": competition,
            "totals": totals, "count": len(out_rows), "stats": out_rows}


def get_national_team(player):
    """Per national team (senior + youth) career totals from the game log."""
    pid = _pid(player)
    perf = _performance(pid)
    rows = _filter_games(perf, national=True)
    by_team = OrderedDict()
    for e in rows:
        team = (e.get("clubsInformation") or {}).get("club", {}).get("clubId")
        by_team.setdefault(team, []).append(e)
    clubs = lookup.clubs([t for t in by_team if t])
    teams = []
    for team, items in by_team.items():
        items.sort(key=lambda e: ((e.get("gameInformation") or {}).get("date") or {}).get("dateTimeUTC") or "")
        played = [e for e in items if (e.get("statistics") or {}).get("generalStatistics", {}).get("participationState") == "played"]
        first = ((played[0].get("gameInformation") or {}).get("date") or {}).get("dateTimeUTC") if played else None
        last = ((played[-1].get("gameInformation") or {}).get("date") or {}).get("dateTimeUTC") if played else None
        row = {"team": clubs.get(str(team)) or {"id": _int(team), "name": None},
               "debut": first[:10] if first else None,
               "last_match": last[:10] if last else None,
               "age_at_debut": (played[0].get("statistics") or {}).get("generalStatistics", {}).get("age") if played else None}
        row.update(_aggregate(items))
        teams.append(row)
    teams.sort(key=lambda t: t["appearances"], reverse=True)
    return {"player_id": pid, "count": len(teams), "teams": teams}


# ---- HTML tabs ---------------------------------------------------------------------------

def get_injuries(player, page=1):
    pid = _pid(player)
    params = {"page": page} if page and page > 1 else None
    doc = P.soup(get_page(_player_path(pid, "verletzungen"), params, expect=f"/spieler/{pid}"))
    table = P.find_table(doc, "Injury history")
    injuries = []
    for row in P.items_table(table) if table is not None else []:
        missed = row.get("Games missed")
        injuries.append({
            "season": P.season_obj(P.text(row.get("Season"))),
            "injury": P.text(row.get("Injury")),
            "from": P.iso_date(P.text(row.get("from"))),
            "until": P.iso_date(P.text(row.get("until"))),
            "days": P.to_int(P.text(row.get("Days"))),
            "games_missed": P.to_int(P.text(missed)) if missed is not None else None,
            "clubs_affected": [P.club_ref(a) for a in missed.select("a[href*='/verein/']")] if missed is not None else [],
        })
    totals_table = P.find_table(doc, "Total")
    totals = []
    for row in P.items_table(totals_table) if totals_table is not None else []:
        totals.append({"season": P.season_obj(P.text(row.get("Season"))),
                       "days": P.to_int(P.text(row.get("Days"))),
                       "injuries": P.to_int(P.text(row.get("Injuries"))),
                       "games_missed": P.to_int(P.text(row.get("Games missed")))})
    return {"player_id": pid,
            "pagination": P.pagination(page, INJURIES_PAGE_SIZE, total_pages_=P.total_pages(doc)),
            "injuries": injuries, "totals_by_season": totals}


def parse_achievements(doc):
    """The 'All titles' table: title header rows followed by one row per
    season/club (shared by player, club and manager achievement tabs)."""
    box = P.box_by_headline(doc, "All titles")
    table = box.select_one("table") if box is not None else None
    if table is None:
        return _achievement_boxes(doc)
    titles, current = [], None
    for tr in P.table_rows(table):
        tds = P.cells(tr)
        first = tds[0]
        if "hauptlink" in (first.get("class") or []) and first.get("colspan"):
            label = P.text(first) or ""
            m = re.match(r"^\s*(\d+)\s*x\s+(.+)$", label, re.I)
            count = int(m.group(1)) if m else None
            name = m.group(2).strip() if m else label
            current = {"title": name, "count": count, "wins": []}
            titles.append(current)
            continue
        if current is None:
            continue
        win = {"season": P.season_obj(P.text(first)),
               "club": P.club_ref(tr) if tr.select_one("a[href*='/verein/']") else None}
        extra = [P.text(td) for td in tds[1:] if P.text(td) and not td.select_one("a[href*='/verein/']")]
        if extra and not win["club"]:
            win["note"] = " ".join(extra)
        current["wins"].append(win)
    return titles


def _achievement_boxes(doc):
    """Coach pages list one box per title ('3x English Champion') holding a
    season/club table instead of one 'All titles' table."""
    titles = []
    for box in doc.select(".box"):
        h = P.text(box.select_one("h2, .content-box-headline")) or ""
        m = re.match(r"^(\d+)x\s+(.+)$", h)
        table = box.select_one("table")
        if not m or table is None:
            continue
        wins = []
        for tr in P.table_rows(table):
            tds = P.cells(tr)
            wins.append({"season": P.season_obj(P.text(tds[0])),
                         "club": P.club_ref(tr) if tr.select_one("a[href*='/verein/']") else None})
        titles.append({"title": m.group(2).strip(), "count": int(m.group(1)), "wins": wins})
    return titles


def get_achievements(player):
    pid = _pid(player)
    doc = P.soup(get_page(_player_path(pid, "erfolge"), expect=f"/spieler/{pid}"))
    titles = parse_achievements(doc)
    return {"player_id": pid, "count": len(titles), "total_titles": sum(t["count"] or 0 for t in titles),
            "titles": titles}


def get_squad_numbers(player):
    pid = _pid(player)
    doc = P.soup(get_page(_player_path(pid, "rueckennummern"), expect=f"/spieler/{pid}"))
    out = {"player_id": pid, "clubs": [], "national_teams": []}
    for box in doc.select(".box"):
        h = P.text(box.select_one("h2, .content-box-headline")) or ""
        if not h.startswith("Squad number history"):
            continue
        key = "national_teams" if "national team" in h else "clubs"
        table = box.select_one("table.items")
        for tr in P.table_rows(table) if table is not None else []:
            tds = P.cells(tr)
            if len(tds) < 3:
                continue
            out[key].append({"season": P.season_obj(P.text(tds[0])),
                             "team": P.club_ref(tr),
                             "number": P.to_int(P.text(tds[-1]))})
    return out


def get_rumours(player):
    pid = _pid(player)
    doc = P.soup(get_page(_player_path(pid, "geruechte"), expect=f"/spieler/{pid}"))
    current = []
    box = P.box_by_headline(doc, "Rumours")
    table = box.select_one("table.items") if box is not None else None
    if table is not None:
        for row in P.items_table(table):
            current.append(_rumour_row(row))
    archive = []
    table = P.find_table(doc, "Rumour archive")
    for row in P.items_table(table) if table is not None else []:
        club_cell = row.get("verein_id") or row.get("col1") or row.get("Interested club")
        thread = row.get("Most recent source") or row.get("col2")
        a = thread.select_one("a[href]") if thread is not None else None
        archive.append({
            "interested_club": P.club_ref(row.get("Interested club")) or P.club_ref(club_cell),
            "last_source_date": P.iso_date(P.text(thread)),
            "last_reply_date": P.iso_date(P.text(row.get("Last reply"))),
            "probability_percent": P.percent(P.text(row.get("User assessment"))),
            "thread_link": P.link(a.get("href")) if a is not None else None,
        })
    return {"player_id": pid, "current": current, "archive": archive}


def _rumour_row(row):
    thread = row.get("Most recent source") or row.get("Last reply")
    a = thread.select_one("a[href]") if thread is not None else None
    return {"interested_club": P.club_ref(row.get("Interested club") or row.get("col1")),
            "last_source_date": P.iso_date(P.text(row.get("Most recent source"))),
            "last_reply_date": P.iso_date(P.text(row.get("Last reply"))),
            "probability_percent": P.percent(P.text(row.get("User assessment") or row.get("Assessment"))),
            "thread_link": P.link(a.get("href")) if a is not None else None}


def get_upcoming_matches(player, limit=10):
    pid = _pid(player)
    payload = get_ceapi(f"/ceapi/nextMatches/player/{pid}", {"limit": limit})
    teams = payload.get("teams") or {} if isinstance(payload, dict) else {}
    matches = []
    for m in (payload.get("matches") or []) if isinstance(payload, dict) else []:
        info = m.get("match") or {}
        comp = m.get("competition") or {}
        matches.append({
            "id": m.get("id"),
            "link": P.link(info.get("link")),
            "datetime_utc": _epoch_datetime(info.get("time")),
            "competition": {"id": comp.get("id"), "name": comp.get("label"), "link": P.link(comp.get("link"))},
            "matchday": P.to_int(info.get("day")),
            "group": info.get("group") or None,
            "home_club": _next_team(teams, info.get("home")),
            "away_club": _next_team(teams, info.get("away")),
            "result": None if info.get("result") in (None, "-:-") else info.get("result"),
            "status": (info.get("state") or "").lower() or None,
            "player_is_injured": bool(info.get("injury")),
            "player_is_suspended": bool(info.get("suspension")),
        })
    return {"player_id": pid, "count": len(matches), "matches": matches}


def _next_team(teams, tid):
    t = teams.get(str(tid)) or {}
    if not t and tid is None:
        return None
    return {"id": _int(tid), "name": t.get("name"), "link": P.link(t.get("link")),
            "crest": P.image(t.get("image"), "head") if t.get("image") and "/wappen/" in t.get("image") else P.image(t.get("image")),
            "is_national_team": bool(t.get("isNT"))}


def _epoch_datetime(ts):
    if not ts:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
