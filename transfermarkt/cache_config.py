"""Cache TTL per /transfermarkt/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?page=1` and no `page` share a
row). Tiers follow how often Transfermarkt itself moves the data: market
values are re-rated in batches (weekly at most per league), transfers and
rumours land daily, tables change on match days, static facts rarely.
"""
from datetime import timedelta

# Quick search results shift only when entities are created/renamed.
SEARCH_CACHE = timedelta(days=1)
# Advanced search is a POST over the full player index; filters on market
# value / age change daily at most.
ADVANCED_SEARCH_CACHE = timedelta(hours=12)
# Player card: club, contract, market value (re-rated at most weekly).
PLAYER_PROFILE_CACHE = timedelta(days=1)
# Market value history: appended on each re-rating.
MARKET_VALUE_CACHE = timedelta(days=1)
# Transfer history: a player moves twice a year at most.
PLAYER_TRANSFERS_CACHE = timedelta(days=1)
# Game-by-game log grows after every match; stats/match-log/national-team
# derive from the same 1.4 MB payload, so they share this tier.
PERFORMANCE_CACHE = timedelta(hours=6)
# Injuries, achievements, squad numbers: edited after matches / seasons.
PLAYER_TABS_CACHE = timedelta(days=1)
# Rumours are a live feed.
RUMOURS_CACHE = timedelta(hours=3)
# Upcoming fixtures move with kick-off times and results.
UPCOMING_CACHE = timedelta(hours=3)
# Club card (squad size, values, transfer record).
CLUB_PROFILE_CACHE = timedelta(days=1)
# Squad lists change in transfer windows; values weekly.
SQUAD_CACHE = timedelta(days=1)
# Club transfers of a season.
CLUB_TRANSFERS_CACHE = timedelta(hours=12)
# Fixtures & results: results land on match days.
FIXTURES_CACHE = timedelta(hours=6)
# Squad statistics: after every match.
SQUAD_STATS_CACHE = timedelta(hours=6)
# Staff, stadium, achievements, historical placements: near-static.
CLUB_STATIC_CACHE = timedelta(days=7)
# Competition directory & overview.
COMPETITIONS_CACHE = timedelta(days=7)
COMPETITION_OVERVIEW_CACHE = timedelta(days=1)
# Standings, top scorers, matchdays: match-day churn.
STANDINGS_CACHE = timedelta(hours=3)
TOP_SCORERS_CACHE = timedelta(hours=6)
MATCHDAY_CACHE = timedelta(hours=3)
# Competition-wide transfers (a 1.4 MB page) and value lists.
COMPETITION_TRANSFERS_CACHE = timedelta(hours=12)
MARKET_VALUE_LISTS_CACHE = timedelta(hours=12)
# A finished match report never changes; a live/upcoming one does, but the
# tmapi payload is cheap and three routes share it.
MATCH_CACHE = timedelta(hours=6)
# Manager card and career.
MANAGER_CACHE = timedelta(days=1)
# Global lists: transfers/rumours daily feeds, records and rankings slower.
LATEST_TRANSFERS_CACHE = timedelta(hours=6)
TRANSFER_RECORDS_CACHE = timedelta(days=1)
CONTRACTS_CACHE = timedelta(days=1)
FIFA_RANKING_CACHE = timedelta(days=7)
# Vendored lookup tables never call upstream; cached anyway to skip the JSON load.
HELPERS_CACHE = timedelta(days=30)
