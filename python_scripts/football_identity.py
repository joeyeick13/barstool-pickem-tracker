from __future__ import annotations

import re
from html import unescape


# ============================================================
# BARSTOOL PICK EM — SHARED FOOTBALL IDENTITY
# ============================================================
#
# Single source of truth for:
#
#   - text normalization
#   - college-football team aliases
#   - ESPN naming variants
#   - ESPN display names with mascots
#   - matchup parsing
#   - market normalization
#   - selected-team identity
#   - signed spread lines
#   - canonical wager identity
#
# DESIGN RULE:
# Fail closed when an identity is genuinely ambiguous.
#
# There are NO week-specific fixes, event IDs, or matchup
# corrections in this file.
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
# TEAM NAME NORMALIZATION
# ============================================================

DIRECTIONAL_WORDS = {
    "n": "northern",
    "s": "southern",
    "e": "eastern",
    "w": "western",
    "c": "central",
}


def normalized_team_text(value):
    text = norm(value)

    if not text:
        return ""

    tokens = text.split()

    if not tokens:
        return ""

    if (
        tokens[0] in DIRECTIONAL_WORDS
        and len(tokens) >= 2
    ):
        tokens[0] = DIRECTIONAL_WORDS[
            tokens[0]
        ]

    if (
        len(tokens) >= 2
        and tokens[-1] == "st"
    ):
        tokens[-1] = "state"

    return " ".join(tokens)


def team_name_variants(value):
    """
    Generate safe generic ESPN/Barstool naming variants.

    This does NOT guess between unrelated schools.
    """
    base = norm(value)
    normalized = normalized_team_text(value)

    variants = {
        base,
        normalized,
    }

    tokens = normalized.split()

    if tokens:
        if (
            tokens[0] in {
                "northern",
                "southern",
                "eastern",
                "western",
                "central",
            }
            and len(tokens) >= 2
        ):
            short = {
                "northern": "n",
                "southern": "s",
                "eastern": "e",
                "western": "w",
                "central": "c",
            }[
                tokens[0]
            ]

            variants.add(
                " ".join(
                    [
                        short,
                        *tokens[1:],
                    ]
                )
            )

        if (
            len(tokens) >= 2
            and tokens[-1] == "state"
        ):
            variants.add(
                " ".join(
                    [
                        *tokens[:-1],
                        "st",
                    ]
                )
            )

    return {
        item
        for item in variants
        if item
    }


# ============================================================
# TEAM ALIASES
# ============================================================

ALIASES = {
    "air force": {"air force", "afa"},
    "akron": {"akron", "akr"},
    "alabama": {"alabama", "bama", "ala"},
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
    "arkansas": {"arkansas", "ark"},
    "arkansas state": {
        "arkansas state",
        "arkansas st",
        "ark st",
        "arst",
    },
    "army": {"army"},
    "auburn": {"auburn", "aub"},
    "ball state": {
        "ball state",
        "ball st",
        "ball",
    },
    "baylor": {"baylor", "bay"},
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
    "duke": {"duke"},
    "east carolina": {
        "east carolina",
        "ecu",
    },
    "eastern illinois": {
        "eastern illinois",
        "e illinois",
        "eiu",
    },
    "eastern michigan": {
        "eastern michigan",
        "eastern mich",
        "e michigan",
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
    "grambling": {
        "grambling",
        "grambling state",
        "grambling st",
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
    "iowa": {"iowa"},
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
        "k-state",
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
        "mary",
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
        "minny",
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
    "navy": {"navy"},
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
    "norfolk state": {
        "norfolk state",
        "norfolk st",
        "norfolk",
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
        "n illinois",
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
    "ohio": {"ohio"},
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
    "prairie view": {
        "prairie view",
        "prairie view a and m",
        "prairie view a&m",
        "pvamu",
    },
    "purdue": {
        "purdue",
        "pur",
    },
    "rice": {"rice"},
    "rutgers": {
        "rutgers",
        "rutg",
        "ru",
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
    "tcu": {
        "tcu",
        "texas christian",
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
    "troy": {"troy"},
    "tulane": {"tulane"},
    "tulsa": {"tulsa"},
    "uab": {
        "uab",
        "alabama birmingham",
    },
    "ucf": {
        "ucf",
        "central florida",
    },
    "ucla": {"ucla"},
    "umass": {
        "umass",
        "massachusetts",
    },
    "unlv": {"unlv"},
    "usc": {
        "usc",
        "southern california",
        "southern cal",
    },
    "utah": {"utah"},
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
        "w michigan",
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

        expanded = set()

        for value in values:
            expanded.update(
                team_name_variants(value)
            )

        for value in expanded:
            key = norm(value)

            if not key:
                continue

            existing = index.get(key)

            if (
                existing
                and existing != canonical
            ):
                # Never silently allow two schools to own one alias.
                index[key] = None
            else:
                index[key] = canonical

    for value in AMBIGUOUS_ALIASES:
        index[norm(value)] = None

    return index


ALIAS_INDEX = _build_alias_index()


# ============================================================
# RUNTIME ESPN TEAM IDENTITY REGISTRY
# ============================================================
#
# ESPN gives every competitor a stable team ID plus multiple safe
# names for that same team (for example location, displayName,
# shortDisplayName, abbreviation).  espn_resolver.py registers those
# structured relationships here whenever it retrieves a slate.
#
# This is intentionally runtime-only:
#   - no week-specific corrections
#   - no event-specific corrections
#   - no mascot guessing
#   - no fuzzy matching
#
# A name is learned only because ESPN itself attached that name to the
# same stable team ID.  Conflicting names fail closed.
# ============================================================

ESPN_TEAM_IDENTITIES = {}
ESPN_NAME_INDEX = {}
ESPN_CANONICAL_NAMES = {}


def _runtime_name_variants(value):
    variants = set()

    for item in team_name_variants(value):
        normalized = norm(item)

        if normalized:
            variants.add(normalized)

    return variants


def _set_runtime_name(name, canonical):
    """
    Add one ESPN-provided name to the runtime index.

    If ESPN ever causes the same normalized name to point at two
    different canonical teams during one process, mark it ambiguous
    instead of silently choosing one.
    """
    for variant in _runtime_name_variants(name):
        if variant in {
            norm(item)
            for item in AMBIGUOUS_ALIASES
        }:
            ESPN_NAME_INDEX[variant] = None
            continue

        if variant not in ESPN_NAME_INDEX:
            ESPN_NAME_INDEX[variant] = canonical
            continue

        existing = ESPN_NAME_INDEX.get(variant)

        if existing != canonical:
            ESPN_NAME_INDEX[variant] = None


def register_espn_team_identity(
    team_id,
    canonical_name,
    names=None,
):
    """
    Register a structured ESPN team identity.

    Parameters
    ----------
    team_id:
        ESPN's stable team ID.

    canonical_name:
        ESPN's school/location identity for the competitor.  This is
        the anchor; the function does NOT derive a school name by
        stripping words from a display name.

    names:
        Other safe names ESPN supplied for the same team ID, such as
        displayName, shortDisplayName, location, and abbreviation.

    Returns the canonical identity when registration succeeds, else
    None.

    Safety:
      - stable ESPN team ID establishes the relationship
      - known static aliases remain authoritative
      - explicit ambiguous aliases remain ambiguous
      - a team ID cannot silently change canonical identity
      - a name collision between two ESPN teams fails closed
    """
    team_id = clean_text(team_id)
    anchor = clean_text(canonical_name)

    if not team_id or not anchor:
        return None

    if is_ambiguous_hint(anchor):
        return None

    # Prefer the curated static identity when ESPN's anchor is already
    # known there.  Otherwise the normalized ESPN anchor itself becomes
    # the runtime canonical identity.
    anchor_variants = team_name_variants(anchor)

    static_found = {
        ALIAS_INDEX[item]
        for item in anchor_variants
        if (
            item in ALIAS_INDEX
            and ALIAS_INDEX[item]
        )
    }

    if len(static_found) > 1:
        return None

    if len(static_found) == 1:
        canonical = next(iter(static_found))
    else:
        canonical = normalized_team_text(anchor)

    if not canonical:
        return None

    existing_canonical = ESPN_CANONICAL_NAMES.get(team_id)

    if (
        existing_canonical
        and existing_canonical != canonical
    ):
        # Never let one ESPN team ID silently become another team.
        return None

    ESPN_CANONICAL_NAMES[team_id] = canonical

    values = {
        anchor,
        canonical,
    }

    for value in names or []:
        value = clean_text(value)

        if value:
            values.add(value)

    bucket = ESPN_TEAM_IDENTITIES.setdefault(
        team_id,
        set(),
    )

    bucket.update(values)

    for value in values:
        _set_runtime_name(
            value,
            canonical,
        )

    return canonical


def runtime_espn_aliases(value):
    """
    Return all ESPN-learned names belonging to value's runtime
    canonical team.
    """
    normalized = norm(value)

    if not normalized:
        return set()

    canonical = ESPN_NAME_INDEX.get(normalized)

    if not canonical:
        return set()

    values = {
        canonical,
    }

    for team_id, team_canonical in ESPN_CANONICAL_NAMES.items():
        if team_canonical != canonical:
            continue

        values.update(
            ESPN_TEAM_IDENTITIES.get(
                team_id,
                set(),
            )
        )

    expanded = set()

    for item in values:
        expanded.update(
            _runtime_name_variants(item)
        )

    return expanded




# ============================================================
# TEAM IDENTITY
# ============================================================

def _canonical_from_known_prefix(value):
    """
    Resolve ESPN display names that append a mascot to a school.

    Examples:
        Arizona State Sun Devils -> arizona state
        Army Black Knights       -> army
        Oregon Ducks             -> oregon
        Clemson Tigers           -> clemson

    Permanent safety rules:

      1. Only a known alias can establish the school identity.
      2. The longest matching alias wins.
      3. If equally specific aliases point to different schools,
         resolution fails closed.
      4. Explicitly ambiguous aliases such as OSU never qualify.
      5. No mascot dictionary is required.
      6. No week, event, or matchup-specific knowledge is used.
    """
    normalized = normalized_team_text(value)

    if not normalized:
        return None

    matches = []

    for alias, canonical in ALIAS_INDEX.items():
        if not alias or not canonical:
            continue

        if norm(alias) in {
            norm(item)
            for item in AMBIGUOUS_ALIASES
        }:
            continue

        alias_normalized = normalized_team_text(alias)

        if not alias_normalized:
            continue

        if normalized.startswith(
            alias_normalized + " "
        ):
            matches.append(
                (
                    len(
                        alias_normalized.split()
                    ),
                    len(alias_normalized),
                    canonical,
                )
            )

    if not matches:
        return None

    # Prefer the most specific school name.
    #
    # Example:
    #   "Miami Ohio RedHawks"
    #
    # can begin with both "Miami" and "Miami Ohio".
    # "Miami Ohio" must win.
    best_token_count = max(
        item[0]
        for item in matches
    )

    token_best = [
        item
        for item in matches
        if item[0] == best_token_count
    ]

    best_length = max(
        item[1]
        for item in token_best
    )

    finalists = {
        item[2]
        for item in token_best
        if item[1] == best_length
    }

    if len(finalists) != 1:
        return None

    return next(
        iter(finalists)
    )


def canonical_team(value):
    """
    Return a stable canonical team identity.

    Resolution order:

      1. exact known static alias
      2. exact ESPN-registered runtime identity
      3. generic known static school prefix + appended mascot
      4. normalized unknown name

    Explicitly ambiguous aliases return None.

    Runtime ESPN identities are learned only from structured ESPN team
    records registered by espn_resolver.py.  This function never strips
    an unknown mascot or performs fuzzy matching.
    """
    normalized = norm(value)

    if not normalized:
        return None

    ambiguous = {
        norm(item)
        for item in AMBIGUOUS_ALIASES
    }

    if normalized in ambiguous:
        return None

    candidates = team_name_variants(
        normalized
    )

    found = {
        ALIAS_INDEX[candidate]
        for candidate in candidates
        if (
            candidate in ALIAS_INDEX
            and ALIAS_INDEX[candidate]
        )
    }

    if len(found) == 1:
        return next(
            iter(found)
        )

    if len(found) > 1:
        return None

    runtime_found = {
        ESPN_NAME_INDEX[candidate]
        for candidate in candidates
        if (
            candidate in ESPN_NAME_INDEX
            and ESPN_NAME_INDEX[candidate]
        )
    }

    if len(runtime_found) == 1:
        return next(
            iter(runtime_found)
        )

    if len(runtime_found) > 1:
        return None

    # Known static school prefixes safely handle ESPN display names for
    # schools already represented in the curated alias registry.
    prefixed = _canonical_from_known_prefix(
        normalized
    )

    if prefixed:
        return prefixed

    return normalized_team_text(
        normalized
    )

def alias_group(value):
    normalized = norm(value)

    if not normalized:
        return set()

    if normalized in {
        norm(item)
        for item in AMBIGUOUS_ALIASES
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

    values = {
        canonical,
        normalized,
    }

    if aliases:
        values.update(
            aliases
        )

    values.update(
        runtime_espn_aliases(
            normalized
        )
    )

    expanded = set()

    for item in values:
        expanded.update(
            team_name_variants(item)
        )

    return {
        norm(item)
        for item in expanded
        if norm(item)
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

    first_team = canonical_team(
        first
    )

    second_team = canonical_team(
        second
    )

    if (
        first_team
        and second_team
        and first_team == second_team
    ):
        return True

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
    """
    text = clean_text(value)

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
    Strongest matchup source first:

      1. explicit matchup
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
    Visible selection controls the sign of a spread.
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
    market = base_market(
        pick.get(
            "bet_type"
        )
    )

    if market == "SPREAD":
        visible = spread_line_from_selection(
            pick
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

    Game identity is included whenever available for ALL markets,
    including spreads.
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

    Card-level completeness remains x_ingest.py's responsibility.
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
