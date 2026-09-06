from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

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

SUPPORTED = {
    "SPREAD",
    "TOTAL",
    "MONEYLINE",
}

# Exact aliases only.
# No fuzzy matching anywhere.
ALIASES = {
    "uconn": {
        "uconn",
        "connecticut",
        "connecticut huskies",
    },
    "cal": {
        "cal",
        "california",
        "california golden bears",
    },
    "notre dame": {
        "notre dame",
        "nd",
        "notre dame fighting irish",
    },
    "washington state": {
        "washington state",
        "wazzu",
        "wsu",
        "washington state cougars",
    },
    "oklahoma state": {
        "oklahoma state",
        "ok state",
        "ok st",
        "osu cowboys",
        "oklahoma state cowboys",
    },
    "boise state": {
        "boise state",
        "boise",
        "boise state broncos",
    },
    "oregon": {
        "oregon",
        "ore",
        "oregon ducks",
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
    "washington": {
        "washington",
        "wash",
        "uw",
        "washington huskies",
    },
    "wisconsin": {
        "wisconsin",
        "wis",
        "wisc",
        "wisconsin badgers",
    },
    "texas a&m": {
        "texas a&m",
        "texas am",
        "texas a m",
        "texas a and m",
        "a&m",
        "aggies",
        "texas a&m aggies",
    },
    "miami ohio": {
        "miami ohio",
        "miami oh",
        "miami (oh)",
        "miami redhawks",
    },
    "fiu": {
        "fiu",
        "florida international",
        "fiu panthers",
    },
    "usf": {
        "usf",
        "south florida",
        "south florida bulls",
    },
    "ole miss": {
        "ole miss",
        "mississippi",
        "mississippi rebels",
    },
    "lsu": {
        "lsu",
        "louisiana state",
        "lsu tigers",
    },
}

SPECIAL_MARKERS = (
    "1q",
    "1st q",
    "first quarter",
    "1h",
    "1st h",
    "first half",
    "team total",
    " tt ",
    " tt",
)


def norm(value):
    text = str(value or "").lower()

    text = (
        text.replace("&", " and ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("'", "")
    )

    text = re.sub(
        r"[^a-z0-9 ]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def alias_group(value):
    normalized = norm(value)

    if not normalized:
        return set()

    output = {
        normalized
    }

    for canonical, aliases in ALIASES.items():
        group = {
            norm(canonical)
        }

        group |= {
            norm(x)
            for x in aliases
        }

        if normalized in group:
            output |= group

    return output


def is_specialty(pick):
    bet_type = str(
        pick.get("bet_type")
        or ""
    ).upper()

    if bet_type not in SUPPORTED:
        return True

    text = (
        " "
        + norm(
            pick.get("selection")
        )
        + " "
    )

    return any(
        marker in text
        for marker in SPECIAL_MARKERS
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


def espn_team_names(comp):
    team = (
        comp.get("team")
        or {}
    )

    values = (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
    )

    names = set()

    for value in values:
        names |= alias_group(
            value
        )

    return names


def comp_matches_team(
    comp,
    team_name,
):
    return bool(
        alias_group(team_name)
        & espn_team_names(comp)
    )


def season_year_for_pick(
    pick
):
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

    if pick.get("season"):
        return int(
            pick["season"]
        )

    return datetime.now(
        timezone.utc
    ).year


def week_window(
    season_year,
    week,
):
    """
    Week 1 is the Monday-Sunday
    week containing September 1.

    2026 Week 1:
    Aug 31 through Sep 6.
    """

    sep1 = date(
        int(season_year),
        9,
        1,
    )

    week1_monday = (
        sep1
        - timedelta(
            days=sep1.weekday()
        )
    )

    start = (
        week1_monday
        + timedelta(
            days=(
                7
                * (
                    int(week)
                    - 1
                )
            )
        )
    )

    end = (
        start
        + timedelta(
            days=6
        )
    )

    return (
        start,
        end,
    )


def fetch_scoreboard_day(
    day
):
    response = requests.get(
        ESPN,
        params={
            "dates": day.strftime(
                "%Y%m%d"
            ),
            "limit": 1000,
        },
        timeout=30,
    )

    response.raise_for_status()

    return (
        response.json()
        .get(
            "events",
            [],
        )
    )


def fetch_week_events(
    season_year,
    week,
):
    start, end = week_window(
        season_year,
        week,
    )

    events_by_id = {}

    day = start

    while day <= end:

        for event in (
            fetch_scoreboard_day(
                day
            )
        ):
            event_id = event.get(
                "id"
            )

            if event_id:
                events_by_id[
                    event_id
                ] = event

        day += timedelta(
            days=1
        )

    return list(
        events_by_id.values()
    )


def split_matchup(
    value
):
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


def game_contains_team(
    event,
    team_name,
):
    comps = competitors(
        event
    )

    if len(comps) != 2:
        return False

    return any(
        comp_matches_team(
            comp,
            team_name,
        )
        for comp in comps
    )


def unique_event_for_team(
    team_name,
    events,
):
    """
    For spreads and moneylines:

    team + week = game

    Opponent text is intentionally
    ignored here.

    If team appears in zero or more
    than one game, fail closed.
    """

    if not team_name:
        return None

    matches = [
        event
        for event in events
        if game_contains_team(
            event,
            team_name,
        )
    ]

    if len(matches) == 1:
        return matches[0]

    return None


def total_team_hints(
    pick
):
    """
    Game totals require two teams.

    Prefer structured team/opponent.
    Fall back to matchup text.
    """

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

    parts = split_matchup(
        pick.get("matchup")
    )

    if len(parts) >= 2:
        return parts[:2]

    return []


def unique_event_for_total(
    pick,
    events,
):
    hints = total_team_hints(
        pick
    )

    if len(hints) != 2:
        return None

    matches = []

    for event in events:

        comps = competitors(
            event
        )

        if len(comps) != 2:
            continue

        both_teams_found = all(
            any(
                comp_matches_team(
                    comp,
                    hint,
                )
                for comp in comps
            )
            for hint in hints
        )

        if both_teams_found:
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
    team_name = (
        pick.get("team")
        or pick.get("side")
    )

    if not team_name:
        return None

    matches = [
        comp
        for comp in competitors(
            event
        )
        if comp_matches_team(
            comp,
            team_name,
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
    }:

        team_name = (
            pick.get("team")
            or pick.get("side")
        )

        return (
            unique_event_for_team(
                team_name,
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

        side = norm(
            pick.get("side")
            or pick.get(
                "selection"
            )
        )

        total = sum(
            scores.values()
        )

        if "over" in side:

            if total > float(line):
                result = "WIN"

            elif total < float(line):
                result = "LOSS"

            else:
                result = "PUSH"

        elif "under" in side:

            if total < float(line):
                result = "WIN"

            elif total > float(line):
                result = "LOSS"

            else:
                result = "PUSH"

        else:
            return False

    elif bet_type in {
        "SPREAD",
        "MONEYLINE",
    }:

        selected = selected_comp(
            pick,
            event,
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
    pick["status"] = "FINAL"

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

    # Each week gets fetched once.
    week_cache = {}

    graded = 0
    unmatched = 0
    not_final = 0
    specialty = 0

    for pick in picks:

        if pick.get("sport") not in {
            "CFB",
            "NCAAF",
        }:
            continue

        bet_type = str(
            pick.get("bet_type")
            or ""
        ).upper()

        if is_specialty(
            pick
        ):

            clear_grade(
                pick,
                "REVIEW",
            )

            specialty += 1

            print(
                "QUARANTINED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if bet_type not in SUPPORTED:
            continue

        # Recheck previous grades.
        # This repairs any bad grades
        # created by older grader versions.
        clear_grade(
            pick
        )

        week = int(
            pick.get("week")
            or 1
        )

        season_year = (
            season_year_for_pick(
                pick
            )
        )

        cache_key = (
            season_year,
            week,
        )

        if (
            cache_key
            not in week_cache
        ):

            try:
                week_cache[
                    cache_key
                ] = (
                    fetch_week_events(
                        season_year,
                        week,
                    )
                )

                print(
                    f"Loaded "
                    f"{len(week_cache[cache_key])} "
                    f"ESPN events for "
                    f"{season_year} "
                    f"Week {week}"
                )

            except Exception as exc:

                print(
                    "ESPN WEEK FETCH "
                    "FAILED:",
                    season_year,
                    f"Week {week}",
                    "|",
                    exc,
                )

                week_cache[
                    cache_key
                ] = []

        event = resolve_event(
            pick,
            week_cache[
                cache_key
            ],
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
        f"Specialty picks quarantined: "
        f"{specialty}"
    )


if __name__ == "__main__":
    grade_open()
