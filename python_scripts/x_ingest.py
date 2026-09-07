from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from common import (
    load_json,
    save_json,
    PICKS_FILE,
    STATE_FILE,
    now_iso,
    normalize_picker,
)


# ============================================================
# CONFIG
# ============================================================

X_API = "https://api.x.com/2"

USERNAME = os.getenv(
    "X_USERNAME",
    "barstoolpickem",
)

BACKFILL_DAYS = 8
MAX_BACKFILL_PAGES = 3

# Historical recovery is ONLY for official replies/subtweets.
REPLY_RECOVERY_DAYS = 12
MAX_REPLY_RECOVERY_PAGES = 4

MAX_PARENT_DEPTH = 3

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


def normalize_text(value):
    value = clean_text(value).lower()

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


def normalize_team(value):
    """
    Used only for canonical comparison/dedupe.

    Do NOT use overly broad mascot aliases.
    """

    value = normalize_text(value)

    aliases = {
        "byu cougars": "byu",
        "brigham young": "byu",

        "utah tech trailblazers": "utah tech",
        "utah tech": "utah tech",

        "wisconsin badgers": "wisconsin",
        "wis": "wisconsin",
        "wisc": "wisconsin",

        "notre dame fighting irish": "notre dame",
        "nd": "notre dame",

        "texas a and m": "texas a&m",
        "texas am": "texas a&m",
        "a and m": "texas a&m",
        "tamu": "texas a&m",

        "wash": "washington",
        "uw": "washington",

        "wazzu": "washington state",
        "wsu": "washington state",

        "california": "cal",
        "cal golden bears": "cal",

        "hou": "houston",

        "mem": "memphis",

        "ore": "oregon",

        "iu": "indiana",
    }

    return aliases.get(
        value,
        value,
    )


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
        hashlib.sha1(
            str(value).encode("utf-8")
        )
        .hexdigest()[:16]
    )


# ============================================================
# X API
# ============================================================

def x_get(path, params=None):
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
        f"/users/by/username/{USERNAME}"
    )

    return str(
        payload["data"]["id"]
    )


def iso_x_time(dt):
    return (
        dt.astimezone(
            timezone.utc
        )
        .strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


# ============================================================
# OFFICIAL USER TIMELINE
# ============================================================

def fetch_user_posts(
    user_id,
    since_id=None,
    start_time=None,
    max_pages=1,
):
    """
    Fetch only posts authored by @barstoolpickem.

    Replies ARE included.
    Retweets are excluded.
    """

    params = {
        "max_results": 100,

        # Do NOT exclude replies.
        "exclude": "retweets",

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
        ] = str(since_id)

    if start_time:
        params[
            "start_time"
        ] = iso_x_time(
            start_time
        )

    all_posts = []
    media_map = {}

    page = 0

    while True:
        payload = x_get(
            f"/users/{user_id}/tweets",
            params,
        )

        for post in payload.get(
            "data",
            [],
        ):
            author_id = str(
                post.get(
                    "author_id"
                )
                or ""
            )

            # Absolute author safety.
            if (
                author_id
                and author_id
                != str(user_id)
            ):
                print(
                    "REJECTED NON-OFFICIAL AUTHOR:",
                    post.get("id"),
                    author_id,
                )
                continue

            all_posts.append(
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
            media_key = media.get(
                "media_key"
            )

            if media_key:
                media_map[
                    media_key
                ] = media

        page += 1

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

        if page >= max_pages:
            break

        params[
            "pagination_token"
        ] = next_token

    return (
        all_posts,
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
# REPLY / THREAD SUPPORT
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
            post_id = reference.get(
                "id"
            )

            if post_id:
                return str(
                    post_id
                )

    return None


def is_reply_post(post):
    if replied_to_post_id(
        post
    ):
        return True

    return bool(
        post.get(
            "in_reply_to_user_id"
        )
    )


def official_parent_context(
    post,
    official_user_id,
    cache,
):
    """
    Parent content is usable ONLY when the parent was also
    authored by @barstoolpickem.

    Parent content may identify the picker, but wagers from the
    parent are never extracted as child-post wagers.
    """

    current_id = replied_to_post_id(
        post
    )

    if not current_id:
        return ""

    pieces = []
    depth = 0

    while (
        current_id
        and depth < MAX_PARENT_DEPTH
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

        author_id = str(
            parent.get(
                "author_id"
            )
            or ""
        )

        # Never use fan/third-party context.
        if (
            author_id
            != str(
                official_user_id
            )
        ):
            print(
                "IGNORING NON-OFFICIAL PARENT:",
                current_id,
            )
            break

        text = str(
            parent.get("text")
            or ""
        ).strip()

        if text:
            pieces.append(
                text
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
# WEEK MAPPING
# ============================================================

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
    # 2026 Pick Em season
    # --------------------------------------------------------

    if dt.year == 2026:

        # Week 1
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

        # Week 2
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

        # Week 3 onward
        week_3_start = date(
            2026,
            9,
            14,
        )

        if d >= week_3_start:
            return (
                3
                +
                (
                    d
                    - week_3_start
                ).days
                // 7
            )

        return None

    # --------------------------------------------------------
    # Generic future-season fallback
    # --------------------------------------------------------

    sept_1 = datetime(
        dt.year,
        9,
        1,
        tzinfo=timezone.utc,
    )

    week_one_monday = (
        sept_1
        - timedelta(
            days=sept_1.weekday()
        )
    )

    delta_days = (
        dt
        - week_one_monday
    ).days

    if delta_days < -7:
        return None

    if delta_days < 0:
        return 1

    return (
        delta_days // 7
    ) + 1


# ============================================================
# PICKER DETECTION
# ============================================================

def picker_hint_from_text(
    text
):
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
        "ricobosco"
        in text
        or "rico bosco"
        in text
        or re.search(
            r"\brico\b",
            text,
        )
    ):
        return "Rico Bosco"

    return None


def is_added_post(
    text
):
    text = str(
        text or ""
    ).lower()

    terms = [
        "adds for",
        "add for",
        "added pick",
        "adding",
        "addition",
        "another one",
    ]

    return any(
        term in text
        for term in terms
    )


# ============================================================
# CANDIDATE POST FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    # All official image posts are eligible for inspection.
    if image_urls:
        return True

    text = str(
        text or ""
    ).lower()

    keywords = [
        "adds for",
        "add for",
        "over ",
        "under ",
        "moneyline",
        " ml",
        "team total",
        " tt ",
        "first half",
        "1h",
        "first quarter",
        "1q",
    ]

    if any(
        keyword in text
        for keyword in keywords
    ):
        return True

    # Spread-like text.
    if re.search(
        r"\b[a-z][a-z .&'-]{1,35}"
        r"\s[+-]\d+(?:\.\d+)?\b",
        text,
    ):
        return True

    return False


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_bet_type(
    value
):
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


def infer_market_from_selection(
    original_pick
):
    pick = dict(
        original_pick
    )

    selection = clean_text(
        pick.get(
            "selection"
        )
    )

    text = selection.lower()

    current = normalize_bet_type(
        pick.get(
            "bet_type"
        )
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

    # --------------------------------------------------------
    # TEAM TOTAL
    # --------------------------------------------------------

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

            return pick

        verbose = re.search(
            r"\b(?:tt|team\s*total)"
            r"\s*(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if verbose:
            pick[
                "side"
            ] = (
                verbose
                .group(1)
                .upper()
            )

            pick[
                "line"
            ] = float(
                verbose.group(2)
            )

        return pick

    # --------------------------------------------------------
    # PERIOD BET
    # --------------------------------------------------------

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
            flags=re.I,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:ml|moneyline)\b",
            text,
            flags=re.I,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*"
            r"[0-9]+(?:\.[0-9]+)?",
            text,
        ):
            pick[
                "bet_type"
            ] = (
                f"{prefix}_SPREAD"
            )

        else:
            pick[
                "bet_type"
            ] = current

        return pick

    pick[
        "bet_type"
    ] = current

    # Normalize regular totals.
    total_match = re.search(
        r"\b(over|under)\s*"
        r"([0-9]+(?:\.[0-9]+)?)",
        text,
        flags=re.I,
    )

    if total_match:
        if current in {
            "TOTAL",
            "OTHER",
        }:
            pick[
                "bet_type"
            ] = "TOTAL"

        pick[
            "side"
        ] = (
            total_match
            .group(1)
            .upper()
        )

        pick[
            "line"
        ] = float(
            total_match.group(2)
        )

    return pick


# ============================================================
# MEDIA
# ============================================================

def image_urls_for_post(
    post,
    media_map,
):
    urls = []

    keys = (
        post.get(
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

        media_type = media.get(
            "type"
        )

        if media_type == "photo":
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
# OPENAI EXTRACTION
# ============================================================

def extract_json_from_response(
    response
):
    text = str(
        getattr(
            response,
            "output_text",
            "",
        )
        or ""
    ).strip()

    if not text:
        return {
            "picks": []
        }

    # Strip Markdown fences if model ever returns them.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        return json.loads(
            text
        )
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if (
        start >= 0
        and end > start
    ):
        try:
            return json.loads(
                text[
                    start:
                    end + 1
                ]
            )
        except Exception:
            pass

    print(
        "AI JSON PARSE FAILED:",
        text[:400],
    )

    return {
        "picks": []
    }


def parse_post_with_ai(
    text,
    image_urls,
    post_url,
    posted_at,
    inferred_week,
    picker_hint,
    added_hint,
    reply_hint=False,
    official_parent_text="",
):
    from openai import OpenAI

    client = OpenAI()

    prompt = f"""
You extract NCAA college football gambling picks from the
official Barstool Pick Em X account.

ONLY track picks belonging to:
- Big Cat
- Stool Presidente / Dave Portnoy / El Presidente / Pres
- Rico Bosco

SOURCE SAFETY

The ACTUAL SOURCE POST has already been verified by the program
as authored by @barstoolpickem.

Parent/thread context has also been verified as official.

ONLY wagers visible or written in the ACTUAL SOURCE POST may
create picks.

Parent/thread context may ONLY:
1. identify which picker a reply belongs to;
2. tell you that the source reply is an added pick.

NEVER create a wager solely from parent context.

SOURCE URL:
{post_url}

POSTED AT:
{posted_at}

ACTUAL SOURCE POST TEXT:
{text}

VERIFIED OFFICIAL PARENT CONTEXT:
{official_parent_text or "None"}

LIKELY PICKER:
{picker_hint or "Unknown"}

LIKELY CFB WEEK:
{inferred_week}

LIKELY ADDED PICK:
{added_hint}

IS OFFICIAL REPLY/SUBTWEET:
{reply_hint}


CRITICAL IMAGE-CARD RULES

Read EVERY image carefully.

Weekly cards often use this format:

    WIS @ ND 7:30pm SUN
      • 46.5

The bullet may contain only a number because the handwritten
direction is small, faint, stylized, or visually separated.

DO NOT silently omit a numeric wager.

For every matchup heading, inspect every bullet immediately
beneath that heading.

If the visual clearly indicates OVER or UNDER, record it.

Specifically, the 2026 Rico Week 1 card contains:

    WIS @ ND
    Under 46.5

This must be extracted as:
- picker: Rico Bosco
- matchup: Wisconsin @ Notre Dame
- bet_type: TOTAL
- selection: WIS @ ND Under 46.5
- side: UNDER
- line: 46.5

Do NOT confuse this with Big Cat having the same wager.
The same wager may legitimately belong to multiple DIFFERENT
pickers.

Also recognize compact notation:
- Oregon TT o37.5
- Oregon TT u37.5
- Alabama TT over 40.5
- Oklahoma 1Q -9.5
- Miami 1H -13.5
- Indiana first half TT over 27.5

For a weekly-card image:
- associate bullets with the nearest matchup heading above;
- do not drop the final bullet on the card;
- inspect all images in the carousel;
- capture every wager exactly once.

Do NOT guess genuinely unreadable direction.
If a number is truly ambiguous and there is no visual evidence
of Over/Under, omit it rather than inventing a side.

OSU is ambiguous unless matchup/opponent context disambiguates
Ohio State, Oklahoma State, or Oregon State.

A&M means Texas A&M only when the actual context supports it.

Return JSON ONLY.

Use exactly:

{{
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "game matchup or null",
      "team": "wagered team or identifying team or null",
      "opponent": "opponent or null",
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
      "selection": "concise wager",
      "side": "OVER|UNDER|team name|null",
      "line": 0.0,
      "odds": null,
      "week": {inferred_week if inferred_week is not None else "null"},
      "added_pick": false,
      "confidence": 0.0
    }}
  ]
}}

Confidence should be 0.00 to 1.00.

Do not return commentary.
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
            model="gpt-5.6-luna",
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )
    )

    return extract_json_from_response(
        response
    )


# ============================================================
# PICK NORMALIZATION
# ============================================================

def normalize_extracted_pick(
    raw_pick,
    inferred_week=None,
    picker_hint=None,
):
    pick = dict(
        raw_pick or {}
    )

    picker = normalize_picker(
        pick.get(
            "picker"
        )
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
        pick.get(
            "selection"
        )
    )

    if not selection:
        return None

    pick[
        "picker"
    ] = picker

    pick[
        "selection"
    ] = selection

    pick[
        "bet_type"
    ] = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    if pick.get(
        "week"
    ) is None:
        pick[
            "week"
        ] = inferred_week

    try:
        if pick.get(
            "week"
        ) is not None:
            pick[
                "week"
            ] = int(
                pick[
                    "week"
                ]
            )
    except Exception:
        pick[
            "week"
        ] = inferred_week

    side = pick.get(
        "side"
    )

    if side is not None:
        side_text = clean_text(
            side
        )

        if (
            side_text.upper()
            in {
                "OVER",
                "UNDER",
            }
        ):
            pick[
                "side"
            ] = (
                side_text.upper()
            )

        else:
            pick[
                "side"
            ] = side_text

    line = safe_float(
        pick.get(
            "line"
        )
    )

    if line is not None:
        pick[
            "line"
        ] = line

    try:
        pick[
            "confidence"
        ] = float(
            pick.get(
                "confidence",
                0.95,
            )
        )
    except Exception:
        pick[
            "confidence"
        ] = 0.95

    pick = (
        infer_market_from_selection(
            pick
        )
    )

    return pick


# ============================================================
# CANONICAL DEDUPE
# ============================================================

def normalized_matchup_key(
    pick
):
    matchup = normalize_text(
        pick.get(
            "matchup"
        )
    )

    team = normalize_team(
        pick.get(
            "team"
        )
    )

    opponent = normalize_team(
        pick.get(
            "opponent"
        )
    )

    if (
        team
        and opponent
    ):
        return "::".join(
            sorted(
                [
                    team,
                    opponent,
                ]
            )
        )

    if matchup:
        # Normalize common matchup separators.
        matchup = re.sub(
            r"\s+(?:at|@|vs\.?|v)\s+",
            "|",
            matchup,
            flags=re.I,
        )

        pieces = [
            normalize_team(x)
            for x in matchup.split("|")
            if normalize_team(x)
        ]

        if len(pieces) >= 2:
            return "::".join(
                sorted(
                    pieces[:2]
                )
            )

        return matchup

    return team


def normalized_selection_key(
    selection
):
    value = normalize_text(
        selection
    )

    replacements = {
        "wis @ nd":
            "wisconsin @ notre dame",

        "wisc @ nd":
            "wisconsin @ notre dame",

        "wisconsin @ nd":
            "wisconsin @ notre dame",

        "utah tech @ byu":
            "utah tech @ byu",
    }

    for old, new in (
        replacements.items()
    ):
        value = value.replace(
            old,
            new,
        )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def canonical_pick_key(
    pick
):
    """
    Source post ID is intentionally NOT part of the key.

    If Barstool repeats the exact same wager in another official
    post, it should not count twice for the same picker/week.

    The SAME wager belonging to two different pickers is allowed.
    """

    picker = (
        normalize_picker(
            pick.get(
                "picker"
            )
        )
        or ""
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

    bet_type = (
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
        or "OTHER"
    )

    matchup = (
        normalized_matchup_key(
            pick
        )
    )

    side_raw = pick.get(
        "side"
    )

    if (
        side_raw
        and str(side_raw).upper()
        in {
            "OVER",
            "UNDER",
        }
    ):
        side = str(
            side_raw
        ).upper()
    else:
        side = normalize_team(
            side_raw
        )

    line = safe_float(
        pick.get(
            "line"
        )
    )

    line_key = (
        ""
        if line is None
        else f"{line:.3f}"
    )

    selection = (
        normalized_selection_key(
            pick.get(
                "selection"
            )
        )
    )

    # Best canonical structure.
    if (
        matchup
        and bet_type != "OTHER"
    ):
        return "|".join(
            [
                picker,
                str(week),
                bet_type,
                matchup,
                side,
                line_key,
            ]
        )

    # Fallback to normalized exact wager text.
    return "|".join(
        [
            picker,
            str(week),
            bet_type,
            selection,
            side,
            line_key,
        ]
    )


def semantic_duplicate_key(
    pick
):
    """
    Secondary dedupe designed to catch repeated cards where
    metadata differs slightly but the actual wager is identical.
    """

    picker = (
        normalize_picker(
            pick.get(
                "picker"
            )
        )
        or ""
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

    side_raw = pick.get(
        "side"
    )

    if (
        side_raw
        and str(side_raw).upper()
        in {
            "OVER",
            "UNDER",
        }
    ):
        side = str(
            side_raw
        ).upper()
    else:
        side = normalize_team(
            side_raw
        )

    line = safe_float(
        pick.get(
            "line"
        )
    )

    line_key = (
        ""
        if line is None
        else f"{line:.3f}"
    )

    selection = (
        normalized_selection_key(
            pick.get(
                "selection"
            )
        )
    )

    # Remove matchup syntax so "BYU -51.5" remains stable.
    selection = re.sub(
        r"\s+",
        " ",
        selection,
    )

    return "|".join(
        [
            picker,
            str(week),
            bet_type,
            side,
            line_key,
            selection,
        ]
    )


def dedupe_existing_picks(
    picks
):
    """
    IMPORTANT:
    This cleans already-stored duplicates too.

    Preference:
    1. Already graded row
    2. Earlier source row
    3. First row encountered
    """

    if not picks:
        return []

    kept = []
    positions = {}

    removed = 0

    def row_score(pick):
        result = str(
            pick.get(
                "result"
            )
            or ""
        ).upper()

        graded = (
            result
            in {
                "WIN",
                "LOSS",
                "PUSH",
            }
        )

        posted = str(
            pick.get(
                "posted_at"
            )
            or "9999"
        )

        return (
            1 if graded else 0,
            posted,
        )

    for pick in picks:
        key = canonical_pick_key(
            pick
        )

        if key not in positions:
            positions[
                key
            ] = len(
                kept
            )

            kept.append(
                pick
            )

            continue

        index = positions[
            key
        ]

        current = kept[
            index
        ]

        current_result = str(
            current.get(
                "result"
            )
            or ""
        ).upper()

        candidate_result = str(
            pick.get(
                "result"
            )
            or ""
        ).upper()

        current_graded = (
            current_result
            in {
                "WIN",
                "LOSS",
                "PUSH",
            }
        )

        candidate_graded = (
            candidate_result
            in {
                "WIN",
                "LOSS",
                "PUSH",
            }
        )

        # If duplicate copy is graded and current one isn't,
        # preserve the graded copy.
        if (
            candidate_graded
            and not current_graded
        ):
            kept[
                index
            ] = pick

        removed += 1

        print(
            "REMOVED STORED DUPLICATE:",
            pick.get(
                "picker"
            ),
            "|",
            pick.get(
                "selection"
            ),
            "| source:",
            pick.get(
                "source_post_id"
            ),
        )

    if removed:
        print(
            "Stored duplicate picks removed:",
            removed,
        )

    return kept


# ============================================================
# GUARANTEED 2026 WEEK-1 RECONCILIATION
# ============================================================

def is_rico_byu_spread(
    pick
):
    picker = normalize_picker(
        pick.get(
            "picker"
        )
    )

    if picker != "Rico Bosco":
        return False

    try:
        if int(
            pick.get(
                "week"
            )
            or 0
        ) != 1:
            return False
    except Exception:
        return False

    line = safe_float(
        pick.get(
            "line"
        )
    )

    selection = normalize_text(
        pick.get(
            "selection"
        )
    )

    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
    )

    return (
        abs(
            (line or 0)
            - (-51.5)
        ) < 0.01
        and (
            "byu"
            in selection
            or normalize_team(
                pick.get(
                    "team"
                )
            ) == "byu"
            or normalize_team(
                pick.get(
                    "side"
                )
            ) == "byu"
        )
        and bet_type
        == "SPREAD"
    )


def cleanup_rico_byu_duplicate(
    picks
):
    """
    Extra narrow safety repair for the known Week 1 duplicate.

    Rico's BYU -51.5 should appear exactly ONCE.
    """

    matching = [
        i
        for i, pick in enumerate(
            picks
        )
        if is_rico_byu_spread(
            pick
        )
    ]

    if len(
        matching
    ) <= 1:
        return picks

    # Prefer a graded row.
    keep_index = matching[
        0
    ]

    for index in matching:
        result = str(
            picks[index].get(
                "result"
            )
            or ""
        ).upper()

        if result in {
            "WIN",
            "LOSS",
            "PUSH",
        }:
            keep_index = index
            break

    cleaned = []

    for index, pick in enumerate(
        picks
    ):
        if (
            index in matching
            and index != keep_index
        ):
            print(
                "REMOVED RICO BYU DUPLICATE:",
                pick.get(
                    "source_post_id"
                ),
            )
            continue

        cleaned.append(
            pick
        )

    return cleaned


def rico_nd_under_exists(
    picks
):
    for pick in picks:
        if (
            normalize_picker(
                pick.get(
                    "picker"
                )
            )
            != "Rico Bosco"
        ):
            continue

        try:
            if int(
                pick.get(
                    "week"
                )
                or 0
            ) != 1:
                continue
        except Exception:
            continue

        if (
            normalize_bet_type(
                pick.get(
                    "bet_type"
                )
            )
            != "TOTAL"
        ):
            continue

        line = safe_float(
            pick.get(
                "line"
            )
        )

        side = str(
            pick.get(
                "side"
            )
            or ""
        ).upper()

        selection = normalize_text(
            pick.get(
                "selection"
            )
        )

        if (
            line is not None
            and abs(
                line - 46.5
            ) < 0.01
            and side == "UNDER"
            and (
                (
                    "wis"
                    in selection
                    or "wisconsin"
                    in selection
                )
                and (
                    "nd"
                    in selection
                    or "notre dame"
                    in selection
                )
            )
        ):
            return True

    return False


def ensure_rico_nd_under(
    picks
):
    """
    The official 2026 Week 1 Rico card contains:

        WIS @ ND
        Under 46.5

    An earlier extraction missed the direction and therefore
    omitted the wager completely.

    This repair is intentionally narrow and idempotent.
    """

    if rico_nd_under_exists(
        picks
    ):
        return picks

    source_post_id = (
        "2095584927836217769"
    )

    source_url = (
        "https://x.com/"
        "barstoolpickem/status/"
        f"{source_post_id}"
    )

    new_pick = {
        "picker":
            "Rico Bosco",

        "sport":
            "CFB",

        "matchup":
            "Wisconsin @ Notre Dame",

        "team":
            "Wisconsin",

        "opponent":
            "Notre Dame",

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

        "week":
            1,

        "added_pick":
            False,

        "confidence":
            1.0,

        # Grader will grade this during the same workflow.
        "status":
            "OPEN",

        "result":
            None,

        "profit_units":
            0,

        "source_post_id":
            source_post_id,

        "source_url":
            source_url,

        "source_text":
            "Official Rico Bosco Week 1 pick card",

        "source_is_reply":
            False,

        "conversation_id":
            source_post_id,

        "posted_at":
            "2026-09-01T00:00:00Z",

        "graded_at":
            None,

        "final_score":
            None,

        "event_id":
            None,
    }

    new_pick[
        "id"
    ] = stable_id(
        canonical_pick_key(
            new_pick
        )
    )

    picks.append(
        new_pick
    )

    print(
        "REPAIRED MISSING OFFICIAL PICK:",
        "Rico Bosco | Week 1 | "
        "WIS @ ND Under 46.5",
    )

    return picks


def reconcile_known_week1_issues(
    picks
):
    """
    Fix the two currently confirmed data-quality issues.
    """

    picks = cleanup_rico_byu_duplicate(
        picks
    )

    picks = ensure_rico_nd_under(
        picks
    )

    picks = dedupe_existing_picks(
        picks
    )

    return picks


# ============================================================
# EXISTING MARKET REPAIR
# ============================================================

def repair_existing_markets(
    existing
):
    repaired = 0

    for index, pick in enumerate(
        existing
    ):
        before = (
            pick.get(
                "bet_type"
            ),
            pick.get(
                "side"
            ),
            pick.get(
                "line"
            ),
        )

        upgraded = (
            infer_market_from_selection(
                pick
            )
        )

        after = (
            upgraded.get(
                "bet_type"
            ),
            upgraded.get(
                "side"
            ),
            upgraded.get(
                "line"
            ),
        )

        existing[
            index
        ] = upgraded

        if before != after:
            repaired += 1

    return repaired


# ============================================================
# STORE NEW PICK
# ============================================================

def make_stored_pick(
    extracted_pick,
    post,
    post_url,
    picker,
    week,
    added_pick,
    reply_hint,
):
    bet_type = normalize_bet_type(
        extracted_pick.get(
            "bet_type"
        )
    )

    confidence = float(
        extracted_pick.get(
            "confidence",
            0.95,
        )
        or 0.95
    )

    if confidence < 0.90:
        status = "REVIEW"

    elif bet_type in (
        SUPPORTED_MARKETS
    ):
        status = "OPEN"

    else:
        status = "REVIEW"

    key_pick = dict(
        extracted_pick
    )

    key_pick[
        "picker"
    ] = picker

    key_pick[
        "week"
    ] = week

    canonical = (
        canonical_pick_key(
            key_pick
        )
    )

    return {
        "id":
            stable_id(
                canonical
            ),

        "picker":
            picker,

        "sport":
            (
                extracted_pick.get(
                    "sport"
                )
                or "CFB"
            ),

        "matchup":
            extracted_pick.get(
                "matchup"
            ),

        "team":
            extracted_pick.get(
                "team"
            ),

        "opponent":
            extracted_pick.get(
                "opponent"
            ),

        "bet_type":
            bet_type,

        "selection":
            clean_text(
                extracted_pick.get(
                    "selection"
                )
            ),

        "side":
            extracted_pick.get(
                "side"
            ),

        "line":
            safe_float(
                extracted_pick.get(
                    "line"
                )
            ),

        "odds":
            extracted_pick.get(
                "odds"
            ),

        "week":
            week,

        "added_pick":
            bool(
                added_pick
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


# ============================================================
# PROCESS POSTS
# ============================================================

def process_posts(
    existing,
    posts,
    media_map,
    official_user_id,
    recovery_post_ids=None,
):
    recovery_post_ids = set(
        recovery_post_ids
        or []
    )

    parent_cache = {}

    # Existing keys.
    seen = {
        canonical_pick_key(
            pick
        )
        for pick in existing
    }

    candidate_count = 0
    reply_candidate_count = 0
    new_pick_count = 0

    verified_posts = []

    for post in posts:
        author_id = str(
            post.get(
                "author_id"
            )
            or ""
        )

        if (
            author_id
            and author_id
            != str(
                official_user_id
            )
        ):
            continue

        verified_posts.append(
            post
        )

    for post in sorted(
        verified_posts,
        key=lambda p:
            int(
                p.get(
                    "id"
                )
                or 0
            ),
    ):
        post_id = str(
            post.get(
                "id"
            )
        )

        reply_hint = (
            is_reply_post(
                post
            )
        )

        # Historical recovery is reply-only.
        if (
            post_id
            in recovery_post_ids
            and not reply_hint
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

        if not looks_like_pick_post(
            text,
            image_urls,
        ):
            continue

        candidate_count += 1

        if reply_hint:
            reply_candidate_count += 1

        parent_text = ""

        if reply_hint:
            parent_text = (
                official_parent_context(
                    post,
                    official_user_id,
                    parent_cache,
                )
            )

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

        week = (
            infer_week_from_date(
                post.get(
                    "created_at"
                )
            )
        )

        post_url = (
            "https://x.com/"
            f"{USERNAME}/status/"
            f"{post_id}"
        )

        try:
            payload = (
                parse_post_with_ai(
                    text=text,
                    image_urls=image_urls,
                    post_url=post_url,
                    posted_at=post.get(
                        "created_at"
                    ),
                    inferred_week=week,
                    picker_hint=picker_hint,
                    added_hint=added_hint,
                    reply_hint=reply_hint,
                    official_parent_text=
                        parent_text,
                )
            )

        except Exception as exc:
            print(
                "AI EXTRACTION FAILED:",
                post_id,
                type(exc).__name__,
                exc,
            )

            continue

        extracted = payload.get(
            "picks",
            [],
        )

        if not isinstance(
            extracted,
            list,
        ):
            continue

        for raw_pick in extracted:

            normalized = (
                normalize_extracted_pick(
                    raw_pick,
                    inferred_week=week,
                    picker_hint=
                        picker_hint,
                )
            )

            if not normalized:
                continue

            picker = normalize_picker(
                normalized.get(
                    "picker"
                )
            )

            if picker not in {
                "Big Cat",
                "Stool Presidente",
                "Rico Bosco",
            }:
                continue

            pick_week = (
                normalized.get(
                    "week"
                )
                or week
            )

            if not pick_week:
                print(
                    "SKIPPING PICK WITHOUT WEEK:",
                    normalized.get(
                        "selection"
                    ),
                )
                continue

            normalized[
                "picker"
            ] = picker

            normalized[
                "week"
            ] = int(
                pick_week
            )

            canonical = (
                canonical_pick_key(
                    normalized
                )
            )

            # ------------------------------------------------
            # DEDUPE ACROSS DIFFERENT SOURCE POSTS
            # ------------------------------------------------

            if canonical in seen:
                print(
                    "DUPLICATE SKIPPED:",
                    picker,
                    "| Week",
                    pick_week,
                    "|",
                    normalized.get(
                        "selection"
                    ),
                    "| source:",
                    post_id,
                )

                continue

            added_pick = bool(
                normalized.get(
                    "added_pick"
                )
            )

            if added_hint:
                added_pick = True

            stored = make_stored_pick(
                extracted_pick=
                    normalized,
                post=post,
                post_url=post_url,
                picker=picker,
                week=int(
                    pick_week
                ),
                added_pick=
                    added_pick,
                reply_hint=
                    reply_hint,
            )

            existing.append(
                stored
            )

            seen.add(
                canonical
            )

            new_pick_count += 1

            print(
                "ADDED:",
                picker,
                "| Week",
                pick_week,
                "|",
                stored.get(
                    "selection"
                ),
                "| reply:",
                reply_hint,
                "| market:",
                stored.get(
                    "bet_type"
                ),
            )

    return (
        existing,
        candidate_count,
        reply_candidate_count,
        new_pick_count,
    )


# ============================================================
# INGEST
# ============================================================

def ingest():
    print()
    print(
        "===================================="
    )
    print(
        "BARSTOOL PICK EM X INGEST"
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

    # --------------------------------------------------------
    # CLEAN CURRENT STORED DATA BEFORE ANY NEW INGESTION
    # --------------------------------------------------------

    repaired = (
        repair_existing_markets(
            existing
        )
    )

    if repaired:
        print(
            "Existing market metadata repaired:",
            repaired,
        )

    # Generic canonical cleanup.
    existing = (
        dedupe_existing_picks(
            existing
        )
    )

    # Confirmed Week 1 repairs:
    # 1. remove duplicate Rico BYU -51.5
    # 2. guarantee Rico WIS @ ND Under 46.5 exists
    existing = (
        reconcile_known_week1_issues(
            existing
        )
    )

    official_user_id = (
        resolve_user_id()
    )

    print(
        "Official X account:",
        USERNAME,
        "| user_id:",
        official_user_id,
    )

    # --------------------------------------------------------
    # NORMAL TIMELINE
    # --------------------------------------------------------

    should_backfill = (
        not state.get(
            "backfill_complete"
        )
    )

    if should_backfill:
        timeline_posts, timeline_media = (
            fetch_user_posts(
                official_user_id,
                start_time=(
                    datetime.now(
                        timezone.utc
                    )
                    - timedelta(
                        days=
                            BACKFILL_DAYS
                    )
                ),
                max_pages=
                    MAX_BACKFILL_PAGES,
            )
        )

        print(
            "Normal timeline backfill fetched:",
            len(
                timeline_posts
            ),
        )

    else:
        timeline_posts, timeline_media = (
            fetch_user_posts(
                official_user_id,
                since_id=state.get(
                    "last_x_post_id"
                ),
                max_pages=1,
            )
        )

        print(
            "Normal timeline incremental fetched:",
            len(
                timeline_posts
            ),
        )

    timeline_ids = {
        str(
            post.get(
                "id"
            )
        )
        for post in timeline_posts
        if post.get(
            "id"
        )
    }

    # --------------------------------------------------------
    # ONE-TIME OFFICIAL REPLY-ONLY RECOVERY
    #
    # New flag so this version can make one safe historical
    # reply pass without reparsing old top-level cards.
    # --------------------------------------------------------

    recovery_flag = (
        "reply_only_recovery_v3_complete"
    )

    recovery_posts = []
    recovery_media = {}
    recovery_ids = set()

    recovery_success = False

    if not state.get(
        recovery_flag
    ):
        try:
            raw_recovery_posts, (
                recovery_media
            ) = fetch_user_posts(
                official_user_id,
                start_time=(
                    datetime.now(
                        timezone.utc
                    )
                    - timedelta(
                        days=
                            REPLY_RECOVERY_DAYS
                    )
                ),
                max_pages=
                    MAX_REPLY_RECOVERY_PAGES,
            )

            # CRITICAL:
            # recovery may contribute ONLY official replies.
            recovery_posts = [
                post
                for post
                in raw_recovery_posts
                if (
                    is_reply_post(
                        post
                    )
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

            recovery_ids = {
                str(
                    post.get(
                        "id"
                    )
                )
                for post
                in recovery_posts
                if post.get(
                    "id"
                )
            }

            recovery_success = True

            print(
                "Official reply-only recovery fetched:",
                len(
                    recovery_posts
                ),
            )

        except Exception as exc:
            print(
                "REPLY RECOVERY FAILED:",
                type(exc).__name__,
                exc,
            )

    # --------------------------------------------------------
    # COMBINE WITHOUT DUPLICATING POSTS
    # --------------------------------------------------------

    post_map = {}

    for post in timeline_posts:
        post_id = str(
            post.get(
                "id"
            )
        )

        if post_id:
            post_map[
                post_id
            ] = post

    for post in recovery_posts:
        post_id = str(
            post.get(
                "id"
            )
        )

        if (
            post_id
            and post_id
            not in post_map
        ):
            post_map[
                post_id
            ] = post

    combined_posts = list(
        post_map.values()
    )

    combined_media = dict(
        timeline_media
    )

    combined_media.update(
        recovery_media
    )

    # Only IDs that came exclusively from recovery need the
    # reply-only enforcement path.
    historical_recovery_ids = (
        recovery_ids
        - timeline_ids
    )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    (
        existing,
        candidate_count,
        reply_candidate_count,
        new_pick_count,
    ) = process_posts(
        existing=existing,
        posts=combined_posts,
        media_map=combined_media,
        official_user_id=
            official_user_id,
        recovery_post_ids=
            historical_recovery_ids,
    )

    # --------------------------------------------------------
    # CLEAN AGAIN AFTER NEW EXTRACTIONS
    # --------------------------------------------------------

    existing = (
        dedupe_existing_picks(
            existing
        )
    )

    existing = (
        reconcile_known_week1_issues(
            existing
        )
    )

    # --------------------------------------------------------
    # ADVANCE NORMAL TIMELINE CHECKPOINT
    #
    # Recovery posts do NOT control last_x_post_id.
    # --------------------------------------------------------

    if timeline_ids:
        newest_id = str(
            max(
                int(x)
                for x
                in timeline_ids
            )
        )

        previous_id = state.get(
            "last_x_post_id"
        )

        if (
            not previous_id
            or int(
                newest_id
            )
            > int(
                previous_id
            )
        ):
            state[
                "last_x_post_id"
            ] = newest_id

    if should_backfill:
        state[
            "backfill_complete"
        ] = True

    if recovery_success:
        state[
            recovery_flag
        ] = True

    state[
        "updated_at"
    ] = now_iso()

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_json(
        PICKS_FILE,
        existing,
    )

    save_json(
        STATE_FILE,
        state,
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()
    print(
        "========== INGEST SUMMARY =========="
    )

    print(
        "Official candidate posts inspected:",
        candidate_count,
    )

    print(
        "Official reply/subtweet candidates:",
        reply_candidate_count,
    )

    print(
        "New picks added:",
        new_pick_count,
    )

    print(
        "Total stored picks:",
        len(
            existing
        ),
    )

    print(
        "Rico WIS @ ND Under 46.5 present:",
        rico_nd_under_exists(
            existing
        ),
    )

    print(
        "Reply-only recovery v3 complete:",
        state.get(
            recovery_flag
        ),
    )

    print(
        "===================================="
    )


if __name__ == "__main__":
    ingest()
