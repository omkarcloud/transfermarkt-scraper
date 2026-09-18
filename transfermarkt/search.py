"""Search endpoints.

  search            the site's quick search (/schnellsuche) — one HTML page
                    that lists players, clubs, competitions, managers &
                    officials and agents, each section with its own pager
                    (Spieler_page / Verein_page / Wettbewerb_page /
                    Trainer_page / Berater_page)
  search-advanced   the advanced player search (/detailsuche) — a POST of the
                    FULL form (resources/advanced_search_form.json is the
                    site's own default body; a partial body yields 0 hits),
                    paged by POSTing the same body to ?page=N
"""
import json
import os
import re

from . import parsers as P
from . import refs
from .fetch import get_page, post_page

_RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
QUICK_PAGE_SIZE = 10
ADVANCED_PAGE_SIZE = 25

_SECTION = {   # public type -> (headline needle, site page param)
    "players": ("for players", "Spieler_page"),
    "clubs": ("Clubs", "Verein_page"),
    "competitions": ("competitions", "Wettbewerb_page"),
    "managers": ("Managers", "Trainer_page"),
    "agents": ("for agents", "Berater_page"),
}


def _section_tables(doc):
    out = {}
    for box in doc.select(".box"):
        h = box.select_one("h2.content-box-headline")
        table = box.select_one("table.items")
        if h is None or table is None:
            continue
        title = P.text(h) or ""
        for kind, (needle, _) in _SECTION.items():
            if needle.lower() in title.lower():
                m = re.search(r"(\d[\d.,]*)\s*Hits", title)
                out[kind] = (table, P.to_int(m.group(1)) if m else None)
    return out


def _player_hit(row):
    player = P.player_ref(row.get("Name/Position") or row.get("col0"))
    if not player:
        return None
    club_cell = row.get("Club")
    agent_cell = row.get("Agents")
    agent_a = agent_cell.select_one("a[href]") if agent_cell is not None else None
    player.pop("position", None)   # the inline second line is the club here, not the position
    return {
        "player": player,
        "position": P.text(row.get("Position")),
        "club": P.club_ref(club_cell) if club_cell is not None else None,
        "age": P.to_int(P.text(row.get("Age"))),
        "nationalities": P.flags(row.get("Nat.")),
        "market_value": P.money_obj(P.text(row.get("Market Value"))),
        "agent": {"name": P.text(agent_cell), "link": P.link(agent_a.get("href")) if agent_a is not None else None,
                  "is_verified": bool(agent_cell.select_one("img[title='verified']"))} if agent_cell is not None and P.text(agent_cell) else None,
    }


def _club_hit(row):
    club = P.club_ref(row.get("Club"))
    if not club:
        return None
    club["crest"] = club.get("crest") or P.crest((row.get("col0") or row.get("Club")).select_one("img"))
    comp = (row.get("Club") or {}).select_one("a[href*='wettbewerb/']") if row.get("Club") is not None else None
    return {
        "club": club,
        "competition": P.competition_ref(comp) if comp is not None else None,
        "country": P.flag_country(row.get("Country").select_one("img")) if row.get("Country") is not None else None,
        "squad_size": P.to_int(P.text(row.get("Squad"))),
        "total_market_value": P.money_obj(P.text(row.get("Total Market Value"))),
    }


def _competition_hit(row):
    comp = P.competition_ref(row.get("Competition"))
    if not comp:
        return None
    comp["logo"] = comp.get("logo") or P.logo((row.get("col0") or row.get("Competition")).select_one("img"))
    return {
        "competition": comp,
        "country": P.flag_country(row.get("Country").select_one("img")) if row.get("Country") is not None else None,
        "clubs_count": P.to_int(P.text(row.get("Clubs"))),
        "players_count": P.to_int(P.text(row.get("Players"))),
        "total_market_value": P.money_obj(P.text(row.get("Total Market Value"))),
        "average_market_value": P.money_obj(P.text(row.get("Mean market value"))),
        "confederation": P.text(row.get("Continent")),
    }


def _manager_hit(row):
    person = P.manager_ref(row.get("Name") or row.get("col0"))
    if not person:
        return None
    club_cell = row.get("Club")
    return {
        "manager": person,
        "role": P.text(row.get("Function")),
        "club": P.club_ref(club_cell) if club_cell is not None else None,
        "age": P.to_int(P.text(row.get("Age"))),
        "nationalities": P.flags(row.get("Nat.")),
        "contract_until": P.iso_date(P.text(row.get("Contract until"))),
    }


def _agent_hit(row):
    cell = row.get("Company")
    a = cell.select_one("a[href*='/berater/']") if cell is not None else None
    if a is None:
        return None
    aid = P.id_in(a.get("href"), "agent")
    img = cell.select_one("img")
    premium = row.get("Premium Service") or row.get("col0")
    return {
        "agency": {"id": aid, "name": P.clean(a.get("title")) or P.text(a), "link": P.link(a.get("href")),
                   "logo": P.image(img.get("src")) if img is not None else None},
        "is_licensed": "licensed" in (P.text(row.get("Licence")) or "").lower(),
        "has_premium_service": bool(premium is not None and premium.select_one("span[title]") is not None
                                    and "inactive" not in (premium.select_one("span[title]").get("title") or "")),
        "agents": P.text(row.get("Agents")),
    }


_HIT = {"players": _player_hit, "clubs": _club_hit, "competitions": _competition_hit,
        "managers": _manager_hit, "agents": _agent_hit}


def search(query, type="all", page=1):
    params = {"query": query}
    if page and page > 1 and type != "all":
        params[_SECTION[type][1]] = page
    doc = P.soup(get_page("/schnellsuche/ergebnis/schnellsuche", params))
    sections = _section_tables(doc)
    out = {"query": query, "type": type}
    kinds = list(_SECTION) if type == "all" else [type]
    if type == "all":
        totals = [sections[k][1] for k in kinds if k in sections and sections[k][1] is not None]
        out["pagination"] = P.pagination(1, QUICK_PAGE_SIZE, sum(totals) if totals else None, total_pages_=1)
    for kind in kinds:
        table, total = sections.get(kind, (None, None))
        rows = []
        for row in P.items_table(table) if table is not None else []:
            hit = _HIT[kind](row)
            if hit:
                rows.append(hit)
        if type == "all":
            out[kind] = {"total_count": total, "results": rows}
        else:
            out["pagination"] = P.pagination(page, QUICK_PAGE_SIZE, total,
                                             total_pages_=P.total_pages(doc, _SECTION[type][1]) if total else 1)
            out["results"] = rows
    return out


# ---- advanced player search ---------------------------------------------------------------

_form_template = None


def _form():
    global _form_template
    if _form_template is None:
        with open(os.path.join(_RESOURCES, "advanced_search_form.json"), encoding="utf-8") as f:
            _form_template = json.load(f)
    return dict(_form_template)


def _advanced_row(row):
    player = P.player_ref(row.get("Player"))
    if not player:
        return None
    nt = next((row[k] for k in row if k.lower().startswith("national player")), None)
    nt_club = P.club_ref(nt) if nt is not None else None
    if nt_club and nt is not None:
        nt_club["status"] = P.text(nt.select_one("tr:nth-of-type(2)")) if nt.select_one("tr:nth-of-type(2)") else None
        nt_club["name"] = (nt_club["name"] or "").split(" ")[0] if nt_club["name"] and "national" in nt_club["name"].lower() else nt_club["name"]
    caps_key = next((k for k in row if k.lower().startswith("international matc")), None)
    return {
        "player": player, "position": player.pop("position", None),
        "shirt_number": P.to_int(P.text(row.get("#"))),
        "date_of_birth": P.date_and_age(P.text(next((row[k] for k in row if k.startswith("Date of birth")), None)))[0],
        "age": P.date_and_age(P.text(next((row[k] for k in row if k.startswith("Date of birth")), None)))[1],
        "nationalities": P.flags(row.get("Nat.")),
        "club": P.club_ref(row.get("Club")),
        "height_m": P.height_m(P.text(row.get("Height"))),
        "national_team": nt_club,
        "caps": P.to_int(P.text(row.get(caps_key))) if caps_key else None,
        "market_value": P.money_obj(P.text(row.get("Market value"))),
    }


def search_advanced(page=1, **filters):
    """filters are the schema's kwargs (see schemas.AdvancedSearchSchema)."""
    form = _form()
    f = filters
    set_ = form.__setitem__
    if f.get("name"):
        set_("Detailsuche[name]", f["name"])
    if f.get("first_name"):
        set_("Detailsuche[vorname]", f["first_name"])
    if f.get("exact_name"):
        set_("Detailsuche[genaue_suche]", "1")
    if f.get("place_of_birth"):
        set_("Detailsuche[geb_ort]", f["place_of_birth"])
    if f.get("nationality"):
        set_("Detailsuche[land_id]", str(f["nationality"]))
    if f.get("second_nationality"):
        set_("Detailsuche[zweites_land_id]", str(f["second_nationality"]))
    if f.get("country_of_birth"):
        set_("Detailsuche[geb_land_id]", str(f["country_of_birth"]))
    if f.get("continent"):
        set_("Detailsuche[kontinent_id]", str(f["continent"]))
    if f.get("birth_year"):
        set_("Detailsuche[geburtsjahr]", str(f["birth_year"]))
    min_age, max_age = f.get("min_age") or 0, f.get("max_age") or 150
    set_("Detailsuche[minAlter]", str(min_age)); set_("Detailsuche[maxAlter]", str(max_age))
    set_("Detailsuche[age]", f"{min_age};{max_age}")
    min_year, max_year = f.get("min_birth_year") or 1850, f.get("max_birth_year") or 2015
    set_("Detailsuche[minJahrgang]", str(min_year)); set_("Detailsuche[maxJahrgang]", str(max_year))
    set_("Detailsuche[jahrgang]", f"{min_year};{max_year}")
    min_h = int(round((f.get("min_height_m") or 0) * 100)); max_h = int(round((f.get("max_height_m") or 2.2) * 100))
    set_("Detailsuche[minGroesse]", str(min_h)); set_("Detailsuche[maxGroesse]", str(max_h))
    set_("Detailsuche[groesse]", f"{min_h};{max_h}")
    min_mv = int(f.get("min_market_value") or 0); max_mv = int(f.get("max_market_value") or 200000000)
    set_("Detailsuche[minMarktwert]", str(min_mv)); set_("Detailsuche[maxMarktwert]", str(max_mv))
    set_("Detailsuche[marktwert]", f"{min_mv};{max_mv}")
    if f.get("position_id"):
        set_("Detailsuche[hauptposition_id]", str(f["position_id"]))
    if f.get("side_position_id"):
        set_("Detailsuche[nebenposition_id_1]", str(f["side_position_id"]))
    if f.get("foot"):
        set_("Detailsuche[fuss_id]", refs.FEET[f["foot"]])
    if f.get("is_captain") is True:
        set_("Detailsuche[captain]", "1")
    if f.get("shirt_number"):
        set_("Detailsuche[rn]", str(f["shirt_number"]))
    if f.get("competition"):
        set_("Detailsuche[wettbewerb_id]", f["competition"])
    if f.get("club_country"):
        set_("Detailsuche[w_land_id]", str(f["club_country"]))
    min_caps, max_caps = f.get("min_caps") or 0, f.get("max_caps") or 300
    set_("Detailsuche[minNmSpiele]", str(min_caps)); set_("Detailsuche[maxNmSpiele]", str(max_caps))
    set_("Detailsuche[nm_spiele]", f"{min_caps};{max_caps}")
    set_("Detailsuche[trans_id]", "1" if f.get("has_transfer_this_season") else "0")
    set_("Detailsuche[aktiv]", "1" if f.get("is_active") else "0")
    set_("Detailsuche[vereinslos]", "1" if f.get("is_free_agent") else "0")
    set_("Detailsuche[leihen]", "1" if f.get("is_on_loan") else "0")
    body = list(form.items())
    for value in f.get("position_groups") or []:   # multi-select; "Exclude position" is only its placeholder
        body.append(("Detailsuche[position][]", refs.POSITION_GROUPS[value]))
    for value in f.get("contract_expires") or []:
        body.append(("Detailsuche[vertrag][]", str(value)))
    for value in f.get("league_tiers") or []:
        body.append(("Detailsuche[art_neu][]", refs.LEAGUE_TIERS[value]))
    for value in f.get("national_team_status") or []:
        body.append(("Detailsuche[nm_status][]", refs.NATIONAL_TEAM_STATUS[value]))
    params = {"page": page} if page and page > 1 else None
    doc = P.soup(post_page("/detailsuche/spielerdetail/suche", body, params))
    total = P.hits(doc, "Search results for players")   # the site caps the count (and paging) at 250
    table = P.find_table(doc, "Search results for players")
    rows = []
    for row in P.items_table(table) if table is not None else []:
        hit = _advanced_row(row)
        if hit:
            rows.append(hit)
    return {"filters": {k: v for k, v in f.items() if v not in (None, [], False)},
            "pagination": P.pagination(page, ADVANCED_PAGE_SIZE, total),
            "players": rows}
