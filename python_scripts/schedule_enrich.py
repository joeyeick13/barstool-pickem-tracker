from __future__ import annotations

from datetime import datetime, timezone

from common import (
    PICKS_FILE,
    load_json,
    save_json,
)

from grade import (
    build_complete_week_slate,
    is_official_result,
    resolve_event,
    season_year_for_pick,
)


# ============================================================
# HELPERS
# ============================================================

def week_num(pick):
    try:
        return int(
            pick.get("week")
            or 0
        )
    except Exception:
        return 0


def event_id(event):
    if not event:
        return None

    value = event.get("id")

    if value is None:
        return None

    return str(value)


def event_kickoff(event):
    """
    ESPN scoreboard events normally provide an ISO timestamp
    in event["date"], e.g.
    2026-09-12T23:30Z.

    Store it exactly as UTC ISO so the browser can convert it
    to Pacific time.
    """

    if not event:
        return None

    value = event.get("date")

    if not value:
        competitions = (
            event.get("competitions")
            or []
        )

        if competitions:
            value = (
                competitions[0].get("date")
            )

    if not value:
        return None

    return str(value)


def event_matchup_text(event):
    """
    Human-readable ESPN matchup for debugging/auditing.
    """

    competitions = (
        event.get("competitions")
        or []
    )

    if not competitions:
        return None

    competitors = (
        competitions[0].get("competitors")
        or []
    )

    if len(competitors) != 2:
        return None

    teams = []

    for comp in competitors:
        team = (
            comp.get("team")
            or {}
        )

        name = (
            team.get("shortDisplayName")
            or team.get("displayName")
            or team.get("location")
            or team.get("abbreviation")
        )

        if name:
            teams.append(name)

    if len(teams) != 2:
        return None

    return f"{teams[0]} vs {teams[1]}"


def find_event_by_id(
    events,
    stored_event_id,
):
    if not stored_event_id:
        return None

    stored_event_id = str(
        stored_event_id
    )

    matches = [
        event
        for event in events
        if str(
            event.get("id")
            or ""
        )
        == stored_event_id
    ]

    if len(matches) == 1:
        return matches[0]

    return None


# ============================================================
# ENRICH SCHEDULE
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


    # --------------------------------------------------------
    # Only provisional CFB picks need pregame enrichment.
    #
    # Official PAT HILL rows are final and protected.
    # --------------------------------------------------------

    provisional = [
        pick
        for pick in picks
        if (
            pick.get("sport")
            in {
                "CFB",
                "NCAAF",
            }
            and not is_official_result(
                pick
            )
        )
    ]


    if not provisional:

        print()
        print(
            "========== PREGAME SCHEDULE =========="
        )
        print(
            "No provisional CFB picks to enrich."
        )
        print(
            "======================================"
        )

        return


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


    print()
    print(
        "========== PREGAME SCHEDULE =========="
    )

    print(
        "Weeks to enrich:",
        required_weeks,
    )


    week_cache = {}


    # --------------------------------------------------------
    # Build ESPN schedule once per week.
    # --------------------------------------------------------

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


    matched = 0
    reviewed = 0
    refreshed = 0


    # --------------------------------------------------------
    # Enrich each provisional pick.
    # --------------------------------------------------------

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
                []
            )
        )

        if not events:

            pick[
                "game_match_status"
            ] = "REVIEW"

            pick[
                "game_match_confidence"
            ] = "NONE"

            continue


        old_event_id = pick.get(
            "event_id"
        )


        # ====================================================
        # FIRST:
        # If this pick was already pre-matched, refresh the
        # kickoff from the same event instead of rematching.
        # ====================================================

        event = find_event_by_id(
            events,
            old_event_id,
        )


        if event is not None:

            kickoff = event_kickoff(
                event
            )

            pick[
                "game_time"
            ] = kickoff

            pick[
                "game_match_status"
            ] = "MATCHED"

            pick[
                "game_match_source"
            ] = "ESPN"

            pick[
                "game_match_confidence"
            ] = "LOCKED"

            pick[
                "game_matchup"
            ] = event_matchup_text(
                event
            )

            pick[
                "schedule_checked_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            refreshed += 1

            print(
                "PREGAME MATCH REFRESHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| event:",
                old_event_id,
                "| kickoff:",
                kickoff,
            )

            continue


        # ====================================================
        # SECOND:
        # No stored event -> use the same conservative resolver
        # grade.py already uses.
        # ====================================================

        event = resolve_event(
            pick,
            events,
        )


        if event is None:

            pick[
                "event_id"
            ] = None

            pick[
                "game_time"
            ] = None

            pick[
                "game_match_status"
            ] = "REVIEW"

            pick[
                "game_match_source"
            ] = "ESPN"

            pick[
                "game_match_confidence"
            ] = "NONE"

            pick[
                "game_matchup"
            ] = None

            pick[
                "schedule_checked_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            reviewed += 1

            print(
                "PREGAME UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| matchup:",
                pick.get("matchup"),
                "| team:",
                pick.get("team"),
            )

            continue


        matched_event_id = (
            event_id(
                event
            )
        )

        kickoff = event_kickoff(
            event
        )


        # ----------------------------------------------------
        # Store the exact ESPN event BEFORE kickoff.
        # ----------------------------------------------------

        pick[
            "event_id"
        ] = matched_event_id

        pick[
            "game_time"
        ] = kickoff

        pick[
            "game_match_status"
        ] = "MATCHED"

        pick[
            "game_match_source"
        ] = "ESPN"

        pick[
            "game_match_confidence"
        ] = "EXACT"

        pick[
            "game_matchup"
        ] = event_matchup_text(
            event
        )

        pick[
            "schedule_checked_at"
        ] = datetime.now(
            timezone.utc
        ).isoformat()

        matched += 1


        print(
            "PREGAME MATCHED:",
            pick.get("picker"),
            "|",
            pick.get("selection"),
            "| event:",
            matched_event_id,
            "| kickoff:",
            kickoff,
            "|",
            pick.get(
                "game_matchup"
            ),
        )


    save_json(
        PICKS_FILE,
        picks,
    )


    print()
    print(
        "Pregame newly matched:",
        matched,
    )

    print(
        "Pregame existing matches refreshed:",
        refreshed,
    )

    print(
        "Pregame needs review:",
        reviewed,
    )

    print(
        "======================================"
    )


if __name__ == "__main__":
    enrich_schedule()
