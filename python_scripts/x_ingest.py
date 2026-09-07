from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from common import (
    PICKS_FILE,
    STATE_FILE,
    load_json,
    normalize_picker,
    now_iso,
    save_json,
)


# ============================================================
# CONFIG
# ============================================================

X_API = "https://api.x.com/2"

USERNAME = os.getenv(
    "X_USERNAME",
    "barstoolpickem",
)

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.6-luna",
)

MAX_PARENT_DEPTH = 3

INITIAL_BACKFILL_DAYS = 10
INITIAL_BACKFILL_PAGES = 3

# Clean Week 1 state from before the bad historical reprocessing.
WEEK1_BASELINE_URL = (
    "https://raw.githubusercontent.com/"
    "joeyeick13/"
    "barstool-pickem-tracker/"
    "1402a56/"
    "data/picks.json"
)

# Changing this would cause the repair to run again.
# Leave it alone after this version is installed.
WEEK1_REPAIR_FLAG = "week1_reconciliation_v4_complete"


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


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    value = str(value or "")

    return (
        value
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
        .replace("&amp;", "&")
        .strip()
    )


def norm(value):
    value = clean_text(
        value
    ).lower()

    value = value.replace(
        "&",
        " and ",
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = re.sub(
        r"[^a-z0-9+.\-/'()@ ]",
        "",
        value,
    )

    return value.strip()


def safe_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except Exception:
        pass

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        str(value),
    )

    if not match:
        return None

    try:
        return float(
            match.group(0)
        )

    except Exception:
        return None


def stable_id(value):
    return (
        hashlib
        .sha1(
            str(value).encode(
                "utf-8"
            )
        )
        .hexdigest()[:16]
    )


def pick_week(pick):
    try:
        return int(
            pick.get("week")
            or 0
        )

    except Exception:
        return 0


# ============================================================
# TEAM NORMALIZATION
# ============================================================

TEAM_ALIASES = {
    "nd": "notre dame",
    "notre dame": "notre dame",

    "wis": "wisconsin",
    "wisc": "wisconsin",
    "wisconsin": "wisconsin",

    "byu": "byu",
    "brigham young": "byu",

    "utah tech": "utah tech",

    "wazzu": "washington state",
    "wsu": "washington state",
    "wash state": "washington state",
    "washington state": "washington state",

    "wash": "washington",
    "uw": "washington",
    "washington": "washington",

    "california": "cal",
    "cal": "cal",

    "ore": "oregon",
    "oregon": "oregon",

    "hou": "houston",
    "houston": "houston",

    "mem": "memphis",
    "memphis": "memphis",

    "iu": "indiana",
    "indiana": "indiana",

    "a and m": "texas a&m",
    "texas am": "texas a&m",
    "texas a and m": "texas a&m",
    "tamu": "texas a&m",
    "texas a&m": "texas a&m",

    "cmu": "central michigan",
    "central michigan": "central michigan",

    "ok st": "oklahoma state",
    "ok state": "oklahoma state",
    "oklahoma state": "oklahoma state",

    "usc": "usc",
    "ucla": "ucla",
}


def normalize_team(value):
    value = norm(value)

    return TEAM_ALIASES.get(
        value,
        value,
    )


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_bet_type(value):
    raw = str(
        value or "OTHER"
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


def normalize_side(value):
    value = clean_text(
        value
    )

    if value.upper() in {
        "OVER",
        "UNDER",
    }:
        return value.upper()

    return normalize_team(
        value
    )


# ============================================================
# MATCHUP NORMALIZATION
# ============================================================

def matchup_key(value):
    value = norm(value)

    if not value:
        return ""

    pieces = re.split(
        r"\s*@\s*"
        r"|\s*/\s*"
        r"|\s+vs\.?\s+"
        r"|\s+v\.?\s+"
        r"|\s+at\s+",
        value,
        flags=re.I,
    )

    pieces = [
        normalize_team(piece)
        for piece in pieces
        if normalize_team(piece)
    ]

    if len(pieces) >= 2:
        return "::".join(
            sorted(
                pieces[:2]
            )
        )

    return normalize_team(
        value
    )


def structured_matchup_key(pick):
    team = normalize_team(
        pick.get("team")
    )

    opponent = normalize_team(
        pick.get("opponent")
    )

    if team and opponent:
        return "::".join(
            sorted(
                [
                    team,
                    opponent,
                ]
            )
        )

    return matchup_key(
        pick.get("matchup")
    )


# ============================================================
# SELECTION HELPERS
# ============================================================

def spread_team_from_selection(selection):
    """
    Examples:

    BYU -51.5
    Utah Tech @ BYU / BYU -51.5
    Central Michigan +11.5
    Oklahoma 1Q -9.5
    """

    text = clean_text(
        selection
    )

    match = re.search(
        r"([A-Za-z0-9 .&'-]+?)"
        r"\s*[+-]\s*"
        r"\d+(?:\.\d+)?"
        r"\s*$",
        text,
    )

    if not match:
        return ""

    candidate = clean_text(
        match.group(1)
    )

    # If the selection contains a matchup, use only the final
    # portion immediately before the spread.
    for separator in [
        "/",
        "@",
        " vs ",
        " at ",
    ]:
        if separator in candidate.lower():
            pieces = re.split(
                re.escape(separator),
                candidate,
                flags=re.I,
            )

            candidate = (
                pieces[-1]
                .strip()
            )

    candidate = re.sub(
        r"\b(?:1q|1h|first quarter|first half)\b",
        "",
        candidate,
        flags=re.I,
    ).strip()

    return normalize_team(
        candidate
    )


def team_total_team_from_selection(selection):
    text = clean_text(
        selection
    )

    match = re.search(
        r"^(.+?)\s+"
        r"(?:tt|team\s+total)\b",
        text,
        flags=re.I,
    )

    if not match:
        return ""

    return normalize_team(
        match.group(1)
    )


# ============================================================
# CANONICAL WAGER KEY
# ============================================================

def canonical_pick_key(pick):
    """
    A duplicate is defined by the WAGER, not by the X post.

    source_post_id is deliberately excluded.

    Same wager + same picker + same week = one record.

    The same wager can still belong to two different pickers.
    """

    picker = (
        normalize_picker(
            pick.get("picker")
        )
        or ""
    )

    week = pick_week(
        pick
    )

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    side = normalize_side(
        pick.get("side")
    )

    line = safe_float(
        pick.get("line")
    )

    line_key = (
        ""
        if line is None
        else f"{line:.3f}"
    )

    team = normalize_team(
        pick.get("team")
    )

    opponent = normalize_team(
        pick.get("opponent")
    )

    selection = norm(
        pick.get("selection")
    )

    game = structured_matchup_key(
        pick
    )


    # --------------------------------------------------------
    # SPREAD
    # --------------------------------------------------------

    if bet_type in {
        "SPREAD",
        "FIRST_QUARTER_SPREAD",
        "FIRST_HALF_SPREAD",
    }:

        wager_team = (
            team
            or (
                side
                if side not in {
                    "OVER",
                    "UNDER",
                    "",
                }
                else ""
            )
            or spread_team_from_selection(
                pick.get("selection")
            )
        )

        if wager_team:
            return (
                picker,
                week,
                bet_type,
                wager_team,
                line_key,
            )


    # --------------------------------------------------------
    # TEAM TOTAL
    # --------------------------------------------------------

    if bet_type in {
        "TEAM_TOTAL",
        "FIRST_QUARTER_TEAM_TOTAL",
        "FIRST_HALF_TEAM_TOTAL",
    }:

        wager_team = (
            team
            or team_total_team_from_selection(
                pick.get("selection")
            )
        )

        if wager_team:
            return (
                picker,
                week,
                bet_type,
                wager_team,
                side,
                line_key,
            )


    # --------------------------------------------------------
    # GAME TOTAL
    # --------------------------------------------------------

    if bet_type in {
        "TOTAL",
        "FIRST_QUARTER_TOTAL",
        "FIRST_HALF_TOTAL",
    }:

        if game:
            return (
                picker,
                week,
                bet_type,
                game,
                side,
                line_key,
            )


    # --------------------------------------------------------
    # MONEYLINE
    # --------------------------------------------------------

    if bet_type in {
        "MONEYLINE",
        "FIRST_QUARTER_MONEYLINE",
        "FIRST_HALF_MONEYLINE",
    }:

        wager_team = (
            team
            or (
                side
                if side not in {
                    "OVER",
                    "UNDER",
                    "",
                }
                else ""
            )
        )

        if wager_team:
            return (
                picker,
                week,
                bet_type,
                wager_team,
            )


    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    return (
        picker,
        week,
        bet_type,
        selection,
        side,
        line_key,
    )


# ============================================================
# DEDUPE
# ============================================================

def pick_quality(pick):
    score = 0

    result = str(
        pick.get("result")
        or ""
    ).upper()

    if result in {
        "WIN",
        "LOSS",
        "PUSH",
    }:
        score += 100

    if pick.get(
        "event_id"
    ):
        score += 20

    if pick.get(
        "final_score"
    ):
        score += 15

    if pick.get(
        "matchup"
    ):
        score += 5

    if pick.get(
        "opponent"
    ):
        score += 3

    if pick.get(
        "team"
    ):
        score += 2

    return score


def dedupe_picks(picks):
    groups = {}

    for index, pick in enumerate(
        picks
    ):
        key = canonical_pick_key(
            pick
        )

        groups.setdefault(
            key,
            [],
        ).append(
            (
                index,
                pick,
            )
        )

    keep_indexes = set()
    removed = 0

    for key, members in groups.items():

        if len(members) == 1:
            keep_indexes.add(
                members[0][0]
            )

            continue

        best_index, best_pick = max(
            members,
            key=lambda item:
                (
                    pick_quality(
                        item[1]
                    ),
                    -item[0],
                )
        )

        keep_indexes.add(
            best_index
        )

        for index, duplicate in members:

            if index == best_index:
                continue

            removed += 1

            print(
                "DUPLICATE REMOVED:",
                duplicate.get(
                    "picker"
                ),
                "|",
                duplicate.get(
                    "selection"
                ),
                "| source:",
                duplicate.get(
                    "source_post_id"
                ),
            )

    cleaned = [
        pick
        for index, pick
        in enumerate(picks)
        if index in keep_indexes
    ]

    if removed:
        print(
            "Total duplicate wagers removed:",
            removed,
        )

    return cleaned


# ============================================================
# CONFIRMED WEEK 1 PICK DETECTORS
# ============================================================

def is_bigcat_alabama_tt(pick):
    if (
        normalize_picker(
            pick.get("picker")
        )
        != "Big Cat"
    ):
        return False

    if pick_week(
        pick
    ) != 1:
        return False

    line = safe_float(
        pick.get("line")
    )

    if (
        line is None
        or abs(
            line - 40.5
        ) > 0.01
    ):
        return False

    text = norm(
        pick.get("selection")
    )

    return (
        "alabama"
        in text
        and (
            "tt" in text
            or "team total" in text
        )
        and (
            "over" in text
            or normalize_side(
                pick.get("side")
            ) == "OVER"
        )
    )


def is_bigcat_central_michigan(pick):
    if (
        normalize_picker(
            pick.get("picker")
        )
        != "Big Cat"
    ):
        return False

    if pick_week(
        pick
    ) != 1:
        return False

    line = safe_float(
        pick.get("line")
    )

    if (
        line is None
        or abs(
            line - 11.5
        ) > 0.01
    ):
        return False

    text = norm(
        pick.get("selection")
    )

    return (
        "central michigan"
        in text
        or re.search(
            r"\bcmu\b",
            text,
        )
        is not None
    )


def best_matching_pick(
    picks,
    predicate,
):
    matches = [
        pick
        for pick in picks
        if predicate(
            pick
        )
    ]

    if not matches:
        return None

    return max(
        matches,
        key=pick_quality,
    )


# ============================================================
# RICO MISSING ND UNDER
# ============================================================

def rico_nd_under_exists(picks):
    for pick in picks:

        if (
            normalize_picker(
                pick.get("picker")
            )
            != "Rico Bosco"
        ):
            continue

        if pick_week(
            pick
        ) != 1:
            continue

        if (
            normalize_bet_type(
                pick.get("bet_type")
            )
            != "TOTAL"
        ):
            continue

        line = safe_float(
            pick.get("line")
        )

        if (
            line is None
            or abs(
                line - 46.5
            ) > 0.01
        ):
            continue

        if (
            normalize_side(
                pick.get("side")
            )
            != "UNDER"
        ):
            continue

        game = (
            structured_matchup_key(
                pick
            )
        )

        text = norm(
            pick.get("selection")
        )

        if (
            game
            == "notre dame::wisconsin"
            or (
                (
                    "wisconsin" in text
                    or re.search(
                        r"\bwis\b",
                        text,
                    )
                )
                and (
                    "notre dame" in text
                    or re.search(
                        r"\bnd\b",
                        text,
                    )
                )
            )
        ):
            return True

    return False


def make_rico_nd_under():
    source_post_id = (
        "2095584927836217769"
    )

    pick = {
        "picker":
            "Rico Bosco",

        "sport":
            "CFB",

        "matchup":
            "Wisconsin @ Notre Dame",

        "team":
            None,

        "opponent":
            None,

        "bet_type":
            "TOTAL",

        "selection":
            "WIS @ ND Under 46.5",

        "side":
            "UNDER",

        "line":
            46.5,

        "odds":
            None,

        "units":
            1.0,

        "mortal_lock":
            False,

        "week":
            1,

        "added_pick":
            False,

        "confidence":
            1.0,

        "status":
            "OPEN",

        "result":
            None,

        "profit_units":
            0,

        "source_post_id":
            source_post_id,

        "source_url":
            (
                "https://x.com/"
                "barstoolpickem/status/"
                f"{source_post_id}"
            ),

        "source_text":
            (
                "Official Rico Bosco "
                "Week 1 pick card"
            ),

        "source_is_reply":
            False,

        "conversation_id":
            source_post_id,

        "posted_at":
            None,

        "graded_at":
            None,

        "final_score":
            None,

        "event_id":
            None,
    }

    pick["id"] = stable_id(
        repr(
            canonical_pick_key(
                pick
            )
        )
    )

    return pick


# ============================================================
# ONE-TIME WEEK 1 RECONCILIATION
# ============================================================

def restore_week1_once(
    existing,
    state,
):
    """
    Repair the damage from the historical reprocessing run.

    Week 1 is rebuilt from the clean snapshot.

    We then preserve the two confirmed Big Cat picks that were
    discovered after that clean snapshot:

        Alabama TT Over 40.5
        Central Michigan +11.5

    Finally we add Rico's confirmed missing:

        WIS @ ND Under 46.5

    Week 2+ is never touched.
    """

    if state.get(
        WEEK1_REPAIR_FLAG
    ):
        return existing

    print()
    print(
        "===================================="
    )
    print(
        "ONE-TIME WEEK 1 RECONCILIATION"
    )
    print(
        "===================================="
    )

    # Pull these from the CURRENT dataset before replacing W1,
    # so we preserve their real X metadata/source IDs.
    alabama_pick = (
        best_matching_pick(
            existing,
            is_bigcat_alabama_tt,
        )
    )

    cmu_pick = (
        best_matching_pick(
            existing,
            is_bigcat_central_michigan,
        )
    )

    if not alabama_pick:
        print(
            "WARNING: confirmed Big Cat "
            "Alabama TT Over 40.5 row "
            "not found in current data."
        )

    if not cmu_pick:
        print(
            "WARNING: confirmed Big Cat "
            "Central Michigan +11.5 row "
            "not found in current data."
        )

    try:
        response = requests.get(
            WEEK1_BASELINE_URL,
            timeout=30,
        )

        response.raise_for_status()

        baseline = response.json()

        if not isinstance(
            baseline,
            list,
        ):
            raise RuntimeError(
                "Week 1 baseline was not a list"
            )

    except Exception as exc:
        print(
            "WEEK 1 BASELINE DOWNLOAD FAILED:"
        )

        print(
            type(exc).__name__,
            exc,
        )

        print(
            "Repair NOT marked complete. "
            "It will retry next run."
        )

        return existing


    # --------------------------------------------------------
    # KEEP ONLY CLEAN WEEK 1 FROM BASELINE
    # --------------------------------------------------------

    clean_week1 = [
        dict(pick)
        for pick in baseline
        if pick_week(
            pick
        ) == 1
    ]


    # --------------------------------------------------------
    # PRESERVE ALL CURRENT WEEK 2+ DATA
    # --------------------------------------------------------

    future_picks = [
        pick
        for pick in existing
        if pick_week(
            pick
        ) >= 2
    ]


    # --------------------------------------------------------
    # ADD THE TWO CONFIRMED BIG CAT REPLY PICKS
    # --------------------------------------------------------

    if alabama_pick:
        clean_week1.append(
            dict(
                alabama_pick
            )
        )

    if cmu_pick:
        clean_week1.append(
            dict(
                cmu_pick
            )
        )


    # --------------------------------------------------------
    # ADD RICO'S CONFIRMED MISSING CARD PICK
    # --------------------------------------------------------

    if not rico_nd_under_exists(
        clean_week1
    ):
        clean_week1.append(
            make_rico_nd_under()
        )

        print(
            "ADDED CONFIRMED MISSING PICK:",
            "Rico Bosco | "
            "WIS @ ND Under 46.5",
        )


    # --------------------------------------------------------
    # REMOVE ALL DUPLICATES
    # --------------------------------------------------------

    clean_week1 = dedupe_picks(
        clean_week1
    )

    repaired = (
        clean_week1
        + future_picks
    )

    repaired = dedupe_picks(
        repaired
    )


    # --------------------------------------------------------
    # MARK COMPLETE
    # --------------------------------------------------------

    state[
        WEEK1_REPAIR_FLAG
    ] = True

    # Retire all old historical recovery logic permanently.
    state[
        "reply_recovery_complete"
    ] = True

    state[
        "reply_only_recovery_v2_complete"
    ] = True

    state[
        "reply_only_recovery_v3_complete"
    ] = True

    print(
        "Clean Week 1 rows after repair:",
        len(clean_week1),
    )

    print(
        "Week 2+ rows preserved:",
        len(future_picks),
    )

    print(
        "Week 1 reconciliation complete."
    )

    return repaired


# ============================================================
# WEEK INFERENCE
# ============================================================

def explicit_week_from_text(text):
    match = re.search(
        r"\bweek\s*#?\s*"
        r"(\d{1,2})\b",
        str(text or ""),
        flags=re.I,
    )

    if not match:
        return None

    try:
        return int(
            match.group(1)
        )

    except Exception:
        return None


def infer_week_from_date(
    created_at
):
    if not created_at:
        return None

    try:
        dt = datetime.fromisoformat(
            str(created_at).replace(
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

    d = (
        dt
        .astimezone(
            timezone.utc
        )
        .date()
    )


    # --------------------------------------------------------
    # 2026 EXACT SCHEDULE
    # --------------------------------------------------------

    if d.year == 2026:

        if (
            date(
                2026,
                8,
                22,
            )
            <= d
            <= date(
                2026,
                9,
                7,
            )
        ):
            return 1

        if (
            date(
                2026,
                9,
                8,
            )
            <= d
            <= date(
                2026,
                9,
                13,
            )
        ):
            return 2

        week3_start = date(
            2026,
            9,
            14,
        )

        if d >= week3_start:
            return (
                3
                +
                (
                    d
                    - week3_start
                ).days
                // 7
            )

        return None


    # --------------------------------------------------------
    # FUTURE-SEASON FALLBACK
    #
    # Explicit "Week N" text always wins when available.
    #
    # Otherwise use Monday-based football weeks beginning around
    # the start of September.
    # --------------------------------------------------------

    september_1 = date(
        d.year,
        9,
        1,
    )

    first_monday = (
        september_1
        - timedelta(
            days=
                september_1.weekday()
        )
    )

    delta = (
        d
        - first_monday
    ).days

    if delta < -10:
        return None

    if delta < 0:
        return 1

    return (
        delta // 7
    ) + 1


def infer_week(
    text,
    created_at,
):
    explicit = (
        explicit_week_from_text(
            text
        )
    )

    if explicit:
        return explicit

    return infer_week_from_date(
        created_at
    )


# ============================================================
# X API
# ============================================================

def x_get(
    path,
    params=None,
):
    token = os.environ[
        "X_BEARER_TOKEN"
    ]

    response = requests.get(
        X_API + path,
        params=params or {},
        headers={
            "Authorization":
                f"Bearer {token}"
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def resolve_user_id():
    payload = x_get(
        f"/users/by/username/"
        f"{USERNAME}"
    )

    return str(
        payload["data"]["id"]
    )


def iso_x_time(dt):
    return (
        dt
        .astimezone(
            timezone.utc
        )
        .strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


# ============================================================
# FETCH OFFICIAL TIMELINE
# ============================================================

def fetch_user_posts(
    user_id,
    *,
    since_id=None,
    start_time=None,
    max_pages=1,
):
    """
    Fetch only posts authored by @barstoolpickem.

    Replies are INCLUDED.
    Retweets are excluded.

    We never use search/recent for historical recovery.
    """

    params = {
        "max_results":
            100,

        "exclude":
            "retweets",

        "tweet.fields": (
            "author_id,"
            "created_at,"
            "attachments,"
            "text,"
            "referenced_tweets,"
            "conversation_id,"
            "in_reply_to_user_id"
        ),

        "expansions":
            "attachments.media_keys",

        "media.fields": (
            "media_key,"
            "type,"
            "url,"
            "preview_image_url"
        ),
    }

    if since_id:
        params[
            "since_id"
        ] = str(
            since_id
        )

    if start_time:
        params[
            "start_time"
        ] = iso_x_time(
            start_time
        )

    posts = []
    media_map = {}

    pages = 0

    while True:

        payload = x_get(
            f"/users/"
            f"{user_id}/tweets",
            params,
        )

        for post in payload.get(
            "data",
            [],
        ):

            # Absolute safety:
            # never ingest fan/third-party content.
            if (
                str(
                    post.get(
                        "author_id"
                    )
                    or ""
                )
                != str(
                    user_id
                )
            ):
                print(
                    "REJECTED NON-OFFICIAL POST:",
                    post.get(
                        "id"
                    ),
                )

                continue

            posts.append(
                post
            )

        for media in (
            payload
            .get(
                "includes",
                {},
            )
            .get(
                "media",
                [],
            )
        ):
            key = media.get(
                "media_key"
            )

            if key:
                media_map[
                    key
                ] = media

        pages += 1

        next_token = (
            payload
            .get(
                "meta",
                {},
            )
            .get(
                "next_token"
            )
        )

        if not next_token:
            break

        if pages >= max_pages:
            break

        params[
            "pagination_token"
        ] = next_token

    return (
        posts,
        media_map,
    )


# ============================================================
# SINGLE POST FETCH
# ============================================================

def fetch_post_by_id(post_id):
    payload = x_get(
        f"/tweets/{post_id}",
        {
            "tweet.fields": (
                "author_id,"
                "created_at,"
                "attachments,"
                "text,"
                "referenced_tweets,"
                "conversation_id,"
                "in_reply_to_user_id"
            ),

            "expansions":
                "attachments.media_keys",

            "media.fields": (
                "media_key,"
                "type,"
                "url,"
                "preview_image_url"
            ),
        },
    )

    post = payload.get(
        "data"
    )

    media_map = {}

    for media in (
        payload
        .get(
            "includes",
            {},
        )
        .get(
            "media",
            [],
        )
    ):
        key = media.get(
            "media_key"
        )

        if key:
            media_map[
                key
            ] = media

    return (
        post,
        media_map,
    )


# ============================================================
# MEDIA
# ============================================================

def image_urls_for_post(
    post,
    media_map,
):
    urls = []

    keys = (
        post
        .get(
            "attachments",
            {},
        )
        .get(
            "media_keys",
            [],
        )
    )

    for key in keys:

        media = media_map.get(
            key,
            {},
        )

        if (
            media.get("type")
            == "photo"
        ):
            url = media.get(
                "url"
            )

        else:
            url = media.get(
                "preview_image_url"
            )

        if (
            url
            and url not in urls
        ):
            urls.append(
                url
            )

    return urls


# ============================================================
# REPLY SUPPORT
# ============================================================

def replied_to_post_id(post):
    for reference in (
        post.get(
            "referenced_tweets"
        )
        or []
    ):
        if (
            reference.get("type")
            == "replied_to"
        ):
            value = reference.get(
                "id"
            )

            if value:
                return str(
                    value
                )

    return None


def is_reply_post(post):
    return bool(
        replied_to_post_id(
            post
        )
        or post.get(
            "in_reply_to_user_id"
        )
    )


def official_parent_context(
    post,
    official_user_id,
    cache,
):
    """
    Parent text is context only.

    It can identify the picker for an official reply.

    It may NEVER create picks itself.

    If the parent is not @barstoolpickem, we stop immediately.
    """

    current_id = (
        replied_to_post_id(
            post
        )
    )

    pieces = []
    depth = 0

    while (
        current_id
        and depth
        < MAX_PARENT_DEPTH
    ):
        depth += 1

        if current_id in cache:
            parent = cache[
                current_id
            ]

        else:

            try:
                parent, _ = (
                    fetch_post_by_id(
                        current_id
                    )
                )

            except Exception as exc:
                print(
                    "PARENT FETCH FAILED:",
                    current_id,
                    type(exc).__name__,
                    exc,
                )

                cache[
                    current_id
                ] = None

                break

            cache[
                current_id
            ] = parent

        if not parent:
            break

        # Fan parent = no context.
        if (
            str(
                parent.get(
                    "author_id"
                )
                or ""
            )
            != str(
                official_user_id
            )
        ):
            break

        parent_text = str(
            parent.get(
                "text"
            )
            or ""
        ).strip()

        if parent_text:
            pieces.append(
                parent_text
            )

        current_id = (
            replied_to_post_id(
                parent
            )
        )

    return "\n\n".join(
        pieces
    )


# ============================================================
# PICKER HINTS
# ============================================================

def picker_hint_from_text(text):
    text = str(
        text or ""
    ).lower()

    if (
        "barstoolbigcat" in text
        or "big cat" in text
        or "bigcat" in text
    ):
        return "Big Cat"

    if (
        "stoolpresidente" in text
        or "stool presidente" in text
        or "dave portnoy" in text
        or "portnoy" in text
        or "el pres" in text
    ):
        return (
            "Stool Presidente"
        )

    if (
        "rico bosco" in text
        or "ricobosco" in text
        or "returnofrb" in text
        or re.search(
            r"\brico\b",
            text,
        )
    ):
        return "Rico Bosco"

    return None


def is_added_post(text):
    text = str(
        text or ""
    ).lower()

    phrases = [
        "adds for",
        "add for",
        "added pick",
        "adding",
        "addition",
        "another one",
    ]

    return any(
        phrase in text
        for phrase in phrases
    )


# ============================================================
# PICK-POST FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    # Images are eligible because weekly card posts are images.
    if image_urls:
        return True

    text = str(
        text or ""
    ).lower()

    keywords = [
        "over ",
        "under ",
        "moneyline",
        " ml",
        "team total",
        " tt ",
        "1q",
        "1h",
        "first quarter",
        "first half",
        "adds for",
        "add for",
    ]

    if any(
        keyword in text
        for keyword in keywords
    ):
        return True

    # Spread-like wager.
    if re.search(
        r"[a-z][a-z .&'-]{1,40}"
        r"\s[+-]\d+(?:\.\d+)?",
        text,
        flags=re.I,
    ):
        return True

    return False


# ============================================================
# DETERMINISTIC MARKET REPAIR
# ============================================================

def normalize_extracted_market(pick):
    pick = dict(
        pick
    )

    selection = clean_text(
        pick.get("selection")
    )

    text = selection.lower()

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    first_quarter = bool(
        re.search(
            r"\b(?:"
            r"1q|"
            r"first quarter|"
            r"1st quarter"
            r")\b",
            text,
            flags=re.I,
        )
    )

    first_half = bool(
        re.search(
            r"\b(?:"
            r"1h|"
            r"first half|"
            r"1st half"
            r")\b",
            text,
            flags=re.I,
        )
    )

    team_total = bool(
        re.search(
            r"\b(?:"
            r"tt|"
            r"team total"
            r")\b",
            text,
            flags=re.I,
        )
    )


    # --------------------------------------------------------
    # TEAM TOTAL
    # --------------------------------------------------------

    if team_total:

        if first_quarter:
            bet_type = (
                "FIRST_QUARTER_TEAM_TOTAL"
            )

        elif first_half:
            bet_type = (
                "FIRST_HALF_TEAM_TOTAL"
            )

        else:
            bet_type = (
                "TEAM_TOTAL"
            )

        compact = re.search(
            r"\b(?:tt|team total)"
            r"\s*(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        verbose = re.search(
            r"\b(?:tt|team total)"
            r"\s*(over|under)\s*"
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

        elif verbose:

            pick["side"] = (
                verbose
                .group(1)
                .upper()
            )

            pick["line"] = float(
                verbose.group(2)
            )


    # --------------------------------------------------------
    # FIRST QUARTER / FIRST HALF
    # --------------------------------------------------------

    elif (
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
            flags=re.I,
        ):
            bet_type = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:ml|moneyline)\b",
            text,
            flags=re.I,
        ):
            bet_type = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*"
            r"\d+(?:\.\d+)?",
            text,
        ):
            bet_type = (
                f"{prefix}_SPREAD"
            )


    # --------------------------------------------------------
    # NORMAL TOTAL
    # --------------------------------------------------------

    else:

        total = re.search(
            r"\b(over|under)"
            r"\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if total:

            if bet_type == "OTHER":
                bet_type = "TOTAL"

            pick["side"] = (
                total
                .group(1)
                .upper()
            )

            pick["line"] = float(
                total.group(2)
            )


    pick["bet_type"] = (
        normalize_bet_type(
            bet_type
        )
    )

    return pick


# ============================================================
# OPENAI EXTRACTION
# ============================================================

def parse_post_with_ai(
    *,
    text,
    image_urls,
    post_url,
    posted_at,
    inferred_week,
    picker_hint,
    reply_hint,
    parent_text,
):
    from openai import OpenAI

    client = OpenAI()

    prompt = f"""
You extract NCAA college football gambling picks from the
official @barstoolpickem X account.

TRACK ONLY:
- Big Cat
- Stool Presidente / Dave Portnoy / El Pres
- Rico Bosco


ACTUAL SOURCE POST
==================

URL:
{post_url}

POSTED:
{posted_at}

TEXT:
{text}

VERIFIED OFFICIAL PARENT CONTEXT:
{parent_text or "None"}

PICKER HINT:
{picker_hint or "Unknown"}

DEFAULT WEEK:
{inferred_week}

IS OFFICIAL REPLY:
{reply_hint}


NON-NEGOTIABLE RULES
====================

1. Only wagers contained in the ACTUAL SOURCE POST may create
   new picks.

2. Parent context is used ONLY to identify the picker or explain
   that a reply is an added pick.

3. Never extract a parent wager while parsing its child reply.

4. Never use fan replies or fan text.

5. Read ALL attached images.

6. Extract EVERY wager visible on the weekly card.

7. Each bullet belongs to the closest matchup heading above it.

8. Pay special attention to the final bullet on each card.

9. Do not treat a printed historical "Record" on a card as picks.

10. Do not treat kickoff times or final scores as betting lines.

11. Same wager appearing in two official posts is okay. Extract
    it normally; the program deduplicates it.

12. The same wager may legitimately belong to DIFFERENT pickers.


CARD SHORTHAND
==============

Examples:

BYU -51.5
=> SPREAD

WIS @ ND Under 46.5
=> TOTAL / UNDER / 46.5

Oregon TT o37.5
=> TEAM_TOTAL / OVER / 37.5

Alabama TT over 40.5
=> TEAM_TOTAL / OVER / 40.5

Oklahoma 1Q -9.5
=> FIRST_QUARTER_SPREAD

Miami 1H -13.5
=> FIRST_HALF_SPREAD

Indiana first half TT over 27.5
=> FIRST_HALF_TEAM_TOTAL / OVER / 27.5

OSU @ HOU Over 49.5
=> TOTAL

OSU is ambiguous without opponent/matchup context.

A&M may be Texas A&M when the source context makes that clear.


VERY IMPORTANT IMAGE RULE
=========================

A card may visually look like:

WIS @ ND 7:30pm SUN
• Under 46.5

The word "Under" may be small, handwritten, faint, or positioned
away from the number.

Inspect the image carefully.

Do not omit a wager merely because the direction is visually
separated from the numeric line.

But if the direction is genuinely unreadable, do NOT invent it.


OUTPUT
======

Return JSON only:

{{
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "matchup or null",
      "team": "wagered team or null",
      "opponent": "opponent or null",
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
      "selection": "concise exact wager",
      "side": "OVER|UNDER|team name|null",
      "line": 0.0,
      "odds": null,
      "units": 1.0,
      "week": {inferred_week if inferred_week is not None else "null"},
      "added_pick": false,
      "confidence": 0.99
    }}
  ]
}}

Confidence is 0 to 1.

No markdown.
No explanation.
JSON only.
"""

    content = [
        {
            "type":
                "input_text",

            "text":
                prompt,
        }
    ]

    for image_url in image_urls:
        content.append(
            {
                "type":
                    "input_image",

                "image_url":
                    image_url,
            }
        )

    response = (
        client.responses.create(
            model=OPENAI_MODEL,

            input=[
                {
                    "role":
                        "user",

                    "content":
                        content,
                }
            ],
        )
    )

    raw = str(
        getattr(
            response,
            "output_text",
            "",
        )
        or ""
    ).strip()

    raw = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw,
        flags=re.I,
    )

    raw = re.sub(
        r"\s*```$",
        "",
        raw,
    )

    try:
        payload = json.loads(
            raw
        )

    except Exception:

        start = raw.find(
            "{"
        )

        end = raw.rfind(
            "}"
        )

        if (
            start < 0
            or end <= start
        ):
            raise

        payload = json.loads(
            raw[
                start:
                end + 1
            ]
        )

    picks = payload.get(
        "picks",
        [],
    )

    if not isinstance(
        picks,
        list,
    ):
        raise ValueError(
            "OpenAI returned picks "
            "in an invalid format"
        )

    return [
        normalize_extracted_market(
            pick
        )
        for pick in picks
    ]


# ============================================================
# NORMALIZE AI PICK
# ============================================================

def normalize_ai_pick(
    raw_pick,
    *,
    default_week,
    picker_hint,
):
    pick = dict(
        raw_pick or {}
    )

    picker = normalize_picker(
        pick.get("picker")
    )

    if not picker:
        picker = normalize_picker(
            picker_hint
        )

    if picker not in {
        "Big Cat",
        "Stool Presidente",
        "Rico Bosco",
    }:
        return None

    selection = clean_text(
        pick.get("selection")
    )

    if not selection:
        return None

    pick["picker"] = picker

    pick["selection"] = (
        selection
    )

    try:
        week = int(
            pick.get("week")
            or default_week
        )

    except Exception:
        week = default_week

    if not week:
        return None

    pick["week"] = week

    pick["bet_type"] = (
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    pick["line"] = safe_float(
        pick.get("line")
    )

    side = pick.get(
        "side"
    )

    if side is not None:

        if (
            str(side).upper()
            in {
                "OVER",
                "UNDER",
            }
        ):
            pick["side"] = (
                str(side).upper()
            )

        else:
            pick["side"] = (
                clean_text(
                    side
                )
            )

    try:
        pick["confidence"] = (
            float(
                pick.get(
                    "confidence"
                )
                or 0.95
            )
        )

    except Exception:
        pick["confidence"] = (
            0.95
        )

    return pick


# ============================================================
# CREATE STORED ROW
# ============================================================

def stored_pick_from_ai(
    extracted,
    *,
    post,
    post_url,
    reply_hint,
    added_hint,
):
    bet_type = normalize_bet_type(
        extracted.get(
            "bet_type"
        )
    )

    confidence = float(
        extracted.get(
            "confidence"
        )
        or 0.95
    )

    if (
        confidence >= 0.90
        and bet_type
        in SUPPORTED_MARKETS
    ):
        status = "OPEN"

    else:
        status = "REVIEW"

    pick = {
        "picker":
            extracted[
                "picker"
            ],

        "sport":
            extracted.get(
                "sport"
            )
            or "CFB",

        "matchup":
            extracted.get(
                "matchup"
            ),

        "team":
            extracted.get(
                "team"
            ),

        "opponent":
            extracted.get(
                "opponent"
            ),

        "bet_type":
            bet_type,

        "selection":
            clean_text(
                extracted.get(
                    "selection"
                )
            ),

        "side":
            extracted.get(
                "side"
            ),

        "line":
            safe_float(
                extracted.get(
                    "line"
                )
            ),

        "odds":
            extracted.get(
                "odds"
            ),

        "units":
            float(
                extracted.get(
                    "units"
                )
                or 1
            ),

        "mortal_lock":
            False,

        "week":
            int(
                extracted[
                    "week"
                ]
            ),

        "added_pick":
            bool(
                extracted.get(
                    "added_pick"
                )
                or added_hint
            ),

        "confidence":
            confidence,

        "status":
            status,

        "result":
            None,

        "profit_units":
            0,

        "source_post_id":
            str(
                post.get(
                    "id"
                )
            ),

        "source_url":
            post_url,

        "source_text":
            str(
                post.get(
                    "text"
                )
                or ""
            ),

        "source_is_reply":
            bool(
                reply_hint
            ),

        "conversation_id":
            post.get(
                "conversation_id"
            ),

        "posted_at":
            post.get(
                "created_at"
            ),

        "graded_at":
            None,

        "final_score":
            None,

        "event_id":
            None,
    }

    pick["id"] = stable_id(
        repr(
            canonical_pick_key(
                pick
            )
        )
    )

    return pick


# ============================================================
# PROCESSED POST STATE
# ============================================================

def initialize_processed_ids(
    existing,
    state,
):
    """
    On first install of this simplified ingest, mark every
    existing source post as already handled.

    This prevents old cards/replies from being parsed again.
    """

    if state.get(
        "processed_ids_initialized_v2"
    ):
        return

    ids = {
        str(
            pick.get(
                "source_post_id"
            )
        )
        for pick in existing
        if pick.get(
            "source_post_id"
        )
    }

    state[
        "processed_post_ids"
    ] = sorted(
        ids,
        key=lambda value:
            int(value)
            if str(value).isdigit()
            else 0,
    )[-1000:]

    state[
        "processed_ids_initialized_v2"
    ] = True

    print(
        "Seeded processed post IDs:",
        len(ids),
    )


# ============================================================
# RETRY QUEUE
# ============================================================

def fetch_retry_posts(
    state,
    official_user_id,
):
    failed_ids = list(
        state.get(
            "failed_post_ids",
            [],
        )
        or []
    )

    posts = []
    media_map = {}

    still_failed = []

    for post_id in failed_ids:

        try:
            post, media = (
                fetch_post_by_id(
                    post_id
                )
            )

        except Exception as exc:

            print(
                "RETRY FETCH FAILED:",
                post_id,
                type(exc).__name__,
                exc,
            )

            still_failed.append(
                post_id
            )

            continue

        if not post:
            still_failed.append(
                post_id
            )

            continue

        if (
            str(
                post.get(
                    "author_id"
                )
                or ""
            )
            != str(
                official_user_id
            )
        ):
            continue

        posts.append(
            post
        )

        media_map.update(
            media
        )

    state[
        "failed_post_ids"
    ] = still_failed

    return (
        posts,
        media_map,
    )


# ============================================================
# PROCESS NEW POSTS
# ============================================================

def process_posts(
    existing,
    posts,
    media_map,
    official_user_id,
    state,
):
    parent_cache = {}

    seen_wagers = {
        canonical_pick_key(
            pick
        )
        for pick in existing
    }

    processed_ids = set(
        str(value)
        for value in (
            state.get(
                "processed_post_ids",
                [],
            )
            or []
        )
    )

    failed_ids = set(
        str(value)
        for value in (
            state.get(
                "failed_post_ids",
                [],
            )
            or []
        )
    )

    candidate_count = 0
    new_pick_count = 0

    for post in sorted(
        posts,
        key=lambda item:
            int(
                item.get(
                    "id"
                )
                or 0
            ),
    ):

        post_id = str(
            post.get(
                "id"
            )
            or ""
        )

        if not post_id:
            continue


        # ----------------------------------------------------
        # OFFICIAL ACCOUNT ONLY
        # ----------------------------------------------------

        if (
            str(
                post.get(
                    "author_id"
                )
                or ""
            )
            != str(
                official_user_id
            )
        ):
            continue


        # ----------------------------------------------------
        # DO NOT REPARSE SUCCESSFULLY HANDLED POSTS
        # ----------------------------------------------------

        if (
            post_id
            in processed_ids
            and post_id
            not in failed_ids
        ):
            continue


        image_urls = (
            image_urls_for_post(
                post,
                media_map,
            )
        )

        text = str(
            post.get(
                "text"
            )
            or ""
        )


        # ----------------------------------------------------
        # NOT A PICK POST
        # ----------------------------------------------------

        if not looks_like_pick_post(
            text,
            image_urls,
        ):

            processed_ids.add(
                post_id
            )

            failed_ids.discard(
                post_id
            )

            continue


        candidate_count += 1


        # ----------------------------------------------------
        # REPLY CONTEXT
        # ----------------------------------------------------

        reply_hint = (
            is_reply_post(
                post
            )
        )

        parent_text = ""

        if reply_hint:
            parent_text = (
                official_parent_context(
                    post,
                    official_user_id,
                    parent_cache,
                )
            )


        # ----------------------------------------------------
        # PICKER
        # ----------------------------------------------------

        picker_hint = (
            picker_hint_from_text(
                text
            )
        )

        if (
            not picker_hint
            and parent_text
        ):
            picker_hint = (
                picker_hint_from_text(
                    parent_text
                )
            )


        # ----------------------------------------------------
        # WEEK
        # ----------------------------------------------------

        week = infer_week(
            text,
            post.get(
                "created_at"
            ),
        )


        # ----------------------------------------------------
        # ADDED PICK
        # ----------------------------------------------------

        added_hint = (
            is_added_post(
                text
            )
        )

        if (
            reply_hint
            and parent_text
            and is_added_post(
                parent_text
            )
        ):
            added_hint = True


        post_url = (
            "https://x.com/"
            f"{USERNAME}/status/"
            f"{post_id}"
        )


        print(
            "PARSING:",
            post_id,
            "| week:",
            week,
            "| reply:",
            reply_hint,
            "| picker:",
            picker_hint,
        )


        # ----------------------------------------------------
        # AI EXTRACTION
        # ----------------------------------------------------

        try:
            raw_picks = (
                parse_post_with_ai(
                    text=text,
                    image_urls=
                        image_urls,
                    post_url=
                        post_url,
                    posted_at=
                        post.get(
                            "created_at"
                        ),
                    inferred_week=
                        week,
                    picker_hint=
                        picker_hint,
                    reply_hint=
                        reply_hint,
                    parent_text=
                        parent_text,
                )
            )

        except Exception as exc:

            print(
                "PARSE FAILED — QUEUED FOR RETRY:",
                post_id,
                type(exc).__name__,
                exc,
            )

            failed_ids.add(
                post_id
            )

            continue


        # ----------------------------------------------------
        # SUCCESSFULLY PARSED POST
        # ----------------------------------------------------

        failed_ids.discard(
            post_id
        )

        processed_ids.add(
            post_id
        )


        for raw_pick in raw_picks:

            extracted = (
                normalize_ai_pick(
                    raw_pick,
                    default_week=
                        week,
                    picker_hint=
                        picker_hint,
                )
            )

            if not extracted:
                continue


            # ------------------------------------------------
            # DEDUPE BEFORE STORE
            # ------------------------------------------------

            key = canonical_pick_key(
                extracted
            )

            if key in seen_wagers:

                print(
                    "DUPLICATE SKIPPED:",
                    extracted.get(
                        "picker"
                    ),
                    "|",
                    extracted.get(
                        "selection"
                    ),
                    "| source:",
                    post_id,
                )

                continue


            stored = (
                stored_pick_from_ai(
                    extracted,
                    post=post,
                    post_url=
                        post_url,
                    reply_hint=
                        reply_hint,
                    added_hint=
                        added_hint,
                )
            )

            existing.append(
                stored
            )

            seen_wagers.add(
                key
            )

            new_pick_count += 1

            print(
                "ADDED:",
                stored.get(
                    "picker"
                ),
                "| Week",
                stored.get(
                    "week"
                ),
                "|",
                stored.get(
                    "selection"
                ),
            )


    # Keep state files from growing forever.
    state[
        "processed_post_ids"
    ] = sorted(
        processed_ids,
        key=lambda value:
            int(value)
            if str(value).isdigit()
            else 0,
    )[-1000:]

    state[
        "failed_post_ids"
    ] = sorted(
        failed_ids,
        key=lambda value:
            int(value)
            if str(value).isdigit()
            else 0,
    )[-100:]

    return (
        existing,
        candidate_count,
        new_pick_count,
    )


# ============================================================
# AUDIT
# ============================================================

def print_week_audit(
    picks,
    week,
):
    print()
    print(
        f"========== WEEK {week} AUDIT =========="
    )

    for picker in [
        "Rico Bosco",
        "Big Cat",
        "Stool Presidente",
    ]:

        rows = [
            pick
            for pick in picks
            if (
                normalize_picker(
                    pick.get(
                        "picker"
                    )
                )
                == picker
                and pick_week(
                    pick
                )
                == week
            )
        ]

        wins = sum(
            1
            for pick in rows
            if (
                str(
                    pick.get(
                        "result"
                    )
                    or ""
                ).upper()
                == "WIN"
            )
        )

        losses = sum(
            1
            for pick in rows
            if (
                str(
                    pick.get(
                        "result"
                    )
                    or ""
                ).upper()
                == "LOSS"
            )
        )

        pushes = sum(
            1
            for pick in rows
            if (
                str(
                    pick.get(
                        "result"
                    )
                    or ""
                ).upper()
                == "PUSH"
            )
        )

        pending = (
            len(rows)
            - wins
            - losses
            - pushes
        )

        print(
            picker,
            "| picks:",
            len(rows),
            "| record:",
            f"{wins}-{losses}",
            "| pushes:",
            pushes,
            "| pending:",
            pending,
        )

    print(
        "===================================="
    )


# ============================================================
# MAIN
# ============================================================

def ingest():

    print()
    print(
        "===================================="
    )
    print(
        "BARSTOOL PICK EM INGEST"
    )
    print(
        "===================================="
    )


    state = load_json(
        STATE_FILE,
        {},
    )

    existing = load_json(
        PICKS_FILE,
        [],
    )

    if not isinstance(
        existing,
        list,
    ):
        existing = []


    # ========================================================
    # 1. REPAIR WEEK 1 ONCE
    # ========================================================

    existing = restore_week1_once(
        existing,
        state,
    )

    existing = dedupe_picks(
        existing
    )


    # ========================================================
    # 2. SEED PROCESSED IDS
    #
    # Everything already in the database is considered handled.
    # No old reply recovery.
    # ========================================================

    initialize_processed_ids(
        existing,
        state,
    )


    # ========================================================
    # 3. RESOLVE OFFICIAL X ACCOUNT
    # ========================================================

    official_user_id = (
        resolve_user_id()
    )

    print(
        "Official account:",
        USERNAME,
        "| user ID:",
        official_user_id,
    )


    # ========================================================
    # 4. RETRY ONLY POSTS THAT PREVIOUSLY FAILED
    # ========================================================

    retry_posts, retry_media = (
        fetch_retry_posts(
            state,
            official_user_id,
        )
    )


    # ========================================================
    # 5. FETCH NEW OFFICIAL POSTS ONLY
    # ========================================================

    last_x_post_id = state.get(
        "last_x_post_id"
    )

    if last_x_post_id:

        timeline_posts, timeline_media = (
            fetch_user_posts(
                official_user_id,
                since_id=
                    last_x_post_id,
                max_pages=2,
            )
        )

    else:

        # Safety fallback only for a completely fresh state.
        timeline_posts, timeline_media = (
            fetch_user_posts(
                official_user_id,
                start_time=(
                    datetime.now(
                        timezone.utc
                    )
                    - timedelta(
                        days=
                            INITIAL_BACKFILL_DAYS
                    )
                ),
                max_pages=
                    INITIAL_BACKFILL_PAGES,
            )
        )


    # ========================================================
    # 6. COMBINE RETRIES + NEW POSTS
    # ========================================================

    post_map = {}

    for post in (
        retry_posts
        + timeline_posts
    ):

        post_id = str(
            post.get(
                "id"
            )
            or ""
        )

        if post_id:
            post_map[
                post_id
            ] = post

    combined_posts = list(
        post_map.values()
    )

    combined_media = dict(
        retry_media
    )

    combined_media.update(
        timeline_media
    )


    # ========================================================
    # 7. PROCESS
    # ========================================================

    (
        existing,
        candidate_count,
        new_pick_count,
    ) = process_posts(
        existing,
        combined_posts,
        combined_media,
        official_user_id,
        state,
    )


    # ========================================================
    # 8. FINAL DATABASE DEDUPE
    # ========================================================

    existing = dedupe_picks(
        existing
    )


    # ========================================================
    # 9. ADVANCE NORMAL TIMELINE CURSOR
    #
    # Failed parse IDs remain in failed_post_ids and get retried,
    # so advancing the cursor cannot permanently lose them.
    # ========================================================

    timeline_ids = [
        int(
            post.get(
                "id"
            )
        )
        for post in timeline_posts
        if (
            post.get(
                "id"
            )
            and str(
                post.get(
                    "id"
                )
            ).isdigit()
        )
    ]

    if timeline_ids:

        newest_id = str(
            max(
                timeline_ids
            )
        )

        old_id = state.get(
            "last_x_post_id"
        )

        if (
            not old_id
            or int(
                newest_id
            )
            > int(
                old_id
            )
        ):
            state[
                "last_x_post_id"
            ] = newest_id


    # ========================================================
    # 10. SAVE
    # ========================================================

    state[
        "updated_at"
    ] = now_iso()

    save_json(
        PICKS_FILE,
        existing,
    )

    save_json(
        STATE_FILE,
        state,
    )


    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print(
        "========== INGEST SUMMARY =========="
    )

    print(
        "New official timeline posts:",
        len(
            timeline_posts
        ),
    )

    print(
        "Retry posts:",
        len(
            retry_posts
        ),
    )

    print(
        "Candidate pick posts parsed:",
        candidate_count,
    )

    print(
        "New wagers added:",
        new_pick_count,
    )

    print(
        "Failed posts queued for retry:",
        len(
            state.get(
                "failed_post_ids",
                [],
            )
        ),
    )

    print(
        "Total stored wagers:",
        len(
            existing
        ),
    )

    print(
        "Last X post ID:",
        state.get(
            "last_x_post_id"
        ),
    )

    print(
        "Week 1 reconciliation:",
        state.get(
            WEEK1_REPAIR_FLAG
        ),
    )

    print(
        "===================================="
    )

    print_week_audit(
        existing,
        1,
    )


if __name__ == "__main__":
    ingest()
