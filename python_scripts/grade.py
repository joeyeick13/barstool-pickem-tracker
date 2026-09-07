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


# ============================================================
# CONFIG
# ============================================================

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

# Never resolve these without matchup/opponent context.
AMBIGUOUS_ALIASES = {
    "osu",
}


# ============================================================
# TEAM ALIASES
# ============================================================
#
# IMPORTANT:
# Do NOT use generic mascot aliases such as "Aggies".
# Multiple schools share mascot names.
#
# Texas A&M intentionally includes A&M/TAMU but NOT "Aggies".
# ============================================================

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

    "coastal carolina": {
        "coastal carolina",
        "coastal",
        "ccu",
    },

    "colorado": {
        "colorado",
        "colo",
        "colorado buffaloes",
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

    "lafayette": {
        "lafayette",
        "lafayette leopards",
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
        "miami redhawks",
        "m-oh",
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

    "stanford": {
        "stanford",
        "stan",
        "stanford cardinal",
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


# ============================================================
# TEXT / TEAM NORMALIZATION
# ============================================================

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


# ============================================================
# ESPN EVENT HELPERS
# ============================================================

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
        .get("completed")
    )


def espn_team_names(comp):
    """
    Only use fields that identify the SCHOOL/team.

    IMPORTANT:
    team["name"] is intentionally excluded.
    ESPN often uses it as a mascot-only value,
    which caused false matches such as "Aggies".
    """

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


# ============================================================
# SEASON / WEEK
# ============================================================

def season_year_for_pick(
    pick,
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
    """
    Verified broad Week 1 collection window.

    Broad window is intentional because ESPN's
    2026 Week 1 slate spans Aug 29 through Sep 7.

    We fail closed for seasons we have not
    explicitly configured.
    """

    if int(season_year) != 2026:

        raise RuntimeError(
            "No verified calendar configured "
            f"for {season_year}. "
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
            int(week) - 1
        ) * 7
    )

    return (
        week_1_start + shift,
        week_1_end + shift,
    )


# ============================================================
# HTTP
# ============================================================

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


# ============================================================
# SCOREBOARD
# ============================================================

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
        f"{len(events)} unique events"
    )

    return events


# ============================================================
# ESPN TEAM DIRECTORY / SCHEDULE FALLBACK
# ============================================================

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
    """
    Same safety rule as espn_team_names():

    Do NOT use mascot-only team["name"].
    """

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


# ============================================================
# PICK PARSING
# ============================================================

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
            pick.get("selection")
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
    """
    Examples:

    'OSU @ HOU Over 49.5'
        -> ['OSU', 'HOU']

    'Texas A&M Over 53.5'
        -> ['Texas A&M']

    'Over 49.5'
        -> []
    """

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


def side_identity(
    pick,
):
    """
    Selection is primary for side bets.

    A&M -40.5 -> A&M -> Texas A&M.
    """

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


def matchup_identity(
    pick,
):
    """
    Read the dedicated matchup field.

    This is critical for records like:

        selection = "Over 49.5"
        matchup   = "OSU @ HOU"

    The old grader skipped this and trusted
    bad extracted team/opponent fields.
    """

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


def total_identity(
    pick,
):
    """
    Safe priority:

    1. Visible matchup inside selection.
    2. Dedicated matchup field.
    3. One-team identity inside selection.
    4. Structured team/opponent.
    5. Structured team only.

    This prevents:
        Over 49.5 + matchup OSU @ HOU

    from being overwritten by:
        team=Ohio State, opponent=Houston.
    """

    parsed = parse_total_identity(
        pick.get("selection")
    )

    if len(parsed) == 2:
        return parsed

    matchup = matchup_identity(
        pick
    )

    if matchup:
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


# ============================================================
# REQUIRED TEAM HINTS FOR FALLBACK
# ============================================================

def safe_hints_from_identity(
    identity,
):
    return [
        value

        for value in identity

        if (
            value
            and not is_ambiguous_hint(
                value
            )
        )
    ]


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

        identity = total_identity(
            pick
        )

        return safe_hints_from_identity(
            identity
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

        for hint in (
            identities_needed_for_pick(
                pick
            )
        ):

            if hint:
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


# ============================================================
# SLATE COMPLETION
# ============================================================

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

                if (
                    len(events_by_id)
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
        f"{len(events)} unique events"
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


# ============================================================
# EVENT MATCHING
# ============================================================

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
    if (
        not team_a
        or not team_b
    ):
        return None

    if (
        is_ambiguous_hint(team_a)
        or is_ambiguous_hint(team_b)
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
    """
    Resolve exactly one ambiguous abbreviation
    through its known opponent.

    Example:

        OSU @ HOU

    HOU -> Houston.

    Houston has one game in the slate.
    ESPN says Houston's opponent is Oregon State.

    Therefore OSU means Oregon State here.

    No fuzzy matching.
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

    comps = competitors(
        event
    )

    if len(comps) != 2:
        return None

    anchor_matches = [
        comp

        for comp
        in comps

        if comp_matches(
            comp,
            anchor,
        )
    ]

    if len(anchor_matches) != 1:
        return None

    print(
        "CONTEXT RESOLVED:",
        f"{team_a} @ {team_b}",
        "->",
        final_score_text(
            event
        ),
        "| event:",
        event.get("id"),
    )

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


def resolve_total_event(
    pick,
    events,
):
    """
    TOTAL matching priority:

    1. Matchup explicitly inside selection.
    2. Dedicated matchup field.
    3. One-team selection identity.
    4. Structured team/opponent fallback.
    5. Structured single-team fallback.

    This is the key fix for Rico OSU/HOU.
    """

    parsed = parse_total_identity(
        pick.get("selection")
    )

    # ------------------------------------------------
    # 1. Explicit two-team matchup in selection
    # ------------------------------------------------

    if len(parsed) == 2:

        event = resolve_pair(
            parsed,
            events,
        )

        if event is not None:
            return event

    # ------------------------------------------------
    # 2. Dedicated matchup field
    # ------------------------------------------------

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

    # ------------------------------------------------
    # 3. One-team identity in selection
    #
    # Texas A&M Over 53.5
    # ------------------------------------------------

    if (
        len(parsed) == 1
        and not is_ambiguous_hint(
            parsed[0]
        )
    ):

        event = unique_event_for_team(
            parsed[0],
            events,
        )

        if event is not None:
            return event

    # ------------------------------------------------
    # 4. Structured pair is LAST pair fallback.
    #
    # This prevents a bad extracted:
    #
    # team = Ohio State
    # opponent = Houston
    #
    # from overriding matchup = OSU @ HOU.
    # ------------------------------------------------

    pair = structured_pair(
        pick
    )

    if (
        len(pair) == 2
        and not any(
            is_ambiguous_hint(x)
            for x in pair
        )
    ):

        event = unique_event_for_pair(
            pair[0],
            pair[1],
            events,
        )

        if event is not None:
            return event

    # ------------------------------------------------
    # 5. Structured single team
    # ------------------------------------------------

    team = clean_text(
        pick.get("team")
    )

    if (
        team
        and not is_ambiguous_hint(
            team
        )
    ):

        event = unique_event_for_team(
            team,
            events,
        )

        if event is not None:
            return event

    return None


def resolve_side_event(
    pick,
    events,
):
    """
    Side bets are primarily identified from
    the actual selection text.

    Example:
        A&M -40.5
        -> A&M
        -> Texas A&M

    If the side is ambiguous, use matchup context.
    """

    identity = side_identity(
        pick
    )

    if (
        identity
        and not is_ambiguous_hint(
            identity
        )
    ):

        event = unique_event_for_team(
            identity,
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


# ============================================================
# SCORE PARSING
# ============================================================

def score_value(comp):
    """
    ESPN returns score in multiple shapes.

    Scoreboard:
        "score": "34"

    Team schedule:
        "score": {
            "value": 34,
            "displayValue": "34"
        }

    Never guess if unreadable.
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

        score = score_value(
            comp
        )

        if score is None:

            score_text = "?"

        elif float(
            score
        ).is_integer():

            score_text = str(
                int(score)
            )

        else:

            score_text = str(
                score
            )

        output.append(
            f"{label} {score_text}"
        )

    return " - ".join(
        output
    )


# ============================================================
# SELECTED TEAM
# ============================================================

def selected_comp(
    pick,
    event,
):
    """
    Identify the selected side from selection text.

    For A&M -40.5:
        A&M -> Texas A&M competitor.
    """

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

    if len(matches) == 1:
        return matches[0]

    return None


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


# ============================================================
# GRADING
# ============================================================

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

    # ========================================================
    # GAME TOTAL
    # ========================================================

    if bet_type == "TOTAL":

        if line is None:
            return False

        direction = total_direction(
            pick
        )

        if direction is None:
            return False

        game_total = sum(
            scores.values()
        )

        if direction == "OVER":

            if game_total > float(line):
                result = "WIN"

            elif game_total < float(line):
                result = "LOSS"

            else:
                result = "PUSH"

        else:

            if game_total < float(line):
                result = "WIN"

            elif game_total > float(line):
                result = "LOSS"

            else:
                result = "PUSH"

    # ========================================================
    # TEAM TOTAL
    # ========================================================

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

        if (
            not selected_id
            or selected_id
            not in scores
        ):
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

    # ========================================================
    # SPREAD / MONEYLINE
    # ========================================================

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

        if (
            not selected_id
            or selected_id
            not in scores
        ):
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

        if (
            not other_id
            or other_id
            not in scores
        ):
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

            adjusted_margin = (
                margin
                + float(line)
            )

            if adjusted_margin > 0:
                result = "WIN"

            elif adjusted_margin < 0:
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


# ============================================================
# GRADE RESET
# ============================================================

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


# ============================================================
# MAIN
# ============================================================

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

    # ========================================================
    # FETCH FIRST
    #
    # Do not alter any grades until ESPN retrieval succeeds.
    # ========================================================

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

        # ====================================================
        # ESPN FAILURE -> PRESERVE EXISTING GRADE
        # ====================================================

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

        # ====================================================
        # PARTIAL GAME -> REVIEW
        # ====================================================

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

        # ====================================================
        # UNSUPPORTED -> REVIEW
        # ====================================================

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

        # ====================================================
        # TEAM TOTAL WITHOUT DIRECTION -> REVIEW
        # ====================================================

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

        # ESPN succeeded, so safely recalculate.
        clear_grade(
            pick
        )

        # ====================================================
        # UNMATCHED
        # ====================================================

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
                pick.get("selection"),
                "| matchup:",
                pick.get("matchup"),
                "| identity:",
                identity,
            )

            continue

        # ====================================================
        # MATCHED BUT NOT FINAL
        # ====================================================

        if not completed(
            event
        ):

            not_final += 1

            print(
                "NOT FINAL:",
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

            continue

        # ====================================================
        # GRADE
        # ====================================================

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
        "Matched but not final: "
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
        "====================================="
    )


if __name__ == "__main__":
    grade_open()
