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
# ESPN CONFIG
# ============================================================

ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football"
)

ESPN_SCOREBOARD = f"{ESPN_BASE}/scoreboard"
ESPN_SUMMARY = f"{ESPN_BASE}/summary"

ESPN_CORE = (
    "https://sports.core.api.espn.com/v2/"
    "sports/football/leagues/"
    "college-football"
)


# ============================================================
# SUPPORTED MARKETS
# ============================================================

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


# ============================================================
# TEAM ALIASES
#
# Fail closed whenever something is genuinely ambiguous.
# ============================================================

ALIASES = {
    "alabama": {
        "alabama",
        "bama",
        "ala",
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

    "bowling green": {
        "bowling green",
        "bowling green state",
        "bowling green st",
        "bgsu",
    },

    "delaware": {
        "delaware",
        "del",
    },

    "eastern michigan": {
        "eastern michigan",
        "eastern mich",
        "emu",
    },

    "florida atlantic": {
        "florida atlantic",
        "fau",
    },

    "georgia southern": {
        "georgia southern",
        "ga southern",
    },

    "maryland": {
        "maryland",
        "md",
    },

    "navy": {
        "navy",
    },

    "nebraska": {
        "nebraska",
        "neb",
    },

    "sacramento state": {
        "sacramento state",
        "sacramento st",
        "sac state",
        "sac st",
    },

    "sam houston": {
        "sam houston",
        "sam houston state",
        "sam houston st",
        "shsu",
    },

    "vanderbilt": {
        "vanderbilt",
        "vandy",
    },

    "ball state": {
        "ball state",
        "ball st",
        "ball",
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

    "central michigan": {
        "central michigan",
        "central mich",
        "cmu",
    },

    "clemson": {
        "clem",
        "clemson",
    },

    "coastal carolina": {
        "coastal carolina",
        "coastal",
        "ccu",
    },

    "colorado": {
        "colorado",
        "colo",
    },

    "connecticut": {
        "uconn",
        "connecticut",
        "conn",
    },

    "duke": {
        "duke",
    },

    "east carolina": {
        "ecu",
        "east carolina",
    },

    "eastern illinois": {
        "eiu",
        "eastern illinois",
    },

    "fiu": {
        "fiu",
        "florida international",
        "florida intl",
    },

    "fresno state": {
        "fresno",
        "fresno state",
        "fresno st",
    },

    "georgia tech": {
        "georgia tech",
        "ga tech",
        "gatech",
        "gt",
    },

    "hawaii": {
        "hawaii",
        "hawai'i",
        "haw",
    },

    "houston": {
        "houston",
        "hou",
    },

    "illinois": {
        "illinois",
        "ill",
    },

    "indiana": {
        "indiana",
        "ind",
        "iu",
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

    "lafayette": {
        "lafayette",
        "laf",
    },

    "liu": {
        "liu",
        "long island",
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
        "michigan",
        "mich",
    },

    "michigan state": {
        "michigan state",
        "michigan st",
        "mich state",
        "msu",
    },

    "minnesota": {
        "minnesota",
        "minn",
    },

    "missouri state": {
        "missouri state",
        "missouri st",
        "mo state",
        "most",
    },

    "new mexico": {
        "new mexico",
        "unm",
    },

    "north texas": {
        "north texas",
        "unt",
    },

    "notre dame": {
        "notre dame",
        "nd",
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
        "oklahoma state",
        "oklahoma st",
        "ok state",
        "ok st",
    },

    "oregon": {
        "oregon",
        "ore",
    },

    "oregon state": {
        "oregon state",
        "oregon st",
        "ore st",
    },

    "pittsburgh": {
        "pittsburgh",
        "pitt",
    },

    "rutgers": {
        "rutgers",
        "rutg",
        "ru",
    },

    "south florida": {
        "south florida",
        "usf",
    },

    "stanford": {
        "stanford",
        "stan",
    },

    "temple": {
        "temple",
        "tem",
    },

    "texas": {
        "texas",
        "tex",
    },

    "texas a&m": {
        "texas a&m",
        "texas am",
        "texas a and m",
        "texas a m",
        "tamu",
        "ta&m",
        "a&m",
        "a and m",
    },

    "texas state": {
        "texas state",
        "texas st",
        "tex st",
        "txst",
    },

    "toledo": {
        "toledo",
        "tol",
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
        "utu",
    },

    "utep": {
        "utep",
        "texas el paso",
    },

    "wake forest": {
        "wake forest",
        "wake",
    },

    "washington": {
        "washington",
        "wash",
        "uw",
    },

    "washington state": {
        "washington state",
        "washington st",
        "wash state",
        "wazzu",
        "wsu",
    },

    "west virginia": {
        "west virginia",
        "wvu",
    },

    "western michigan": {
        "western michigan",
        "western mich",
        "wmu",
    },

    "wisconsin": {
        "wisconsin",
        "wisc",
        "wis",
    },
}


# Bare OSU is intentionally not assigned.
AMBIGUOUS_ALIASES = {
    "osu",
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


def safe_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except Exception:
        return None


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
            "Origin":
                "https://www.espn.com",
            "Referer":
                "https://www.espn.com/",
        },
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# OFFICIAL RESULT LOCK
# ============================================================

def is_official_result(pick):
    """
    PAT HILL STANDINGS is final authority.

    Once this field is true, grade.py is not allowed to alter:
      - result
      - status
      - score
      - event
      - profit
      - grading timestamp

    ESPN becomes irrelevant for this row.
    """

    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def official_result_valid(pick):
    return (
        is_official_result(
            pick
        )
        and str(
            pick.get(
                "result"
            )
            or ""
        ).upper()
        in {
            "WIN",
            "LOSS",
            "PUSH",
        }
    )


# ============================================================
# TEAM MATCHING
# ============================================================

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

    for canonical, names in (
        ALIASES.items()
    ):

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


def team_name_candidates(comp):
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

    # Mascot-only team["name"] intentionally excluded.

    return names


def comp_matches_hint(
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
        & team_name_candidates(
            comp
        )
    )


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_bet_type(value):
    raw = str(
        value or ""
    ).upper().strip()

    aliases = {
        "1Q_SPREAD":
            "FIRST_QUARTER_SPREAD",

        "1Q_TOTAL":
            "FIRST_QUARTER_TOTAL",

        "1Q_MONEYLINE":
            "FIRST_QUARTER_MONEYLINE",

        "1Q_TEAM_TOTAL":
            "FIRST_QUARTER_TEAM_TOTAL",

        "1H_SPREAD":
            "FIRST_HALF_SPREAD",

        "1H_TOTAL":
            "FIRST_HALF_TOTAL",

        "1H_MONEYLINE":
            "FIRST_HALF_MONEYLINE",

        "1H_TEAM_TOTAL":
            "FIRST_HALF_TEAM_TOTAL",
    }

    return aliases.get(
        raw,
        raw,
    )


def normalize_existing_pick_market(pick):
    """
    Upgrade older extracted market labels.

    IMPORTANT:
    Officially reconciled rows are left untouched.
    """

    if is_official_result(
        pick
    ):
        return

    text = str(
        pick.get(
            "selection"
        )
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
            r"\b(?:"
            r"1q|"
            r"1st\s*q|"
            r"first\s*quarter"
            r")\b",
            text,
            flags=re.I,
        )
    )

    first_half = bool(
        re.search(
            r"\b(?:"
            r"1h|"
            r"1st\s*h|"
            r"first\s*half"
            r")\b",
            text,
            flags=re.I,
        )
    )

    team_total = bool(
        re.search(
            r"\b(?:"
            r"tt|"
            r"team\s*total"
            r")\b",
            text,
            flags=re.I,
        )
    )

    if team_total:

        if first_quarter:
            pick[
                "bet_type"
            ] = (
                "FIRST_QUARTER_TEAM_TOTAL"
            )

        elif first_half:
            pick[
                "bet_type"
            ] = (
                "FIRST_HALF_TEAM_TOTAL"
            )

        else:
            pick[
                "bet_type"
            ] = "TEAM_TOTAL"

        compact = re.search(
            r"\b(?:tt|team\s*total)"
            r"\s*(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if compact:

            pick["side"] = (
                "OVER"
                if (
                    compact
                    .group(1)
                    .lower()
                    == "o"
                )
                else "UNDER"
            )

            pick["line"] = float(
                compact.group(2)
            )

        verbose = re.search(
            r"\b(?:tt|team\s*total)"
            r"\s*(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if verbose:

            pick["side"] = (
                verbose
                .group(1)
                .upper()
            )

            pick["line"] = float(
                verbose.group(2)
            )

        return


    if (
        first_quarter
        or first_half
    ):

        prefix = (
            "FIRST_QUARTER"
            if first_quarter
            else "FIRST_HALF"
        )

        if re.search(
            r"\b(?:over|under)\b",
            text,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:"
            r"ml|moneyline"
            r")\b",
            text,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*"
            r"\d+(?:\.\d+)?",
            text,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_SPREAD"
            )

        return


    pick["bet_type"] = (
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )


def market_period(
    bet_type
):
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


def base_market(
    bet_type
):
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
        pick.get(
            "side"
        )
        or ""
    ).upper().strip()

    if side in {
        "OVER",
        "UNDER",
    }:
        return side

    selection = str(
        pick.get(
            "selection"
        )
        or ""
    )

    if re.search(
        r"\bover\b",
        selection,
        flags=re.I,
    ):
        return "OVER"

    if re.search(
        r"\bunder\b",
        selection,
        flags=re.I,
    ):
        return "UNDER"

    return None


def pick_posted_datetime(pick):
    return parse_datetime(
        pick.get(
            "posted_at"
        )
    )


def season_year_for_pick(pick):
    if pick.get(
        "season"
    ):
        return int(
            pick["season"]
        )

    posted = (
        pick_posted_datetime(
            pick
        )
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
    2026 verified Pick Em week structure.

    Week 1:
      Aug 22 - Sep 7

    Week 2:
      Sep 8 - Sep 13

    Week 3 onward:
      Monday-Sunday
    """

    season_year = int(
        season_year
    )

    week = int(
        week
    )

    if season_year == 2026:

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

        week3 = date(
            2026,
            9,
            14,
        )

        start = (
            week3
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

    # --------------------------------------------------------
    # FUTURE-SEASON SAFE FALLBACK
    #
    # We would rather leave a pick OPEN than misgrade it.
    # --------------------------------------------------------

    raise RuntimeError(
        "Season calendar has not "
        f"been verified for {season_year}. "
        "Existing grades preserved."
    )


# ============================================================
# ESPN EVENT HELPERS
# ============================================================

def competitions(event):
    return (
        event.get(
            "competitions"
        )
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
    status = (
        event.get(
            "status"
        )
        or {}
    )

    if status:
        return status

    comps = competitions(
        event
    )

    if comps:
        return (
            comps[0].get(
                "status"
            )
            or {}
        )

    return {}


def completed(event):
    return bool(
        (
            event_status(
                event
            )
            .get("type")
            or {}
        ).get(
            "completed"
        )
    )


def score_value(comp):
    value = comp.get(
        "score"
    )

    if value is None:
        return None

    if isinstance(
        value,
        (
            str,
            int,
            float,
        ),
    ):

        try:
            return float(
                value
            )

        except Exception:
            return None

    if isinstance(
        value,
        dict,
    ):

        for key in (
            "value",
            "displayValue",
            "score",
        ):

            candidate = value.get(
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
                    return float(
                        candidate
                    )

            except Exception:
                continue

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

        score = score_value(
            comp
        )

        if score is None:
            display = "?"

        elif float(
            score
        ).is_integer():
            display = str(
                int(score)
            )

        else:
            display = str(
                score
            )

        output.append(
            f"{label} {display}"
        )

    return " - ".join(
        output
    )


def event_team_sets(event):
    return [
        team_name_candidates(
            comp
        )
        for comp in competitors(
            event
        )
    ]


def event_contains_team(
    event,
    hint,
):
    if not hint:
        return False

    if is_ambiguous_hint(
        hint
    ):
        return False

    target = alias_group(
        hint
    )

    matches = [
        names
        for names in event_team_sets(
            event
        )
        if target & names
    ]

    return len(
        matches
    ) == 1


def event_contains_pair(
    event,
    team_a,
    team_b,
):
    if (
        not team_a
        or not team_b
    ):
        return False

    if (
        is_ambiguous_hint(
            team_a
        )
        or is_ambiguous_hint(
            team_b
        )
    ):
        return False

    sets = event_team_sets(
        event
    )

    if len(sets) != 2:
        return False

    a = alias_group(
        team_a
    )

    b = alias_group(
        team_b
    )

    return (
        (
            bool(
                a & sets[0]
            )
            and bool(
                b & sets[1]
            )
        )
        or
        (
            bool(
                a & sets[1]
            )
            and bool(
                b & sets[0]
            )
        )
    )


# ============================================================
# MATCHUP PARSING
# ============================================================

def split_matchup(value):
    text = clean_text(
        value
    )

    if not text:
        return []

    pieces = re.split(
        r"\s*@\s*"
        r"|\s*/\s*"
        r"|\s+vs\.?\s+"
        r"|\s+v\.?\s+"
        r"|\s+at\s+",
        text,
        flags=re.I,
    )

    return [
        piece.strip()
        for piece in pieces
        if piece.strip()
    ][:2]


def selection_matchup_hints(
    pick
):
    selection = clean_text(
        pick.get(
            "selection"
        )
    )

    pieces = split_matchup(
        selection
    )

    if len(pieces) == 2:

        # Strip wager information from second team.
        pieces[1] = re.split(
            r"\b(?:"
            r"over|under|"
            r"1q|1h|"
            r"tt|team total"
            r")\b"
            r"|[+-]\s*\d",
            pieces[1],
            maxsplit=1,
            flags=re.I,
        )[0].strip()

        if (
            pieces[0]
            and pieces[1]
        ):
            return pieces

    return []


def structured_matchup_hints(
    pick
):
    matchup = split_matchup(
        pick.get(
            "matchup"
        )
    )

    if len(matchup) == 2:
        return matchup

    team = clean_text(
        pick.get("team")
    )

    opponent = clean_text(
        pick.get(
            "opponent"
        )
    )

    if team and opponent:
        return [
            team,
            opponent,
        ]

    return []


# ============================================================
# SELECTED TEAM
# ============================================================

def side_identity(pick):
    """
    Determine selected team for spreads, moneylines
    and team totals.
    """

    market = base_market(
        pick.get(
            "bet_type"
        )
    )

    team = clean_text(
        pick.get(
            "team"
        )
    )

    if team:
        return team

    side = clean_text(
        pick.get(
            "side"
        )
    )

    if (
        side
        and side.upper()
        not in {
            "OVER",
            "UNDER",
        }
    ):
        return side

    selection = clean_text(
        pick.get(
            "selection"
        )
    )

    if market == "TEAM_TOTAL":

        match = re.search(
            r"^(.+?)\s+"
            r"(?:tt|team\s+total)\b",
            selection,
            flags=re.I,
        )

        if match:
            return (
                match
                .group(1)
                .strip()
            )

    if market in {
        "SPREAD",
        "MONEYLINE",
    }:

        text = re.sub(
            r"\b(?:"
            r"1q|1h|"
            r"first quarter|"
            r"first half"
            r")\b",
            "",
            selection,
            flags=re.I,
        ).strip()

        spread = re.search(
            r"^(.+?)\s+"
            r"[+-]\s*"
            r"\d+(?:\.\d+)?"
            r"\s*$",
            text,
            flags=re.I,
        )

        if spread:

            value = (
                spread
                .group(1)
                .strip()
            )

            # If source contains matchup, keep only
            # selected team portion.
            pieces = split_matchup(
                value
            )

            if pieces:
                return pieces[-1]

            return value

    return None


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
        if comp_matches_hint(
            comp,
            hint,
        )
    ]

    if len(matches) == 1:
        return matches[0]

    return None


# ============================================================
# ESPN SCOREBOARD FETCH
# ============================================================

def fetch_day_events(day):
    """
    Fetch the complete ESPN college-football slate for one day.

    IMPORTANT:
    ESPN's unfiltered college-football scoreboard can return only a
    partial/default slate. To avoid missing perfectly valid Pick Em
    games, request three views and merge them by event ID:

      - default scoreboard
      - FBS group 80
      - FCS group 81

    This keeps the resolver conservative. We are expanding the event
    pool, not loosening team matching.
    """

    datestring = (
        day.strftime(
            "%Y%m%d"
        )
    )

    all_events = []

    # None = ESPN default view.
    # 80   = FBS.
    # 81   = FCS.
    for group in (
        None,
        80,
        81,
    ):

        params = {
            "dates":
                datestring,

            "limit":
                1000,
        }

        if group is not None:
            params[
                "groups"
            ] = group

        try:

            payload = request_json(
                ESPN_SCOREBOARD,
                params,
            )

            events = (
                payload.get(
                    "events"
                )
                or []
            )

            all_events.extend(
                events
            )

        except Exception as exc:

            print(
                "ESPN SCOREBOARD VIEW FAILED:",
                datestring,
                "| group:",
                group if group is not None else "default",
                "|",
                exc,
            )

    # De-duplicate the three scoreboard views immediately.
    return merge_events(
        all_events
    )


def fetch_date_range_events(
    start,
    end,
):
    """
    Fetch ESPN's date-range college-football slate for the exact Pick Em
    calendar window. This avoids assuming Barstool Pick Em week numbers
    equal ESPN's own college-football week numbers.

    We request default, FBS group 80, and FCS group 81, then merge by
    ESPN event ID. Matching rules remain unchanged and conservative.
    """

    datestring = (
        f"{start.strftime('%Y%m%d')}-"
        f"{end.strftime('%Y%m%d')}"
    )

    all_events = []

    for group in (
        None,
        80,
        81,
    ):

        params = {
            "dates": datestring,
            "limit": 1000,
        }

        if group is not None:
            params["groups"] = group

        try:
            payload = request_json(
                ESPN_SCOREBOARD,
                params,
            )

            events = (
                payload.get("events")
                or []
            )

            print(
                "ESPN DATE-RANGE VIEW:",
                datestring,
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "| events:",
                len(events),
            )

            all_events.extend(events)

        except Exception as exc:
            print(
                "ESPN DATE-RANGE VIEW FAILED:",
                datestring,
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "|",
                exc,
            )

    return merge_events(all_events)


def event_quality(event):
    score = 0

    if completed(
        event
    ):
        score += 100

    if len(
        competitors(
            event
        )
    ) == 2:
        score += 20

    if all(
        score_value(comp)
        is not None
        for comp in competitors(
            event
        )
    ):
        score += 20

    if competitions(
        event
    ):
        score += 5

    return score


def merge_events(events):
    by_id = {}

    for event in events:

        event_id = str(
            event.get(
                "id"
            )
            or ""
        )

        if not event_id:
            continue

        old = by_id.get(
            event_id
        )

        if (
            old is None
            or event_quality(
                event
            )
            > event_quality(
                old
            )
        ):
            by_id[
                event_id
            ] = event

    return list(
        by_id.values()
    )


def fetch_week_events(
    season_year,
    week,
):
    """
    Fetch ESPN's week-based college-football schedule.

    This supplements the date-by-date scoreboard because ESPN can omit
    games from the daily/default views.

    We request:
      - default
      - FBS group 80
      - FCS group 81

    Then merge everything by ESPN event ID.

    IMPORTANT:
    This expands the candidate event pool only.
    It does NOT loosen event matching.
    """

    all_events = []

    for group in (
        None,
        80,
        81,
    ):

        params = {
            "dates":
                str(
                    season_year
                ),

            "seasontype":
                2,

            "week":
                int(
                    week
                ),

            "limit":
                1000,
        }

        if group is not None:
            params[
                "groups"
            ] = group

        try:

            payload = request_json(
                ESPN_SCOREBOARD,
                params,
            )

            events = (
                payload.get(
                    "events"
                )
                or []
            )

            print(
                "ESPN WEEK VIEW:",
                season_year,
                "Week",
                week,
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "| events:",
                len(
                    events
                ),
            )

            all_events.extend(
                events
            )

        except Exception as exc:

            print(
                "ESPN WEEK VIEW FAILED:",
                season_year,
                "Week",
                week,
                "| group:",
                (
                    group
                    if group is not None
                    else "default"
                ),
                "|",
                exc,
            )

    return merge_events(
        all_events
    )


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    """
    Build the most complete ESPN event slate possible for a Pick Em week.

    Sources merged:

      1. Every calendar day inside our verified Pick Em week window
         - ESPN default scoreboard
         - FBS group 80
         - FCS group 81

      2. ESPN date-range scoreboard for that exact same calendar window
         - ESPN default scoreboard
         - FBS group 80
         - FCS group 81

    We intentionally do NOT assume Barstool Pick Em week numbers match
    ESPN's own week numbers. Events are de-duplicated by ESPN event ID.

    Matching remains conservative. This function only expands the pool
    of legitimate ESPN events available to the existing resolver.
    """

    start, end = week_window(
        season_year,
        week,
    )

    all_events = []

    current = start

    while current <= end:
        try:
            events = fetch_day_events(
                current
            )
            all_events.extend(events)

        except Exception as exc:
            print(
                "ESPN DAY FETCH FAILED:",
                current,
                "|",
                exc,
            )

        current += timedelta(days=1)

    daily_merged = merge_events(
        all_events
    )

    print(
        "Date-based slate:",
        len(daily_merged),
        "unique events",
    )

    try:
        range_events = fetch_date_range_events(
            start,
            end,
        )

        print(
            "Date-range slate:",
            len(range_events),
            "unique events",
        )

        all_events.extend(range_events)

    except Exception as exc:
        print(
            "ESPN DATE-RANGE FETCH FAILED:",
            season_year,
            "Week",
            week,
            "|",
            exc,
        )

    merged = merge_events(
        all_events
    )

    if not merged:
        raise RuntimeError(
            "No ESPN events retrieved "
            f"for {season_year} Week {week}."
        )

    print(
        "Combined complete slate:",
        len(merged),
        "unique events",
    )

    return merged


# ============================================================
# EVENT RESOLUTION
# ============================================================

def resolve_event(
    pick,
    events,
):
    """
    Conservative matching priority:

    1. Explicit matchup
    2. Matchup encoded in selection
    3. team + opponent
    4. selected team only

    A single-team match is accepted only if exactly one event
    in the entire week matches.
    """

    # --------------------------------------------------------
    # 1. Dedicated matchup field
    # --------------------------------------------------------

    hints = split_matchup(
        pick.get(
            "matchup"
        )
    )

    if len(hints) == 2:

        matches = [
            event
            for event in events
            if event_contains_pair(
                event,
                hints[0],
                hints[1],
            )
        ]

        if len(matches) == 1:
            return matches[0]

    # --------------------------------------------------------
    # 2. Selection matchup
    # --------------------------------------------------------

    hints = (
        selection_matchup_hints(
            pick
        )
    )

    if len(hints) == 2:

        matches = [
            event
            for event in events
            if event_contains_pair(
                event,
                hints[0],
                hints[1],
            )
        ]

        if len(matches) == 1:
            return matches[0]

    # --------------------------------------------------------
    # 3. Structured team/opponent
    # --------------------------------------------------------

    team = clean_text(
        pick.get("team")
    )

    opponent = clean_text(
        pick.get(
            "opponent"
        )
    )

    if team and opponent:

        matches = [
            event
            for event in events
            if event_contains_pair(
                event,
                team,
                opponent,
            )
        ]

        if len(matches) == 1:
            return matches[0]

    # --------------------------------------------------------
    # 4. Selected team
    # --------------------------------------------------------

    selected = side_identity(
        pick
    )

    if (
        selected
        and not is_ambiguous_hint(
            selected
        )
    ):

        matches = [
            event
            for event in events
            if event_contains_team(
                event,
                selected,
            )
        ]

        if len(matches) == 1:
            return matches[0]

    return None


def find_event_by_id(
    events,
    stored_event_id,
):
    """
    Strict pregame event lock.

    Once schedule_enrich.py has attached an ESPN event_id to a pick,
    grading must use that exact event.

    Never silently rematch a locked pick to another game.
    """

    if not stored_event_id:
        return None

    target = str(
        stored_event_id
    )

    matches = [
        event
        for event in events
        if str(
            event.get(
                "id"
            )
            or ""
        )
        ==
        target
    ]

    if len(matches) == 1:
        return matches[0]

    return None


# ============================================================
# ESPN SUMMARY
# ============================================================

def fetch_summary(
    event_id,
):
    return request_json(
        ESPN_SUMMARY,
        {
            "event":
                str(
                    event_id
                )
        },
    )


def summary_competitors(
    summary
):
    header = (
        summary.get(
            "header"
        )
        or {}
    )

    competitions_list = (
        header.get(
            "competitions"
        )
        or []
    )

    if not competitions_list:
        return []

    return (
        competitions_list[0]
        .get(
            "competitors"
        )
        or []
    )


def hydrate_event_from_summary(
    event,
    summary,
):
    """
    Use ESPN summary data to strengthen the exact event object.

    This does not change which event was selected.
    """

    summary_comps = (
        summary_competitors(
            summary
        )
    )

    if not summary_comps:
        return event

    hydrated = dict(
        event
    )

    competitions_list = [
        dict(comp)
        for comp in competitions(
            event
        )
    ]

    if not competitions_list:
        competitions_list = [
            {}
        ]

    competition = dict(
        competitions_list[0]
    )

    competition[
        "competitors"
    ] = summary_comps

    header = (
        summary.get(
            "header"
        )
        or {}
    )

    header_competitions = (
        header.get(
            "competitions"
        )
        or []
    )

    if header_competitions:

        summary_competition = (
            header_competitions[0]
            or {}
        )

        if summary_competition.get(
            "status"
        ):

            competition[
                "status"
            ] = (
                summary_competition[
                    "status"
                ]
            )

            hydrated[
                "status"
            ] = (
                summary_competition[
                    "status"
                ]
            )

    competitions_list[0] = (
        competition
    )

    hydrated[
        "competitions"
    ] = (
        competitions_list
    )

    return hydrated


# ============================================================
# PERIOD LINE SCORES
# ============================================================

def core_linescore_url(
    event_id,
    team_id,
):
    return (
        f"{ESPN_CORE}/"
        f"events/{event_id}/"
        f"competitions/{event_id}/"
        f"competitors/{team_id}/"
        "linescores"
    )


def extract_period_value(
    item
):
    """
    ESPN core responses vary slightly.

    Return:
      (period_number, score)
    """

    period = item.get(
        "period"
    )

    if isinstance(
        period,
        dict,
    ):

        period = (
            period.get(
                "number"
            )
            or
            period.get(
                "value"
            )
        )

    if period is None:

        period = (
            item.get(
                "periodNumber"
            )
            or
            item.get(
                "number"
            )
        )

    value = item.get(
        "value"
    )

    if value is None:

        value = item.get(
            "displayValue"
        )

    if value is None:

        value = item.get(
            "score"
        )

    if isinstance(
        value,
        dict,
    ):

        value = (
            value.get(
                "value"
            )
            or
            value.get(
                "displayValue"
            )
        )

    try:

        period = int(
            period
        )

        value = float(
            value
        )

        return (
            period,
            value,
        )

    except Exception:

        return (
            None,
            None,
        )


def fetch_team_linescores(
    event_id,
    team_id,
):
    payload = request_json(
        core_linescore_url(
            event_id,
            team_id,
        )
    )

    items = (
        payload.get(
            "items"
        )
        or []
    )

    output = {}

    for item in items:

        period, value = (
            extract_period_value(
                item
            )
        )

        if (
            period is not None
            and value is not None
        ):

            output[
                period
            ] = value

    return output


def event_period_scores(
    event,
    period,
):
    """
    Return period score by ESPN team ID.

    FIRST_QUARTER:
      Q1

    FIRST_HALF:
      Q1 + Q2

    Full-game markets do not call this.
    """

    event_value = str(
        event.get(
            "id"
        )
        or ""
    )

    if not event_value:
        return None

    output = {}

    for comp in competitors(
        event
    ):

        team = (
            comp.get(
                "team"
            )
            or {}
        )

        team_id = str(
            team.get(
                "id"
            )
            or ""
        )

        if not team_id:
            return None

        try:

            lines = (
                fetch_team_linescores(
                    event_value,
                    team_id,
                )
            )

        except Exception as exc:

            print(
                "PERIOD SCORE FETCH FAILED:",
                event_value,
                "| team:",
                team_id,
                "|",
                exc,
            )

            return None

        if period == (
            "FIRST_QUARTER"
        ):

            if 1 not in lines:
                return None

            output[
                team_id
            ] = lines[1]

        elif period == (
            "FIRST_HALF"
        ):

            if (
                1 not in lines
                or 2 not in lines
            ):
                return None

            output[
                team_id
            ] = (
                lines[1]
                +
                lines[2]
            )

        else:
            return None

    if len(output) != 2:
        return None

    return output


# ============================================================
# GRADING MATH
# ============================================================

def compare_total(
    score,
    line,
    direction,
):
    if (
        score is None
        or line is None
        or direction
        not in {
            "OVER",
            "UNDER",
        }
    ):
        return None

    if score == line:
        return "PUSH"

    if direction == "OVER":

        return (
            "WIN"
            if score > line
            else "LOSS"
        )

    return (
        "WIN"
        if score < line
        else "LOSS"
    )


def compare_spread(
    selected_score,
    opponent_score,
    line,
):
    if (
        selected_score is None
        or opponent_score is None
        or line is None
    ):
        return None

    adjusted = (
        selected_score
        +
        line
    )

    if adjusted == (
        opponent_score
    ):
        return "PUSH"

    return (
        "WIN"
        if adjusted
        >
        opponent_score
        else "LOSS"
    )


def compare_moneyline(
    selected_score,
    opponent_score,
):
    if (
        selected_score is None
        or opponent_score is None
    ):
        return None

    if (
        selected_score
        ==
        opponent_score
    ):
        return "PUSH"

    return (
        "WIN"
        if selected_score
        >
        opponent_score
        else "LOSS"
    )


def full_game_scores(
    event
):
    comps = competitors(
        event
    )

    if len(comps) != 2:
        return None

    output = {}

    for comp in comps:

        team = (
            comp.get(
                "team"
            )
            or {}
        )

        team_id = str(
            team.get(
                "id"
            )
            or ""
        )

        score = score_value(
            comp
        )

        if (
            not team_id
            or score is None
        ):
            return None

        output[
            team_id
        ] = score

    if len(output) != 2:
        return None

    return output


def opponent_comp(
    selected,
    event,
):
    if selected is None:
        return None

    selected_team = (
        selected.get(
            "team"
        )
        or {}
    )

    selected_id = str(
        selected_team.get(
            "id"
        )
        or ""
    )

    others = []

    for comp in competitors(
        event
    ):

        team = (
            comp.get(
                "team"
            )
            or {}
        )

        team_id = str(
            team.get(
                "id"
            )
            or ""
        )

        if (
            team_id
            and team_id
            != selected_id
        ):

            others.append(
                comp
            )

    if len(others) == 1:
        return others[0]

    return None


def team_id_from_comp(
    comp
):
    if not comp:
        return None

    team = (
        comp.get(
            "team"
        )
        or {}
    )

    value = team.get(
        "id"
    )

    if value is None:
        return None

    return str(
        value
    )


def grade_total_market(
    pick,
    scores,
):
    if len(scores) != 2:
        return None

    total = sum(
        scores.values()
    )

    return compare_total(
        total,
        safe_float(
            pick.get(
                "line"
            )
        ),
        total_direction(
            pick
        ),
    )


def grade_team_total_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    if selected is None:
        return None

    selected_id = (
        team_id_from_comp(
            selected
        )
    )

    if (
        not selected_id
        or selected_id
        not in scores
    ):
        return None

    return compare_total(
        scores[
            selected_id
        ],
        safe_float(
            pick.get(
                "line"
            )
        ),
        total_direction(
            pick
        ),
    )


def grade_spread_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    opponent = opponent_comp(
        selected,
        event,
    )

    selected_id = (
        team_id_from_comp(
            selected
        )
    )

    opponent_id = (
        team_id_from_comp(
            opponent
        )
    )

    if (
        not selected_id
        or not opponent_id
        or selected_id
        not in scores
        or opponent_id
        not in scores
    ):
        return None

    return compare_spread(
        scores[
            selected_id
        ],
        scores[
            opponent_id
        ],
        safe_float(
            pick.get(
                "line"
            )
        ),
    )


def grade_moneyline_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    opponent = opponent_comp(
        selected,
        event,
    )

    selected_id = (
        team_id_from_comp(
            selected
        )
    )

    opponent_id = (
        team_id_from_comp(
            opponent
        )
    )

    if (
        not selected_id
        or not opponent_id
        or selected_id
        not in scores
        or opponent_id
        not in scores
    ):
        return None

    return compare_moneyline(
        scores[
            selected_id
        ],
        scores[
            opponent_id
        ],
    )


def grade_market(
    pick,
    event,
    period_scores=None,
):
    bet_type = (
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    market = base_market(
        bet_type
    )

    period = market_period(
        bet_type
    )

    if period == "FULL_GAME":

        scores = full_game_scores(
            event
        )

    else:

        scores = period_scores

    if not scores:
        return None

    if market == "TOTAL":

        return grade_total_market(
            pick,
            scores,
        )

    if market == "TEAM_TOTAL":

        return (
            grade_team_total_market(
                pick,
                event,
                scores,
            )
        )

    if market == "SPREAD":

        return grade_spread_market(
            pick,
            event,
            scores,
        )

    if market == "MONEYLINE":

        return (
            grade_moneyline_market(
                pick,
                event,
                scores,
            )
        )

    return None


# ============================================================
# PROFIT / STATUS HELPERS
# ============================================================

def pick_units(
    pick
):
    value = safe_float(
        pick.get(
            "units"
        )
    )

    if value is None:
        return 1.0

    return value


def pick_odds(
    pick
):
    value = pick.get(
        "odds"
    )

    if value in (
        None,
        "",
    ):
        return -110

    try:
        return int(
            value
        )

    except Exception:
        return -110


def calculate_profit(
    pick,
    result,
):
    try:

        return american_profit(
            pick_odds(
                pick
            ),
            pick_units(
                pick
            ),
            result,
        )

    except Exception:

        if result == "LOSS":
            return (
                -1
                *
                pick_units(
                    pick
                )
            )

        if result == "PUSH":
            return 0.0

        odds = pick_odds(
            pick
        )

        units = pick_units(
            pick
        )

        if odds > 0:

            return (
                units
                *
                odds
                /
                100.0
            )

        return (
            units
            *
            100.0
            /
            abs(
                odds
            )
        )


def clear_grade(
    pick
):
    """
    Clear ESPN-derived grading fields while PRESERVING event_id.

    event_id is intentionally retained because schedule_enrich.py may
    have locked the exact ESPN game before kickoff.
    """

    for key in (
        "result",
        "status",
        "final_score",
        "profit",
        "graded_at",
        "period_score",
        "grade_source",
        "grading_error",
    ):

        pick.pop(
            key,
            None,
        )


def mark_open(
    pick,
):
    pick[
        "status"
    ] = "OPEN"

    pick[
        "result"
    ] = None


def mark_review(
    pick,
    reason,
):
    pick[
        "status"
    ] = "OPEN"

    pick[
        "result"
    ] = None

    pick[
        "grading_error"
    ] = reason


def apply_grade(
    pick,
    event,
    result,
):
    pick[
        "result"
    ] = result

    pick[
        "status"
    ] = "FINAL"

    pick[
        "event_id"
    ] = str(
        event.get(
            "id"
        )
        or ""
    )

    pick[
        "final_score"
    ] = final_score_text(
        event
    )

    pick[
        "profit"
    ] = calculate_profit(
        pick,
        result,
    )

    pick[
        "graded_at"
    ] = now_iso()

    pick[
        "grade_source"
    ] = "ESPN"


# ============================================================
# AUDIT DISPLAY
# ============================================================

def picker_name(
    pick
):
    return (
        pick.get(
            "picker"
        )
        or
        "Unknown"
    )


def pick_label(
    pick
):
    return (
        pick.get(
            "selection"
        )
        or
        pick.get(
            "raw_text"
        )
        or
        "Unknown selection"
    )


def week_number(
    pick
):
    try:

        return int(
            pick.get(
                "week"
            )
            or 1
        )

    except Exception:

        return 1


def print_week_audit(
    picks,
    week,
):
    print()
    print(
        "=" * 70
    )
    print(
        f"WEEK {week} AUDIT"
    )
    print(
        "=" * 70
    )

    week_picks = [
        pick
        for pick in picks
        if week_number(
            pick
        )
        ==
        int(
            week
        )
    ]

    if not week_picks:

        print(
            "No stored picks."
        )
        return

    for picker in (
        "Rico Bosco",
        "Big Cat",
        "Stool Presidente",
    ):

        picker_picks = [
            pick
            for pick in week_picks
            if picker_name(
                pick
            )
            ==
            picker
        ]

        if not picker_picks:
            continue

        wins = sum(
            1
            for pick in picker_picks
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            ==
            "WIN"
        )

        losses = sum(
            1
            for pick in picker_picks
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            ==
            "LOSS"
        )

        pushes = sum(
            1
            for pick in picker_picks
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            ==
            "PUSH"
        )

        open_count = sum(
            1
            for pick in picker_picks
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            not in {
                "WIN",
                "LOSS",
                "PUSH",
            }
        )

        official = sum(
            1
            for pick in picker_picks
            if is_official_result(
                pick
            )
        )

        print()
        print(
            picker
        )

        print(
            "  Record:",
            f"{wins}-{losses}-{pushes}",
            "| Open:",
            open_count,
            "| Official:",
            official,
            "| Rows:",
            len(
                picker_picks
            ),
        )

        for pick in picker_picks:

            result = (
                str(
                    pick.get(
                        "result"
                    )
                    or "OPEN"
                ).upper()
            )

            event_value = (
                pick.get(
                    "event_id"
                )
                or "-"
            )

            official_label = (
                "OFFICIAL"
                if is_official_result(
                    pick
                )
                else "ESPN"
            )

            print(
                "   ",
                result,
                "|",
                official_label,
                "|",
                pick_label(
                    pick
                ),
                "| event:",
                event_value,
            )


# ============================================================
# MAIN GRADER
# ============================================================

def grade_open():
    picks = load_json(
        PICKS_FILE,
        []
    )

    if not isinstance(
        picks,
        list,
    ):

        raise RuntimeError(
            "picks.json must "
            "contain a list."
        )

    print()
    print(
        "=" * 70
    )
    print(
        "GRADER PRECHECK"
    )
    print(
        "=" * 70
    )

    print(
        "Total stored wagers:",
        len(
            picks
        ),
    )

    official_locked = [
        pick
        for pick in picks
        if is_official_result(
            pick
        )
    ]

    provisional = [
        pick
        for pick in picks
        if not is_official_result(
            pick
        )
    ]

    print(
        "Official result rows:",
        len(
            official_locked
        ),
    )

    print(
        "Provisional ESPN rows:",
        len(
            provisional
        ),
    )

    # --------------------------------------------------------
    # Normalize only provisional rows.
    # --------------------------------------------------------

    for pick in provisional:

        normalize_existing_pick_market(
            pick
        )

    # --------------------------------------------------------
    # Group provisional picks by season/week.
    # --------------------------------------------------------

    groups = {}

    for pick in provisional:

        sport = str(
            pick.get(
                "sport"
            )
            or ""
        ).upper()

        if sport not in {
            "CFB",
            "NCAAF",
        }:

            continue

        season_year = (
            season_year_for_pick(
                pick
            )
        )

        week = week_number(
            pick
        )

        key = (
            season_year,
            week,
        )

        groups.setdefault(
            key,
            []
        ).append(
            pick
        )

    # --------------------------------------------------------
    # Load complete ESPN slate once per required week.
    # --------------------------------------------------------

    event_cache = {}

    failed_weeks = set()

    for (
        season_year,
        week,
    ), week_picks in sorted(
        groups.items()
    ):

        try:

            events = (
                build_complete_week_slate(
                    week_picks,
                    season_year,
                    week,
                )
            )

            event_cache[
                (
                    season_year,
                    week,
                )
            ] = events

            print(
                "Loaded ESPN schedule:",
                season_year,
                "Week",
                week,
                "| events:",
                len(
                    events
                ),
            )

        except Exception as exc:

            failed_weeks.add(
                (
                    season_year,
                    week,
                )
            )

            print(
                "ESPN WEEK LOAD FAILED:",
                season_year,
                "Week",
                week,
                "|",
                exc,
            )

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    official_preserved = 0
    safely_graded = 0
    unmatched = 0
    matched_not_final = 0
    review_unsupported = 0
    period_unavailable = 0
    preserved = 0

    pending_event_ids = set()

    # --------------------------------------------------------
    # Grade
    # --------------------------------------------------------

    for pick in picks:

        # ----------------------------------------------------
        # PAT HILL rows are immutable.
        # ----------------------------------------------------

        if is_official_result(
            pick
        ):

            official_preserved += 1

            continue

        sport = str(
            pick.get(
                "sport"
            )
            or ""
        ).upper()

        if sport not in {
            "CFB",
            "NCAAF",
        }:

            continue

        bet_type = (
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
        )

        pick[
            "bet_type"
        ] = bet_type

        if bet_type not in SUPPORTED:

            clear_grade(
                pick
            )

            mark_review(
                pick,
                (
                    "Unsupported "
                    f"bet type: {bet_type}"
                ),
            )

            review_unsupported += 1

            print(
                "UNSUPPORTED MARKET:",
                picker_name(
                    pick
                ),
                "|",
                pick_label(
                    pick
                ),
                "|",
                bet_type,
            )

            continue

        season_year = (
            season_year_for_pick(
                pick
            )
        )

        week = week_number(
            pick
        )

        cache_key = (
            season_year,
            week,
        )

        # ----------------------------------------------------
        # If ESPN failed for this entire week, preserve the
        # existing row exactly as-is.
        # ----------------------------------------------------

        if (
            cache_key
            in failed_weeks
        ):

            preserved += 1

            print(
                "ESPN FAILURE - "
                "PRESERVING PICK:",
                picker_name(
                    pick
                ),
                "|",
                pick_label(
                    pick
                ),
            )

            continue

        events = event_cache.get(
            cache_key,
            []
        )

        stored_event_id = (
            pick.get(
                "event_id"
            )
        )

        # ----------------------------------------------------
        # PRE-GAME EVENT LOCK
        #
        # If schedule_enrich.py already found the event,
        # use that exact event forever.
        #
        # Never silently rematch a locked pick.
        # ----------------------------------------------------

        if stored_event_id:

            event = find_event_by_id(
                events,
                stored_event_id,
            )

            if event is None:

                preserved += 1

                print(
                    "PRE-GAME EVENT NOT FOUND "
                    "- PRESERVING PICK:",
                    picker_name(
                        pick
                    ),
                    "|",
                    pick_label(
                        pick
                    ),
                    "| event:",
                    stored_event_id,
                )

                continue

            print(
                "USING PRE-GAME EVENT:",
                picker_name(
                    pick
                ),
                "|",
                pick_label(
                    pick
                ),
                "| event:",
                stored_event_id,
            )

        else:

            # ------------------------------------------------
            # Conservative fallback for legacy / unmatched
            # rows.
            # ------------------------------------------------

            event = resolve_event(
                pick,
                events,
            )

            if event is None:

                clear_grade(
                    pick
                )

                mark_open(
                    pick
                )

                unmatched += 1

                print(
                    "NO SAFE ESPN MATCH:",
                    picker_name(
                        pick
                    ),
                    "|",
                    pick_label(
                        pick
                    ),
                )

                continue

            event_value = str(
                event.get(
                    "id"
                )
                or ""
            )

            if event_value:

                pick[
                    "event_id"
                ] = event_value

                pick[
                    "game_match_status"
                ] = "MATCHED"

                pick[
                    "game_match_source"
                ] = "ESPN"

                pick[
                    "game_match_confidence"
                ] = (
                    "GRADER_FALLBACK"
                )

                print(
                    "GRADER FALLBACK "
                    "MATCH LOCKED:",
                    picker_name(
                        pick
                    ),
                    "|",
                    pick_label(
                        pick
                    ),
                    "| event:",
                    event_value,
                )

        # ----------------------------------------------------
        # From here onward the event has been safely resolved.
        # Recalculate provisional grading from ESPN.
        #
        # clear_grade() intentionally keeps event_id.
        # ----------------------------------------------------

        clear_grade(
            pick
        )

        event_value = str(
            event.get(
                "id"
            )
            or ""
        )

        if event_value:

            pick[
                "event_id"
            ] = event_value

        # ----------------------------------------------------
        # Save the ESPN kickoff/matchup whenever possible.
        # This also helps the dashboard stay populated if
        # schedule_enrich.py did not previously add them.
        # ----------------------------------------------------

        event_date_value = (
            event.get(
                "date"
            )
        )

        if event_date_value:

            pick[
                "game_time"
            ] = event_date_value

        event_comps = competitors(
            event
        )

        if len(
            event_comps
        ) == 2:

            names = []

            for comp in event_comps:

                team = (
                    comp.get(
                        "team"
                    )
                    or {}
                )

                name = (
                    team.get(
                        "shortDisplayName"
                    )
                    or
                    team.get(
                        "displayName"
                    )
                    or
                    team.get(
                        "location"
                    )
                    or
                    team.get(
                        "abbreviation"
                    )
                )

                if name:
                    names.append(
                        str(
                            name
                        )
                    )

            if len(
                names
            ) == 2:

                pick[
                    "game_matchup"
                ] = (
                    f"{names[0]} "
                    f"vs {names[1]}"
                )

        # ----------------------------------------------------
        # Not final yet.
        # ----------------------------------------------------

        if not completed(
            event
        ):

            mark_open(
                pick
            )

            matched_not_final += 1

            if event_value:

                pending_event_ids.add(
                    event_value
                )

            print(
                "MATCHED - NOT FINAL:",
                picker_name(
                    pick
                ),
                "|",
                pick_label(
                    pick
                ),
                "| event:",
                event_value,
            )

            continue

        # ----------------------------------------------------
        # Hydrate the exact event from ESPN summary.
        #
        # This is especially important for period markets.
        # ----------------------------------------------------

        summary = None

        if event_value:

            try:

                summary = fetch_summary(
                    event_value
                )

                event = (
                    hydrate_event_from_summary(
                        event,
                        summary,
                    )
                )

            except Exception as exc:

                print(
                    "SUMMARY FETCH FAILED:",
                    event_value,
                    "|",
                    exc,
                )

        # ----------------------------------------------------
        # Verify the event still reports final after hydration.
        # ----------------------------------------------------

        if not completed(
            event
        ):

            mark_open(
                pick
            )

            matched_not_final += 1

            if event_value:

                pending_event_ids.add(
                    event_value
                )

            continue

        period = market_period(
            bet_type
        )

        period_scores = None

        # ----------------------------------------------------
        # 1Q / 1H grading.
        # ----------------------------------------------------

        if period in {
            "FIRST_QUARTER",
            "FIRST_HALF",
        }:

            try:

                period_scores = (
                    event_period_scores(
                        event,
                        period,
                    )
                )

            except Exception as exc:

                period_scores = None

                print(
                    "PERIOD SCORE ERROR:",
                    event_value,
                    "|",
                    period,
                    "|",
                    exc,
                )

            if not period_scores:

                mark_open(
                    pick
                )

                pick[
                    "grading_error"
                ] = (
                    "Period score "
                    "unavailable"
                )

                period_unavailable += 1

                print(
                    "PERIOD SCORE "
                    "UNAVAILABLE:",
                    picker_name(
                        pick
                    ),
                    "|",
                    pick_label(
                        pick
                    ),
                    "| event:",
                    event_value,
                )

                continue

        # ----------------------------------------------------
        # Calculate wager result.
        # ----------------------------------------------------

        result = grade_market(
            pick,
            event,
            period_scores,
        )

        if result not in {
            "WIN",
            "LOSS",
            "PUSH",
        }:

            mark_review(
                pick,
                (
                    "Could not safely "
                    "grade matched event."
                ),
            )

            review_unsupported += 1

            print(
                "MATCHED BUT "
                "NOT GRADEABLE:",
                picker_name(
                    pick
                ),
                "|",
                pick_label(
                    pick
                ),
                "| event:",
                event_value,
            )

            continue

        # ----------------------------------------------------
        # Apply final ESPN grade.
        # ----------------------------------------------------

        apply_grade(
            pick,
            event,
            result,
        )

        if (
            period_scores
            is not None
        ):

            pick[
                "period_score"
            ] = period_scores

        safely_graded += 1

        print(
            "GRADED:",
            picker_name(
                pick
            ),
            "|",
            pick_label(
                pick
            ),
            "|",
            result,
            "|",
            final_score_text(
                event
            ),
            "| event:",
            event_value,
        )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_json(
        PICKS_FILE,
        picks,
    )

    print()
    print(
        "=" * 70
    )
    print(
        "GRADING SUMMARY"
    )
    print(
        "=" * 70
    )

    print(
        "Official results preserved:",
        official_preserved,
    )

    print(
        "Safely ESPN graded:",
        safely_graded,
    )

    print(
        "Unmatched supported picks:",
        unmatched,
    )

    print(
        "Matched but not final picks:",
        matched_not_final,
    )

    print(
        "Review / unsupported:",
        review_unsupported,
    )

    print(
        "Period score unavailable:",
        period_unavailable,
    )

    print(
        "Preserved due ESPN failure:",
        preserved,
    )

    print(
        "Unique pending tracked games:",
        len(
            pending_event_ids
        ),
    )

    # --------------------------------------------------------
    # Week audits
    # --------------------------------------------------------

    weeks = sorted(
        {
            week_number(
                pick
            )
            for pick in picks
        }
    )

    for week in weeks:

        print_week_audit(
            picks,
            week,
        )

    return picks


# ============================================================
# DIRECT RUN
# ============================================================

if __name__ == "__main__":
    grade_open()
