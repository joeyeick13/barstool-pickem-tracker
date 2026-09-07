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

# One-time recovery window for replies/subtweets that were
# missed before reply-aware ingestion existed.
REPLY_RECOVERY_DAYS = 10
MAX_REPLY_RECOVERY_PAGES = 4

# Prevent excessive parent-chain lookups.
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
        f"/users/by/username/{USERNAME}"
    )

    return str(
        payload[
            "data"
        ][
            "id"
        ]
    )


def iso_x_time(
    dt,
):
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
# OFFICIAL USER TIMELINE
# ============================================================

def fetch_user_posts(
    user_id,
    since_id=None,
    start_time=None,
    max_pages=1,
):
    """
    Fetch posts authored by one specific X user.

    IMPORTANT:
    /users/{user_id}/tweets is the account's OWN timeline.

    We exclude retweets but intentionally DO NOT exclude
    replies.

    We also request author_id and later verify every post
    matches user_id before parsing. This prevents fan replies
    from ever becoming tracker picks.
    """

    params = {
        "max_results": 100,

        # Replies must remain included.
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
        ] = str(
            since_id
        )

    if start_time:
        params[
            "start_time"
        ] = iso_x_time(
            start_time
        )

    all_posts = []
    media_map = {}

    pages = 0

    while True:

        payload = x_get(
            f"/users/{user_id}/tweets",
            params,
        )

        for post in payload.get(
            "data",
            [],
        ):

            # Belt-and-suspenders author verification.
            #
            # If X supplies author_id and it does not match
            # the official account ID, reject it.
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
                    user_id
                )
            ):

                print(
                    "REJECTED NON-OFFICIAL AUTHOR:",
                    post.get(
                        "id"
                    ),
                    "| author:",
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

            media_key = (
                media.get(
                    "media_key"
                )
            )

            if media_key:

                media_map[
                    media_key
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
        all_posts,
        media_map,
    )


# ============================================================
# SINGLE POST FETCH
# ============================================================

def fetch_post_by_id(
    post_id,
):
    """
    Fetch one X post.

    author_id is included so parent context can be restricted
    to posts authored by Barstool Pick Em itself.
    """

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

        media_key = (
            media.get(
                "media_key"
            )
        )

        if media_key:

            media_map[
                media_key
            ] = media

    return (
        post,
        media_map,
    )


# ============================================================
# MERGE TIMELINE SOURCES
# ============================================================

def merge_post_sources(
    sources,
):
    """
    Merge multiple fetches of the SAME official user timeline.

    sources:
        [
            (posts, media),
            (posts, media),
            ...
        ]

    Deduplicate by X post ID.
    """

    posts_by_id = {}
    media_map = {}

    for posts, media in sources:

        for post in posts:

            post_id = str(
                post.get(
                    "id"
                )
                or ""
            )

            if post_id:

                posts_by_id[
                    post_id
                ] = post

        media_map.update(
            media
        )

    return (
        list(
            posts_by_id.values()
        ),
        media_map,
    )


# ============================================================
# REPLY DETECTION
# ============================================================

def replied_to_post_id(
    post,
):
    for reference in (
        post.get(
            "referenced_tweets"
        )
        or []
    ):

        if (
            reference.get(
                "type"
            )
            == "replied_to"
        ):

            value = (
                reference.get(
                    "id"
                )
            )

            if value:

                return str(
                    value
                )

    return None


def is_reply_post(
    post,
):
    if replied_to_post_id(
        post
    ):

        return True

    return bool(
        post.get(
            "in_reply_to_user_id"
        )
    )


# ============================================================
# OFFICIAL THREAD CONTEXT
# ============================================================

def official_parent_context(
    post,
    official_user_id,
    cache,
):
    """
    Retrieve parent/ancestor text ONLY when those ancestors
    were also authored by Barstool Pick Em.

    This solves cases like:

        Parent:
            Adds for @BarstoolBigCat

        Reply:
            Tulane/Duke Over 50.5
            Oklahoma State Over 57.5

    The reply itself contains the wagers but not the picker.
    The official parent provides attribution.

    If the parent is a fan or any other account, its text is
    ignored completely.
    """

    parent_id = replied_to_post_id(
        post
    )

    if not parent_id:

        return ""

    context_parts = []

    current_parent_id = (
        parent_id
    )

    depth = 0

    while (
        current_parent_id
        and depth
        < MAX_PARENT_DEPTH
    ):

        depth += 1

        if (
            current_parent_id
            in cache
        ):

            parent = cache[
                current_parent_id
            ]

        else:

            try:

                parent, _ = (
                    fetch_post_by_id(
                        current_parent_id
                    )
                )

            except Exception as exc:

                print(
                    "PARENT FETCH FAILED:",
                    current_parent_id,
                    "|",
                    type(
                        exc
                    ).__name__,
                    "|",
                    exc,
                )

                cache[
                    current_parent_id
                ] = None

                break

            cache[
                current_parent_id
            ] = parent

        if not parent:
            break

        author_id = str(
            parent.get(
                "author_id"
            )
            or ""
        )

        # Critical safety rule:
        # Never use a fan/other account's reply as picker
        # attribution context.
        if (
            author_id
            != str(
                official_user_id
            )
        ):

            print(
                "IGNORING NON-OFFICIAL PARENT:",
                current_parent_id,
                "| author:",
                author_id,
            )

            break

        parent_text = str(
            parent.get(
                "text"
            )
            or ""
        ).strip()

        if parent_text:

            context_parts.append(
                parent_text
            )

        current_parent_id = (
            replied_to_post_id(
                parent
            )
        )

    return "\n\n".join(
        context_parts
    )


# ============================================================
# WEEK MAPPING
# ============================================================

def infer_week_from_date(
    created_at,
):
    """
    2026 Pick Em mapping.

    Week 1:
        Aug 22 - Sep 7

    Week 2:
        Sep 8 - Sep 13

    Week 3:
        Sep 14 - Sep 20

    Week 4+:
        Monday-Sunday.
    """

    if not created_at:
        return None

    try:

        dt = datetime.fromisoformat(
            str(
                created_at
            ).replace(
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

    if dt.year == 2026:

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

    # Generic fallback for future seasons.
    september_first = datetime(
        dt.year,
        9,
        1,
        tzinfo=timezone.utc,
    )

    week_one_monday = (
        september_first
        - timedelta(
            days=
                september_first.weekday()
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
        delta_days
        // 7
    ) + 1


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(
    value,
):
    value = str(
        value or ""
    ).lower()

    value = (
        value
        .replace(
            "−",
            "-",
        )
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .replace(
            "½",
            ".5",
        )
        .replace(
            "&amp;",
            "&",
        )
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = re.sub(
        r"[^a-z0-9+.\-&'()/@ ]",
        "",
        value,
    )

    return value.strip()


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_bet_type(
    value,
):
    raw = str(
        value
        or "OTHER"
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

        "FIRST_QUARTER_SPREAD":
            "FIRST_QUARTER_SPREAD",

        "FIRST_QUARTER_TOTAL":
            "FIRST_QUARTER_TOTAL",

        "FIRST_QUARTER_MONEYLINE":
            "FIRST_QUARTER_MONEYLINE",

        "FIRST_QUARTER_TEAM_TOTAL":
            "FIRST_QUARTER_TEAM_TOTAL",

        "FIRST_HALF_SPREAD":
            "FIRST_HALF_SPREAD",

        "FIRST_HALF_TOTAL":
            "FIRST_HALF_TOTAL",

        "FIRST_HALF_MONEYLINE":
            "FIRST_HALF_MONEYLINE",

        "FIRST_HALF_TEAM_TOTAL":
            "FIRST_HALF_TEAM_TOTAL",

        "SPREAD":
            "SPREAD",

        "TOTAL":
            "TOTAL",

        "MONEYLINE":
            "MONEYLINE",

        "TEAM_TOTAL":
            "TEAM_TOTAL",
    }

    return aliases.get(
        raw,
        raw,
    )


def infer_market_from_selection(
    pick,
):
    """
    Deterministic cleanup after AI extraction.
    """

    pick = dict(
        pick
    )

    selection = str(
        pick.get(
            "selection"
        )
        or ""
    ).strip()

    text = (
        selection
        .lower()
        .replace(
            "−",
            "-",
        )
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .replace(
            "½",
            ".5",
        )
    )

    current_type = (
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
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
            ] = (
                "TEAM_TOTAL"
            )

        compact = re.search(
            r"\b(?:"
            r"tt|team\s*total"
            r")\s*"
            r"(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\b",
            text,
            flags=re.I,
        )

        if compact:

            pick[
                "side"
            ] = (
                "OVER"
                if (
                    compact
                    .group(1)
                    .lower()
                    == "o"
                )
                else "UNDER"
            )

            pick[
                "line"
            ] = float(
                compact.group(
                    2
                )
            )

            return pick

        verbose = re.search(
            r"\b(?:"
            r"tt|team\s*total"
            r")\s*"
            r"(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\b",
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
                verbose.group(
                    2
                )
            )

        return pick

    # --------------------------------------------------------
    # PERIOD MARKETS
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
            ] = current_type

        return pick

    pick[
        "bet_type"
    ] = current_type

    return pick


# ============================================================
# PICK CANDIDATE FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    """
    Images are inspected because weekly cards are image posts.

    Text-only replies/subtweets must still look like wagers.
    """

    if image_urls:
        return True

    t = str(
        text or ""
    ).lower()

    keywords = [
        "adds for",
        "add for",
        "mortal lock",
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
        keyword in t
        for keyword in keywords
    ):

        return True

    # Examples:
    # Cal +2.5
    # UNLV -3
    # Central Michigan +11.5

    if re.search(
        r"\b[a-z][a-z .&'-]{1,35}"
        r"\s[+-]\d+(?:\.\d+)?\b",
        t,
    ):

        return True

    return False


# ============================================================
# PICKER / ADDITION CONTEXT
# ============================================================

def picker_hint_from_text(
    text,
):
    t = str(
        text or ""
    ).lower()

    if (
        "barstoolbigcat"
        in t
        or "big cat"
        in t
        or "bigcat"
        in t
    ):

        return "Big Cat"

    if (
        "stoolpresidente"
        in t
        or "stool presidente"
        in t
        or "dave portnoy"
        in t
        or "portnoy"
        in t
        or "el pres"
        in t
    ):

        return "Stool Presidente"

    if (
        "ricobosco"
        in t
        or "rico bosco"
        in t
        or re.search(
            r"\brico\b",
            t,
        )
    ):

        return "Rico Bosco"

    return None


def is_added_post(
    text,
):
    t = str(
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
        term in t
        for term in terms
    )


# ============================================================
# OPENAI EXTRACTION
# ============================================================

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
You are extracting gambling picks from the official
Barstool Pick Em X account.

ONLY track picks belonging to:
- Big Cat
- Stool Presidente / Dave Portnoy / El Presidente / Pres
- Rico Bosco

IMPORTANT SOURCE SAFETY:
The actual SOURCE POST below has already been verified by the
program to be authored by the official Barstool Pick Em account.

Any parent/thread text supplied below is used ONLY for context
and has also been verified as authored by Barstool Pick Em.

Never extract a wager from parent context unless that wager is
also explicitly present in the actual SOURCE POST.

SOURCE POST:
{post_url}

POSTED AT:
{posted_at}

ACTUAL SOURCE POST TEXT:
{text}

OFFICIAL BARSTOOL PICK EM PARENT/THREAD CONTEXT:
{official_parent_text or "None"}

CONTEXT HINTS:
Likely picker: {picker_hint}
Likely college-football week: {inferred_week}
Likely added-pick post: {added_hint}
This source post is a reply/subtweet: {reply_hint}

Return JSON only using exactly this structure:

{{
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "matchup or identifying game text, or null",
      "team": "team being wagered on or team identifying the matchup, or null",
      "opponent": "opponent if visible, or null",
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
      "selection": "concise exact wager",
      "side": "OVER|UNDER|team name|null",
      "line": 0.0,
      "odds": null,
      "units": 1,
      "mortal_lock": false,
      "week": 1,
      "added_pick": false,
      "confidence": 0.99
    }}
  ]
}}

RULES:

1. Extract wagers ONLY from the ACTUAL SOURCE POST.

2. Parent/thread context may identify WHO the reply belongs to,
   but parent wagers must not be re-extracted from the parent.

3. A Barstool Pick Em reply/subtweet can contain a valid new
   wager.

4. Example:

   Parent:
   "Adds for @BarstoolBigCat"

   Actual reply:
   "Tulane/Duke Over 50.5
    Oklahoma State Over 57.5"

   Extract exactly two Big Cat wagers from the reply.

5. If the parent says "Adds for @BarstoolBigCat", then a reply
   directly underneath containing wagers should generally be
   attributed to Big Cat.

6. Never infer picks from:
   - fan comments
   - promotional text
   - historical results
   - kickoff times
   - record summaries
   - commentary
   - jokes

7. Weekly image cards can contain multiple wagers. Extract every
   explicit wager visible.

8. SPREAD:

   "Cal +2.5"

   bet_type = SPREAD
   team = "Cal"
   side = "Cal"
   line = 2.5

9. GAME TOTAL:

   "FIU/USF Over 53.5"

   bet_type = TOTAL
   matchup = "FIU/USF"
   side = "OVER"
   line = 53.5

10. A one-team phrase like:
    "Texas A&M Over 53.5"

    is a GAME TOTAL unless the source explicitly says TT or
    team total.

11. TEAM TOTAL:

    "Alabama TT over 40.5"

    bet_type = TEAM_TOTAL
    team = "Alabama"
    side = "OVER"
    line = 40.5

12. Compact team total:

    "Oregon TT o37.5"

    bet_type = TEAM_TOTAL
    team = "Oregon"
    side = "OVER"
    line = 37.5

    o = OVER
    u = UNDER

13. FIRST QUARTER:

    "Oklahoma 1Q -9.5"

    bet_type = FIRST_QUARTER_SPREAD
    team = "Oklahoma"
    side = "Oklahoma"
    line = -9.5

14. FIRST HALF:

    "Miami 1H -13.5"

    bet_type = FIRST_HALF_SPREAD

    "Indiana first half TT Over 27.5"

    bet_type = FIRST_HALF_TEAM_TOTAL
    team = "Indiana"
    side = "OVER"
    line = 27.5

15. Never turn a 1Q or 1H wager into a full-game wager.

16. added_pick = true when the actual post or verified parent
    context clearly indicates these are additions.

17. Mortal Lock is true only when explicitly identified.

18. units = 1 unless explicitly stated otherwise.

19. odds = null unless explicitly displayed.

20. Confidence:
    0.95-1.00 = clearly readable and attributable
    below 0.90 = meaningful uncertainty

21. If the actual source post contains no new wagers:
    return {{"picks":[]}}

JSON only.
No markdown.
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

                "detail":
                    "high",
            }
        )

    response = (
        client
        .responses
        .create(
            model=os.getenv(
                "OPENAI_MODEL",
                "gpt-5.6-luna",
            ),

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

    raw = (
        response
        .output_text
        .strip()
    )

    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw,
        flags=re.S,
    )

    parsed = json.loads(
        raw
    )

    return [
        infer_market_from_selection(
            pick
        )
        for pick
        in parsed.get(
            "picks",
            [],
        )
    ]


# ============================================================
# DEDUPLICATION
# ============================================================

def canonical_pick_key(
    pick,
):
    """
    X post ID deliberately excluded.

    If the exact wager gets repeated later, it should not be
    counted twice simply because it appeared in two posts.
    """

    picker = (
        normalize_picker(
            pick.get(
                "picker"
            )
        )
        or ""
    )

    week = (
        pick.get(
            "week"
        )
        or ""
    )

    bet_type = normalize_text(
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    matchup = normalize_text(
        pick.get(
            "matchup"
        )
    )

    team = normalize_text(
        pick.get(
            "team"
        )
    )

    side = normalize_text(
        pick.get(
            "side"
        )
    )

    line = pick.get(
        "line"
    )

    try:

        if line is not None:

            line = float(
                line
            )

            if line.is_integer():

                line = int(
                    line
                )

    except Exception:

        pass

    selection = normalize_text(
        pick.get(
            "selection"
        )
    )

    structured = "|".join(
        [
            picker,
            str(
                week
            ),
            bet_type,
            matchup,
            team,
            side,
            str(
                line
            ),
        ]
    )

    if (
        not matchup
        and not team
    ):

        structured += (
            "|"
            + selection
        )

    return structured


def stable_id(
    canonical_key,
):
    return (
        hashlib
        .sha1(
            canonical_key.encode()
        )
        .hexdigest()[:16]
    )


def existing_keys(
    existing,
):
    keys = set()

    for pick in existing:

        copy = dict(
            pick
        )

        if not copy.get(
            "week"
        ):

            copy[
                "week"
            ] = infer_week_from_date(
                copy.get(
                    "posted_at"
                )
            )

        copy = (
            infer_market_from_selection(
                copy
            )
        )

        keys.add(
            canonical_pick_key(
                copy
            )
        )

    return keys


# ============================================================
# EXISTING MARKET REPAIR
# ============================================================

def repair_existing_markets(
    existing,
):
    repaired = 0

    for pick in existing:

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

        normalized = (
            infer_market_from_selection(
                pick
            )
        )

        pick[
            "bet_type"
        ] = normalize_bet_type(
            normalized.get(
                "bet_type"
            )
        )

        if (
            normalized.get(
                "side"
            )
            is not None
        ):

            pick[
                "side"
            ] = normalized[
                "side"
            ]

        if (
            normalized.get(
                "line"
            )
            is not None
        ):

            pick[
                "line"
            ] = normalized[
                "line"
            ]

        after = (
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

        if before != after:

            repaired += 1

            print(
                "MARKET REPAIRED:",
                pick.get(
                    "picker"
                ),
                "|",
                pick.get(
                    "selection"
                ),
                "|",
                before,
                "->",
                after,
            )

    return repaired


def needs_source_reparse(
    pick,
):
    bet_type = (
        normalize_bet_type(
            pick.get(
                "bet_type"
            )
        )
    )

    if bet_type not in {
        "TEAM_TOTAL",
        "FIRST_QUARTER_TEAM_TOTAL",
        "FIRST_HALF_TEAM_TOTAL",
    }:

        return False

    side = str(
        pick.get(
            "side"
        )
        or ""
    ).upper()

    if side in {
        "OVER",
        "UNDER",
    }:

        return False

    return bool(
        pick.get(
            "source_post_id"
        )
    )


def candidate_matches_existing(
    old_pick,
    candidate,
):
    old_picker = (
        normalize_picker(
            old_pick.get(
                "picker"
            )
        )
    )

    new_picker = (
        normalize_picker(
            candidate.get(
                "picker"
            )
        )
    )

    if (
        old_picker
        and new_picker
        and old_picker
        != new_picker
    ):

        return False

    try:

        old_line = float(
            old_pick.get(
                "line"
            )
        )

        new_line = float(
            candidate.get(
                "line"
            )
        )

    except Exception:

        return False

    if abs(
        old_line
        - new_line
    ) > 0.001:

        return False

    old_team = normalize_text(
        old_pick.get(
            "team"
        )
    )

    new_team = normalize_text(
        candidate.get(
            "team"
        )
    )

    if (
        old_team
        and new_team
        and old_team
        != new_team
    ):

        return False

    return True


def repair_missing_direction_from_source(
    existing,
    official_user_id,
):
    targets = [
        pick
        for pick in existing
        if needs_source_reparse(
            pick
        )
    ]

    if not targets:
        return 0

    grouped = {}

    for pick in targets:

        post_id = str(
            pick.get(
                "source_post_id"
            )
        )

        grouped.setdefault(
            post_id,
            [],
        ).append(
            pick
        )

    repaired = 0

    parent_cache = {}

    for post_id, picks in grouped.items():

        try:

            post, media_map = (
                fetch_post_by_id(
                    post_id
                )
            )

            if not post:
                continue

            # Repair only from the official source account.
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

                print(
                    "SOURCE REPAIR REJECTED NON-OFFICIAL POST:",
                    post_id,
                )

                continue

            image_urls = []

            for media_key in (
                post
                .get(
                    "attachments",
                    {},
                )
                .get(
                    "media_keys",
                    [],
                )
            ):

                media = media_map.get(
                    media_key,
                    {},
                )

                if (
                    media.get(
                        "type"
                    )
                    == "photo"
                    and media.get(
                        "url"
                    )
                ):

                    image_urls.append(
                        media[
                            "url"
                        ]
                    )

            parent_text = (
                official_parent_context(
                    post,
                    official_user_id,
                    parent_cache,
                )
            )

            combined_context = (
                str(
                    post.get(
                        "text"
                    )
                    or ""
                )
                + "\n"
                + parent_text
            )

            picker_hint = (
                picker_hint_from_text(
                    combined_context
                )
            )

            added_hint = (
                is_added_post(
                    combined_context
                )
            )

            inferred_week = (
                infer_week_from_date(
                    post.get(
                        "created_at"
                    )
                )
            )

            extracted = (
                parse_post_with_ai(
                    text=
                        post.get(
                            "text",
                            "",
                        ),

                    image_urls=
                        image_urls,

                    post_url=(
                        f"https://x.com/"
                        f"{USERNAME}/status/"
                        f"{post_id}"
                    ),

                    posted_at=
                        post.get(
                            "created_at"
                        ),

                    inferred_week=
                        inferred_week,

                    picker_hint=
                        picker_hint,

                    added_hint=
                        added_hint,

                    reply_hint=
                        is_reply_post(
                            post
                        ),

                    official_parent_text=
                        parent_text,
                )
            )

            for old_pick in picks:

                matches = [
                    candidate
                    for candidate
                    in extracted
                    if (
                        candidate_matches_existing(
                            old_pick,
                            candidate,
                        )
                    )
                ]

                if len(
                    matches
                ) != 1:

                    continue

                candidate = (
                    infer_market_from_selection(
                        matches[
                            0
                        ]
                    )
                )

                direction = str(
                    candidate.get(
                        "side"
                    )
                    or ""
                ).upper()

                if direction not in {
                    "OVER",
                    "UNDER",
                }:

                    continue

                old_pick[
                    "side"
                ] = direction

                old_pick[
                    "bet_type"
                ] = normalize_bet_type(
                    candidate.get(
                        "bet_type"
                    )
                    or old_pick.get(
                        "bet_type"
                    )
                )

                if (
                    candidate.get(
                        "line"
                    )
                    is not None
                ):

                    old_pick[
                        "line"
                    ] = candidate[
                        "line"
                    ]

                if candidate.get(
                    "selection"
                ):

                    old_pick[
                        "selection"
                    ] = candidate[
                        "selection"
                    ]

                repaired += 1

        except Exception as exc:

            print(
                "SOURCE REPAIR FAILED:",
                post_id,
                "|",
                type(
                    exc
                ).__name__,
                "|",
                exc,
            )

    return repaired


# ============================================================
# INGEST
# ============================================================

def ingest():

    state = load_json(
        STATE_FILE,
        {
            "last_x_post_id":
                None,

            "backfill_complete":
                False,

            "reply_recovery_complete":
                False,
        },
    )

    if (
        "reply_recovery_complete"
        not in state
    ):

        state[
            "reply_recovery_complete"
        ] = False

    existing = load_json(
        PICKS_FILE,
        [],
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
    # REPAIR EXISTING MARKET METADATA
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

    source_repaired = (
        repair_missing_direction_from_source(
            existing,
            official_user_id,
        )
    )

    if source_repaired:

        print(
            "Existing source cards reparsed:",
            source_repaired,
        )

    # --------------------------------------------------------
    # NORMAL TIMELINE
    # --------------------------------------------------------

    should_backfill = (
        not existing
        or not state.get(
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
                        days=BACKFILL_DAYS
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

                since_id=
                    state.get(
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

    # --------------------------------------------------------
    # ONE-TIME REPLY/SUBTWEET RECOVERY
    # --------------------------------------------------------

    recovery_posts = []
    recovery_media = {}

    should_reply_recovery = (
        not state.get(
            "reply_recovery_complete"
        )
    )

    recovery_success = False

    if should_reply_recovery:

        try:

            recovery_posts, recovery_media = (
                fetch_user_posts(
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
            )

            recovery_success = True

            reply_count = sum(
                is_reply_post(
                    post
                )
                for post
                in recovery_posts
            )

            print(
                "Official timeline recovery fetched:",
                len(
                    recovery_posts
                ),
                "posts | replies/subtweets:",
                reply_count,
            )

        except Exception as exc:

            print(
                "REPLY RECOVERY FAILED:",
                type(
                    exc
                ).__name__,
                "|",
                exc,
            )

    # --------------------------------------------------------
    # MERGE
    # --------------------------------------------------------

    posts, media_map = (
        merge_post_sources(
            [
                (
                    timeline_posts,
                    timeline_media,
                ),
                (
                    recovery_posts,
                    recovery_media,
                ),
            ]
        )
    )

    # Final safety verification.
    posts = [
        post
        for post in posts
        if (
            not post.get(
                "author_id"
            )
            or str(
                post.get(
                    "author_id"
                )
            )
            == str(
                official_user_id
            )
        )
    ]

    print(
        "Verified official posts after merge:",
        len(
            posts
        ),
    )

    seen_keys = (
        existing_keys(
            existing
        )
    )

    parent_cache = {}

    candidate_count = 0
    reply_candidate_count = 0
    added_count = 0

    # --------------------------------------------------------
    # PROCESS OLDEST -> NEWEST
    # --------------------------------------------------------

    for post in sorted(
        posts,
        key=lambda item:
            int(
                item[
                    "id"
                ]
            ),
    ):

        # ----------------------------------------------------
        # ABSOLUTE AUTHOR SAFETY CHECK
        # ----------------------------------------------------

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

            print(
                "SKIPPING NON-OFFICIAL POST:",
                post.get(
                    "id"
                ),
                "| author:",
                author_id,
            )

            continue

        # ----------------------------------------------------
        # MEDIA
        # ----------------------------------------------------

        image_urls = []

        for media_key in (
            post
            .get(
                "attachments",
                {},
            )
            .get(
                "media_keys",
                [],
            )
        ):

            media = (
                media_map.get(
                    media_key,
                    {},
                )
            )

            if (
                media.get(
                    "type"
                )
                == "photo"
                and media.get(
                    "url"
                )
            ):

                image_urls.append(
                    media[
                        "url"
                    ]
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

        reply_hint = (
            is_reply_post(
                post
            )
        )

        if reply_hint:

            reply_candidate_count += 1

        # ----------------------------------------------------
        # OFFICIAL THREAD CONTEXT
        # ----------------------------------------------------

        parent_text = ""

        if reply_hint:

            parent_text = (
                official_parent_context(
                    post,
                    official_user_id,
                    parent_cache,
                )
            )

        combined_context = (
            text
            + "\n"
            + parent_text
        )

        picker_hint = (
            picker_hint_from_text(
                combined_context
            )
        )

        added_hint = (
            is_added_post(
                combined_context
            )
        )

        inferred_week = (
            infer_week_from_date(
                post.get(
                    "created_at"
                )
            )
        )

        post_url = (
            f"https://x.com/"
            f"{USERNAME}/status/"
            f"{post['id']}"
        )

        print(
            "Parsing official candidate:",
            post[
                "id"
            ],
            "| reply:",
            reply_hint,
            "| picker_hint:",
            picker_hint,
            "| week:",
            inferred_week,
            "| parent_context:",
            bool(
                parent_text
            ),
        )

        try:

            extracted = (
                parse_post_with_ai(
                    text=
                        text,

                    image_urls=
                        image_urls,

                    post_url=
                        post_url,

                    posted_at=
                        post.get(
                            "created_at"
                        ),

                    inferred_week=
                        inferred_week,

                    picker_hint=
                        picker_hint,

                    added_hint=
                        added_hint,

                    reply_hint=
                        reply_hint,

                    official_parent_text=
                        parent_text,
                )
            )

        except Exception as exc:

            print(
                "Parse failed:",
                post[
                    "id"
                ],
                "|",
                type(
                    exc
                ).__name__,
                "|",
                exc,
            )

            continue

        print(
            "AI extracted",
            len(
                extracted
            ),
            "pick(s) from",
            post[
                "id"
            ],
        )

        # ----------------------------------------------------
        # STORE EXTRACTED PICKS
        # ----------------------------------------------------

        for extracted_pick in extracted:

            extracted_pick = (
                infer_market_from_selection(
                    extracted_pick
                )
            )

            picker = (
                normalize_picker(
                    extracted_pick.get(
                        "picker"
                    )
                )
            )

            if not picker:

                picker = (
                    picker_hint
                )

            if not picker:

                print(
                    "SKIPPING UNKNOWN PICKER:",
                    post[
                        "id"
                    ],
                    "|",
                    extracted_pick.get(
                        "selection"
                    ),
                )

                continue

            selection = str(
                extracted_pick.get(
                    "selection"
                )
                or ""
            ).strip()

            if not selection:
                continue

            week = (
                extracted_pick.get(
                    "week"
                )
                or inferred_week
            )

            try:

                if week is not None:

                    week = int(
                        week
                    )

            except Exception:

                week = (
                    inferred_week
                )

            try:

                confidence = float(
                    extracted_pick.get(
                        "confidence"
                    )
                    or 0
                )

            except Exception:

                confidence = 0

            bet_type = (
                normalize_bet_type(
                    extracted_pick.get(
                        "bet_type"
                    )
                    or "OTHER"
                )
            )

            added_pick = bool(
                extracted_pick.get(
                    "added_pick"
                )
            )

            if added_hint:

                added_pick = True

            pick_for_key = dict(
                extracted_pick
            )

            pick_for_key[
                "picker"
            ] = picker

            pick_for_key[
                "week"
            ] = week

            pick_for_key[
                "selection"
            ] = selection

            pick_for_key[
                "bet_type"
            ] = bet_type

            canonical = (
                canonical_pick_key(
                    pick_for_key
                )
            )

            if canonical in seen_keys:

                print(
                    "Duplicate skipped:",
                    picker,
                    "| Week",
                    week,
                    "|",
                    selection,
                )

                continue

            pick_id = (
                stable_id(
                    canonical
                )
            )

            if confidence < 0.90:

                status = (
                    "REVIEW"
                )

            elif (
                bet_type
                in SUPPORTED_MARKETS
            ):

                status = (
                    "OPEN"
                )

            else:

                status = (
                    "REVIEW"
                )

            existing.append(
                {
                    "id":
                        pick_id,

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
                        selection,

                    "side":
                        extracted_pick.get(
                            "side"
                        ),

                    "line":
                        extracted_pick.get(
                            "line"
                        ),

                    "odds":
                        extracted_pick.get(
                            "odds"
                        ),

                    "units":
                        float(
                            extracted_pick.get(
                                "units"
                            )
                            or 1
                        ),

                    "mortal_lock":
                        bool(
                            extracted_pick.get(
                                "mortal_lock"
                            )
                        ),

                    "week":
                        week,

                    "added_pick":
                        added_pick,

                    "confidence":
                        confidence,

                    "status":
                        status,

                    "result":
                        None,

                    "profit_units":
                        0,

                    "source_post_id":
                        post[
                            "id"
                        ],

                    "source_url":
                        post_url,

                    "source_text":
                        text,

                    "source_is_reply":
                        reply_hint,

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
            )

            seen_keys.add(
                canonical
            )

            added_count += 1

            print(
                "ADDED:",
                picker,
                "| Week",
                week,
                "|",
                selection,
                "| reply:",
                reply_hint,
                "| market:",
                bet_type,
            )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    fetched_posts = (
        timeline_posts
        + recovery_posts
    )

    if fetched_posts:

        newest_id = max(
            (
                str(
                    post[
                        "id"
                    ]
                )
                for post
                in fetched_posts
            ),
            key=int,
        )

        previous_id = (
            state.get(
                "last_x_post_id"
            )
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

    # Only mark recovery finished after the official timeline
    # recovery request itself succeeded.
    if (
        should_reply_recovery
        and recovery_success
    ):

        state[
            "reply_recovery_complete"
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
        added_count,
    )

    print(
        "Total stored picks:",
        len(
            existing
        ),
    )

    print(
        "Reply recovery complete:",
        state.get(
            "reply_recovery_complete"
        ),
    )

    print(
        "===================================="
    )


if __name__ == "__main__":
    ingest()
