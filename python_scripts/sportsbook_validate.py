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

# NCAA, Regular Season
NCAA_TOURNAMENT_ID = 27653

HTTP_TIMEOUT = 60

# OddsPapi endpoint cooldowns.
GENERAL_COOLDOWN_SECONDS = 1.05
HISTORICAL_COOLDOWN_SECONDS = 5.10

# ============================================================
# TEMPORARY HISTORICAL AUDIT
# ============================================================
#
# Week 4 has already been officially reconciled, which removed
# the provisional ESPN event IDs.
#
# For this audit only, Week 4 official rows are allowed through
# and their OddsPapi fixture is rediscovered independently.
#
# THIS FILE IS READ ONLY.
#
# It never calls save_json().
# It never changes picks.json.
# It never changes results or official records.
# ============================================================

AUDIT_OFFICIAL_WEEK = 4

# Week 4 source posts were made before the games were played.
# We use the post timestamp to discover NCAA fixtures in the
# following several days.
HISTORICAL_LOOKBACK_DAYS = 1
HISTORICAL_LOOKAHEAD_DAYS = 5


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


def _iso_date(dt):
    return dt.date().isoformat()


# ============================================================
# BASIC HELPERS
# ============================================================

def _week_number(pick):
    try:
        return int(
            pick.get("week")
        )
    except (TypeError, ValueError):
        return None


def _is_official(pick):
    return bool(
        pick.get("official_reconciled")
    )


def _is_cfb(pick):
    return (
        str(
            pick.get("sport")
            or ""
        )
        .strip()
        .upper()
        in {
            "CFB",
            "NCAAF",
        }
    )


def _eligible_for_validation(pick):
    if not _is_cfb(pick):
        return False

    week = _week_number(pick)

    # Temporary Week 4 historical audit.
    if week == AUDIT_OFFICIAL_WEEK:
        return True

    # Normal production behavior for every other week.
    if _is_official(pick):
        return False

    return True


def _clean_text(value):
    return str(
        value or ""
    ).strip()


def _pick_matchup(pick):
    return (
        _clean_text(
            pick.get("event_matchup")
        )
        or _clean_text(
            pick.get("matchup")
        )
        or _clean_text(
            pick.get("game_matchup")
        )
        or _clean_text(
            pick.get("source_matchup_text")
        )
    )


def _pick_team(pick):
    return (
        _clean_text(
            pick.get("team")
        )
        or _clean_text(
            pick.get("source_team_text")
        )
    )


def _pick_opponent(pick):
    return _clean_text(
        pick.get("opponent")
    )


def _pick_reference_time(pick):
    # Prefer actual game time when it survived reconciliation.
    for key in (
        "game_time",
        "posted_at",
    ):
        dt = _parse_dt(
            pick.get(key)
        )

        if dt is not None:
            return dt

    return None


# ============================================================
# ODDSPAPI CLIENT
# ============================================================

class OddsPapi:
    def __init__(self, api_key):
        self.api_key = api_key

        self.session = requests.Session()

        self.fixture_cache = {}
        self.fixture_detail_cache = {}
        self.odds_cache = {}
        self.historical_cache = {}

        self.markets = None

    def get(
        self,
        endpoint,
        cooldown=0,
        **params,
    ):
        params["apiKey"] = self.api_key

        response = self.session.get(
            f"{API_BASE}/{endpoint}",
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        # Historical fixture windows can legitimately contain
        # no results.
        if response.status_code == 404:
            return None

        response.raise_for_status()

        payload = response.json()

        if cooldown:
            time.sleep(cooldown)

        return payload

    def get_markets(self):
        if self.markets is None:
            payload = (
                self.get(
                    "markets",
                    cooldown=GENERAL_COOLDOWN_SECONDS,
                    language="en",
                )
                or []
            )

            self.markets = {
                str(row.get("marketId")): row
                for row in payload
                if (
                    isinstance(row, dict)
                    and row.get("marketId") is not None
                )
            }

        return self.markets

    def fixtures_between(
        self,
        start,
        end,
    ):
        key = (
            _iso_date(start),
            _iso_date(end),
        )

        if key not in self.fixture_cache:
            payload = self.get(
                "fixtures",
                cooldown=GENERAL_COOLDOWN_SECONDS,
                sportId=SPORT_ID,
                tournamentId=NCAA_TOURNAMENT_ID,
                **{
                    "from": _iso_date(start),
                    "to": _iso_date(end),
                },
            )

            self.fixture_cache[key] = (
                payload
                if isinstance(payload, list)
                else []
            )

        return self.fixture_cache[key]

    def fixture_detail(
        self,
        fixture_id,
    ):
        fixture_id = str(fixture_id)

        if fixture_id not in self.fixture_detail_cache:
            payload = self.get(
                "fixture",
                cooldown=GENERAL_COOLDOWN_SECONDS,
                fixtureId=fixture_id,
                language="en",
            )

            self.fixture_detail_cache[
                fixture_id
            ] = payload or {}

        return self.fixture_detail_cache[
            fixture_id
        ]

    def odds(
        self,
        fixture_id,
    ):
        fixture_id = str(fixture_id)

        if fixture_id not in self.odds_cache:
            payload = self.get(
                "odds",
                cooldown=GENERAL_COOLDOWN_SECONDS,
                fixtureId=fixture_id,
                bookmakers=BOOKMAKER,
                oddsFormat="american",
                language="en",
                verbosity=3,
            )

            self.odds_cache[
                fixture_id
            ] = payload or {}

        return self.odds_cache[
            fixture_id
        ]

    def historical_odds(
        self,
        fixture_id,
    ):
        fixture_id = str(fixture_id)

        if fixture_id not in self.historical_cache:
            payload = self.get(
                "historical-odds",
                cooldown=HISTORICAL_COOLDOWN_SECONDS,
                fixtureId=fixture_id,
                bookmakers=BOOKMAKER,
            )

            self.historical_cache[
                fixture_id
            ] = payload or {}

        return self.historical_cache[
            fixture_id
        ]


# ============================================================
# FIXTURE IDENTITY
# ============================================================

def _fixture_teams(fixture):
    return (
        _clean_text(
            fixture.get("participant1Name")
        ),
        _clean_text(
            fixture.get("participant2Name")
        ),
    )


def _team_matches_fixture(
    hint,
    team1,
    team2,
):
    if not hint:
        return False

    return (
        teams_equivalent(
            hint,
            team1,
        )
        or teams_equivalent(
            hint,
            team2,
        )
    )


def _pair_matches_fixture(
    team_hint,
    opponent_hint,
    team1,
    team2,
):
    if (
        not team_hint
        or not opponent_hint
    ):
        return False

    return (
        (
            teams_equivalent(
                team_hint,
                team1,
            )
            and teams_equivalent(
                opponent_hint,
                team2,
            )
        )
        or
        (
            teams_equivalent(
                team_hint,
                team2,
            )
            and teams_equivalent(
                opponent_hint,
                team1,
            )
        )
    )


def _matchup_text_matches_fixture(
    matchup,
    team1,
    team2,
):
    if not matchup:
        return False

    # Prefer identity-aware comparison where possible.
    separators = (
        " @ ",
        " vs ",
        " vs. ",
        " v ",
    )

    lowered = matchup.lower()

    for separator in separators:
        if separator.strip() not in lowered:
            continue

        # Case-insensitive separator split.
        index = lowered.find(
            separator.strip()
        )

        if index < 0:
            continue

    # Generic identity check using both fixture teams.
    #
    # This intentionally does not accept one matching team as
    # sufficient. Historical fixture discovery must identify
    # both sides whenever matchup text is available.
    words = matchup.replace(
        "@",
        " "
    ).replace(
        "vs.",
        " "
    ).replace(
        "vs",
        " "
    )

    direct = (
        team1.lower() in matchup.lower()
        and team2.lower() in matchup.lower()
    )

    if direct:
        return True

    # Try common two-sided splits.
    for token in (
        " @ ",
        " vs. ",
        " vs ",
        " v ",
    ):
        if token not in matchup.lower():
            continue

        lower_matchup = matchup.lower()
        idx = lower_matchup.find(token)

        left = matchup[:idx].strip()
        right = matchup[
            idx + len(token):
        ].strip()

        if not left or not right:
            continue

        if (
            (
                teams_equivalent(
                    left,
                    team1,
                )
                and teams_equivalent(
                    right,
                    team2,
                )
            )
            or
            (
                teams_equivalent(
                    left,
                    team2,
                )
                and teams_equivalent(
                    right,
                    team1,
                )
            )
        ):
            return True

    # Avoid unused-variable lint complaints while keeping this
    # helper intentionally conservative.
    _ = words

    return False


def _fixture_match_score(
    pick,
    fixture,
):
    team1, team2 = _fixture_teams(
        fixture
    )

    if not team1 or not team2:
        return None

    team_hint = _pick_team(pick)
    opponent_hint = _pick_opponent(pick)
    matchup = _pick_matchup(pick)

    score = 0
    reasons = []

    if _pair_matches_fixture(
        team_hint,
        opponent_hint,
        team1,
        team2,
    ):
        score += 100
        reasons.append(
            "TEAM_OPPONENT_PAIR"
        )

    if _matchup_text_matches_fixture(
        matchup,
        team1,
        team2,
    ):
        score += 80
        reasons.append(
            "MATCHUP_PAIR"
        )

    if _team_matches_fixture(
        team_hint,
        team1,
        team2,
    ):
        score += 20
        reasons.append(
            "SELECTED_TEAM"
        )

    if _team_matches_fixture(
        opponent_hint,
        team1,
        team2,
    ):
        score += 10
        reasons.append(
            "OPPONENT"
        )

    # Require either a verified pair or at least a selected
    # team plus opponent identity. A lone ambiguous team is not
    # enough for historical relocking.
    if score < 30:
        return None

    return (
        score,
        reasons,
    )


def _discover_historical_fixture(
    api,
    pick,
):
    reference = _pick_reference_time(
        pick
    )

    if reference is None:
        return (
            None,
            "NO_REFERENCE_TIME",
            None,
        )

    # If the stored game_time survived, center tightly around
    # that known kickoff.
    if _parse_dt(
        pick.get("game_time")
    ):
        start = (
            reference
            - timedelta(days=1)
        )

        end = (
            reference
            + timedelta(days=1)
        )

    else:
        # Official reconciliation removed the event lock, so
        # use the X post date and look forward to the weekend.
        start = (
            reference
            - timedelta(
                days=HISTORICAL_LOOKBACK_DAYS
            )
        )

        end = (
            reference
            + timedelta(
                days=HISTORICAL_LOOKAHEAD_DAYS
            )
        )

    fixtures = api.fixtures_between(
        start,
        end,
    )

    candidates = []

    for fixture in fixtures:
        matched = _fixture_match_score(
            pick,
            fixture,
        )

        if matched is None:
            continue

        score, reasons = matched

        kickoff = _parse_dt(
            fixture.get("startTime")
        )

        # Prefer games after the source post. This matters when
        # the same schools appear elsewhere in a broad window.
        if kickoff is not None:
            if kickoff >= reference:
                temporal_penalty = 0
            else:
                temporal_penalty = abs(
                    (
                        reference
                        - kickoff
                    ).total_seconds()
                )
        else:
            temporal_penalty = float(
                "inf"
            )

        candidates.append(
            {
                "score": score,
                "reasons": reasons,
                "temporal_penalty":
                    temporal_penalty,
                "fixture": fixture,
            }
        )

    if not candidates:
        return (
            None,
            "FIXTURE_NOT_FOUND",
            None,
        )

    candidates.sort(
        key=lambda row: (
            -row["score"],
            row["temporal_penalty"],
        )
    )

    best = candidates[0]

    if len(candidates) > 1:
        second = candidates[1]

        if (
            best["score"]
            == second["score"]
            and best[
                "temporal_penalty"
            ]
            == second[
                "temporal_penalty"
            ]
        ):
            return (
                None,
                "AMBIGUOUS_FIXTURE",
                {
                    "candidate_count":
                        len(candidates),
                },
            )

    fixture = best["fixture"]

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
            "start_time":
                fixture.get(
                    "startTime"
                ),
            "identity_reasons":
                best["reasons"],
        },
    )


# ============================================================
# MARKET HELPERS
# ============================================================

def _period_ok(
    pick,
    market,
):
    wanted = str(
        market_period(
            normalize_bet_type(
                pick.get("bet_type")
            )
        )
        or ""
    ).lower()

    actual = str(
        market.get("period")
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
        market.get("marketName")
        or ""
    ).lower()

    market_type = str(
        market.get("marketType")
        or ""
    ).lower()

    if "team total" in name:
        return "TEAM_TOTAL"

    if (
        "handicap" in name
        or "spread" in name
        or "handicap" in market_type
    ):
        return "SPREAD"

    if (
        "over under" in name
        or "total" in name
        or "total" in market_type
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


def _catalog_markets_for_pick(
    api,
    pick,
):
    wanted_family = base_market(
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    matches = []

    for market_id, meta in (
        api.get_markets().items()
    ):
        if (
            safe_float(
                meta.get("sportId")
            )
            not in (
                None,
                float(SPORT_ID),
            )
        ):
            continue

        if (
            _market_family(meta)
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
                    str(market_id),
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
            }
        )

    return matches


def _historical_book(
    history,
):
    return (
        (
            history.get(
                "bookmakers"
            )
            or {}
        )
        .get(
            BOOKMAKER
        )
        or {}
    )


def _historical_markets(
    history,
):
    return (
        _historical_book(
            history
        )
        .get(
            "markets"
        )
        or {}
    )


def _historical_price_entries(
    history,
    market_id,
    outcome_id,
):
    market = (
        _historical_markets(
            history
        )
        .get(
            str(market_id)
        )
        or {}
    )

    outcome = (
        (
            market.get(
                "outcomes"
            )
            or {}
        )
        .get(
            str(outcome_id)
        )
        or {}
    )

    players = (
        outcome.get(
            "players"
        )
        or {}
    )

    entries = players.get(
        "0"
    )

    if isinstance(entries, list):
        return [
            entry
            for entry in entries
            if isinstance(
                entry,
                dict,
            )
        ]

    if isinstance(entries, dict):
        return [entries]

    return []


def _latest_pre_post_entry(
    entries,
    posted_at,
):
    if not entries:
        return None

    parsed = []

    for entry in entries:
        created = _parse_dt(
            entry.get(
                "createdAt"
            )
        )

        if created is None:
            continue

        parsed.append(
            (
                created,
                entry,
            )
        )

    if not parsed:
        return None

    parsed.sort(
        key=lambda row: row[0]
    )

    if posted_at is not None:
        before = [
            row
            for row in parsed
            if row[0] <= posted_at
        ]

        if before:
            return before[-1]

    # If no quote existed before the post, use the earliest
    # historical observation only for informational comparison.
    return parsed[0]


# ============================================================
# SPREAD HISTORICAL AUDIT
# ============================================================

def _spread_audit(
    api,
    fixture,
    history,
    pick,
):
    selected = side_identity(
        pick
    )

    x_line = safe_float(
        pick.get("line")
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

    team1, team2 = _fixture_teams(
        fixture
    )

    if teams_equivalent(
        selected,
        team1,
    ):
        selected_number = "1"

    elif teams_equivalent(
        selected,
        team2,
    ):
        selected_number = "2"

    else:
        return (
            "REVIEW",
            "SELECTED_TEAM_NOT_IN_FIXTURE",
            {
                "participant1": team1,
                "participant2": team2,
            },
        )

    posted_at = _parse_dt(
        pick.get("posted_at")
    )

    rows = []

    for market in _catalog_markets_for_pick(
        api,
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
            outcome_name = (
                str(
                    outcome.get(
                        "outcomeName"
                    )
                    or ""
                )
                .strip()
                .lower()
            )

            if outcome_name in {
                selected_number,
                (
                    "participant "
                    + selected_number
                ),
            }:
                selected_outcome = (
                    outcome.get(
                        "outcomeId"
                    )
                )

                break

        if selected_outcome is None:
            continue

        entries = (
            _historical_price_entries(
                history,
                market[
                    "market_id"
                ],
                selected_outcome,
            )
        )

        snapshot = (
            _latest_pre_post_entry(
                entries,
                posted_at,
            )
        )

        if snapshot is None:
            continue

        created_at, price = snapshot

        # OddsPapi handicap is from participant 1's
        # perspective. Participant 2 receives the reciprocal.
        dk_line = (
            handicap
            if selected_number == "1"
            else -handicap
        )

        rows.append(
            {
                "draftkings_line":
                    dk_line,
                "market":
                    market,
                "created_at":
                    created_at,
                "price":
                    price,
            }
        )

    if not rows:
        return (
            "UNAVAILABLE",
            "NO_HISTORICAL_DK_SPREAD",
            None,
        )

    # Prefer the DraftKings rung closest in absolute magnitude
    # to the literal X line.
    rows.sort(
        key=lambda row:
            abs(
                abs(
                    row[
                        "draftkings_line"
                    ]
                )
                - abs(
                    x_line
                )
            )
    )

    best = rows[0]

    dk_line = best[
        "draftkings_line"
    ]

    magnitude_difference = abs(
        abs(x_line)
        - abs(dk_line)
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
            x_line == 0
            and dk_line == 0
        )
        or (
            x_line * dk_line > 0
        )
    )

    detail = {
        "fixture_id":
            fixture.get(
                "fixtureId"
            ),
        "participant1":
            team1,
        "participant2":
            team2,
        "market_id":
            best[
                "market"
            ][
                "market_id"
            ],
        "market_name":
            best[
                "market"
            ][
                "name"
            ],
        "market_period":
            best[
                "market"
            ][
                "period"
            ],
        "x_line":
            x_line,
        "draftkings_line":
            dk_line,
        "magnitude_difference":
            round(
                magnitude_difference,
                3,
            ),
        "snapshot_created_at":
            best[
                "created_at"
            ].isoformat(),
        "source_posted_at":
            (
                posted_at.isoformat()
                if posted_at
                else None
            ),
        "draftkings_price":
            (
                best[
                    "price"
                ].get(
                    "priceAmerican"
                )
                or best[
                    "price"
                ].get(
                    "price"
                )
            ),
    }

    if exact:
        return (
            "CONFIRMED",
            "EXACT_HISTORICAL_SPREAD_MATCH",
            detail,
        )

    if same_sign:
        return (
            "NO_CHANGE",
            "SAME_SIGN_LINE_MOVEMENT",
            detail,
        )

    if magnitude_difference <= 3.0:
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
# TOTAL / OTHER HISTORICAL AUDIT
# ============================================================

def _generic_audit(
    api,
    fixture,
    history,
    pick,
):
    family = base_market(
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    catalog = _catalog_markets_for_pick(
        api,
        pick,
    )

    historical = _historical_markets(
        history
    )

    available = [
        market
        for market in catalog
        if market[
            "market_id"
        ] in historical
    ]

    if not available:
        return (
            "UNAVAILABLE",
            (
                "NO_HISTORICAL_DK_"
                + str(family)
            ),
            None,
        )

    x_line = safe_float(
        pick.get("line")
    )

    if (
        family in {
            "TOTAL",
            "TEAM_TOTAL",
        }
        and x_line is not None
    ):
        exact = [
            market
            for market in available
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
                "EXACT_HISTORICAL_MARKET_NUMBER",
                {
                    "fixture_id":
                        fixture.get(
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

        lines = sorted(
            {
                market[
                    "handicap"
                ]
                for market in available
                if market[
                    "handicap"
                ]
                is not None
            }
        )

        return (
            "NO_CHANGE",
            "HISTORICAL_DK_MARKET_DIFFERENT_NUMBER",
            {
                "fixture_id":
                    fixture.get(
                        "fixtureId"
                    ),
                "x_line":
                    x_line,
                "draftkings_lines":
                    lines,
            },
        )

    if family == "MONEYLINE":
        selected = side_identity(
            pick
        )

        team1, team2 = _fixture_teams(
            fixture
        )

        if (
            selected
            and (
                teams_equivalent(
                    selected,
                    team1,
                )
                or teams_equivalent(
                    selected,
                    team2,
                )
            )
        ):
            return (
                "CONFIRMED",
                "SELECTED_TEAM_IN_HISTORICAL_DK_FIXTURE",
                {
                    "fixture_id":
                        fixture.get(
                            "fixtureId"
                        ),
                    "participant1":
                        team1,
                    "participant2":
                        team2,
                },
            )

    return (
        "UNAVAILABLE",
        "NO_SAFE_HISTORICAL_RULE",
        {
            "fixture_id":
                fixture.get(
                    "fixtureId"
                ),
        },
    )


# ============================================================
# MAIN
# ============================================================

def validate_sportsbook():
    print()
    print("=" * 72)
    print(
        "DRAFTKINGS VALIDATION — "
        "WEEK 4 HISTORICAL OBSERVATION MODE"
    )
    print("=" * 72)

    print(
        f"Historical official audit enabled for "
        f"Week {AUDIT_OFFICIAL_WEEK}."
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
    matched_fixture_count = 0

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

        try:
            fixture = None
            fixture_detail = None

            # ------------------------------------------------
            # NORMAL PROVISIONAL PATH
            # ------------------------------------------------
            #
            # Future provisional rows can still use any stored
            # OddsPapi fixture metadata if we add it later.
            #
            # For the current Week 4 historical audit, discover
            # the fixture independently because official
            # reconciliation removed ESPN event_id.
            # ------------------------------------------------

            (
                fixture,
                fixture_status,
                fixture_detail,
            ) = _discover_historical_fixture(
                api,
                pick,
            )

            if fixture is None:
                counts[
                    fixture_status
                ] += 1

                print(
                    f"{fixture_status}: "
                    f"{label}"
                )

                if fixture_detail:
                    print(
                        "  ",
                        fixture_detail,
                    )

                continue

            matched_fixture_count += 1

            print(
                f"FIXTURE_MATCHED: "
                f"{label}"
            )

            if fixture_detail:
                print(
                    "  ",
                    fixture_detail,
                )

            fixture_id = fixture.get(
                "fixtureId"
            )

            if not fixture_id:
                counts[
                    "FIXTURE_WITHOUT_ID"
                ] += 1

                print(
                    f"FIXTURE_WITHOUT_ID: "
                    f"{label}"
                )

                continue

            # ------------------------------------------------
            # HISTORICAL DRAFTKINGS ODDS
            # ------------------------------------------------

            history = api.historical_odds(
                fixture_id
            )

            if not history:
                counts[
                    "NO_HISTORICAL_ODDS"
                ] += 1

                print(
                    f"NO_HISTORICAL_ODDS: "
                    f"{label}"
                )

                continue

            if not _historical_book(
                history
            ):
                counts[
                    "NO_DRAFTKINGS_HISTORY"
                ] += 1

                print(
                    f"NO_DRAFTKINGS_HISTORY: "
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
                ) = _spread_audit(
                    api,
                    fixture,
                    history,
                    pick,
                )

            else:
                (
                    status,
                    reason,
                    detail,
                ) = _generic_audit(
                    api,
                    fixture,
                    history,
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

            response_text = ""

            try:
                response_text = (
                    exc.response.text[:500]
                )
            except Exception:
                pass

            print(
                f"API_ERROR: "
                f"{label} | "
                f"HTTP {response_status}"
            )

            if response_text:
                print(
                    "  Response:",
                    response_text,
                )

        except requests.RequestException as exc:
            counts[
                "API_ERROR"
            ] += 1

            print(
                f"API_ERROR: "
                f"{label} | "
                f"{type(exc).__name__}: "
                f"{exc}"
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
    print("-" * 72)
    print(
        "DRAFTKINGS WEEK 4 "
        "HISTORICAL OBSERVATION SUMMARY"
    )
    print("-" * 72)

    print(
        f"Eligible wagers: "
        f"{eligible_count}"
    )

    print(
        f"Fixture matches: "
        f"{matched_fixture_count}"
    )

    for key in sorted(counts):
        print(
            f"{key}: "
            f"{counts[key]}"
        )

    print("-" * 72)

    print(
        "Observation mode made NO changes "
        "to picks.json."
    )


if __name__ == "__main__":
    validate_sportsbook()
