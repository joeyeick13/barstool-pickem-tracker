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
    register_espn_team_identity,
    side_identity,
    split_matchup,
)


# ============================================================
# BARSTOOL PICK EM — SHARED ESPN EVENT RESOLVER
# ============================================================
#
# PERMANENT RESPONSIBILITIES
#
#   1. Build a complete ESPN CFB slate
#   2. Normalize ESPN team identity
#   3. Resolve wagers to exactly one ESPN event
#   4. Preserve existing event locks
#   5. Fail closed when resolution is not safe
#
# IMPORTANT:
#
# This file contains NO week-specific event IDs and NO
# matchup-specific patches.
#
# ============================================================


ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football/scoreboard"
)

REQUEST_TIMEOUT = 30

# ESPN's CFB scoreboard does NOT reliably treat arbitrarily
# large limits as "give me everything".
#
# limit=100 is intentionally used because that is the stable
# scoreboard size used by the ESPN site/API behavior.
ESPN_LIMIT = 100


# ------------------------------------------------------------
# ESPN GROUPS
# ------------------------------------------------------------
#
# We deliberately query multiple overlapping views.
#
# 80 = FBS
# 81 = FCS
#
# Conference views provide another independent way to recover
# games if ESPN's aggregate FBS/FCS views are incomplete.
#
# Duplicates are harmless because every response is merged by
# ESPN event_id.
# ------------------------------------------------------------

ESPN_GROUPS = (
    None,

    # Division-level views
    80,     # FBS
    81,     # FCS

    # Major / FBS conference views
    1,      # ACC
    4,      # Big 12
    5,      # Big Ten
    8,      # SEC
    9,      # Pac-12 / legacy ESPN grouping
    12,     # Conference USA
    15,     # MAC
    17,     # Mountain West
    18,     # FBS Independents
    37,     # Sun Belt
    151,    # American
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
    ESPN request behavior mirrors the request headers that have
    already worked successfully in the production grader.
    """

    last_error = None

    headers = {
        "Accept":
            "application/json, text/plain, */*",

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

            payload = response.json()

            if not isinstance(
                payload,
                dict,
            ):
                raise RuntimeError(
                    "ESPN response was not a JSON object."
                )

            return payload

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
# ESPN TEAM IDENTITY
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
    IMPORTANT:

    Do NOT use mascot-only team["name"].

    Example:
        "Tigers"

    is not a safe football identity.
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


def competitor_identity_anchor(
    competitor
):
    """
    Return ESPN's school/location identity for this competitor.

    The runtime identity registry requires one safe anchor plus the
    alternate names ESPN attached to the same stable team ID.

    We intentionally use ESPN's location field as the anchor rather
    than guessing a school name by stripping mascot words from a
    display name.
    """
    team = competitor_team(
        competitor
    )

    anchor = clean_text(
        team.get(
            "location"
        )
    )

    return anchor or None


def register_competitor_identity(
    competitor
):
    team_id = competitor_team_id(
        competitor
    )

    anchor = competitor_identity_anchor(
        competitor
    )

    values = competitor_identity_values(
        competitor
    )

    if (
        team_id
        and anchor
        and values
    ):
        register_espn_team_identity(
            team_id,
            anchor,
            values,
        )

    return values


def register_event_identities(
    event
):
    for competitor in competitors(
        event
    ):
        register_competitor_identity(
            competitor
        )

    return event


def register_slate_identities(
    events
):
    for event in events:
        register_event_identities(
            event
        )

    return events


def competitor_aliases(
    competitor
):
    result = set()

    for value in (
        register_competitor_identity(
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
# CONTEXTUAL AMBIGUOUS IDENTITIES
# ============================================================

AMBIGUOUS_CONTEXT_GROUPS = {
    "osu": {
        "ohio state",
        "oklahoma state",
        "oregon state",
    },
    "tu": {
        "temple",
        "tulane",
        "tulsa",
    },
    "um": {
        "michigan",
        "miami",
        "mississippi",
        "montana",
    },
    "usc": {
        "south carolina",
        "usc",
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
    candidates = (
        ambiguous_candidate_teams(
            hint
        )
    )

    if not candidates:
        return False

    aliases = competitor_aliases(
        competitor
    )

    for candidate in candidates:

        if (
            aliases
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

    hint_key = norm(
        hint
    )

    # Resolver-local context-only aliases are intentionally not
    # global football identities. They may participate only in
    # constrained two-team matching.
    if (
        hint_key
        in AMBIGUOUS_CONTEXT_GROUPS
    ):
        if not allow_contextual_ambiguous:
            return False

        return (
            comp_matches_ambiguous_hint(
                competitor,
                hint,
            )
        )

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
    Bare ambiguous aliases never qualify for one-team resolution.

    This includes both shared football_identity ambiguous aliases
    and resolver-local context-only aliases such as TU and UM.
    """

    if not hint:
        return False

    if (
        is_ambiguous_hint(
            hint
        )
        or norm(hint)
        in AMBIGUOUS_CONTEXT_GROUPS
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
    Two-team matching can safely use a contextual ambiguous
    alias because the second team constrains the game.

    Example:

        OSU @ Houston

    can resolve if exactly one ESPN event contains Houston and
    one of the legitimate OSU candidates.
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

    merged = list(
        by_id.values()
    )

    register_slate_identities(
        merged
    )

    return merged


# ============================================================
# ESPN SCOREBOARD RETRIEVAL
# ============================================================

def fetch_scoreboard_group(
    day,
    group,
):
    """
    Fetch one ESPN scoreboard view.

    We use exactly one calendar day per request. This avoids the
    date-range endpoint that ESPN is currently rejecting.
    """

    datestring = (
        day.strftime(
            "%Y%m%d"
        )
    )

    params = {
        "dates":
            datestring,

        "limit":
            ESPN_LIMIT,
    }

    if group is not None:

        params[
            "groups"
        ] = group

    payload = request_json(
        ESPN_SCOREBOARD,
        params,
    )

    events = (
        payload.get(
            "events"
        )
        or []
    )

    return events


def fetch_complete_day(
    day
):
    """
    Build one day's slate from overlapping ESPN group views.

    This intentionally favors completeness over minimizing API
    calls.

    A game returned through multiple views is merged by
    event_id.
    """

    all_events = []

    successful_views = 0

    for group in ESPN_GROUPS:

        label = (
            str(group)
            if group is not None
            else "default"
        )

        try:

            events = (
                fetch_scoreboard_group(
                    day,
                    group,
                )
            )

            successful_views += 1

            print(
                "ESPN DAY VIEW:",
                day.strftime(
                    "%Y%m%d"
                ),
                "| group:",
                label,
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
                "ESPN DAY VIEW:",
                day.strftime(
                    "%Y%m%d"
                ),
                "FAILED",
                "| group:",
                label,
                "|",
                type(exc).__name__,
                exc,
            )

    if successful_views == 0:

        raise RuntimeError(
            "Every ESPN scoreboard view failed for "
            f"{day.isoformat()}."
        )

    merged = merge_events(
        all_events
    )

    print(
        "ESPN COMPLETE DAY:",
        day.strftime(
            "%Y%m%d"
        ),
        "| unique events:",
        len(
            merged
        ),
    )

    return merged


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    """
    Exhaustively build the Pick Em week's ESPN candidate slate.

    Retrieval strategy:

      - every calendar day in the Pick Em window
      - default ESPN scoreboard
      - FBS aggregate
      - FCS aggregate
      - every major FBS conference view
      - merge everything by ESPN event_id

    There is intentionally no date-range dependency.
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
        "ESPN limit:",
        ESPN_LIMIT,
    )
    print(
        "ESPN group views:",
        len(
            ESPN_GROUPS
        ),
    )
    print(
        "===================================="
    )

    all_events = []

    current = start

    while current <= end:

        try:

            day_events = (
                fetch_complete_day(
                    current
                )
            )

            all_events.extend(
                day_events
            )

        except Exception as exc:

            print(
                "ESPN COMPLETE DAY FAILED:",
                current.isoformat(),
                "|",
                type(exc).__name__,
                exc,
            )

        current += timedelta(
            days=1
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

    Never manufactures a match.
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
# EVENT RESOLUTION
# ============================================================

def resolve_event_detailed(
    pick,
    events,
):
    """
    Permanent resolution hierarchy:

      0. existing event_id
      1. explicit matchup
      2. structured team + opponent
      3. matchup embedded in selection
      4. canonical game identity
      5. unique selected-team fallback

    A successful match must resolve to exactly one ESPN event.
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
                    "Stored ESPN event_id found.",
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
                "Stored event_id was not found. "
                "Automatic rematching blocked.",
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
                    "Explicit matchup matched exactly "
                    "one ESPN event.",
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
                    "Explicit matchup matched multiple "
                    "ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 2. TEAM + OPPONENT
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
                    "Structured team/opponent matched "
                    "exactly one ESPN event.",
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
                    "Structured team/opponent matched "
                    "multiple ESPN events.",
                candidates=
                    matches,
                confidence=0.0,
            )

    # --------------------------------------------------------
    # 3. MATCHUP INSIDE SELECTION
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
                    "Selection matchup matched exactly "
                    "one ESPN event.",
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
                    "Selection matchup matched multiple "
                    "ESPN events.",
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
                    "Canonical game identity matched "
                    "exactly one ESPN event.",
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
            "No exact or uniquely safe ESPN event "
            "match was found.",
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
# EVENT LOCKING
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
