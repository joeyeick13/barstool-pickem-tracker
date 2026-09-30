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

# OddsPapi:
# NCAA, Regular Season
NCAA_TOURNAMENT_ID = 27653

HTTP_TIMEOUT = 90

# ESPN kickoff -> OddsPapi fixture matching.
#
# We first require BOTH teams to match and then use kickoff
# time only as an additional constraint.
FIXTURE_TIME_TOLERANCE_MINUTES = 90
FIXTURE_QUERY_PADDING_HOURS = 3

# Historical DraftKings closing-board reconstruction.
#
# A quote older than this is not treated as a reliable
# pre-kickoff board.
SNAPSHOT_MAX_AGE_HOURS = 12

# OddsPapi documented endpoint cooldowns.
FIXTURES_COOLDOWN_SECONDS = 2.10
MARKETS_COOLDOWN_SECONDS = 1.10
HISTORICAL_COOLDOWN_SECONDS = 5.10

# ------------------------------------------------------------
# HISTORICAL AUDIT
# ------------------------------------------------------------
#
# Default is Week 4 for the current audit.
#
# This can later be overridden without changing code:
#
# SPORTSBOOK_AUDIT_WEEK=5
#
# IMPORTANT:
#
# This module is READ ONLY.
#
# It never saves picks.json.
# It never changes official results.
# It never changes event IDs.
# It never changes selections.
# ------------------------------------------------------------

AUDIT_OFFICIAL_WEEK = int(
    os.getenv(
        "SPORTSBOOK_AUDIT_WEEK",
        "4",
    )
)


# ============================================================
# TIME HELPERS
# ============================================================

def _parse_dt(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value)
            .strip()
            .replace(
                "Z",
                "+00:00",
            )
        )

    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def _iso_utc(value):
    return (
        value
        .astimezone(
            timezone.utc
        )
        .strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


# ============================================================
# PICK HELPERS
# ============================================================

def _week_number(pick):
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
        str(
            pick.get(
                "sport"
            )
            or ""
        )
        .strip()
        .upper()
        in {
            "CFB",
            "NCAAF",
        }
    )


def _is_official(pick):
    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def _eligible(pick):
    if not _is_cfb(
        pick
    ):
        return False

    week = _week_number(
        pick
    )

    # Current historical audit.
    if (
        week
        == AUDIT_OFFICIAL_WEEK
    ):
        return True

    # Normal behavior:
    # never sportsbook-audit already-official historical
    # rows outside the explicitly requested audit week.
    return not _is_official(
        pick
    )


def _label(pick):
    return (
        f"W{pick.get('week')} | "
        f"{pick.get('picker')} | "
        f"{pick.get('selection')}"
    )


# ============================================================
# MARKET HELPERS
# ============================================================

def _market_period_key(
    pick
):
    value = str(
        market_period(
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
        )
        or ""
    ).strip().lower()

    return (
        value
        .replace(
            " ",
            "",
        )
        .replace(
            "_",
            "",
        )
        .replace(
            "-",
            "",
        )
    )


def _period_matches(
    pick,
    meta,
):
    wanted = _market_period_key(
        pick
    )

    actual = str(
        meta.get(
            "period"
        )
        or ""
    ).strip().lower()

    actual_key = (
        actual
        .replace(
            " ",
            "",
        )
        .replace(
            "_",
            "",
        )
        .replace(
            "-",
            "",
        )
    )

    # Full game.
    if wanted in {
        "",
        "full",
        "fullgame",
        "game",
        "result",
        "fulltime",
    }:
        return actual_key in {
            "",
            "full",
            "fullgame",
            "game",
            "match",
            "result",
            "fulltime",
        }

    # First half.
    if wanted in {
        "1h",
        "firsthalf",
        "half1",
    }:
        return actual_key in {
            "1h",
            "firsthalf",
            "half1",
            "p1",
        }

    # First quarter.
    if wanted in {
        "1q",
        "firstquarter",
        "quarter1",
    }:
        return actual_key in {
            "1q",
            "firstquarter",
            "quarter1",
            "q1",
        }

    return (
        actual_key
        == wanted
    )


def _market_family(
    meta
):
    name = str(
        meta.get(
            "marketName"
        )
        or ""
    ).lower()

    market_type = str(
        meta.get(
            "marketType"
        )
        or ""
    ).lower()

    if (
        "team total"
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
        or "totals"
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

    # --------------------------------------------------------
    # GENERIC GET
    # --------------------------------------------------------

    def get(
        self,
        endpoint,
        cooldown=0.0,
        **params,
    ):
        query = dict(
            params
        )

        query[
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
                    params=query,
                    timeout=HTTP_TIMEOUT,
                )
            )

            # OddsPapi uses 404 for an empty fixture window.
            if (
                response.status_code
                == 404
            ):
                if cooldown:
                    time.sleep(
                        cooldown
                    )

                return None

            # Respect provider throttling.
            if (
                response.status_code
                == 429
            ):
                retry_seconds = (
                    5.0
                    * attempt
                )

                try:
                    payload = (
                        response.json()
                    )

                    retry_ms = (
                        (
                            payload.get(
                                "error"
                            )
                            or {}
                        )
                        .get(
                            "retryMs"
                        )
                    )

                    if (
                        retry_ms
                        is not None
                    ):
                        retry_seconds = max(
                            (
                                float(
                                    retry_ms
                                )
                                / 1000.0
                                + 0.5
                            ),
                            retry_seconds,
                        )

                except Exception:
                    pass

                time.sleep(
                    retry_seconds
                )

                last_error = (
                    requests.HTTPError(
                        (
                            "429 from "
                            f"OddsPapi /{endpoint}"
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

    # --------------------------------------------------------
    # FIXTURES
    # --------------------------------------------------------

    def fixtures_near(
        self,
        kickoff,
    ):
        start = (
            kickoff
            - timedelta(
                hours=
                    FIXTURE_QUERY_PADDING_HOURS
            )
        )

        end = (
            kickoff
            + timedelta(
                hours=
                    FIXTURE_QUERY_PADDING_HOURS
            )
        )

        key = (
            _iso_utc(
                start
            ),
            _iso_utc(
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
                    FIXTURES_COOLDOWN_SECONDS,
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

    # --------------------------------------------------------
    # MARKET CATALOG
    # --------------------------------------------------------

    def markets(
        self
    ):
        if (
            self._markets
            is None
        ):
            payload = self.get(
                "markets",
                cooldown=
                    MARKETS_COOLDOWN_SECONDS,
                language="en",
            )

            if not isinstance(
                payload,
                list,
            ):
                raise RuntimeError(
                    (
                        "OddsPapi /markets "
                        "did not return "
                        "a JSON list."
                    )
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

    # --------------------------------------------------------
    # HISTORICAL ODDS
    # --------------------------------------------------------

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
# ESPN EVENT -> TEAM PAIR
# ============================================================

def _espn_home_away(
    event
):
    away = None
    home = None

    for competitor in competitors(
        event
    ):
        location = (
            competitor_home_away(
                competitor
            )
        )

        if (
            location
            == "away"
        ):
            away = (
                competitor_display_name(
                    competitor
                )
            )

        elif (
            location
            == "home"
        ):
            home = (
                competitor_display_name(
                    competitor
                )
            )

    if (
        away
        and home
    ):
        return (
            away,
            home,
        )

    # Fail-soft display fallback.
    event_competitors = (
        competitors(
            event
        )
    )

    if (
        len(
            event_competitors
        )
        == 2
    ):
        return (
            competitor_display_name(
                event_competitors[0]
            ),
            competitor_display_name(
                event_competitors[1]
            ),
        )

    return (
        None,
        None,
    )


# ============================================================
# ODDSPAPI FIXTURE IDENTITY
# ============================================================

def _fixture_names(
    fixture
):
    participant1 = str(
        fixture.get(
            "participant1Name"
        )
        or fixture.get(
            "participant1ShortName"
        )
        or fixture.get(
            "participant1Abbr"
        )
        or ""
    ).strip()

    participant2 = str(
        fixture.get(
            "participant2Name"
        )
        or fixture.get(
            "participant2ShortName"
        )
        or fixture.get(
            "participant2Abbr"
        )
        or ""
    ).strip()

    return (
        participant1,
        participant2,
    )


def _name_equivalent(
    left,
    right,
):
    if (
        not left
        or not right
    ):
        return False

    return teams_equivalent(
        left,
        right,
    )


def _fixture_pair_matches(
    away,
    home,
    fixture,
):
    participant1, participant2 = (
        _fixture_names(
            fixture
        )
    )

    if (
        not participant1
        or not participant2
    ):
        return False

    # OddsPapi participant order is not assumed to equal
    # ESPN away/home order.
    return (
        (
            _name_equivalent(
                away,
                participant1,
            )
            and
            _name_equivalent(
                home,
                participant2,
            )
        )
        or
        (
            _name_equivalent(
                away,
                participant2,
            )
            and
            _name_equivalent(
                home,
                participant1,
            )
        )
    )


# ============================================================
# ESPN EVENT -> ODDSPAPI FIXTURE
# ============================================================

def _match_oddspapi_fixture(
    api,
    espn_event,
):
    kickoff = event_datetime(
        espn_event
    )

    if (
        kickoff
        is None
    ):
        return (
            None,
            "ESPN_EVENT_WITHOUT_KICKOFF",
            None,
        )

    away, home = (
        _espn_home_away(
            espn_event
        )
    )

    if (
        not away
        or not home
    ):
        return (
            None,
            "ESPN_EVENT_WITHOUT_TWO_TEAMS",
            None,
        )

    fixtures = api.fixtures_near(
        kickoff
    )

    candidates = []

    for fixture in fixtures:

        try:
            fixture_sport = int(
                fixture.get(
                    "sportId"
                )
                or 0
            )

        except Exception:
            fixture_sport = 0

        try:
            fixture_tournament = int(
                fixture.get(
                    "tournamentId"
                )
                or 0
            )

        except Exception:
            fixture_tournament = 0

        if (
            fixture_sport
            != SPORT_ID
        ):
            continue

        if (
            fixture_tournament
            != NCAA_TOURNAMENT_ID
        ):
            continue

        fixture_time = _parse_dt(
            fixture.get(
                "startTime"
            )
        )

        if (
            fixture_time
            is None
        ):
            continue

        delta_minutes = (
            abs(
                (
                    fixture_time
                    - kickoff
                )
                .total_seconds()
            )
            / 60.0
        )

        if (
            delta_minutes
            >
            FIXTURE_TIME_TOLERANCE_MINUTES
        ):
            continue

        # Critical safety condition:
        # BOTH teams must match.
        if not _fixture_pair_matches(
            away,
            home,
            fixture,
        ):
            continue

        candidates.append(
            (
                delta_minutes,
                fixture,
            )
        )

    if not candidates:
        return (
            None,
            "ODDSPAPI_FIXTURE_NOT_FOUND",
            {
                "espn_event_id":
                    event_id(
                        espn_event
                    ),
                "espn_matchup":
                    event_matchup_text(
                        espn_event
                    ),
                "espn_kickoff":
                    kickoff.isoformat(),
                "oddspapi_candidates_in_window":
                    len(
                        fixtures
                    ),
            },
        )

    candidates.sort(
        key=lambda row:
            row[0]
    )

    # If two different OddsPapi fixtures match the same teams
    # at the exact same time, fail closed.
    if (
        len(
            candidates
        )
        > 1
    ):
        first_delta = (
            candidates[0][0]
        )

        equally_close = [
            row

            for row
            in candidates

            if (
                abs(
                    row[0]
                    - first_delta
                )
                < 0.01
            )
        ]

        if (
            len(
                equally_close
            )
            > 1
        ):
            return (
                None,
                "ODDSPAPI_FIXTURE_AMBIGUOUS",
                {
                    "espn_event_id":
                        event_id(
                            espn_event
                        ),
                    "espn_matchup":
                        event_matchup_text(
                            espn_event
                        ),
                    "fixture_ids":
                        [
                            row[1].get(
                                "fixtureId"
                            )

                            for row
                            in equally_close
                        ],
                },
            )

    delta, fixture = (
        candidates[0]
    )

    participant1, participant2 = (
        _fixture_names(
            fixture
        )
    )

    return (
        fixture,
        "MATCHED",
        {
            "espn_event_id":
                event_id(
                    espn_event
                ),
            "espn_matchup":
                event_matchup_text(
                    espn_event
                ),
            "espn_kickoff":
                kickoff.isoformat(),
            "oddspapi_fixture_id":
                fixture.get(
                    "fixtureId"
                ),
            "oddspapi_matchup":
                (
                    f"{participant1} "
                    f"vs "
                    f"{participant2}"
                ),
            "kickoff_delta_minutes":
                round(
                    delta,
                    2,
                ),
        },
    )


# ============================================================
# HISTORICAL RESPONSE HELPERS
# ============================================================

def _history_book(
    history
):
    books = (
        history.get(
            "bookmakers"
        )
        or {}
    )

    if not isinstance(
        books,
        dict,
    ):
        return {}

    return (
        books.get(
            BOOKMAKER
        )
        or {}
    )


def _history_markets(
    history
):
    book = _history_book(
        history
    )

    markets = (
        book.get(
            "markets"
        )
        or {}
    )

    if not isinstance(
        markets,
        dict,
    ):
        return {}

    return markets


def _snapshots(
    outcome
):
    players = (
        outcome.get(
            "players"
        )
        or {}
    )

    values = (
        players.get(
            "0"
        )
    )

    # Historical odds:
    # players["0"] is a LIST of snapshots.
    if isinstance(
        values,
        list,
    ):
        return [
            item

            for item
            in values

            if isinstance(
                item,
                dict,
            )
        ]

    # Defensive compatibility.
    if isinstance(
        values,
        dict,
    ):
        return [
            values
        ]

    return []


def _snapshot_at_or_before(
    outcome,
    target,
):
    candidates = []

    for snapshot in _snapshots(
        outcome
    ):
        created = _parse_dt(
            snapshot.get(
                "createdAt"
            )
        )

        price = safe_float(
            snapshot.get(
                "price"
            )
        )

        if (
            created
            is None
            or price
            is None
            or price
            <= 1.0
        ):
            continue

        if (
            created
            <= target
        ):
            candidates.append(
                (
                    created,
                    price,
                    snapshot,
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda row:
            row[0]
    )

    return (
        candidates[-1]
    )


# ============================================================
# MARKET CATALOG HELPERS
# ============================================================

def _outcome_name(
    meta,
    outcome_id,
):
    for outcome in (
        meta.get(
            "outcomes"
        )
        or []
    ):
        if (
            str(
                outcome.get(
                    "outcomeId"
                )
            )
            ==
            str(
                outcome_id
            )
        ):
            return str(
                outcome.get(
                    "outcomeName"
                )
                or ""
            ).strip()

    return ""


def _two_sided_market_at(
    history_market,
    meta,
    target,
):
    rows = []

    outcomes = (
        history_market.get(
            "outcomes"
        )
        or {}
    )

    for (
        outcome_id,
        outcome,
    ) in outcomes.items():

        if not isinstance(
            outcome,
            dict,
        ):
            continue

        snapshot = (
            _snapshot_at_or_before(
                outcome,
                target,
            )
        )

        if (
            snapshot
            is None
        ):
            continue

        (
            created,
            price,
            raw,
        ) = snapshot

        # We want a usable two-sided board.
        if (
            raw.get(
                "active"
            )
            is False
        ):
            continue

        rows.append(
            {
                "outcome_id":
                    str(
                        outcome_id
                    ),
                "outcome_name":
                    _outcome_name(
                        meta,
                        outcome_id,
                    ),
                "created_at":
                    created,
                "price":
                    price,
            }
        )

    if (
        len(
            rows
        )
        != 2
    ):
        return None

    newest = max(
        row[
            "created_at"
        ]
        for row
        in rows
    )

    oldest = min(
        row[
            "created_at"
        ]
        for row
        in rows
    )

    # Both sides should describe essentially the same board.
    if (
        newest
        - oldest
        >
        timedelta(
            minutes=30
        )
    ):
        return None

    # Reject stale historical markets.
    if (
        target
        - newest
        >
        timedelta(
            hours=
                SNAPSHOT_MAX_AGE_HOURS
        )
    ):
        return None

    implied = sum(
        1.0
        / row[
            "price"
        ]

        for row
        in rows
    )

    hold = (
        implied
        - 1.0
    )

    # Reject obviously incoherent two-sided price pairs.
    if (
        hold
        < -0.05
        or hold
        > 0.25
    ):
        return None

    # Main spread/total lines are generally the rungs priced
    # closest to even money.
    evenness = sum(
        abs(
            row[
                "price"
            ]
            - 2.0
        )

        for row
        in rows
    )

    return {
        "rows":
            rows,
        "newest":
            newest,
        "oldest":
            oldest,
        "hold":
            hold,
        "evenness":
            evenness,
    }


def _catalog_for_history(
    api,
    history,
    pick,
):
    family = base_market(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    catalog = api.markets()

    results = []

    for (
        market_id,
        history_market,
    ) in _history_markets(
        history
    ).items():

        meta = catalog.get(
            str(
                market_id
            )
        )

        if not meta:
            continue

        try:
            market_sport = int(
                meta.get(
                    "sportId"
                )
                or 0
            )

        except Exception:
            market_sport = 0

        if (
            market_sport
            != SPORT_ID
        ):
            continue

        if (
            _market_family(
                meta
            )
            != family
        ):
            continue

        if not _period_matches(
            pick,
            meta,
        ):
            continue

        results.append(
            (
                str(
                    market_id
                ),
                meta,
                history_market,
            )
        )

    return results


# ============================================================
# PARTICIPANT / SPREAD HELPERS
# ============================================================

def _selected_participant_number(
    pick,
    fixture,
):
    selected = side_identity(
        pick
    )

    if not selected:
        return None

    participant1, participant2 = (
        _fixture_names(
            fixture
        )
    )

    match1 = _name_equivalent(
        selected,
        participant1,
    )

    match2 = _name_equivalent(
        selected,
        participant2,
    )

    if (
        match1
        and not match2
    ):
        return "1"

    if (
        match2
        and not match1
    ):
        return "2"

    return None


def _participant_line(
    meta,
    participant_number,
):
    handicap = safe_float(
        meta.get(
            "handicap"
        )
    )

    if (
        handicap
        is None
    ):
        return None

    # OddsPapi handicap is expressed from participant 1's
    # perspective. Participant 2 receives the reciprocal line.
    if (
        participant_number
        == "1"
    ):
        return handicap

    return -handicap


def _market_has_participant(
    board,
    participant_number,
):
    return any(
        (
            str(
                row.get(
                    "outcome_name"
                )
                or ""
            )
            .strip()
            ==
            participant_number
        )

        for row
        in board[
            "rows"
        ]
    )


# ============================================================
# CLOSING MARKET RECONSTRUCTION
# ============================================================

def _best_closing_market(
    api,
    history,
    pick,
    fixture,
    kickoff,
):
    family = base_market(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    candidates = []

    participant_number = None

    if family in {
        "SPREAD",
        "MONEYLINE",
    }:
        participant_number = (
            _selected_participant_number(
                pick,
                fixture,
            )
        )

        if (
            participant_number
            is None
        ):
            return (
                None,
                (
                    "SELECTED_TEAM_NOT_IN_"
                    "ODDSPAPI_FIXTURE"
                ),
            )

    for (
        market_id,
        meta,
        history_market,
    ) in _catalog_for_history(
        api,
        history,
        pick,
    ):

        board = (
            _two_sided_market_at(
                history_market,
                meta,
                (
                    kickoff
                    - timedelta(
                        seconds=1
                    )
                ),
            )
        )

        if (
            board
            is None
        ):
            continue

        if (
            participant_number
            is not None
            and not _market_has_participant(
                board,
                participant_number,
            )
        ):
            continue

        candidates.append(
            {
                "market_id":
                    market_id,
                "meta":
                    meta,
                "board":
                    board,
                "participant_number":
                    participant_number,
            }
        )

    if not candidates:
        return (
            None,
            (
                "NO_USABLE_PREKICK_"
                "DRAFTKINGS_MARKET"
            ),
        )

    # --------------------------------------------------------
    # MAIN-LINE PROXY
    # --------------------------------------------------------
    #
    # OddsPapi historical snapshots do not expose the live
    # mainLine flag documented on /odds.
    #
    # Therefore, for the historical audit, choose the
    # two-sided spread/total rung whose two prices are closest
    # to even money immediately before kickoff.
    #
    # This is intentionally used as an independent closing-line
    # sanity check, NOT as proof of the original X-post line.
    # --------------------------------------------------------

    candidates.sort(
        key=lambda row: (
            row[
                "board"
            ][
                "evenness"
            ],
            -row[
                "board"
            ][
                "newest"
            ].timestamp(),
        )
    )

    return (
        candidates[0],
        "MATCHED",
    )


# ============================================================
# SPREAD AUDIT
# ============================================================

def _spread_result(
    api,
    history,
    pick,
    fixture,
    kickoff,
):
    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if (
        x_line
        is None
    ):
        return (
            "REVIEW",
            "MISSING_X_SPREAD_LINE",
            None,
        )

    (
        chosen,
        reason,
    ) = _best_closing_market(
        api,
        history,
        pick,
        fixture,
        kickoff,
    )

    if (
        chosen
        is None
    ):
        return (
            "UNAVAILABLE",
            reason,
            None,
        )

    dk_line = _participant_line(
        chosen[
            "meta"
        ],
        chosen[
            "participant_number"
        ],
    )

    if (
        dk_line
        is None
    ):
        return (
            "UNAVAILABLE",
            (
                "DRAFTKINGS_MARKET_"
                "WITHOUT_HANDICAP"
            ),
            None,
        )

    magnitude_difference = abs(
        abs(
            x_line
        )
        -
        abs(
            dk_line
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
            == 0
            and dk_line
            == 0
        )
        or (
            x_line
            * dk_line
            > 0
        )
    )

    detail = {
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

        "market_handicap_participant1":
            chosen[
                "meta"
            ].get(
                "handicap"
            ),

        "snapshot_at":
            chosen[
                "board"
            ][
                "newest"
            ].isoformat(),

        "market_hold":
            round(
                chosen[
                    "board"
                ][
                    "hold"
                ],
                5,
            ),

        "magnitude_difference":
            round(
                magnitude_difference,
                3,
            ),

        "note":
            (
                "Closing-line proxy only; "
                "official reconciliation "
                "no longer contains the "
                "original X card timestamp."
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

    # User-approved sign discrepancy threshold:
    # opposite sign + magnitude difference <= 3.
    #
    # HISTORICAL AUDIT MODE DOES NOT MUTATE.
    #
    # We flag it so it can be inspected before any correction.
    if (
        magnitude_difference
        <= 3.0
    ):
        return (
            "SIGN_REVIEW",
            (
                "OPPOSITE_SIGN_WITHIN_3_"
                "VS_DK_CLOSE"
            ),
            detail,
        )

    return (
        "REVIEW",
        (
            "OPPOSITE_SIGN_OVER_3_"
            "VS_DK_CLOSE"
        ),
        detail,
    )


# ============================================================
# TOTAL AUDIT
# ============================================================

def _total_result(
    api,
    history,
    pick,
    fixture,
    kickoff,
):
    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if (
        x_line
        is None
    ):
        return (
            "REVIEW",
            "MISSING_X_TOTAL_LINE",
            None,
        )

    (
        chosen,
        reason,
    ) = _best_closing_market(
        api,
        history,
        pick,
        fixture,
        kickoff,
    )

    if (
        chosen
        is None
    ):
        return (
            "UNAVAILABLE",
            reason,
            None,
        )

    dk_line = safe_float(
        chosen[
            "meta"
        ].get(
            "handicap"
        )
    )

    if (
        dk_line
        is None
    ):
        return (
            "UNAVAILABLE",
            (
                "DRAFTKINGS_MARKET_"
                "WITHOUT_TOTAL"
            ),
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
                "board"
            ][
                "newest"
            ].isoformat(),

        "market_hold":
            round(
                chosen[
                    "board"
                ][
                    "hold"
                ],
                5,
            ),

        "note":
            (
                "Totals are observation-only. "
                "A different closing total does "
                "not prove the X card was "
                "extracted incorrectly."
            ),
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

def _moneyline_result(
    api,
    history,
    pick,
    fixture,
    kickoff,
):
    (
        chosen,
        reason,
    ) = _best_closing_market(
        api,
        history,
        pick,
        fixture,
        kickoff,
    )

    if (
        chosen
        is None
    ):
        return (
            "UNAVAILABLE",
            reason,
            None,
        )

    return (
        "CONFIRMED",
        (
            "SELECTED_TEAM_PRESENT_"
            "IN_DK_MONEYLINE"
        ),
        {
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
                    "board"
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
    espn_event,
    fixture,
):
    kickoff = event_datetime(
        espn_event
    )

    if (
        kickoff
        is None
    ):
        return (
            "UNAVAILABLE",
            "NO_ESPN_KICKOFF",
            None,
        )

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

    if not _history_book(
        history
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
        return _spread_result(
            api,
            history,
            pick,
            fixture,
            kickoff,
        )

    if family in {
        "TOTAL",
        "TEAM_TOTAL",
    }:
        return _total_result(
            api,
            history,
            pick,
            fixture,
            kickoff,
        )

    if (
        family
        == "MONEYLINE"
    ):
        return _moneyline_result(
            api,
            history,
            pick,
            fixture,
            kickoff,
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
# MAIN VALIDATOR
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

    print(
        (
            "Historical comparison uses "
            "the final usable pre-kickoff "
            "DraftKings board because "
            "official reconciliation "
            "replaced the original "
            f"Week {AUDIT_OFFICIAL_WEEK} "
            "X timestamp."
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

    eligible = [
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
        )
    ]

    audit_picks = [
        pick

        for pick
        in eligible

        if (
            _week_number(
                pick
            )
            ==
            AUDIT_OFFICIAL_WEEK
        )
    ]

    if not audit_picks:

        print(
            "No eligible wagers found."
        )

        return

    # Current tracker season.
    season_year = 2026

    print()

    print(
        (
            "Rebuilding authoritative "
            "ESPN slate..."
        )
    )

    # --------------------------------------------------------
    # ESPN IS THE GAME-IDENTITY AUTHORITY
    # --------------------------------------------------------
    #
    # Do not ask OddsPapi to interpret raw abbreviations like:
    #
    # UT
    # UM
    # USC
    # TU
    # OSU
    #
    # The existing shared ESPN resolver handles those using
    # matchup context and fails closed when unsafe.
    # --------------------------------------------------------

    espn_events = (
        build_complete_week_slate(
            audit_picks,
            season_year,
            AUDIT_OFFICIAL_WEEK,
        )
    )

    api = OddsPapi(
        api_key
    )

    counts = Counter()

    # ESPN event -> OddsPapi fixture.
    resolved_cache = {}

    # Unique OddsPapi fixtures successfully linked.
    matched_fixture_ids = set()

    # Same wager appearing for multiple pickers can reuse its
    # sportsbook comparison result.
    history_result_cache = {}

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

        # ----------------------------------------------------
        # STEP A — RECONSTRUCT ESPN EVENT
        # ----------------------------------------------------
        #
        # Official reconciliation deliberately removed the
        # provisional event_id.
        #
        # Use a COPY of the pick and explicitly clear event_id
        # so the shared resolver reconstructs the exact event
        # from matchup/team context.
        #
        # Original picks.json is untouched.
        # ----------------------------------------------------

        resolver_pick = dict(
            pick
        )

        resolver_pick[
            "event_id"
        ] = None

        resolution = (
            resolve_event_detailed(
                resolver_pick,
                espn_events,
            )
        )

        espn_event = (
            resolution.get(
                "event"
            )
        )

        if (
            espn_event
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

        espn_id = event_id(
            espn_event
        )

        # ----------------------------------------------------
        # STEP B — ESPN EVENT -> ODDSPAPI FIXTURE
        # ----------------------------------------------------

        if (
            espn_id
            in resolved_cache
        ):
            (
                fixture,
                fixture_status,
                fixture_detail,
            ) = (
                resolved_cache[
                    espn_id
                ]
            )

        else:
            (
                fixture,
                fixture_status,
                fixture_detail,
            ) = (
                _match_oddspapi_fixture(
                    api,
                    espn_event,
                )
            )

            resolved_cache[
                espn_id
            ] = (
                fixture,
                fixture_status,
                fixture_detail,
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

        # ----------------------------------------------------
        # STEP C — HISTORICAL DRAFTKINGS
        # ----------------------------------------------------

        cache_key = (
            fixture_id,
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            ),
            str(
                pick.get(
                    "side"
                )
                or ""
            ),
            safe_float(
                pick.get(
                    "line"
                )
            ),
        )

        if (
            cache_key
            in history_result_cache
        ):
            (
                status,
                reason,
                detail,
            ) = (
                history_result_cache[
                    cache_key
                ]
            )

        else:

            try:
                (
                    status,
                    reason,
                    detail,
                ) = _audit_pick(
                    api,
                    pick,
                    espn_event,
                    fixture,
                )

            except requests.HTTPError as exc:

                status = (
                    "API_ERROR"
                )

                response_status = getattr(
                    exc.response,
                    "status_code",
                    "unknown",
                )

                reason = (
                    f"HTTP_"
                    f"{response_status}"
                )

                detail = None

            except requests.RequestException as exc:

                status = (
                    "API_ERROR"
                )

                reason = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                detail = None

            except Exception as exc:

                status = (
                    "VALIDATOR_ERROR"
                )

                reason = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                detail = None

            history_result_cache[
                cache_key
            ] = (
                status,
                reason,
                detail,
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
            "Unique ESPN events "
            "resolved to OddsPapi: "
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
