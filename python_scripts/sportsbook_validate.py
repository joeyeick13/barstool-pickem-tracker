from __future__ import annotations

import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from common import PICKS_FILE, load_json
from football_identity import (
    base_market,
    market_period,
    normalize_bet_type,
    safe_float,
    side_identity,
    teams_equivalent,
)
from espn_resolver import (
    build_complete_week_slate,
    competitor_display_name,
    competitor_home_away,
    competitors,
    event_datetime,
    event_id,
    event_matchup_text,
    resolve_event_detailed,
)


# ============================================================
# CONFIG
# ============================================================

API_BASE = "https://api.oddspapi.io/v4"
BOOKMAKER = "draftkings"

SPORT_ID = 14
NCAA_TOURNAMENT_ID = 27653

HTTP_TIMEOUT = 90

FIXTURE_WINDOW_HOURS = 3
FIXTURE_TOLERANCE_MINUTES = 90

GENERAL_COOLDOWN_SECONDS = 1.05
HISTORICAL_COOLDOWN_SECONDS = 5.10

AUDIT_OFFICIAL_WEEK = int(
    os.getenv(
        "SPORTSBOOK_AUDIT_WEEK",
        "4",
    )
)

SEASON_YEAR = int(
    os.getenv(
        "PICKEM_SEASON_YEAR",
        "2026",
    )
)


# ============================================================
# BASIC HELPERS
# ============================================================

def _clean(value):
    return str(
        value or ""
    ).strip()


def _parse_dt(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value)
            .strip()
            .replace(
                "Z",
                "+00:00",
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def _iso(dt):
    return (
        dt
        .astimezone(
            timezone.utc
        )
        .strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


def _week(pick):
    try:
        return int(
            pick.get(
                "week"
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _is_cfb(pick):
    return (
        _clean(
            pick.get(
                "sport"
            )
        ).upper()
        in {
            "CFB",
            "NCAAF",
        }
    )


def _eligible(pick):
    if not _is_cfb(
        pick
    ):
        return False

    if (
        _week(
            pick
        )
        == AUDIT_OFFICIAL_WEEK
    ):
        return True

    return not bool(
        pick.get(
            "official_reconciled"
        )
    )


def _label(pick):
    return (
        f"W{pick.get('week')} | "
        f"{pick.get('picker')} | "
        f"{pick.get('selection')}"
    )


# ============================================================
# ODDSPAPI CLIENT
# ============================================================

class OddsPapi:

    def __init__(
        self,
        api_key,
    ):
        self.api_key = api_key

        self.session = (
            requests.Session()
        )

        self.fixture_cache = {}
        self.history_cache = {}

        self._markets = None

    def get(
        self,
        endpoint,
        cooldown=0.0,
        **params,
    ):
        params = dict(
            params
        )

        params[
            "apiKey"
        ] = self.api_key

        last_error = None

        for attempt in range(
            1,
            5,
        ):
            response = (
                self.session.get(
                    f"{API_BASE}/{endpoint}",
                    params=params,
                    timeout=HTTP_TIMEOUT,
                )
            )

            if (
                response.status_code
                == 404
            ):
                return None

            if (
                response.status_code
                == 429
            ):
                wait = max(
                    5.0 * attempt,
                    float(
                        response.headers.get(
                            "Retry-After"
                        )
                        or 0
                    ),
                )

                time.sleep(
                    wait
                )

                last_error = (
                    requests.HTTPError(
                        (
                            "429 from OddsPapi "
                            f"/{endpoint}"
                        ),
                        response=response,
                    )
                )

                continue

            response.raise_for_status()

            payload = (
                response.json()
            )

            if cooldown:
                time.sleep(
                    cooldown
                )

            return payload

        if last_error:
            raise last_error

        raise RuntimeError(
            (
                "OddsPapi request failed: "
                f"/{endpoint}"
            )
        )

    def fixtures_near(
        self,
        kickoff,
    ):
        start = (
            kickoff
            - timedelta(
                hours=
                    FIXTURE_WINDOW_HOURS
            )
        )

        end = (
            kickoff
            + timedelta(
                hours=
                    FIXTURE_WINDOW_HOURS
            )
        )

        key = (
            _iso(
                start
            ),
            _iso(
                end
            ),
        )

        if (
            key
            not in self.fixture_cache
        ):
            payload = self.get(
                "fixtures",
                cooldown=
                    GENERAL_COOLDOWN_SECONDS,
                sportId=
                    SPORT_ID,
                tournamentId=
                    NCAA_TOURNAMENT_ID,
                language="en",
                **{
                    "from":
                        key[0],

                    "to":
                        key[1],
                },
            )

            self.fixture_cache[
                key
            ] = (
                payload
                if isinstance(
                    payload,
                    list,
                )
                else []
            )

        return (
            self.fixture_cache[
                key
            ]
        )

    def markets(
        self
    ):
        if (
            self._markets
            is None
        ):
            payload = (
                self.get(
                    "markets",
                    cooldown=
                        GENERAL_COOLDOWN_SECONDS,
                    language="en",
                )
                or []
            )

            self._markets = {
                str(
                    row.get(
                        "marketId"
                    )
                ):
                    row

                for row
                in payload

                if (
                    isinstance(
                        row,
                        dict,
                    )
                    and row.get(
                        "marketId"
                    )
                    is not None
                )
            }

        return self._markets

    def historical_odds(
        self,
        fixture_id,
    ):
        fixture_id = str(
            fixture_id
        )

        if (
            fixture_id
            not in self.history_cache
        ):
            payload = self.get(
                "historical-odds",
                cooldown=
                    HISTORICAL_COOLDOWN_SECONDS,
                fixtureId=
                    fixture_id,
                bookmakers=
                    BOOKMAKER,
            )

            self.history_cache[
                fixture_id
            ] = (
                payload
                if isinstance(
                    payload,
                    dict,
                )
                else {}
            )

        return (
            self.history_cache[
                fixture_id
            ]
        )


# ============================================================
# ESPN EVENT SIDES
# ============================================================

def _espn_sides(
    event
):
    home = None
    away = None

    for competitor in competitors(
        event
    ):
        name = (
            competitor_display_name(
                competitor
            )
        )

        location = (
            competitor_home_away(
                competitor
            )
        )

        if (
            location
            == "home"
        ):
            home = name

        elif (
            location
            == "away"
        ):
            away = name

    return (
        home,
        away,
    )


# ============================================================
# ODDSPAPI FIXTURE SIDES
# ============================================================

def _fixture_sides(
    fixture
):
    return (
        _clean(
            fixture.get(
                "participant1Name"
            )
        ),
        _clean(
            fixture.get(
                "participant2Name"
            )
        ),
    )


def _same_team(
    left,
    right,
):
    return bool(
        left
        and right
        and teams_equivalent(
            left,
            right,
        )
    )


# ============================================================
# ESPN EVENT -> ODDSPAPI FIXTURE
# ============================================================

def _match_fixture(
    api,
    event,
):
    kickoff = (
        event_datetime(
            event
        )
    )

    home, away = (
        _espn_sides(
            event
        )
    )

    if (
        kickoff is None
        or not home
        or not away
    ):
        return (
            None,
            "ESPN_EVENT_INCOMPLETE",
            None,
        )

    candidates = []

    for fixture in (
        api.fixtures_near(
            kickoff
        )
    ):
        participant1, participant2 = (
            _fixture_sides(
                fixture
            )
        )

        fixture_time = (
            _parse_dt(
                fixture.get(
                    "startTime"
                )
            )
        )

        if (
            not participant1
            or not participant2
            or fixture_time is None
        ):
            continue

        delta = (
            abs(
                (
                    fixture_time
                    - kickoff
                ).total_seconds()
            )
            / 60.0
        )

        if (
            delta
            >
            FIXTURE_TOLERANCE_MINUTES
        ):
            continue

        # OddsPapi American football:
        # participant 1 = home
        # participant 2 = away.
        if (
            _same_team(
                home,
                participant1,
            )
            and _same_team(
                away,
                participant2,
            )
        ):
            identity_rank = 0
            orientation = (
                "P1_HOME"
            )

        # Defensive fallback only.
        elif (
            _same_team(
                home,
                participant2,
            )
            and _same_team(
                away,
                participant1,
            )
        ):
            identity_rank = 1
            orientation = (
                "REVERSED"
            )

        else:
            continue

        candidates.append(
            (
                identity_rank,
                delta,
                fixture,
                orientation,
            )
        )

    if not candidates:
        return (
            None,
            "ODDSPAPI_FIXTURE_NOT_FOUND",
            {
                "espn_event_id":
                    event_id(
                        event
                    ),

                "espn_matchup":
                    event_matchup_text(
                        event
                    ),

                "espn_kickoff":
                    kickoff.isoformat(),
            },
        )

    candidates.sort(
        key=lambda row: (
            row[0],
            row[1],
        )
    )

    best = candidates[0]

    if (
        len(
            candidates
        )
        > 1
        and candidates[1][0:2]
        == best[0:2]
    ):
        return (
            None,
            "ODDSPAPI_FIXTURE_AMBIGUOUS",
            None,
        )

    (
        _,
        delta,
        fixture,
        orientation,
    ) = best

    return (
        fixture,
        "MATCHED",
        {
            "fixture_id":
                fixture.get(
                    "fixtureId"
                ),

            "participant1":
                fixture.get(
                    "participant1Name"
                ),

            "participant2":
                fixture.get(
                    "participant2Name"
                ),

            "orientation":
                orientation,

            "kickoff_delta_minutes":
                round(
                    delta,
                    2,
                ),
        },
    )


# ============================================================
# CONTEXT-ONLY TEAM ABBREVIATIONS
# ============================================================

_CONTEXT_ALIASES = {
    "osu": (
        "ohio state",
        "oklahoma state",
        "oregon state",
    ),

    "um": (
        "michigan",
        "miami",
        "mississippi",
        "montana",
    ),

    "usc": (
        "usc",
        "south carolina",
    ),

    "tu": (
        "temple",
        "tulane",
        "tulsa",
    ),
}


def _selected_espn_team(
    pick,
    event,
):
    """
    Resolve the wager side only AFTER ESPN has identified the
    exact game.

    Ambiguous aliases never become global football identities.
    """

    selected = _clean(
        side_identity(
            pick
        )
        or pick.get(
            "team"
        )
    )

    names = [
        competitor_display_name(
            competitor
        )

        for competitor
        in competitors(
            event
        )
    ]

    if (
        len(
            names
        )
        != 2
        or not selected
    ):
        return (
            None,
            "NO_SELECTED_TEAM",
        )

    direct = [
        name

        for name
        in names

        if _same_team(
            selected,
            name,
        )
    ]

    if (
        len(
            direct
        )
        == 1
    ):
        return (
            direct[0],
            "DIRECT",
        )

    key = (
        selected
        .lower()
        .replace(
            ".",
            "",
        )
        .strip()
    )

    # --------------------------------------------------------
    # UT
    # --------------------------------------------------------
    #
    # Verified Pick Em context:
    #
    # Texas @ Tennessee
    # UT +4.5
    #
    # UT means Tennessee.
    #
    # This deliberately does NOT create a global UT alias.
    # --------------------------------------------------------

    if (
        key
        == "ut"
    ):
        has_texas = any(
            _same_team(
                "Texas",
                name,
            )

            for name
            in names
        )

        tennessee = [
            name

            for name
            in names

            if _same_team(
                "Tennessee",
                name,
            )
        ]

        if (
            has_texas
            and len(
                tennessee
            )
            == 1
        ):
            return (
                tennessee[0],
                (
                    "CONTEXT_UT_"
                    "TEXAS_TENNESSEE"
                ),
            )

    candidates = (
        _CONTEXT_ALIASES.get(
            key,
            (),
        )
    )

    contextual = [
        name

        for name
        in names

        if any(
            _same_team(
                candidate,
                name,
            )

            for candidate
            in candidates
        )
    ]

    if (
        len(
            contextual
        )
        == 1
    ):
        return (
            contextual[0],
            (
                "CONTEXT_"
                f"{key.upper()}"
            ),
        )

    return (
        None,
        "AMBIGUOUS_SELECTED_TEAM",
    )


# ============================================================
# ESPN SELECTED TEAM -> ODDSPAPI PARTICIPANT
# ============================================================

def _selected_fixture_number(
    pick,
    event,
    fixture,
):
    (
        selected_name,
        method,
    ) = _selected_espn_team(
        pick,
        event,
    )

    if not selected_name:
        return (
            None,
            method,
            None,
        )

    participant1, participant2 = (
        _fixture_sides(
            fixture
        )
    )

    match1 = (
        _same_team(
            selected_name,
            participant1,
        )
    )

    match2 = (
        _same_team(
            selected_name,
            participant2,
        )
    )

    if (
        match1
        == match2
    ):
        return (
            None,
            (
                "SELECTED_TEAM_NOT_"
                "UNIQUE_IN_FIXTURE"
            ),
            selected_name,
        )

    return (
        (
            "1"
            if match1
            else "2"
        ),
        method,
        selected_name,
    )


# ============================================================
# MARKET CLASSIFICATION
# ============================================================

def _period_ok(
    pick,
    market,
):
    wanted = _clean(
        market_period(
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
        )
    ).lower()

    actual = _clean(
        market.get(
            "period"
        )
    ).lower()

    if wanted in {
        "",
        "full",
        "fullgame",
        "full_game",
        "game",
    }:
        return actual in {
            "",
            "fulltime",
            "full_time",
            "game",
            "match",
        }

    if wanted in {
        "1h",
        "firsthalf",
        "first_half",
    }:
        return actual in {
            "1h",
            "firsthalf",
            "first_half",
            "first half",
        }

    if wanted in {
        "1q",
        "firstquarter",
        "first_quarter",
    }:
        return actual in {
            "1q",
            "firstquarter",
            "first_quarter",
            "first quarter",
        }

    return (
        actual.replace(
            "_",
            "",
        )
        ==
        wanted.replace(
            "_",
            "",
        )
    )


def _market_family(
    market,
):
    name = _clean(
        market.get(
            "marketName"
        )
    ).lower()

    market_type = _clean(
        market.get(
            "marketType"
        )
    ).lower()

    # --------------------------------------------------------
    # IMPORTANT
    # --------------------------------------------------------
    #
    # Team totals MUST be classified before generic totals.
    #
    # Previously:
    #
    # Over Under Team 1
    #
    # could fall through to TOTAL and incorrectly validate a
    # full-game Over/Under.
    # --------------------------------------------------------

    if (
        "team 1"
        in name
        or "team 2"
        in name
        or "team total"
        in name
        or "participant 1 total"
        in name
        or "participant 2 total"
        in name
    ):
        return "TEAM_TOTAL"

    if (
        "handicap"
        in name
        or "spread"
        in name
        or "handicap"
        in market_type
    ):
        return "SPREAD"

    if (
        "total"
        in name
        or "over under"
        in name
        or "total"
        in market_type
    ):
        return "TOTAL"

    if (
        "winner"
        in name
        or "moneyline"
        in name
        or "money line"
        in name
        or market_type
        in {
            "winner",
            "moneyline",
            "money_line",
        }
    ):
        return "MONEYLINE"

    return None


def _catalog_markets(
    api,
    pick,
):
    wanted = base_market(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    rows = []

    for (
        market_id,
        meta,
    ) in api.markets().items():

        sport = safe_float(
            meta.get(
                "sportId"
            )
        )

        if sport not in (
            None,
            float(
                SPORT_ID
            ),
        ):
            continue

        if (
            _market_family(
                meta
            )
            != wanted
        ):
            continue

        if not _period_ok(
            pick,
            meta,
        ):
            continue

        rows.append(
            (
                market_id,
                meta,
            )
        )

    return rows


# ============================================================
# HISTORICAL ODDS HELPERS
# ============================================================

def _history_markets(
    history
):
    books = (
        history.get(
            "bookmakers"
        )
        or {}
    )

    book = (
        books.get(
            BOOKMAKER
        )
        or {}
    )

    return (
        book.get(
            "markets"
        )
        or {}
    )


def _history_entries(
    history_market,
    outcome_id,
):
    outcome = (
        (
            history_market.get(
                "outcomes"
            )
            or {}
        )
        .get(
            str(
                outcome_id
            )
        )
        or {}
    )

    players = (
        outcome.get(
            "players"
        )
        or {}
    )

    entries = (
        players.get(
            "0"
        )
    )

    if isinstance(
        entries,
        list,
    ):
        return [
            entry

            for entry
            in entries

            if isinstance(
                entry,
                dict,
            )
        ]

    if isinstance(
        entries,
        dict,
    ):
        return [
            entries
        ]

    return []


def _outcome_id(
    meta,
    wanted_name,
):
    wanted = str(
        wanted_name
    ).lower()

    for outcome in (
        meta.get(
            "outcomes"
        )
        or []
    ):
        name = _clean(
            outcome.get(
                "outcomeName"
            )
        ).lower()

        if name in {
            wanted,
            (
                "participant "
                + wanted
            ),
        }:
            return outcome.get(
                "outcomeId"
            )

    return None


def _latest_active_before(
    entries,
    cutoff,
):
    usable = []

    for entry in entries:
        created = _parse_dt(
            entry.get(
                "createdAt"
            )
        )

        price = safe_float(
            entry.get(
                "price"
            )
        )

        if (
            created is None
            or created > cutoff
            or price is None
            or price <= 1
        ):
            continue

        if (
            entry.get(
                "active"
            )
            is False
        ):
            continue

        usable.append(
            (
                created,
                entry,
            )
        )

    if not usable:
        return None

    return max(
        usable,
        key=lambda row:
            row[0],
    )


def _market_quote(
    history_market,
    meta,
    cutoff,
):
    quotes = []

    for outcome in (
        meta.get(
            "outcomes"
        )
        or []
    ):
        outcome_id = (
            outcome.get(
                "outcomeId"
            )
        )

        latest = (
            _latest_active_before(
                _history_entries(
                    history_market,
                    outcome_id,
                ),
                cutoff,
            )
        )

        if latest:
            quotes.append(
                {
                    "name":
                        _clean(
                            outcome.get(
                                "outcomeName"
                            )
                        ),

                    "created":
                        latest[0],

                    "entry":
                        latest[1],
                }
            )

    if (
        len(
            quotes
        )
        != 2
    ):
        return None

    newest = max(
        quote[
            "created"
        ]

        for quote
        in quotes
    )

    oldest = min(
        quote[
            "created"
        ]

        for quote
        in quotes
    )

    if (
        newest
        - oldest
        >
        timedelta(
            minutes=30
        )
    ):
        return None

    prices = [
        safe_float(
            quote[
                "entry"
            ].get(
                "price"
            )
        )

        for quote
        in quotes
    ]

    if any(
        price is None
        or price <= 1

        for price
        in prices
    ):
        return None

    hold = (
        sum(
            1.0
            / price

            for price
            in prices
        )
        - 1.0
    )

    if (
        hold < -0.05
        or hold > 0.25
    ):
        return None

    return {
        "quotes":
            quotes,

        "newest":
            newest,

        "evenness":
            sum(
                abs(
                    price
                    - 2.0
                )

                for price
                in prices
            ),

        "hold":
            hold,
    }


# ============================================================
# DRAFTKINGS CLOSING MARKET
# ============================================================

def _best_market(
    api,
    history,
    pick,
    cutoff,
    selected_number=None,
):
    historical = (
        _history_markets(
            history
        )
    )

    candidates = []

    for (
        market_id,
        meta,
    ) in _catalog_markets(
        api,
        pick,
    ):
        history_market = (
            historical.get(
                str(
                    market_id
                )
            )
        )

        if not isinstance(
            history_market,
            dict,
        ):
            continue

        if (
            selected_number
            is not None
        ):
            selected_outcome = (
                _outcome_id(
                    meta,
                    selected_number,
                )
            )

            if (
                selected_outcome
                is None
            ):
                continue

            if not _latest_active_before(
                _history_entries(
                    history_market,
                    selected_outcome,
                ),
                cutoff,
            ):
                continue

        quote = (
            _market_quote(
                history_market,
                meta,
                cutoff,
            )
        )

        if quote is None:
            continue

        candidates.append(
            {
                "market_id":
                    str(
                        market_id
                    ),

                "meta":
                    meta,

                "quote":
                    quote,
            }
        )

    if not candidates:
        return None

    # Historical odds do not give us a dependable single
    # main-line flag for every snapshot.
    #
    # Within the EXACT market family and period, choose the
    # two-sided DraftKings rung priced closest to even money.
    candidates.sort(
        key=lambda row: (
            row[
                "quote"
            ][
                "evenness"
            ],

            -row[
                "quote"
            ][
                "newest"
            ].timestamp(),
        )
    )

    return candidates[0]


# ============================================================
# SPREAD AUDIT
# ============================================================

def _spread_audit(
    api,
    history,
    pick,
    event,
    fixture,
):
    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if x_line is None:
        return (
            "REVIEW",
            "MISSING_X_SPREAD_LINE",
            None,
        )

    (
        selected_number,
        side_method,
        selected_name,
    ) = _selected_fixture_number(
        pick,
        event,
        fixture,
    )

    if (
        selected_number
        is None
    ):
        return (
            "REVIEW",
            side_method,
            {
                "selected_espn_team":
                    selected_name,
            },
        )

    kickoff = (
        event_datetime(
            event
        )
    )

    chosen = (
        _best_market(
            api,
            history,
            pick,
            (
                kickoff
                - timedelta(
                    seconds=1
                )
            ),
            selected_number=
                selected_number,
        )
    )

    if chosen is None:
        return (
            "UNAVAILABLE",
            "NO_PREKICK_DK_SPREAD",
            None,
        )

    participant1_line = (
        safe_float(
            chosen[
                "meta"
            ].get(
                "handicap"
            )
        )
    )

    if (
        participant1_line
        is None
    ):
        return (
            "UNAVAILABLE",
            (
                "DK_SPREAD_WITHOUT_"
                "HANDICAP"
            ),
            None,
        )

    # OddsPapi handicap is participant 1's handicap.
    #
    # Participant 2 receives the reciprocal.
    dk_line = (
        participant1_line

        if (
            selected_number
            == "1"
        )

        else
        -participant1_line
    )

    magnitude_difference = (
        abs(
            abs(
                x_line
            )
            -
            abs(
                dk_line
            )
        )
    )

    exact = (
        abs(
            x_line
            - dk_line
        )
        <= 0.001
    )

    same_sign = (
        exact
        or (
            x_line
            == dk_line
            == 0
        )
        or (
            x_line
            * dk_line
            > 0
        )
    )

    detail = {
        "selected_espn_team":
            selected_name,

        "selected_fixture_participant":
            selected_number,

        "side_resolution":
            side_method,

        "participant1":
            fixture.get(
                "participant1Name"
            ),

        "participant2":
            fixture.get(
                "participant2Name"
            ),

        "x_line":
            x_line,

        "draftkings_closing_line":
            dk_line,

        "market_id":
            chosen[
                "market_id"
            ],

        "market_name":
            chosen[
                "meta"
            ].get(
                "marketName"
            ),

        "market_period":
            chosen[
                "meta"
            ].get(
                "period"
            ),

        "snapshot_at":
            chosen[
                "quote"
            ][
                "newest"
            ].isoformat(),

        "magnitude_difference":
            round(
                magnitude_difference,
                3,
            ),
    }

    if exact:
        return (
            "CONFIRMED",
            (
                "EXACT_DK_CLOSING_"
                "SPREAD_MATCH"
            ),
            detail,
        )

    if same_sign:
        return (
            "NO_CHANGE",
            (
                "SAME_SIGN_DK_CLOSING_"
                "LINE_MOVEMENT"
            ),
            detail,
        )

    if (
        magnitude_difference
        <= 3.0
    ):
        return (
            "SIGN_REVIEW",
            (
                "OPPOSITE_SIGN_WITHIN_"
                "3_VS_DK_CLOSE"
            ),
            detail,
        )

    return (
        "REVIEW",
        (
            "OPPOSITE_SIGN_OVER_"
            "3_VS_DK_CLOSE"
        ),
        detail,
    )


# ============================================================
# GAME TOTAL AUDIT
# ============================================================

def _total_audit(
    api,
    history,
    pick,
    event,
):
    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if x_line is None:
        return (
            "REVIEW",
            "MISSING_X_TOTAL_LINE",
            None,
        )

    kickoff = (
        event_datetime(
            event
        )
    )

    chosen = (
        _best_market(
            api,
            history,
            pick,
            (
                kickoff
                - timedelta(
                    seconds=1
                )
            ),
        )
    )

    if chosen is None:
        return (
            "UNAVAILABLE",
            "NO_PREKICK_DK_TOTAL",
            None,
        )

    # _market_family() now guarantees that a game TOTAL cannot
    # accidentally select:
    #
    # Over Under Team 1
    # Over Under Team 2
    #
    dk_line = safe_float(
        chosen[
            "meta"
        ].get(
            "handicap"
        )
    )

    if dk_line is None:
        return (
            "UNAVAILABLE",
            "DK_TOTAL_WITHOUT_HANDICAP",
            None,
        )

    detail = {
        "x_line":
            x_line,

        "draftkings_closing_total":
            dk_line,

        "market_id":
            chosen[
                "market_id"
            ],

        "market_name":
            chosen[
                "meta"
            ].get(
                "marketName"
            ),

        "market_period":
            chosen[
                "meta"
            ].get(
                "period"
            ),

        "snapshot_at":
            chosen[
                "quote"
            ][
                "newest"
            ].isoformat(),
    }

    if (
        abs(
            x_line
            - dk_line
        )
        <= 0.001
    ):
        return (
            "CONFIRMED",
            (
                "EXACT_DK_CLOSING_"
                "TOTAL_MATCH"
            ),
            detail,
        )

    return (
        "NO_CHANGE",
        "DK_TOTAL_MOVED",
        detail,
    )


# ============================================================
# MONEYLINE AUDIT
# ============================================================

def _moneyline_audit(
    api,
    history,
    pick,
    event,
    fixture,
):
    (
        selected_number,
        side_method,
        selected_name,
    ) = _selected_fixture_number(
        pick,
        event,
        fixture,
    )

    if (
        selected_number
        is None
    ):
        return (
            "REVIEW",
            side_method,
            {
                "selected_espn_team":
                    selected_name,
            },
        )

    kickoff = (
        event_datetime(
            event
        )
    )

    chosen = (
        _best_market(
            api,
            history,
            pick,
            (
                kickoff
                - timedelta(
                    seconds=1
                )
            ),
            selected_number=
                selected_number,
        )
    )

    if chosen is None:
        return (
            "UNAVAILABLE",
            "NO_PREKICK_DK_MONEYLINE",
            None,
        )

    return (
        "CONFIRMED",
        (
            "SELECTED_TEAM_PRESENT_"
            "IN_DK_MONEYLINE"
        ),
        {
            "selected_espn_team":
                selected_name,

            "selected_fixture_participant":
                selected_number,

            "side_resolution":
                side_method,

            "market_id":
                chosen[
                    "market_id"
                ],

            "market_name":
                chosen[
                    "meta"
                ].get(
                    "marketName"
                ),

            "snapshot_at":
                chosen[
                    "quote"
                ][
                    "newest"
                ].isoformat(),
        },
    )


# ============================================================
# SINGLE PICK AUDIT
# ============================================================

def _audit_pick(
    api,
    pick,
    event,
    fixture,
):
    fixture_id = (
        fixture.get(
            "fixtureId"
        )
    )

    if not fixture_id:
        return (
            "UNAVAILABLE",
            (
                "ODDSPAPI_FIXTURE_"
                "WITHOUT_ID"
            ),
            None,
        )

    history = (
        api.historical_odds(
            fixture_id
        )
    )

    if not history:
        return (
            "UNAVAILABLE",
            "NO_ODDSPAPI_HISTORY",
            None,
        )

    books = (
        history.get(
            "bookmakers"
        )
        or {}
    )

    if not books.get(
        BOOKMAKER
    ):
        return (
            "UNAVAILABLE",
            "NO_DRAFTKINGS_HISTORY",
            None,
        )

    family = base_market(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    if (
        family
        == "SPREAD"
    ):
        return _spread_audit(
            api,
            history,
            pick,
            event,
            fixture,
        )

    if (
        family
        == "TOTAL"
    ):
        return _total_audit(
            api,
            history,
            pick,
            event,
        )

    if (
        family
        == "MONEYLINE"
    ):
        return _moneyline_audit(
            api,
            history,
            pick,
            event,
            fixture,
        )

    # Do not let a team total silently fall through to a
    # game-total comparison.
    if (
        family
        == "TEAM_TOTAL"
    ):
        return (
            "UNAVAILABLE",
            "TEAM_TOTAL_AUDIT_NOT_ENABLED",
            None,
        )

    return (
        "UNAVAILABLE",
        (
            "UNSUPPORTED_MARKET_"
            f"{family}"
        ),
        None,
    )


# ============================================================
# MAIN
# ============================================================

def validate_sportsbook():

    print()

    print(
        "=" * 72
    )

    print(
        (
            "DRAFTKINGS VALIDATION — "
            "ESPN → ODDSPAPI "
            "HISTORICAL AUDIT"
        )
    )

    print(
        "=" * 72
    )

    print(
        (
            "Official audit week: "
            f"{AUDIT_OFFICIAL_WEEK}"
        )
    )

    print(
        (
            "READ ONLY: picks.json "
            "will not be modified."
        )
    )

    api_key = os.getenv(
        "ODDSPAPI_API_KEY"
    )

    if not api_key:
        print(
            (
                "Skipping DraftKings "
                "validation: "
                "ODDSPAPI_API_KEY "
                "not configured."
            )
        )

        return

    picks = load_json(
        PICKS_FILE,
        [],
    )

    if not isinstance(
        picks,
        list,
    ):
        raise RuntimeError(
            (
                "picks.json must "
                "contain a list."
            )
        )

    audit_picks = [
        pick

        for pick
        in picks

        if (
            isinstance(
                pick,
                dict,
            )
            and _eligible(
                pick
            )
            and _week(
                pick
            )
            == AUDIT_OFFICIAL_WEEK
        )
    ]

    if not audit_picks:
        print(
            "No eligible wagers found."
        )

        return

    # --------------------------------------------------------
    # ESPN REMAINS THE GAME IDENTITY AUTHORITY
    # --------------------------------------------------------

    events = (
        build_complete_week_slate(
            audit_picks,
            SEASON_YEAR,
            AUDIT_OFFICIAL_WEEK,
        )
    )

    api = OddsPapi(
        api_key
    )

    counts = Counter()

    event_fixture_cache = {}

    history_result_cache = {}

    matched_fixture_ids = set()

    print()

    print(
        "-" * 72
    )

    print(
        "WAGER AUDIT"
    )

    print(
        "-" * 72
    )

    for pick in audit_picks:

        label = _label(
            pick
        )

        # Official reconciliation removed the provisional
        # event_id, so reconstruct the event from the existing
        # ESPN resolver without mutating the real row.
        resolver_pick = dict(
            pick
        )

        resolver_pick[
            "event_id"
        ] = None

        resolution = (
            resolve_event_detailed(
                resolver_pick,
                events,
            )
        )

        event = (
            resolution.get(
                "event"
            )
        )

        if (
            event
            is None
        ):
            status = (
                "ESPN_UNRESOLVED"
            )

            counts[
                status
            ] += 1

            print(
                (
                    f"{status}: "
                    f"{label} | "
                    f"{resolution.get('method')} | "
                    f"{resolution.get('reason')}"
                )
            )

            continue

        espn_id = (
            event_id(
                event
            )
        )

        # ----------------------------------------------------
        # ESPN EVENT -> ODDSPAPI FIXTURE
        # ----------------------------------------------------

        if (
            espn_id
            not in event_fixture_cache
        ):
            event_fixture_cache[
                espn_id
            ] = _match_fixture(
                api,
                event,
            )

        (
            fixture,
            fixture_status,
            fixture_detail,
        ) = (
            event_fixture_cache[
                espn_id
            ]
        )

        if (
            fixture
            is None
        ):
            counts[
                fixture_status
            ] += 1

            print(
                (
                    f"{fixture_status}: "
                    f"{label}"
                )
            )

            if fixture_detail:
                print(
                    "  ",
                    fixture_detail,
                )

            continue

        fixture_id = str(
            fixture.get(
                "fixtureId"
            )
        )

        matched_fixture_ids.add(
            fixture_id
        )

        # Include selected-side identity in the cache key.
        #
        # This prevents an ambiguous abbreviation from borrowing
        # another picker's side resolution for the same game.
        cache_key = (
            fixture_id,

            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            ),

            _clean(
                side_identity(
                    pick
                )
                or pick.get(
                    "team"
                )
            ).lower(),

            safe_float(
                pick.get(
                    "line"
                )
            ),
        )

        if (
            cache_key
            not in history_result_cache
        ):
            try:
                history_result_cache[
                    cache_key
                ] = _audit_pick(
                    api,
                    pick,
                    event,
                    fixture,
                )

            except requests.HTTPError as exc:

                response_status = getattr(
                    exc.response,
                    "status_code",
                    "unknown",
                )

                history_result_cache[
                    cache_key
                ] = (
                    "API_ERROR",
                    (
                        "HTTP_"
                        f"{response_status}"
                    ),
                    None,
                )

            except requests.RequestException as exc:

                history_result_cache[
                    cache_key
                ] = (
                    "API_ERROR",
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    None,
                )

            except Exception as exc:

                history_result_cache[
                    cache_key
                ] = (
                    "VALIDATOR_ERROR",
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    None,
                )

        (
            status,
            reason,
            detail,
        ) = (
            history_result_cache[
                cache_key
            ]
        )

        counts[
            status
        ] += 1

        print(
            (
                f"{status}: "
                f"{label} | "
                f"ESPN {espn_id} "
                f"→ OddsPapi {fixture_id} | "
                f"{reason}"
            )
        )

        if detail:
            print(
                "  ",
                detail,
            )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print(
        "-" * 72
    )

    print(
        (
            "DRAFTKINGS HISTORICAL "
            "AUDIT SUMMARY"
        )
    )

    print(
        "-" * 72
    )

    print(
        (
            f"Eligible Week "
            f"{AUDIT_OFFICIAL_WEEK} "
            f"wagers: "
            f"{len(audit_picks)}"
        )
    )

    print(
        (
            "Unique OddsPapi "
            "fixtures matched: "
            f"{len(matched_fixture_ids)}"
        )
    )

    for key in sorted(
        counts
    ):
        print(
            (
                f"{key}: "
                f"{counts[key]}"
            )
        )

    print(
        "-" * 72
    )

    print(
        (
            "Observation mode made "
            "NO changes to picks.json."
        )
    )


if __name__ == "__main__":
    validate_sportsbook()
