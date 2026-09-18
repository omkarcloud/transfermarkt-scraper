"""Match endpoints. `match` = numeric match id or a transfermarkt.com match
report link. One tmapi /game/{id} payload carries the whole report — clubs,
coaches, formations, line-ups with market values, goals / cards /
substitutions with the players involved, and both clubs' statistics
(possession, shots, passes, tackles, corners...) — so details / lineups /
statistics are three views of one cached fetch. Club, coach, line-up
player, referee and stadium names are hydrated with tmapi bulk lookups.
"""
from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_api, get_api_optional, run_parallel


def _mid(match):
    return refs.resolve_match(match)


def _game(mid):
    return get_api(f"/game/{mid}")


def _action(a, side):
    return {
        "minute": a.get("minute"),
        "added_time": a.get("addedTime") or 0,
        "team": side,
        "player": P.api_player_ref(a.get("activePlayer")) or ({"id": P._int(a.get("activePlayerId"))} if a.get("activePlayerId") else None),
        "detail": a.get("action") or None,
        "reason": a.get("reason") or None,
    }


def _goal(a, side):
    out = _action(a, side)
    out["assist_by"] = P.api_player_ref(a.get("passivePlayer"))
    out["type"] = "own_goal" if "own goal" in (a.get("action") or "").lower() else \
        "penalty" if "penalty" in (a.get("action") or "").lower() else "goal"
    return out


def _card(a, side):
    out = _action(a, side)
    action = (a.get("action") or "").lower()
    out["card"] = "red" if "red" in action and "yellow" not in action else \
        "second_yellow" if "second" in action or "yellow-red" in action else "yellow"
    return out


def _substitution(a, side):
    out = _action(a, side)
    out["player_off"] = out.pop("player")
    out["player_on"] = P.api_player_ref(a.get("passivePlayer"))
    return out


def _lineup_player(p, names):
    ref = dict(names.get(str(p.get("id"))) or {"id": P._int(p.get("id")), "name": None,
                                              "link": P.entity_link("player", p.get("id"))})
    return {
        "player": {k: ref.get(k) for k in ("id", "name", "link", "image", "nationalities")},
        "shirt_number": p.get("shirtNumber"),
        "position": refs.position(p.get("positionId")) or ref.get("position"),
        "age": p.get("ageAtGameDate"),
        "is_captain": bool(p.get("isCaptain")),
        "market_value": P.api_money(p.get("marketValue")),
    }


def _club_stats(cs):
    if not isinstance(cs, dict) or not cs.get("clubId"):
        return None
    passing, goals, defense, set_pieces, game, penalties = (cs.get(k) or {} for k in (
        "passingStatistics", "goalStatistics", "defensiveStatistics", "setPieceStatistics",
        "gameStatistics", "penaltyStatistics"))
    return {
        "possession_percent": game.get("possessionPercentage"),
        "formation": game.get("formation"),
        "goals": goals.get("goals"), "assists": goals.get("assists"),
        "shots": goals.get("totalShotAttempts"), "shots_on_target": goals.get("onTargetShotAttempts"),
        "shots_off_target": goals.get("offTargetShotAttempts"), "shots_blocked": goals.get("blockedShotAttempts"),
        "passes": passing.get("totalPasses"), "passes_completed": passing.get("accuratePasses"),
        "tackles": defense.get("totalTackles"), "tackles_won": defense.get("successfulTackles"),
        "clearances": defense.get("clearances"), "saves": defense.get("saves"),
        "offsides": defense.get("offsides"), "clean_sheets": defense.get("cleanSheets"),
        "corners": set_pieces.get("cornersTaken"), "corners_conceded": set_pieces.get("cornersConceded"),
        "goal_kicks": set_pieces.get("goalKicks"), "throw_ins": set_pieces.get("throwIns"),
        "fouls_committed": penalties.get("freeKicksConcededFromFouls"),
        "fouls_suffered": penalties.get("freeKicksWonFromFouls"),
        "penalties_won": penalties.get("penaltiesWon"), "penalties_conceded": penalties.get("penaltiesConceded"),
        "yellow_cards": game.get("yellowCards"), "second_yellow_cards": game.get("secondYellowCards"),
        "red_cards": game.get("redCards"), "substitutions": game.get("substitutionsMade"),
    }


def _side(raw, side, clubs, coaches, names):
    lineup = raw.get("lineup") or {}
    actions = raw.get("actions") or {}
    mv = lineup.get("marketValues") or {}
    goals = [_goal(a, side) for a in actions.get("goals") or []]
    return {
        "club": clubs.get(str(raw.get("clubId"))) or {"id": P._int(raw.get("clubId")), "name": None},
        "coach": coaches.get(str(raw.get("coachId"))) or ({"id": P._int(raw.get("coachId"))} if raw.get("coachId") else None),
        "formation": (raw.get("tactic") or {}).get("tactic") or None,
        "goals": len(goals),
        "starting_lineup": [_lineup_player(p, names) for p in lineup.get("players") or []],
        "substitutes": [_lineup_player(p, names) for p in lineup.get("substitutes") or []],
        "lineup_market_value": {"starters": P.api_money(mv.get("players")), "bench": P.api_money(mv.get("substitutes")),
                                "total": P.api_money(mv.get("total"))},
        "squad_market_value": P.api_money(raw.get("squadMarketValue")),
        "goal_events": goals,
        "cards": [_card(a, side) for a in actions.get("cards") or []],
        "substitutions": [_substitution(a, side) for a in actions.get("substitutes") or []],
        "statistics": _club_stats(raw.get("clubStatistics")),
    }


def _hydrate(game):
    home, away = game.get("homeClub") or {}, game.get("awayClub") or {}
    base = game.get("baseDetails") or {}
    player_ids = set()
    for side in (home, away):
        for key in ("players", "substitutes"):
            for p in (side.get("lineup") or {}).get(key) or []:
                player_ids.add(p.get("id"))
    coach_ids = [c for c in (home.get("coachId"), away.get("coachId")) if c]
    clubs, coaches, names, referee, stadium = run_parallel([
        lambda: lookup.clubs([home.get("clubId"), away.get("clubId")]),
        lambda: lookup.coaches(coach_ids) if coach_ids else {},
        lambda: lookup.players([p for p in player_ids if p]),
        lambda: get_api_optional(f"/referee/{game.get('refereeId') or base.get('refereeId')}") if (game.get("refereeId") or base.get("refereeId")) else None,
        lambda: get_api_optional(f"/stadium/{base.get('stadiumId')}") if base.get("stadiumId") else None,
    ])
    return clubs, coaches, names, referee, stadium


def _referee(r):
    if not r:
        return None
    return {"id": P._int(r.get("id")), "name": r.get("name"),
            "link": P.link(r.get("relativeUrl")) or P.entity_link("referee", r.get("id")),
            "nationalities": P.api_nationalities(r.get("nationalities")), "date_of_birth": r.get("dateOfBirth")}


def _stadium(s):
    if not s:
        return None
    loc = s.get("location") or {}
    return {"id": P._int(s.get("id")), "name": s.get("name"), "city": loc.get("city") or None,
            "country": refs.country(loc.get("countryId")), "capacity": (s.get("capacity") or {}).get("crowd")}


def get_details(match):
    mid = _mid(match)
    game = _game(mid)
    clubs, coaches, names, referee, stadium = _hydrate(game)
    base = game.get("baseDetails") or {}
    ext = game.get("extendedDetails") or {}
    home_raw, away_raw = game.get("homeClub") or {}, game.get("awayClub") or {}
    home = _side(home_raw, "home", clubs, coaches, names)
    away = _side(away_raw, "away", clubs, coaches, names)
    date = (base.get("date") or {}).get("dateTimeUTC")
    comp = base.get("competition") or {}
    events = sorted(
        [{"type": "goal", **g} for g in home["goal_events"] + away["goal_events"]] +
        [{"type": "card", **c} for c in home["cards"] + away["cards"]] +
        [{"type": "substitution", **s} for s in home["substitutions"] + away["substitutions"]],
        key=lambda e: ((e.get("minute") or 0), (e.get("added_time") or 0)))
    return {
        "id": int(game["id"]),
        "link": P.entity_link("match", mid),
        "competition": lookup.competition(comp.get("id")) if comp.get("id") else None,
        "season": P.api_season(base.get("season")),
        "matchday": base.get("gameDay"),
        "round": (refs.lookup("competition_groups", base.get("competitionGroupId")) or {}).get("name"),
        "date": date[:10] if date else None,
        "datetime_utc": date,
        "is_time_confirmed": bool((base.get("date") or {}).get("isTimeDefined")),
        "stadium": _stadium(stadium),
        "attendance": ext.get("crowdSize"),
        "is_sold_out": bool(ext.get("isSoldOut")),
        "is_behind_closed_doors": bool(ext.get("isGhostGame")),
        "referee": _referee(referee),
        "duration_minutes": ext.get("duration"),
        "home_goals": home["goals"], "away_goals": away["goals"],
        "result": _result_text(game, home, away),
        "home": home, "away": away,
        "events": events,
        "has_match_report": bool(base.get("isGameReport")),
    }


def _result_text(game, home, away):
    res = game.get("result") or {}
    for key in ("finalResult", "result", "score"):
        if res.get(key):
            return res[key]
    return f"{home['goals']}:{away['goals']}"


def get_lineups(match):
    d = get_details(match)
    keep = ("club", "coach", "formation", "starting_lineup", "substitutes", "lineup_market_value", "substitutions")
    return {"id": d["id"], "link": d["link"], "competition": d["competition"], "date": d["date"],
            "result": d["result"],
            "home": {k: d["home"][k] for k in keep}, "away": {k: d["away"][k] for k in keep}}


def get_statistics(match):
    d = get_details(match)
    return {"id": d["id"], "link": d["link"], "competition": d["competition"], "date": d["date"],
            "result": d["result"],
            "home": {"club": d["home"]["club"], "statistics": d["home"]["statistics"]},
            "away": {"club": d["away"]["club"], "statistics": d["away"]["statistics"]}}
