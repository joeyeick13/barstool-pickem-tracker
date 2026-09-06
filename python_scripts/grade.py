from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

from common import (
    load_json,
    save_json,
    PICKS_FILE,
    american_profit,
    now_iso,
)

ESPN = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football/scoreboard"
)

REGULAR_SEASON = 2
FBS_GROUP = 80

# If ESPN suddenly returns only a handful
# of events, do NOT wipe or change grades.
MIN_VALID_WEEK_EVENTS = 20

SUPPORTED = {
    "SPREAD",
    "TOTAL",
    "MONEYLINE",
    "TEAM_TOTAL",
}

# Exact alias matching only.
# No fuzzy matching.
ALIASES = {
    "alabama": {
        "alabama",
        "bama",
        "crimson tide",
    },
    "auburn": {
        "auburn",
        "aub",
        "auburn tigers",
    },
    "baylor": {
        "baylor",
        "baylor bears",
    },
    "boise state": {
        "boise",
        "boise state",
        "boise state broncos",
    },
    "california": {
        "cal",
        "california",
        "california golden bears",
    },
    "clemson": {
        "clem",
        "clemson",
        "clemson tigers",
    },
    "connecticut": {
        "uconn",
        "connecticut",
        "connecticut huskies",
    },
    "georgia tech": {
        "gatech",
        "ga tech",
        "georgia tech",
        "gt",
        "yellow jackets",
    },
    "houston": {
        "houston",
        "hou",
        "houston cougars",
    },
    "kansas": {
        "kansas",
        "ku",
        "kansas jayhawks",
    },
    "lsu": {
        "lsu",
        "louisiana state",
        "lsu tigers",
    },
    "memphis": {
        "mem",
        "memphis",
        "memphis tigers",
    },
    "miami florida": {
        "miami",
        "miami fl",
        "miami florida",
        "miami hurricanes",
    },
    "miami ohio": {
        "miami oh",
        "miami ohio",
        "miami redhawks",
    },
    "michigan": {
        "mich",
        "michigan",
        "michigan wolverines",
    },
    "minnesota": {
        "minn",
        "minnesota",
        "minnesota golden gophers",
    },
    "notre dame": {
        "nd",
        "notre dame",
        "notre dame fighting irish",
    },
    "ohio state": {
        "ohio state",
        "osu",
        "ohio state buckeyes",
    },
    "oklahoma state": {
        "ok state",
        "ok st",
        "oklahoma state",
        "oklahoma state cowboys",
    },
    "oregon": {
        "ore",
        "oregon",
        "oregon ducks",
    },
    "pittsburgh": {
        "pitt",
        "pittsburgh",
        "pittsburgh panthers",
    },
    "rutgers": {
        "rutgers",
        "ru",
        "rutg",
        "rutgers scarlet knights",
    },
    "south florida": {
        "usf",
        "south florida",
        "south florida bulls",
    },
    "texas": {
        "tex",
        "texas",
        "texas longhorns",
    },
    "texas a&m": {
        "a&m",
        "texas a&m",
        "texas am",
        "texas a m",
        "texas a and m",
        "aggies",
        "texas a&m aggies",
    },
    "toledo": {
        "tol",
        "toledo",
        "toledo rockets",
    },
    "ucla": {
        "ucla",
        "ucla bruins",
    },
    "unlv": {
        "unlv",
        "unlv rebels",
    },
    "wake forest": {
        "wake",
        "wake forest",
        "wake forest demon deacons",
    },
    "washington": {
        "wash",
        "uw",
        "washington",
        "washington huskies",
    },
    "washington state": {
        "wazzu",
        "wsu",
        "washington state",
        "washington state cougars",
    },
    "west virginia": {
        "wvu",
        "west virginia",
        "west virginia mountaineers",
    },
    "wisconsin": {
        "wis",
        "wisc",
        "wisconsin",
        "wisconsin badgers",
    },
    "fiu": {
        "fiu",
        "florida international",
        "fiu panthers",
    },
}

# These require quarter/half scoring data
# and must never be graded from a final score.
PARTIAL_MARKERS = (
    "1q",
    "1st q",
    "first quarter",
    "2q",
    "2nd q",
    "second quarter",
    "1h",
    "1st h",
    "first half",
    "2h",
    "2nd h",
    "second half",
)


def norm(value):
    text = str(value or "").lower()

    text = (
        text.replace("&", " and ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("'", "")
        .replace(".", " ")
    )

    text = re.sub(
        r"[^a-z0-9 ]",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def alias_group(value):
    normalized = norm(value)

    if not normalized:
        return set()

    output = {
        normalized
    }

    for canonical, names in ALIASES.items():
        group = {
            norm(canonical)
        }

        group |= {
            norm(x)
            for x in names
        }

        if normalized in group:
            output |= group

    return output


def has_partial_game_marker(pick):
    text = (
        " "
        + norm(
            pick.get("selection")
        )
        + " "
    )

    return any(
        marker in text
        for marker in PARTIAL_MARKERS
    )


def competitors(event):
    competitions = (
        event.get("competitions")
        or []
    )

    if not competitions:
        return []

    return (
        competitions[0]
        .get("competitors")
        or []
    )


def completed(event):
    return bool(
        event.get(
            "status",
            {},
        )
        .get(
            "type",
            {},
        )
        .get(
            "completed"
        )
    )


def espn_names(comp):
    team = (
        comp.get("team")
        or {}
    )

    names = set()

    for value in (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
    ):
        names |= alias_group(
            value
        )

    return names


def comp_matches(
    comp,
    team_hint,
):
    return bool(
        alias_group(team_hint)
        & espn_names(comp)
    )


def split_matchup(value):
    if not value:
        return []

    return [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            str(value),
            flags=re.I,
        )
        if part.strip()
    ]


def season_year_for_pick(
    pick
):
    if pick.get("season"):
        return int(
            pick["season"]
        )

    posted = pick.get(
        "posted_at"
    )

    if posted:
        try:
            return (
                datetime.fromisoformat(
                    str(posted).replace(
                        "Z",
                        "+00:00",
                    )
                ).year
            )
        except Exception:
            pass

    return datetime.now(
        timezone.utc
    ).year


def fetch_week_events(
    season_year,
    week,
):
    """
    Fetch ESPN's actual official
    college-football week.

    No homemade Monday-Sunday window.
    """

    response = requests.get(
        ESPN,
        params={
            "dates": str(
                int(season_year)
            ),
            "seasontype": (
                REGULAR_SEASON
            ),
            "week": int(week),
            "groups": FBS_GROUP,
            "limit": 1000,
        },
        timeout=30,
    )

    response.raise_for_status()

    events = (
        response.json()
        .get(
            "events",
            [],
        )
    )

    # Production safeguard.
    #
    # If ESPN changes or partially fails,
    # do not destroy existing grades.
    if (
        len(events)
        < MIN_VALID_WEEK_EVENTS
    ):
        raise RuntimeError(
            "ESPN returned only "
            f"{len(events)} events for "
            f"{season_year} Week {week}; "
            "refusing to alter grades."
        )

    return events


def unique_event_for_team(
    team_hint,
    events,
):
    """
    Resolve:
        team + ESPN week

    A side only grades if the team appears
    in exactly ONE game in that week.
    """

    if not team_hint:
        return None

    matches = []

    for event in events:
        comps = competitors(
            event
        )

        if len(comps) != 2:
            continue

        if any(
            comp_matches(
                comp,
                team_hint,
            )
            for comp in comps
        ):
            matches.append(
                event
            )

    if len(matches) == 1:
        return matches[0]

    # Zero matches or multiple matches:
    # never guess.
    return None


def matchup_hints(
    pick
):
    """
    Return identifying team hints for
    a game total.

    Examples:

    FIU/USF Over 53.5
        -> FIU + USF

    Texas A&M Over 53.5
        -> Texas A&M

    Generic Over 55.5
        -> nothing
    """

    parts = split_matchup(
        pick.get("matchup")
    )

    if parts:
        return parts[:2]

    team = pick.get(
        "team"
    )

    opponent = pick.get(
        "opponent"
    )

    if (
        team
        and opponent
    ):
        return [
            str(team),
            str(opponent),
        ]

    if team:
        return [
            str(team)
        ]

    selection = str(
        pick.get("selection")
        or ""
    )

    match = re.match(
        r"^\s*(.+?)\s+"
        r"(?:over|under)\s+\d",
        selection,
        flags=re.I,
    )

    if match:
        candidate = (
            match.group(1)
            .strip()
        )

        if (
            candidate
            and norm(candidate)
            not in {
                "over",
                "under",
            }
        ):
            return [
                candidate
            ]

    return []


def unique_event_for_total(
    pick,
    events,
):
    hints = matchup_hints(
        pick
    )

    # Generic Over/Under with no game
    # identifier is never graded.
    if not hints:
        return None

    # Example:
    # Texas A&M Over 53.5
    #
    # If Texas A&M played exactly once
    # in the ESPN week, that identifies
    # the game safely.
    if len(hints) == 1:
        return (
            unique_event_for_team(
                hints[0],
                events,
            )
        )

    # Two-team matchup.
    matches = []

    for event in events:
        comps = competitors(
            event
        )

        if len(comps) != 2:
            continue

        both_found = all(
            any(
                comp_matches(
                    comp,
                    hint,
                )
                for comp in comps
            )
            for hint in hints[:2]
        )

        if both_found:
            matches.append(
                event
            )

    if len(matches) == 1:
        return matches[0]

    return None


def selected_comp(
    pick,
    event,
):
    team_hint = (
        pick.get("team")
        or pick.get("side")
    )

    if not team_hint:
        return None

    matches = [
        comp
        for comp in competitors(
            event
        )
        if comp_matches(
            comp,
            team_hint,
        )
    ]

    if len(matches) == 1:
        return matches[0]

    return None


def resolve_event(
    pick,
    events,
):
    bet_type = str(
        pick.get("bet_type")
        or ""
    ).upper()

    if bet_type in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
    }:

        team_hint = (
            pick.get("team")
            or pick.get("side")
        )

        return (
            unique_event_for_team(
                team_hint,
                events,
            )
        )

    if bet_type == "TOTAL":
        return (
            unique_event_for_total(
                pick,
                events,
            )
        )

    return None


def final_score_text(
    event
):
    output = []

    for comp in competitors(
        event
    ):
        team = (
            comp.get("team")
            or {}
        )

        label = (
            team.get("abbreviation")
            or team.get(
                "shortDisplayName"
            )
            or team.get(
                "displayName"
            )
            or "Team"
        )

        score = int(
            float(
                comp.get("score")
                or 0
            )
        )

        output.append(
            f"{label} {score}"
        )

    return " - ".join(
        output
    )


def total_direction(
    pick
):
    text = norm(
        pick.get("side")
        or pick.get("selection")
    )

    if "over" in text:
        return "OVER"

    if "under" in text:
        return "UNDER"

    return None


def grade_pick(
    pick,
    event,
):
    if not completed(
        event
    ):
        return False

    comps = competitors(
        event
    )

    if len(comps) != 2:
        return False

    scores = {
        comp["id"]: float(
            comp.get("score")
            or 0
        )
        for comp in comps
    }

    bet_type = str(
        pick.get("bet_type")
        or ""
    ).upper()

    line = pick.get(
        "line"
    )

    if bet_type == "TOTAL":

        if line is None:
            return False

        direction = (
            total_direction(
                pick
            )
        )

        if direction is None:
            return False

        total = sum(
            scores.values()
        )

        if direction == "OVER":

            if total > float(line):
                result = "WIN"

            elif total < float(line):
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if total < float(line):
                result = "WIN"

            elif total > float(line):
                result = "LOSS"

            else:
                result = "PUSH"

    elif bet_type == "TEAM_TOTAL":

        if line is None:
            return False

        direction = (
            total_direction(
                pick
            )
        )

        # Example:
        # "Oregon TT 37.5"
        #
        # We know the number, but not whether
        # the pick was Over or Under.
        # Do not guess.
        if direction is None:
            return False

        selected = (
            selected_comp(
                pick,
                event,
            )
        )

        if selected is None:
            return False

        team_score = scores[
            selected["id"]
        ]

        if direction == "OVER":

            if team_score > float(line):
                result = "WIN"

            elif team_score < float(line):
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if team_score < float(line):
                result = "WIN"

            elif team_score > float(line):
                result = "LOSS"

            else:
                result = "PUSH"

    elif bet_type in {
        "SPREAD",
        "MONEYLINE",
    }:

        selected = (
            selected_comp(
                pick,
                event,
            )
        )

        if selected is None:
            return False

        other = next(
            comp
            for comp in comps
            if comp["id"]
            != selected["id"]
        )

        margin = (
            scores[
                selected["id"]
            ]
            - scores[
                other["id"]
            ]
        )

        if bet_type == "MONEYLINE":

            if margin > 0:
                result = "WIN"

            elif margin < 0:
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if line is None:
                return False

            adjusted = (
                margin
                + float(line)
            )

            if adjusted > 0:
                result = "WIN"

            elif adjusted < 0:
                result = "LOSS"

            else:
                result = "PUSH"

    else:
        return False

    pick["result"] = result

    pick["status"] = (
        "FINAL"
    )

    pick["event_id"] = (
        event.get("id")
    )

    pick["graded_at"] = (
        now_iso()
    )

    pick["final_score"] = (
        final_score_text(
            event
        )
    )

    pick["profit_units"] = (
        american_profit(
            pick.get("odds"),
            float(
                pick.get("units")
                or 1
            ),
            result,
        )
    )

    return True


def clear_grade(
    pick,
    status="OPEN",
):
    pick["result"] = None
    pick["status"] = status

    pick["event_id"] = None
    pick["graded_at"] = None
    pick["final_score"] = None

    pick["profit_units"] = 0


def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    # Determine every season/week
    # currently represented in the DB.
    required_weeks = sorted({
        (
            season_year_for_pick(
                pick
            ),
            int(
                pick.get("week")
                or 1
            ),
        )
        for pick in picks
        if pick.get("sport")
        in {
            "CFB",
            "NCAAF",
        }
    })

    week_cache = {}

    failed_weeks = set()

    # Fetch first.
    #
    # We do NOT touch existing grades
    # until the ESPN slate is verified.
    for (
        season_year,
        week,
    ) in required_weeks:

        try:
            events = (
                fetch_week_events(
                    season_year,
                    week,
                )
            )

            week_cache[
                (
                    season_year,
                    week,
                )
            ] = events

            print(
                "Loaded "
                f"{len(events)} "
                "official ESPN events "
                f"for {season_year} "
                f"Week {week}"
            )

        except Exception as exc:

            failed_weeks.add(
                (
                    season_year,
                    week,
                )
            )

            print(
                "ESPN WEEK FETCH FAILED:",
                season_year,
                f"Week {week}",
                "|",
                exc,
            )

    graded = 0
    unmatched = 0
    not_final = 0
    review = 0
    preserved = 0

    for pick in picks:

        if pick.get("sport") not in {
            "CFB",
            "NCAAF",
        }:
            continue

        season_year = (
            season_year_for_pick(
                pick
            )
        )

        week = int(
            pick.get("week")
            or 1
        )

        key = (
            season_year,
            week,
        )

        # Critical safety feature:
        #
        # if ESPN fails, keep whatever
        # grade is currently stored.
        if key in failed_weeks:

            preserved += 1

            print(
                "PRESERVED - "
                "ESPN unavailable:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        bet_type = str(
            pick.get("bet_type")
            or ""
        ).upper()

        # Quarter / half bets cannot
        # use the final-game score.
        if has_partial_game_marker(
            pick
        ):

            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - "
                "partial-game bet:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if bet_type not in SUPPORTED:

            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - "
                "unsupported:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        # A team total must explicitly
        # say Over or Under.
        if (
            bet_type
            == "TEAM_TOTAL"
            and total_direction(
                pick
            )
            is None
        ):

            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - "
                "team-total direction "
                "missing:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        events = (
            week_cache[
                key
            ]
        )

        event = resolve_event(
            pick,
            events,
        )

        # ESPN week successfully loaded,
        # so old grades can now be safely
        # revalidated.
        clear_grade(
            pick
        )

        if event is None:

            unmatched += 1

            print(
                "UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if not completed(
            event
        ):

            not_final += 1

            print(
                "NOT FINAL:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if grade_pick(
            pick,
            event,
        ):

            graded += 1

            print(
                "GRADED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
                "|",
                pick.get(
                    "result"
                ),
                "|",
                pick.get(
                    "final_score"
                ),
            )

        else:

            unmatched += 1

            print(
                "GRADE FAILED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

    save_json(
        PICKS_FILE,
        picks,
    )

    print(
        f"Safely graded: "
        f"{graded}"
    )

    print(
        f"Unmatched supported picks: "
        f"{unmatched}"
    )

    print(
        f"Matched but not final: "
        f"{not_final}"
    )

    print(
        f"Review/specialty picks: "
        f"{review}"
    )

    print(
        "Preserved due to ESPN "
        f"fetch failure: {preserved}"
    )


if __name__ == "__main__":
    grade_open()
