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
    best_matchup_hints,
    canonical_matchup,
    canonical_team,
    clean_text,
    is_ambiguous_hint,
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
# This file intentionally contains NO team alias table and NO
# independent event-matching algorithm.
#
# Team identity lives in:
#
#   football_identity.py
#
# ESPN event resolution lives in:
#
#   espn_resolver.py
#
# DESIGN RULE
# -----------
# FAIL CLOSED.
#
# A stored ESPN event ID is NOT automatically trusted merely
# because that event still exists.
#
# Every existing lock must also be compatible with the wager's
# stored matchup identity.
#
# If the wager contains a complete two-team matchup and that
# matchup contradicts the locked ESPN event:
#
#   - do NOT refresh the bad lock
#   - do NOT silently rewrite the Barstool matchup
#   - do NOT guess another event
#   - mark the wager REVIEW
#   - preserve diagnostics
#
# If there is no complete two-team wager identity available,
# the existing event lock can be retained, because there is no
# independent matchup evidence with which to contradict it.
#
# New event resolution continues to use espn_resolver.py.
#
# There are NO week-specific fixes, event IDs, or hard-coded
# historical corrections in this file.
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

def event_team_names(event):
    """
    Return the two ESPN competitors using the strongest useful
    text fields available in the scoreboard event.

    This is not a second resolver. It only exposes the team
    identities of an already selected ESPN event so that an
    existing lock can be validated.
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

    teams = []

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

        if value:
            teams.append(value)

    return teams


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

    best_matchup_hints() is shared with the rest of the tracker.
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
):
    """
    Existing event exists, but the wager's own two-team identity
    contradicts ESPN.

    IMPORTANT:
    We deliberately preserve event_id and the original wager
    matchup for diagnostics. We do not silently mutate either
    side of the conflict.

    Because game_match_status becomes REVIEW, downstream code
    and the integrity audit can block publication/grading until
    the source-data problem is resolved.
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

    reason = (
        "STORED_EVENT_MATCHUP_CONFLICT"
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
        " vs ".join(wager_hints)
        if len(wager_hints) == 2
        else None
    )

    pick[
        "conflicting_event_teams"
    ] = event_teams

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
    Persist the exact ESPN event lock and the compatibility
    fields used by the existing website/tracker.
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
        #
        # Permanent rule:
        #
        # Existing event IDs are no longer blindly trusted.
        #
        # 1. Exact stored ID must still exist.
        # 2. If the wager has a complete two-team identity,
        #    that identity must agree with the ESPN event.
        # 3. Contradictions fail closed.
        # 4. We do NOT silently rewrite source matchup data.
        # ====================================================

        if stored_event_id:

            event = find_event_by_id(
                events,
                stored_event_id,
            )

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

            # -----------------------------------------------
            # EXISTING LOCK CONTRADICTS WAGER
            # -----------------------------------------------

            if compatibility is False:

                mark_existing_lock_conflict(
                    pick,
                    event,
                )

                review += 1
                stored_event_conflicts += 1

                review_reasons[
                    "STORED_EVENT_MATCHUP_CONFLICT"
                ] = (
                    review_reasons.get(
                        "STORED_EVENT_MATCHUP_CONFLICT",
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
                        " vs ".join(hints)
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
                    "  action: REVIEW — existing lock "
                    "was NOT trusted",
                )

                continue

            # -----------------------------------------------
            # EXISTING LOCK VERIFIED
            # -----------------------------------------------

            if compatibility is True:
                confidence = (
                    "LOCKED_VALIDATED"
                )

                existing_locks_validated += 1

            else:
                # There is no complete two-team wager identity
                # with which to independently challenge the
                # existing event ID.
                #
                # Preserve the lock but make that limitation
                # observable.
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
        #
        # resolve_event_detailed() is authoritative, but if the
        # wager itself has a complete two-team identity we still
        # verify the returned event before persisting it.
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
                    " vs ".join(hints)
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
        "Existing locks matchup-validated:",
        existing_locks_validated,
    )

    print(
        "Existing locks without full matchup evidence:",
        existing_locks_unverifiable,
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
        "Stored event matchup conflicts:",
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
