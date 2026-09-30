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


# ============================================================
# CONFIG
# ============================================================

API_BASE = "https://api.oddspapi.io/v4"

BOOKMAKER = "draftkings"

SPORT_ID = 14

HTTP_TIMEOUT = 30

FIXTURE_WINDOW_HOURS = 18

ODDS_COOLDOWN_SECONDS = 0.55

# ============================================================
# TEMPORARY HISTORICAL AUDIT CONFIG
# ============================================================
#
# Week 4 has already been officially reconciled.
#
# Normally official rows are excluded from sportsbook
# validation. For this temporary audit, Week 4 is deliberately
# included so we can independently compare the original wagers
# against DraftKings.
#
# IMPORTANT:
# This file remains READ ONLY.
# It never saves picks.json and never modifies a wager.
# ============================================================

AUDIT_OFFICIAL_WEEK = 4


# ============================================================
# TIME HELPERS
# ============================================================

def _parse_dt(value):
    if not value:
        return None

    text = (
        str(value)
        .strip()
        .replace("Z", "+00:00")
    )

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
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
        self.odds_cache = {}

        self.markets = None

    def get(
        self,
        endpoint,
        **params,
    ):
        params[
            "apiKey"
        ] = self.api_key

        response = (
            self.session.get(
                f"{API_BASE}/{endpoint}",
                params=params,
                timeout=HTTP_TIMEOUT,
            )
        )

        response.raise_for_status()

        return response.json()

    def get_markets(self):
        if self.markets is None:
            payload = self.get(
                "markets",
                language="en",
            )

            self.markets = {
                str(
                    row.get(
                        "marketId"
                    )
                ): row
                for row in payload
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

        return self.markets

    def fixtures_near(
        self,
        game_time,
    ):
        center = _parse_dt(
            game_time
        )

        if center is None:
            return []

        start = (
            center
            - timedelta(
                hours=FIXTURE_WINDOW_HOURS
            )
        )

        end = (
            center
            + timedelta(
                hours=FIXTURE_WINDOW_HOURS
            )
        )

        key = (
            start.date().isoformat(),
            end.date().isoformat(),
        )

        if key not in self.fixture_cache:
            self.fixture_cache[
                key
            ] = self.get(
                "fixtures",
                sportId=SPORT_ID,
                **{
                    "from":
                        start
                        .isoformat()
                        .replace(
                            "+00:00",
                            "Z",
                        ),

                    "to":
                        end
                        .isoformat()
                        .replace(
                            "+00:00",
                            "Z",
                        ),
                },
                hasOdds="true",
                bookmakers=BOOKMAKER,
            )

        return self.fixture_cache[
            key
        ]

    def odds(
        self,
        fixture_id,
    ):
        fixture_id = str(
            fixture_id
        )

        if (
            fixture_id
            not in self.odds_cache
        ):
            self.odds_cache[
                fixture_id
            ] = self.get(
                "odds",
                fixtureId=fixture_id,
                bookmakers=BOOKMAKER,
                language="en",
                verbosity=3,
            )

            time.sleep(
                ODDS_COOLDOWN_SECONDS
            )

        return self.odds_cache[
            fixture_id
        ]


# ============================================================
# PICK HELPERS
# ============================================================

def _is_official(
    pick,
):
    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def _week_number(
    pick,
):
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


def _is_cfb(
    pick,
):
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


def _eligible_for_validation(
    pick,
):
    if not _is_cfb(
        pick
    ):
        return False

    week = _week_number(
        pick
    )

    # Temporary historical audit:
    #
    # ALL Week 4 CFB wagers are eligible, even though they have
    # already been officially reconciled.
    if week == AUDIT_OFFICIAL_WEEK:
        return True

    # Normal production behavior remains unchanged for every
    # other week: official rows are authoritative and skipped.
    if _is_official(
        pick
    ):
        return False

    return True


# ============================================================
# FIXTURE IDENTITY HELPERS
# ============================================================

def _fixture_participants(
    odds,
):
    return (
        odds.get(
            "participant1Name"
        ),
        odds.get(
            "participant2Name"
        ),
    )


def _pick_identity_hints(
    pick,
):
    hints = []

    team = pick.get(
        "team"
    )

    opponent = pick.get(
        "opponent"
    )

    if team:
        hints.append(
            team
        )

    if opponent:
        hints.append(
            opponent
        )

    return hints


def _pair_matches(
    hints,
    team1,
    team2,
):
    if len(
        hints
    ) < 2:
        return False

    return (
        (
            teams_equivalent(
                hints[0],
                team1,
            )
            and
            teams_equivalent(
                hints[1],
                team2,
            )
        )
        or
        (
            teams_equivalent(
                hints[0],
                team2,
            )
            and
            teams_equivalent(
                hints[1],
                team1,
            )
        )
    )


def _single_hints_match(
    hints,
    team1,
    team2,
):
    if not hints:
        return False

    return all(
        any(
            teams_equivalent(
                hint,
                team,
            )
            for team
            in (
                team1,
                team2,
            )
        )
        for hint
        in hints
    )


# ============================================================
# FIXTURE MATCHING
# ============================================================

def _match_fixture(
    api,
    pick,
):
    expected_time = _parse_dt(
        pick.get(
            "game_time"
        )
    )

    if expected_time is None:
        return (
            None,
            "NO_GAME_TIME",
        )

    candidates = []

    for fixture in api.fixtures_near(
        expected_time
    ):
        fixture_id = fixture.get(
            "fixtureId"
        )

        if not fixture_id:
            continue

        try:
            odds = api.odds(
                fixture_id
            )

        except requests.RequestException:
            continue

        (
            team1,
            team2,
        ) = _fixture_participants(
            odds
        )

        if (
            not team1
            or not team2
        ):
            continue

        hints = _pick_identity_hints(
            pick
        )

        if len(
            hints
        ) >= 2:
            pair_ok = _pair_matches(
                hints,
                team1,
                team2,
            )

        else:
            pair_ok = _single_hints_match(
                hints,
                team1,
                team2,
            )

            if not pair_ok:
                matchup = str(
                    pick.get(
                        "event_matchup"
                    )
                    or pick.get(
                        "matchup"
                    )
                    or ""
                )

                if matchup:
                    pair_ok = (
                        team1.lower()
                        in matchup.lower()
                        and
                        team2.lower()
                        in matchup.lower()
                    )

        if not pair_ok:
            continue

        start = _parse_dt(
            odds.get(
                "startTime"
            )
            or fixture.get(
                "startTime"
            )
        )

        delta = (
            abs(
                (
                    start
                    - expected_time
                )
                .total_seconds()
            )
            if start is not None
            else float(
                "inf"
            )
        )

        candidates.append(
            (
                delta,
                odds,
            )
        )

    if not candidates:
        return (
            None,
            "FIXTURE_NOT_FOUND",
        )

    candidates.sort(
        key=lambda row: row[0]
    )

    if (
        len(candidates) > 1
        and candidates[0][0]
        == candidates[1][0]
    ):
        return (
            None,
            "AMBIGUOUS_FIXTURE",
        )

    return (
        candidates[0][1],
        "MATCHED",
    )


# ============================================================
# MARKET MATCHING
# ============================================================

def _period_ok(
    pick,
    market,
):
    wanted = str(
        market_period(
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
        )
        or ""
    ).lower()

    actual = str(
        market.get(
            "period"
        )
        or ""
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

    return False


def _market_family(
    market,
):
    name = str(
        market.get(
            "marketName"
        )
        or ""
    ).lower()

    market_type = str(
        market.get(
            "marketType"
        )
        or ""
    ).lower()

    if "team total" in name:
        return "TEAM_TOTAL"

    if (
        "handicap" in name
        or "spread" in name
        or "handicap"
        in market_type
    ):
        return "SPREAD"

    if (
        "over under" in name
        or "total" in name
        or "total"
        in market_type
    ):
        return "TOTAL"

    if (
        "winner" in name
        or "moneyline" in name
        or "money line" in name
    ):
        return "MONEYLINE"

    if market_type in {
        "winner",
        "moneyline",
        "money_line",
    }:
        return "MONEYLINE"

    return None


def _draftkings_markets(
    api,
    odds,
    pick,
):
    book = (
        (
            odds.get(
                "bookmakerOdds"
            )
            or {}
        )
        .get(
            BOOKMAKER
        )
        or {}
    )

    offered = (
        book.get(
            "markets"
        )
        or {}
    )

    catalog = (
        api.get_markets()
    )

    wanted_family = (
        base_market(
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
        )
    )

    matches = []

    for (
        market_id,
        market_payload,
    ) in offered.items():

        meta = catalog.get(
            str(
                market_id
            )
        )

        if not meta:
            continue

        if (
            _market_family(
                meta
            )
            != wanted_family
        ):
            continue

        if not _period_ok(
            pick,
            meta,
        ):
            continue

        matches.append(
            {
                "market_id":
                    str(
                        market_id
                    ),

                "name":
                    meta.get(
                        "marketName"
                    ),

                "period":
                    meta.get(
                        "period"
                    ),

                "handicap":
                    safe_float(
                        meta.get(
                            "handicap"
                        )
                    ),

                "outcomes":
                    meta.get(
                        "outcomes"
                    )
                    or [],

                "payload":
                    market_payload,
            }
        )

    return matches


# ============================================================
# SPREAD OBSERVATION
# ============================================================

def _spread_observation(
    api,
    odds,
    pick,
):
    selected = side_identity(
        pick
    )

    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if (
        not selected
        or x_line is None
    ):
        return (
            "REVIEW",
            "MISSING_SELECTED_TEAM_OR_LINE",
            None,
        )

    (
        participant1,
        participant2,
    ) = _fixture_participants(
        odds
    )

    if teams_equivalent(
        selected,
        participant1,
    ):
        selected_number = "1"

    elif teams_equivalent(
        selected,
        participant2,
    ):
        selected_number = "2"

    else:
        return (
            "REVIEW",
            "SELECTED_TEAM_NOT_IN_DK_FIXTURE",
            None,
        )

    rows = []

    for market in _draftkings_markets(
        api,
        odds,
        pick,
    ):
        handicap = market[
            "handicap"
        ]

        if handicap is None:
            continue

        selected_outcome = None

        for outcome in market[
            "outcomes"
        ]:
            outcome_name = str(
                outcome.get(
                    "outcomeName"
                )
                or ""
            ).strip().lower()

            if outcome_name in {
                selected_number,
                (
                    "participant "
                    + selected_number
                ),
            }:
                selected_outcome = str(
                    outcome.get(
                        "outcomeId"
                    )
                )

                break

        if selected_outcome is None:
            continue

        payload_outcome = (
            market[
                "payload"
            ]
            .get(
                "outcomes",
                {},
            )
            .get(
                selected_outcome,
                {},
            )
        )

        price = (
            (
                payload_outcome.get(
                    "players"
                )
                or {}
            )
            .get(
                "0"
            )
        )

        if (
            not isinstance(
                price,
                dict,
            )
            or price.get(
                "active"
            )
            is False
        ):
            continue

        draftkings_line = (
            handicap
            if selected_number == "1"
            else -handicap
        )

        rows.append(
            (
                draftkings_line,
                market,
                price,
            )
        )

    if not rows:
        return (
            "UNAVAILABLE",
            "NO_COMPARABLE_DK_SPREAD",
            None,
        )

    rows.sort(
        key=lambda row:
            abs(
                abs(
                    row[0]
                )
                - abs(
                    x_line
                )
            )
    )

    (
        draftkings_line,
        market,
        price,
    ) = rows[0]

    same_sign = (
        (
            x_line == 0
            and draftkings_line == 0
        )
        or (
            x_line
            * draftkings_line
            > 0
        )
    )

    magnitude_difference = abs(
        abs(
            x_line
        )
        - abs(
            draftkings_line
        )
    )

    detail = {
        "fixture_id":
            odds.get(
                "fixtureId"
            ),

        "participant1":
            participant1,

        "participant2":
            participant2,

        "market_id":
            market[
                "market_id"
            ],

        "market_name":
            market[
                "name"
            ],

        "market_period":
            market[
                "period"
            ],

        "x_line":
            x_line,

        "draftkings_line":
            draftkings_line,

        "magnitude_difference":
            round(
                magnitude_difference,
                3,
            ),

        "draftkings_price":
            (
                price.get(
                    "priceAmerican"
                )
                or price.get(
                    "price"
                )
            ),
    }

    if (
        abs(
            x_line
            - draftkings_line
        )
        <= 0.001
    ):
        return (
            "CONFIRMED",
            "EXACT_SPREAD_MATCH",
            detail,
        )

    if same_sign:
        return (
            "NO_CHANGE",
            "SAME_SIGN_LINE_MOVEMENT",
            detail,
        )

    if (
        magnitude_difference
        <= 3.0
    ):
        return (
            "WOULD_CORRECT",
            "OPPOSITE_SIGN_WITHIN_3_POINTS",
            detail,
        )

    return (
        "REVIEW",
        "OPPOSITE_SIGN_OVER_3_POINTS",
        detail,
    )


# ============================================================
# TOTAL / MONEYLINE OBSERVATION
# ============================================================

def _generic_observation(
    api,
    odds,
    pick,
):
    family = base_market(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    markets = (
        _draftkings_markets(
            api,
            odds,
            pick,
        )
    )

    if not markets:
        return (
            "UNAVAILABLE",
            (
                "NO_COMPARABLE_DK_"
                + str(
                    family
                )
            ),
            None,
        )

    x_line = safe_float(
        pick.get(
            "line"
        )
    )

    if (
        family
        in {
            "TOTAL",
            "TEAM_TOTAL",
        }
        and x_line is not None
    ):
        exact = [
            market
            for market
            in markets
            if (
                market[
                    "handicap"
                ]
                is not None
                and abs(
                    market[
                        "handicap"
                    ]
                    - x_line
                )
                <= 0.001
            )
        ]

        if exact:
            market = exact[0]

            return (
                "CONFIRMED",
                "EXACT_MARKET_NUMBER_AVAILABLE",
                {
                    "fixture_id":
                        odds.get(
                            "fixtureId"
                        ),

                    "market_id":
                        market[
                            "market_id"
                        ],

                    "market_name":
                        market[
                            "name"
                        ],

                    "market_period":
                        market[
                            "period"
                        ],

                    "x_line":
                        x_line,

                    "draftkings_line":
                        market[
                            "handicap"
                        ],
                },
            )

        return (
            "NO_CHANGE",
            "DK_MARKET_EXISTS_DIFFERENT_NUMBER",
            {
                "fixture_id":
                    odds.get(
                        "fixtureId"
                    ),

                "x_line":
                    x_line,

                "draftkings_lines":
                    sorted(
                        {
                            market[
                                "handicap"
                            ]
                            for market
                            in markets
                            if market[
                                "handicap"
                            ]
                            is not None
                        }
                    ),
            },
        )

    if family == "MONEYLINE":
        selected = side_identity(
            pick
        )

        participants = [
            odds.get(
                "participant1Name"
            ),
            odds.get(
                "participant2Name"
            ),
        ]

        if (
            selected
            and any(
                teams_equivalent(
                    selected,
                    team,
                )
                for team
                in participants
            )
        ):
            return (
                "CONFIRMED",
                "SELECTED_TEAM_IN_DK_FIXTURE",
                {
                    "fixture_id":
                        odds.get(
                            "fixtureId"
                        ),
                },
            )

    return (
        "UNAVAILABLE",
        "NO_SAFE_OBSERVATION_RULE",
        {
            "fixture_id":
                odds.get(
                    "fixtureId"
                ),
        },
    )


# ============================================================
# MAIN VALIDATION STAGE
# ============================================================

def validate_sportsbook():
    print()
    print(
        "=" * 72
    )
    print(
        "DRAFTKINGS VALIDATION — OBSERVATION MODE"
    )
    print(
        "=" * 72
    )

    print(
        f"Historical official audit enabled for Week "
        f"{AUDIT_OFFICIAL_WEEK}."
    )

    print(
        "READ ONLY: no wager data will be modified."
    )

    api_key = os.getenv(
        "ODDSPAPI_API_KEY"
    )

    if not api_key:
        print(
            "Skipping DraftKings validation: "
            "ODDSPAPI_API_KEY not configured."
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
            "picks.json must contain a list."
        )

    api = OddsPapi(
        api_key
    )

    counts = Counter()

    eligible_count = 0

    for pick in picks:
        if not isinstance(
            pick,
            dict,
        ):
            continue

        if not _eligible_for_validation(
            pick
        ):
            continue

        eligible_count += 1

        label = (
            f"W{pick.get('week')} | "
            f"{pick.get('picker')} | "
            f"{pick.get('selection')}"
        )

        if not pick.get(
            "event_id"
        ):
            counts[
                "NO_EVENT_ID"
            ] += 1

            print(
                f"NO_EVENT_ID: "
                f"{label}"
            )

            continue

        try:
            (
                odds,
                fixture_status,
            ) = _match_fixture(
                api,
                pick,
            )

            if odds is None:
                counts[
                    fixture_status
                ] += 1

                print(
                    f"{fixture_status}: "
                    f"{label}"
                )

                continue

            family = base_market(
                normalize_bet_type(
                    pick.get(
                        "bet_type"
                    )
                )
            )

            if family == "SPREAD":
                (
                    status,
                    reason,
                    detail,
                ) = _spread_observation(
                    api,
                    odds,
                    pick,
                )

            else:
                (
                    status,
                    reason,
                    detail,
                ) = _generic_observation(
                    api,
                    odds,
                    pick,
                )

            counts[
                status
            ] += 1

            print(
                f"{status}: "
                f"{label} | "
                f"{reason}"
            )

            if detail:
                print(
                    "  ",
                    detail,
                )

        except requests.HTTPError as exc:
            counts[
                "API_ERROR"
            ] += 1

            response_status = getattr(
                exc.response,
                "status_code",
                "unknown",
            )

            print(
                f"API_ERROR: "
                f"{label} | "
                f"HTTP {response_status}"
            )

        except requests.RequestException as exc:
            counts[
                "API_ERROR"
            ] += 1

            print(
                f"API_ERROR: "
                f"{label} | "
                f"{type(exc).__name__}"
            )

        except Exception as exc:
            counts[
                "VALIDATOR_ERROR"
            ] += 1

            print(
                f"VALIDATOR_ERROR: "
                f"{label} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    print()
    print(
        "-" * 72
    )
    print(
        "DRAFTKINGS OBSERVATION SUMMARY"
    )
    print(
        "-" * 72
    )

    print(
        f"Eligible wagers: "
        f"{eligible_count}"
    )

    for key in sorted(
        counts
    ):
        print(
            f"{key}: "
            f"{counts[key]}"
        )

    print(
        "-" * 72
    )

    print(
        "Observation mode made NO changes "
        "to picks.json."
    )


if __name__ == "__main__":
    validate_sportsbook()
