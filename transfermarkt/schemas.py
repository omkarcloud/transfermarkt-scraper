"""Marshmallow request schemas for every /transfermarkt/* route.

Generic fields live in the shared top-level schema_fields.py; this module
adds the Transfermarkt resolvers (id-or-link refs, seasons, countries,
positions) and the per-route schemas. Every schema's load() output is the
kwargs dict its endpoint function takes. ONE param per input: `player`,
`club`, `competition`, `manager`, `match` each take a bare id OR a pasted
transfermarkt.com link (refs.py).
"""
from marshmallow import ValidationError, fields, post_load, validate, validates_schema

from schema_fields import (
    BaseSchema, ChoiceField, CommaListField, Flag, PageField, PositiveInt, QueryField,
    RefField, StrippedString, NonNegativeNumber,
)
from transfermarkt import refs
from transfermarkt.search import ADVANCED_PAGE_SIZE  # noqa: F401  (documents the page size)


# ---- id-or-link fields -------------------------------------------------------------------

class PlayerRefField(RefField):
    resolver = staticmethod(refs.resolve_player)


class ClubRefField(RefField):
    resolver = staticmethod(refs.resolve_club)


class CompetitionRefField(RefField):
    resolver = staticmethod(refs.resolve_competition)


class ManagerRefField(RefField):
    resolver = staticmethod(refs.resolve_manager)


class MatchRefField(RefField):
    resolver = staticmethod(refs.resolve_match)


class SeasonField(StrippedString):
    """Season start year (2025) or a season name (25/26, 2025/26) -> start year."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_season(value)
        except ValueError as e:
            raise ValidationError(str(e))


class CountryField(StrippedString):
    """Country name (England), FIFA code (ENG) or Transfermarkt country id -> id."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_country_id(value)
        except ValueError as e:
            raise ValidationError(str(e))


class PositionField(StrippedString):
    """Position id (1-14), name (Centre-Back) or short code (CB) -> id."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_position_id(value)
        except ValueError as e:
            raise ValidationError(str(e))


class CompetitionCodeField(StrippedString):
    """Optional competition code / link (filters), resolved like `competition`."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_competition(value)
        except ValueError as e:
            raise ValidationError(str(e))


def _position_group():
    return ChoiceField(list(refs.POSITION_GROUPS))


def _age_group():
    return ChoiceField(refs.AGE_GROUPS)


def _continent():
    return ChoiceField(["1", "2", "3", "4", "5", "6"],
                       metadata={"description": "confederation id: 1 AFC, 2 CAF, 3 CONCACAF, 4 CONMEBOL, 5 OFC, 6 UEFA"})


class _Money(NonNegativeNumber):
    """Whole euros."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        return int(value) if value is not None else None


# ---- search -----------------------------------------------------------------------------

class SearchSchema(BaseSchema):
    query = QueryField()
    type = ChoiceField(refs.SEARCH_TYPES, load_default="all")
    page = PageField(max_page=50)


class AdvancedSearchSchema(BaseSchema):
    name = StrippedString(required=False, load_default=None, validate=validate.Length(max=100))
    first_name = StrippedString(required=False, load_default=None, validate=validate.Length(max=100))
    exact_name = Flag()
    place_of_birth = StrippedString(required=False, load_default=None, validate=validate.Length(max=100))
    nationality = CountryField()
    second_nationality = CountryField()
    country_of_birth = CountryField()
    continent = _continent()
    birth_year = PositiveInt(max_value=2100)
    min_birth_year = PositiveInt(max_value=2100)
    max_birth_year = PositiveInt(max_value=2100)
    min_age = PositiveInt(max_value=100)
    max_age = PositiveInt(max_value=100)
    min_height_m = NonNegativeNumber()
    max_height_m = NonNegativeNumber()
    min_market_value = _Money()
    max_market_value = _Money()
    position_groups = CommaListField(allowed=list(refs.POSITION_GROUPS), upper=False)
    position_id = PositionField()
    side_position_id = PositionField()
    foot = ChoiceField(list(refs.FEET))
    is_captain = Flag()
    shirt_number = PositiveInt(max_value=99)
    competition = CompetitionCodeField()
    club_country = CountryField()
    league_tiers = CommaListField(allowed=list(refs.LEAGUE_TIERS), upper=False)
    national_team_status = CommaListField(allowed=list(refs.NATIONAL_TEAM_STATUS), upper=False)
    min_caps = PositiveInt(max_value=300)
    max_caps = PositiveInt(max_value=300)
    contract_expires = CommaListField(upper=False)
    is_active = Flag()
    is_free_agent = Flag()
    is_on_loan = Flag()
    has_transfer_this_season = Flag()
    page = PageField(max_page=200)

    @validates_schema
    def _ranges(self, data, **kwargs):
        for lo, hi in (("min_age", "max_age"), ("min_birth_year", "max_birth_year"),
                       ("min_height_m", "max_height_m"), ("min_market_value", "max_market_value"),
                       ("min_caps", "max_caps")):
            if data.get(lo) is not None and data.get(hi) is not None and data[lo] > data[hi]:
                raise ValidationError(f"{lo} must be <= {hi}.", lo)
        for year in data.get("contract_expires") or []:
            if not (year.isdigit() and 2000 <= int(year) <= 2100):
                raise ValidationError("contract_expires must be a comma list of years (e.g. 2026,2027).",
                                      "contract_expires")


# ---- players ----------------------------------------------------------------------------

class PlayerSchema(BaseSchema):
    player = PlayerRefField()


class PlayerStatsSchema(PlayerSchema):
    season = SeasonField()
    competition = CompetitionCodeField()


class PlayerMatchLogSchema(PlayerStatsSchema):
    page = PageField(max_page=100)


class PlayerInjuriesSchema(PlayerSchema):
    page = PageField(max_page=50)


class PlayerUpcomingSchema(PlayerSchema):
    limit = PositiveInt(max_value=50, load_default=10)


# ---- clubs -------------------------------------------------------------------------------

class ClubSchema(BaseSchema):
    club = ClubRefField()


class ClubSeasonSchema(ClubSchema):
    season = SeasonField()


class ClubTransfersSchema(ClubSeasonSchema):
    window = ChoiceField(list(refs.TRANSFER_WINDOWS))
    position_group = _position_group()
    position_id = PositionField()
    skip_loans = Flag()
    skip_youth = Flag()


class ClubSquadStatsSchema(ClubSeasonSchema):
    competition = CompetitionCodeField()


class ClubTransferRecordsSchema(ClubSeasonSchema):
    type = ChoiceField(["arrivals", "departures"], load_default="arrivals")


# ---- competitions ------------------------------------------------------------------------

class CompetitionListSchema(BaseSchema):
    region = ChoiceField(list(refs.REGIONS), load_default="europe")


class CompetitionSchema(BaseSchema):
    competition = CompetitionRefField()


class CompetitionSeasonSchema(CompetitionSchema):
    season = SeasonField()


class CompetitionTopScorersSchema(CompetitionSeasonSchema):
    page = PageField(max_page=50)
    position_id = PositionField()
    age_group = _age_group()


class CompetitionMatchdaySchema(CompetitionSeasonSchema):
    matchday = PositiveInt(max_value=99)


class CompetitionMarketValuesSchema(CompetitionSchema):
    page = PageField(max_page=50)
    position_group = _position_group()
    position_id = PositionField()
    age_group = _age_group()
    only_loans = Flag()


class CompetitionRumoursSchema(CompetitionSchema):
    page = PageField(max_page=50)


# ---- matches / managers --------------------------------------------------------------------

class MatchSchema(BaseSchema):
    match = MatchRefField()


class ManagerSchema(BaseSchema):
    manager = ManagerRefField()


# ---- global lists ---------------------------------------------------------------------------

class _Paged(BaseSchema):
    page = PageField(max_page=100)


class LatestTransfersSchema(_Paged):
    country = CountryField()
    competition = CompetitionCodeField()
    club_country = CountryField()
    min_market_value = _Money()
    max_market_value = _Money()
    min_fee = _Money()
    max_fee = _Money()


class TransferRecordsSchema(_Paged):
    season = SeasonField()
    country = CountryField()
    position_group = _position_group()
    position_id = PositionField()
    age_group = _age_group()
    window = ChoiceField(list(refs.TRANSFER_WINDOWS))
    only_loans = Flag()


class MostValuablePlayersSchema(_Paged):
    position_group = _position_group()
    position_id = PositionField()
    age_group = _age_group()
    birth_year = PositiveInt(max_value=2100)
    continent = _continent()
    country = CountryField()
    year = PositiveInt(max_value=2100)
    only_loans = Flag()


class MostValuableClubsSchema(_Paged):
    country = CountryField()
    continent = _continent()


class MarketValueChangesSchema(_Paged):
    position_group = _position_group()
    position_id = PositionField()
    age_group = _age_group()
    country = CountryField()
    competition = CompetitionCodeField()


class ContractsExpiringSchema(_Paged):
    year = PositiveInt(max_value=2100)
    position_group = _position_group()
    position_id = PositionField()
    age_group = _age_group()
    country = CountryField()
    competition = CompetitionCodeField()


class FreeAgentsSchema(_Paged):
    position_group = _position_group()
    position_id = PositionField()
    country = CountryField()


class FifaRankingSchema(_Paged):
    date = StrippedString(required=False, load_default=None,
                          validate=validate.Regexp(r"^\d{4}-\d{2}-\d{2}$", error="Must be YYYY-MM-DD."))


class RumoursSchema(_Paged):
    pass


class EmptySchema(BaseSchema):
    pass
