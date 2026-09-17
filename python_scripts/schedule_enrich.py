from __future__ import annotations

from datetime import datetime, timezone

from common import (
    PICKS_FILE,
    load_json,
    save_json,
)

from espn_resolver import (
    build_complete_week_slate,
    event_date,
    event_matchup_text,
    find_event_by_id,
    lock_pick_to_event,
    resolve_event_detailed,
)

from football_identity import (
    base_market,
    best_matchup_hints,
    canonical_team,
    clean_text,
    is_ambiguous_hint,
    normalize_bet_type,
    side_identity,
    teams_equivalent,
)


# ============================================================
# BARSTOOL PICK EM — PREGAME SCHEDULE ENRICHMENT
# ============================================================
#
# PURPOSE
# -------
# Match every provisional college-football wager to the exact
# ESPN event BEFORE grading.
#
# Team identity lives in:
#
#   football_identity.py
#
# ESPN event resolution lives in:
#
#   espn_resolver.py
#
#
# PERMANENT TRUST MODEL
# ---------------------
#
# 1. A stored ESPN event ID is never trusted merely because the
#    ID exists.
#
# 2. If the wager contains a reliable selected-team identity,
#    the selected team must belong to the stored ESPN event.
#
# 3. If the selected team belongs to the stored ESPN event but
#    the historical matchup/opponent metadata contradicts ESPN,
#    the event lock is treated as authoritative and ONLY the
#    stale matchup metadata is repaired.
#
# 4. The original stale metadata is preserved in diagnostic
#    fields before repair.
#
# 5. If the selected team does NOT belong to the ESPN event,
#    nothing is repaired. The wager fails closed into REVIEW.
#
# 6. Totals without an independent selected-team anchor are NOT
#    automatically repaired from ESPN merely to make an audit
#    pass.
#
# 7. New event resolution continues to use espn_resolver.py.
#
# 8. There are NO week-specific fixes, event IDs, post IDs,
#    picker-specific fixes, or historical correction tables in
#    this file.
#
#
# WHY THIS EXISTS
# ---------------
#
# Historical ingestion can contain a correct ESPN event lock but
# stale opponent/matchup text.
#
# Example pattern:
#
#   selection team = Team A
#   stored event   = Team A vs Team B
#   old matchup    = Team A vs Team C
#
# When Team A independently proves that the stored event belongs
# to the wager, Team C is stale metadata. We can safely repair
# the opponent from the already-validated ESPN event.
#
# But:
#
#   selection team = Team A
#   stored event   = Team B vs Team C
#
# is fundamentally different. The event itself is suspect and
# MUST NOT be blessed or rewritten automatically.
#
# ============================================================


# ============================================================
# BASIC PICK HELPERS
# ============================================================

def week_num(pick):
    try:
        return int(
            pick.get("week")
            or 0
        )

    except Exception:
        return 0


def season_year_for_pick(pick):
    """
    Determine football season year without depending on grade.py.

    Priority:
      1. explicit season_year
      2. explicit season
      3. source post timestamp
      4. current UTC year
    """

    for key in (
        "season_year",
        "season",
    ):
        value = pick.get(key)

        if value is None:
            continue

        try:
            year = int(value)

            if 2000 <= year <= 2100:
                return year

        except Exception:
            pass

    for key in (
        "posted_at",
        "created_at",
    ):
        value = pick.get(key)

        if not value:
            continue

        try:
            parsed = datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00",
                )
            )

            return parsed.year

        except Exception:
            pass

    return datetime.now(
        timezone.utc
    ).year


def is_official_result(pick):
    """
    PAT HILL result-card rows are authoritative historical
    results and must never be rematched here.
    """

    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def is_cfb_pick(pick):
    return (
        str(
            pick.get("sport")
            or ""
        )
        .upper()
        .strip()
        in {
            "CFB",
            "NCAAF",
        }
    )


def is_provisional_cfb(pick):
    return (
        is_cfb_pick(pick)
        and not is_official_result(
            pick
        )
    )


def utc_now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# ESPN EVENT TEAM HELPERS
# ============================================================

def event_competitors(event):
    """
    Return structured ESPN competitors.

    Each row contains:
        name
        home_away

    The exact ESPN event is already known when this helper is
    used. This helper does not perform event resolution.
    """

    competitions = (
        event.get("competitions")
        or []
    )

    if not competitions:
        return []

    competitors = (
        competitions[0].get(
            "competitors"
        )
        or []
    )

    rows = []

    for competitor in competitors:
        team = (
            competitor.get("team")
            or {}
        )

        value = (
            team.get("displayName")
            or team.get("shortDisplayName")
            or team.get("location")
            or team.get("name")
            or team.get("abbreviation")
        )

        value = clean_text(value)

        if not value:
            continue

        rows.append({
            "name": value,
            "home_away": str(
                competitor.get(
                    "homeAway"
                )
                or ""
            ).lower().strip(),
        })

    return rows


def event_team_names(event):
    return [
        row["name"]
        for row in event_competitors(
            event
        )
    ]


def ordered_event_teams(event):
    """
    Return:
        (away_team, home_team)

    when ESPN home/away metadata is available.

    Otherwise return the two competitors in ESPN's supplied
    order.
    """

    rows = event_competitors(
        event
    )

    if len(rows) != 2:
        return None, None

    away = next(
        (
            row["name"]
            for row in rows
            if row[
                "home_away"
            ] == "away"
        ),
        None,
    )

    home = next(
        (
            row["name"]
            for row in rows
            if row[
                "home_away"
            ] == "home"
        ),
        None,
    )

    if away and home:
        return away, home

    return (
        rows[0]["name"],
        rows[1]["name"],
    )


# ============================================================
# MATCHUP COMPATIBILITY
# ============================================================

AMBIGUOUS_CONTEXT_GROUPS = {
    "osu": {
        "ohio state",
        "oklahoma state",
        "oregon state",
    },
}


def normalized_ambiguous_context(value):
    return (
        str(value or "")
        .lower()
        .strip()
    )


def contextual_ambiguous_match(
    ambiguous_value,
    event_team,
):
    """
    Bare ambiguous aliases such as OSU cannot identify a team
    alone.

    Inside a complete two-team matchup, however, we may test
    whether an ESPN team belongs to the known ambiguity group.
    """

    key = normalized_ambiguous_context(
        ambiguous_value
    )

    candidates = (
        AMBIGUOUS_CONTEXT_GROUPS.get(
            key,
            set(),
        )
    )

    if not candidates:
        return False

    event_canonical = canonical_team(
        event_team
    )

    if not event_canonical:
        return False

    return event_canonical in candidates


def team_hint_matches_event_team(
    hint,
    event_team,
):
    """
    Shared alias identity first.

    Ambiguous aliases are allowed only as contextual candidates
    inside a complete two-team matchup.
    """

    if not hint or not event_team:
        return False

    if teams_equivalent(
        hint,
        event_team,
    ):
        return True

    if is_ambiguous_hint(hint):
        return contextual_ambiguous_match(
            hint,
            event_team,
        )

    if is_ambiguous_hint(event_team):
        return contextual_ambiguous_match(
            event_team,
            hint,
        )

    return False


def matchup_matches_event(
    wager_hints,
    event,
):
    """
    Validate an unordered two-team wager matchup against the
    exact two teams in an ESPN event.

    Returns:
        True  -> compatible
        False -> contradictory
        None  -> insufficient evidence
    """

    if len(wager_hints) != 2:
        return None

    event_teams = event_team_names(
        event
    )

    if len(event_teams) != 2:
        return None

    first_hint = wager_hints[0]
    second_hint = wager_hints[1]

    first_event = event_teams[0]
    second_event = event_teams[1]

    direct = (
        team_hint_matches_event_team(
            first_hint,
            first_event,
        )
        and
        team_hint_matches_event_team(
            second_hint,
            second_event,
        )
    )

    if direct:
        return True

    reverse = (
        team_hint_matches_event_team(
            first_hint,
            second_event,
        )
        and
        team_hint_matches_event_team(
            second_hint,
            first_event,
        )
    )

    if reverse:
        return True

    return False


def wager_matchup_hints(pick):
    """
    Return the strongest complete two-team identity supplied by
    the wager itself.
    """

    hints = best_matchup_hints(
        pick
    )

    if len(hints) != 2:
        return []

    return [
        clean_text(hints[0]),
        clean_text(hints[1]),
    ]


# ============================================================
# SELECTED-TEAM ANCHOR
# ============================================================

def wager_selected_team(pick):
    """
    Return the wager's independently selected team when the
    market supports one.

    Spread:
        ASU +14.5 -> Arizona State

    Moneyline:
        Clemson ML -> Clemson

    Team total:
        Texas State TT Over 33.5 -> Texas State

    Full-game totals do not have a selected-team anchor and
    therefore return None.
    """

    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    market = base_market(
        bet_type
    )

    if market not in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
    }:
        return None

    selected = side_identity(
        pick
    )

    selected = clean_text(
        selected
    )

    if not selected:
        return None

    return selected


def selected_team_event_matches(
    pick,
    event,
):
    """
    Determine whether the wager's selected team belongs to this
    exact ESPN event.

    Returns a diagnostic dictionary rather than a bare boolean.

    matched_count == 1:
        strong independent anchor

    matched_count == 0:
        selected team contradicts event

    matched_count > 1:
        ambiguous / unsafe
    """

    selected = wager_selected_team(
        pick
    )

    event_teams = event_team_names(
        event
    )

    if not selected:
        return {
            "selected_team": None,
            "matched_count": None,
            "matched_team": None,
            "event_teams": event_teams,
            "status": "NO_SELECTED_TEAM",
        }

    matches = [
        event_team
        for event_team in event_teams
        if team_hint_matches_event_team(
            selected,
            event_team,
        )
    ]

    if len(matches) == 1:
        status = "UNIQUE_MATCH"

    elif len(matches) == 0:
        status = "NO_MATCH"

    else:
        status = "AMBIGUOUS"

    return {
        "selected_team": selected,
        "matched_count": len(
            matches
        ),
        "matched_team": (
            matches[0]
            if len(matches) == 1
            else None
        ),
        "event_teams": event_teams,
        "status": status,
    }


def opponent_from_event(
    selected_event_team,
    event,
):
    """
    Given the already-proven selected ESPN team, return the
    other competitor.

    This only succeeds when there is exactly one other team.
    """

    event_teams = event_team_names(
        event
    )

    if len(event_teams) != 2:
        return None

    others = [
        team
        for team in event_teams
        if not teams_equivalent(
            team,
            selected_event_team,
        )
    ]

    if len(others) != 1:
        return None

    return others[0]


# ============================================================
# REVIEW STATE
# ============================================================

def mark_review(
    pick,
    reason,
    *,
    method=None,
    candidate_event_ids=None,
):
    """
    Preserve the wager while recording exactly why it was not
    safely matched.

    The pick remains retryable on later workflow runs.
    """

    pick[
        "game_match_status"
    ] = "REVIEW"

    pick[
        "game_match_confidence"
    ] = "NONE"

    pick[
        "game_match_source"
    ] = "ESPN"

    pick[
        "game_match_review_reason"
    ] = reason

    pick[
        "event_resolution_reason"
    ] = reason

    if method:
        pick[
            "event_resolution_method"
        ] = method

    else:
        pick.pop(
            "event_resolution_method",
            None,
        )

    pick[
        "event_resolution_candidates"
    ] = (
        candidate_event_ids
        or []
    )

    pick[
        "schedule_checked_at"
    ] = utc_now_iso()


def mark_schedule_unavailable(
    pick
):
    mark_review(
        pick,
        "ESPN_WEEK_SCHEDULE_UNAVAILABLE",
        method=
            "ESPN_WEEK_SCHEDULE_UNAVAILABLE",
    )


def mark_existing_lock_conflict(
    pick,
    event,
    reason,
):
    """
    Preserve diagnostics when an existing lock cannot be safely
    validated or repaired.
    """

    stored_event_id = str(
        pick.get("event_id")
        or ""
    ).strip()

    wager_hints = wager_matchup_hints(
        pick
    )

    event_teams = event_team_names(
        event
    )

    mark_review(
        pick,
        reason,
        method=
            "EXISTING_EVENT_ID_CONFLICT",
        candidate_event_ids=[
            stored_event_id
        ] if stored_event_id else [],
    )

    pick[
        "conflicting_event_matchup"
    ] = event_matchup_text(
        event
    )

    pick[
        "conflicting_wager_matchup"
    ] = (
        " vs ".join(
            wager_hints
        )
        if len(wager_hints) == 2
        else None
    )

    pick[
        "conflicting_event_teams"
    ] = event_teams

    pick[
        "conflicting_selected_team"
    ] = wager_selected_team(
        pick
    )

    pick[
        "event_matchup"
    ] = event_matchup_text(
        event
    )

    pick[
        "event_date"
    ] = event_date(
        event
    )


# ============================================================
# MATCHED STATE
# ============================================================

def clear_conflict_diagnostics(
    pick
):
    for key in (
        "conflicting_event_matchup",
        "conflicting_wager_matchup",
        "conflicting_event_teams",
        "conflicting_selected_team",
    ):
        pick.pop(
            key,
            None,
        )


def apply_match(
    pick,
    event,
    *,
    method,
    confidence,
):
    """
    Persist the exact ESPN event lock and compatibility fields.
    """

    lock_pick_to_event(
        pick,
        event,
        method=method,
    )

    clear_conflict_diagnostics(
        pick
    )

    pick[
        "game_match_status"
    ] = "MATCHED"

    pick[
        "game_match_source"
    ] = "ESPN"

    pick[
        "game_match_confidence"
    ] = confidence

    pick[
        "game_matchup"
    ] = event_matchup_text(
        event
    )

    pick[
        "game_match_review_reason"
    ] = None

    pick[
        "event_resolution_reason"
    ] = None

    pick[
        "event_resolution_candidates"
    ] = []

    pick[
        "schedule_checked_at"
    ] = utc_now_iso()


# ============================================================
# SAFE HISTORICAL METADATA REPAIR
# ============================================================

def preserve_original_value(
    pick,
    original_key,
    current_key,
):
    """
    Preserve the first known stale value.

    We never overwrite an already-recorded original value on
    later workflow runs.
    """

    if original_key in pick:
        return

    current = pick.get(
        current_key
    )

    if current is None:
        return

    pick[
        original_key
    ] = current


def repair_matchup_from_locked_event(
    pick,
    event,
    anchor,
):
    """
    Repair stale matchup/opponent metadata ONLY after the
    selected-team anchor has independently proven that the
    existing ESPN event belongs to this wager.

    Returns:
        (True, details)

    or:
        (False, reason)

    This function NEVER changes:
        event_id
        selection
        line
        picker
        week
        source_post_id
        official result
    """

    if not anchor:
        return (
            False,
            "NO_SELECTED_TEAM_ANCHOR",
        )

    if (
        anchor.get(
            "status"
        )
        != "UNIQUE_MATCH"
    ):
        return (
            False,
            "SELECTED_TEAM_NOT_UNIQUELY_IN_EVENT",
        )

    selected_event_team = (
        anchor.get(
            "matched_team"
        )
    )

    if not selected_event_team:
        return (
            False,
            "SELECTED_TEAM_EVENT_MATCH_MISSING",
        )

    opponent = opponent_from_event(
        selected_event_team,
        event,
    )

    if not opponent:
        return (
            False,
            "EVENT_OPPONENT_NOT_UNIQUE",
        )

    away_team, home_team = (
        ordered_event_teams(
            event
        )
    )

    if not away_team or not home_team:
        return (
            False,
            "EVENT_HOME_AWAY_UNAVAILABLE",
        )

    repaired_matchup = (
        f"{away_team} @ {home_team}"
    )

    # --------------------------------------------------------
    # PRESERVE ORIGINAL HISTORICAL METADATA
    # --------------------------------------------------------

    preserve_original_value(
        pick,
        "pre_espn_repair_matchup",
        "matchup",
    )

    preserve_original_value(
        pick,
        "pre_espn_repair_opponent",
        "opponent",
    )

    preserve_original_value(
        pick,
        "pre_espn_repair_team",
        "team",
    )

    # --------------------------------------------------------
    # REPAIR ONLY DERIVED GAME IDENTITY METADATA
    # --------------------------------------------------------

    pick[
        "matchup"
    ] = repaired_matchup

    pick[
        "opponent"
    ] = opponent

    # If team is missing, populate it from the independently
    # proven selected ESPN team.
    #
    # If team already exists, preserve the source value. Its
    # identity has already been proven equivalent to the ESPN
    # selected team by the anchor check.
    if not clean_text(
        pick.get(
            "team"
        )
    ):
        pick[
            "team"
        ] = selected_event_team

    pick[
        "matchup_metadata_repaired"
    ] = True

    pick[
        "matchup_metadata_repair_source"
    ] = "ESPN_LOCKED_EVENT"

    pick[
        "matchup_metadata_repair_reason"
    ] = (
        "SELECTED_TEAM_VALIDATED_EVENT"
    )

    pick[
        "matchup_metadata_repaired_at"
    ] = utc_now_iso()

    pick[
        "matchup_metadata_repair_event_id"
    ] = str(
        event.get("id")
        or ""
    )

    return (
        True,
        {
            "matchup": repaired_matchup,
            "opponent": opponent,
            "selected_event_team":
                selected_event_team,
        },
    )


# ============================================================
# SCHEDULE ENRICHMENT
# ============================================================

def enrich_schedule():

    picks = load_json(
        PICKS_FILE,
        [],
    )

    if not isinstance(
        picks,
        list,
    ):
        picks = []

    provisional = [
        pick
        for pick in picks
        if is_provisional_cfb(
            pick
        )
    ]

    print()
    print(
        "========== PREGAME SCHEDULE =========="
    )

    if not provisional:
        print(
            "No provisional CFB picks to enrich."
        )

        print(
            "======================================"
        )

        return

    # ========================================================
    # DETERMINE REQUIRED WEEKS
    # ========================================================

    required_weeks = sorted({
        (
            season_year_for_pick(
                pick
            ),
            week_num(
                pick
            ),
        )
        for pick in provisional
        if week_num(
            pick
        ) > 0
    })

    print(
        "Weeks to enrich:",
        required_weeks,
    )

    week_cache = {}

    # ========================================================
    # FETCH EACH ESPN WEEK SLATE ONCE
    # ========================================================

    for (
        season,
        week,
    ) in required_weeks:

        week_picks = [
            pick
            for pick in provisional
            if (
                season_year_for_pick(
                    pick
                )
                == season
                and week_num(
                    pick
                )
                == week
            )
        ]

        try:
            events = (
                build_complete_week_slate(
                    week_picks,
                    season,
                    week,
                )
            )

            week_cache[
                (
                    season,
                    week,
                )
            ] = events

            print(
                "Loaded ESPN schedule:",
                season,
                "Week",
                week,
                "| events:",
                len(events),
            )

        except Exception as exc:
            print(
                "PREGAME ESPN FETCH FAILED:",
                season,
                "Week",
                week,
                "|",
                type(exc).__name__,
                exc,
            )

    # ========================================================
    # COUNTERS
    # ========================================================

    newly_matched = 0
    refreshed = 0
    review = 0

    stored_event_missing = 0
    stored_event_conflicts = 0

    existing_locks_validated = 0
    existing_locks_unverifiable = 0

    metadata_repairs = 0
    repair_failures = 0
    selected_team_conflicts = 0

    resolution_methods = {}
    review_reasons = {}

    # ========================================================
    # RESOLVE EVERY PROVISIONAL PICK
    # ========================================================

    for pick in provisional:

        season = (
            season_year_for_pick(
                pick
            )
        )

        week = week_num(
            pick
        )

        events = (
            week_cache.get(
                (
                    season,
                    week,
                ),
                [],
            )
        )

        # ----------------------------------------------------
        # ESPN SLATE UNAVAILABLE
        # ----------------------------------------------------

        if not events:
            mark_schedule_unavailable(
                pick
            )

            review += 1

            review_reasons[
                "ESPN_WEEK_SCHEDULE_UNAVAILABLE"
            ] = (
                review_reasons.get(
                    "ESPN_WEEK_SCHEDULE_UNAVAILABLE",
                    0,
                )
                + 1
            )

            print(
                "PREGAME REVIEW:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| ESPN schedule unavailable",
            )

            continue

        stored_event_id = str(
            pick.get("event_id")
            or ""
        ).strip()

        # ====================================================
        # EXISTING EVENT LOCK
        # ====================================================

        if stored_event_id:

            event = find_event_by_id(
                events,
                stored_event_id,
            )

            # ------------------------------------------------
            # STORED EVENT NO LONGER EXISTS IN WEEK SLATE
            # ------------------------------------------------

            if event is None:
                mark_review(
                    pick,
                    "STORED_EVENT_NOT_FOUND",
                    method=
                        "EXISTING_EVENT_ID_MISSING",
                )

                review += 1
                stored_event_missing += 1

                review_reasons[
                    "STORED_EVENT_NOT_FOUND"
                ] = (
                    review_reasons.get(
                        "STORED_EVENT_NOT_FOUND",
                        0,
                    )
                    + 1
                )

                print(
                    "PREGAME STORED EVENT MISSING:",
                    pick.get("picker"),
                    "|",
                    pick.get("selection"),
                    "| event:",
                    stored_event_id,
                )

                continue

            hints = wager_matchup_hints(
                pick
            )

            compatibility = (
                matchup_matches_event(
                    hints,
                    event,
                )
            )

            # ------------------------------------------------
            # EXISTING MATCHUP AGREES WITH EVENT
            # ------------------------------------------------

            if compatibility is True:

                apply_match(
                    pick,
                    event,
                    method=
                        "EXISTING_EVENT_ID",
                    confidence=
                        "LOCKED_VALIDATED",
                )

                refreshed += 1
                existing_locks_validated += 1

                resolution_methods[
                    "EXISTING_EVENT_ID"
                ] = (
                    resolution_methods.get(
                        "EXISTING_EVENT_ID",
                        0,
                    )
                    + 1
                )

                print(
                    "PREGAME MATCH REFRESHED:",
                    pick.get("picker"),
                    "|",
                    pick.get("selection"),
                    "| event:",
                    stored_event_id,
                    "| kickoff:",
                    event_date(event),
                    "|",
                    event_matchup_text(
                        event
                    ),
                    "| validation:",
                    "LOCKED_VALIDATED",
                )

                continue

            # ------------------------------------------------
            # EXISTING MATCHUP CONTRADICTS EVENT
            # ------------------------------------------------
            #
            # We now distinguish:
            #
            # A. selected team proves the event belongs to this
            #    wager -> repair stale opponent/matchup metadata
            #
            # B. selected team does not belong to event ->
            #    event itself is suspect -> REVIEW
            #
            # C. no independent selected team exists ->
            #    insufficient proof -> REVIEW
            # ------------------------------------------------

            if compatibility is False:

                anchor = (
                    selected_team_event_matches(
                        pick,
                        event,
                    )
                )

                # --------------------------------------------
                # SAFE SELF-HEAL
                # --------------------------------------------

                if (
                    anchor.get(
                        "status"
                    )
                    == "UNIQUE_MATCH"
                ):

                    repaired, details = (
                        repair_matchup_from_locked_event(
                            pick,
                            event,
                            anchor,
                        )
                    )

                    if repaired:

                        # Re-validate the repaired matchup.
                        repaired_hints = (
                            wager_matchup_hints(
                                pick
                            )
                        )

                        repaired_compatibility = (
                            matchup_matches_event(
                                repaired_hints,
                                event,
                            )
                        )

                        if (
                            repaired_compatibility
                            is True
                        ):

                            apply_match(
                                pick,
                                event,
                                method=
                                    "EXISTING_EVENT_ID_METADATA_REPAIR",
                                confidence=
                                    "LOCKED_SELECTED_TEAM_VALIDATED",
                            )

                            refreshed += 1
                            metadata_repairs += 1
                            existing_locks_validated += 1

                            resolution_methods[
                                "EXISTING_EVENT_ID_METADATA_REPAIR"
                            ] = (
                                resolution_methods.get(
                                    "EXISTING_EVENT_ID_METADATA_REPAIR",
                                    0,
                                )
                                + 1
                            )

                            print(
                                "PREGAME METADATA REPAIRED:",
                                pick.get(
                                    "picker"
                                ),
                                "|",
                                pick.get(
                                    "selection"
                                ),
                                "| event:",
                                stored_event_id,
                            )

                            print(
                                "  selected team:",
                                anchor.get(
                                    "selected_team"
                                ),
                            )

                            print(
                                "  validated ESPN team:",
                                anchor.get(
                                    "matched_team"
                                ),
                            )

                            print(
                                "  repaired matchup:",
                                details.get(
                                    "matchup"
                                ),
                            )

                            print(
                                "  repaired opponent:",
                                details.get(
                                    "opponent"
                                ),
                            )

                            continue

                        # Repair itself failed its defensive
                        # post-repair verification.
                        repair_failures += 1

                        reason = (
                            "METADATA_REPAIR_POSTCHECK_FAILED"
                        )

                        mark_existing_lock_conflict(
                            pick,
                            event,
                            reason,
                        )

                        review += 1
                        stored_event_conflicts += 1

                        review_reasons[
                            reason
                        ] = (
                            review_reasons.get(
                                reason,
                                0,
                            )
                            + 1
                        )

                        print(
                            "PREGAME REPAIR POSTCHECK FAILED:",
                            pick.get(
                                "picker"
                            ),
                            "|",
                            pick.get(
                                "selection"
                            ),
                            "| event:",
                            stored_event_id,
                        )

                        continue

                    # Selected team anchored the event, but
                    # reconstruction itself was unsafe.
                    repair_failures += 1

                    reason = (
                        "SAFE_METADATA_REPAIR_FAILED"
                    )

                    mark_existing_lock_conflict(
                        pick,
                        event,
                        reason,
                    )

                    review += 1
                    stored_event_conflicts += 1

                    review_reasons[
                        reason
                    ] = (
                        review_reasons.get(
                            reason,
                            0,
                        )
                        + 1
                    )

                    print(
                        "PREGAME METADATA REPAIR FAILED:",
                        pick.get("picker"),
                        "|",
                        pick.get("selection"),
                        "| event:",
                        stored_event_id,
                        "| reason:",
                        details,
                    )

                    continue

                # --------------------------------------------
                # SELECTED TEAM CONTRADICTS EVENT
                # --------------------------------------------

                if (
                    anchor.get(
                        "status"
                    )
                    == "NO_MATCH"
                ):

                    reason = (
                        "SELECTED_TEAM_EVENT_CONFLICT"
                    )

                    mark_existing_lock_conflict(
                        pick,
                        event,
                        reason,
                    )

                    review += 1
                    stored_event_conflicts += 1
                    selected_team_conflicts += 1

                    review_reasons[
                        reason
                    ] = (
                        review_reasons.get(
                            reason,
                            0,
                        )
                        + 1
                    )

                    print(
                        "PREGAME SELECTED TEAM CONFLICT:",
                        pick.get("picker"),
                        "|",
                        pick.get("selection"),
                        "| event:",
                        stored_event_id,
                    )

                    print(
                        "  selected team:",
                        anchor.get(
                            "selected_team"
                        ),
                    )

                    print(
                        "  ESPN teams:",
                        anchor.get(
                            "event_teams"
                        ),
                    )

                    print(
                        "  action: REVIEW — event lock "
                        "was NOT trusted",
                    )

                    continue

                # --------------------------------------------
                # NO SAFE INDEPENDENT ANCHOR
                # --------------------------------------------

                reason = (
                    "MATCHUP_CONFLICT_WITHOUT_SAFE_TEAM_ANCHOR"
                )

                mark_existing_lock_conflict(
                    pick,
                    event,
                    reason,
                )

                review += 1
                stored_event_conflicts += 1

                review_reasons[
                    reason
                ] = (
                    review_reasons.get(
                        reason,
                        0,
                    )
                    + 1
                )

                print(
                    "PREGAME STORED EVENT CONFLICT:",
                    pick.get("picker"),
                    "|",
                    pick.get("selection"),
                    "| event:",
                    stored_event_id,
                )

                print(
                    "  wager matchup:",
                    (
                        " vs ".join(
                            hints
                        )
                        if len(hints) == 2
                        else None
                    ),
                )

                print(
                    "  ESPN matchup:",
                    event_matchup_text(
                        event
                    ),
                )

                print(
                    "  selected-team anchor:",
                    anchor.get(
                        "status"
                    ),
                )

                print(
                    "  action: REVIEW — insufficient "
                    "evidence for automatic repair",
                )

                continue

            # ------------------------------------------------
            # NO COMPLETE MATCHUP IDENTITY
            # ------------------------------------------------
            #
            # There is no two-team wager identity to challenge
            # the existing event.
            #
            # If a selected team exists, validate it.
            #
            # If there is no selected team (for example a total
            # with incomplete matchup metadata), preserve the
            # existing lock but explicitly label it unverified.
            # ------------------------------------------------

            anchor = (
                selected_team_event_matches(
                    pick,
                    event,
                )
            )

            if (
                anchor.get(
                    "status"
                )
                == "NO_MATCH"
            ):

                reason = (
                    "SELECTED_TEAM_EVENT_CONFLICT"
                )

                mark_existing_lock_conflict(
                    pick,
                    event,
                    reason,
                )

                review += 1
                stored_event_conflicts += 1
                selected_team_conflicts += 1

                review_reasons[
                    reason
                ] = (
                    review_reasons.get(
                        reason,
                        0,
                    )
                    + 1
                )

                print(
                    "PREGAME SELECTED TEAM CONFLICT:",
                    pick.get("picker"),
                    "|",
                    pick.get("selection"),
                    "| event:",
                    stored_event_id,
                )

                print(
                    "  selected team:",
                    anchor.get(
                        "selected_team"
                    ),
                )

                print(
                    "  ESPN teams:",
                    anchor.get(
                        "event_teams"
                    ),
                )

                continue

            if (
                anchor.get(
                    "status"
                )
                == "UNIQUE_MATCH"
            ):
                confidence = (
                    "LOCKED_SELECTED_TEAM_VALIDATED"
                )

                existing_locks_validated += 1

            else:
                confidence = (
                    "LOCKED_UNVERIFIED_MATCHUP"
                )

                existing_locks_unverifiable += 1

            apply_match(
                pick,
                event,
                method=
                    "EXISTING_EVENT_ID",
                confidence=confidence,
            )

            refreshed += 1

            resolution_methods[
                "EXISTING_EVENT_ID"
            ] = (
                resolution_methods.get(
                    "EXISTING_EVENT_ID",
                    0,
                )
                + 1
            )

            print(
                "PREGAME MATCH REFRESHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| event:",
                stored_event_id,
                "| kickoff:",
                event_date(event),
                "|",
                event_matchup_text(
                    event
                ),
                "| validation:",
                confidence,
            )

            continue

        # ====================================================
        # NEW EVENT RESOLUTION
        # ====================================================

        result = (
            resolve_event_detailed(
                pick,
                events,
            )
        )

        event = result.get(
            "event"
        )

        method = (
            result.get("method")
            or
            "NO_CONFIDENT_EVENT_MATCH"
        )

        reason = (
            result.get("reason")
            or
            "NO_CONFIDENT_EVENT_MATCH"
        )

        candidates = (
            result.get(
                "candidate_event_ids"
            )
            or []
        )

        # ----------------------------------------------------
        # UNRESOLVED
        # ----------------------------------------------------

        if event is None:

            pick[
                "event_id"
            ] = None

            pick[
                "game_time"
            ] = None

            pick[
                "game_matchup"
            ] = None

            mark_review(
                pick,
                reason,
                method=method,
                candidate_event_ids=
                    candidates,
            )

            review += 1

            review_reasons[
                method
            ] = (
                review_reasons.get(
                    method,
                    0,
                )
                + 1
            )

            print(
                "PREGAME UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
            )

            print(
                "  matchup:",
                pick.get("matchup"),
            )

            print(
                "  team:",
                pick.get("team"),
                "| opponent:",
                pick.get("opponent"),
            )

            print(
                "  method:",
                method,
            )

            print(
                "  reason:",
                reason,
            )

            if candidates:
                print(
                    "  candidate event IDs:",
                    candidates,
                )

            continue

        # ----------------------------------------------------
        # SUCCESSFUL NEW LOCK
        # ----------------------------------------------------

        confidence_value = (
            result.get(
                "confidence"
            )
        )

        if (
            confidence_value
            is not None
            and confidence_value
            >= 0.99
        ):
            confidence_label = (
                "EXACT"
            )

        else:
            confidence_label = (
                "UNIQUE"
            )

        # ----------------------------------------------------
        # DEFENSIVE VALIDATION OF NEW LOCK
        # ----------------------------------------------------

        hints = wager_matchup_hints(
            pick
        )

        compatibility = (
            matchup_matches_event(
                hints,
                event,
            )
        )

        if compatibility is False:

            # ------------------------------------------------
            # A new resolution is NOT allowed to use the
            # historical-metadata repair rule.
            #
            # The repair rule is only for a previously locked
            # event that is independently anchored by the
            # selected team.
            #
            # A brand-new contradictory resolution must fail
            # closed.
            # ------------------------------------------------

            candidate_id = str(
                event.get("id")
                or ""
            )

            mark_review(
                pick,
                "NEW_EVENT_MATCHUP_CONFLICT",
                method=
                    "RESOLVER_RESULT_CONFLICT",
                candidate_event_ids=[
                    candidate_id
                ] if candidate_id else [],
            )

            pick[
                "event_id"
            ] = None

            pick[
                "game_time"
            ] = None

            pick[
                "game_matchup"
            ] = None

            review += 1

            review_reasons[
                "NEW_EVENT_MATCHUP_CONFLICT"
            ] = (
                review_reasons.get(
                    "NEW_EVENT_MATCHUP_CONFLICT",
                    0,
                )
                + 1
            )

            print(
                "PREGAME RESOLVER CONFLICT:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
            )

            print(
                "  wager matchup:",
                (
                    " vs ".join(
                        hints
                    )
                    if len(hints) == 2
                    else None
                ),
            )

            print(
                "  resolver event:",
                event_matchup_text(
                    event
                ),
            )

            print(
                "  action: REVIEW — event was NOT locked",
            )

            continue

        # ----------------------------------------------------
        # NEW LOCK SELECTED-TEAM DEFENSE
        # ----------------------------------------------------

        anchor = (
            selected_team_event_matches(
                pick,
                event,
            )
        )

        if (
            anchor.get(
                "status"
            )
            == "NO_MATCH"
        ):

            candidate_id = str(
                event.get("id")
                or ""
            )

            mark_review(
                pick,
                "NEW_EVENT_SELECTED_TEAM_CONFLICT",
                method=
                    "RESOLVER_SELECTED_TEAM_CONFLICT",
                candidate_event_ids=[
                    candidate_id
                ] if candidate_id else [],
            )

            pick[
                "event_id"
            ] = None

            pick[
                "game_time"
            ] = None

            pick[
                "game_matchup"
            ] = None

            review += 1

            review_reasons[
                "NEW_EVENT_SELECTED_TEAM_CONFLICT"
            ] = (
                review_reasons.get(
                    "NEW_EVENT_SELECTED_TEAM_CONFLICT",
                    0,
                )
                + 1
            )

            print(
                "PREGAME NEW EVENT SELECTED TEAM CONFLICT:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| selected team:",
                anchor.get(
                    "selected_team"
                ),
                "| ESPN teams:",
                anchor.get(
                    "event_teams"
                ),
            )

            continue

        apply_match(
            pick,
            event,
            method=method,
            confidence=
                confidence_label,
        )

        newly_matched += 1

        resolution_methods[
            method
        ] = (
            resolution_methods.get(
                method,
                0,
            )
            + 1
        )

        print(
            "PREGAME MATCHED:",
            pick.get("picker"),
            "|",
            pick.get("selection"),
            "| event:",
            pick.get("event_id"),
            "| kickoff:",
            pick.get("game_time"),
            "|",
            pick.get("game_matchup"),
            "| method:",
            method,
        )

    # ========================================================
    # SAVE
    # ========================================================

    save_json(
        PICKS_FILE,
        picks,
    )

    # ========================================================
    # AUDIT SUMMARY
    # ========================================================

    print()
    print(
        "========== PREGAME AUDIT ============="
    )

    print(
        "Provisional CFB wagers:",
        len(provisional),
    )

    print(
        "Pregame newly matched:",
        newly_matched,
    )

    print(
        "Pregame existing matches refreshed:",
        refreshed,
    )

    print(
        "Existing locks validated:",
        existing_locks_validated,
    )

    print(
        "Existing locks without full independent evidence:",
        existing_locks_unverifiable,
    )

    print(
        "Historical matchup metadata repaired:",
        metadata_repairs,
    )

    print(
        "Metadata repair failures:",
        repair_failures,
    )

    print(
        "Selected-team/event conflicts:",
        selected_team_conflicts,
    )

    print(
        "Pregame needs review:",
        review,
    )

    print(
        "Stored event IDs missing:",
        stored_event_missing,
    )

    print(
        "Stored event matchup conflicts requiring review:",
        stored_event_conflicts,
    )

    print()

    print(
        "Resolution methods:"
    )

    if resolution_methods:

        for method in sorted(
            resolution_methods
        ):
            print(
                " ",
                method,
                ":",
                resolution_methods[
                    method
                ],
            )

    else:
        print(
            "  none"
        )

    print()

    print(
        "Review reasons:"
    )

    if review_reasons:

        for reason in sorted(
            review_reasons
        ):
            print(
                " ",
                reason,
                ":",
                review_reasons[
                    reason
                ],
            )

    else:
        print(
            "  none"
        )

    print(
        "======================================"
    )


if __name__ == "__main__":
    enrich_schedule()
