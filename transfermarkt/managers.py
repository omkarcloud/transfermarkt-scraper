"""Manager / coach endpoints. `manager` = numeric id or a transfermarkt.com
coach link (/trainer/{id}). tmapi /coach/{id} carries the base card; the
HTML profile adds the current post, coaching facts and the club history
table (stationen/plus/1 = the detailed view with W/D/L, players used,
points per match).
"""
import re

from . import parsers as P
from . import refs
from .fetch import get_api, get_page, run_parallel
from .players import parse_achievements


def _mid(manager):
    return refs.resolve_manager(manager)


def _path(mid, tab, suffix=""):
    return f"/-/{tab}/trainer/{mid}{suffix}"


def _career_row(row):
    club_cell = row.get("Club & role") or row.get("col1")
    club = P.club_ref(club_cell)
    inline = club_cell.select_one("table.inline-table") if club_cell is not None else None
    role = None
    if inline is not None and len(inline.select("tr")) > 1:
        role = P.text(inline.select("tr")[1])
    elif club_cell is not None and club:
        role = P.clean((P.text(club_cell) or "").replace(club["name"] or "", "", 1))
    appointed = P.text(row.get("Appointed"))
    until = P.text(row.get("In charge until"))
    return {
        "club": club,
        "role": role,
        "appointed": P.iso_date(appointed),
        "appointed_season": P.season_obj(appointed),
        "in_charge_until": P.iso_date(until),
        "in_charge_until_season": P.season_obj(until),
        "days_in_charge": P.to_int(P.text(row.get("Days in charge"))),
        "matches": P.to_int(P.text(row.get("Matches"))),
        "wins": P.to_int(P.text(row.get("W"))), "draws": P.to_int(P.text(row.get("D"))),
        "losses": P.to_int(P.text(row.get("L"))),
        "players_used": P.to_int(P.text(row.get("Players used"))),
        "average_goals": P.to_float(P.text(row.get("ø-Goals"))),
        "points_per_match": P.to_float(P.text(row.get("PPM"))),
    }


def get_profile(manager):
    mid = _mid(manager)
    api = get_api(f"/coach/{mid}")            # cheap and 404s fast for unknown ids
    html = get_page(_path(mid, "profil"), expect=f"/trainer/{mid}")
    doc = P.soup(html)
    hero = P.data_header(doc)
    life = api.get("lifeDates") or {}
    birth = api.get("birthPlaceDetails") or {}
    attrs = api.get("attributes") or {}
    nat = api.get("nationalityDetails") or {}
    details = {}
    box = P.box_by_headline(doc, "Personal Details")
    if box is not None:
        for tr in box.select("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) >= 2:
                details[(P.text(tds[0]) or "").rstrip(":")] = tds[1]

    def hv(label):
        el = hero.get(label) or details.get(label)
        return P.text(el) if el is not None else None

    club_info = doc.select_one(".data-header__club-info")
    info_text = P.text(club_info) or ""
    club_a = club_info.select_one("a[href*='/verein/']") if club_info is not None else None
    club_ref = P.club_ref(club_a) if club_a is not None else None
    is_last = "Last position" in info_text
    current_club = None if is_last or (club_ref or {}).get("id") in (515, 123) else club_ref
    role_m = re.search(r"(?:Last position|Position):\s*(.+?)(?:\s+(?:Manchester|[A-Z]).*)?$", info_text)
    values = [P.text(v) for v in club_info.select(".dataValue")] if club_info is not None else []
    plain_labels = [P.text(l) for l in club_info.select(".data-header__label")
                    if ":" not in (P.text(l) or "")] if club_info is not None else []
    role = values[0] if values else plain_labels[0] if plain_labels else (role_m.group(1) if role_m else None)
    dates = {k: P.iso_date(m.group(1)) for k, m in (
        (key, re.search(rf"{label}:\s*([\d/.]+)", info_text)) for key, label in (
            ("date_left", "Date left"), ("appointed", "Appointed"), ("contract_until", "Contract until"),
            ("contract_until", "Contract expires")))
        if m}
    league_a = club_info.select_one(".data-header__league a[href*='wettbewerb/']") if club_info is not None else None
    former_player = doc.select_one(".data-header a[href*='/spieler/']")
    agent_el = details.get("Agent")
    agent_a = agent_el.select_one("a[href]") if agent_el is not None else None
    history = P.find_table(doc, "History")
    career = [_career_row(r) for r in P.items_table(history)] if history is not None else []
    return {
        "id": int(api["id"]),
        "name": api.get("name"),
        "full_name": nat.get("passportName") or None,
        "link": P.link(api.get("relativeUrl")) or P.entity_link("manager", mid),
        "image": api.get("portraitUrl") or None,
        "date_of_birth": life.get("dateOfBirth"),
        "age": life.get("age"),
        "date_of_death": life.get("dateOfDeath"),
        "place_of_birth": birth.get("placeOfBirth") or None,
        "country_of_birth": refs.country(birth.get("countryOfBirthId")),
        "nationalities": P.api_nationalities(nat.get("nationalities")),
        "role": (refs.lookup("roles", attrs.get("personnelRoleId")) or {}).get("name") or role,
        "current_club": current_club,
        "current_role": role if current_club else None,
        "current_competition": P.competition_ref(league_a) if league_a is not None and current_club else None,
        "appointed": dates.get("appointed") if current_club else None,
        "contract_until": dates.get("contract_until") if current_club else None,
        "is_available": bool(attrs.get("isAvailable")) or (is_last and current_club is None),
        "last_club": club_ref if is_last else None,
        "last_role": role if is_last else None,
        "date_left": dates.get("date_left"),
        "coaching_licence": (attrs.get("license") or {}).get("name") or hv("Coaching Licence"),
        "average_term_years": P.to_float(hv("Avg. term as coach")),
        "preferred_formation": hv("Preferred formation"),
        "agent": {"name": P.text(agent_el), "link": P.link(agent_a.get("href")) if agent_a is not None else None} if agent_el is not None and P.text(agent_el) else None,
        "former_player": {"id": P.id_in(former_player.get("href"), "player"), "link": P.link(former_player.get("href")),
                          "last_club": hv("Last club"), "most_games_for": hv("Most games for"),
                          "retired": P.iso_date(hv("Retired"))} if former_player is not None else None,
        "career": career,
        "image_source": api.get("portraitUrlSource") or None,
    }


def get_career(manager):
    mid = _mid(manager)
    doc = P.soup(get_page(_path(mid, "stationen", "/plus/1"), expect=f"/trainer/{mid}"))
    table = P.find_table(doc, "History") or doc.select_one("table.items")
    rows = [_career_row(r) for r in P.items_table(table)] if table is not None else []
    totals = {"matches": sum(r["matches"] or 0 for r in rows), "wins": sum(r["wins"] or 0 for r in rows),
              "draws": sum(r["draws"] or 0 for r in rows), "losses": sum(r["losses"] or 0 for r in rows)}
    return {"manager_id": mid, "count": len(rows), "totals": totals, "stations": rows}


def get_achievements(manager):
    mid = _mid(manager)
    doc = P.soup(get_page(_path(mid, "erfolge"), expect=f"/trainer/{mid}"))
    titles = parse_achievements(doc)
    return {"manager_id": mid, "count": len(titles), "total_titles": sum(t["count"] or 0 for t in titles),
            "titles": titles}
