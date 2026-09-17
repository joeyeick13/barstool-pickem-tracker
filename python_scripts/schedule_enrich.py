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


# ============================================================
# BARSTOOL PICK EM — PREGAME SCHEDULE ENRICHMENT
# ============================================================
#
# PURPOSE
# -------
# Match every provisional college-football wager to the exact
# ESPN event BEFORE grading.
#
# This file intentionally contains NO team aliases and NO
# event-matching algorithm.
#
# Those now live in:
#
#   football_identity.py
#   espn_resolver.py
#
# That means ingestion, schedule matching and grading can use
# the same permanent football identity system.
#
# DESIGN RULE
# -----------
# FAIL CLOSED.
#
# If we cannot confidently identify the game, we mark the pick
# for review and keep trying on later workflow runs.
#
# We never guess an ESPN event.
# ============================================================


# ============================================================
# BASIC PICK HELPERS
# ============================================================

def week_num(
    pick
):
    try:
        return int(
            pick.get(
                "week"
            )
            or 0
        )

    except Exception:
        return 0


def season_year_for_pick(
    pick
):
    """
    Determine football season year without depending on grade.py.

    Priority:
      1. explicit season_year
      2. explicit season
      3. source post timestamp
      4. current UTC year

    College-football Pick Em runs during the fall, so the
    timestamp year is the appropriate season year.
    """

    for key in (
        "season_year",
        "season",
    ):
        value = pick.get(
            key
        )

        if value is None:
            continue

        try:
            year = int(
                value
            )

            if (
                2000
                <= year
                <= 2100
            ):
                return year

        except Exception:
            pass

    for key in (
        "posted_at",
        "created_at",
    ):
        value = pick.get(
            key
        )

        if not value:
            continue

        try:
            parsed = (
                datetime.fromisoformat(
                    str(value).replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

            return parsed.year

        except Exception:
            pass

    return datetime.now(
        timezone.utc
    ).year


def is_official_result(
    pick
):
    """
    PAT HILL result-card rows are authoritative historical
    results and should never be rematched by pregame schedule
    enrichment.
    """

    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def is_cfb_pick(
    pick
):
    return (
        str(
            pick.get(
                "sport"
            )
            or ""
        )
        .upper()
        .strip()
        in {
            "CFB",
            "NCAAF",
        }
    )


def is_provisional_cfb(
    pick
):
    return (
        is_cfb_pick(
            pick
        )
        and not is_official_result(
            pick
        )
    )


def utc_now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


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
    matched.

    The pick remains retryable on the next workflow run.
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


# ============================================================
# MATCHED STATE
# ============================================================

def apply_match(
    pick,
    event,
    *,
    method,
    confidence,
):
    """
    Persist the exact ESPN event lock and the compatibility
    fields already used by the existing website/tracker.
    """

    lock_pick_to_event(
        pick,
        event,
        method=method,
    )

    # Existing tracker/UI field names.
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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| ESPN schedule unavailable",
            )

            continue

        stored_event_id = str(
            pick.get(
                "event_id"
            )
            or ""
        ).strip()

        # ====================================================
        # EXISTING EVENT LOCK
        # ====================================================
        #
        # Existing event IDs are NEVER automatically replaced
        # by another event.
        #
        # We only refresh metadata from the exact same ESPN ID.
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

            apply_match(
                pick,
                event,
                method=
                    "EXISTING_EVENT_ID",
                confidence=
                    "LOCKED",
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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| event:",
                stored_event_id,
                "| kickoff:",
                event_date(
                    event
                ),
                "|",
                event_matchup_text(
                    event
                ),
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
            result.get(
                "method"
            )
            or
            "NO_CONFIDENT_EVENT_MATCH"
        )

        reason = (
            result.get(
                "reason"
            )
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

            # No event was ever locked, so these fields should
            # remain empty. We deliberately do NOT fabricate
            # schedule information.
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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
            )

            print(
                "  matchup:",
                pick.get(
                    "matchup"
                ),
            )

            print(
                "  team:",
                pick.get(
                    "team"
                ),
                "| opponent:",
                pick.get(
                    "opponent"
                ),
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
            pick.get(
                "picker"
            ),
            "|",
            pick.get(
                "selection"
            ),
            "| event:",
            pick.get(
                "event_id"
            ),
            "| kickoff:",
            pick.get(
                "game_time"
            ),
            "|",
            pick.get(
                "game_matchup"
            ),
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
        len(
            provisional
        ),
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
        "Pregame needs review:",
        review,
    )

    print(
        "Stored event IDs missing:",
        stored_event_missing,
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
