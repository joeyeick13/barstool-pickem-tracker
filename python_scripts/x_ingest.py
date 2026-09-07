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
USERNAME = os.getenv("X_USERNAME", "barstoolpickem")

BACKFILL_DAYS = 8
MAX_BACKFILL_PAGES = 3

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

def x_get(path, params=None):
    token = os.environ["X_BEARER_TOKEN"]

    response = requests.get(
        X_API + path,
        params=params or {},
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def resolve_user_id():
    payload = x_get(
        f"/users/by/username/{USERNAME}"
    )

    return payload["data"]["id"]


def iso_x_time(dt):
    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def fetch_posts(
    user_id,
    since_id=None,
    backfill=False,
):
    """
    Normal:
        Only fetch posts newer than last_x_post_id.

    Backfill:
        Fetch several pages covering the recent card window.
    """

    params = {
        "max_results": 100,
        "exclude": "retweets",
        "tweet.fields": (
            "created_at,attachments,text,"
            "referenced_tweets"
        ),
        "expansions": "attachments.media_keys",
        "media.fields": (
            "media_key,type,url,preview_image_url"
        ),
    }

    if backfill:
        params["start_time"] = iso_x_time(
            datetime.now(timezone.utc)
            - timedelta(days=BACKFILL_DAYS)
        )

    elif since_id:
        params["since_id"] = since_id

    all_posts = []
    all_media = {}

    pages = 0

    while True:
        payload = x_get(
            f"/users/{user_id}/tweets",
            params,
        )

        all_posts.extend(
            payload.get("data", [])
        )

        for media in (
            payload.get("includes", {})
            .get("media", [])
        ):
            media_key = media.get("media_key")

            if media_key:
                all_media[media_key] = media

        pages += 1

        next_token = (
            payload.get("meta", {})
            .get("next_token")
        )

        if not next_token:
            break

        if not backfill:
            break

        if pages >= MAX_BACKFILL_PAGES:
            break

        params["pagination_token"] = (
            next_token
        )

    return all_posts, all_media


def fetch_post_by_id(post_id):
    """
    Used when an older stored REVIEW pick needs its
    original source card re-read after parser improvements.
    """

    payload = x_get(
        f"/tweets/{post_id}",
        {
            "tweet.fields": (
                "created_at,attachments,text"
            ),
            "expansions": (
                "attachments.media_keys"
            ),
            "media.fields": (
                "media_key,type,url,"
                "preview_image_url"
            ),
        },
    )

    post = payload.get("data")

    media_map = {}

    for media in (
        payload.get("includes", {})
        .get("media", [])
    ):
        if media.get("media_key"):
            media_map[
                media["media_key"]
            ] = media

    return post, media_map


# ============================================================
# WEEK MAPPING
# ============================================================

def infer_week_from_date(created_at):
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

    The Sep 7 exception is important because ESPN's
    2026 Week 1 extends through Monday Sep 7.
    """

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

    d = dt.astimezone(
        timezone.utc
    ).date()

    if dt.year == 2026:

        if (
            date(2026, 8, 22)
            <= d
            <= date(2026, 9, 7)
        ):
            return 1

        if (
            date(2026, 9, 8)
            <= d
            <= date(2026, 9, 13)
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
                + (
                    d
                    - week_3_start
                ).days
                // 7
            )

        return None

    # Generic fallback for later seasons.
    september_first = datetime(
        dt.year,
        9,
        1,
        tzinfo=timezone.utc,
    )

    week_one_monday = (
        september_first
        - timedelta(
            days=september_first.weekday()
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
# TEXT / MARKET NORMALIZATION
# ============================================================

def normalize_text(value):
    value = str(
        value or ""
    ).lower()

    value = (
        value
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
        .replace("&amp;", "&")
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


def normalize_bet_type(value):
    raw = str(
        value or "OTHER"
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

        "FIRST_QUARTER_SPREAD": (
            "FIRST_QUARTER_SPREAD"
        ),
        "FIRST_QUARTER_TOTAL": (
            "FIRST_QUARTER_TOTAL"
        ),
        "FIRST_QUARTER_MONEYLINE": (
            "FIRST_QUARTER_MONEYLINE"
        ),
        "FIRST_QUARTER_TEAM_TOTAL": (
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

        "FIRST_HALF_SPREAD": (
            "FIRST_HALF_SPREAD"
        ),
        "FIRST_HALF_TOTAL": (
            "FIRST_HALF_TOTAL"
        ),
        "FIRST_HALF_MONEYLINE": (
            "FIRST_HALF_MONEYLINE"
        ),
        "FIRST_HALF_TEAM_TOTAL": (
            "FIRST_HALF_TEAM_TOTAL"
        ),

        "SPREAD": "SPREAD",
        "TOTAL": "TOTAL",
        "MONEYLINE": "MONEYLINE",
        "TEAM_TOTAL": "TEAM_TOTAL",
    }

    return aliases.get(
        raw,
        raw,
    )


def infer_market_from_selection(pick):
    """
    Deterministic cleanup after AI extraction.

    Supports:
        Oregon TT o37.5
        Oregon TT u37.5
        Indiana first half TT Over 27.5
        Oklahoma 1Q -9.5
        Miami 1H -13.5
        1Q Over 14.5
        1H Under 28.5
    """

    pick = dict(
        pick
    )

    selection = str(
        pick.get("selection")
        or ""
    ).strip()

    text = (
        selection.lower()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    current_type = (
        normalize_bet_type(
            pick.get("bet_type")
        )
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

    is_team_total = bool(
        re.search(
            r"\b(?:tt|team\s*total)\b",
            text,
            flags=re.I,
        )
    )

    # --------------------------------------------------------
    # TEAM TOTAL
    # --------------------------------------------------------

    if is_team_total:

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

        # TT o37.5 / TT u37.5
        compact = re.search(
            r"\b(?:tt|team\s*total)\s*"
            r"(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\b",
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

            return pick

        # TT Over 37.5 / TT Under 37.5
        verbose = re.search(
            r"\b(?:tt|team\s*total)\s*"
            r"(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\b",
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

        return pick

    # --------------------------------------------------------
    # PERIOD MARKETS
    # --------------------------------------------------------

    if first_quarter or first_half:

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
            pick["bet_type"] = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:ml|moneyline)\b",
            text,
            flags=re.I,
        ):
            pick["bet_type"] = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*"
            r"[0-9]+(?:\.[0-9]+)?",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_SPREAD"
            )

        else:
            pick["bet_type"] = (
                current_type
            )

        return pick

    pick["bet_type"] = (
        current_type
    )

    return pick


# ============================================================
# CANDIDATE POST FILTERING
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    """
    Images are always inspected because weekly cards
    are image posts.
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


def picker_hint_from_text(text):
    t = str(
        text or ""
    ).lower()

    if (
        "barstoolbigcat" in t
        or "big cat" in t
        or "bigcat" in t
    ):
        return "Big Cat"

    if (
        "stoolpresidente" in t
        or "stool presidente" in t
        or "dave portnoy" in t
        or "portnoy" in t
        or "el pres" in t
    ):
        return "Stool Presidente"

    if (
        "ricobosco" in t
        or "rico bosco" in t
        or "rico" in t
    ):
        return "Rico Bosco"

    return None


def is_added_post(text):
    t = str(
        text or ""
    ).lower()

    added_terms = [
        "adds for",
        "add for",
        "added pick",
        "adding",
        "addition",
        "another one",
    ]

    return any(
        term in t
        for term in added_terms
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
):
    from openai import OpenAI

    client = OpenAI()

    prompt = f"""
You are extracting gambling picks from the official
Barstool Pick Em X account.

Only track picks belonging to:
- Big Cat
- Stool Presidente / Dave Portnoy / El Presidente / Pres
- Rico Bosco

SOURCE POST:
{post_url}

POSTED AT:
{posted_at}

POST TEXT:
{text}

CONTEXT HINTS:
Likely picker from post text: {picker_hint}
Likely college-football week from post date: {inferred_week}
This appears to be a later/additional-picks post: {added_hint}

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

IMPORTANT RULES:

1. Extract only explicit wagers.
   Never invent a wager.

2. A weekly card may contain MANY wagers.
   Extract every wager visible in every image.

3. Multiple images may belong to different Pick Em
   personalities. Read each card heading.

4. Common picker labels:
   - "Big Cat's Picks" = Big Cat
   - Dave / Pres / El Pres / Portnoy = Stool Presidente
   - Rico = Rico Bosco

5. Ignore:
   - record lines
   - kickoff times
   - promotional text
   - DraftKings branding
   - commentary
   - jokes
   - historical records
   - final scores
   - recaps that do not announce a new wager

6. Later posts frequently say:
   "Adds for @BarstoolBigCat"
   followed by one or more wagers.

   Every wager from such a post must have:
   added_pick = true

7. If a later post contains a single wager, still
   extract it when the picker is identifiable.

8. Use likely week {inferred_week} when the source
   does not explicitly state a week.

9. SPREAD EXAMPLE:

   "Cal +2.5"

   bet_type = SPREAD
   team = "Cal"
   side = "Cal"
   line = 2.5
   selection = "Cal +2.5"

10. GAME TOTAL EXAMPLE:

    "FIU/USF Over 53.5"

    bet_type = TOTAL
    matchup = "FIU/USF"
    side = "OVER"
    line = 53.5

11. "Texas A&M Over 53.5" may identify a game
    total using one team.

    Do NOT call it a TEAM_TOTAL unless the source
    explicitly says TT or team total.

12. TEAM TOTALS:

    "Oregon TT o37.5"

    bet_type = TEAM_TOTAL
    team = "Oregon"
    side = "OVER"
    line = 37.5
    selection = "Oregon TT o37.5"

    "Oregon TT u37.5"

    bet_type = TEAM_TOTAL
    team = "Oregon"
    side = "UNDER"
    line = 37.5

    IMPORTANT:
    o = OVER
    u = UNDER

    o37.5 means Over 37.5.
    u37.5 means Under 37.5.

13. FIRST QUARTER:

    "Oklahoma 1Q -9.5"

    bet_type = FIRST_QUARTER_SPREAD
    team = "Oklahoma"
    side = "Oklahoma"
    line = -9.5

    "Oklahoma/Temple 1Q Over 14.5"

    bet_type = FIRST_QUARTER_TOTAL
    matchup = "Oklahoma/Temple"
    side = "OVER"
    line = 14.5

    "Oklahoma 1Q TT o10.5"

    bet_type = FIRST_QUARTER_TEAM_TOTAL
    team = "Oklahoma"
    side = "OVER"
    line = 10.5

14. FIRST HALF:

    "Miami 1H -13.5"

    bet_type = FIRST_HALF_SPREAD
    team = "Miami"
    side = "Miami"
    line = -13.5

    "Indiana first half TT Over 27.5"

    bet_type = FIRST_HALF_TEAM_TOTAL
    team = "Indiana"
    side = "OVER"
    line = 27.5

    "USC 1H Over 31.5"

    bet_type = FIRST_HALF_TOTAL
    team = "USC"
    side = "OVER"
    line = 31.5

15. NEVER convert a first-quarter or first-half
    wager into a full-game wager.

16. Mortal Lock:
    mortal_lock = true ONLY when explicitly identified
    as the Mortal Lock.

17. Preserve lines exactly.
    Convert unicode minus signs into numeric negatives.

18. Defaults:
    units = 1
    odds = null unless explicitly shown.

19. Confidence:
    0.95-1.00 = clearly readable and attributed
    below 0.90 = uncertainty exists

20. If there are no actual new wagers:
    return {{"picks":[]}}

JSON only.
No markdown.
"""

    content = [
        {
            "type": "input_text",
            "text": prompt,
        }
    ]

    for image_url in image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": image_url,
                "detail": "high",
            }
        )

    response = client.responses.create(
        model=os.getenv(
            "OPENAI_MODEL",
            "gpt-5.6-luna",
        ),
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )

    raw = (
        response.output_text
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

    picks = []

    for pick in parsed.get(
        "picks",
        [],
    ):
        picks.append(
            infer_market_from_selection(
                pick
            )
        )

    return picks


# ============================================================
# DEDUPE
# ============================================================

def canonical_pick_key(pick):
    """
    Cross-post duplicate protection.

    X post ID intentionally excluded.
    """

    picker = (
        normalize_picker(
            pick.get("picker")
        )
        or ""
    )

    week = (
        pick.get("week")
        or ""
    )

    bet_type = normalize_text(
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    matchup = normalize_text(
        pick.get("matchup")
    )

    team = normalize_text(
        pick.get("team")
    )

    side = normalize_text(
        pick.get("side")
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
        pick.get("selection")
    )

    structured = "|".join(
        [
            picker,
            str(week),
            bet_type,
            matchup,
            team,
            side,
            str(line),
        ]
    )

    if not matchup and not team:
        structured += (
            "|"
            + selection
        )

    return structured


def stable_id(canonical_key):
    return (
        hashlib.sha1(
            canonical_key.encode()
        )
        .hexdigest()[:16]
    )


def existing_keys(existing):
    keys = set()

    for pick in existing:
        copy = dict(
            pick
        )

        if not copy.get("week"):
            copy["week"] = (
                infer_week_from_date(
                    copy.get("posted_at")
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
# EXISTING PICK REPAIR
# ============================================================

def repair_existing_markets(existing):
    """
    Reclassify previously stored 1Q / 1H / TT picks using
    their stored selection string.

    This only changes market metadata.
    It never assigns a result.
    """

    repaired = 0

    for pick in existing:

        before = (
            pick.get("bet_type"),
            pick.get("side"),
            pick.get("line"),
        )

        normalized = (
            infer_market_from_selection(
                pick
            )
        )

        pick["bet_type"] = (
            normalize_bet_type(
                normalized.get(
                    "bet_type"
                )
            )
        )

        if normalized.get(
            "side"
        ) is not None:
            pick["side"] = (
                normalized["side"]
            )

        if normalized.get(
            "line"
        ) is not None:
            pick["line"] = (
                normalized["line"]
            )

        after = (
            pick.get("bet_type"),
            pick.get("side"),
            pick.get("line"),
        )

        if before != after:
            repaired += 1

            print(
                "MARKET REPAIRED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "|",
                before,
                "->",
                after,
            )

    return repaired


def needs_source_reparse(pick):
    """
    Re-read the source image when an older team-total
    extraction lost its Over/Under direction.

    Example:
        Stored: Oregon TT 37.5
        Source: Oregon TT o37.5
    """

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    if bet_type not in {
        "TEAM_TOTAL",
        "FIRST_QUARTER_TEAM_TOTAL",
        "FIRST_HALF_TEAM_TOTAL",
    }:
        return False

    side = str(
        pick.get("side")
        or ""
    ).upper()

    if side in {
        "OVER",
        "UNDER",
    }:
        return False

    return bool(
        pick.get("source_post_id")
    )


def candidate_matches_existing(
    existing_pick,
    candidate,
):
    """
    Conservative matcher for source-card repair.

    Requires same picker and same line.
    If both records contain a team, the team must also match.
    """

    old_picker = normalize_picker(
        existing_pick.get("picker")
    )

    new_picker = normalize_picker(
        candidate.get("picker")
    )

    if (
        old_picker
        and new_picker
        and old_picker != new_picker
    ):
        return False

    try:
        old_line = float(
            existing_pick.get("line")
        )

        new_line = float(
            candidate.get("line")
        )

    except Exception:
        return False

    if abs(
        old_line
        - new_line
    ) > 0.001:
        return False

    old_team = normalize_text(
        existing_pick.get("team")
    )

    new_team = normalize_text(
        candidate.get("team")
    )

    if (
        old_team
        and new_team
        and old_team != new_team
    ):
        return False

    return True


def repair_missing_direction_from_source(
    existing,
):
    """
    Generic source-card repair.

    If an older stored team total is missing its Over/Under
    direction, re-read that pick's original X source.

    No team, line, picker, or result is hardcoded.
    """

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
            pick.get("source_post_id")
        )

        grouped.setdefault(
            post_id,
            [],
        ).append(
            pick
        )

    repaired = 0

    for post_id, picks in grouped.items():

        try:
            post, mmap = fetch_post_by_id(
                post_id
            )

            if not post:
                continue

            media_keys = (
                post.get(
                    "attachments",
                    {},
                )
                .get(
                    "media_keys",
                    [],
                )
            )

            image_urls = []

            for media_key in media_keys:
                media = mmap.get(
                    media_key,
                    {},
                )

                if (
                    media.get("type")
                    == "photo"
                    and media.get("url")
                ):
                    image_urls.append(
                        media["url"]
                    )

            picker_hint = (
                picker_hint_from_text(
                    post.get(
                        "text",
                        "",
                    )
                )
            )

            inferred_week = (
                infer_week_from_date(
                    post.get("created_at")
                )
            )

            extracted = (
                parse_post_with_ai(
                    text=post.get(
                        "text",
                        "",
                    ),
                    image_urls=image_urls,
                    post_url=(
                        f"https://x.com/"
                        f"{USERNAME}/status/"
                        f"{post_id}"
                    ),
                    posted_at=post.get(
                        "created_at"
                    ),
                    inferred_week=inferred_week,
                    picker_hint=picker_hint,
                    added_hint=is_added_post(
                        post.get(
                            "text",
                            "",
                        )
                    ),
                )
            )

            for old_pick in picks:

                matches = [
                    candidate
                    for candidate in extracted
                    if candidate_matches_existing(
                        old_pick,
                        candidate,
                    )
                ]

                if len(matches) != 1:
                    print(
                        "SOURCE REPAIR SKIPPED:",
                        old_pick.get(
                            "selection"
                        ),
                        "| candidates:",
                        len(matches),
                    )

                    continue

                candidate = (
                    infer_market_from_selection(
                        matches[0]
                    )
                )

                direction = str(
                    candidate.get("side")
                    or ""
                ).upper()

                if direction not in {
                    "OVER",
                    "UNDER",
                }:
                    continue

                old_pick["side"] = (
                    direction
                )

                old_pick["bet_type"] = (
                    normalize_bet_type(
                        candidate.get(
                            "bet_type"
                        )
                        or old_pick.get(
                            "bet_type"
                        )
                    )
                )

                if candidate.get(
                    "line"
                ) is not None:
                    old_pick["line"] = (
                        candidate["line"]
                    )

                if candidate.get(
                    "selection"
                ):
                    old_pick["selection"] = (
                        candidate[
                            "selection"
                        ]
                    )

                repaired += 1

                print(
                    "SOURCE MARKET REPAIRED:",
                    old_pick.get(
                        "picker"
                    ),
                    "|",
                    old_pick.get(
                        "selection"
                    ),
                    "| side:",
                    old_pick.get(
                        "side"
                    ),
                )

        except Exception as exc:
            print(
                "SOURCE REPAIR FAILED:",
                post_id,
                "|",
                type(exc).__name__,
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
            "last_x_post_id": None,
            "backfill_complete": False,
        },
    )

    existing = load_json(
        PICKS_FILE,
        [],
    )

    # --------------------------------------------------------
    # REPAIR OLD MARKET METADATA
    # --------------------------------------------------------

    repaired = repair_existing_markets(
        existing
    )

    if repaired:
        print(
            "Existing market metadata repaired:",
            repaired,
        )

    # Re-read old source cards only when a team total is
    # missing its O/U direction.
    source_repaired = (
        repair_missing_direction_from_source(
            existing
        )
    )

    if source_repaired:
        print(
            "Existing source cards reparsed:",
            source_repaired,
        )

    # --------------------------------------------------------
    # DETERMINE BACKFILL VS INCREMENTAL
    # --------------------------------------------------------

    should_backfill = (
        not existing
        or not state.get(
            "backfill_complete"
        )
    )

    uid = resolve_user_id()

    posts, mmap = fetch_posts(
        uid,
        since_id=(
            None
            if should_backfill
            else state.get(
                "last_x_post_id"
            )
        ),
        backfill=should_backfill,
    )

    print(
        f"Fetched {len(posts)} X posts "
        f"({'backfill' if should_backfill else 'incremental'} mode)"
    )

    seen_keys = existing_keys(
        existing
    )

    added_count = 0
    candidate_count = 0

    # --------------------------------------------------------
    # PROCESS POSTS OLDEST -> NEWEST
    # --------------------------------------------------------

    for post in sorted(
        posts,
        key=lambda item: int(
            item["id"]
        ),
    ):

        media_keys = (
            post.get(
                "attachments",
                {},
            )
            .get(
                "media_keys",
                [],
            )
        )

        image_urls = []

        for media_key in media_keys:

            media = mmap.get(
                media_key,
                {},
            )

            if (
                media.get("type")
                == "photo"
                and media.get("url")
            ):
                image_urls.append(
                    media["url"]
                )

        text = post.get(
            "text",
            "",
        )

        if not looks_like_pick_post(
            text,
            image_urls,
        ):
            continue

        candidate_count += 1

        post_url = (
            f"https://x.com/"
            f"{USERNAME}/status/"
            f"{post['id']}"
        )

        inferred_week = (
            infer_week_from_date(
                post.get(
                    "created_at"
                )
            )
        )

        picker_hint = (
            picker_hint_from_text(
                text
            )
        )

        added_hint = (
            is_added_post(
                text
            )
        )

        print(
            f"Parsing candidate post "
            f"{post['id']} "
            f"images={len(image_urls)} "
            f"picker_hint={picker_hint} "
            f"week={inferred_week}"
        )

        try:
            extracted = (
                parse_post_with_ai(
                    text=text,
                    image_urls=image_urls,
                    post_url=post_url,
                    posted_at=post.get(
                        "created_at"
                    ),
                    inferred_week=inferred_week,
                    picker_hint=picker_hint,
                    added_hint=added_hint,
                )
            )

        except Exception as exc:
            print(
                f"Parse failed for post "
                f"{post['id']}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            continue

        print(
            f"AI extracted "
            f"{len(extracted)} picks "
            f"from post {post['id']}"
        )

        # ----------------------------------------------------
        # PROCESS EXTRACTED PICKS
        # ----------------------------------------------------

        for extracted_pick in extracted:

            extracted_pick = (
                infer_market_from_selection(
                    extracted_pick
                )
            )

            picker = normalize_picker(
                extracted_pick.get(
                    "picker"
                )
            )

            if not picker:
                picker = picker_hint

            if not picker:
                print(
                    "Skipping pick with "
                    "unknown picker from post",
                    post["id"],
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

            week = extracted_pick.get(
                "week"
            )

            if not week:
                week = inferred_week

            try:
                if week is not None:
                    week = int(
                        week
                    )

            except Exception:
                week = inferred_week

            try:
                confidence = float(
                    extracted_pick.get(
                        "confidence"
                    )
                    or 0
                )

            except Exception:
                confidence = 0

            added_pick = bool(
                extracted_pick.get(
                    "added_pick"
                )
            )

            if added_hint:
                added_pick = True

            bet_type = normalize_bet_type(
                extracted_pick.get(
                    "bet_type"
                )
                or "OTHER"
            )

            extracted_pick[
                "bet_type"
            ] = bet_type

            # ------------------------------------------------
            # DEDUPE
            # ------------------------------------------------

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

            pid = stable_id(
                canonical
            )

            # ------------------------------------------------
            # INITIAL STATUS
            # ------------------------------------------------

            if confidence < 0.90:
                status = "REVIEW"

            elif (
                bet_type
                in SUPPORTED_MARKETS
            ):
                status = "OPEN"

            else:
                status = "REVIEW"

            # ------------------------------------------------
            # STORE
            # ------------------------------------------------

            existing.append(
                {
                    "id": pid,
                    "picker": picker,
                    "sport": (
                        extracted_pick.get(
                            "sport"
                        )
                        or "CFB"
                    ),
                    "matchup": (
                        extracted_pick.get(
                            "matchup"
                        )
                    ),
                    "team": (
                        extracted_pick.get(
                            "team"
                        )
                    ),
                    "opponent": (
                        extracted_pick.get(
                            "opponent"
                        )
                    ),
                    "bet_type": bet_type,
                    "selection": selection,
                    "side": (
                        extracted_pick.get(
                            "side"
                        )
                    ),
                    "line": (
                        extracted_pick.get(
                            "line"
                        )
                    ),
                    "odds": (
                        extracted_pick.get(
                            "odds"
                        )
                    ),
                    "units": float(
                        extracted_pick.get(
                            "units"
                        )
                        or 1
                    ),
                    "mortal_lock": bool(
                        extracted_pick.get(
                            "mortal_lock"
                        )
                    ),
                    "week": week,
                    "added_pick": (
                        added_pick
                    ),
                    "confidence": (
                        confidence
                    ),
                    "status": status,
                    "result": None,
                    "profit_units": 0,
                    "source_post_id": (
                        post["id"]
                    ),
                    "source_url": (
                        post_url
                    ),
                    "source_text": (
                        text
                    ),
                    "posted_at": (
                        post.get(
                            "created_at"
                        )
                    ),
                    "graded_at": None,
                    "final_score": None,
                    "event_id": None,
                }
            )

            seen_keys.add(
                canonical
            )

            added_count += 1

            print(
                "Added:",
                picker,
                "| Week",
                week,
                "|",
                selection,
                "|",
                (
                    "ADDED PICK"
                    if added_pick
                    else "MAIN CARD"
                ),
                "| market:",
                bet_type,
            )

    # --------------------------------------------------------
    # UPDATE STATE
    # --------------------------------------------------------

    if posts:
        state[
            "last_x_post_id"
        ] = max(
            (
                post["id"]
                for post in posts
            ),
            key=int,
        )

    if should_backfill:
        state[
            "backfill_complete"
        ] = True

    state["updated_at"] = (
        now_iso()
    )

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

    print(
        "Candidate posts inspected:",
        candidate_count,
    )

    print(
        "New picks added:",
        added_count,
    )

    print(
        "Total stored picks:",
        len(existing),
    )


if __name__ == "__main__":
    ingest()
