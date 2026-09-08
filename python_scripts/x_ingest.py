from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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

PACIFIC = ZoneInfo(
    "America/Los_Angeles"
)

TRACKED_PICKERS = {
    "Big Cat",
    "Rico Bosco",
    "Stool Presidente",
}

MAX_PARENT_DEPTH = 3

INITIAL_BACKFILL_DAYS = 10
INITIAL_BACKFILL_PAGES = 3

# Tuesday result reconciliation looks back several days so it
# can recover the complete official standings thread.
STANDINGS_LOOKBACK_DAYS = 5
STANDINGS_MAX_PAGES = 4

PROCESSED_IDS_FLAG = (
    "processed_ids_initialized_v2"
)

OFFICIAL_RESULT_STATE_KEY = (
    "official_result_threads_processed"
)


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
# CANONICAL PICK KEY
# ============================================================

def canonical_pick_key(pick):
    """
    Same picker + same week + same actual wager = same pick.

    Source post ID is intentionally excluded so an official
    repost/reply does not create a duplicate wager.
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

    selection = norm(
        pick.get("selection")
    )

    game = structured_matchup_key(
        pick
    )

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
                    "",
                    "OVER",
                    "UNDER",
                }
                else ""
            )
        )

        return (
            picker,
            week,
            bet_type,
            wager_team,
            line_key,
            selection
            if not wager_team
            else "",
        )

    if bet_type in {
        "TEAM_TOTAL",
        "FIRST_QUARTER_TEAM_TOTAL",
        "FIRST_HALF_TEAM_TOTAL",
    }:

        return (
            picker,
            week,
            bet_type,
            team,
            side,
            line_key,
        )

    if bet_type in {
        "TOTAL",
        "FIRST_QUARTER_TOTAL",
        "FIRST_HALF_TOTAL",
    }:

        return (
            picker,
            week,
            bet_type,
            game,
            side,
            line_key,
        )

    if bet_type in {
        "MONEYLINE",
        "FIRST_QUARTER_MONEYLINE",
        "FIRST_HALF_MONEYLINE",
    }:

        return (
            picker,
            week,
            bet_type,
            team or side,
        )

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
        "official_reconciled"
    ):
        score += 1000

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
        "team"
    ):
        score += 3

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

    for _, members in groups.items():

        if len(members) == 1:

            keep_indexes.add(
                members[0][0]
            )

            continue

        best_index, _ = max(
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
            )

    cleaned = [
        pick
        for index, pick
        in enumerate(picks)
        if index in keep_indexes
    ]

    if removed:

        print(
            "Duplicate wagers removed:",
            removed,
        )

    return cleaned


# ============================================================
# WEEK INFERENCE
# ============================================================

def explicit_week_from_text(text):
    match = re.search(
        r"\bweek\s*#?\s*(\d{1,2})\b",
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


def infer_week_from_date(created_at):
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
        .astimezone(PACIFIC)
        .date()
    )


    # --------------------------------------------------------
    # 2026 VERIFIED WINDOWS
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


def standings_target_week(
    root_post
):
    """
    Tuesday standings settle the week that just finished.

    Example:
    Tuesday during Week 2 -> official results for Week 1.
    """

    current_week = infer_week(
        root_post.get("text"),
        root_post.get(
            "created_at"
        ),
    )

    if not current_week:
        return None

    return max(
        1,
        current_week - 1,
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
# FETCH OFFICIAL ACCOUNT POSTS
# ============================================================

def fetch_user_posts(
    user_id,
    *,
    since_id=None,
    start_time=None,
    max_pages=1,
):

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
# THREAD / REPLY HELPERS
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


        # Never use fan / outside account parent context.
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
        "barstoolbigcat"
        in text
        or "big cat"
        in text
        or "bigcat"
        in text
    ):

        return "Big Cat"

    if (
        "stoolpresidente"
        in text
        or "stool presidente"
        in text
        or "dave portnoy"
        in text
        or "portnoy"
        in text
        or "el pres"
        in text
    ):

        return (
            "Stool Presidente"
        )

    if (
        "return_of_rb"
        in text
        or "returnofrb"
        in text
        or "rico bosco"
        in text
        or "ricobosco"
        in text
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
# PAT HILL STANDINGS DETECTION
# ============================================================

def is_pat_hill_text(text):

    return (
        "pat hill standings"
        in str(
            text or ""
        ).lower()
    )


def is_standings_context(
    text,
    parent_text="",
):

    combined = (
        str(text or "")
        + "\n"
        + str(parent_text or "")
    )

    return is_pat_hill_text(
        combined
    )


def should_scan_standings():
    """
    Scheduled standings reconciliation runs Tuesday Pacific.
    """

    now_pt = datetime.now(
        PACIFIC
    )

    return (
        now_pt.weekday()
        == 1
    )


# ============================================================
# PRINTED STANDINGS PARSER
# ============================================================

def parse_printed_standings(text):
    """
    Example:

    @Return_Of_RB 13-5 (72%)
    @BarstoolBigCat 16-19 (46%)
    @stoolpresidente 5-6 (45%)
    """

    output = {}

    patterns = {
        "Rico Bosco": [
            r"@return_of_rb\s+(\d+)-(\d+)(?:-(\d+))?",
            r"@returnofrb\s+(\d+)-(\d+)(?:-(\d+))?",
        ],

        "Big Cat": [
            r"@barstoolbigcat\s+(\d+)-(\d+)(?:-(\d+))?",
        ],

        "Stool Presidente": [
            r"@stoolpresidente\s+(\d+)-(\d+)(?:-(\d+))?",
        ],
    }

    lowered = str(
        text or ""
    ).lower()

    for picker, picker_patterns in (
        patterns.items()
    ):

        for pattern in picker_patterns:

            match = re.search(
                pattern,
                lowered,
                flags=re.I,
            )

            if not match:
                continue

            wins = int(
                match.group(1)
            )

            losses = int(
                match.group(2)
            )

            pushes = int(
                match.group(3)
                or 0
            )

            output[
                picker
            ] = {
                "wins":
                    wins,

                "losses":
                    losses,

                "pushes":
                    pushes,
            }

            break

    return output


# ============================================================
# NORMAL PICK POST FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):

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

    if re.search(
        r"[a-z][a-z .&'-]{1,40}"
        r"\s[+-]\d+(?:\.\d+)?",
        text,
        flags=re.I,
    ):

        return True

    return False


# ============================================================
# DETERMINISTIC MARKET NORMALIZATION
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
# NORMAL POST OPENAI EXTRACTION
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

ACTUAL SOURCE POST:
URL: {post_url}
POSTED: {posted_at}

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

RULES:

1. Only wagers contained in the ACTUAL SOURCE POST may create
   picks.

2. Parent context may identify the picker, but never copy a
   wager from the parent into the child.

3. Never use fan content.

4. Read every attached image.

5. Extract every wager visible on the card.

6. Each bullet belongs to the closest matchup heading above it.

7. Do not treat historical record text, kickoff times, final
   scores, check marks or red Xs as new picks.

8. This function is for NEW PICKS, not result cards.

Return JSON only:

{{
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "matchup or null",
      "team": "team or null",
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

No markdown.
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

    response = client.responses.create(
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

    raw = str(
        response.output_text
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

    payload = json.loads(
        raw
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
            "AI picks is not a list"
        )

    return [
        normalize_extracted_market(
            pick
        )
        for pick in picks
    ]


# ============================================================
# NORMALIZE NORMAL AI PICK
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

    if picker not in TRACKED_PICKERS:
        return None

    selection = clean_text(
        pick.get("selection")
    )

    if not selection:
        return None

    pick["picker"] = picker
    pick["selection"] = selection

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

        if str(
            side
        ).upper() in {
            "OVER",
            "UNDER",
        }:

            pick["side"] = (
                str(side).upper()
            )

        else:

            pick["side"] = (
                clean_text(side)
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

        pick["confidence"] = 0.95

    return pick


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

        "official_reconciled":
            False,
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
# PROCESSED NORMAL POST STATE
# ============================================================

def initialize_processed_ids(
    existing,
    state,
):

    if state.get(
        PROCESSED_IDS_FLAG
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
        PROCESSED_IDS_FLAG
    ] = True

    print(
        "Seeded processed post IDs:",
        len(ids),
    )


# ============================================================
# RETRY NORMAL POSTS
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
# PROCESS NORMAL NEW POSTS
# ============================================================

def process_normal_posts(
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
                item.get("id")
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
        # PAT HILL RESULT CARDS MUST NEVER BECOME NEW PICKS
        # ----------------------------------------------------

        if is_standings_context(
            text,
            parent_text,
        ):

            print(
                "NORMAL INGEST SKIPPING "
                "PAT HILL RESULT POST:",
                post_id,
            )

            processed_ids.add(
                post_id
            )

            failed_ids.discard(
                post_id
            )

            continue


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

        picker_hint = (
            picker_hint_from_text(
                text
                + "\n"
                + parent_text
            )
        )

        added_hint = (
            is_added_post(
                text
                + "\n"
                + parent_text
            )
        )

        week = infer_week(
            text,
            post.get(
                "created_at"
            ),
        )

        post_url = (
            "https://x.com/"
            f"{USERNAME}/status/"
            f"{post_id}"
        )

        print(
            "PARSING NEW PICK POST:",
            post_id,
            "| week:",
            week,
            "| reply:",
            reply_hint,
            "| picker:",
            picker_hint,
        )

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
                "ADDED NEW PICK:",
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
# OFFICIAL RESULT CARD EXTRACTION
# ============================================================

def parse_official_result_cards(
    *,
    root_post,
    thread_posts,
    media_map,
    target_week,
):

    from openai import OpenAI

    client = OpenAI()

    image_urls = []

    for post in thread_posts:

        for image_url in (
            image_urls_for_post(
                post,
                media_map,
            )
        ):

            if (
                image_url
                not in image_urls
            ):

                image_urls.append(
                    image_url
                )

    if not image_urls:

        raise ValueError(
            "PAT HILL thread has no images"
        )

    thread_text = "\n\n".join(
        str(
            post.get(
                "text"
            )
            or ""
        )
        for post in thread_posts
    )

    prompt = f"""
You are reconciling the OFFICIAL weekly results for the
Barstool Pick Em college football show.

These images come only from the verified official
@barstoolpickem PAT HILL STANDINGS thread.

TARGET WEEK:
{target_week}

THREAD TEXT:
{thread_text}


TASK
====

Read every result-card image carefully.

There should be result cards for:

- Rico Bosco
- Big Cat
- Stool Presidente / Dave Portnoy / El Pres

Each card contains the wagers actually counted by Barstool for
the completed week.

A GREEN CHECK means WIN.
A RED X means LOSS.
A push/tie symbol, if shown, means PUSH.

The official result cards are the final source of truth.


CRITICAL RULES
==============

1. Extract EVERY individual wager shown on each picker's final
   card.

2. Cards may span multiple images. Combine the pages belonging
   to the same picker.

3. Do not duplicate a wager simply because one card is split
   across multiple screenshots.

4. Preserve the actual wager line.

5. Preserve 1Q, 1H and team-total distinctions.

6. The section labeled "Adds:" contains legitimate additional
   picks. Return those with added_pick=true.

7. Do not invent any wager not visible on the cards.

8. Do not use our historical tracker data.

9. Read the printed record on each card, such as 13-5-0.

10. Your extracted individual results MUST exactly add up to
    that printed record.

11. If any card is missing, cropped so badly that the complete
    card cannot be read, or the result totals cannot be
    reconciled, set complete=false.

12. Never guess merely to make the totals work.


OUTPUT JSON ONLY
================

{{
  "complete": true,
  "week": {target_week},
  "pickers": [
    {{
      "picker": "Rico Bosco",
      "printed_record": {{
        "wins": 0,
        "losses": 0,
        "pushes": 0
      }},
      "picks": [
        {{
          "selection": "exact concise wager",
          "matchup": "matchup or null",
          "team": "team or null",
          "opponent": "opponent or null",
          "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
          "side": "OVER|UNDER|team name|null",
          "line": 0.0,
          "result": "WIN|LOSS|PUSH",
          "added_pick": false
        }}
      ]
    }}
  ]
}}

Return all three tracked pickers.

No markdown.
No commentary.
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

    response = client.responses.create(
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

    raw = str(
        response.output_text
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

    return json.loads(
        raw
    )


# ============================================================
# VALIDATE OFFICIAL RESULT-CARD EXTRACTION
# ============================================================

def validate_official_results(
    payload,
    *,
    printed_standings,
    target_week,
):

    if not payload.get(
        "complete"
    ):

        raise ValueError(
            "AI marked result thread incomplete"
        )

    try:

        payload_week = int(
            payload.get("week")
        )

    except Exception:

        raise ValueError(
            "Official result payload "
            "has no valid week"
        )

    if payload_week != int(
        target_week
    ):

        raise ValueError(
            "Official result week mismatch: "
            f"{payload_week} vs {target_week}"
        )

    picker_rows = (
        payload.get(
            "pickers"
        )
        or []
    )

    by_picker = {}

    for row in picker_rows:

        picker = normalize_picker(
            row.get("picker")
        )

        if picker not in TRACKED_PICKERS:
            continue

        by_picker[
            picker
        ] = row

    missing = (
        TRACKED_PICKERS
        - set(
            by_picker.keys()
        )
    )

    if missing:

        raise ValueError(
            "Missing official result cards for: "
            + ", ".join(
                sorted(missing)
            )
        )

    validated = {}

    for picker in sorted(
        TRACKED_PICKERS
    ):

        row = by_picker[
            picker
        ]

        record = (
            row.get(
                "printed_record"
            )
            or {}
        )

        wins = int(
            record.get(
                "wins"
            )
            or 0
        )

        losses = int(
            record.get(
                "losses"
            )
            or 0
        )

        pushes = int(
            record.get(
                "pushes"
            )
            or 0
        )

        picks = (
            row.get(
                "picks"
            )
            or []
        )

        result_counts = {
            "WIN": 0,
            "LOSS": 0,
            "PUSH": 0,
        }

        normalized_picks = []

        for pick in picks:

            result = str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()

            if result not in result_counts:

                raise ValueError(
                    f"{picker} has invalid "
                    f"official result: {result}"
                )

            selection = clean_text(
                pick.get(
                    "selection"
                )
            )

            if not selection:

                raise ValueError(
                    f"{picker} has official "
                    "pick with empty selection"
                )

            normalized = (
                normalize_extracted_market(
                    dict(pick)
                )
            )

            normalized[
                "selection"
            ] = selection

            normalized[
                "result"
            ] = result

            normalized_picks.append(
                normalized
            )

            result_counts[
                result
            ] += 1


        # ----------------------------------------------------
        # CARD INTERNAL VALIDATION
        # ----------------------------------------------------

        if (
            result_counts["WIN"]
            != wins
        ):

            raise ValueError(
                f"{picker} image validation failed: "
                f"expected {wins} wins, extracted "
                f"{result_counts['WIN']}"
            )

        if (
            result_counts["LOSS"]
            != losses
        ):

            raise ValueError(
                f"{picker} image validation failed: "
                f"expected {losses} losses, extracted "
                f"{result_counts['LOSS']}"
            )

        if (
            result_counts["PUSH"]
            != pushes
        ):

            raise ValueError(
                f"{picker} image validation failed: "
                f"expected {pushes} pushes, extracted "
                f"{result_counts['PUSH']}"
            )

        if len(
            normalized_picks
        ) != (
            wins
            + losses
            + pushes
        ):

            raise ValueError(
                f"{picker} official count mismatch"
            )


        # ----------------------------------------------------
        # ROOT TWEET RECORD VALIDATION
        # ----------------------------------------------------

        if picker in printed_standings:

            tweet_record = (
                printed_standings[
                    picker
                ]
            )

            expected_tuple = (
                tweet_record[
                    "wins"
                ],
                tweet_record[
                    "losses"
                ],
                tweet_record[
                    "pushes"
                ],
            )

            image_tuple = (
                wins,
                losses,
                pushes,
            )

            if (
                expected_tuple
                != image_tuple
            ):

                raise ValueError(
                    f"{picker} standings-text "
                    "record does not match card: "
                    f"{expected_tuple} vs "
                    f"{image_tuple}"
                )

        validated[
            picker
        ] = {
            "wins":
                wins,

            "losses":
                losses,

            "pushes":
                pushes,

            "picks":
                normalized_picks,
        }

    return validated


# ============================================================
# BUILD FINAL OFFICIAL WEEK ROWS
# ============================================================

def build_official_week_rows(
    *,
    validated,
    target_week,
    root_post,
):

    root_id = str(
        root_post.get(
            "id"
        )
    )

    root_url = (
        "https://x.com/"
        f"{USERNAME}/status/"
        f"{root_id}"
    )

    reconciled_at = now_iso()

    rows = []

    for picker in [
        "Rico Bosco",
        "Big Cat",
        "Stool Presidente",
    ]:

        picker_data = validated[
            picker
        ]

        for index, source_pick in enumerate(
            picker_data[
                "picks"
            ]
        ):

            result = str(
                source_pick[
                    "result"
                ]
            ).upper()

            if result == "WIN":

                profit_units = 1.0

            elif result == "LOSS":

                profit_units = -1.0

            else:

                profit_units = 0.0

            row = {
                "picker":
                    picker,

                "sport":
                    "CFB",

                "matchup":
                    source_pick.get(
                        "matchup"
                    ),

                "team":
                    source_pick.get(
                        "team"
                    ),

                "opponent":
                    source_pick.get(
                        "opponent"
                    ),

                "bet_type":
                    normalize_bet_type(
                        source_pick.get(
                            "bet_type"
                        )
                    ),

                "selection":
                    clean_text(
                        source_pick.get(
                            "selection"
                        )
                    ),

                "side":
                    source_pick.get(
                        "side"
                    ),

                "line":
                    safe_float(
                        source_pick.get(
                            "line"
                        )
                    ),

                "odds":
                    None,

                "units":
                    1.0,

                "mortal_lock":
                    False,

                "week":
                    int(
                        target_week
                    ),

                "added_pick":
                    bool(
                        source_pick.get(
                            "added_pick"
                        )
                    ),

                "confidence":
                    1.0,

                "status":
                    "FINAL",

                "result":
                    result,

                "profit_units":
                    profit_units,

                "source_post_id":
                    root_id,

                "source_url":
                    root_url,

                "source_text":
                    "PAT HILL STANDINGS",

                "source_is_reply":
                    False,

                "conversation_id":
                    root_post.get(
                        "conversation_id"
                    )
                    or root_id,

                "posted_at":
                    root_post.get(
                        "created_at"
                    ),

                "graded_at":
                    reconciled_at,

                "final_score":
                    None,

                "event_id":
                    None,

                "official_reconciled":
                    True,

                "official_result_post_id":
                    root_id,

                "official_result_source":
                    "PAT HILL STANDINGS",

                "official_reconciled_at":
                    reconciled_at,
            }

            row["id"] = stable_id(
                "|".join(
                    [
                        "official",
                        str(target_week),
                        picker,
                        str(index),
                        row[
                            "selection"
                        ],
                        result,
                    ]
                )
            )

            rows.append(
                row
            )

    return rows


# ============================================================
# REPLACE COMPLETED WEEK WITH OFFICIAL RESULTS
# ============================================================

def replace_week_with_official(
    existing,
    *,
    official_rows,
    target_week,
):

    kept = []

    removed_count = 0

    for pick in existing:

        if (
            pick_week(
                pick
            )
            == int(
                target_week
            )
            and normalize_picker(
                pick.get(
                    "picker"
                )
            )
            in TRACKED_PICKERS
        ):

            removed_count += 1

            continue

        kept.append(
            pick
        )

    print(
        "Existing provisional Week",
        target_week,
        "rows removed:",
        removed_count,
    )

    print(
        "Official Week",
        target_week,
        "rows inserted:",
        len(
            official_rows
        ),
    )

    return (
        kept
        + official_rows
    )


# ============================================================
# SELF-HEALING OFFICIAL WEEK VALIDATION
# ============================================================

def official_week_is_healthy(
    existing,
    *,
    target_week,
    printed_standings,
    result_post_id,
):
    """
    A processed standings thread is never trusted blindly.

    Stored official rows must still exactly match:
      - official picker record
      - official total pick count
      - valid WIN / LOSS / PUSH
      - FINAL status
      - official_reconciled=True
      - correct source standings post

    If anything is broken, reconciliation runs again.
    """

    print()
    print(
        "VALIDATING STORED OFFICIAL WEEK:",
        target_week,
    )

    healthy = True

    for picker in [
        "Rico Bosco",
        "Big Cat",
        "Stool Presidente",
    ]:

        expected = (
            printed_standings.get(
                picker
            )
        )

        if not expected:

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| printed standings missing",
            )

            healthy = False

            continue

        rows = [
            pick
            for pick in existing
            if (
                pick_week(
                    pick
                )
                == int(
                    target_week
                )
                and normalize_picker(
                    pick.get(
                        "picker"
                    )
                )
                == picker
            )
        ]


        # ----------------------------------------------------
        # ALL ROWS MUST BE OFFICIAL
        # ----------------------------------------------------

        non_official = [
            pick
            for pick in rows
            if not pick.get(
                "official_reconciled"
            )
        ]

        if non_official:

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| non-official rows:",
                len(
                    non_official
                ),
            )

            healthy = False


        # ----------------------------------------------------
        # ALL ROWS MUST COME FROM THIS RESULT POST
        # ----------------------------------------------------

        wrong_source = [
            pick
            for pick in rows
            if str(
                pick.get(
                    "official_result_post_id"
                )
                or ""
            )
            != str(
                result_post_id
            )
        ]

        if wrong_source:

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| wrong result source rows:",
                len(
                    wrong_source
                ),
            )

            healthy = False


        # ----------------------------------------------------
        # ALL ROWS MUST HAVE VALID FINAL RESULTS
        # ----------------------------------------------------

        invalid_rows = [
            pick
            for pick in rows
            if (
                str(
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
                or str(
                    pick.get(
                        "status"
                    )
                    or ""
                ).upper()
                != "FINAL"
            )
        ]

        if invalid_rows:

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| invalid/pending official rows:",
                len(
                    invalid_rows
                ),
            )

            for pick in invalid_rows:

                print(
                    "   INVALID:",
                    pick.get(
                        "selection"
                    ),
                    "| result:",
                    pick.get(
                        "result"
                    ),
                    "| status:",
                    pick.get(
                        "status"
                    ),
                )

            healthy = False


        # ----------------------------------------------------
        # CURRENT STORED RECORD
        # ----------------------------------------------------

        wins = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "WIN"
        )

        losses = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "LOSS"
        )

        pushes = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "PUSH"
        )

        expected_wins = int(
            expected.get(
                "wins"
            )
            or 0
        )

        expected_losses = int(
            expected.get(
                "losses"
            )
            or 0
        )

        expected_pushes = int(
            expected.get(
                "pushes"
            )
            or 0
        )

        expected_total = (
            expected_wins
            + expected_losses
            + expected_pushes
        )

        print(
            picker,
            "| stored:",
            f"{wins}-{losses}-{pushes}",
            "| expected:",
            f"{expected_wins}-"
            f"{expected_losses}-"
            f"{expected_pushes}",
            "| rows:",
            len(
                rows
            ),
            "/",
            expected_total,
        )


        # ----------------------------------------------------
        # RECORD MUST MATCH
        # ----------------------------------------------------

        if (
            wins
            != expected_wins
            or losses
            != expected_losses
            or pushes
            != expected_pushes
        ):

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| record mismatch",
            )

            healthy = False


        # ----------------------------------------------------
        # PICK COUNT MUST MATCH
        # ----------------------------------------------------

        if len(
            rows
        ) != expected_total:

            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| expected",
                expected_total,
                "official rows but found",
                len(
                    rows
                ),
            )

            healthy = False


    if healthy:

        print(
            "STORED OFFICIAL WEEK IS HEALTHY"
        )

    else:

        print(
            "STORED OFFICIAL WEEK FAILED VALIDATION"
        )

    return healthy


# ============================================================
# PAT HILL STANDINGS RECONCILIATION
# ============================================================

def reconcile_pat_hill_standings(
    existing,
    state,
    official_user_id,
):

    print()
    print(
        "===================================="
    )
    print(
        "PAT HILL STANDINGS SCAN"
    )
    print(
        "===================================="
    )


    # ========================================================
    # LOOK BACK SEVERAL DAYS
    #
    # This intentionally ignores last_x_post_id.
    # ========================================================

    start_time = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=
                STANDINGS_LOOKBACK_DAYS
        )
    )

    posts, media_map = (
        fetch_user_posts(
            official_user_id,
            start_time=
                start_time,
            max_pages=
                STANDINGS_MAX_PAGES,
        )
    )


    # ========================================================
    # FIND OFFICIAL ROOT POSTS
    # ========================================================

    root_candidates = [
        post
        for post in posts
        if is_pat_hill_text(
            post.get(
                "text"
            )
        )
    ]

    if not root_candidates:

        print(
            "No PAT HILL STANDINGS "
            "root post found yet."
        )

        return existing


    # Newest standings first.
    root_candidates = sorted(
        root_candidates,
        key=lambda post:
            int(
                post.get(
                    "id"
                )
                or 0
            ),
        reverse=True,
    )

    processed = set(
        str(value)
        for value in (
            state.get(
                OFFICIAL_RESULT_STATE_KEY,
                [],
            )
            or []
        )
    )


    # ========================================================
    # PROCESS STANDINGS THREADS
    # ========================================================

    for root_post in root_candidates:

        root_id = str(
            root_post.get(
                "id"
            )
            or ""
        )

        if not root_id:
            continue

        conversation_id = str(
            root_post.get(
                "conversation_id"
            )
            or root_id
        )


        # ----------------------------------------------------
        # ROOT + ALL OFFICIAL REPLIES/SUBTWEETS
        # ----------------------------------------------------

        thread_posts = [
            post
            for post in posts
            if (
                str(
                    post.get(
                        "conversation_id"
                    )
                    or post.get(
                        "id"
                    )
                )
                == conversation_id
                and str(
                    post.get(
                        "author_id"
                    )
                    or ""
                )
                == str(
                    official_user_id
                )
            )
        ]

        if not any(
            str(
                post.get(
                    "id"
                )
            )
            == root_id
            for post in thread_posts
        ):

            thread_posts.append(
                root_post
            )

        thread_posts = sorted(
            thread_posts,
            key=lambda post:
                int(
                    post.get(
                        "id"
                    )
                    or 0
                ),
        )


        # ----------------------------------------------------
        # DETERMINE COMPLETED WEEK
        # ----------------------------------------------------

        target_week = (
            standings_target_week(
                root_post
            )
        )

        if not target_week:

            print(
                "Could not determine standings week:",
                root_id,
            )

            continue


        # ----------------------------------------------------
        # READ ROOT PRINTED RECORDS
        # ----------------------------------------------------

        printed_standings = (
            parse_printed_standings(
                root_post.get(
                    "text"
                )
            )
        )

        if (
            len(
                printed_standings
            )
            != 3
        ):

            print(
                "PAT HILL standings text "
                "does not contain all 3 pickers yet."
            )

            print(
                "Found:",
                printed_standings,
            )

            continue


        print()
        print(
            "PAT HILL STANDINGS FOUND"
        )

        print(
            "Official result post:",
            root_id,
        )

        print(
            "Conversation:",
            conversation_id,
        )

        print(
            "Official thread posts found:",
            len(
                thread_posts
            ),
        )

        print(
            "Reconciling Week:",
            target_week,
        )

        print(
            "Printed standings:",
            printed_standings,
        )


        # ====================================================
        # SELF-HEALING VALIDATION
        # ====================================================

        if root_id in processed:

            healthy = (
                official_week_is_healthy(
                    existing,
                    target_week=
                        target_week,
                    printed_standings=
                        printed_standings,
                    result_post_id=
                        root_id,
                )
            )

            if healthy:

                print(
                    "Already reconciled and "
                    "validated standings thread:",
                    root_id,
                )

                continue


            print()
            print(
                "****************************************"
            )

            print(
                "OFFICIAL WEEK DATA IS NOT HEALTHY"
            )

            print(
                "RE-RUNNING PAT HILL RECONCILIATION"
            )

            print(
                "****************************************"
            )


        # ====================================================
        # EXTRACT + VALIDATE ALL RESULT CARDS
        # ====================================================

        try:

            payload = (
                parse_official_result_cards(
                    root_post=
                        root_post,
                    thread_posts=
                        thread_posts,
                    media_map=
                        media_map,
                    target_week=
                        target_week,
                )
            )

            validated = (
                validate_official_results(
                    payload,
                    printed_standings=
                        printed_standings,
                    target_week=
                        target_week,
                )
            )

        except Exception as exc:

            print(
                "OFFICIAL RECONCILIATION "
                "NOT READY / FAILED:"
            )

            print(
                type(exc).__name__,
                exc,
            )

            print(
                "Existing database left unchanged."
            )

            print(
                "Thread will be retried on "
                "the next Tuesday run."
            )

            continue


        # ====================================================
        # REPORT VALIDATED RESULTS
        # ====================================================

        print()
        print(
            "OFFICIAL CARD VALIDATION PASSED"
        )

        for picker in [
            "Rico Bosco",
            "Big Cat",
            "Stool Presidente",
        ]:

            data = validated[
                picker
            ]

            print(
                picker,
                "| Official:",
                f"{data['wins']}-"
                f"{data['losses']}-"
                f"{data['pushes']}",
                "| picks:",
                len(
                    data[
                        "picks"
                    ]
                ),
            )


        # ====================================================
        # BUILD OFFICIAL ROWS
        # ====================================================

        official_rows = (
            build_official_week_rows(
                validated=
                    validated,
                target_week=
                    target_week,
                root_post=
                    root_post,
            )
        )

        expected_total = sum(
            (
                data[
                    "wins"
                ]
                + data[
                    "losses"
                ]
                + data[
                    "pushes"
                ]
            )
            for data in (
                validated.values()
            )
        )


        # ----------------------------------------------------
        # FINAL PRE-REPLACEMENT SAFETY CHECK
        # ----------------------------------------------------

        if len(
            official_rows
        ) != expected_total:

            print(
                "OFFICIAL RECONCILIATION ABORTED:"
            )

            print(
                "Built",
                len(
                    official_rows
                ),
                "rows but expected",
                expected_total,
            )

            continue


        # ====================================================
        # REPLACE ONLY THE COMPLETED WEEK
        # ====================================================

        existing = (
            replace_week_with_official(
                existing,
                official_rows=
                    official_rows,
                target_week=
                    target_week,
            )
        )


        # ====================================================
        # VERIFY REPLACEMENT
        # ====================================================

        post_replace_healthy = (
            official_week_is_healthy(
                existing,
                target_week=
                    target_week,
                printed_standings=
                    printed_standings,
                result_post_id=
                    root_id,
            )
        )

        if not post_replace_healthy:

            raise RuntimeError(
                "Official rows failed "
                "post-reconciliation validation."
            )


        # ====================================================
        # SAVE SUCCESS STATE
        # ====================================================

        processed.add(
            root_id
        )

        state[
            OFFICIAL_RESULT_STATE_KEY
        ] = sorted(
            processed,
            key=lambda value:
                int(value)
                if value.isdigit()
                else 0,
        )[-100:]

        state[
            "last_official_reconciled_week"
        ] = int(
            target_week
        )

        state[
            "last_official_result_post_id"
        ] = root_id

        state[
            "last_official_reconciled_at"
        ] = now_iso()

        print()
        print(
            "===================================="
        )

        print(
            "OFFICIAL WEEK",
            target_week,
            "RECONCILIATION COMPLETE"
        )

        print(
            "===================================="
        )

    return existing


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
                == int(
                    week
                )
            )
        ]

        wins = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "WIN"
        )

        losses = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "LOSS"
        )

        pushes = sum(
            1
            for pick in rows
            if str(
                pick.get(
                    "result"
                )
                or ""
            ).upper()
            == "PUSH"
        )

        pending = (
            len(rows)
            - wins
            - losses
            - pushes
        )

        official = (
            all(
                bool(
                    pick.get(
                        "official_reconciled"
                    )
                )
                for pick in rows
            )
            if rows
            else False
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
            "| official:",
            official,
        )

    print(
        "===================================="
    )


# ============================================================
# MAIN INGEST
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
    # 1. SEED PROCESSED IDS FROM EXISTING PICKS
    # ========================================================

    initialize_processed_ids(
        existing,
        state,
    )


    # ========================================================
    # 2. RESOLVE OFFICIAL ACCOUNT
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
    # 3. RETRY FAILED NORMAL PICK POSTS
    # ========================================================

    retry_posts, retry_media = (
        fetch_retry_posts(
            state,
            official_user_id,
        )
    )


    # ========================================================
    # 4. FETCH ONLY NEW NORMAL TIMELINE POSTS
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
    # 5. COMBINE RETRIES + NEW POSTS
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
    # 6. NORMAL NEW-PICK INGEST
    # ========================================================

    (
        existing,
        candidate_count,
        new_pick_count,
    ) = process_normal_posts(
        existing,
        combined_posts,
        combined_media,
        official_user_id,
        state,
    )


    # ========================================================
    # 7. ADVANCE NORMAL X CURSOR
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
    # 8. TUESDAY OFFICIAL RESULT RECONCILIATION
    # ========================================================

    if should_scan_standings():

        existing = (
            reconcile_pat_hill_standings(
                existing,
                state,
                official_user_id,
            )
        )

    else:

        print(
            "Not Tuesday Pacific — "
            "skipping PAT HILL reconciliation scan."
        )


    # ========================================================
    # 9. FINAL DEDUPE
    # ========================================================

    existing = dedupe_picks(
        existing
    )


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
        "Candidate new-pick posts:",
        candidate_count,
    )

    print(
        "New wagers added:",
        new_pick_count,
    )

    print(
        "Failed posts queued:",
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
        "Last official reconciled week:",
        state.get(
            "last_official_reconciled_week"
        ),
    )

    print(
        "===================================="
    )


    # ========================================================
    # AUDIT CURRENTLY IMPORTANT COMPLETED WEEK
    # ========================================================

    print_week_audit(
        existing,
        1,
    )


if __name__ == "__main__":
    ingest()
