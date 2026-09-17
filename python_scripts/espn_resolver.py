from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time

import requests

from football_identity import (
    alias_group,
    best_matchup_hints,
    canonical_game_identity,
    canonical_team,
    clean_text,
    is_ambiguous_hint,
    norm,
    side_identity,
    split_matchup,
)


# ============================================================
# BARSTOOL PICK EM — SHARED ESPN EVENT RESOLVER
# ============================================================
#
# SINGLE SOURCE OF TRUTH FOR:
#
#   - Pick Em week windows
#   - ESPN scoreboard retrieval
#   - FBS/FCS slate merging
#   - ESPN team identity
#   - matchup resolution
#   - event_id locking
#   - schedule metadata
#   - resolution diagnostics
#
# DESIGN RULE:
#
# FAIL CLOSED.
#
# Never guess an ESPN event.
# ============================================================


ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football/scoreboard"
)

REQUEST_TIMEOUT = 30

ESPN_GROUPS = (
    None,
    80,
    81,
)


# ============================================================
# HTTP
# ============================================================

def request_json(
    url,
    params=None,
    *,
    attempts=3,
):
    """
    ESPN request behavior intentionally mirrors the request
    headers already proven to work in the production grader.
    """

    last_error = None

    headers = {
        "Accept": (
            "application/json, "
            "text/plain, */*"
        ),
        "Origin":
            "https://www.espn.com",
        "Referer":
            "https://www.espn.com/",
    }

    for attempt in range(
        1,
        attempts + 1,
    ):
        try:

            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers=headers,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt >= attempts:
                break

            time.sleep(
                attempt
            )

    raise last_error


# ============================================================
# PICK EM WEEK WINDOWS
# ============================================================

def week_window(
    season_year,
    week,
):
    season_year = int(
        season_year
    )

    week = int(
        week
    )

    if week < 1:
        raise ValueError(
            f"Invalid week: {week}"
        )

    # --------------------------------------------------------
    # VERIFIED 2026 PICK EM WINDOWS
    # --------------------------------------------------------

    if season_year == 2026:

        if week == 1:

            return (
                date(
                    2026,
                    8,
                    22,
                ),
                date(
                    2026,
                    9,
                    7,
                ),
            )

        if week == 2:

            return (
                date(
                    2026,
                    9,
                    8,
                ),
                date(
                    2026,
                    9,
                    13,
                ),
            )

        week3_start = date(
            2026,
            9,
            14,
        )

        start = (
            week3_start
            + timedelta(
                days=(
                    week - 3
                ) * 7
            )
        )

        return (
            start,
            start
            + timedelta(
                days=6
            ),
        )

    # --------------------------------------------------------
    # FUTURE-SEASON FALLBACK
    # --------------------------------------------------------
    #
    # This is schedule discovery only.
    #
    # The audit layer we add later will prevent silently using
    # an unverified future season calendar for final grading.
    # --------------------------------------------------------

    september_1 = date(
        season_year,
        9,
        1,
    )

    first_monday = (
        september_1
        - timedelta(
            days=
                september_1.weekday()
        )
    )

    start = (
        first_monday
        + timedelta(
            days=(
                week - 1
            ) * 7
        )
    )

    return (
        start,
        start
        + timedelta(
            days=6
        ),
    )


# ============================================================
# ESPN EVENT STRUCTURE
# ============================================================

def competitions(
    event
):
    return (
        event.get(
            "competitions"
        )
        or []
    )


def primary_competition(
    event
):
    comps = competitions(
        event
    )

    if not comps:
        return {}

    return comps[0]


def competitors(
    event
):
    return (
        primary_competition(
            event
        ).get(
            "competitors"
        )
        or []
    )


def event_id(
    event
):
    value = str(
        event.get(
            "id"
        )
        or ""
    ).strip()

    return value or None


def competition_id(
    event
):
    competition = (
        primary_competition(
            event
        )
    )

    value = str(
        competition.get(
            "id"
        )
        or event.get(
            "id"
        )
        or ""
    ).strip()

    return value or None


def event_date(
    event
):
    competition = (
        primary_competition(
            event
        )
    )

    return (
        competition.get(
            "date"
        )
        or event.get(
            "date"
        )
    )


def event_datetime(
    event
):
    value = event_date(
        event
    )

    if not value:
        return None

    try:

        parsed = (
            datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00",
                )
            )
        )

    except Exception:
        return None

    if parsed.tzinfo is None:

        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def event_status_name(
    event
):
    status = (
        event.get(
            "status"
        )
        or {}
    )

    status_type = (
        status.get(
            "type"
        )
        or {}
    )

    return str(
        status_type.get(
            "name"
        )
        or ""
    ).upper().strip()


def event_completed(
    event
):
    status = (
        event.get(
            "status"
        )
        or {}
    )

    status_type = (
        status.get(
            "type"
        )
        or {}
    )

    if (
        status_type.get(
            "completed"
        )
        is True
    ):
        return True

    return (
        event_status_name(
            event
        )
        in {
            "STATUS_FINAL",
            "FINAL",
        }
    )


# ============================================================
# ESPN TEAM IDENTITIES
# ============================================================

def competitor_team(
    competitor
):
    return (
        competitor.get(
            "team"
        )
        or {}
    )


def competitor_team_id(
    competitor
):
    team = competitor_team(
        competitor
    )

    value = str(
        team.get(
            "id"
        )
        or competitor.get(
            "id"
        )
        or ""
    ).strip()

    return value or None


def competitor_home_away(
    competitor
):
    return str(
        competitor.get(
            "homeAway"
        )
        or ""
    ).lower().strip()


def competitor_identity_values(
    competitor
):
    """
    Deliberately excludes mascot-only team["name"].
    """

    team = competitor_team(
        competitor
    )

    values = []

    for key in (
        "displayName",
        "shortDisplayName",
        "location",
        "abbreviation",
    ):

        value = clean_text(
            team.get(
                key
            )
        )

        if (
            value
            and value not in values
        ):
            values.append(
                value
            )

    return values


def competitor_aliases(
    competitor
):
    result = set()

    for value in (
        competitor_identity_values(
            competitor
        )
    ):

        normalized = norm(
            value
        )

        if normalized:
            result.add(
                normalized
            )

        result.update(
            alias_group(
                value
            )
        )

        canonical = canonical_team(
            value
        )

        if canonical:

            result.add(
                norm(
                    canonical
                )
            )

    return {
        value
        for value in result
        if value
    }


# ============================================================
# AMBIGUOUS ALIASES
# ============================================================

AMBIGUOUS_CONTEXT_GROUPS = {
    "osu": {
        "ohio state",
        "oklahoma state",
        "oregon state",
    },
}


def ambiguous_candidate_teams(
    hint
):
    return (
        AMBIGUOUS_CONTEXT_GROUPS.get(
            norm(
                hint
            ),
            set(),
        )
    )


def comp_matches_ambiguous_hint(
    competitor,
    hint,
):
    """
    Bare ambiguous aliases never resolve by themselves.

    They MAY participate in a two-team matchup when the other
    side of the matchup makes the ESPN event unique.

    Example:
        OSU @ Houston

    can match Oregon State @ Houston if that is the only
    qualifying event.

    But:
        OSU +3

    cannot resolve on OSU alone.
    """

    candidates = (
        ambiguous_candidate_teams(
            hint
        )
    )

    if not candidates:
        return False

    comp_aliases = (
        competitor_aliases(
            competitor
        )
    )

    for candidate in candidates:

        if (
            comp_aliases
            & alias_group(
                candidate
            )
        ):
            return True

    return False


def comp_matches_hint(
    competitor,
    hint,
    *,
    allow_contextual_ambiguous=False,
):
    if not hint:
        return False

    if is_ambiguous_hint(
        hint
    ):

        if not allow_contextual_ambiguous:
            return False

        return (
            comp_matches_ambiguous_hint(
                competitor,
                hint,
            )
        )

    hint_aliases = alias_group(
        hint
    )

    if not hint_aliases:
        return False

    return bool(
        competitor_aliases(
            competitor
        )
        & hint_aliases
    )


def event_contains_team(
    event,
    hint,
):
    """
    Single-team matching never accepts a bare ambiguous alias.
    """

    if (
        not hint
        or is_ambiguous_hint(
            hint
        )
    ):
        return False

    return any(
        comp_matches_hint(
            competitor,
            hint,
        )
        for competitor
        in competitors(
            event
        )
    )


def event_contains_pair(
    event,
    first,
    second,
):
    """
    Two-team matching.

    Ambiguous aliases such as OSU are allowed only here, where
    the opponent can provide the context needed to identify one
    unique ESPN event.
    """

    if (
        not first
        or not second
    ):
        return False

    event_comps = competitors(
        event
    )

    if len(
        event_comps
    ) != 2:
        return False

    first_matches = [
        index
        for index, competitor
        in enumerate(
            event_comps
        )
        if comp_matches_hint(
            competitor,
            first,
            allow_contextual_ambiguous=True,
        )
    ]

    second_matches = [
        index
        for index, competitor
        in enumerate(
            event_comps
        )
        if comp_matches_hint(
            competitor,
            second,
            allow_contextual_ambiguous=True,
        )
    ]

    for first_index in first_matches:

        for second_index in second_matches:

            if (
                first_index
                != second_index
            ):
                return True

    return False


def canonical_event_game(
    event
):
    comps = competitors(
        event
    )

    if len(comps) != 2:
        return None

    canonical = []

    for competitor in comps:

        resolved = None

        for value in (
            competitor_identity_values(
                competitor
            )
        ):

            candidate = (
                canonical_team(
                    value
                )
            )

            if candidate:

                resolved = candidate
                break

        if not resolved:
            return None

        canonical.append(
            resolved
        )

    if (
        not canonical[0]
        or not canonical[1]
        or canonical[0]
        == canonical[1]
    ):
        return None

    return tuple(
        sorted(
            canonical
        )
    )


# ============================================================
# EVENT DISPLAY METADATA
# ============================================================

def competitor_display_name(
    competitor
):
    team = competitor_team(
        competitor
    )

    return (
        clean_text(
            team.get(
                "shortDisplayName"
            )
        )
        or clean_text(
            team.get(
                "displayName"
            )
        )
        or clean_text(
            team.get(
                "location"
            )
        )
        or clean_text(
            team.get(
                "abbreviation"
            )
        )
    )


def event_matchup_text(
    event
):
    comps = competitors(
        event
    )

    if len(comps) != 2:

        return clean_text(
            event.get(
                "shortName"
            )
            or event.get(
                "name"
            )
        )

    away = None
    home = None

    for competitor in comps:

        if (
            competitor_home_away(
                competitor
            )
            == "away"
        ):
            away = competitor

        elif (
            competitor_home_away(
                competitor
            )
            == "home"
        ):
            home = competitor

    if (
        away is not None
        and home is not None
    ):

        return (
            f"{competitor_display_name(away)} "
            f"@ "
            f"{competitor_display_name(home)}"
        )

    return (
        f"{competitor_display_name(comps[0])} "
        f"vs "
        f"{competitor_display_name(comps[1])}"
    )


def event_metadata(
    event
):
    return {
        "event_id":
            event_id(
                event
            ),

        "competition_id":
            competition_id(
                event
            ),

        "game_time":
            event_date(
                event
            ),

        "event_matchup":
            event_matchup_text(
                event
            ),

        "event_status":
            event_status_name(
                event
            ),
    }


# ============================================================
# EVENT MERGING
# ============================================================

def event_quality(
    event
):
    score = 0

    if event_completed(
        event
    ):
        score += 100

    if len(
        competitors(
            event
        )
    ) == 2:
        score += 25

    if event_date(
        event
    ):
        score += 10

    if canonical_event_game(
        event
    ):
        score += 20

    if competitions(
        event
    ):
        score += 5

    return score


def merge_events(
    events
):
    by_id = {}

    for event in events:

        current_id = event_id(
            event
        )

        if not current_id:
            continue

        old = by_id.get(
            current_id
        )

        if (
            old is None
            or event_quality(
                event
            )
            > event_quality(
                old
            )
        ):

            by_id[
                current_id
            ] = event

    return list(
        by_id.values()
    )


# ============================================================
# ESPN SCOREBOARD FETCHING
# ============================================================

def fetch_scoreboard_view(
    params,
    *,
    label,
):
    all_events = []

    for group in ESPN_GROUPS:

        view_params = dict(
            params
        )

        if group is not None:

            view_params[
                "groups"
            ] = group

        try:

            payload = request_json(
                ESPN_SCOREBOARD,
                view_params,
            )

            events = (
                payload.get(
                    "events"
                )
                or []
            )

            print(
                label,
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "| events:",
                len(
                    events
                ),
            )

            all_events.extend(
                events
            )

        except Exception as exc:

            print(
                label,
                "FAILED",
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "|",
                type(exc).__name__,
                exc,
            )

    return merge_events(
        all_events
    )


def fetch_day_events(
    day
):
    datestring = (
        day.strftime(
            "%Y%m%d"
        )
    )

    return fetch_scoreboard_view(
        {
            "dates":
                datestring,

            "limit":
                1000,
        },
        label=(
            f"ESPN DAY VIEW: "
            f"{datestring}"
        ),
    )


def fetch_date_range_events(
    start,
    end,
):
    datestring = (
        f"{start.strftime('%Y%m%d')}-"
        f"{end.strftime('%Y%m%d')}"
    )

    return fetch_scoreboard_view(
        {
            "dates":
                datestring,

            "limit":
                1000,
        },
        label=(
            f"ESPN DATE-RANGE VIEW: "
            f"{datestring}"
        ),
    )


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    """
    Build candidate slate from the same two ESPN retrieval
    strategies already proven in the production grader:

      1. every individual day in the Pick Em window
      2. ESPN date-range endpoint

    The two feeds are merged by ESPN event_id.
    """

    start, end = week_window(
        season_year,
        week,
    )

    print()
    print(
        "===================================="
    )
    print(
        "BUILDING ESPN SLATE"
    )
    print(
        "Season:",
        season_year,
        "| Pick Em Week:",
        week,
    )
    print(
        "Window:",
        start.isoformat(),
        "through",
        end.isoformat(),
    )
    print(
        "===================================="
    )

    all_events = []

    # --------------------------------------------------------
    # DAILY VIEWS
    # --------------------------------------------------------

    current = start

    while current <= end:

        try:

            events = (
                fetch_day_events(
                    current
                )
            )

            all_events.extend(
                events
            )

        except Exception as exc:

            print(
                "ESPN DAY FETCH FAILED:",
                current,
                "|",
                type(exc).__name__,
                exc,
            )

        current += timedelta(
            days=1
        )

    daily_merged = merge_events(
        all_events
    )

    print(
        "Date-based slate:",
        len(
            daily_merged
        ),
        "unique events",
    )

    # --------------------------------------------------------
    # DATE-RANGE VIEW
    # --------------------------------------------------------

    try:

        range_events = (
            fetch_date_range_events(
                start,
                end,
            )
        )

        print(
            "Date-range slate:",
            len(
                range_events
            ),
            "unique events",
        )

        all_events.extend(
            range_events
        )

    except Exception as exc:

        print(
            "ESPN DATE-RANGE FETCH FAILED:",
            season_year,
            "Week",
            week,
            "|",
            type(exc).__name__,
            exc,
        )

    merged = merge_events(
        all_events
    )

    if not merged:

        raise RuntimeError(
            "No ESPN events retrieved "
            f"for {season_year} "
            f"Pick Em Week {week}."
        )

    print(
        "Combined complete slate:",
        len(
            merged
        ),
        "unique events",
    )

    return merged


# ============================================================
# EXACT EVENT-ID LOOKUP
# ============================================================

def find_event_by_id(
    events,
    stored_event_id,
):
    target = str(
        stored_event_id
        or ""
    ).strip()

    if not target:
        return None

    matches = [
        event
        for event in events
        if event_id(
            event
        )
        == target
    ]

    if len(matches) == 1:
        return matches[0]

    return None


# ============================================================
# PICK POSTING TIME
# ============================================================

def pick_posted_datetime(
    pick
):
    value = (
        pick.get(
            "posted_at"
        )
        or pick.get(
            "created_at"
        )
    )

    if not value:
        return None

    try:

        parsed = (
            datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00",
                )
            )
        )

    except Exception:
        return None

    if parsed.tzinfo is None:

        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


# ============================================================
# CANDIDATE HELPERS
# ============================================================

def unique_event(
    events
):
    events = merge_events(
        events
    )

    if len(events) == 1:
        return events[0]

    return None


def pair_matches(
    events,
    first,
    second,
):
    return [
        event
        for event in events
        if event_contains_pair(
            event,
            first,
            second,
        )
    ]


def team_matches(
    events,
    hint,
):
    return [
        event
        for event in events
        if event_contains_team(
            event,
            hint,
        )
    ]


def filter_events_near_post_time(
    events,
    pick,
):
    """
    Tie-breaker only.

    Never creates a match.
    """

    posted = pick_posted_datetime(
        pick
    )

    if (
        posted is None
        or len(
            events
        ) <= 1
    ):
        return events

    reasonable = []

    for event in events:

        kickoff = event_datetime(
            event
        )

        if kickoff is None:

            reasonable.append(
                event
            )

            continue

        if kickoff >= (
            posted
            - timedelta(
                hours=12
            )
        ):

            reasonable.append(
                event
            )

    if reasonable:
        return reasonable

    return events


# ============================================================
# RESOLUTION RESULT
# ============================================================

def resolution_result(
    *,
    event=None,
    method=None,
    reason=None,
    candidates=None,
    confidence=None,
):
    candidate_ids = [
        event_id(
            candidate
        )
        for candidate
        in (
            candidates
            or []
        )
        if event_id(
            candidate
        )
    ]

    return {
        "event":
            event,

        "event_id":
            (
                event_id(
                    event
                )
                if event
                else None
            ),

        "method":
            method,

        "reason":
            reason,

        "confidence":
            confidence,

        "candidate_event_ids":
            candidate_ids,
    }


# ============================================================
# PERMANENT EVENT RESOLUTION
# ============================================================

def resolve_event_detailed(
    pick,
    events,
):
    """
    Resolution hierarchy:

      0. existing event_id lock
      1. explicit matchup
      2. structured team + opponent
      3. matchup embedded in selection
      4. canonical game identity
      5. selected-team-only fallback

    Every successful resolution must identify exactly one ESPN
    event.
    """

    # --------------------------------------------------------
    # 0. EXISTING EVENT LOCK
    # --------------------------------------------------------

    stored_event_id = str(
        pick.get(
            "event_id"
        )
        or ""
    ).strip()

    if stored_event_id:

        locked = find_event_by_id(
            events,
            stored_event_id,
        )

        if locked:

            return resolution_result(
                event=locked,
                method=
                    "EXISTING_EVENT_ID",
                reason=
                    "Stored ESPN event_id "
                    "found in current slate.",
                candidates=[
                    locked
                ],
                confidence=1.0,
            )

        return resolution_result(
            event=None,
            method=
                "EXISTING_EVENT_ID_MISSING",
            reason=
                "Stored event_id was not found "
                "in current ESPN slate; "
                "automatic rematching blocked.",
            candidates=[],
            confidence=0.0,
        )

    # --------------------------------------------------------
    # 1. EXPLICIT MATCHUP
    # --------------------------------------------------------

    explicit = split_matchup(
        pick.get(
            "matchup"
        )
    )

    if len(
        explicit
    ) == 2:

        matches = pair_matches(
            events,
            explicit[0],
            explicit[1],
        )

        matches = (
            filter_events_near_post_time(
                matches,
                pick,
            )
        )

        unique = unique_event(
            matches
        )

        if unique:

            return resolution_result(
                event=unique,
                method=
                    "EXPLICIT_MATCHUP",
                reason=
                    "Exact two-team alias match "
                    "from pick.matchup.",
                candidates=
                    matches,
                confidence=1.0,
            )

        if len(
            matches
        ) > 1:

            return resolution_result(
                event=None,
                method=
                    "EXPLICIT_MATCHUP_AMBIGUOUS",
                reason=
                    "Explicit matchup matched "
                    "multiple ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 2. STRUCTURED TEAM + OPPONENT
    # --------------------------------------------------------

    team = clean_text(
        pick.get(
            "team"
        )
    )

    opponent = clean_text(
        pick.get(
            "opponent"
        )
    )

    if team and opponent:

        matches = pair_matches(
            events,
            team,
            opponent,
        )

        matches = (
            filter_events_near_post_time(
                matches,
                pick,
            )
        )

        unique = unique_event(
            matches
        )

        if unique:

            return resolution_result(
                event=unique,
                method=
                    "TEAM_OPPONENT",
                reason=
                    "Exact two-team alias match "
                    "from structured team/opponent.",
                candidates=
                    matches,
                confidence=1.0,
            )

        if len(
            matches
        ) > 1:

            return resolution_result(
                event=None,
                method=
                    "TEAM_OPPONENT_AMBIGUOUS",
                reason=
                    "Structured team/opponent "
                    "matched multiple ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 3. MATCHUP EMBEDDED IN SELECTION
    # --------------------------------------------------------

    selection_only_pick = dict(
        pick
    )

    selection_only_pick[
        "matchup"
    ] = None

    selection_only_pick[
        "team"
    ] = None

    selection_only_pick[
        "opponent"
    ] = None

    selection_hints = (
        best_matchup_hints(
            selection_only_pick
        )
    )

    if len(
        selection_hints
    ) == 2:

        matches = pair_matches(
            events,
            selection_hints[0],
            selection_hints[1],
        )

        matches = (
            filter_events_near_post_time(
                matches,
                pick,
            )
        )

        unique = unique_event(
            matches
        )

        if unique:

            return resolution_result(
                event=unique,
                method=
                    "SELECTION_MATCHUP",
                reason=
                    "Exact two-team alias match "
                    "from selection text.",
                candidates=
                    matches,
                confidence=0.99,
            )

        if len(
            matches
        ) > 1:

            return resolution_result(
                event=None,
                method=
                    "SELECTION_MATCHUP_AMBIGUOUS",
                reason=
                    "Selection matchup matched "
                    "multiple ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 4. CANONICAL GAME IDENTITY
    # --------------------------------------------------------

    game_identity = (
        canonical_game_identity(
            pick
        )
    )

    if game_identity:

        matches = [
            event
            for event in events
            if canonical_event_game(
                event
            )
            == game_identity
        ]

        matches = (
            filter_events_near_post_time(
                matches,
                pick,
            )
        )

        unique = unique_event(
            matches
        )

        if unique:

            return resolution_result(
                event=unique,
                method=
                    "CANONICAL_GAME_IDENTITY",
                reason=
                    "Canonical two-team identity "
                    "matched exactly one ESPN event.",
                candidates=
                    matches,
                confidence=0.99,
            )

        if len(
            matches
        ) > 1:

            return resolution_result(
                event=None,
                method=
                    "CANONICAL_GAME_AMBIGUOUS",
                reason=
                    "Canonical game identity matched "
                    "multiple ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 5. SELECTED TEAM ONLY
    # --------------------------------------------------------

    selected = side_identity(
        pick
    )

    if (
        selected
        and not is_ambiguous_hint(
            selected
        )
    ):

        matches = team_matches(
            events,
            selected,
        )

        matches = (
            filter_events_near_post_time(
                matches,
                pick,
            )
        )

        unique = unique_event(
            matches
        )

        if unique:

            return resolution_result(
                event=unique,
                method=
                    "UNIQUE_SELECTED_TEAM",
                reason=
                    "Selected team appears in exactly "
                    "one eligible ESPN event.",
                candidates=
                    matches,
                confidence=0.95,
            )

        if len(
            matches
        ) > 1:

            return resolution_result(
                event=None,
                method=
                    "SELECTED_TEAM_AMBIGUOUS",
                reason=
                    "Selected team appears in multiple "
                    "eligible ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    return resolution_result(
        event=None,
        method=
            "NO_CONFIDENT_EVENT_MATCH",
        reason=
            "No exact or uniquely safe ESPN "
            "event match was found.",
        candidates=[],
        confidence=0.0,
    )


def resolve_event(
    pick,
    events,
):
    return (
        resolve_event_detailed(
            pick,
            events,
        ).get(
            "event"
        )
    )


# ============================================================
# EVENT LOCK
# ============================================================

def lock_pick_to_event(
    pick,
    event,
    *,
    method=None,
):
    metadata = event_metadata(
        event
    )

    pick[
        "event_id"
    ] = metadata[
        "event_id"
    ]

    pick[
        "competition_id"
    ] = metadata[
        "competition_id"
    ]

    pick[
        "game_time"
    ] = metadata[
        "game_time"
    ]

    pick[
        "event_matchup"
    ] = metadata[
        "event_matchup"
    ]

    pick[
        "event_status"
    ] = metadata[
        "event_status"
    ]

    if method:

        pick[
            "event_resolution_method"
        ] = method

    pick.pop(
        "event_resolution_review",
        None,
    )

    pick.pop(
        "event_resolution_reason",
        None,
    )

    return pick


# ============================================================
# DIAGNOSTICS
# ============================================================

def describe_event(
    event
):
    return {
        "event_id":
            event_id(
                event
            ),

        "matchup":
            event_matchup_text(
                event
            ),

        "game_time":
            event_date(
                event
            ),

        "canonical_game":
            canonical_event_game(
                event
            ),
    }


def audit_pick_resolution(
    pick,
    events,
):
    result = (
        resolve_event_detailed(
            pick,
            events,
        )
    )

    event = result.get(
        "event"
    )

    return {
        "picker":
            pick.get(
                "picker"
            ),

        "week":
            pick.get(
                "week"
            ),

        "selection":
            pick.get(
                "selection"
            ),

        "matchup":
            pick.get(
                "matchup"
            ),

        "team":
            pick.get(
                "team"
            ),

        "opponent":
            pick.get(
                "opponent"
            ),

        "method":
            result.get(
                "method"
            ),

        "reason":
            result.get(
                "reason"
            ),

        "confidence":
            result.get(
                "confidence"
            ),

        "event":
            (
                describe_event(
                    event
                )
                if event
                else None
            ),

        "candidate_event_ids":
            (
                result.get(
                    "candidate_event_ids"
                )
                or []
            ),
    }
