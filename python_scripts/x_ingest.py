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

# Historical recovery is ONLY for replies/subtweets that older
# versions of the tracker may have missed.
REPLY_RECOVERY_DAYS = 10
MAX_REPLY_RECOVERY_PAGES = 4

# Parent tweets are used only for context / picker attribution.
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

    return str(
        payload["data"]["id"]
    )


def iso_x_time(dt):
    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
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
    Fetch posts authored by @barstoolpickem.

    Replies are intentionally INCLUDED.
    Retweets are excluded.

    author_id is requested and verified again locally as an
    additional safety layer.
    """

    params = {
        "max_results": 100,

        # IMPORTANT:
        # Do not add "replies" here.
        # We WANT official replies/subtweets.
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

        "expansions": (
            "attachments.media_keys"
        ),

        "media.fields": (
            "media_key,"
            "type,"
            "url,"
            "preview_image_url"
        ),
    }

    if since_id:
        params["since_id"] = str(
            since_id
        )

    if start_time:
        params["start_time"] = iso_x_time(
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
            author_id = str(
                post.get("author_id")
                or ""
            )

            # Belt-and-suspenders safety:
            # never process another user's post.
            if (
                author_id
                and author_id != str(user_id)
            ):
                print(
                    "REJECTED NON-OFFICIAL AUTHOR:",
                    post.get("id"),
                    "| author:",
                    author_id,
                )
                continue

            all_posts.append(
                post
            )

        for media in (
            payload
            .get("includes", {})
            .get("media", [])
        ):
            media_key = media.get(
                "media_key"
            )

            if media_key:
                media_map[
                    media_key
                ] = media

        pages += 1

        next_token = (
            payload
            .get("meta", {})
            .get("next_token")
        )

        if not next_token:
            break

        if pages >= max_pages:
            break

        params["pagination_token"] = (
            next_token
        )

    return (
        all_posts,
        media_map,
    )


# ============================================================
# SINGLE POST FETCH
# ============================================================

def fetch_post_by_id(post_id):
    """
    Fetch one X post.

    Used for:
    - verified official parent-thread context
    - repairing old team-total direction when OCR previously
      lost an o/u marker
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

            "expansions": (
                "attachments.media_keys"
            ),

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
        .get("includes", {})
        .get("media", [])
    ):
        media_key = media.get(
            "media_key"
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
# REPLY DETECTION
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


# ============================================================
# VERIFIED OFFICIAL PARENT CONTEXT
# ============================================================

def official_parent_context(
    post,
    official_user_id,
    cache,
):
    """
    Retrieve parent/ancestor text ONLY when those ancestors
    were also authored by @barstoolpickem.

    Example:

        Official parent:
            Adds for @BarstoolBigCat

        Official reply:
            Tulane/Duke Over 50.5
            Oklahoma State Over 57.5

    Parent text may identify the picker.

    IMPORTANT:
    Parent wagers are never themselves extracted from this
    context. Only the actual child/source post can create new
    picks.

    If the parent belongs to a fan or any other account,
    context traversal stops immediately.
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
                parent, _ = fetch_post_by_id(
                    current_id
                )

            except Exception as exc:
                print(
                    "PARENT FETCH FAILED:",
                    current_id,
                    "|",
                    type(exc).__name__,
                    "|",
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

        # Do not use any fan / third-party parent content.
        if (
            author_id
            != str(official_user_id)
        ):
            print(
                "IGNORING NON-OFFICIAL PARENT:",
                current_id,
                "| author:",
                author_id,
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

        current_id = replied_to_post_id(
            parent
        )

    return "\n\n".join(
        pieces
    )


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

    d = (
        dt
        .astimezone(timezone.utc)
        .date()
    )

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

    # Generic fallback for future seasons.
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
# TEXT NORMALIZATION
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


def infer_market_from_selection(pick):
    """
    Deterministic cleanup after AI extraction.

    Examples supported:

        Oregon TT o37.5
        Oregon TT u37.5
        Alabama TT over 40.5

        Oklahoma 1Q -9.5
        Miami 1H -13.5

        Indiana first half TT Over 27.5
    """

    pick = dict(
        pick
    )

    selection = str(
        pick.get("selection")
        or ""
    ).strip()

    text = (
        selection
        .lower()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    current_type = normalize_bet_type(
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

    is_team_total = bool(
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

    if is_team_total:
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

        # Compact:
        # Oregon TT o37.5
        # Oregon TT u37.5
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
                if compact.group(1).lower()
                == "o"
                else "UNDER"
            )

            pick[
                "line"
            ] = float(
                compact.group(2)
            )

            return pick

        # Verbose:
        # Alabama TT over 40.5
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
                verbose.group(1)
                .upper()
            )

            pick[
                "line"
            ] = float(
                verbose.group(2)
            )

        return pick

    # --------------------------------------------------------
    # FIRST QUARTER / FIRST HALF
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
# CANDIDATE POST FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    """
    Images are always inspected because weekly cards are image
    posts.

    Text-only official replies/subtweets must still look like
    actual wagers.
    """

    if image_urls:
        return True

    t = str(
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
        keyword in t
        for keyword in keywords
    ):
        return True

    # Spread-like:
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
# PICKER / ADDED-PICK CONTEXT
# ============================================================

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
        or re.search(
            r"\brico\b",
            t,
        )
    ):
        return "Rico Bosco"

    return None


def is_added_post(text):
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

SOURCE SAFETY:

The ACTUAL SOURCE POST below has already been verified by the
program to have been authored by the official @barstoolpickem
account.

Any parent/thread text below has also been verified as authored
by the same official Barstool Pick Em account.

IMPORTANT:

Only wagers explicitly present in the ACTUAL SOURCE POST may
create picks.

Parent/thread context may be used to identify the picker or
determine that a source reply is an added-pick continuation.

NEVER extract a wager merely because it appears in parent
context.

SOURCE URL:
{post_url}

POSTED AT:
{posted_at}

ACTUAL SOURCE POST TEXT:
{text}

VERIFIED OFFICIAL PARENT/THREAD CONTEXT:
{official_parent_text or "None"}

CONTEXT HINTS:

Likely picker:
{picker_hint}

Likely CFB week:
{inferred_week}

Likely addition:
{added_hint}

Is reply/subtweet:
{reply_hint}


Return JSON only using exactly this structure:

{{
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "matchup text or null",
      "team": "team being wagered on or team identifying game, or null",
      "opponent": "opponent if visible, or null",
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
      "selection": "concise exact wager",
      "side": "OVER|UNDER|team name|null",
      "line": 0.0,
      "odds": null,
      "units": 1,
      "mortal_lock": false,
      "week": {inferred_week},
      "added_pick": false,
      "confidence": 0.99
    }}
  ]
}}


RULES:

1. Extract only explicit wagers.

2. Weekly image cards may contain many wagers.
   Extract every explicit wager visible.

3. Common picker labels:
   - Big Cat = Big Cat
   - Dave / Pres / El Pres / Portnoy = Stool Presidente
   - Rico = Rico Bosco

4. Ignore:
   - historical records
   - kickoff times
   - promotional text
   - DraftKings branding
   - commentary
   - jokes
   - final scores
   - recaps that do not announce a new pick

5. OFFICIAL REPLIES / SUBTWEETS:

   The account sometimes continues an additions post using
   replies underneath the original tweet.

   Example:

   Official parent:
   "Adds for @BarstoolBigCat"

   Actual official reply:
   "Tulane/Duke Over 50.5
    Oklahoma State Over 57.5"

   Extract exactly two Big Cat wagers from the ACTUAL REPLY:

   Tulane/Duke Over 50.5
   Oklahoma State Over 57.5

   Parent context tells you they belong to Big Cat.

6. If a parent contains previous wagers, do NOT repeat them.
   Only wagers appearing in the actual source post count.

7. If actual post or verified parent says:
   "Adds for @BarstoolBigCat"

   then new wagers in the source post should normally have:
   added_pick = true

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

10. ONE-TEAM GAME TOTAL:

    "Texas A&M Over 53.5"

    This is normally a GAME TOTAL.

    Do NOT classify as TEAM_TOTAL unless the source explicitly
    uses TT or says team total.

11. TEAM TOTAL:

    "Alabama TT over 40.5"

    bet_type = TEAM_TOTAL
    team = "Alabama"
    side = "OVER"
    line = 40.5

12. COMPACT TEAM TOTAL:

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

15. FIRST HALF TEAM TOTAL:

    "Indiana first half TT over 27.5"

    bet_type = FIRST_HALF_TEAM_TOTAL
    team = "Indiana"
    side = "OVER"
    line = 27.5

16. NEVER turn a 1Q or 1H wager into a full-game wager.

17. Mortal Lock:
    mortal_lock = true only when explicitly stated.

18. units = 1 unless explicitly stated otherwise.

19. odds = null unless explicitly displayed.

20. Confidence:
    0.95-1.00 = clearly readable and attributable
    below 0.90 = meaningful uncertainty

21. If the ACTUAL SOURCE POST contains no new wager:
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
# CANONICAL DEDUPE
# ============================================================

def canonical_pick_key(pick):
    """
    X post ID is deliberately excluded.

    This prevents a repeated wager from counting twice merely
    because @barstoolpickem repeats it in another post.
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
            pick.get(
                "bet_type"
            )
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

    if (
        not matchup
        and not team
    ):
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
                    copy.get(
                        "posted_at"
                    )
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

def repair_existing_markets(existing):
    """
    Upgrade old stored market labels from selection text.

    Does NOT assign or alter results.
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

        if (
            normalized.get("side")
            is not None
        ):
            pick["side"] = (
                normalized["side"]
            )

        if (
            normalized.get("line")
            is not None
        ):
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


# ============================================================
# MISSING TEAM-TOTAL DIRECTION REPAIR
# ============================================================

def needs_source_reparse(pick):
    """
    Re-read an old source only when a stored team-total pick
    is missing OVER/UNDER direction.

    Example:

        Old stored:
            Oregon TT 37.5

        Source:
            Oregon TT o37.5
    """

    bet_type = normalize_bet_type(
        pick.get(
            "bet_type"
        )
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
        pick.get(
            "source_post_id"
        )
    )


def candidate_matches_existing(
    existing_pick,
    candidate,
):
    """
    Conservative source-repair matcher.

    Requires:
    - same picker when both exist
    - exact same line
    - same team when both exist
    """

    old_picker = normalize_picker(
        existing_pick.get(
            "picker"
        )
    )

    new_picker = normalize_picker(
        candidate.get(
            "picker"
        )
    )

    if (
        old_picker
        and new_picker
        and old_picker != new_picker
    ):
        return False

    try:
        old_line = float(
            existing_pick.get(
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
        existing_pick.get(
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
        and old_team != new_team
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

            # Absolute source-author verification.
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
                    media.get("type")
                    == "photo"
                    and media.get("url")
                ):
                    image_urls.append(
                        media["url"]
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
                    post.get("text")
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
                    added_hint=added_hint,
                    reply_hint=is_reply_post(
                        post
                    ),
                    official_parent_text=parent_text,
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

                if (
                    candidate.get("line")
                    is not None
                ):
                    old_pick["line"] = (
                        candidate["line"]
                    )

                if candidate.get(
                    "selection"
                ):
                    old_pick[
                        "selection"
                    ] = candidate[
                        "selection"
                    ]

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
            "last_x_post_id":
                None,

            "backfill_complete":
                False,

            "reply_only_recovery_v2_complete":
                False,
        },
    )

    # New flag intentionally differs from previous broken
    # recovery implementations.
    #
    # This guarantees ONE clean reply-only recovery after this
    # version is installed.
    if (
        "reply_only_recovery_v2_complete"
        not in state
    ):
        state[
            "reply_only_recovery_v2_complete"
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
    # REPAIR OLD MARKET METADATA
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

    # --------------------------------------------------------
    # REPAIR OLD MISSING TEAM-TOTAL DIRECTION
    # --------------------------------------------------------

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
    # NORMAL INCREMENTAL / INITIAL BACKFILL
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
            len(timeline_posts),
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
            len(timeline_posts),
        )

    # Track IDs fetched by the normal incremental timeline.
    timeline_ids = {
        str(
            post["id"]
        )
        for post in timeline_posts
        if post.get("id")
    }

    # --------------------------------------------------------
    # ONE-TIME HISTORICAL REPLY-ONLY RECOVERY
    # --------------------------------------------------------

    recovery_posts = []
    recovery_media = {}

    recovery_success = False

    should_reply_recovery = (
        not state.get(
            "reply_only_recovery_v2_complete"
        )
    )

    if should_reply_recovery:
        try:
            raw_recovery_posts, recovery_media = (
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

            print(
                "Recovery timeline fetched:",
                len(
                    raw_recovery_posts
                ),
                "official posts",
            )

            # =================================================
            # CRITICAL SAFETY RULE
            #
            # Historical recovery is allowed to contribute ONLY
            # official replies/subtweets.
            #
            # Ordinary historical top-level tweets are discarded
            # here and will never be passed through OpenAI again.
            #
            # This is the fix for the duplication/corruption
            # problem we found during QA.
            # =================================================

            recovery_posts = [
                post
                for post
                in raw_recovery_posts
                if is_reply_post(
                    post
                )
            ]

            recovery_success = True

            print(
                "Historical replies eligible:",
                len(
                    recovery_posts
                ),
            )

        except Exception as exc:
            print(
                "REPLY-ONLY RECOVERY FAILED:",
                type(exc).__name__,
                "|",
                exc,
            )

    # --------------------------------------------------------
    # MERGE NORMAL NEW POSTS + HISTORICAL REPLIES
    # --------------------------------------------------------

    posts_by_id = {}

    media_map = {}

    media_map.update(
        timeline_media
    )

    media_map.update(
        recovery_media
    )

    # Every genuinely new normal official post may be parsed.
    for post in timeline_posts:
        post_id = str(
            post.get("id")
            or ""
        )

        if post_id:
            posts_by_id[
                post_id
            ] = post

    # Historical recovery contributes replies ONLY.
    for post in recovery_posts:
        post_id = str(
            post.get("id")
            or ""
        )

        if post_id:
            posts_by_id[
                post_id
            ] = post

    posts = list(
        posts_by_id.values()
    )

    print(
        "Unique official posts eligible for parsing:",
        len(posts),
    )

    # --------------------------------------------------------
    # FINAL AUTHOR VERIFICATION
    # --------------------------------------------------------

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
            print(
                "SKIPPING NON-OFFICIAL POST:",
                post.get("id"),
                "| author:",
                author_id,
            )
            continue

        verified_posts.append(
            post
        )

    posts = verified_posts

    # --------------------------------------------------------
    # EXISTING PICK DEDUPE KEYS
    # --------------------------------------------------------

    seen_keys = existing_keys(
        existing
    )

    parent_cache = {}

    candidate_count = 0
    reply_candidate_count = 0
    new_pick_count = 0

    # --------------------------------------------------------
    # PROCESS OLDEST -> NEWEST
    # --------------------------------------------------------

    for post in sorted(
        posts,
        key=lambda p:
            int(
                p["id"]
            ),
    ):
        post_id = str(
            post["id"]
        )

        # ----------------------------------------------------
        # ABSOLUTE AUTHOR SAFETY
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
            media = media_map.get(
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

        text = str(
            post.get("text")
            or ""
        )

        # ----------------------------------------------------
        # CANDIDATE FILTER
        # ----------------------------------------------------

        if not looks_like_pick_post(
            text,
            image_urls,
        ):
            continue

        candidate_count += 1

        reply_hint = is_reply_post(
            post
        )

        if reply_hint:
            reply_candidate_count += 1

        # ----------------------------------------------------
        # VERIFIED OFFICIAL PARENT CONTEXT
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
            f"{post_id}"
        )

        historical_reply_recovery = (
            reply_hint
            and post_id
            not in timeline_ids
        )

        print(
            "Parsing official candidate:",
            post_id,
            "| reply:",
            reply_hint,
            "| historical reply recovery:",
            historical_reply_recovery,
            "| picker_hint:",
            picker_hint,
            "| week:",
            inferred_week,
            "| parent_context:",
            bool(parent_text),
        )

        # ----------------------------------------------------
        # AI PARSE
        # ----------------------------------------------------

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
                    reply_hint=reply_hint,
                    official_parent_text=parent_text,
                )
            )

        except Exception as exc:
            print(
                "PARSE FAILED:",
                post_id,
                "|",
                type(exc).__name__,
                "|",
                exc,
            )
            continue

        print(
            "AI extracted",
            len(extracted),
            "pick(s) from",
            post_id,
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

            picker = normalize_picker(
                extracted_pick.get(
                    "picker"
                )
            )

            if not picker:
                picker = picker_hint

            if not picker:
                print(
                    "SKIPPING UNKNOWN PICKER:",
                    post_id,
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

            bet_type = normalize_bet_type(
                extracted_pick.get(
                    "bet_type"
                )
                or "OTHER"
            )

            added_pick = bool(
                extracted_pick.get(
                    "added_pick"
                )
            )

            # Parent/additions context can safely mark the
            # source reply as an added pick.
            if added_hint:
                added_pick = True

            # ------------------------------------------------
            # CANONICAL DEDUPE
            # ------------------------------------------------

            key_pick = dict(
                extracted_pick
            )

            key_pick["picker"] = (
                picker
            )

            key_pick["week"] = (
                week
            )

            key_pick["selection"] = (
                selection
            )

            key_pick["bet_type"] = (
                bet_type
            )

            canonical = (
                canonical_pick_key(
                    key_pick
                )
            )

            if canonical in seen_keys:
                print(
                    "DUPLICATE SKIPPED:",
                    picker,
                    "| Week",
                    week,
                    "|",
                    selection,
                    "| source:",
                    post_id,
                )
                continue

            pick_id = stable_id(
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
                        post_id,

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

            new_pick_count += 1

            print(
                "ADDED:",
                picker,
                "| Week",
                week,
                "|",
                selection,
                "| reply:",
                reply_hint,
                "| historical recovery:",
                historical_reply_recovery,
                "| market:",
                bet_type,
            )

    # --------------------------------------------------------
    # UPDATE STATE
    # --------------------------------------------------------

    # IMPORTANT:
    # Advance last_x_post_id only from the NORMAL timeline.
    #
    # Historical recovery must never move the incremental
    # cursor.
    if timeline_posts:
        newest_id = max(
            (
                str(
                    post["id"]
                )
                for post in timeline_posts
                if post.get("id")
            ),
            key=int,
        )

        previous_id = state.get(
            "last_x_post_id"
        )

        if (
            not previous_id
            or int(newest_id)
            > int(previous_id)
        ):
            state[
                "last_x_post_id"
            ] = newest_id

    if should_backfill:
        state[
            "backfill_complete"
        ] = True

    # Mark the new recovery complete only if its timeline fetch
    # actually succeeded.
    if (
        should_reply_recovery
        and recovery_success
    ):
        state[
            "reply_only_recovery_v2_complete"
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
        len(existing),
    )

    print(
        "Reply-only recovery complete:",
        state.get(
            "reply_only_recovery_v2_complete"
        ),
    )

    print(
        "===================================="
    )


if __name__ == "__main__":
    ingest()
