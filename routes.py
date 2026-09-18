"""The 51 Transfermarkt endpoints. Every path is served with and without the
`/transfermarkt` prefix, so code generated against the hosted API on RapidAPI
(paths like /players/profile) runs unchanged against this server.

Query params are validated by the same marshmallow schemas the hosted API uses
(transfermarkt/schemas.py): unknown params are rejected, ids and links resolve
to ids, seasons accept `2025` or `25/26`, and countries and positions accept
names, codes or ids.
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from transfermarkt import (clubs, competitions, helpers, lists, managers,
                           matches, players, schemas, search)

# path, schema, implementation, paginated
ENDPOINTS = [
    ("/players/profile", schemas.PlayerSchema, players.get_profile, False),
    ("/search", schemas.SearchSchema, search.search, True),
    ("/players/search-advanced", schemas.AdvancedSearchSchema, search.search_advanced, True),
    ("/players/market-value", schemas.PlayerSchema, players.get_market_value, False),
    ("/players/transfers", schemas.PlayerSchema, players.get_transfers, False),
    ("/players/stats", schemas.PlayerStatsSchema, players.get_stats, False),
    ("/players/match-log", schemas.PlayerMatchLogSchema, players.get_match_log, True),
    ("/players/national-team", schemas.PlayerSchema, players.get_national_team, False),
    ("/players/injuries", schemas.PlayerInjuriesSchema, players.get_injuries, True),
    ("/players/achievements", schemas.PlayerSchema, players.get_achievements, False),
    ("/players/squad-numbers", schemas.PlayerSchema, players.get_squad_numbers, False),
    ("/players/rumours", schemas.PlayerSchema, players.get_rumours, False),
    ("/players/upcoming-matches", schemas.PlayerUpcomingSchema, players.get_upcoming_matches, False),
    ("/clubs/profile", schemas.ClubSchema, clubs.get_profile, False),
    ("/clubs/squad", schemas.ClubSeasonSchema, clubs.get_squad, False),
    ("/clubs/transfers", schemas.ClubTransfersSchema, clubs.get_transfers, False),
    ("/clubs/fixtures", schemas.ClubSeasonSchema, clubs.get_fixtures, False),
    ("/clubs/squad-stats", schemas.ClubSquadStatsSchema, clubs.get_squad_stats, False),
    ("/clubs/staff", schemas.ClubSchema, clubs.get_staff, False),
    ("/clubs/stadium", schemas.ClubSchema, clubs.get_stadium, False),
    ("/clubs/achievements", schemas.ClubSchema, clubs.get_achievements, False),
    ("/clubs/history", schemas.ClubSchema, clubs.get_history, False),
    ("/clubs/transfer-records", schemas.ClubTransferRecordsSchema, clubs.get_transfer_records, False),
    ("/clubs/national-players", schemas.ClubSchema, clubs.get_national_players, False),
    ("/competitions/list", schemas.CompetitionListSchema, competitions.get_list, False),
    ("/competitions/overview", schemas.CompetitionSeasonSchema, competitions.get_overview, False),
    ("/competitions/standings", schemas.CompetitionSeasonSchema, competitions.get_standings, False),
    ("/competitions/top-scorers", schemas.CompetitionTopScorersSchema, competitions.get_top_scorers, True),
    ("/competitions/matchday", schemas.CompetitionMatchdaySchema, competitions.get_matchday, False),
    ("/competitions/fixtures", schemas.CompetitionSeasonSchema, competitions.get_fixtures, False),
    ("/competitions/transfers", schemas.CompetitionSeasonSchema, competitions.get_transfers, False),
    ("/competitions/market-values", schemas.CompetitionMarketValuesSchema, competitions.get_market_values, True),
    ("/competitions/club-values", schemas.CompetitionSchema, competitions.get_club_values, False),
    ("/competitions/rumours", schemas.CompetitionRumoursSchema, competitions.get_rumours, True),
    ("/matches/details", schemas.MatchSchema, matches.get_details, False),
    ("/matches/lineups", schemas.MatchSchema, matches.get_lineups, False),
    ("/matches/statistics", schemas.MatchSchema, matches.get_statistics, False),
    ("/managers/profile", schemas.ManagerSchema, managers.get_profile, False),
    ("/managers/career", schemas.ManagerSchema, managers.get_career, False),
    ("/managers/achievements", schemas.ManagerSchema, managers.get_achievements, False),
    ("/transfers/latest", schemas.LatestTransfersSchema, lists.get_latest_transfers, True),
    ("/transfers/records", schemas.TransferRecordsSchema, lists.get_transfer_records, True),
    ("/transfers/rumours", schemas.RumoursSchema, lists.get_latest_rumours, True),
    ("/players/most-valuable", schemas.MostValuablePlayersSchema, lists.get_most_valuable_players, True),
    ("/players/market-value-changes", schemas.MarketValueChangesSchema, lists.get_market_value_changes, True),
    ("/players/contracts-expiring", schemas.ContractsExpiringSchema, lists.get_contracts_expiring, True),
    ("/players/free-agents", schemas.FreeAgentsSchema, lists.get_free_agents, True),
    ("/clubs/most-valuable", schemas.MostValuableClubsSchema, lists.get_most_valuable_clubs, True),
    ("/rankings/fifa", schemas.FifaRankingSchema, lists.get_fifa_ranking, True),
    ("/helpers/countries", schemas.EmptySchema, helpers.countries, False),
    ("/helpers/positions", schemas.EmptySchema, helpers.positions, False),
]


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as plain unicode strings (bottle 0.12's .get() hands
    back latin-1 decoded bytes, so a UTF-8 "Kylian Mbappé" would arrive as
    "MbappÃ©")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def page_link(path, params, page):
    """Absolute link to another page of the same list, on this server."""
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "", False)}
    query["page"] = page
    return f"{request.urlparts.scheme}://{request.urlparts.netloc}{path}?{urlencode(query, doseq=True)}"


def as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def paginate(result, path, raw_params):
    """Lift the scraper's `pagination` block into the flat public shape with
    next/previous links built from the caller's own query params."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)          # per-page count; `count` is the total
    page = as_int(pagination.get("page")) or as_int(raw_params.get("page")) or 1
    total_pages = max(as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": page_link(path, raw_params, page + 1 if page < total_pages else None),
        "previous": page_link(path, raw_params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(path, schema, impl, paginated):
    """Validate the query, run the scraper and map failures to HTTP:
    bad params -> 400, missing entity -> 404, blocks / transport -> 502."""
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:                     # bad id / param value
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:                     # transfermarkt rejected the request
        return json_response({"error": f"transfermarkt rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"transfermarkt blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"transfermarkt {path.strip('/')} failed: {e}"}, 502)
    except Exception as e:                      # parser surprises
        return json_response({"error": f"transfermarkt {path.strip('/')} failed: {type(e).__name__}: {e}"}, 500)
    if paginated:
        result = paginate(result, path, raw)
    return json_response(result)


def mount(path, schema, impl, paginated):
    """Serve one endpoint at /path and /transfermarkt/path."""
    def handler():
        return call(path, schema, impl, paginated)
    handler.__name__ = "transfermarkt_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/transfermarkt" + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
