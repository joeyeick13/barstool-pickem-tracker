from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import requests

from common import (
    PICKS_FILE,
    american_profit,
    load_json,
    now_iso,
    save_json,
)


ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football"
)

ESPN_SCOREBOARD = f"{ESPN_BASE}/scoreboard"
ESPN_TEAMS = f"{ESPN_BASE}/teams"

REGULAR_SEASON = 2

SUPPORTED = {
    "SPREAD",
    "TOTAL",
    "MONEYLINE",
    "TEAM_TOTAL",
}

PARTIAL_GAME_MARKERS = (
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
    "byu": {
        "byu",
        "brigham young",
        "byu cougars",
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
    "eastern illinois": {
        "eiu",
        "eastern illinois",
    },
    "fiu": {
        "fiu",
        "florida international",
        "fiu panthers",
    },
    "georgia tech": {
        "gatech",
        "ga tech",
        "georgia tech",
        "gt",
        "yellow jackets",
    },
    "houston": {
        "hou",
        "houston",
        "houston cougars",
    },
    "illinois": {
        "ill",
        "illinois",
        "illinois fighting illini",
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
        "aggies",
        "texas a&m",
        "texas am",
        "texas a m",
        "texas a and m",
        "texas a&m aggies",
    },
    "texas state": {
        "texas state",
        "tex st",
        "txst",
        "texas state bobcats",
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
    "utah tech": {
        "utah tech",
        "ut tech",
        "utu",
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
}


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

    output = {normalized}

    for canonical, names in ALIASES.items():
        group = {norm(canonical)}

        group |= {
            norm(name)
            for name in names
        }

        if normalized in group:
            output |= group

    return output


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
        event.get("status", {})
        .get("type", {})
        .get("completed")
    )


def espn_team_names(comp):
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
        names |= alias_group(value)

    return names


def comp_matches(comp, hint):
    if not hint:
        return False

    return bool(
        alias_group(hint)
        & espn_team_names(comp)
    )


def season_year_for_pick(pick):
    if pick.get("season"):
        return int(pick["season"])

    posted = pick.get("posted_at")

    if posted:
        try:
            return datetime.fromisoformat(
                str(posted).replace(
                    "Z",
                    "+00:00",
                )
            ).year
        except Exception:
            pass

    return datetime.now(
        timezone.utc
    ).year


def week_window(
    season_year,
    week,
):
    if int(season_year) != 2026:
        raise RuntimeError(
            "No verified calendar configured for "
            f"{season_year}. Grades preserved."
        )

    week_1_start = date(
        2026,
        8,
        27,
    )

    week_1_end = date(
        2026,
        9,
        7,
    )

    shift = timedelta(
        days=(
            int(week) - 1
        ) * 7
    )

    return (
        week_1_start + shift,
        week_1_end + shift,
    )


def request_json(
    url,
    params=None,
):
    response = requests.get(
        url,
        params=params,
        timeout=30,
        headers={
            "Accept": (
                "application/json, "
                "text/plain, */*"
            ),
            "Origin": "https://www.espn.com",
            "Referer": "https://www.espn.com/",
        },
    )

    response.raise_for_status()

    return response.json()


def fetch_scoreboard_events(
    season_year,
    week,
):
    start_date, end_date = week_window(
        season_year,
        week,
    )

    events_by_id = {}

    current = start_date

    while current <= end_date:
        date_string = current.strftime(
            "%Y%m%d"
        )

        payload = request_json(
            ESPN_SCOREBOARD,
            params={
                "dates": date_string,
                "limit": 1000,
            },
        )

        daily_events = (
            payload.get("events")
            or []
        )

        print(
            f"ESPN scoreboard "
            f"{date_string}: "
            f"{len(daily_events)} events"
        )

        for event in daily_events:
            event_id = str(
                event.get("id")
                or ""
            )

            if not event_id:
                continue

            season = (
                event.get("season")
                or {}
            )

            event_year = season.get(
                "year"
            )

            season_type = season.get(
                "type"
            )

            if (
                event_year is not None
                and int(event_year)
                != int(season_year)
            ):
                continue

            if (
                season_type is not None
                and int(season_type)
                != REGULAR_SEASON
            ):
                continue

            events_by_id[
                event_id
            ] = event

        current += timedelta(
            days=1
        )

    events = list(
        events_by_id.values()
    )

    print(
        f"Scoreboard produced "
        f"{len(events)} unique events"
    )

    return events


def fetch_all_teams():
    payload = request_json(
        ESPN_TEAMS,
        params={
            "limit": 1000,
        },
    )

    sports = (
        payload.get("sports")
        or []
    )

    output = []

    for sport in sports:
        leagues = (
            sport.get("leagues")
            or []
        )

        for league in leagues:
            teams = (
                league.get("teams")
                or []
            )

            for wrapper in teams:
                team = (
                    wrapper.get("team")
                    or wrapper
                )

                if team:
                    output.append(team)

    return output


def team_aliases_from_record(team):
    names = set()

    for value in (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
        team.get("slug"),
    ):
        names |= alias_group(value)

    return names


def resolve_espn_team_id(
    hint,
    teams,
):
    target = alias_group(hint)

    if not target:
        return None

    matches = []

    for team in teams:
        if (
            target
            & team_aliases_from_record(
                team
            )
        ):
            team_id = team.get("id")

            if team_id:
                matches.append(
                    str(team_id)
                )

    matches = list(
        dict.fromkeys(matches)
    )

    if len(matches) == 1:
        return matches[0]

    return None


def fetch_team_schedule(
    team_id,
    season_year,
):
    url = (
        f"{ESPN_TEAMS}/"
        f"{team_id}/schedule"
    )

    payload = request_json(
        url,
        params={
            "season": int(
                season_year
            ),
            "seasontype": (
                REGULAR_SEASON
            ),
        },
    )

    return (
        payload.get("events")
        or []
    )


def event_date(event):
    raw = event.get("date")

    if not raw:
        return None

    try:
        return datetime.fromisoformat(
            str(raw).replace(
                "Z",
                "+00:00",
            )
        ).date()
    except Exception:
        return None


def event_in_week_window(
    event,
    season_year,
    week,
):
    start_date, end_date = week_window(
        season_year,
        week,
    )

    value = event_date(event)

    if value is None:
        return False

    return (
        start_date
        <= value
        <= end_date
    )


def merge_event(
    events_by_id,
    event,
):
    event_id = str(
        event.get("id")
        or ""
    )

    if not event_id:
        return

    events_by_id[
        event_id
    ] = event


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


def has_partial_game_marker(pick):
    text = (
        " "
        + norm(
            pick.get(
                "selection"
            )
        )
        + " "
    )

    return any(
        marker in text
        for marker
        in PARTIAL_GAME_MARKERS
    )


def parse_side_team(selection):
    text = str(
        selection
        or ""
    ).strip()

    if not text:
        return None

    text = re.sub(
        r"\s+(?:moneyline|ml)\s*$",
        "",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\s+[+-]\d+(?:\.\d+)?\s*$",
        "",
        text,
    )

    text = text.strip()

    return text or None


def parse_total_identity(selection):
    text = str(
        selection
        or ""
    ).strip()

    match = re.match(
        r"^(.*?)\s+"
        r"(over|under)\s+"
        r"\d+(?:\.\d+)?\s*$",
        text,
        flags=re.I,
    )

    if not match:
        return []

    prefix = (
        match.group(1)
        .strip()
    )

    if not prefix:
        return []

    parts = [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            prefix,
            flags=re.I,
        )
        if part.strip()
    ]

    return parts[:2]


def side_identity(pick):
    parsed = parse_side_team(
        pick.get(
            "selection"
        )
    )

    if parsed:
        return parsed

    return (
        pick.get("team")
        or pick.get("side")
    )


def total_identity(pick):
    parsed = parse_total_identity(
        pick.get(
            "selection"
        )
    )

    if parsed:
        return parsed

    matchup_parts = split_matchup(
        pick.get(
            "matchup"
        )
    )

    if len(matchup_parts) >= 2:
        return matchup_parts[:2]

    team = pick.get("team")
    opponent = pick.get(
        "opponent"
    )

    if team and opponent:
        return [
            str(team),
            str(opponent),
        ]

    if team:
        return [
            str(team)
        ]

    return []


def identities_needed_for_pick(
    pick,
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
        identity = side_identity(
            pick
        )

        return (
            [identity]
            if identity
            else []
        )

    if bet_type == "TOTAL":
        return total_identity(
            pick
        )

    return []


def collect_required_team_hints(
    picks,
    season_year,
    week,
):
    hints = []

    for pick in picks:
        if pick.get("sport") not in {
            "CFB",
            "NCAAF",
        }:
            continue

        if (
            season_year_for_pick(
                pick
            )
            != int(season_year)
        ):
            continue

        if (
            int(
                pick.get("week")
                or 1
            )
            != int(week)
        ):
            continue

        for hint in identities_needed_for_pick(
            pick
        ):
            if hint:
                hints.append(
                    hint
                )

    output = []

    seen = set()

    for hint in hints:
        key = norm(hint)

        if key in seen:
            continue

        seen.add(key)
        output.append(hint)

    return output


def team_present_in_events(
    hint,
    events,
):
    return any(
        any(
            comp_matches(
                comp,
                hint,
            )
            for comp
            in competitors(
                event
            )
        )
        for event
        in events
    )


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    scoreboard_events = (
        fetch_scoreboard_events(
            season_year,
            week,
        )
    )

    events_by_id = {
        str(event.get("id")): event
        for event
        in scoreboard_events
        if event.get("id")
    }

    required_hints = (
        collect_required_team_hints(
            picks,
            season_year,
            week,
        )
    )

    missing_hints = [
        hint
        for hint
        in required_hints
        if not team_present_in_events(
            hint,
            list(
                events_by_id.values()
            ),
        )
    ]

    print(
        f"Missing from scoreboard: "
        f"{len(missing_hints)} team identities"
    )

    if missing_hints:
        all_teams = (
            fetch_all_teams()
        )

        print(
            f"Loaded "
            f"{len(all_teams)} ESPN teams "
            "for fallback resolution"
        )

        unresolved_team_ids = []

        for hint in missing_hints:
            team_id = (
                resolve_espn_team_id(
                    hint,
                    all_teams,
                )
            )

            if not team_id:
                unresolved_team_ids.append(
                    hint
                )

                print(
                    "TEAM ID UNRESOLVED:",
                    hint,
                )

                continue

            try:
                schedule_events = (
                    fetch_team_schedule(
                        team_id,
                        season_year,
                    )
                )
            except Exception as exc:
                print(
                    "TEAM SCHEDULE FAILED:",
                    hint,
                    "|",
                    exc,
                )

                continue

            added = 0

            for event in schedule_events:
                if not event_in_week_window(
                    event,
                    season_year,
                    week,
                ):
                    continue

                before = len(
                    events_by_id
                )

                merge_event(
                    events_by_id,
                    event,
                )

                after = len(
                    events_by_id
                )

                if after > before:
                    added += 1

            print(
                "TEAM FALLBACK:",
                hint,
                "| team_id:",
                team_id,
                "| added:",
                added,
            )

    events = list(
        events_by_id.values()
    )

    print(
        f"Combined complete slate: "
        f"{len(events)} unique events"
    )

    unresolved_after_merge = [
        hint
        for hint
        in required_hints
        if not team_present_in_events(
            hint,
            events,
        )
    ]

    if unresolved_after_merge:
        print(
            "WARNING - unresolved team identities:",
            ", ".join(
                unresolved_after_merge
            ),
        )

    return events


def unique_event_for_team(
    team_hint,
    events,
):
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
            for comp
            in comps
        ):
            matches.append(
                event
            )

    if len(matches) == 1:
        return matches[0]

    return None


def unique_event_for_pair(
    team_a,
    team_b,
    events,
):
    matches = []

    for event in events:
        comps = competitors(
            event
        )

        if len(comps) != 2:
            continue

        found_a = any(
            comp_matches(
                comp,
                team_a,
            )
            for comp in comps
        )

        found_b = any(
            comp_matches(
                comp,
                team_b,
            )
            for comp in comps
        )

        if found_a and found_b:
            matches.append(
                event
            )

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
        return unique_event_for_team(
            side_identity(
                pick
            ),
            events,
        )

    if bet_type == "TOTAL":
        identity = total_identity(
            pick
        )

        if len(identity) == 2:
            return unique_event_for_pair(
                identity[0],
                identity[1],
                events,
            )

        if len(identity) == 1:
            return unique_event_for_team(
                identity[0],
                events,
            )

        return None

    return None


def selected_comp(
    pick,
    event,
):
    hint = side_identity(
        pick
    )

    if not hint:
        return None

    matches = [
        comp
        for comp
        in competitors(event)
        if comp_matches(
            comp,
            hint,
        )
    ]

    if len(matches) == 1:
        return matches[0]

    return None


def total_direction(pick):
    text = norm(
        pick.get("side")
        or pick.get(
            "selection"
        )
    )

    if "over" in text:
        return "OVER"

    if "under" in text:
        return "UNDER"

    return None


def final_score_text(event):
    output = []

    for comp in competitors(
        event
    ):
        team = (
            comp.get("team")
            or {}
        )

        label = (
            team.get(
                "abbreviation"
            )
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

    line = pick.get("line")

    if bet_type == "TOTAL":
        if line is None:
            return False

        direction = total_direction(
            pick
        )

        if direction is None:
            return False

        total = sum(
            scores.values()
        )

        if direction == "OVER":
            result = (
                "WIN"
                if total > float(line)
                else "LOSS"
                if total < float(line)
                else "PUSH"
            )
        else:
            result = (
                "WIN"
                if total < float(line)
                else "LOSS"
                if total > float(line)
                else "PUSH"
            )

    elif bet_type == "TEAM_TOTAL":
        if line is None:
            return False

        direction = total_direction(
            pick
        )

        if direction is None:
            return False

        selected = selected_comp(
            pick,
            event,
        )

        if selected is None:
            return False

        team_score = scores[
            selected["id"]
        ]

        if direction == "OVER":
            result = (
                "WIN"
                if team_score > float(line)
                else "LOSS"
                if team_score < float(line)
                else "PUSH"
            )
        else:
            result = (
                "WIN"
                if team_score < float(line)
                else "LOSS"
                if team_score > float(line)
                else "PUSH"
            )

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
            scores[selected["id"]]
            - scores[other["id"]]
        )

        if bet_type == "MONEYLINE":
            result = (
                "WIN"
                if margin > 0
                else "LOSS"
                if margin < 0
                else "PUSH"
            )
        else:
            if line is None:
                return False

            adjusted = (
                margin
                + float(line)
            )

            result = (
                "WIN"
                if adjusted > 0
                else "LOSS"
                if adjusted < 0
                else "PUSH"
            )

    else:
        return False

    pick["result"] = result
    pick["status"] = "FINAL"
    pick["event_id"] = event.get(
        "id"
    )
    pick["graded_at"] = now_iso()
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

    for (
        season_year,
        week,
    ) in required_weeks:
        try:
            events = (
                build_complete_week_slate(
                    picks,
                    season_year,
                    week,
                )
            )

            if not events:
                raise RuntimeError(
                    "No ESPN events retrieved."
                )

            week_cache[
                (
                    season_year,
                    week,
                )
            ] = events

            print(
                f"Loaded "
                f"{len(events)} "
                "merged ESPN events "
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

        if key in failed_weeks:
            preserved += 1

            print(
                "PRESERVED:",
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

        if has_partial_game_marker(
            pick
        ):
            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - partial game:",
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
                "REVIEW - unsupported:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if (
            bet_type == "TEAM_TOTAL"
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
                "REVIEW - team total "
                "direction missing:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        events = week_cache[key]

        event = resolve_event(
            pick,
            events,
        )

        clear_grade(
            pick
        )

        if event is None:
            unmatched += 1

            identity = (
                total_identity(
                    pick
                )
                if bet_type == "TOTAL"
                else side_identity(
                    pick
                )
            )

            print(
                "UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
                "| identity:",
                identity,
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
                "| event:",
                event.get(
                    "id"
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
                "| event:",
                pick.get(
                    "event_id"
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

    print()
    print(
        "========== GRADING SUMMARY =========="
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
        f"Preserved due to ESPN failure: "
        f"{preserved}"
    )
    print(
        "====================================="
    )


if __name__ == "__main__":
    grade_open()
