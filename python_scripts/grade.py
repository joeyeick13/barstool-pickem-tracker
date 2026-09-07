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

# These abbreviations require matchup context.
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
        "conn",
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
        "fiu panthers",
    },

    "georgia tech": {
        "gatech",
        "ga tech",
        "georgia tech",
        "gt",
        "yellow jackets",
    },

    "hawaii": {
        "hawaii",
        "hawai'i",
        "haw",
        "hawaii rainbow warriors",
        "hawaii rainbow warrior",
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

    "kent state": {
        "kent",
        "kent state",
        "kent state golden flashes",
    },

    "liberty": {
        "liberty",
        "liberty flames",
    },

    "liu": {
        "liu",
        "long island",
        "long island university",
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

    "miami ohio": {
        "miami oh",
        "miami ohio",
        "miami (oh)",
        "miami redhawks",
    },

    "michigan": {
        "mich",
        "michigan",
        "michigan wolverines",
    },

    "michigan state": {
        "michigan state",
        "msu",
        "mich state",
        "michigan st",
    },

    "minnesota": {
        "minn",
        "minnesota",
        "minnesota golden gophers",
    },

    "missouri state": {
        "missouri state",
        "missouri st",
        "most",
        "mo state",
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
        "ru",
        "rutg",
        "rutgers scarlet knights",
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
        "aggies",
        "tamu",
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
            for name
            in names
        }

        if normalized in group:
            output |= group

    return output


def is_ambiguous_hint(
    value,
):
    return (
        norm(value)
        in AMBIGUOUS_ALIASES
    )


def competitors(
    event,
):
    competitions = (
        event.get(
            "competitions"
        )
        or []
    )

    if not competitions:
        return []

    return (
        competitions[0]
        .get(
            "competitors"
        )
        or []
    )


def completed(
    event,
):
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


def espn_team_names(
    comp,
):
    team = (
        comp.get("team")
        or {}
    )

    names = set()

    for value in (
        team.get(
            "displayName"
        ),
        team.get(
            "shortDisplayName"
        ),
        team.get(
            "name"
        ),
        team.get(
            "abbreviation"
        ),
        team.get(
            "location"
        ),
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
        alias_group(
            hint
        )
        & espn_team_names(
            comp
        )
    )


def season_year_for_pick(
    pick,
):
    if pick.get(
        "season"
    ):
        return int(
            pick["season"]
        )

    posted = pick.get(
        "posted_at"
    )

    if posted:

        try:

            return (
                datetime
                .fromisoformat(
                    str(
                        posted
                    ).replace(
                        "Z",
                        "+00:00",
                    )
                )
                .year
            )

        except Exception:
            pass

    return datetime.now(
        timezone.utc
    ).year


def week_window(
    season_year,
    week,
):
    if (
        int(
            season_year
        )
        != 2026
    ):

        raise RuntimeError(
            "No verified calendar "
            "configured for "
            f"{season_year}. "
            "Grades preserved."
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
            int(week)
            - 1
        ) * 7
    )

    return (
        week_1_start
        + shift,
        week_1_end
        + shift,
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

    while (
        current
        <= end_date
    ):

        date_string = (
            current.strftime(
                "%Y%m%d"
            )
        )

        payload = request_json(
            ESPN_SCOREBOARD,
            params={
                "dates": (
                    date_string
                ),
                "limit": 1000,
            },
        )

        daily_events = (
            payload.get(
                "events"
            )
            or []
        )

        print(
            "ESPN scoreboard "
            f"{date_string}: "
            f"{len(daily_events)} "
            "events"
        )

        for event in daily_events:

            event_id = str(
                event.get("id")
                or ""
            )

            if not event_id:
                continue

            season = (
                event.get(
                    "season"
                )
                or {}
            )

            event_year = (
                season.get(
                    "year"
                )
            )

            season_type = (
                season.get(
                    "type"
                )
            )

            if (
                event_year
                is not None
                and int(
                    event_year
                )
                != int(
                    season_year
                )
            ):
                continue

            if (
                season_type
                is not None
                and int(
                    season_type
                )
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
        "Scoreboard produced "
        f"{len(events)} "
        "unique events"
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
        payload.get(
            "sports"
        )
        or []
    )

    output = []

    for sport in sports:

        leagues = (
            sport.get(
                "leagues"
            )
            or []
        )

        for league in leagues:

            teams = (
                league.get(
                    "teams"
                )
                or []
            )

            for wrapper in teams:

                team = (
                    wrapper.get(
                        "team"
                    )
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
        team.get(
            "displayName"
        ),
        team.get(
            "shortDisplayName"
        ),
        team.get(
            "name"
        ),
        team.get(
            "abbreviation"
        ),
        team.get(
            "location"
        ),
        team.get(
            "slug"
        ),
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
                    str(
                        team_id
                    )
                )

    matches = list(
        dict.fromkeys(
            matches
        )
    )

    if len(
        matches
    ) == 1:

        return matches[0]

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
        payload.get(
            "events"
        )
        or []
    )


def event_date(
    event,
):
    raw = event.get(
        "date"
    )

    if not raw:
        return None

    try:

        return (
            datetime
            .fromisoformat(
                str(
                    raw
                ).replace(
                    "Z",
                    "+00:00",
                )
            )
            .date()
        )

    except Exception:
        return None


def event_in_week_window(
    event,
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

    value = event_date(
        event
    )

    return bool(
        value
        and start_date
        <= value
        <= end_date
    )


def split_matchup(
    value,
):
    if not value:
        return []

    text = clean_text(
        value
    )

    return [
        part.strip()
        for part
        in re.split(
            (
                r"\s+"
                r"(?:vs\.?|v\.?|at|@)"
                r"\s+|/"
            ),
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
        for marker
        in PARTIAL_GAME_MARKERS
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
        (
            r"\s+"
            r"(?:moneyline|ml)"
            r"\s*$"
        ),
        "",
        text,
        flags=re.I,
    )

    text = re.sub(
        (
            r"\s+"
            r"[+-]\d+"
            r"(?:\.\d+)?"
            r"\s*$"
        ),
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
            r"(over|under)"
            r"\s+"
            r"\d+"
            r"(?:\.\d+)?"
            r"\s*$"
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
        for part
        in re.split(
            (
                r"\s+"
                r"(?:vs\.?|v\.?|at|@)"
                r"\s+|/"
            ),
            prefix,
            flags=re.I,
        )
        if part.strip()
    ][:2]


def side_identity(
    pick,
):
    parsed = (
        parse_side_team(
            pick.get(
                "selection"
            )
        )
    )

    if (
        parsed
        and not is_ambiguous_hint(
            parsed
        )
    ):
        return parsed

    structured = clean_text(
        pick.get(
            "team"
        )
        or pick.get(
            "side"
        )
    )

    if (
        structured
        and not is_ambiguous_hint(
            structured
        )
    ):
        return structured

    return (
        parsed
        or structured
        or None
    )


def structured_total_pair(
    pick,
):
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

    if (
        team
        and opponent
        and not is_ambiguous_hint(
            team
        )
        and not is_ambiguous_hint(
            opponent
        )
    ):

        return [
            team,
            opponent,
        ]

    matchup_parts = (
        split_matchup(
            pick.get(
                "matchup"
            )
        )
    )

    if (
        len(
            matchup_parts
        ) >= 2
        and not any(
            is_ambiguous_hint(
                value
            )
            for value
            in matchup_parts[:2]
        )
    ):

        return (
            matchup_parts[:2]
        )

    return []


def total_identity(
    pick,
):
    parsed = (
        parse_total_identity(
            pick.get(
                "selection"
            )
        )
    )

    if (
        len(
            parsed
        ) == 2
        and not any(
            is_ambiguous_hint(
                value
            )
            for value in parsed
        )
    ):
        return parsed

    if (
        len(
            parsed
        ) == 1
        and not is_ambiguous_hint(
            parsed[0]
        )
    ):
        return parsed

    pair = (
        structured_total_pair(
            pick
        )
    )

    if pair:
        return pair

    team = clean_text(
        pick.get(
            "team"
        )
    )

    if (
        team
        and not is_ambiguous_hint(
            team
        )
    ):
        return [
            team
        ]

    return parsed


def identities_needed_for_pick(
    pick,
):
    bet_type = str(
        pick.get(
            "bet_type"
        )
        or ""
    ).upper()

    if bet_type in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
    }:

        identity = (
            side_identity(
                pick
            )
        )

        return (
            [identity]
            if identity
            else []
        )

    if bet_type == "TOTAL":

        parsed = (
            parse_total_identity(
                pick.get(
                    "selection"
                )
            )
        )

        # Example:
        # OSU @ HOU
        #
        # OSU is ambiguous, but HOU is not.
        # Fetch Houston so the actual opponent
        # can tell us which OSU is intended.
        if len(
            parsed
        ) == 2:

            safe_hints = [
                value
                for value
                in parsed
                if not is_ambiguous_hint(
                    value
                )
            ]

            if safe_hints:
                return safe_hints

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

        if (
            pick.get(
                "sport"
            )
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
            != int(
                season_year
            )
        ):
            continue

        if (
            int(
                pick.get(
                    "week"
                )
                or 1
            )
            != int(
                week
            )
        ):
            continue

        if has_partial_game_marker(
            pick
        ):
            continue

        for hint in (
            identities_needed_for_pick(
                pick
            )
        ):

            if (
                hint
                and not is_ambiguous_hint(
                    hint
                )
            ):

                hints.append(
                    hint
                )

    output = []
    seen = set()

    for hint in hints:

        key = norm(
            hint
        )

        if key in seen:
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
        for event in events
    )


def merge_event(
    events_by_id,
    event,
):
    event_id = str(
        event.get("id")
        or ""
    )

    if event_id:

        events_by_id[
            event_id
        ] = event


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
        str(
            event.get("id")
        ): event

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
        "Missing from scoreboard: "
        f"{len(missing_hints)} "
        "team identities"
    )

    if missing_hints:

        all_teams = (
            fetch_all_teams()
        )

        print(
            "Loaded "
            f"{len(all_teams)} "
            "ESPN teams for "
            "fallback resolution"
        )

        for hint in missing_hints:

            team_id = (
                resolve_espn_team_id(
                    hint,
                    all_teams,
                )
            )

            if not team_id:

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

                if not (
                    event_in_week_window(
                        event,
                        season_year,
                        week,
                    )
                ):
                    continue

                before = len(
                    events_by_id
                )

                merge_event(
                    events_by_id,
                    event,
                )

                if (
                    len(
                        events_by_id
                    )
                    > before
                ):
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
        "Combined complete slate: "
        f"{len(events)} "
        "unique events"
    )

    unresolved = [
        hint

        for hint
        in required_hints

        if not team_present_in_events(
            hint,
            events,
        )
    ]

    if unresolved:

        print(
            "WARNING - unresolved "
            "team identities:",
            ", ".join(
                unresolved
            ),
        )

    return events


def unique_event_for_team(
    team_hint,
    events,
):
    if not team_hint:
        return None

    if is_ambiguous_hint(
        team_hint
    ):
        return None

    matches = []

    for event in events:

        comps = competitors(
            event
        )

        if len(
            comps
        ) != 2:
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

    if len(
        matches
    ) == 1:

        return matches[0]

    return None


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

        if len(
            comps
        ) != 2:
            continue

        found_a = any(
            comp_matches(
                comp,
                team_a,
            )
            for comp
            in comps
        )

        found_b = any(
            comp_matches(
                comp,
                team_b,
            )
            for comp
            in comps
        )

        if (
            found_a
            and found_b
        ):

            matches.append(
                event
            )

    if len(
        matches
    ) == 1:

        return matches[0]

    return None


def resolve_ambiguous_pair(
    team_a,
    team_b,
    events,
):
    """
    Resolve exactly one ambiguous side
    using the known opponent's actual game.

    Example:

        OSU @ HOU

    HOU resolves to Houston.
    Find Houston's unique game.
    Whatever team Houston played is the
    intended OSU in this matchup.

    This is how OSU @ HOU correctly resolves
    to Oregon State vs Houston instead of
    trusting a bad structured Ohio State field.
    """

    a_ambiguous = (
        is_ambiguous_hint(
            team_a
        )
    )

    b_ambiguous = (
        is_ambiguous_hint(
            team_b
        )
    )

    # Exactly one side must be ambiguous.
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

    event = (
        unique_event_for_team(
            anchor,
            events,
        )
    )

    if event is None:
        return None

    comps = competitors(
        event
    )

    if len(
        comps
    ) != 2:
        return None

    anchor_matches = [
        comp

        for comp in comps

        if comp_matches(
            comp,
            anchor,
        )
    ]

    if len(
        anchor_matches
    ) != 1:

        return None

    return event


def resolve_total_event(
    pick,
    events,
):
    parsed = (
        parse_total_identity(
            pick.get(
                "selection"
            )
        )
    )

    # -----------------------------------
    # 1. Visible two-team matchup
    # -----------------------------------

    if len(
        parsed
    ) == 2:

        team_a = parsed[0]
        team_b = parsed[1]

        if (
            is_ambiguous_hint(
                team_a
            )
            or is_ambiguous_hint(
                team_b
            )
        ):

            event = (
                resolve_ambiguous_pair(
                    team_a,
                    team_b,
                    events,
                )
            )

            if event is not None:
                return event

        else:

            event = (
                unique_event_for_pair(
                    team_a,
                    team_b,
                    events,
                )
            )

            if event is not None:
                return event

    # -----------------------------------
    # 2. Visible one-team total
    # -----------------------------------

    if (
        len(
            parsed
        ) == 1
        and not is_ambiguous_hint(
            parsed[0]
        )
    ):

        event = (
            unique_event_for_team(
                parsed[0],
                events,
            )
        )

        if event is not None:
            return event

    # -----------------------------------
    # 3. Structured pair fallback
    # -----------------------------------

    pair = (
        structured_total_pair(
            pick
        )
    )

    if len(
        pair
    ) == 2:

        event = (
            unique_event_for_pair(
                pair[0],
                pair[1],
                events,
            )
        )

        if event is not None:
            return event

    # -----------------------------------
    # 4. Single-team fallback
    # -----------------------------------

    identity = (
        total_identity(
            pick
        )
    )

    if (
        len(
            identity
        ) == 1
        and not is_ambiguous_hint(
            identity[0]
        )
    ):

        return (
            unique_event_for_team(
                identity[0],
                events,
            )
        )

    return None


def resolve_event(
    pick,
    events,
):
    bet_type = str(
        pick.get(
            "bet_type"
        )
        or ""
    ).upper()

    if bet_type in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
    }:

        return (
            unique_event_for_team(
                side_identity(
                    pick
                ),
                events,
            )
        )

    if bet_type == "TOTAL":

        return (
            resolve_total_event(
                pick,
                events,
            )
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

        for comp
        in competitors(
            event
        )

        if comp_matches(
            comp,
            hint,
        )
    ]

    if len(
        matches
    ) == 1:

        return matches[0]

    return None


def total_direction(
    pick,
):
    text = norm(
        pick.get(
            "side"
        )
        or pick.get(
            "selection"
        )
    )

    if "over" in text:
        return "OVER"

    if "under" in text:
        return "UNDER"

    return None


def score_value(
    comp,
):
    """
    ESPN exposes different score shapes.

    Scoreboard may use:
        "score": "34"

    Team schedules may use:
        "score": {
            "value": 34,
            "displayValue": "34"
        }

    Return a float or None.
    Never guess.
    """

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

            value = (
                raw.get(
                    key
                )
            )

            if isinstance(
                value,
                dict,
            ):

                value = (
                    value.get(
                        "value"
                    )
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

        score = (
            score_value(
                comp
            )
        )

        if score is None:

            score_text = "?"

        elif float(
            score
        ).is_integer():

            score_text = str(
                int(
                    score
                )
            )

        else:

            score_text = str(
                score
            )

        output.append(
            f"{label} "
            f"{score_text}"
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

    if len(
        comps
    ) != 2:
        return False

    scores = {}

    for comp in comps:

        value = (
            score_value(
                comp
            )
        )

        # Never grade if the final score
        # cannot be safely read.
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
        pick.get(
            "bet_type"
        )
        or ""
    ).upper()

    line = pick.get(
        "line"
    )

    # ----------------------------
    # GAME TOTAL
    # ----------------------------

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

        game_total = sum(
            scores.values()
        )

        if direction == "OVER":

            if (
                game_total
                > float(
                    line
                )
            ):
                result = "WIN"

            elif (
                game_total
                < float(
                    line
                )
            ):
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if (
                game_total
                < float(
                    line
                )
            ):
                result = "WIN"

            elif (
                game_total
                > float(
                    line
                )
            ):
                result = "LOSS"

            else:
                result = "PUSH"

    # ----------------------------
    # TEAM TOTAL
    # ----------------------------

    elif (
        bet_type
        == "TEAM_TOTAL"
    ):

        if line is None:
            return False

        direction = (
            total_direction(
                pick
            )
        )

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

        selected_id = str(
            selected.get("id")
            or ""
        )

        if (
            not selected_id
            or selected_id
            not in scores
        ):
            return False

        team_score = (
            scores[
                selected_id
            ]
        )

        if direction == "OVER":

            if (
                team_score
                > float(
                    line
                )
            ):
                result = "WIN"

            elif (
                team_score
                < float(
                    line
                )
            ):
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if (
                team_score
                < float(
                    line
                )
            ):
                result = "WIN"

            elif (
                team_score
                > float(
                    line
                )
            ):
                result = "LOSS"

            else:
                result = "PUSH"

    # ----------------------------
    # SPREAD / MONEYLINE
    # ----------------------------

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

        selected_id = str(
            selected.get("id")
            or ""
        )

        if (
            not selected_id
            or selected_id
            not in scores
        ):
            return False

        other_candidates = [
            comp

            for comp
            in comps

            if str(
                comp.get("id")
                or ""
            )
            != selected_id
        ]

        if len(
            other_candidates
        ) != 1:
            return False

        other = (
            other_candidates[0]
        )

        other_id = str(
            other.get("id")
            or ""
        )

        if (
            not other_id
            or other_id
            not in scores
        ):
            return False

        margin = (
            scores[
                selected_id
            ]
            - scores[
                other_id
            ]
        )

        if (
            bet_type
            == "MONEYLINE"
        ):

            if margin > 0:
                result = "WIN"

            elif margin < 0:
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if line is None:
                return False

            adjusted_margin = (
                margin
                + float(
                    line
                )
            )

            if (
                adjusted_margin
                > 0
            ):
                result = "WIN"

            elif (
                adjusted_margin
                < 0
            ):
                result = "LOSS"

            else:
                result = "PUSH"

    else:
        return False

    pick["result"] = (
        result
    )

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
            pick.get(
                "odds"
            ),
            float(
                pick.get(
                    "units"
                )
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
                pick.get(
                    "week"
                )
                or 1
            ),
        )

        for pick
        in picks

        if pick.get(
            "sport"
        )
        in {
            "CFB",
            "NCAAF",
        }
    })

    week_cache = {}
    failed_weeks = set()

    # -----------------------------------
    # FETCH EVERYTHING FIRST
    # -----------------------------------
    #
    # If ESPN retrieval fails, existing
    # grades remain untouched.

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
                    "No ESPN events "
                    "retrieved."
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

    for pick in picks:

        if (
            pick.get(
                "sport"
            )
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
            pick.get(
                "week"
            )
            or 1
        )

        key = (
            season_year,
            week,
        )

        # -----------------------------------
        # PRESERVE ON ESPN FAILURE
        # -----------------------------------

        if key in failed_weeks:

            preserved += 1

            print(
                "PRESERVED:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        bet_type = str(
            pick.get(
                "bet_type"
            )
            or ""
        ).upper()

        # -----------------------------------
        # PARTIAL-GAME BETS
        # -----------------------------------

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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        # -----------------------------------
        # UNSUPPORTED TYPE
        # -----------------------------------

        if (
            bet_type
            not in SUPPORTED
        ):

            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - unsupported:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        # -----------------------------------
        # TEAM TOTAL SAFETY
        # -----------------------------------

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
                "REVIEW - team total "
                "direction missing:",
                pick.get(
                    "picker"
                ),
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

        event = (
            resolve_event(
                pick,
                events,
            )
        )

        # ESPN slate succeeded.
        # Recalculate previous automatic grade.
        clear_grade(
            pick
        )

        # -----------------------------------
        # NO UNIQUE MATCH
        # -----------------------------------

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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| identity:",
                identity,
            )

            continue

        # -----------------------------------
        # GAME NOT FINAL
        # -----------------------------------

        if not completed(
            event
        ):

            not_final += 1

            print(
                "NOT FINAL:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "|",
                final_score_text(
                    event
                ),
                "| event:",
                event.get(
                    "id"
                ),
            )

            continue

        # -----------------------------------
        # GRADE
        # -----------------------------------

        if grade_pick(
            pick,
            event,
        ):

            graded += 1

            print(
                "GRADED:",
                pick.get(
                    "picker"
                ),
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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| event:",
                event.get(
                    "id"
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
