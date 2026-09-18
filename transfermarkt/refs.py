"""Transfermarkt reference parsing: ONE param per input that auto-detects
its forms (tripadvisor QueryOrIdField convention — never a sibling
`url`/`id` pair). Every entity ref accepts a bare id OR a pasted
transfermarkt.* URL of any language domain (the slug is ignored by the site,
only the id segment matters):

  player       418560   | https://www.transfermarkt.com/erling-haaland/profil/spieler/418560
  club         281      | https://www.transfermarkt.co.uk/manchester-city/kader/verein/281/saison_id/2025
  competition  GB1      | https://www.transfermarkt.com/premier-league/tabelle/wettbewerb/GB1
               CL       | https://www.transfermarkt.com/uefa-champions-league/startseite/pokalwettbewerb/CL
  manager      5672     | https://www.transfermarkt.com/pep-guardiola/profil/trainer/5672
  match        4361261  | https://www.transfermarkt.com/spielbericht/index/spielbericht/4361261
                        | https://www.transfermarkt.com/manchester-united_fulham-fc/index/spielbericht/4361261
  season       2025 | 25/26 | 2025/26 | 2025-26  -> 2025 (the season's START year)

Also holds the option tables the list endpoints share (position groups,
age groups, transfer windows, regions) and the lookup helpers over the
vendored tmapi attribute tables (countries, positions, confederations).
"""
import json
import os
import re
from urllib.parse import urlparse

_RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")

_NUM_RE = re.compile(r"^\d{1,10}$")
_COMP_RE = re.compile(r"^[A-Z0-9]{2,8}$", re.I)
_SEASON_RE = re.compile(r"^(\d{4})(?:[/-](\d{2}|\d{4}))?$")
_SHORT_SEASON_RE = re.compile(r"^(\d{2})/(\d{2})$")

_SEGMENTS = {
    "player": ("spieler",),
    "club": ("verein",),
    "competition": ("wettbewerb", "pokalwettbewerb"),
    "manager": ("trainer",),
    "match": ("spielbericht",),
    "referee": ("schiedsrichter",),
}


def _is_transfermarkt_url(value):
    if not value.startswith(("http://", "https://", "//", "www.")):
        return False
    url = value if "://" in value else "https://" + value.lstrip("/")
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return "transfermarkt" in host.split(".")


def _id_from_url(value, kind):
    url = value if "://" in value else "https://" + value.lstrip("/")
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    for seg in _SEGMENTS[kind]:
        if seg in parts:
            idx = parts.index(seg)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    raise ValueError(f"could not find a {kind} id in that transfermarkt.com link "
                     f"(expected a /{'|'.join(_SEGMENTS[kind])}/<id> segment)")


def _resolve_numeric(value, kind, label):
    if isinstance(value, int) and not isinstance(value, bool):
        return value            # already resolved by the schema
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{label} is required")
    if _NUM_RE.match(value):
        return int(value)
    if _is_transfermarkt_url(value):
        candidate = _id_from_url(value, kind)
        if _NUM_RE.match(candidate):
            return int(candidate)
        raise ValueError(f"{label} link carries a non-numeric id: {candidate}")
    raise ValueError(f"{label} must be a numeric Transfermarkt id or a transfermarkt.com {kind} link")


def resolve_player(value):
    return _resolve_numeric(value, "player", "player")


def resolve_club(value):
    return _resolve_numeric(value, "club", "club")


def resolve_manager(value):
    return _resolve_numeric(value, "manager", "manager")


def resolve_match(value):
    return _resolve_numeric(value, "match", "match")


def resolve_competition(value):
    """Competition ids are short upper-case codes (GB1, ES1, CL, FAC)."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("competition is required")
    if _is_transfermarkt_url(value):
        value = _id_from_url(value, "competition")
    if _COMP_RE.match(value):
        return value.upper()
    raise ValueError("competition must be a Transfermarkt competition code (e.g. GB1, ES1, CL) "
                     "or a transfermarkt.com competition link")


def resolve_season(value):
    """'2025' | '25/26' | '2025/26' | '2025/2026' | '2025-26' -> 2025 (start year)."""
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    value = str(value).strip()
    m = _SEASON_RE.match(value)
    if m:
        year = int(m.group(1))
    else:
        m = _SHORT_SEASON_RE.match(value)
        if not m:
            raise ValueError("season must be the start year (2025) or a season like 25/26 or 2025/26")
        year = 2000 + int(m.group(1)) if int(m.group(1)) < 70 else 1900 + int(m.group(1))
    if not 1880 <= year <= 2100:
        raise ValueError("season must be a plausible start year (e.g. 2025)")
    return year


def season_name(start_year):
    """2025 -> '25/26'."""
    if start_year is None:
        return None
    return f"{start_year % 100:02d}/{(start_year + 1) % 100:02d}"


# ---- option tables ---------------------------------------------------------------

POSITION_GROUPS = {          # public value -> site token ("ausrichtung" / "position")
    "goalkeeper": "Torwart",
    "defender": "Abwehr",
    "midfielder": "Mittelfeld",
    "forward": "Sturm",
}
AGE_GROUPS = ["u17", "u18", "u19", "u20", "u21", "u23", "23-30", "o30", "o32", "o34"]
TRANSFER_WINDOWS = {"summer": "s", "winter": "w"}
REGIONS = {                  # /wettbewerbe/<slug>
    "europe": "europa",
    "asia": "asien",
    "america": "amerika",
    "africa": "afrika",
}
NATIONAL_TEAM_STATUS = {     # advanced search Detailsuche[nm_status][]
    "current": "j",
    "former": "n",
    "retired": "z",
    "never": "k",
}
LEAGUE_TIERS = {             # advanced search Detailsuche[art_neu][]
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
    "youth": "7", "reserve": "18",
}
FEET = {"left": "1", "right": "2", "both": "3"}
SEARCH_TYPES = ["all", "players", "clubs", "competitions", "managers", "agents"]
STANDINGS_TYPES = ["total"]


# ---- vendored tmapi attribute tables ------------------------------------------------

_attributes = None


def attributes():
    """The trimmed tmapi /attributes tables (resources/attributes.json):
    countries, confederations, positions, competition_types, tactics,
    roles, outfitters, injuries, absences, contracts, actions, reasons,
    competition_groups. Loaded once per process."""
    global _attributes
    if _attributes is None:
        with open(os.path.join(_RESOURCES, "attributes.json"), encoding="utf-8") as f:
            _attributes = json.load(f)
        for key in list(_attributes):
            _attributes["_by_id_" + key] = {str(row["id"]): row for row in _attributes[key]}
    return _attributes


def lookup(table, value):
    """Row of `table` with that id (str/int), or None."""
    if value in (None, "", 0, "0"):
        return None
    return attributes().get("_by_id_" + table, {}).get(str(value))


def country(country_id):
    """{id, name, code} for a tmapi country id (None for 0/unknown)."""
    row = lookup("countries", country_id)
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "code": row.get("code")}


def confederation(conf_id):
    row = lookup("confederations", conf_id)
    return {"id": row["id"], "name": row["name"]} if row else None


def position(position_id):
    row = lookup("positions", position_id)
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "short_name": row.get("short_name"),
            "group": row.get("group")}


def resolve_country_id(value):
    """Country as a tmapi id, a FIFA code (ENG, BRA) or a name (case-insensitive) -> id."""
    value = (value or "").strip()
    if not value:
        return None
    if _NUM_RE.match(value):
        if not lookup("countries", value):
            raise ValueError(f"unknown country id {value}")
        return int(value)
    low = value.lower()
    for row in attributes()["countries"]:
        if (row.get("code") or "").lower() == low or row["name"].lower() == low:
            return row["id"]
    raise ValueError(f"unknown country '{value}' — use a name (England), a FIFA code (ENG) "
                     "or a Transfermarkt country id (see /transfermarkt/helpers/countries)")


def resolve_position_id(value):
    """Position as an id (1-14) or a name/short name (Centre-Back, CB) -> id."""
    value = (value or "").strip()
    if not value:
        return None
    if _NUM_RE.match(value):
        if not lookup("positions", value):
            raise ValueError(f"unknown position id {value}")
        return int(value)
    low = value.lower().replace("_", " ").replace("-", " ")
    for row in attributes()["positions"]:
        if low in (row["name"].lower().replace("-", " "), (row.get("short_name") or "").lower()):
            return row["id"]
    raise ValueError(f"unknown position '{value}' — use a name (Centre-Back), a short code (CB) "
                     "or an id 1-14 (see /transfermarkt/helpers/positions)")
