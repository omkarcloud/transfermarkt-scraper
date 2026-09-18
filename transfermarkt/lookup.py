"""Bulk id -> entity hydration over tmapi with a process-wide memo.

tmapi hands back ids in many places (club squads, a player's performance
log, a competition table, a match's lineups) and resolves them in bulk:
/clubs?ids[]=..., /competitions?ids[]=..., /players?ids[]=...,
/coaches?ids[]=... (250+ ids per call validated 2026-09-18). Names, crests
and countries hardly change, so each process remembers what it has seen
and only fetches the ids it is missing."""
import threading

from . import parsers as P
from . import refs
from .fetch import get_api, ids_query

_lock = threading.Lock()
_memo = {"clubs": {}, "competitions": {}, "players": {}, "coaches": {}}
CHUNK = 200


def _hydrate(kind, ids, path, normalize):
    ids = [str(i) for i in ids if i not in (None, "", 0, "0")]
    missing = [i for i in dict.fromkeys(ids) if i not in _memo[kind]]
    for start in range(0, len(missing), CHUNK):
        chunk = missing[start:start + CHUNK]
        rows = get_api(path, ids_query(chunk)) or []
        with _lock:
            for row in rows:
                if isinstance(row, dict) and row.get("id") is not None:
                    _memo[kind][str(row["id"])] = normalize(row)
            for i in chunk:
                _memo[kind].setdefault(i, None)
    return {i: _memo[kind].get(i) for i in ids}


def clubs(ids):
    """{id: club ref} for tmapi club ids (None for unknown ids)."""
    return _hydrate("clubs", ids, "/clubs", P.api_club_ref)


def club(club_id):
    return clubs([club_id]).get(str(club_id))


def competitions(ids):
    return _hydrate("competitions", ids, "/competitions", _competition_row)


def competition(cid):
    return competitions([cid]).get(str(cid))


def _competition_row(c):
    ref = P.api_competition_ref(c) or {}
    origin = c.get("originDetails") or {}
    base = c.get("baseDetails") or {}
    ref.update({
        "country": refs.country(origin.get("countryId")),
        "confederation": refs.confederation(origin.get("confederationId")),
        "tier": (refs.lookup("competition_types", c.get("typeId")) or {}).get("name"),
        "is_tournament": bool(base.get("isTournament")),
    })
    return ref


def players(ids):
    return _hydrate("players", ids, "/players", _player_row)


def _player_row(p):
    ref = P.api_player_ref(p) or {}
    life = p.get("lifeDates") or {}
    attrs = p.get("attributes") or {}
    ref.update({
        "date_of_birth": life.get("dateOfBirth"),
        "age": life.get("age"),
        "height_m": attrs.get("height") or None,
        "preferred_foot": (attrs.get("preferredFoot") or {}).get("name"),
        "contract_until": attrs.get("contractUntil") or None,
        "market_value": P.api_money((p.get("marketValueDetails") or {}).get("current")),
    })
    return ref


def coaches(ids):
    return _hydrate("coaches", ids, "/coaches", _coach_row)


def coach(cid):
    return coaches([cid]).get(str(cid))


def _coach_row(c):
    life = c.get("lifeDates") or {}
    attrs = c.get("attributes") or {}
    return {"id": int(c["id"]), "name": c.get("name"),
            "link": P.link(c.get("relativeUrl")) or P.entity_link("manager", c["id"]),
            "image": c.get("portraitUrl") or None,
            "nationalities": P.api_nationalities((c.get("nationalityDetails") or {}).get("nationalities")),
            "date_of_birth": life.get("dateOfBirth"), "age": life.get("age"),
            "role": (refs.lookup("roles", attrs.get("personnelRoleId")) or {}).get("name"),
            "licence": (attrs.get("license") or {}).get("name")}
