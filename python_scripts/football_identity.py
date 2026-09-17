from __future__ import annotations

import re
from html import unescape


# ============================================================
# BARSTOOL PICK EM — SHARED FOOTBALL IDENTITY
# ============================================================
#
# PURPOSE
# -------
# This module is the single source of truth for:
#
#   - text normalization
#   - college-football team aliases
#   - matchup parsing
#   - market normalization
#   - selected-team identity
#   - signed spread lines
#   - canonical wager identity
#
# x_ingest.py, schedule_enrich.py and grade.py will all use
# this module.
#
# IMPORTANT DESIGN RULE:
# ----------------------
# Fail closed.
#
# If a team identity is genuinely ambiguous, this module does
# NOT guess.
#
# Example:
#   OSU
#
# could mean:
#   Ohio State
#   Oklahoma State
#   Oregon State
#
# Therefore bare "OSU" remains ambiguous unless the opponent
# or full matchup provides enough context elsewhere.
# ============================================================


# ============================================================
# BASIC TEXT HELPERS
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
        .replace("½", ".5")
        .replace("'", "")
        .replace(".", " ")
    )

    text = re.sub(
        r"[^a-z0-9+\- ]",
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


# ============================================================
# TEAM ALIASES
# ============================================================
#
# Canonical names should be stable.
#
# Aliases include:
#   - common abbreviations
#   - Barstool card abbreviations
#   - ESPN abbreviations where useful
#
# Mascot-only names are intentionally NOT used.
# ============================================================

ALIASES = {

    "air force": {
        "air force",
        "afa",
    },

    "akron": {
        "akron",
        "akr",
    },

    "alabama": {
        "alabama",
        "bama",
        "ala",
    },

    "appalachian state": {
        "appalachian state",
        "appalachian st",
        "app state",
        "app st",
    },

    "arizona": {
        "arizona",
        "zona",
        "uofa",
        "u of a",
    },

    "arizona state": {
        "arizona state",
        "arizona st",
        "asu",
    },

    "arkansas": {
        "arkansas",
        "ark",
    },

    "arkansas state": {
        "arkansas state",
        "arkansas st",
        "ark st",
        "arst",
    },

    "army": {
        "army",
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
        "bay",
    },

    "boise state": {
        "boise state",
        "boise st",
        "boise",
    },

    "boston college": {
        "boston college",
        "bc",
    },

    "bowling green": {
        "bowling green",
        "bowling green state",
        "bowling green st",
        "bgsu",
    },

    "buffalo": {
        "buffalo",
        "buff",
        "ub",
    },

    "byu": {
        "byu",
        "brigham young",
    },

    "california": {
        "california",
        "cal",
    },

    "central michigan": {
        "central michigan",
        "central mich",
        "cmu",
    },

    "charlotte": {
        "charlotte",
        "char",
    },

    "cincinnati": {
        "cincinnati",
        "cincy",
        "cin",
    },

    "clemson": {
        "clemson",
        "clem",
    },

    "coastal carolina": {
        "coastal carolina",
        "coastal",
        "ccu",
    },

    "colorado": {
        "colorado",
        "colo",
        "cu",
    },

    "colorado state": {
        "colorado state",
        "colorado st",
        "colo state",
        "colo st",
        "csu",
    },

    "connecticut": {
        "connecticut",
        "uconn",
        "conn",
    },

    "delaware": {
        "delaware",
        "del",
    },

    "duke": {
        "duke",
    },

    "east carolina": {
        "east carolina",
        "ecu",
    },

    "eastern illinois": {
        "eastern illinois",
        "eiu",
    },

    "eastern michigan": {
        "eastern michigan",
        "eastern mich",
        "emu",
    },

    "fiu": {
        "fiu",
        "florida international",
        "florida intl",
    },

    "florida": {
        "florida",
        "uf",
    },

    "florida atlantic": {
        "florida atlantic",
        "fau",
    },

    "florida state": {
        "florida state",
        "florida st",
        "fsu",
    },

    "fresno state": {
        "fresno state",
        "fresno st",
        "fresno",
    },

    "georgia": {
        "georgia",
        "uga",
    },

    "georgia southern": {
        "georgia southern",
        "ga southern",
        "gaso",
    },

    "georgia state": {
        "georgia state",
        "georgia st",
        "ga state",
        "ga st",
        "gast",
    },

    "georgia tech": {
        "georgia tech",
        "ga tech",
        "gatech",
        "gt",
    },

    "hawaii": {
        "hawaii",
        "hawai i",
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

    "iowa": {
        "iowa",
    },

    "iowa state": {
        "iowa state",
        "iowa st",
        "isu",
    },

    "jacksonville state": {
        "jacksonville state",
        "jacksonville st",
        "jax state",
        "jax st",
        "jville",
    },

    "james madison": {
        "james madison",
        "jmu",
    },

    "kansas": {
        "kansas",
        "ku",
    },

    "kansas state": {
        "kansas state",
        "kansas st",
        "k state",
        "kstate",
        "ksu",
    },

    "kennesaw state": {
        "kennesaw state",
        "kennesaw st",
        "kennesaw",
        "ksaw",
    },

    "kent state": {
        "kent state",
        "kent st",
        "kent",
    },

    "kentucky": {
        "kentucky",
        "uk",
    },

    "lafayette": {
        "lafayette",
        "laf",
    },

    "liberty": {
        "liberty",
        "lib",
    },

    "liu": {
        "liu",
        "long island",
    },

    "louisiana": {
        "louisiana",
        "ul lafayette",
        "ul laf",
        "ull",
    },

    "louisiana tech": {
        "louisiana tech",
        "la tech",
        "latech",
    },

    "louisville": {
        "louisville",
        "ul",
    },

    "lsu": {
        "lsu",
        "louisiana state",
        "louisiana st",
    },

    "marshall": {
        "marshall",
        "marsh",
    },

    "maryland": {
        "maryland",
        "md",
    },

    "memphis": {
        "memphis",
        "mem",
    },

    "miami": {
        "miami",
        "miami fl",
        "miami florida",
    },

    "miami ohio": {
        "miami ohio",
        "miami oh",
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

    "middle tennessee": {
        "middle tennessee",
        "middle tenn",
        "mtsu",
    },

    "minnesota": {
        "minnesota",
        "minn",
    },

    "mississippi state": {
        "mississippi state",
        "mississippi st",
        "miss state",
        "miss st",
        "msst",
    },

    "missouri": {
        "missouri",
        "mizzou",
        "miz",
    },

    "missouri state": {
        "missouri state",
        "missouri st",
        "mo state",
        "mo st",
        "most",
    },

    "navy": {
        "navy",
    },

    "nc state": {
        "nc state",
        "nc st",
        "ncsu",
        "north carolina state",
        "north carolina st",
    },

    "nebraska": {
        "nebraska",
        "neb",
    },

    "nevada": {
        "nevada",
        "nev",
    },

    "new mexico": {
        "new mexico",
        "unm",
    },

    "new mexico state": {
        "new mexico state",
        "new mexico st",
        "nmsu",
    },

    "north carolina": {
        "north carolina",
        "unc",
    },

    "north texas": {
        "north texas",
        "unt",
    },

    "northern illinois": {
        "northern illinois",
        "northern ill",
        "niu",
    },

    "northwestern": {
        "northwestern",
        "nw",
    },

    "notre dame": {
        "notre dame",
        "nd",
    },

    "ohio": {
        "ohio",
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

    "old dominion": {
        "old dominion",
        "odu",
    },

    "ole miss": {
        "ole miss",
        "mississippi",
    },

    "oregon": {
        "oregon",
        "ore",
    },

    "oregon state": {
        "oregon state",
        "oregon st",
        "ore state",
        "ore st",
    },

    "pittsburgh": {
        "pittsburgh",
        "pitt",
    },

    "purdue": {
        "purdue",
        "pur",
    },

    "rice": {
        "rice",
    },

    "rutgers": {
        "rutgers",
        "rutg",
        "ru",
    },

    "sam houston": {
        "sam houston",
        "sam houston state",
        "sam houston st",
        "shsu",
    },

    "san diego state": {
        "san diego state",
        "san diego st",
        "sdsu",
    },

    "san jose state": {
        "san jose state",
        "san jose st",
        "sjsu",
    },

    "south alabama": {
        "south alabama",
        "usa",
    },

    "south carolina": {
        "south carolina",
        "uofsc",
        "u of sc",
        "scar",
    },

    "south florida": {
        "south florida",
        "usf",
    },

    "southern miss": {
        "southern miss",
        "southern mississippi",
        "usm",
    },

    "smu": {
        "smu",
        "southern methodist",
    },

    "stanford": {
        "stanford",
        "stan",
    },

    "syracuse": {
        "syracuse",
        "cuse",
    },

    "temple": {
        "temple",
        "tem",
    },

    "tennessee": {
        "tennessee",
        "tenn",
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

    "texas tech": {
        "texas tech",
        "ttu",
        "ttech",
    },

    "toledo": {
        "toledo",
        "tol",
    },

    "troy": {
        "troy",
    },

    "tulane": {
        "tulane",
    },

    "tulsa": {
        "tulsa",
    },

    "uab": {
        "uab",
        "alabama birmingham",
    },

    "ucf": {
        "ucf",
        "central florida",
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

    "utah": {
        "utah",
    },

    "utah state": {
        "utah state",
        "utah st",
        "usu",
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

    "utsa": {
        "utsa",
        "texas san antonio",
    },

    "vanderbilt": {
        "vanderbilt",
        "vandy",
    },

    "virginia": {
        "virginia",
        "uva",
    },

    "virginia tech": {
        "virginia tech",
        "va tech",
        "vtech",
        "vt",
        "vt&ch",
        "vt and ch",
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

    "western kentucky": {
        "western kentucky",
        "wku",
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

    "wyoming": {
        "wyoming",
        "wyo",
    },
}


# ============================================================
# AMBIGUOUS SHORT NAMES
# ============================================================

AMBIGUOUS_ALIASES = {
    "osu",
}


# ============================================================
# ALIAS INDEX
# ============================================================

def _build_alias_index():
    index = {}

    for canonical, aliases in ALIASES.items():

        values = {
            canonical,
            *aliases,
        }

        for value in values:
            key = norm(value)

            if not key:
                continue

            existing = index.get(key)

            if (
                existing
                and existing != canonical
            ):
                # Never silently let two schools own the same alias.
                index[key] = None

            else:
                index[key] = canonical

    for value in AMBIGUOUS_ALIASES:
        index[norm(value)] = None

    return index


ALIAS_INDEX = _build_alias_index()


# ============================================================
# TEAM IDENTITY
# ============================================================

def canonical_team(value):
    """
    Return the canonical team identity when known.

    Unknown full team names are returned normalized so that
    ESPN can still match exact normalized names.

    Explicitly ambiguous aliases return None.
    """

    normalized = norm(value)

    if not normalized:
        return None

    if normalized in {
        norm(value)
        for value in AMBIGUOUS_ALIASES
    }:
        return None

    if normalized in ALIAS_INDEX:
        return ALIAS_INDEX[
            normalized
        ]

    return normalized


def alias_group(value):
    normalized = norm(value)

    if not normalized:
        return set()

    if normalized in {
        norm(value)
        for value in AMBIGUOUS_ALIASES
    }:
        return {
            normalized
        }

    canonical = canonical_team(
        normalized
    )

    if not canonical:
        return {
            normalized
        }

    aliases = ALIASES.get(
        canonical
    )

    if not aliases:
        return {
            normalized,
            canonical,
        }

    return {
        norm(canonical),
        *{
            norm(alias)
            for alias in aliases
        },
    }


def is_ambiguous_hint(value):
    return (
        norm(value)
        in {
            norm(item)
            for item in AMBIGUOUS_ALIASES
        }
    )


def teams_equivalent(
    first,
    second,
):
    if (
        not first
        or not second
    ):
        return False

    if (
        is_ambiguous_hint(first)
        or is_ambiguous_hint(second)
    ):
        return False

    return bool(
        alias_group(first)
        & alias_group(second)
    )


# ============================================================
# MATCHUP PARSING
# ============================================================

def split_matchup(value):
    """
    Convert common matchup formats into exactly two team hints.

    Supported examples:

      Florida @ Auburn
      Florida vs Auburn
      Florida v Auburn
      Florida at Auburn
      Florida/Auburn
    """

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

    pieces = [
        piece.strip()
        for piece in pieces
        if piece.strip()
    ]

    if len(pieces) != 2:
        return []

    return pieces


def canonical_matchup(
    first,
    second,
):
    """
    Matchup identity is order-independent.

    This is intentional because the tracker primarily needs
    to identify the GAME, not infer home/away from X text.
    """

    first_team = canonical_team(
        first
    )

    second_team = canonical_team(
        second
    )

    if (
        not first_team
        or not second_team
    ):
        return None

    if first_team == second_team:
        return None

    return tuple(
        sorted(
            [
                first_team,
                second_team,
            ]
        )
    )


def matchup_identity(value):
    pieces = split_matchup(
        value
    )

    if len(pieces) != 2:
        return None

    return canonical_matchup(
        pieces[0],
        pieces[1],
    )


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
        pick.get(
            "team"
        )
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

    if len(pieces) != 2:
        return []

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
        not pieces[0]
        or not pieces[1]
    ):
        return []

    return pieces


def best_matchup_hints(
    pick
):
    """
    Strongest matchup source first.

    1. Explicit structured matchup
    2. team + opponent
    3. matchup embedded in selection
    """

    matchup = split_matchup(
        pick.get(
            "matchup"
        )
    )

    if len(matchup) == 2:
        return matchup

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

    if team and opponent:
        return [
            team,
            opponent,
        ]

    return selection_matchup_hints(
        pick
    )


def canonical_game_identity(
    pick
):
    hints = best_matchup_hints(
        pick
    )

    if len(hints) != 2:
        return None

    return canonical_matchup(
        hints[0],
        hints[1],
    )


# ============================================================
# MARKET NORMALIZATION
# ============================================================

MARKET_ALIASES = {

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


SUPPORTED_MARKETS = {
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


def normalize_bet_type(value):
    raw = str(
        value or ""
    ).upper().strip()

    return MARKET_ALIASES.get(
        raw,
        raw,
    )


def market_period(
    bet_type
):
    bet_type = normalize_bet_type(
        bet_type
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
    bet_type = normalize_bet_type(
        bet_type
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


# ============================================================
# TOTAL DIRECTION
# ============================================================

def total_direction(
    pick
):
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

    selection = clean_text(
        pick.get(
            "selection"
        )
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


# ============================================================
# SELECTED TEAM IDENTITY
# ============================================================

def side_identity(
    pick
):
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

            pieces = split_matchup(
                value
            )

            if pieces:
                return pieces[-1]

            return value

    return None


def canonical_selected_team(
    pick
):
    selected = side_identity(
        pick
    )

    if not selected:
        return None

    return canonical_team(
        selected
    )


# ============================================================
# SPREAD LINE NORMALIZATION
# ============================================================

def spread_line_from_selection(
    pick
):
    """
    The visible selection controls the SIGN of a spread.

    Examples:

      Oregon -22.5
      Purdue +3
      Oregon 1H -13.5
    """

    selection = clean_text(
        pick.get(
            "selection"
        )
    )

    if not selection:
        return None

    text = (
        selection
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    # Ignore trailing American odds:
    #
    # Oregon -22.5 (-110)
    #
    text = re.sub(
        r"\(\s*[+-]\s*"
        r"\d+(?:\.\d+)?\s*\)"
        r"\s*$",
        "",
        text,
    ).strip()

    matches = list(
        re.finditer(
            r"(?<![A-Za-z0-9])"
            r"([+-])\s*"
            r"(\d+(?:\.\d+)?)",
            text,
        )
    )

    if not matches:
        return None

    match = matches[0]

    sign = (
        -1.0
        if match.group(1) == "-"
        else 1.0
    )

    try:
        return (
            sign
            * float(
                match.group(2)
            )
        )

    except Exception:
        return None


def effective_line(
    pick
):
    """
    Return the wager line that should be used for identity.

    Spread markets prefer the signed line visible in selection.
    Other markets use the stored numeric line.
    """

    market = base_market(
        pick.get(
            "bet_type"
        )
    )

    if market == "SPREAD":

        visible = (
            spread_line_from_selection(
                pick
            )
        )

        if visible is not None:
            return visible

    return safe_float(
        pick.get(
            "line"
        )
    )


# ============================================================
# CANONICAL WAGER IDENTITY
# ============================================================

def normalized_line_key(
    value
):
    number = safe_float(
        value
    )

    if number is None:
        return ""

    if float(number).is_integer():
        return str(
            int(number)
        )

    return (
        f"{number:.4f}"
        .rstrip("0")
        .rstrip(".")
    )


def canonical_pick_key(
    pick
):
    """
    Permanent wager identity.

    CRITICAL CHANGE FROM THE OLD TRACKER:
    -------------------------------------
    Game identity is included whenever available for ALL
    markets — including spreads.

    Therefore:

      Team X -3 vs Team Y

    is not automatically considered identical to:

      Team X -3 vs Team Z

    This prevents legitimate wagers from disappearing merely
    because the selected team and line happen to be identical.
    """

    picker = norm(
        pick.get(
            "picker"
        )
    )

    try:
        week = int(
            pick.get(
                "week"
            )
            or 0
        )

    except Exception:
        week = 0

    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    market = base_market(
        bet_type
    )

    period = market_period(
        bet_type
    )

    line = normalized_line_key(
        effective_line(
            pick
        )
    )

    game = canonical_game_identity(
        pick
    )

    if game:
        game_key = (
            f"{game[0]}__{game[1]}"
        )

    else:
        game_key = ""

    if market == "TOTAL":

        side = total_direction(
            pick
        ) or ""

        identity = (
            game_key
            or norm(
                pick.get(
                    "matchup"
                )
            )
        )

        return (
            picker,
            week,
            period,
            market,
            identity,
            side,
            line,
        )

    if market == "TEAM_TOTAL":

        side = total_direction(
            pick
        ) or ""

        team = (
            canonical_selected_team(
                pick
            )
            or norm(
                side_identity(
                    pick
                )
            )
        )

        return (
            picker,
            week,
            period,
            market,
            game_key,
            team,
            side,
            line,
        )

    if market == "SPREAD":

        team = (
            canonical_selected_team(
                pick
            )
            or norm(
                side_identity(
                    pick
                )
            )
        )

        return (
            picker,
            week,
            period,
            market,
            game_key,
            team,
            line,
        )

    if market == "MONEYLINE":

        team = (
            canonical_selected_team(
                pick
            )
            or norm(
                side_identity(
                    pick
                )
            )
        )

        return (
            picker,
            week,
            period,
            market,
            game_key,
            team,
        )

    # Unsupported/unknown markets should remain conservative.
    # Include the selection so unrelated wagers are not merged.

    return (
        picker,
        week,
        period,
        market,
        game_key,
        norm(
            pick.get(
                "selection"
            )
        ),
        line,
    )


# ============================================================
# PICK IDENTITY VALIDATION
# ============================================================

def validate_pick_identity(
    pick
):
    """
    Lightweight structural validation.

    Returns:
        (True, [])
    or:
        (False, ["reason", ...])

    This does NOT decide whether an X post was completely
    extracted. x_ingest.py will add card-level validation later.
    """

    errors = []

    picker = clean_text(
        pick.get(
            "picker"
        )
    )

    if not picker:
        errors.append(
            "missing picker"
        )

    try:
        week = int(
            pick.get(
                "week"
            )
        )

        if week < 1:
            errors.append(
                "invalid week"
            )

    except Exception:
        errors.append(
            "invalid week"
        )

    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    if bet_type not in SUPPORTED_MARKETS:
        errors.append(
            "unsupported market"
        )

    selection = clean_text(
        pick.get(
            "selection"
        )
    )

    if not selection:
        errors.append(
            "missing selection"
        )

    market = base_market(
        bet_type
    )

    if market in {
        "SPREAD",
        "TOTAL",
        "TEAM_TOTAL",
    }:

        if effective_line(
            pick
        ) is None:
            errors.append(
                "missing numeric line"
            )

    if market == "TOTAL":

        if total_direction(
            pick
        ) not in {
            "OVER",
            "UNDER",
        }:
            errors.append(
                "missing total direction"
            )

    if market == "TEAM_TOTAL":

        if total_direction(
            pick
        ) not in {
            "OVER",
            "UNDER",
        }:
            errors.append(
                "missing team-total direction"
            )

        if not side_identity(
            pick
        ):
            errors.append(
                "missing team-total team"
            )

    if market in {
        "SPREAD",
        "MONEYLINE",
    }:

        if not side_identity(
            pick
        ):
            errors.append(
                "missing selected team"
            )

    return (
        len(errors) == 0,
        errors,
    )
