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
        pick.get(
            "selection"
        )
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
        r"\b(?:tt|team\s*total)"
        r"\s*(o|u)\s*"
        r"\d+(?:\.\d+)?",
        text,
        flags=re.I,
    )

    if compact:
        return (
            "OVER"
            if (
                compact
                .group(1)
                .lower()
                == "o"
            )
            else "UNDER"
        )

    return None


# ============================================================
# SEASON / WEEK WINDOWS
# ============================================================

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
    datestring = (
        day.strftime(
            "%Y%m%d"
        )
    )

    params = {
        "dates":
            datestring,

        "limit":
            1000,
    }

    payload = request_json(
        ESPN_SCOREBOARD,
        params,
    )

    return (
        payload.get(
            "events"
        )
        or []
    )


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


def build_complete_week_slate(
    picks,
    season_year,
    week,
):
    """
    Fetch the complete date window for one Pick Em week.

    We intentionally avoid fuzzy score matching.
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

            all_events.extend(
                events
            )

        except Exception as exc:

            print(
                "ESPN DAY FETCH FAILED:",
                current,
                "|",
                exc,
            )

        current += timedelta(
            days=1
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
        len(
            merged
        ),
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


# ============================================================
# EVENT HYDRATION
# ============================================================

def hydrate_event(
    event,
    cache,
):
    event_id = str(
        event.get(
            "id"
        )
        or ""
    )

    if not event_id:
        return event

    if event_id in cache:
        return cache[
            event_id
        ]

    try:

        payload = request_json(
            ESPN_SUMMARY,
            {
                "event":
                    event_id
            },
        )

        header = (
            payload.get(
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

        if competitions_list:

            hydrated = dict(
                event
            )

            hydrated[
                "competitions"
            ] = (
                competitions_list
            )

            hydrated[
                "status"
            ] = (
                header.get(
                    "status"
                )
                or event.get(
                    "status"
                )
            )

            cache[
                event_id
            ] = hydrated

            return hydrated

    except Exception as exc:

        print(
            "ESPN SUMMARY FAILED:",
            event_id,
            "|",
            exc,
        )

    cache[
        event_id
    ] = event

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
            comps[0].get(
                "id"
            )
        )

        if value:
            return str(
                value
            )

    return str(
        event.get(
            "id"
        )
        or ""
    )


def extract_linescores(raw):
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
            item.get(
                "period"
            )
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
                period.get(
                    "number"
                )
                or period.get(
                    "value"
                )
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

            candidate = (
                item.get(key)
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
                continue


        if value is not None:
            output[
                period
            ] = value

    return output


def embedded_linescores(comp):
    return extract_linescores(
        comp.get(
            "linescores"
        )
        or []
    )


def fetch_team_linescores(
    event,
    comp,
):
    event_id = str(
        event.get(
            "id"
        )
        or ""
    )

    comp_id = competition_id(
        event
    )

    team_id = str(
        (
            comp.get(
                "team"
            )
            or {}
        ).get(
            "id"
        )
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
            "limit":
                100
        },
    )

    return (
        payload.get(
            "items"
        )
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
        event.get(
            "id"
        )
        or ""
    )

    team_id = str(
        (
            comp.get(
                "team"
            )
            or {}
        ).get(
            "id"
        )
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


    # Embedded scores first.
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


    # Exact ESPN competitor period endpoint second.
    try:

        raw = fetch_team_linescores(
            event,
            comp,
        )

        scores = extract_linescores(
            raw
        )

        cache[key] = scores

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

        return q1 + q2

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
        "FIRST_QUARTER":
            "1Q",

        "FIRST_HALF":
            "1H",

        "FULL_GAME":
            "FINAL",
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
# CLEAR PROVISIONAL GRADE
# ============================================================

def clear_grade(
    pick,
    status="OPEN",
):
    """
    NEVER call this for official results.
    """

    if is_official_result(
        pick
    ):
        return

    pick["result"] = None
    pick["status"] = status
    pick["event_id"] = None
    pick["graded_at"] = None
    pick["final_score"] = None
    pick["profit_units"] = 0


# ============================================================
# GRADE ONE PICK
# ============================================================

def grade_pick(
    pick,
    event,
    linescore_cache,
):
    """
    Conservative / fail-closed grading.

    Official result rows can never enter this function.
    """

    if is_official_result(
        pick
    ):
        return False

    if not completed(
        event
    ):
        return False


    comps = competitors(
        event
    )

    if len(comps) != 2:
        return False


    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    period = market_period(
        bet_type
    )

    market = base_market(
        bet_type
    )


    # --------------------------------------------------------
    # GET REQUIRED SCORES
    # --------------------------------------------------------

    scores = {}

    for comp in comps:

        comp_key = str(
            comp.get(
                "id"
            )
            or (
                comp.get(
                    "team"
                )
                or {}
            ).get(
                "id"
            )
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
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "|",
                period,
            )

            return False

        scores[
            comp_key
        ] = value


    line = safe_float(
        pick.get(
            "line"
        )
    )


    # ========================================================
    # GAME TOTAL
    # ========================================================

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

        if actual == line:
            result = "PUSH"

        elif direction == "OVER":

            result = (
                "WIN"
                if actual > line
                else "LOSS"
            )

        else:

            result = (
                "WIN"
                if actual < line
                else "LOSS"
            )


    # ========================================================
    # TEAM TOTAL
    # ========================================================

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
            selected.get(
                "id"
            )
            or (
                selected.get(
                    "team"
                )
                or {}
            ).get(
                "id"
            )
            or ""
        )

        if selected_key not in scores:
            return False

        actual = scores[
            selected_key
        ]

        if actual == line:
            result = "PUSH"

        elif direction == "OVER":

            result = (
                "WIN"
                if actual > line
                else "LOSS"
            )

        else:

            result = (
                "WIN"
                if actual < line
                else "LOSS"
            )


    # ========================================================
    # SPREAD / MONEYLINE
    # ========================================================

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
            selected.get(
                "id"
            )
            or (
                selected.get(
                    "team"
                )
                or {}
            ).get(
                "id"
            )
            or ""
        )

        opponents = [
            comp
            for comp in comps
            if str(
                comp.get(
                    "id"
                )
                or (
                    comp.get(
                        "team"
                    )
                    or {}
                ).get(
                    "id"
                )
                or ""
            )
            != selected_key
        ]

        if len(opponents) != 1:
            return False

        opponent = opponents[0]

        opponent_key = str(
            opponent.get(
                "id"
            )
            or (
                opponent.get(
                    "team"
                )
                or {}
            ).get(
                "id"
            )
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
            scores[
                selected_key
            ]
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
                margin + line
            )

            if adjusted > 0:
                result = "WIN"

            elif adjusted < 0:
                result = "LOSS"

            else:
                result = "PUSH"


    else:
        return False


    # ========================================================
    # SAVE PROVISIONAL ESPN RESULT
    # ========================================================

    pick["result"] = result

    pick["status"] = "FINAL"

    pick["event_id"] = (
        event.get(
            "id"
        )
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


# ============================================================
# MAIN GRADER
# ============================================================

def grade_open():

    picks = load_json(
        PICKS_FILE,
        [],
    )

    if not isinstance(
        picks,
        list,
    ):
        picks = []


    # ========================================================
    # 1. NORMALIZE ONLY PROVISIONAL PICKS
    # ========================================================

    for pick in picks:

        if is_official_result(
            pick
        ):
            continue

        normalize_existing_pick_market(
            pick
        )


    # ========================================================
    # 2. VALIDATE OFFICIAL RESULTS
    #
    # We never repair or alter them here.
    # We only report them.
    # ========================================================

    official_locked = 0
    official_invalid = 0

    for pick in picks:

        if not is_official_result(
            pick
        ):
            continue

        official_locked += 1

        if not official_result_valid(
            pick
        ):

            official_invalid += 1

            print(
                "WARNING - OFFICIAL ROW INVALID:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| result:",
                pick.get(
                    "result"
                ),
            )


    # ========================================================
    # 3. ESPN WEEKS NEEDED
    #
    # THE IMPORTANT CHANGE:
    #
    # Officially reconciled picks are completely excluded here.
    #
    # Therefore an officially reconciled Week 1 generates
    # ZERO Week 1 ESPN work.
    # ========================================================

    provisional_picks = [
        pick
        for pick in picks
        if (
            pick.get(
                "sport"
            )
            in {
                "CFB",
                "NCAAF",
            }
            and not is_official_result(
                pick
            )
        )
    ]


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
        for pick in provisional_picks
    })


    print()
    print(
        "========== GRADER PRECHECK =========="
    )

    print(
        "Official results locked:",
        official_locked,
    )

    print(
        "Official invalid rows:",
        official_invalid,
    )

    print(
        "Provisional picks eligible for ESPN:",
        len(
            provisional_picks
        ),
    )

    print(
        "ESPN weeks required:",
        required_weeks,
    )

    print(
        "====================================="
    )


    # ========================================================
    # 4. FETCH ESPN DATA ONLY FOR PROVISIONAL WEEKS
    # ========================================================

    week_cache = {}

    failed_weeks = set()


    for (
        season_year,
        week,
    ) in required_weeks:

        try:

            events = (
                build_complete_week_slate(
                    provisional_picks,
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


    # ========================================================
    # 5. GRADE
    # ========================================================

    graded = 0
    unmatched = 0
    not_final = 0
    review = 0
    preserved = 0
    period_missing = 0
    skipped_official = 0

    pending_events = {}

    summary_cache = {}

    linescore_cache = {}


    for pick in picks:


        # ====================================================
        # ABSOLUTE OFFICIAL RESULT LOCK
        #
        # NOTHING BELOW THIS LINE MAY TOUCH THESE ROWS.
        # ====================================================

        if is_official_result(
            pick
        ):

            skipped_official += 1

            continue


        if pick.get(
            "sport"
        ) not in {
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
            pick.get(
                "week"
            )
            or 1
        )

        key = (
            season_year,
            week,
        )


        # ----------------------------------------------------
        # ESPN failed -> preserve prior data.
        # ----------------------------------------------------

        if key in failed_weeks:

            preserved += 1

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


        # ----------------------------------------------------
        # Unsupported market
        # ----------------------------------------------------

        if bet_type not in SUPPORTED:

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
                "| type:",
                bet_type,
            )

            continue


        # ----------------------------------------------------
        # Team total needs explicit direction.
        # ----------------------------------------------------

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
            week_cache.get(
                key,
                []
            )
        )


        event = resolve_event(
            pick,
            events,
        )


        # ----------------------------------------------------
        # Recalculate PROVISIONAL pick only.
        # ----------------------------------------------------

        clear_grade(
            pick
        )


        if event is None:

            unmatched += 1

            print(
                "UNMATCHED:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "| matchup:",
                pick.get(
                    "matchup"
                ),
                "| team:",
                pick.get(
                    "team"
                ),
                "| type:",
                pick.get(
                    "bet_type"
                ),
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
                event.get(
                    "id"
                )
                or "unknown"
            )

            pending_events.setdefault(
                event_id,
                {
                    "event":
                        event,

                    "picks":
                        [],
                },
            )

            pending_events[
                event_id
            ]["picks"].append(
                {
                    "picker":
                        pick.get(
                            "picker"
                        ),

                    "selection":
                        pick.get(
                            "selection"
                        ),
                }
            )

            continue


        period = market_period(
            bet_type
        )


        success = grade_pick(
            pick,
            event,
            linescore_cache,
        )


        if success:

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

            # A completed event was found, but a period score or
            # another required exact value wasn't available.
            if period != "FULL_GAME":

                period_missing += 1

                print(
                    "PERIOD DATA UNAVAILABLE:",
                    pick.get(
                        "picker"
                    ),
                    "|",
                    pick.get(
                        "selection"
                    ),
                )

            else:

                unmatched += 1

                print(
                    "GRADE FAILED CLOSED:",
                    pick.get(
                        "picker"
                    ),
                    "|",
                    pick.get(
                        "selection"
                    ),
                )


    # ========================================================
    # 6. SAVE
    # ========================================================

    save_json(
        PICKS_FILE,
        picks,
    )


    # ========================================================
    # 7. SUMMARY
    # ========================================================

    print()
    print(
        "========== GRADING SUMMARY =========="
    )

    print(
        "Official Barstool results preserved:",
        skipped_official,
    )

    print(
        "Official rows with invalid result:",
        official_invalid,
    )

    print(
        "Safely ESPN graded:",
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

    print(
        "====================================="
    )


if __name__ == "__main__":
    grade_open()
