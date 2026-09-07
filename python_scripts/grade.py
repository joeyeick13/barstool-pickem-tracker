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
# ESPN
# ============================================================

ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football"
)

ESPN_SCOREBOARD = f"{ESPN_BASE}/scoreboard"
ESPN_TEAMS = f"{ESPN_BASE}/teams"
ESPN_SUMMARY = f"{ESPN_BASE}/summary"

ESPN_CORE = (
    "https://sports.core.api.espn.com/v2/"
    "sports/football/leagues/"
    "college-football"
)

REGULAR_SEASON = 2


SUPPORTED = {
    "SPREAD",
    "TOTAL",
    "MONEYLINE",
    "TEAM_TOTAL",

    "FIRST_QUARTER_SPREAD",
    "FIRST_QUARTER_TOTAL",
    "FIRST_QUARTER_MONEYLINE",
    "FIRST_QUARTER_TEAM_TOTAL",

    "FIRST_HALF_SPREAD",
    "FIRST_HALF_TOTAL",
    "FIRST_HALF_MONEYLINE",
    "FIRST_HALF_TEAM_TOTAL",
}


AMBIGUOUS_ALIASES = {
    "osu",
}


# ============================================================
# SAFE TEAM ALIASES
# ============================================================

ALIASES = {
    "alabama": {
        "alabama",
        "bama",
    },

    "arkansas state": {
        "arkansas state",
        "arkansas st",
        "ark st",
        "arst",
    },

    "auburn": {
        "auburn",
        "aub",
    },

    "ball state": {
        "ball state",
        "ball st",
    },

    "baylor": {
        "baylor",
    },

    "boise state": {
        "boise",
        "boise state",
        "boise st",
    },

    "byu": {
        "byu",
        "brigham young",
    },

    "california": {
        "cal",
        "california",
    },

    "clemson": {
        "clem",
        "clemson",
    },

    "connecticut": {
        "uconn",
        "connecticut",
        "conn",
    },

    "duke": {
        "duke",
    },

    "eastern illinois": {
        "eiu",
        "eastern illinois",
    },

    "east carolina": {
        "ecu",
        "east carolina",
    },

    "fiu": {
        "fiu",
        "florida international",
        "florida intl",
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
    },

    "houston": {
        "hou",
        "houston",
    },

    "illinois": {
        "ill",
        "illinois",
    },

    "indiana": {
        "indiana",
        "ind",
    },

    "kansas": {
        "kansas",
        "ku",
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
    },

    "miami": {
        "miami",
        "miami fl",
        "miami florida",
    },

    "miami ohio": {
        "miami oh",
        "miami ohio",
        "miami (oh)",
        "m-oh",
    },

    "michigan": {
        "mich",
        "michigan",
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
    },

    "ohio state": {
        "ohio state",
        "ohio st",
    },

    "oklahoma": {
        "oklahoma",
        "ou",
    },

    "oklahoma state": {
        "ok state",
        "ok st",
        "oklahoma state",
        "oklahoma st",
    },

    "oregon": {
        "ore",
        "oregon",
    },

    "oregon state": {
        "oregon state",
        "oregon st",
        "ore st",
    },

    "pittsburgh": {
        "pitt",
        "pittsburgh",
    },

    "rutgers": {
        "rutgers",
        "rutg",
        "ru",
    },

    "south carolina": {
        "south carolina",
        "scar",
    },

    "south florida": {
        "usf",
        "south florida",
    },

    "temple": {
        "temple",
        "tem",
    },

    "texas": {
        "tex",
        "texas",
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
    },

    "texas state": {
        "texas state",
        "tex st",
        "txst",
    },

    "toledo": {
        "tol",
        "toledo",
    },

    "tulane": {
        "tulane",
    },

    "tulsa": {
        "tulsa",
    },

    "ucla": {
        "ucla",
    },

    "umass": {
        "umass",
        "massachusetts",
    },

    "unlv": {
        "unlv",
    },

    "usc": {
        "usc",
        "southern california",
        "southern cal",
    },

    "utah tech": {
        "utah tech",
        "ut tech",
    },

    "washington": {
        "wash",
        "uw",
        "washington",
    },

    "washington state": {
        "wazzu",
        "wsu",
        "washington state",
        "washington st",
    },

    "west virginia": {
        "wvu",
        "west virginia",
    },

    "western michigan": {
        "wmu",
        "western michigan",
    },

    "wisconsin": {
        "wis",
        "wisc",
        "wisconsin",
    },
}


# ============================================================
# BASIC HELPERS
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
        text
        .replace("&", " and ")
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


# ============================================================
# DATETIME / WEEK
# ============================================================

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

    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def pick_posted_datetime(pick):
    return parse_datetime(
        pick.get("posted_at")
    )


def event_datetime(event):
    return parse_datetime(
        event.get("date")
    )


def season_year_for_pick(pick):
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
    """
    Verified 2026 mapping.

    Week 1:
        Aug 22 - Sep 7

    Week 2:
        Sep 8 - Sep 13

    Week 3:
        Sep 14 - Sep 20

    Week 4+:
        Monday-Sunday.
    """

    if int(season_year) != 2026:
        raise RuntimeError(
            "Season calendar has not "
            f"been verified for {season_year}. "
            "Existing grades preserved."
        )

    week = int(
        week
    )

    if week == 1:
        return (
            date(2026, 8, 22),
            date(2026, 9, 7),
        )

    if week == 2:
        return (
            date(2026, 9, 8),
            date(2026, 9, 13),
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
        start + timedelta(
            days=6
        ),
    )


# ============================================================
# BET MARKET NORMALIZATION
# ============================================================

def normalize_bet_type(value):
    raw = str(
        value or ""
    ).upper().strip()

    aliases = {
        "1Q_SPREAD": (
            "FIRST_QUARTER_SPREAD"
        ),
        "1Q_TOTAL": (
            "FIRST_QUARTER_TOTAL"
        ),
        "1Q_MONEYLINE": (
            "FIRST_QUARTER_MONEYLINE"
        ),
        "1Q_TEAM_TOTAL": (
            "FIRST_QUARTER_TEAM_TOTAL"
        ),

        "1H_SPREAD": (
            "FIRST_HALF_SPREAD"
        ),
        "1H_TOTAL": (
            "FIRST_HALF_TOTAL"
        ),
        "1H_MONEYLINE": (
            "FIRST_HALF_MONEYLINE"
        ),
        "1H_TEAM_TOTAL": (
            "FIRST_HALF_TEAM_TOTAL"
        ),
    }

    return aliases.get(
        raw,
        raw,
    )


def normalize_existing_pick_market(pick):
    """
    Upgrade old stored market labels from selection text.

    Never assigns a result.
    """

    text = str(
        pick.get("selection")
        or ""
    ).lower()

    text = (
        text
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    first_quarter = bool(
        re.search(
            r"\b(?:1q|1st\s*q|"
            r"first\s*quarter)\b",
            text,
            flags=re.I,
        )
    )

    first_half = bool(
        re.search(
            r"\b(?:1h|1st\s*h|"
            r"first\s*half)\b",
            text,
            flags=re.I,
        )
    )

    team_total = bool(
        re.search(
            r"\b(?:tt|team\s*total)\b",
            text,
            flags=re.I,
        )
    )

    if team_total:

        if first_quarter:
            pick["bet_type"] = (
                "FIRST_QUARTER_TEAM_TOTAL"
            )

        elif first_half:
            pick["bet_type"] = (
                "FIRST_HALF_TEAM_TOTAL"
            )

        else:
            pick["bet_type"] = (
                "TEAM_TOTAL"
            )

        compact = re.search(
            r"\b(?:tt|team\s*total)\s*"
            r"(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if compact:
            pick["side"] = (
                "OVER"
                if compact.group(1).lower()
                == "o"
                else "UNDER"
            )

            pick["line"] = float(
                compact.group(2)
            )

        verbose = re.search(
            r"\b(?:tt|team\s*total)\s*"
            r"(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if verbose:
            pick["side"] = (
                verbose.group(1).upper()
            )

            pick["line"] = float(
                verbose.group(2)
            )

        return

    if first_quarter or first_half:

        prefix = (
            "FIRST_QUARTER"
            if first_quarter
            else "FIRST_HALF"
        )

        if re.search(
            r"\b(?:over|under)\b",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:ml|moneyline)\b",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*"
            r"\d+(?:\.\d+)?",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_SPREAD"
            )

        return

    pick["bet_type"] = (
        normalize_bet_type(
            pick.get("bet_type")
        )
    )


def market_period(bet_type):
    bet_type = (
        normalize_bet_type(
            bet_type
        )
    )

    if bet_type.startswith(
        "FIRST_QUARTER_"
    ):
        return "FIRST_QUARTER"

    if bet_type.startswith(
        "FIRST_HALF_"
    ):
        return "FIRST_HALF"

    return "FULL_GAME"


def base_market(bet_type):
    bet_type = (
        normalize_bet_type(
            bet_type
        )
    )

    for prefix in (
        "FIRST_QUARTER_",
        "FIRST_HALF_",
    ):
        if bet_type.startswith(
            prefix
        ):
            return bet_type[
                len(prefix):
            ]

    return bet_type


def total_direction(pick):
    side = str(
        pick.get("side")
        or ""
    ).strip().lower()

    if side in {
        "o",
        "over",
    }:
        return "OVER"

    if side in {
        "u",
        "under",
    }:
        return "UNDER"

    text = str(
        pick.get("selection")
        or ""
    ).lower()

    if re.search(
        r"\bover\b",
        text,
    ):
        return "OVER"

    if re.search(
        r"\bunder\b",
        text,
    ):
        return "UNDER"

    compact = re.search(
        r"\b(?:tt|team\s*total)\s*"
        r"(o|u)\s*"
        r"\d+(?:\.\d+)?",
        text,
        flags=re.I,
    )

    if compact:
        return (
            "OVER"
            if compact.group(1).lower()
            == "o"
            else "UNDER"
        )

    return None


# ============================================================
# EVENT HELPERS
# ============================================================

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


def event_status(event):
    top = (
        event.get("status")
        or {}
    )

    if top:
        return top

    comps = competitions(
        event
    )

    if comps:
        return (
            comps[0].get("status")
            or {}
        )

    return {}


def completed(event):
    return bool(
        (
            event_status(event)
            .get("type")
            or {}
        ).get("completed")
    )


def event_status_name(event):
    return str(
        (
            event_status(event)
            .get("type")
            or {}
        ).get("name")
        or ""
    )


def event_status_state(event):
    return str(
        (
            event_status(event)
            .get("type")
            or {}
        ).get("state")
        or ""
    )


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
        except Exception:
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

            try:
                if value is not None:
                    return float(
                        value
                    )

            except Exception:
                continue

    return None


def event_has_scores(event):
    comps = competitors(
        event
    )

    return (
        len(comps) == 2
        and all(
            score_value(comp)
            is not None
            for comp in comps
        )
    )


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
            display = "?"

        elif float(
            value
        ).is_integer():
            display = str(
                int(value)
            )

        else:
            display = str(
                value
            )

        output.append(
            f"{label} {display}"
        )

    return " - ".join(
        output
    )


def espn_team_names(comp):
    """
    Mascot-only team["name"] intentionally excluded.
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
    if (
        not hint
        or is_ambiguous_hint(
            hint
        )
    ):
        return False

    return bool(
        alias_group(hint)
        & espn_team_names(
            comp
        )
    )


# ============================================================
# EVENT MERGING
# ============================================================

def event_quality(event):
    quality = 0

    if completed(
        event
    ):
        quality += 1000

    if (
        event_status_state(
            event
        ).lower()
        == "post"
    ):
        quality += 500

    if event_has_scores(
        event
    ):
        quality += 300

    if (
        "FINAL"
        in event_status_name(
            event
        ).upper()
    ):
        quality += 200

    if event.get("date"):
        quality += 20

    quality += (
        len(
            competitors(
                event
            )
        )
        * 10
    )

    return quality


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

    old_quality = event_quality(
        existing
    )

    new_quality = event_quality(
        event
    )

    if new_quality > old_quality:
        events_by_id[
            event_id
        ] = event

        print(
            "EVENT UPGRADED:",
            event_id,
            "| source:",
            source,
            "|",
            old_quality,
            "->",
            new_quality,
        )

        return True

    return False


# ============================================================
# SCOREBOARD
# ============================================================

def fetch_scoreboard_events(
    season_year,
    week,
):
    start_date, end_date = (
        week_window(
            season_year,
            week,
        )
    )

    events_by_id = {}

    current = start_date

    while current <= end_date:

        ymd = current.strftime(
            "%Y%m%d"
        )

        payload = request_json(
            ESPN_SCOREBOARD,
            {
                "dates": ymd,
                "limit": 1000,
            },
        )

        daily = (
            payload.get("events")
            or []
        )

        print(
            "ESPN scoreboard",
            ymd,
            ":",
            len(daily),
            "events",
        )

        for event in daily:

            season = (
                event.get("season")
                or {}
            )

            year = season.get(
                "year"
            )

            season_type = (
                season.get("type")
            )

            if (
                year is not None
                and int(year)
                != int(season_year)
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

            merge_event(
                events_by_id,
                event,
                "scoreboard",
            )

        current += timedelta(
            days=1
        )

    return list(
        events_by_id.values()
    )


# ============================================================
# ESPN TEAM DIRECTORY / SCHEDULE
# ============================================================

def fetch_all_teams():
    payload = request_json(
        ESPN_TEAMS,
        {
            "limit": 1000
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
    if (
        not hint
        or is_ambiguous_hint(
            hint
        )
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
        {
            "season": (
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

    if not dt:
        return False

    start, end = week_window(
        season_year,
        week,
    )

    return (
        start
        <= dt.date()
        <= end
    )


def schedule_events_for_hint(
    hint,
    all_teams,
    season_year,
    week,
    cache,
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
        season_year,
        week,
    )

    if key in cache:
        return cache[
            key
        ]

    team_id = (
        resolve_espn_team_id(
            hint,
            all_teams,
        )
    )

    if not team_id:
        cache[key] = []

        return []

    try:
        schedule = (
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

        cache[key] = []

        return []

    output = [
        event
        for event in schedule
        if event_in_week_window(
            event,
            season_year,
            week,
        )
    ]

    cache[key] = output

    print(
        "TEAM SCHEDULE:",
        hint,
        "| events:",
        len(output),
    )

    return output


# ============================================================
# SELECTION PARSING
# ============================================================

def split_matchup(value):
    if not value:
        return []

    return [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|"
            r"at|@)\s+|/",
            clean_text(value),
            flags=re.I,
        )
        if part.strip()
    ]


def strip_period_tokens(text):
    text = re.sub(
        r"\b(?:1q|1st\s*q|"
        r"first\s*quarter|"
        r"1h|1st\s*h|"
        r"first\s*half)\b",
        " ",
        text,
        flags=re.I,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def parse_side_team(selection):
    text = clean_text(
        selection
    )

    if not text:
        return None

    text = strip_period_tokens(
        text
    )

    # Team-total tail
    text = re.sub(
        r"\s+(?:tt|team\s*total)"
        r"\s*(?:o|u|over|under)?"
        r"\s*\d+(?:\.\d+)?\s*$",
        "",
        text,
        flags=re.I,
    )

    # Moneyline tail
    text = re.sub(
        r"\s+(?:moneyline|ml)\s*$",
        "",
        text,
        flags=re.I,
    )

    # Spread tail
    text = re.sub(
        r"\s+[+-]\d+(?:\.\d+)?\s*$",
        "",
        text,
    )

    # Total tail
    text = re.sub(
        r"\s+(?:over|under)"
        r"\s+\d+(?:\.\d+)?\s*$",
        "",
        text,
        flags=re.I,
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

    text = strip_period_tokens(
        text
    )

    if re.search(
        r"\b(?:tt|team\s*total)\b",
        text,
        flags=re.I,
    ):
        return []

    match = re.match(
        r"^(.*?)\s+"
        r"(?:over|under)\s+"
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

    return [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|"
            r"at|@)\s+|/",
            prefix,
            flags=re.I,
        )
        if part.strip()
    ][:2]


def matchup_identity(pick):
    parts = split_matchup(
        pick.get("matchup")
    )

    if len(parts) >= 2:
        return parts[:2]

    return []


def structured_pair(pick):
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


def side_identity(pick):
    team = clean_text(
        pick.get("team")
    )

    if team:
        return team

    return parse_side_team(
        pick.get("selection")
    )


def total_identity(pick):
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


# ============================================================
# EVENT MATCHING
# ============================================================

def matching_events_for_team(
    team_hint,
    events,
):
    if (
        not team_hint
        or is_ambiguous_hint(
            team_hint
        )
    ):
        return []

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

    return matches


def unique_event_for_team(
    team_hint,
    events,
):
    matches = (
        matching_events_for_team(
            team_hint,
            events,
        )
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

        a = any(
            comp_matches(
                comp,
                team_a,
            )
            for comp in comps
        )

        b = any(
            comp_matches(
                comp,
                team_b,
            )
            for comp in comps
        )

        if a and b:
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
    if a_ambiguous == b_ambiguous:
        return None

    anchor = (
        team_b
        if a_ambiguous
        else team_a
    )

    return unique_event_for_team(
        anchor,
        events,
    )


def resolve_pair(
    pair,
    events,
):
    if len(pair) != 2:
        return None

    a, b = pair

    if (
        is_ambiguous_hint(a)
        or is_ambiguous_hint(b)
    ):
        return (
            resolve_ambiguous_pair(
                a,
                b,
                events,
            )
        )

    return unique_event_for_pair(
        a,
        b,
        events,
    )


def event_for_team_by_post_time(
    team_hint,
    pick,
    events,
):
    matches = (
        matching_events_for_team(
            team_hint,
            events,
        )
    )

    if len(matches) == 1:
        return matches[0]

    if not matches:
        return None

    posted = (
        pick_posted_datetime(
            pick
        )
    )

    if not posted:
        return None

    candidates = []

    for event in matches:

        kickoff = (
            event_datetime(
                event
            )
        )

        if not kickoff:
            continue

        if kickoff >= (
            posted
            - timedelta(hours=3)
        ):
            distance = abs(
                (
                    kickoff
                    - posted
                ).total_seconds()
            )

            candidates.append(
                (
                    distance,
                    kickoff,
                    event,
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        )
    )

    if len(candidates) > 1:

        if abs(
            candidates[1][0]
            - candidates[0][0]
        ) < 3600:

            return None

    event = (
        candidates[0][2]
    )

    print(
        "POST-TIME RESOLVED:",
        team_hint,
        "|",
        pick.get("selection"),
        "| event:",
        event.get("id"),
    )

    return event


# ============================================================
# REQUIRED IDENTITIES / COMPLETE SLATE
# ============================================================

def pair_from_pick(pick):
    market = base_market(
        pick.get("bet_type")
    )

    if market == "TOTAL":
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


def identities_needed_for_pick(
    pick,
):
    market = base_market(
        pick.get("bet_type")
    )

    if market in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
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

    if market == "TOTAL":
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


def pick_belongs_to_week(
    pick,
    season_year,
    week,
):
    return (
        pick.get("sport")
        in {
            "CFB",
            "NCAAF",
        }
        and season_year_for_pick(
            pick
        )
        == int(season_year)
        and int(
            pick.get("week")
            or 1
        )
        == int(week)
    )


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    initial = (
        fetch_scoreboard_events(
            season_year,
            week,
        )
    )

    events_by_id = {}

    for event in initial:
        merge_event(
            events_by_id,
            event,
            "scoreboard",
        )

    required_hints = []

    seen = set()

    for pick in picks:

        if not pick_belongs_to_week(
            pick,
            season_year,
            week,
        ):
            continue

        for hint in (
            identities_needed_for_pick(
                pick
            )
        ):
            key = norm(
                hint
            )

            if (
                key
                and key not in seen
            ):
                seen.add(
                    key
                )

                required_hints.append(
                    hint
                )

    missing = [
        hint
        for hint in required_hints
        if not matching_events_for_team(
            hint,
            list(
                events_by_id.values()
            ),
        )
    ]

    print(
        "Missing from scoreboard:",
        len(missing),
        "team identities",
    )

    all_teams = None
    schedule_cache = {}

    if missing:

        all_teams = fetch_all_teams()

        print(
            "Loaded",
            len(all_teams),
            "ESPN teams",
        )

        for hint in missing:

            schedule_events = (
                schedule_events_for_hint(
                    hint,
                    all_teams,
                    season_year,
                    week,
                    schedule_cache,
                )
            )

            for event in schedule_events:

                merge_event(
                    events_by_id,
                    event,
                    (
                        "team-fallback:"
                        + hint
                    ),
                )

    # Explicit matchup recovery pass.
    for pick in picks:

        if not pick_belongs_to_week(
            pick,
            season_year,
            week,
        ):
            continue

        pair = pair_from_pick(
            pick
        )

        if len(pair) != 2:
            continue

        if resolve_pair(
            pair,
            list(
                events_by_id.values()
            ),
        ):
            continue

        safe_hints = [
            hint
            for hint in pair
            if not is_ambiguous_hint(
                hint
            )
        ]

        if not safe_hints:
            continue

        if all_teams is None:
            all_teams = (
                fetch_all_teams()
            )

        print(
            "MATCHUP RECOVERY:",
            pair,
        )

        for hint in safe_hints:

            schedule_events = (
                schedule_events_for_hint(
                    hint,
                    all_teams,
                    season_year,
                    week,
                    schedule_cache,
                )
            )

            for event in schedule_events:

                merge_event(
                    events_by_id,
                    event,
                    (
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

        if recovered:

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

    events = list(
        events_by_id.values()
    )

    print(
        "Combined complete slate:",
        len(events),
        "unique events",
    )

    return events


# ============================================================
# PICK -> EVENT
# ============================================================

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

        if event:
            return event

    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:

        event = resolve_pair(
            matchup,
            events,
        )

        if event:
            return event

    pair = structured_pair(
        pick
    )

    if len(pair) == 2:

        event = resolve_pair(
            pair,
            events,
        )

        if event:
            return event

    if len(parsed) == 1:

        return (
            event_for_team_by_post_time(
                parsed[0],
                pick,
                events,
            )
        )

    team = clean_text(
        pick.get("team")
    )

    if team:

        return (
            event_for_team_by_post_time(
                team,
                pick,
                events,
            )
        )

    return None


def resolve_side_event(
    pick,
    events,
):
    # Matchup context first.
    matchup = matchup_identity(
        pick
    )

    if len(matchup) == 2:

        event = resolve_pair(
            matchup,
            events,
        )

        if event:
            return event

    # Structured pair second.
    pair = structured_pair(
        pick
    )

    if len(pair) == 2:

        event = resolve_pair(
            pair,
            events,
        )

        if event:
            return event

    # Single-team selection last.
    team = side_identity(
        pick
    )

    if (
        team
        and not is_ambiguous_hint(
            team
        )
    ):

        return (
            event_for_team_by_post_time(
                team,
                pick,
                events,
            )
        )

    return None


def resolve_event(
    pick,
    events,
):
    market = base_market(
        pick.get("bet_type")
    )

    if market == "TOTAL":

        return resolve_total_event(
            pick,
            events,
        )

    if market in {
        "SPREAD",
        "MONEYLINE",
        "TEAM_TOTAL",
    }:

        return resolve_side_event(
            pick,
            events,
        )

    return None


# ============================================================
# EVENT HYDRATION
# ============================================================

def fetch_event_summary(
    event_id,
):
    return request_json(
        ESPN_SUMMARY,
        {
            "event": event_id
        },
    )


def event_from_summary(
    payload,
    fallback,
):
    header = (
        payload.get("header")
        or {}
    )

    comps = (
        header.get("competitions")
        or []
    )

    if not comps:
        return fallback

    competition = (
        comps[0]
    )

    event = dict(
        fallback
    )

    event["id"] = str(
        competition.get("id")
        or header.get("id")
        or fallback.get("id")
    )

    if competition.get("date"):

        event["date"] = (
            competition["date"]
        )

    if competition.get("status"):

        event["status"] = (
            competition["status"]
        )

    event["competitions"] = [
        competition
    ]

    return event


def hydrate_event(
    event,
    cache,
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

    missing_status = (
        not state
        and not name
    )

    suspicious = (
        event_has_scores(
            event
        )
        and state not in {
            "pre",
            "in",
        }
    )

    if not (
        missing_status
        or suspicious
    ):
        return event

    if event_id in cache:
        return cache[
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

        cache[event_id] = (
            hydrated
        )

        print(
            "EVENT HYDRATED:",
            event_id,
            "| completed:",
            completed(
                hydrated
            ),
            "| status:",
            event_status_name(
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

        cache[event_id] = event

        return event


# ============================================================
# QUARTER / HALF LINESCORES
# ============================================================

def competition_id(event):
    comps = competitions(
        event
    )

    if comps:

        value = (
            comps[0].get("id")
        )

        if value:

            return str(
                value
            )

    return str(
        event.get("id")
        or ""
    )


def extract_linescores(raw):
    """
    Convert ESPN linescore payload into:

        {
            1: 7.0,
            2: 10.0,
            3: 3.0,
            4: 7.0
        }

    Supports both embedded scoreboard linescores and
    ESPN Core linescore objects.
    """

    output = {}

    for index, item in enumerate(
        raw or [],
        start=1,
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        period = (
            item.get("period")
            or item.get(
                "sequenceNumber"
            )
            or item.get(
                "sequence"
            )
            or index
        )

        if isinstance(
            period,
            dict,
        ):

            period = (
                period.get("number")
                or period.get("value")
            )

        try:

            period = int(
                period
            )

        except Exception:

            continue

        value = None

        for key in (
            "value",
            "score",
            "displayValue",
        ):

            candidate = item.get(
                key
            )

            if isinstance(
                candidate,
                dict,
            ):

                candidate = (
                    candidate.get(
                        "value"
                    )
                    or candidate.get(
                        "displayValue"
                    )
                )

            try:

                if candidate is not None:

                    value = float(
                        candidate
                    )

                    break

            except Exception:

                pass

        if value is not None:

            output[
                period
            ] = value

    return output


def embedded_linescores(comp):
    return extract_linescores(
        comp.get("linescores")
        or []
    )


def fetch_team_linescores(
    event,
    comp,
):
    event_id = str(
        event.get("id")
        or ""
    )

    comp_id = competition_id(
        event
    )

    team = (
        comp.get("team")
        or {}
    )

    team_id = str(
        team.get("id")
        or ""
    )

    if not (
        event_id
        and comp_id
        and team_id
    ):
        return []

    url = (
        f"{ESPN_CORE}/events/"
        f"{event_id}/competitions/"
        f"{comp_id}/competitors/"
        f"{team_id}/linescores"
    )

    payload = request_json(
        url,
        {
            "limit": 100
        },
    )

    return (
        payload.get("items")
        or payload.get(
            "linescores"
        )
        or []
    )


def get_period_scores(
    event,
    comp,
    cache,
):
    event_id = str(
        event.get("id")
        or ""
    )

    team_id = str(
        (
            comp.get("team")
            or {}
        ).get("id")
        or ""
    )

    key = (
        event_id,
        team_id,
    )

    if key in cache:

        return cache[
            key
        ]

    # First prefer ESPN linescores already embedded
    # in scoreboard/summary data.
    embedded = (
        embedded_linescores(
            comp
        )
    )

    if embedded:

        cache[key] = (
            embedded
        )

        return embedded

    # Otherwise use ESPN's exact competitor
    # period-linescore endpoint.
    try:

        raw = fetch_team_linescores(
            event,
            comp,
        )

        scores = extract_linescores(
            raw
        )

        cache[key] = (
            scores
        )

        return scores

    except Exception as exc:

        print(
            "LINESCORES FAILED:",
            event_id,
            "| team:",
            team_id,
            "|",
            exc,
        )

        cache[key] = {}

        return {}


def score_for_market(
    event,
    comp,
    period,
    cache,
):
    if period == "FULL_GAME":

        return score_value(
            comp
        )

    lines = get_period_scores(
        event,
        comp,
        cache,
    )

    if period == (
        "FIRST_QUARTER"
    ):

        return lines.get(
            1
        )

    if period == (
        "FIRST_HALF"
    ):

        q1 = lines.get(
            1
        )

        q2 = lines.get(
            2
        )

        if (
            q1 is None
            or q2 is None
        ):

            return None

        return (
            q1 + q2
        )

    return None


def period_score_text(
    event,
    period,
    cache,
):
    parts = []

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

        value = score_for_market(
            event,
            comp,
            period,
            cache,
        )

        if value is None:

            display = "?"

        elif float(
            value
        ).is_integer():

            display = str(
                int(value)
            )

        else:

            display = str(
                value
            )

        parts.append(
            f"{label} {display}"
        )

    prefix = {
        "FIRST_QUARTER": "1Q",
        "FIRST_HALF": "1H",
        "FULL_GAME": "FINAL",
    }.get(
        period,
        period,
    )

    return (
        prefix
        + ": "
        + " - ".join(
            parts
        )
    )


# ============================================================
# SELECTED TEAM
# ============================================================

def selected_comp(
    pick,
    event,
):
    hint = side_identity(
        pick
    )

    if (
        not hint
        or is_ambiguous_hint(
            hint
        )
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


# ============================================================
# GRADING
# ============================================================

def grade_pick(
    pick,
    event,
    linescore_cache,
):
    """
    Fail closed.

    1Q = period 1 only.

    1H = period 1 + period 2.

    Period team totals use the selected team's points only.

    We intentionally wait until the entire game is FINAL
    before assigning any result. That avoids accidentally
    grading from an incomplete ESPN live-feed period.
    """

    if not completed(
        event
    ):

        return False

    comps = competitors(
        event
    )

    if len(comps) != 2:

        return False

    raw_type = (
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    period = market_period(
        raw_type
    )

    market = base_market(
        raw_type
    )

    scores = {}

    for comp in comps:

        comp_key = str(
            comp.get("id")
            or (
                comp.get("team")
                or {}
            ).get("id")
            or ""
        )

        if not comp_key:

            return False

        value = score_for_market(
            event,
            comp,
            period,
            linescore_cache,
        )

        if value is None:

            print(
                "PERIOD SCORE UNAVAILABLE:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| period:",
                period,
                "| event:",
                event.get("id"),
            )

            return False

        scores[
            comp_key
        ] = value

    line = pick.get(
        "line"
    )

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    if market == "TOTAL":

        if line is None:

            return False

        direction = (
            total_direction(
                pick
            )
        )

        if not direction:

            return False

        actual = sum(
            scores.values()
        )

        if actual == float(
            line
        ):

            result = "PUSH"

        elif direction == "OVER":

            result = (
                "WIN"
                if actual
                > float(line)
                else "LOSS"
            )

        else:

            result = (
                "WIN"
                if actual
                < float(line)
                else "LOSS"
            )

    # --------------------------------------------------------
    # TEAM TOTAL
    # --------------------------------------------------------

    elif market == (
        "TEAM_TOTAL"
    ):

        if line is None:

            return False

        direction = (
            total_direction(
                pick
            )
        )

        if not direction:

            return False

        selected = selected_comp(
            pick,
            event,
        )

        if not selected:

            return False

        selected_key = str(
            selected.get("id")
            or (
                selected.get("team")
                or {}
            ).get("id")
            or ""
        )

        if selected_key not in scores:

            return False

        actual = scores[
            selected_key
        ]

        if actual == float(
            line
        ):

            result = "PUSH"

        elif direction == "OVER":

            result = (
                "WIN"
                if actual
                > float(line)
                else "LOSS"
            )

        else:

            result = (
                "WIN"
                if actual
                < float(line)
                else "LOSS"
            )

    # --------------------------------------------------------
    # SPREAD / MONEYLINE
    # --------------------------------------------------------

    elif market in {
        "SPREAD",
        "MONEYLINE",
    }:

        selected = selected_comp(
            pick,
            event,
        )

        if not selected:

            return False

        selected_key = str(
            selected.get("id")
            or (
                selected.get("team")
                or {}
            ).get("id")
            or ""
        )

        others = [
            comp
            for comp in comps
            if str(
                comp.get("id")
                or (
                    comp.get("team")
                    or {}
                ).get("id")
                or ""
            )
            != selected_key
        ]

        if len(others) != 1:

            return False

        opponent_key = str(
            others[0].get("id")
            or (
                others[0].get("team")
                or {}
            ).get("id")
            or ""
        )

        if (
            selected_key
            not in scores
            or opponent_key
            not in scores
        ):

            return False

        margin = (
            scores[selected_key]
            - scores[
                opponent_key
            ]
        )

        if market == (
            "MONEYLINE"
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

    if period == "FULL_GAME":

        pick["final_score"] = (
            final_score_text(
                event
            )
        )

    else:

        pick["final_score"] = (
            period_score_text(
                event,
                period,
                linescore_cache,
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


# ============================================================
# MAIN
# ============================================================

def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    # Upgrade older saved 1Q / 1H / TT labels
    # before any event matching takes place.
    for pick in picks:

        normalize_existing_pick_market(
            pick
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

    # --------------------------------------------------------
    # FETCH ALL ESPN DATA FIRST
    # --------------------------------------------------------

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
                "Loaded",
                len(events),
                "merged ESPN events for",
                season_year,
                "Week",
                week,
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
                "Week",
                week,
                "|",
                exc,
            )

    graded = 0
    unmatched = 0
    not_final = 0
    review = 0
    preserved = 0
    period_missing = 0

    pending_events = {}

    summary_cache = {}
    linescore_cache = {}

    # --------------------------------------------------------
    # GRADE
    # --------------------------------------------------------

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

        # If ESPN retrieval failed, preserve the previous state.
        if key in failed_weeks:

            preserved += 1

            continue

        bet_type = (
            normalize_bet_type(
                pick.get("bet_type")
            )
        )

        pick["bet_type"] = (
            bet_type
        )

        # Truly unsupported market.
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
                "| type:",
                bet_type,
            )

            continue

        # Team totals cannot be graded without
        # an explicit Over/Under direction.
        if (
            base_market(
                bet_type
            )
            == "TEAM_TOTAL"
            and not total_direction(
                pick
            )
        ):

            clear_grade(
                pick,
                "REVIEW",
            )

            review += 1

            print(
                "REVIEW - team total direction missing:",
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

        # Recalculate every supported pick each run.
        clear_grade(
            pick
        )

        if event is None:

            unmatched += 1

            print(
                "UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "| matchup:",
                pick.get("matchup"),
                "| team:",
                pick.get("team"),
                "| type:",
                pick.get("bet_type"),
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

            pending_events.setdefault(
                event_id,
                {
                    "event": event,
                    "picks": [],
                },
            )

            pending_events[
                event_id
            ]["picks"].append(
                {
                    "picker": (
                        pick.get("picker")
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
                "| event:",
                event.get("id"),
            )

            continue

        success = grade_pick(
            pick,
            event,
            linescore_cache,
        )

        if success:

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

            period = market_period(
                bet_type
            )

            if period != (
                "FULL_GAME"
            ):

                period_missing += 1

                clear_grade(
                    pick,
                    "REVIEW",
                )

                print(
                    "REVIEW - PERIOD DATA UNAVAILABLE:",
                    pick.get("picker"),
                    "|",
                    pick.get("selection"),
                    "|",
                    period,
                    "| event:",
                    event.get("id"),
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

    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print(
        "========== GRADING SUMMARY =========="
    )

    print(
        "Safely graded:",
        graded,
    )

    print(
        "Unmatched supported picks:",
        unmatched,
    )

    print(
        "Matched but not final picks:",
        not_final,
    )

    print(
        "Review/unsupported picks:",
        review,
    )

    print(
        "Period data unavailable:",
        period_missing,
    )

    print(
        "Preserved due to ESPN failure:",
        preserved,
    )

    print(
        "Unique pending tracked games:",
        len(
            pending_events
        ),
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

            event = info[
                "event"
            ]

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
        "========================================"
    )


if __name__ == "__main__":
    grade_open()
