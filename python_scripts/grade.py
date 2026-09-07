from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import unescape

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
ESPN_SUMMARY = f"{ESPN_BASE}/summary"

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

AMBIGUOUS_ALIASES = {
    "osu",
}


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

    "ball state": {
        "ball state",
        "ball st",
        "ball",
    },

    "baylor": {
        "baylor",
        "baylor bears",
    },

    "boise state": {
        "boise",
        "boise state",
        "boise st",
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
        "conn",
        "connecticut huskies",
    },

    "duke": {
        "duke",
        "duke blue devils",
    },

    "eastern illinois": {
        "eiu",
        "eastern illinois",
    },

    "east carolina": {
        "ecu",
        "east carolina",
        "east carolina pirates",
    },

    "fiu": {
        "fiu",
        "florida international",
        "florida intl",
        "fiu panthers",
    },

    "georgia tech": {
        "gatech",
        "ga tech",
        "georgia tech",
        "gt",
    },

    "hawaii": {
        "hawaii",
        "hawai'i",
        "haw",
        "hawaii rainbow warriors",
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

    "indiana": {
        "indiana",
        "ind",
        "indiana hoosiers",
    },

    "kansas": {
        "kansas",
        "ku",
        "kansas jayhawks",
    },

    "kent state": {
        "kent",
        "kent state",
        "kent st",
    },

    "lsu": {
        "lsu",
        "louisiana state",
        "louisiana st",
    },

    "memphis": {
        "mem",
        "memphis",
        "memphis tigers",
    },

    "miami": {
        "miami",
        "miami fl",
        "miami florida",
        "miami hurricanes",
    },

    "miami ohio": {
        "miami oh",
        "miami ohio",
        "miami (oh)",
        "m-oh",
        "miami redhawks",
    },

    "michigan": {
        "mich",
        "michigan",
        "michigan wolverines",
    },

    "michigan state": {
        "michigan state",
        "michigan st",
        "mich state",
        "msu",
    },

    "minnesota": {
        "minn",
        "minnesota",
        "minnesota golden gophers",
    },

    "mississippi state": {
        "miss st",
        "miss state",
        "mississippi state",
        "mississippi st",
        "msst",
    },

    "missouri state": {
        "missouri state",
        "missouri st",
        "mo state",
        "most",
    },

    "notre dame": {
        "nd",
        "notre dame",
        "notre dame fighting irish",
    },

    "ohio state": {
        "ohio state",
        "ohio st",
        "ohio state buckeyes",
    },

    "oklahoma": {
        "oklahoma",
        "ou",
        "oklahoma sooners",
    },

    "oklahoma state": {
        "ok state",
        "ok st",
        "oklahoma state",
        "oklahoma st",
        "oklahoma state cowboys",
    },

    "oregon": {
        "ore",
        "oregon",
        "oregon ducks",
    },

    "oregon state": {
        "oregon state",
        "oregon st",
        "ore st",
        "oregon state beavers",
    },

    "pittsburgh": {
        "pitt",
        "pittsburgh",
        "pittsburgh panthers",
    },

    "rutgers": {
        "rutgers",
        "rutg",
        "ru",
    },

    "south carolina": {
        "south carolina",
        "scar",
        "south carolina gamecocks",
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
        "a and m",
        "tamu",
        "ta&m",
        "ta and m",
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

    "tulane": {
        "tulane",
        "tulane green wave",
    },

    "tulsa": {
        "tulsa",
        "tulsa golden hurricane",
    },

    "ucla": {
        "ucla",
        "ucla bruins",
    },

    "umass": {
        "umass",
        "massachusetts",
        "massachusetts minutemen",
    },

    "unlv": {
        "unlv",
        "unlv rebels",
    },

    "usc": {
        "usc",
        "southern california",
        "southern cal",
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
        "washington st",
        "washington state cougars",
    },

    "west virginia": {
        "wvu",
        "west virginia",
        "west virginia mountaineers",
    },

    "western michigan": {
        "wmu",
        "western michigan",
    },

    "wisconsin": {
        "wis",
        "wisc",
        "wisconsin",
        "wisconsin badgers",
    },
}


def clean_text(value):
    return unescape(
        str(value or "")
    ).strip()


def norm(value):
    text = unescape(
        str(value or "")
    ).lower()

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
    normalized = norm(
        value
    )

    if not normalized:
        return set()

    if normalized in AMBIGUOUS_ALIASES:
        return {
            normalized
        }

    output = {
        normalized
    }

    for canonical, names in ALIASES.items():

        group = {
            norm(canonical)
        }

        group |= {
            norm(name)
            for name in names
        }

        if normalized in group:
            output |= group

    return output


def is_ambiguous_hint(value):
    return (
        norm(value)
        in AMBIGUOUS_ALIASES
    )


def parse_datetime(value):
    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def pick_posted_datetime(
    pick,
):
    return parse_datetime(
        pick.get("posted_at")
    )


def event_datetime(
    event,
):
    return parse_datetime(
        event.get("date")
    )


def competitions(event):
    return (
        event.get("competitions")
        or []
    )


def competitors(event):
    comps = competitions(
        event
    )

    if not comps:
        return []

    return (
        comps[0].get(
            "competitors"
        )
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
        .get("completed")
    )


def event_status_name(event):
    return str(
        event.get(
            "status",
            {},
        )
        .get(
            "type",
            {},
        )
        .get("name")
        or ""
    )


def event_status_state(event):
    return str(
        event.get(
            "status",
            {},
        )
        .get(
            "type",
            {},
        )
        .get("state")
        or ""
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
        team.get("abbreviation"),
        team.get("location"),
        team.get("slug"),
    ):

        names |= alias_group(
            value
        )

    return names


def comp_matches(
    comp,
    hint,
):
    if not hint:
        return False

    if is_ambiguous_hint(
        hint
    ):
        return False

    return bool(
        alias_group(hint)
        & espn_team_names(comp)
    )


def season_year_for_pick(
    pick,
):
    if pick.get("season"):

        return int(
            pick["season"]
        )

    posted = pick_posted_datetime(
        pick
    )

    if posted:
        return posted.year

    return datetime.now(
        timezone.utc
    ).year


def week_window(
    season_year,
    week,
):
    if int(season_year) != 2026:

        raise RuntimeError(
            "No verified calendar configured "
            f"for {season_year}. "
            "Grades preserved."
        )

    week = int(
        week
    )

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

    week_3_start = date(
        2026,
        9,
        14,
    )

    start = (
        week_3_start
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
            "Origin": (
                "https://www.espn.com"
            ),
            "Referer": (
                "https://www.espn.com/"
            ),
        },
    )

    response.raise_for_status()

    return response.json()


def score_value(comp):
    raw = comp.get(
        "score"
    )

    if raw is None:
        return None

    if isinstance(
        raw,
        (
            str,
            int,
            float,
        ),
    ):

        try:
            return float(
                raw
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

    if isinstance(
        raw,
        dict,
    ):

        for key in (
            "value",
            "displayValue",
            "score",
        ):

            value = raw.get(
                key
            )

            if isinstance(
                value,
                dict,
            ):

                value = (
                    value.get("value")
                    or value.get(
                        "displayValue"
                    )
                )

            if value is None:
                continue

            try:
                return float(
                    value
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

    return None


def event_has_scores(event):
    comps = competitors(
        event
    )

    if len(comps) != 2:
        return False

    return all(
        score_value(comp)
        is not None
        for comp in comps
    )


def final_score_text(
    event,
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

        value = score_value(
            comp
        )

        if value is None:

            score_text = "?"

        elif float(
            value
        ).is_integer():

            score_text = str(
                int(value)
            )

        else:

            score_text = str(
                value
            )

        output.append(
            f"{label} {score_text}"
        )

    return " - ".join(
        output
    )


def event_quality(event):
    score = 0

    if completed(
        event
    ):
        score += 1000

    if (
        event_status_state(
            event
        ).lower()
        == "post"
    ):
        score += 500

    if event_has_scores(
        event
    ):
        score += 300

    status_name = (
        event_status_name(
            event
        ).upper()
    )

    if "FINAL" in status_name:
        score += 200

    if event.get("date"):
        score += 20

    comps = competitors(
        event
    )

    score += (
        len(comps)
        * 10
    )

    for comp in comps:

        team = (
            comp.get("team")
            or {}
        )

        if team.get(
            "displayName"
        ):
            score += 5

        if team.get(
            "abbreviation"
        ):
            score += 3

        if (
            score_value(
                comp
            )
            is not None
        ):
            score += 10

    return score


def merge_event(
    events_by_id,
    event,
    source="unknown",
):
    event_id = str(
        event.get("id")
        or ""
    )

    if not event_id:
        return False

    existing = (
        events_by_id.get(
            event_id
        )
    )

    if existing is None:

        events_by_id[
            event_id
        ] = event

        return True

    existing_quality = (
        event_quality(
            existing
        )
    )

    incoming_quality = (
        event_quality(
            event
        )
    )

    if (
        incoming_quality
        > existing_quality
    ):

        events_by_id[
            event_id
        ] = event

        print(
            "EVENT UPGRADED:",
            event_id,
            "| source:",
            source,
            "|",
            existing_quality,
            "->",
            incoming_quality,
        )

        return True

    return False


def fetch_event_summary(
    event_id,
):
    return request_json(
        ESPN_SUMMARY,
        params={
            "event": event_id,
        },
    )


def event_from_summary(
    payload,
    fallback_event,
):
    header = (
        payload.get("header")
        or {}
    )

    summary_competitions = (
        header.get(
            "competitions"
        )
        or []
    )

    if not summary_competitions:
        return fallback_event

    competition = (
        summary_competitions[0]
    )

    event = dict(
        fallback_event
    )

    event_id = (
        competition.get("id")
        or header.get("id")
        or fallback_event.get("id")
    )

    if event_id:

        event["id"] = str(
            event_id
        )

    if competition.get(
        "date"
    ):

        event["date"] = (
            competition["date"]
        )

    if competition.get(
        "status"
    ):

        event["status"] = (
            competition["status"]
        )

    event["competitions"] = [
        competition
    ]

    if header.get(
        "season"
    ):

        event["season"] = (
            header["season"]
        )

    return event


def hydrate_event(
    event,
    summary_cache,
):
    event_id = str(
        event.get("id")
        or ""
    )

    if not event_id:
        return event

    if completed(
        event
    ):
        return event

    state = (
        event_status_state(
            event
        ).lower()
    )

    name = (
        event_status_name(
            event
        ).upper()
    )

    status_missing = (
        not state
        and not name
    )

    suspicious_scores = (
        event_has_scores(
            event
        )
        and state not in {
            "in",
            "pre",
        }
    )

    if (
        not status_missing
        and not suspicious_scores
    ):
        return event

    if event_id in summary_cache:

        return summary_cache[
            event_id
        ]

    try:

        payload = (
            fetch_event_summary(
                event_id
            )
        )

        hydrated = (
            event_from_summary(
                payload,
                event,
            )
        )

        summary_cache[
            event_id
        ] = hydrated

        print(
            "EVENT HYDRATED:",
            event_id,
            "| status:",
            event_status_name(
                hydrated
            ),
            "| completed:",
            completed(
                hydrated
            ),
            "|",
            final_score_text(
                hydrated
            ),
        )

        return hydrated

    except Exception as exc:

        print(
            "EVENT HYDRATION FAILED:",
            event_id,
            "|",
            exc,
        )

        summary_cache[
            event_id
        ] = event

        return event


def fetch_scoreboard_events(
    season_year,
    week,
):
    (
        start_date,
        end_date,
    ) = week_window(
        season_year,
        week,
    )

    events_by_id = {}

    current = start_date

    while current <= end_date:

        date_string = (
            current.strftime(
                "%Y%m%d"
            )
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
            "ESPN scoreboard "
            f"{date_string}: "
            f"{len(daily_events)} events"
        )

        for event in daily_events:

            season = (
                event.get("season")
                or {}
            )

            event_year = (
                season.get("year")
            )

            season_type = (
                season.get("type")
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

            merge_event(
                events_by_id,
                event,
                source="scoreboard",
            )

        current += timedelta(
            days=1
        )

    events = list(
        events_by_id.values()
    )

    print(
        "Scoreboard produced "
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

    output = []

    for sport in (
        payload.get("sports")
        or []
    ):

        for league in (
            sport.get("leagues")
            or []
        ):

            for wrapper in (
                league.get("teams")
                or []
            ):

                team = (
                    wrapper.get("team")
                    or wrapper
                )

                if team:
                    output.append(
                        team
                    )

    return output


def team_aliases_from_record(
    team,
):
    names = set()

    for value in (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("abbreviation"),
        team.get("location"),
        team.get("slug"),
    ):

        names |= alias_group(
            value
        )

    return names


def resolve_espn_team_id(
    hint,
    teams,
):
    if not hint:
        return None

    if is_ambiguous_hint(
        hint
    ):
        return None

    target = alias_group(
        hint
    )

    matches = []

    for team in teams:

        if (
            target
            & team_aliases_from_record(
                team
            )
        ):

            team_id = (
                team.get("id")
            )

            if team_id:

                matches.append(
                    str(team_id)
                )

    matches = list(
        dict.fromkeys(
            matches
        )
    )

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:

        print(
            "AMBIGUOUS TEAM ID:",
            hint,
            "|",
            matches,
        )

    return None


def fetch_team_schedule(
    team_id,
    season_year,
):
    payload = request_json(
        (
            f"{ESPN_TEAMS}/"
            f"{team_id}/schedule"
        ),
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


def event_in_week_window(
    event,
    season_year,
    week,
):
    dt = event_datetime(
        event
    )

    if dt is None:
        return False

    (
        start_date,
        end_date,
    ) = week_window(
        season_year,
        week,
    )

    return (
        start_date
        <= dt.date()
        <= end_date
    )


def split_matchup(value):
    if not value:
        return []

    text = clean_text(
        value
    )

    return [
        part.strip()

        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            text,
            flags=re.I,
        )

        if part.strip()
    ]


def has_partial_game_marker(
    pick,
):
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
        for marker in PARTIAL_GAME_MARKERS
    )


def parse_side_team(
    selection,
):
    text = clean_text(
        selection
    )

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

    return (
        text.strip()
        or None
    )


def parse_total_identity(
    selection,
):
    text = clean_text(
        selection
    )

    if not text:
        return []

    match = re.match(
        (
            r"^(.*?)\s+"
            r"(over|under)\s+"
            r"\d+(?:\.\d+)?\s*$"
        ),
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

    return [
        part.strip()

        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            prefix,
            flags=re.I,
        )

        if part.strip()
    ][:2]


def matchup_identity(
    pick,
):
    parts = split_matchup(
        pick.get("matchup")
    )

    if len(parts) >= 2:
        return parts[:2]

    return []


def structured_pair(
    pick,
):
    team = clean_text(
        pick.get("team")
    )

    opponent = clean_text(
        pick.get("opponent")
    )

    if team and opponent:

        return [
            team,
            opponent,
        ]

    return []


def side_identity(
    pick,
):
    parsed = parse_side_team(
        pick.get("selection")
    )

    if parsed:
        return parsed

    structured = clean_text(
        pick.get("team")
        or pick.get("side")
    )

    return (
        structured
        or None
    )


def total_identity(
    pick,
):
    parsed = parse_total_identity(
        pick.get("selection")
    )

    if len(parsed) == 2:
        return parsed

    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:
        return matchup

    if len(parsed) == 1:
        return parsed

    pair = structured_pair(
        pick
    )

    if pair:
        return pair

    team = clean_text(
        pick.get("team")
    )

    if team:
        return [
            team
        ]

    return []


def total_direction(
    pick,
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


def matching_events_for_team(
    team_hint,
    events,
):
    if not team_hint:
        return []

    if is_ambiguous_hint(
        team_hint
    ):
        return []

    return [
        event

        for event in events

        if (
            len(
                competitors(
                    event
                )
            ) == 2
            and any(
                comp_matches(
                    comp,
                    team_hint,
                )
                for comp in competitors(
                    event
                )
            )
        )
    ]


def unique_event_for_team(
    team_hint,
    events,
):
    matches = matching_events_for_team(
        team_hint,
        events,
    )

    if len(matches) == 1:
        return matches[0]

    return None


def event_for_team_by_post_time(
    team_hint,
    pick,
    events,
):
    matches = matching_events_for_team(
        team_hint,
        events,
    )

    if len(matches) == 1:
        return matches[0]

    if not matches:
        return None

    posted = pick_posted_datetime(
        pick
    )

    if posted is None:

        print(
            "MULTI-GAME TEAM WITHOUT POST TIME:",
            team_hint,
            "| matches:",
            len(matches),
        )

        return None

    candidates = []

    for event in matches:

        kickoff = event_datetime(
            event
        )

        if kickoff is None:
            continue

        if (
            kickoff
            >= posted
            - timedelta(
                hours=3
            )
        ):

            candidates.append(
                (
                    abs(
                        (
                            kickoff
                            - posted
                        ).total_seconds()
                    ),
                    kickoff,
                    event,
                )
            )

    if not candidates:

        print(
            "NO POST-TIME GAME:",
            team_hint,
            "| posted:",
            posted.isoformat(),
        )

        return None

    candidates.sort(
        key=lambda x: (
            x[0],
            x[1],
        )
    )

    best = candidates[0]

    if len(candidates) > 1:

        difference = abs(
            candidates[1][0]
            - best[0]
        )

        if difference < 3600:

            print(
                "POST-TIME MATCH AMBIGUOUS:",
                team_hint,
            )

            return None

    event = best[2]

    print(
        "POST-TIME RESOLVED:",
        team_hint,
        "| posted:",
        posted.isoformat(),
        "| kickoff:",
        event_datetime(
            event
        ).isoformat(),
        "| event:",
        event.get("id"),
    )

    return event


def unique_event_for_pair(
    team_a,
    team_b,
    events,
):
    if (
        not team_a
        or not team_b
    ):
        return None

    if (
        is_ambiguous_hint(
            team_a
        )
        or is_ambiguous_hint(
            team_b
        )
    ):
        return None

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


def resolve_ambiguous_pair(
    team_a,
    team_b,
    events,
):
    a_ambiguous = is_ambiguous_hint(
        team_a
    )

    b_ambiguous = is_ambiguous_hint(
        team_b
    )

    if (
        a_ambiguous
        == b_ambiguous
    ):
        return None

    anchor = (
        team_b
        if a_ambiguous
        else team_a
    )

    event = unique_event_for_team(
        anchor,
        events,
    )

    if event is None:
        return None

    return event


def resolve_pair(
    pair,
    events,
):
    if len(pair) != 2:
        return None

    team_a = pair[0]
    team_b = pair[1]

    if (
        is_ambiguous_hint(
            team_a
        )
        or is_ambiguous_hint(
            team_b
        )
    ):

        return resolve_ambiguous_pair(
            team_a,
            team_b,
            events,
        )

    return unique_event_for_pair(
        team_a,
        team_b,
        events,
    )


def schedule_events_for_hint(
    hint,
    all_teams,
    season_year,
    week,
    schedule_cache,
):
    if (
        not hint
        or is_ambiguous_hint(
            hint
        )
    ):
        return []

    key = (
        norm(hint),
        int(season_year),
        int(week),
    )

    if key in schedule_cache:

        return schedule_cache[
            key
        ]

    team_id = resolve_espn_team_id(
        hint,
        all_teams,
    )

    if not team_id:

        print(
            "TEAM ID UNRESOLVED:",
            hint,
        )

        schedule_cache[
            key
        ] = []

        return []

    try:

        schedule = fetch_team_schedule(
            team_id,
            season_year,
        )

    except Exception as exc:

        print(
            "TEAM SCHEDULE FAILED:",
            hint,
            "|",
            exc,
        )

        schedule_cache[
            key
        ] = []

        return []

    filtered = [
        event
        for event in schedule
        if event_in_week_window(
            event,
            season_year,
            week,
        )
    ]

    schedule_cache[
        key
    ] = filtered

    print(
        "TEAM SCHEDULE:",
        hint,
        "| team_id:",
        team_id,
        "| week events:",
        len(filtered),
    )

    return filtered


def recover_pair_from_schedules(
    pair,
    events_by_id,
    all_teams,
    season_year,
    week,
    schedule_cache,
):
    if len(pair) != 2:
        return None

    existing = resolve_pair(
        pair,
        list(
            events_by_id.values()
        ),
    )

    if existing is not None:
        return existing

    safe_hints = [
        hint
        for hint in pair
        if not is_ambiguous_hint(
            hint
        )
    ]

    if not safe_hints:
        return None

    print(
        "MATCHUP RECOVERY:",
        pair,
    )

    for hint in safe_hints:

        schedule_events = schedule_events_for_hint(
            hint,
            all_teams,
            season_year,
            week,
            schedule_cache,
        )

        for event in schedule_events:

            merge_event(
                events_by_id,
                event,
                source=(
                    "matchup-recovery:"
                    + hint
                ),
            )

    recovered = resolve_pair(
        pair,
        list(
            events_by_id.values()
        ),
    )

    if recovered is not None:

        print(
            "MATCHUP RECOVERED:",
            pair,
            "->",
            final_score_text(
                recovered
            ),
            "| event:",
            recovered.get("id"),
        )

    return recovered


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
    }:

        identity = side_identity(
            pick
        )

        if (
            identity
            and not is_ambiguous_hint(
                identity
            )
        ):
            return [
                identity
            ]

        return []

    if bet_type == "TEAM_TOTAL":

        if total_direction(
            pick
        ) is None:
            return []

        identity = side_identity(
            pick
        )

        if (
            identity
            and not is_ambiguous_hint(
                identity
            )
        ):
            return [
                identity
            ]

        return []

    if bet_type == "TOTAL":

        return [
            value
            for value in total_identity(
                pick
            )
            if not is_ambiguous_hint(
                value
            )
        ]

    return []


def collect_required_team_hints(
    picks,
    season_year,
    week,
):
    output = []
    seen = set()

    for pick in picks:

        if (
            pick.get("sport")
            not in {
                "CFB",
                "NCAAF",
            }
        ):
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

        if has_partial_game_marker(
            pick
        ):
            continue

        for hint in identities_needed_for_pick(
            pick
        ):

            key = norm(
                hint
            )

            if (
                not key
                or key in seen
            ):
                continue

            seen.add(
                key
            )

            output.append(
                hint
            )

    return output


def team_present_in_events(
    hint,
    events,
):
    return bool(
        matching_events_for_team(
            hint,
            events,
        )
    )


def pair_from_pick(
    pick,
):
    bet_type = str(
        pick.get("bet_type")
        or ""
    ).upper()

    if bet_type == "TOTAL":

        identity = total_identity(
            pick
        )

        if len(identity) == 2:
            return identity

    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:
        return matchup

    pair = structured_pair(
        pick
    )

    if len(pair) == 2:
        return pair

    return []


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    scoreboard_events = fetch_scoreboard_events(
        season_year,
        week,
    )

    events_by_id = {}

    for event in scoreboard_events:

        merge_event(
            events_by_id,
            event,
            source="scoreboard-base",
        )

    required_hints = collect_required_team_hints(
        picks,
        season_year,
        week,
    )

    missing_hints = [
        hint
        for hint in required_hints
        if not team_present_in_events(
            hint,
            list(
                events_by_id.values()
            ),
        )
    ]

    print(
        "Missing from scoreboard: "
        f"{len(missing_hints)} "
        "team identities"
    )

    all_teams = None
    schedule_cache = {}

    if missing_hints:

        all_teams = fetch_all_teams()

        print(
            "Loaded "
            f"{len(all_teams)} "
            "ESPN teams"
        )

        for hint in missing_hints:

            schedule_events = schedule_events_for_hint(
                hint,
                all_teams,
                season_year,
                week,
                schedule_cache,
            )

            merged = 0

            for event in schedule_events:

                if merge_event(
                    events_by_id,
                    event,
                    source=(
                        "team-fallback:"
                        + hint
                    ),
                ):

                    merged += 1

            print(
                "TEAM FALLBACK:",
                hint,
                "| merged/upgraded:",
                merged,
            )

    for pick in picks:

        if (
            pick.get("sport")
            not in {
                "CFB",
                "NCAAF",
            }
        ):
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

        if has_partial_game_marker(
            pick
        ):
            continue

        pair = pair_from_pick(
            pick
        )

        if len(pair) != 2:
            continue

        existing = resolve_pair(
            pair,
            list(
                events_by_id.values()
            ),
        )

        if existing is not None:
            continue

        if all_teams is None:

            all_teams = fetch_all_teams()

            print(
                "Loaded "
                f"{len(all_teams)} "
                "ESPN teams "
                "for matchup recovery"
            )

        recover_pair_from_schedules(
            pair,
            events_by_id,
            all_teams,
            season_year,
            week,
            schedule_cache,
        )

    events = list(
        events_by_id.values()
    )

    print(
        "Combined complete slate: "
        f"{len(events)} unique events"
    )

    return events


def resolve_total_event(
    pick,
    events,
):
    parsed = parse_total_identity(
        pick.get("selection")
    )

    if len(parsed) == 2:

        event = resolve_pair(
            parsed,
            events,
        )

        if event is not None:
            return event

    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:

        event = resolve_pair(
            matchup,
            events,
        )

        if event is not None:
            return event

    if (
        len(parsed) == 1
        and not is_ambiguous_hint(
            parsed[0]
        )
    ):

        event = event_for_team_by_post_time(
            parsed[0],
            pick,
            events,
        )

        if event is not None:
            return event

    pair = structured_pair(
        pick
    )

    if len(pair) == 2:

        event = resolve_pair(
            pair,
            events,
        )

        if event is not None:
            return event

    team = clean_text(
        pick.get("team")
    )

    if (
        team
        and not is_ambiguous_hint(
            team
        )
    ):

        return event_for_team_by_post_time(
            team,
            pick,
            events,
        )

    return None


def resolve_side_event(
    pick,
    events,
):
    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:

        event = resolve_pair(
            matchup,
            events,
        )

        if event is not None:

            print(
                "SIDE MATCHUP RESOLVED:",
                pick.get("selection"),
                "| matchup:",
                matchup,
                "| event:",
                event.get("id"),
            )

            return event

    identity = side_identity(
        pick
    )

    if (
        identity
        and not is_ambiguous_hint(
            identity
        )
    ):

        return event_for_team_by_post_time(
            identity,
            pick,
            events,
        )

    pair = structured_pair(
        pick
    )

    if len(pair) == 2:

        return resolve_pair(
            pair,
            events,
        )

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

        return resolve_side_event(
            pick,
            events,
        )

    if bet_type == "TOTAL":

        return resolve_total_event(
            pick,
            events,
        )

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

    if is_ambiguous_hint(
        hint
    ):
        return None

    matches = [
        comp
        for comp in competitors(
            event
        )
        if comp_matches(
            comp,
            hint,
        )
    ]

    if len(matches) == 1:
        return matches[0]

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

    scores = {}

    for comp in comps:

        value = score_value(
            comp
        )

        if value is None:
            return False

        comp_id = str(
            comp.get("id")
            or ""
        )

        if not comp_id:
            return False

        scores[
            comp_id
        ] = value

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

        direction = total_direction(
            pick
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

        selected_id = str(
            selected.get("id")
            or ""
        )

        if selected_id not in scores:
            return False

        team_score = scores[
            selected_id
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

        selected = selected_comp(
            pick,
            event,
        )

        if selected is None:
            return False

        selected_id = str(
            selected.get("id")
            or ""
        )

        if selected_id not in scores:
            return False

        others = [
            comp
            for comp in comps
            if str(
                comp.get("id")
                or ""
            ) != selected_id
        ]

        if len(others) != 1:
            return False

        other_id = str(
            others[0].get("id")
            or ""
        )

        if other_id not in scores:
            return False

        margin = (
            scores[selected_id]
            - scores[other_id]
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

            events = build_complete_week_slate(
                picks,
                season_year,
                week,
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
                "Loaded "
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

    pending_events = {}
    summary_cache = {}

    for pick in picks:

        if (
            pick.get("sport")
            not in {
                "CFB",
                "NCAAF",
            }
        ):
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
                pick.get("selection"),
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
                pick.get("selection"),
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
                pick.get("selection"),
            )

            continue

        if (
            bet_type == "TEAM_TOTAL"
            and total_direction(
                pick
            ) is None
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
                pick.get("selection"),
            )

            continue

        events = week_cache[
            key
        ]

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
                if bet_type
                == "TOTAL"
                else side_identity(
                    pick
                )
            )

            print(
                "UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| matchup:",
                pick.get("matchup"),
                "| posted:",
                pick.get("posted_at"),
                "| identity:",
                identity,
            )

            continue

        event = hydrate_event(
            event,
            summary_cache,
        )

        if not completed(
            event
        ):

            not_final += 1

            event_id = str(
                event.get("id")
                or "unknown"
            )

            if (
                event_id
                not in pending_events
            ):

                pending_events[
                    event_id
                ] = {
                    "event": event,
                    "picks": [],
                }

            pending_events[
                event_id
            ][
                "picks"
            ].append(
                {
                    "picker": (
                        pick.get(
                            "picker"
                        )
                    ),
                    "selection": (
                        pick.get(
                            "selection"
                        )
                    ),
                }
            )

            print(
                "NOT FINAL:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "|",
                final_score_text(
                    event
                ),
                "| status:",
                event_status_name(
                    event
                ),
                "| state:",
                event_status_state(
                    event
                ),
                "| event:",
                event.get("id"),
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
                pick.get("selection"),
                "|",
                pick.get("result"),
                "|",
                pick.get("final_score"),
                "| event:",
                pick.get("event_id"),
            )

        else:

            unmatched += 1

            print(
                "GRADE FAILED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "|",
                final_score_text(
                    event
                ),
                "| event:",
                event.get("id"),
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
        f"Safely graded: {graded}"
    )

    print(
        "Unmatched supported picks: "
        f"{unmatched}"
    )

    print(
        "Matched but not final picks: "
        f"{not_final}"
    )

    print(
        "Review/specialty picks: "
        f"{review}"
    )

    print(
        "Preserved due to ESPN failure: "
        f"{preserved}"
    )

    print(
        "Unique pending tracked games: "
        f"{len(pending_events)}"
    )

    if pending_events:

        print()
        print(
            "========== TRUE PENDING GAMES =========="
        )

        for (
            event_id,
            info,
        ) in pending_events.items():

            event = (
                info["event"]
            )

            print(
                "EVENT:",
                event_id,
                "|",
                final_score_text(
                    event
                ),
                "| status:",
                event_status_name(
                    event
                ),
                "| state:",
                event_status_state(
                    event
                ),
                "| kickoff:",
                event.get("date"),
                "| tracked picks:",
                len(
                    info["picks"]
                ),
            )

            for tracked in (
                info["picks"]
            ):

                print(
                    "   -",
                    tracked[
                        "picker"
                    ],
                    "|",
                    tracked[
                        "selection"
                    ],
                )

        print(
            "========================================"
        )

    print(
        "====================================="
    )


if __name__ == "__main__":
    grade_open()
