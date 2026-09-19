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

from football_identity import (
    SUPPORTED_MARKETS,
    base_market,
    best_matchup_hints,
    canonical_game_identity,
    canonical_pick_key,
    clean_text,
    market_period,
    normalize_bet_type,
    safe_float,
    side_identity,
    spread_line_from_selection,
    total_direction,
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

STANDINGS_LOOKBACK_DAYS = 14
STANDINGS_MAX_PAGES = 6
PAT_HILL_THREAD_MAX_PAGES = 10
PAT_HILL_ADJACENT_HOURS_BEFORE = 6
PAT_HILL_ADJACENT_HOURS_AFTER = 18

PROCESSED_IDS_FLAG = (
    "processed_ids_initialized_v5"
)

OFFICIAL_RESULT_STATE_KEY = (
    "official_result_threads_processed"
)

MAX_PROCESSED_POST_IDS = 2000
MAX_FAILED_POST_IDS = 250

# Increment only when normal-post extraction/validation semantics change.
CURRENT_INGEST_VALIDATION_VERSION = 5

# Always rescan a bounded recent window from the official account.
# Processed IDs make this cheap/idempotent, while the overlap prevents
# cursor/state bugs from permanently hiding a source post.
RECENT_SAFETY_BACKFILL_DAYS = 10
RECENT_SAFETY_BACKFILL_PAGES = 4


# ============================================================
# BASIC HELPERS
# ============================================================

def stable_id(value):
    return (
        hashlib
        .sha1(
            str(value).encode("utf-8")
        )
        .hexdigest()[:16]
    )


def pick_week(pick):
    try:
        return int(
            pick.get("week") or 0
        )
    except Exception:
        return 0


def post_numeric_sort(value):
    value = str(value or "")

    if value.isdigit():
        return int(value)

    return 0


def parse_json_response(raw):
    raw = str(
        raw or ""
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

    if not raw:
        raise ValueError(
            "AI returned an empty response"
        )

    payload = json.loads(raw)

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "AI response is not a JSON object"
        )

    return payload


def safe_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()

        if lowered in {
            "true",
            "yes",
            "1",
        }:
            return True

        if lowered in {
            "false",
            "no",
            "0",
        }:
            return False

    return bool(value)


def normalize_picker_safe(value):
    picker = normalize_picker(value)

    if picker in TRACKED_PICKERS:
        return picker

    return None


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
        return int(match.group(1))
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
    # VERIFIED 2026 PICK EM WINDOWS
    # --------------------------------------------------------

    if d.year == 2026:

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

        week3_start = date(
            2026,
            9,
            14,
        )

        if d >= week3_start:
            return (
                3
                + (
                    d - week3_start
                ).days // 7
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
            days=september_1.weekday()
        )
    )

    delta = (
        d - first_monday
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
        explicit_week_from_text(text)
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
    Resolve the completed week represented by a PAT HILL post.

    Prefer the explicit "Week N records" label printed in the
    standings post. Fall back to the posting date only when that
    explicit marker is absent.
    """

    text = str(
        root_post.get("text")
        or ""
    )

    explicit = re.search(
        r"\bweek\s*#?\s*(\d{1,2})\s+records?\b",
        text,
        flags=re.I,
    )

    if explicit:
        try:
            return int(
                explicit.group(1)
            )
        except Exception:
            pass

    current_week = infer_week(
        text,
        root_post.get("created_at"),
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
        f"/users/by/username/{USERNAME}"
    )

    return str(
        payload["data"]["id"]
    )


def iso_x_time(dt):
    return (
        dt
        .astimezone(timezone.utc)
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
        "max_results": 100,

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

    posts = []
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
            if (
                str(
                    post.get("author_id")
                    or ""
                )
                != str(user_id)
            ):
                continue

            posts.append(post)

        for media in (
            payload
            .get("includes", {})
            .get("media", [])
        ):
            key = media.get(
                "media_key"
            )

            if key:
                media_map[key] = media

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

        params[
            "pagination_token"
        ] = next_token

    return (
        posts,
        media_map,
    )


def fetch_recent_official_conversation(
    *,
    conversation_id,
    official_user_id,
    max_pages=PAT_HILL_THREAD_MAX_PAGES,
):
    """
    Exhaustively fetch the official account's posts in one X
    conversation using recent search.

    The normal user timeline is intentionally still used for broad PAT
    HILL discovery. This targeted pass exists because a busy account
    timeline can be paginated/truncated before every reply in a result
    thread has been collected.

    Failures are returned to the caller so it can use the deeper timeline
    fallback instead of weakening reconciliation validation.
    """

    params = {
        "query": (
            f"conversation_id:{conversation_id} "
            f"from:{USERNAME} -is:retweet"
        ),
        "max_results": 100,
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

    posts = []
    media_map = {}
    pages = 0

    while True:
        payload = x_get(
            "/tweets/search/recent",
            params,
        )

        for post in payload.get(
            "data",
            [],
        ):
            if (
                str(
                    post.get("author_id")
                    or ""
                )
                != str(official_user_id)
            ):
                continue

            if (
                str(
                    post.get("conversation_id")
                    or post.get("id")
                    or ""
                )
                != str(conversation_id)
            ):
                continue

            posts.append(post)

        for media in (
            payload
            .get("includes", {})
            .get("media", [])
        ):
            key = media.get("media_key")

            if key:
                media_map[key] = media

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

        params["next_token"] = next_token

    return posts, media_map


def merge_post_collections(
    *collections,
):
    """Merge post/media collections without losing media expansions."""

    by_id = {}
    media_map = {}

    for posts, media in collections:
        for post in posts or []:
            post_id = str(
                post.get("id")
                or ""
            )

            if post_id:
                by_id[post_id] = post

        media_map.update(
            media or {}
        )

    posts = sorted(
        by_id.values(),
        key=lambda post:
            post_numeric_sort(
                post.get("id")
            ),
    )

    return posts, media_map


def parse_x_datetime(value):
    """Parse one X created_at value as an aware UTC datetime."""

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def post_has_media(post):
    return bool(
        (
            post.get(
                "attachments",
                {}
            )
            or {}
        ).get(
            "media_keys",
            []
        )
    )


def collect_pat_hill_thread(
    *,
    root_post,
    discovery_posts,
    discovery_media_map,
    official_user_id,
    standings_start_time,
):
    """
    Build the most complete official PAT HILL result-card source set.

    PAT HILL result cards are usually replies in the standings
    conversation, but X can expose an authored reply/subtweet with a
    different conversation_id. Therefore conversation_id is a strong
    signal, not a hard boundary.

    Collection layers:
    1. matching posts already found by standings discovery;
    2. X recent-search posts for the standings conversation;
    3. a deeper official-account timeline pass;
    4. official MEDIA posts in a narrow time window around the standings
       root, even when X assigned a different conversation_id.

    This function never decides that a week is valid. The existing strict
    result-card extraction and printed-record validation remain the only
    authority allowed to replace a week.
    """

    root_id = str(
        root_post.get("id")
        or ""
    )

    conversation_id = str(
        root_post.get("conversation_id")
        or root_id
    )

    root_created_at = parse_x_datetime(
        root_post.get("created_at")
    )

    base_posts = [
        post
        for post in discovery_posts
        if (
            str(
                post.get("author_id")
                or ""
            )
            == str(official_user_id)
            and str(
                post.get("conversation_id")
                or post.get("id")
                or ""
            )
            == conversation_id
        )
    ]

    collections = [
        (
            base_posts,
            discovery_media_map,
        )
    ]

    try:
        search_posts, search_media = (
            fetch_recent_official_conversation(
                conversation_id=conversation_id,
                official_user_id=official_user_id,
            )
        )

        collections.append(
            (search_posts, search_media)
        )

        print(
            "PAT HILL targeted conversation search posts:",
            len(search_posts),
        )

    except Exception as exc:
        print(
            "PAT HILL targeted conversation search unavailable:",
            type(exc).__name__,
            exc,
        )

    try:
        deep_posts, deep_media = (
            fetch_user_posts(
                official_user_id,
                start_time=standings_start_time,
                max_pages=
                    PAT_HILL_THREAD_MAX_PAGES,
            )
        )

        deep_thread_posts = [
            post
            for post in deep_posts
            if (
                str(
                    post.get("author_id")
                    or ""
                )
                == str(official_user_id)
                and str(
                    post.get("conversation_id")
                    or post.get("id")
                    or ""
                )
                == conversation_id
            )
        ]

        collections.append(
            (deep_thread_posts, deep_media)
        )

        print(
            "PAT HILL deep timeline thread posts:",
            len(deep_thread_posts),
        )

        # ----------------------------------------------------
        # X conversation IDs are not a reliable hard boundary
        # for every authored reply/subtweet. Result-card posts
        # are image based, so collect nearby OFFICIAL media
        # posts as candidate source material too.
        # ----------------------------------------------------

        adjacent_media_posts = []

        if root_created_at is not None:
            window_start = (
                root_created_at
                - timedelta(
                    hours=
                        PAT_HILL_ADJACENT_HOURS_BEFORE
                )
            )

            window_end = (
                root_created_at
                + timedelta(
                    hours=
                        PAT_HILL_ADJACENT_HOURS_AFTER
                )
            )

            for post in deep_posts:
                if (
                    str(
                        post.get("author_id")
                        or ""
                    )
                    != str(official_user_id)
                ):
                    continue

                if not post_has_media(post):
                    continue

                post_created_at = (
                    parse_x_datetime(
                        post.get("created_at")
                    )
                )

                if post_created_at is None:
                    continue

                if not (
                    window_start
                    <= post_created_at
                    <= window_end
                ):
                    continue

                adjacent_media_posts.append(
                    post
                )

        collections.append(
            (adjacent_media_posts, deep_media)
        )

        cross_conversation_count = sum(
            1
            for post in adjacent_media_posts
            if str(
                post.get("conversation_id")
                or post.get("id")
                or ""
            )
            != conversation_id
        )

        print(
            "PAT HILL adjacent official media posts:",
            len(adjacent_media_posts),
            "| cross-conversation:",
            cross_conversation_count,
        )

    except Exception as exc:
        print(
            "PAT HILL deep timeline fallback failed:",
            type(exc).__name__,
            exc,
        )

    thread_posts, media_map = (
        merge_post_collections(
            *collections
        )
    )

    if root_id and not any(
        str(post.get("id")) == root_id
        for post in thread_posts
    ):
        thread_posts.append(root_post)
        thread_posts = sorted(
            thread_posts,
            key=lambda post:
                post_numeric_sort(
                    post.get("id")
                ),
        )

    print(
        "PAT HILL merged candidate source posts:",
        len(thread_posts),
        "| images:",
        sum(
            len(
                image_urls_for_post(
                    post,
                    media_map,
                )
            )
            for post in thread_posts
        ),
    )

    return thread_posts, media_map


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

    post = payload.get("data")

    media_map = {}

    for media in (
        payload
        .get("includes", {})
        .get("media", [])
    ):
        key = media.get(
            "media_key"
        )

        if key:
            media_map[key] = media

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
        .get("attachments", {})
        .get("media_keys", [])
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
            url = media.get("url")

        else:
            url = media.get(
                "preview_image_url"
            )

        if (
            url
            and url not in urls
        ):
            urls.append(url)

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
            value = reference.get("id")

            if value:
                return str(value)

    return None


def is_reply_post(post):
    return bool(
        replied_to_post_id(post)
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
        replied_to_post_id(post)
    )

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

        # Never consume fan / outside-account context.
        if (
            str(
                parent.get("author_id")
                or ""
            )
            != str(
                official_user_id
            )
        ):
            break

        parent_text = str(
            parent.get("text")
            or ""
        ).strip()

        if parent_text:
            pieces.append(
                parent_text
            )

        current_id = (
            replied_to_post_id(parent)
        )

    return "\n\n".join(
        pieces
    )


# ============================================================
# PICKER / ADDED PICK HINTS
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
        return "Stool Presidente"

    if (
        "return_of_rb" in text
        or "returnofrb" in text
        or "rico bosco" in text
        or "ricobosco" in text
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
# PAT HILL DETECTION
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


def completed_week_now():
    """Return the latest week that should have official results."""

    now_pt = datetime.now(
        PACIFIC
    )

    current_week = infer_week_from_date(
        now_pt.isoformat()
    )

    if not current_week:
        return 0

    return max(
        0,
        int(current_week) - 1,
    )


def should_scan_standings(state):
    """
    Scan on every Tuesday, and on any later run while official
    reconciliation is behind the latest completed week.

    This makes PAT HILL discovery independent of the normal X cursor
    and self-heals a missed Monday/Tuesday standings post without a
    manual state reset.
    """

    now_pt = datetime.now(
        PACIFIC
    )

    try:
        last_official = int(
            state.get(
                "last_official_reconciled_week"
            )
            or 0
        )
    except Exception:
        last_official = 0

    expected_completed = (
        completed_week_now()
    )

    return (
        now_pt.weekday() == 1
        or last_official
        < expected_completed
    )


# ============================================================
# PRINTED STANDINGS PARSER
# ============================================================

def parse_printed_standings(
    text,
    target_week=None,
):
    """
    Parse the WEEKLY records from a PAT HILL standings post.

    PAT HILL posts can contain season standings first and a separate
    "Week N records" section later. The weekly section is the only
    valid reconciliation target for replacing one week's card.
    """

    source = str(
        text or ""
    )

    weekly_text = source

    if target_week is not None:
        section = re.search(
            (
                rf"\bweek\s*#?\s*{int(target_week)}"
                rf"\s+records?\s*:?\s*(.*)$"
            ),
            source,
            flags=re.I | re.S,
        )

        if section:
            weekly_text = (
                section.group(1)
            )
        else:
            # Fail closed when the post contains a weekly-records
            # section, but not for the target week we intend to
            # replace. This prevents season standings from being
            # mistaken for one week's record.
            any_week_section = re.search(
                r"\bweek\s*#?\s*\d{1,2}\s+records?\b",
                source,
                flags=re.I,
            )

            if any_week_section:
                return {}

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

    lowered = weekly_text.lower()
    output = {}

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

            output[picker] = {
                "wins": int(
                    match.group(1)
                ),
                "losses": int(
                    match.group(2)
                ),
                "pushes": int(
                    match.group(3)
                    or 0
                ),
            }

            break

    return output


# ============================================================
# CANDIDATE NORMAL-PICK FILTER
# ============================================================

def looks_like_pick_post(
    text,
    image_urls,
):
    """
    This is intentionally a broad candidate filter.

    It does NOT decide whether the post really contains picks.
    The validated AI extraction makes that decision.

    Images are candidates because initial cards are image-heavy.
    """

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
        "added pick",
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
        pick or {}
    )

    selection = clean_text(
        pick.get("selection")
    )

    pick[
        "selection"
    ] = selection

    text = selection.lower()

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    first_quarter = bool(
        re.search(
            r"\b(?:1q|first quarter|1st quarter)\b",
            text,
            flags=re.I,
        )
    )

    first_half = bool(
        re.search(
            r"\b(?:1h|first half|1st half)\b",
            text,
            flags=re.I,
        )
    )

    team_total = bool(
        re.search(
            r"\b(?:tt|team total)\b",
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
            bet_type = "TEAM_TOTAL"

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
                    compact.group(1).lower()
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
            r"[+-]\s*\d+(?:\.\d+)?",
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

    pick[
        "bet_type"
    ] = normalize_bet_type(
        bet_type
    )

    # --------------------------------------------------------
    # Spread sign protection.
    #
    # Visible selection is authoritative.
    # --------------------------------------------------------

    if (
        base_market(
            pick["bet_type"]
        )
        == "SPREAD"
    ):
        visible_line = (
            spread_line_from_selection(
                pick
            )
        )

        if visible_line is not None:
            pick[
                "line"
            ] = visible_line
        else:
            structured_line = safe_float(
                pick.get("line")
            )

            selected_team = clean_text(
                pick.get("team")
            )

            if not selected_team:
                selected_team = clean_text(
                    pick.get("side")
                )

            if (
                structured_line is not None
                and selected_team
                and selection
                and clean_text(selection).lower()
                == selected_team.lower()
            ):
                pick["selection"] = (
                    f"{clean_text(selection)} "
                    f"{structured_line:+g}"
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
    """
    Extract one official source post.

    IMPORTANT:
    This function returns a payload, not merely a list of picks.

    The payload contains explicit extraction-completeness metadata
    which is validated before the post may be marked processed.
    """

    from openai import OpenAI

    client = OpenAI()

    supplied_image_count = len(
        image_urls
    )

    prompt = f"""
You are a strict extraction engine for NCAA college football
gambling picks from the verified official @barstoolpickem X
account.

TRACK ONLY:
- Big Cat
- Stool Presidente / Dave Portnoy / El Pres
- Rico Bosco

ACTUAL SOURCE POST:
URL: {post_url}
POSTED: {posted_at}

SOURCE POST TEXT:
{text}

VERIFIED OFFICIAL PARENT CONTEXT:
{parent_text or "None"}

PICKER HINT:
{picker_hint or "Unknown"}

DEFAULT WEEK:
{inferred_week}

IS OFFICIAL REPLY:
{reply_hint}

NUMBER OF SOURCE IMAGES SUPPLIED:
{supplied_image_count}

============================================================
SOURCE BOUNDARY
============================================================

Only wagers physically contained in the ACTUAL SOURCE POST may
be returned as picks.

Parent context may identify the picker or explain the reply,
but NEVER copy a wager from a parent post into this child post.

Never use fan content.

Never use historical tracker data.

This extraction is for NEW PICKS, not weekly result cards.

============================================================
IMAGE COMPLETENESS
============================================================

You MUST inspect every supplied source image.

If {supplied_image_count} images were supplied, inspect all
{supplied_image_count} images before responding.

Return images_supplied={supplied_image_count}.

Return images_read as the number of supplied images you were
actually able to inspect.

If any supplied image cannot be read well enough to determine
whether it contains wagers, set complete=false.

For every image, return an image_checks entry with:
- image_index: 1-based index
- readable: true/false
- contains_wagers: true/false
- wager_count: number of individual wagers extracted from it

Do not count matchup headings as wagers.

Do not count records, kickoff times, scores, checkmarks, red Xs,
or decorative text as wagers.

If a card spans multiple images, read all pages.

============================================================
PICK EXTRACTION
============================================================

Extract EVERY individual wager contained in this source post.

Each wager must be its own pick object.

For card-style images:
- matchup headings establish game context
- each wager underneath belongs to the closest applicable
  matchup heading
- preserve that matchup on the wager
- do not allow a total such as "Over 58.5" or "Under 53.5" to
  lose its matchup context

Preserve:
- picker
- matchup
- selected team
- opponent
- market
- side
- exact signed spread
- total
- 1Q distinction
- 1H distinction
- team-total distinction
- added-pick status

For spreads, the sign shown in the source is critical:
- Team -3 means line=-3
- Team +3 means line=3
- selection MUST include the selected team AND exact signed spread.
  Never return selection="Team" with the spread only in the line field.

For totals:
- side must be OVER or UNDER
- line must be the total number
- matchup must identify the game whenever the source provides
  the matchup context

For team totals:
- team must identify the team whose total is being wagered
- side must be OVER or UNDER

For moneylines:
- team must identify the selected team

============================================================
PICKER
============================================================

Normalize only to:
- Big Cat
- Rico Bosco
- Stool Presidente

If the source itself does not name the picker but the verified
official parent context clearly identifies the picker, you may
use that picker.

If picker identity is still genuinely unknown for a wager, set
complete=false rather than guessing.

============================================================
IS THIS ACTUALLY A PICK POST?
============================================================

The outer system intentionally sends some image posts that are
not gambling-pick posts.

Set is_pick_post=true ONLY if the ACTUAL SOURCE POST contains at
least one new tracked wager.

If it contains no new tracked wager, set is_pick_post=false and
return picks=[].

A result card or PAT HILL standings card is NOT a new-pick post.

============================================================
COMPLETENESS
============================================================

Set complete=true only when:

1. Every supplied image was inspected.
2. Every readable wager in the actual source post was extracted.
3. No wager was copied from parent context.
4. Every returned wager has a known tracked picker.
5. Every returned wager has a usable market and selection.
6. Every spread/total/team-total that requires a numeric line
   has that line.
7. Every total with source matchup context retains that matchup.
8. You did not have to guess through an unreadable/cropped card.

If uncertain whether extraction is complete, set complete=false.

Do NOT invent data merely to make complete=true.

============================================================
OUTPUT
============================================================

Return JSON only:

{{
  "complete": true,
  "is_pick_post": true,
  "images_supplied": {supplied_image_count},
  "images_read": {supplied_image_count},
  "image_checks": [
    {{
      "image_index": 1,
      "readable": true,
      "contains_wagers": true,
      "wager_count": 3
    }}
  ],
  "extraction_notes": "",
  "picks": [
    {{
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "Team A @ Team B or null",
      "team": "selected/team-total team or null",
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
No commentary outside JSON.
JSON only.
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
            }
        )

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )

    return parse_json_response(
        response.output_text
    )


# ============================================================
# NORMALIZE ONE EXTRACTED PICK
# ============================================================

def normalize_ai_pick(
    raw_pick,
    *,
    default_week,
    picker_hint,
):
    if not isinstance(
        raw_pick,
        dict,
    ):
        raise ValueError(
            "Extracted pick is not an object"
        )

    pick = normalize_extracted_market(
        raw_pick
    )

    picker = normalize_picker_safe(
        pick.get("picker")
    )

    if not picker:
        picker = normalize_picker_safe(
            picker_hint
        )

    if not picker:
        raise ValueError(
            "Extracted wager has no valid tracked picker"
        )

    selection = clean_text(
        pick.get("selection")
    )

    if not selection:
        raise ValueError(
            f"{picker} extracted wager has empty selection"
        )

    try:
        week = int(
            pick.get("week")
            or default_week
        )
    except Exception:
        week = default_week

    if not week:
        raise ValueError(
            f"{picker} {selection}: no valid week"
        )

    pick["picker"] = picker
    pick["sport"] = "CFB"
    pick["selection"] = selection
    pick["week"] = int(week)

    pick["bet_type"] = (
        normalize_bet_type(
            pick.get("bet_type")
        )
    )

    pick["matchup"] = (
        clean_text(
            pick.get("matchup")
        )
        or None
    )

    pick["team"] = (
        clean_text(
            pick.get("team")
        )
        or None
    )

    pick["opponent"] = (
        clean_text(
            pick.get("opponent")
        )
        or None
    )

    side = clean_text(
        pick.get("side")
    )

    if side.upper() in {
        "OVER",
        "UNDER",
    }:
        side = side.upper()

    pick["side"] = (
        side or None
    )

    pick["line"] = safe_float(
        pick.get("line")
    )

    try:
        confidence = float(
            pick.get("confidence")
            or 0.95
        )
    except Exception:
        confidence = 0.95

    pick["confidence"] = max(
        0.0,
        min(
            confidence,
            1.0,
        ),
    )

    pick["added_pick"] = safe_bool(
        pick.get("added_pick")
    )

    try:
        pick["units"] = float(
            pick.get("units")
            or 1.0
        )
    except Exception:
        pick["units"] = 1.0

    return pick


# ============================================================
# STRUCTURAL PICK VALIDATION
# ============================================================

def validate_normal_pick(pick):
    """
    Validate one normalized wager before the source post can be
    committed as processed.

    This is intentionally fail-closed.
    """

    errors = []

    picker = normalize_picker_safe(
        pick.get("picker")
    )

    if not picker:
        errors.append(
            "invalid picker"
        )

    week = pick_week(pick)

    if week <= 0:
        errors.append(
            "invalid week"
        )

    selection = clean_text(
        pick.get("selection")
    )

    if not selection:
        errors.append(
            "empty selection"
        )

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    if bet_type not in SUPPORTED_MARKETS:
        errors.append(
            f"unsupported market {bet_type}"
        )

    market = base_market(
        bet_type
    )

    line = safe_float(
        pick.get("line")
    )

    if market in {
        "SPREAD",
        "TOTAL",
        "TEAM_TOTAL",
    }:
        if line is None:
            errors.append(
                f"{market} has no numeric line"
            )

    if market == "SPREAD":
        selected = side_identity(
            pick
        )

        if not selected:
            errors.append(
                "spread has no selected team"
            )

        visible_line = (
            spread_line_from_selection(
                pick
            )
        )

        if visible_line is not None:
            pick["line"] = visible_line

    if market == "TOTAL":
        direction = total_direction(
            pick
        )

        if direction not in {
            "OVER",
            "UNDER",
        }:
            errors.append(
                "total has no OVER/UNDER direction"
            )

        # A full-game/period total must identify its game.
        # This is the exact class of historical failure where
        # "Over 58.5" became detached from its matchup.
        game = canonical_game_identity(
            pick
        )

        hints = best_matchup_hints(
            pick
        )

        if (
            not game
            and len(hints) != 2
        ):
            errors.append(
                "total has no complete matchup identity"
            )

    if market == "TEAM_TOTAL":
        direction = total_direction(
            pick
        )

        if direction not in {
            "OVER",
            "UNDER",
        }:
            errors.append(
                "team total has no OVER/UNDER direction"
            )

        selected = side_identity(
            pick
        )

        if not selected:
            errors.append(
                "team total has no team identity"
            )

    if market == "MONEYLINE":
        selected = side_identity(
            pick
        )

        if not selected:
            errors.append(
                "moneyline has no selected team"
            )

    if errors:
        raise ValueError(
            (
                f"{picker or 'Unknown'} | "
                f"{selection or 'Unknown wager'} | "
                + "; ".join(errors)
            )
        )

    return pick


# ============================================================
# VALIDATE ENTIRE NORMAL-POST EXTRACTION
# ============================================================

def validate_normal_post_payload(
    payload,
    *,
    image_urls,
    default_week,
    picker_hint,
):
    """
    Transaction boundary.

    Nothing from this source post may be committed until this
    function succeeds completely.
    """

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Normal extraction payload is not an object"
        )

    complete = safe_bool(
        payload.get("complete")
    )

    if not complete:
        notes = clean_text(
            payload.get(
                "extraction_notes"
            )
        )

        raise ValueError(
            "AI marked source extraction incomplete"
            + (
                f": {notes}"
                if notes
                else ""
            )
        )

    expected_images = len(
        image_urls
    )

    try:
        reported_supplied = int(
            payload.get(
                "images_supplied"
            )
            or 0
        )
    except Exception:
        reported_supplied = -1

    try:
        images_read = int(
            payload.get(
                "images_read"
            )
            or 0
        )
    except Exception:
        images_read = -1

    if (
        reported_supplied
        != expected_images
    ):
        raise ValueError(
            "AI image-count mismatch: "
            f"source supplied {expected_images}, "
            f"AI reported {reported_supplied}"
        )

    if images_read != expected_images:
        raise ValueError(
            "AI did not confirm reading every image: "
            f"{images_read}/{expected_images}"
        )

    checks = payload.get(
        "image_checks"
    )

    if checks is None:
        checks = []

    if not isinstance(
        checks,
        list,
    ):
        raise ValueError(
            "image_checks is not a list"
        )

    if expected_images:
        if len(checks) != expected_images:
            raise ValueError(
                "AI image_checks count mismatch: "
                f"{len(checks)}/{expected_images}"
            )

        indexes = set()

        for check in checks:
            if not isinstance(
                check,
                dict,
            ):
                raise ValueError(
                    "Invalid image_checks entry"
                )

            try:
                image_index = int(
                    check.get(
                        "image_index"
                    )
                )
            except Exception:
                raise ValueError(
                    "Image check has invalid index"
                )

            indexes.add(
                image_index
            )

            if not safe_bool(
                check.get("readable")
            ):
                raise ValueError(
                    f"Source image {image_index} "
                    "was not readable"
                )

            try:
                wager_count = int(
                    check.get(
                        "wager_count"
                    )
                    or 0
                )
            except Exception:
                raise ValueError(
                    f"Source image {image_index} "
                    "has invalid wager_count"
                )

            if wager_count < 0:
                raise ValueError(
                    f"Source image {image_index} "
                    "has negative wager_count"
                )

        expected_indexes = set(
            range(
                1,
                expected_images + 1,
            )
        )

        if indexes != expected_indexes:
            raise ValueError(
                "Image check indexes do not cover "
                "every supplied image"
            )

    is_pick_post = safe_bool(
        payload.get(
            "is_pick_post"
        )
    )

    raw_picks = payload.get(
        "picks"
    )

    if not isinstance(
        raw_picks,
        list,
    ):
        raise ValueError(
            "AI picks is not a list"
        )

    if (
        is_pick_post
        and not raw_picks
    ):
        raise ValueError(
            "AI says this is a pick post "
            "but extracted zero wagers"
        )

    if (
        not is_pick_post
        and raw_picks
    ):
        raise ValueError(
            "AI says this is not a pick post "
            "but returned wagers"
        )

    # --------------------------------------------------------
    # Non-pick candidate.
    #
    # It may safely be marked processed because:
    # - extraction is complete
    # - every image was read
    # - AI explicitly says no new tracked wagers exist
    # --------------------------------------------------------

    if not is_pick_post:
        return {
            "is_pick_post": False,
            "picks": [],
            "image_wager_count": 0,
        }

    normalized = []

    for raw_pick in raw_picks:
        pick = normalize_ai_pick(
            raw_pick,
            default_week=default_week,
            picker_hint=picker_hint,
        )

        validate_normal_pick(
            pick
        )

        normalized.append(
            pick
        )

    # --------------------------------------------------------
    # Validate image-reported wager count against extraction.
    #
    # This catches the most important multi-image omission case.
    # --------------------------------------------------------

    image_wager_count = 0

    for check in checks:
        try:
            image_wager_count += int(
                check.get(
                    "wager_count"
                )
                or 0
            )
        except Exception:
            raise ValueError(
                "Invalid image wager count"
            )

    # Text-only posts have no image count to reconcile.
    if expected_images:
        if (
            image_wager_count
            != len(normalized)
        ):
            raise ValueError(
                "Image wager-count reconciliation failed: "
                f"image checks report "
                f"{image_wager_count}, "
                f"but {len(normalized)} wagers "
                "were extracted"
            )

    # --------------------------------------------------------
    # Internal canonical duplicate validation.
    #
    # AI must not return the same wager twice from one post.
    # --------------------------------------------------------

    local_keys = set()

    for pick in normalized:
        key = canonical_pick_key(
            pick
        )

        if key in local_keys:
            raise ValueError(
                "AI returned duplicate wager within "
                f"the same source post: "
                f"{pick.get('picker')} | "
                f"{pick.get('selection')}"
            )

        local_keys.add(key)

    return {
        "is_pick_post": True,
        "picks": normalized,
        "image_wager_count":
            image_wager_count,
    }


# ============================================================
# BUILD STORED NORMAL PICK
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
        extracted.get("bet_type")
    )

    confidence = float(
        extracted.get(
            "confidence"
        )
        or 0.95
    )

    status = (
        "OPEN"
        if (
            confidence >= 0.90
            and bet_type
            in SUPPORTED_MARKETS
        )
        else "REVIEW"
    )

    pick = {
        "picker":
            extracted["picker"],

        "sport":
            "CFB",

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
                or 1.0
            ),

        "mortal_lock":
            False,

        "week":
            int(
                extracted["week"]
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
                post.get("id")
            ),

        "source_url":
            post_url,

        "source_text":
            str(
                post.get("text")
                or ""
            ),

        "source_is_reply":
            bool(reply_hint),

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

        "ingest_validated":
            True,

        "ingest_validation_version":
            CURRENT_INGEST_VALIDATION_VERSION,
    }

    pick["id"] = deterministic_pick_id(
        pick
    )

    return pick


# ============================================================
# PICK QUALITY / PERMANENT CANONICAL DEDUPE
# ============================================================

def deterministic_pick_id(pick):
    """
    Build one stable wager ID from the same canonical wager identity
    used by permanent deduplication.

    The ID deliberately does not depend on mutable enrichment fields
    such as ESPN event_id, status, result, final_score, or grading
    timestamps. The same canonical wager therefore receives the same
    ID on every run.

    Official PAT HILL rows keep their existing official IDs because
    those rows represent the authoritative finalized card.
    """
    return stable_id(
        repr(
            canonical_pick_key(
                pick
            )
        )
    )


def ensure_pick_ids(picks):
    """
    Self-heal missing IDs on valid stored wagers and verify that no
    two distinct canonical wagers share one stored ID.

    Existing IDs are preserved. Only rows with a missing/blank ID are
    assigned a deterministic ID.

    This is intentionally generic: there is no week-, picker-, team-,
    post-, selection-, or event-specific repair knowledge here.
    """
    repaired = 0
    owners = {}

    for index, pick in enumerate(
        picks
    ):
        pick_id = clean_text(
            pick.get("id")
        )

        if not pick_id:
            pick_id = deterministic_pick_id(
                pick
            )

            pick["id"] = pick_id
            repaired += 1

            print(
                "MISSING PICK ID SELF-HEALED:",
                pick.get("picker"),
                "| Week",
                pick.get("week"),
                "|",
                pick.get("selection"),
                "| id:",
                pick_id,
            )

        canonical_key = (
            canonical_pick_key(
                pick
            )
        )

        prior = owners.get(
            pick_id
        )

        if prior is None:
            owners[pick_id] = (
                index,
                canonical_key,
            )
            continue

        prior_index, prior_key = prior

        if prior_key != canonical_key:
            raise RuntimeError(
                "Stored pick ID collision: "
                f"id={pick_id} belongs to two "
                "different canonical wagers "
                f"(rows {prior_index + 1} "
                f"and {index + 1})."
            )

    if repaired:
        print(
            "Missing wager IDs self-healed:",
            repaired,
        )

    return picks


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

    if pick.get("event_id"):
        score += 30

    if pick.get("final_score"):
        score += 15

    if pick.get("matchup"):
        score += 10

    if pick.get("team"):
        score += 5

    if pick.get(
        "ingest_validated"
    ):
        score += 3

    return score


def dedupe_picks(picks):
    """
    One permanent canonical identity system.

    canonical_pick_key comes from football_identity.py and
    includes game identity for spreads as well as totals.
    """

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
            key=lambda item: (
                pick_quality(
                    item[1]
                ),
                -item[0],
            ),
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
                "| Week",
                duplicate.get(
                    "week"
                ),
                "|",
                duplicate.get(
                    "selection"
                ),
                "| matchup:",
                duplicate.get(
                    "matchup"
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

    # Preserve any prior processed state as well.
    ids.update(
        str(value)
        for value in (
            state.get(
                "processed_post_ids",
                [],
            )
            or []
        )
    )

    state[
        "processed_post_ids"
    ] = sorted(
        ids,
        key=post_numeric_sort,
    )[
        -MAX_PROCESSED_POST_IDS:
    ]

    state[
        PROCESSED_IDS_FLAG
    ] = True

    print(
        "Seeded/validated processed post IDs:",
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
                str(post_id)
            )

            continue

        if not post:
            still_failed.append(
                str(post_id)
            )
            continue

        if (
            str(
                post.get("author_id")
                or ""
            )
            != str(
                official_user_id
            )
        ):
            continue

        posts.append(post)

        media_map.update(media)

    # Successfully re-fetched IDs will be re-added below if
    # validation fails again.
    state[
        "failed_post_ids"
    ] = still_failed

    return (
        posts,
        media_map,
    )


# ============================================================
# PROCESS NORMAL POSTS — TRANSACTIONAL
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
        canonical_pick_key(pick)
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
    validated_post_count = 0
    non_pick_candidate_count = 0
    duplicate_count = 0

    for post in sorted(
        posts,
        key=lambda item:
            post_numeric_sort(
                item.get("id")
            ),
    ):
        post_id = str(
            post.get("id")
            or ""
        )

        if not post_id:
            continue

        if (
            str(
                post.get("author_id")
                or ""
            )
            != str(
                official_user_id
            )
        ):
            continue

        prior_rows_for_post = [
            pick
            for pick in existing
            if str(
                pick.get("source_post_id")
                or ""
            ) == post_id
            and not pick.get(
                "official_reconciled"
            )
        ]

        needs_validation_upgrade = any(
            int(
                pick.get(
                    "ingest_validation_version"
                )
                or 0
            ) < CURRENT_INGEST_VALIDATION_VERSION
            for pick in prior_rows_for_post
        )

        if (
            post_id in processed_ids
            and post_id not in failed_ids
            and not needs_validation_upgrade
        ):
            continue

        if needs_validation_upgrade:
            print(
                "REVALIDATING PRIOR SOURCE POST:",
                post_id,
                "| old validation generation detected",
            )

        image_urls = (
            image_urls_for_post(
                post,
                media_map,
            )
        )

        text = str(
            post.get("text")
            or ""
        )

        reply_hint = (
            is_reply_post(post)
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
        # PAT HILL RESULT POSTS
        #
        # Safe to mark processed by normal ingestion because
        # they are handled separately by official reconciliation.
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

        # ----------------------------------------------------
        # Clearly irrelevant posts
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

        print()
        print(
            "PARSING CANDIDATE PICK POST:",
            post_id,
            "| week:",
            week,
            "| reply:",
            reply_hint,
            "| picker:",
            picker_hint,
            "| images:",
            len(image_urls),
        )

        # ----------------------------------------------------
        # PHASE 1: EXTRACT
        # ----------------------------------------------------

        try:
            payload = parse_post_with_ai(
                text=text,
                image_urls=image_urls,
                post_url=post_url,
                posted_at=post.get(
                    "created_at"
                ),
                inferred_week=week,
                picker_hint=picker_hint,
                reply_hint=reply_hint,
                parent_text=parent_text,
            )

        except Exception as exc:
            print(
                "EXTRACTION FAILED — "
                "QUEUED FOR RETRY:",
                post_id,
                type(exc).__name__,
                exc,
            )

            failed_ids.add(
                post_id
            )

            processed_ids.discard(
                post_id
            )

            continue

        # ----------------------------------------------------
        # PHASE 2: VALIDATE THE ENTIRE SOURCE POST
        #
        # THIS MUST PASS BEFORE ANY WAGER IS COMMITTED.
        # ----------------------------------------------------

        try:
            validated = (
                validate_normal_post_payload(
                    payload,
                    image_urls=image_urls,
                    default_week=week,
                    picker_hint=picker_hint,
                )
            )

        except Exception as exc:
            print(
                "VALIDATION FAILED — "
                "POST NOT PROCESSED / "
                "QUEUED FOR RETRY:",
                post_id,
                type(exc).__name__,
                exc,
            )

            failed_ids.add(
                post_id
            )

            processed_ids.discard(
                post_id
            )

            continue

        # ----------------------------------------------------
        # Complete extraction says this broad candidate did not
        # actually contain a new tracked wager.
        # ----------------------------------------------------

        if not validated[
            "is_pick_post"
        ]:
            print(
                "VALIDATED NON-PICK CANDIDATE:",
                post_id,
                "| all source material inspected",
            )

            processed_ids.add(
                post_id
            )

            failed_ids.discard(
                post_id
            )

            validated_post_count += 1
            non_pick_candidate_count += 1

            continue

        extracted_picks = (
            validated["picks"]
        )

        # ----------------------------------------------------
        # VALIDATION-GENERATION UPGRADE
        # ----------------------------------------------------

        if needs_validation_upgrade:
            old_source_rows = [
                pick
                for pick in existing
                if str(
                    pick.get("source_post_id")
                    or ""
                ) == post_id
                and not pick.get(
                    "official_reconciled"
                )
            ]

            old_source_keys = {
                canonical_pick_key(pick)
                for pick in old_source_rows
            }

            existing = [
                pick
                for pick in existing
                if not (
                    str(
                        pick.get("source_post_id")
                        or ""
                    ) == post_id
                    and not pick.get(
                        "official_reconciled"
                    )
                )
            ]

            seen_wagers.difference_update(
                old_source_keys
            )

            print(
                "REBUILDING VALIDATED SOURCE POST:",
                post_id,
                "| prior provisional rows:",
                len(old_source_rows),
            )

        # ----------------------------------------------------
        # PHASE 3: BUILD THE TRANSACTION IN MEMORY
        #
        # No mutation of existing[] yet.
        # ----------------------------------------------------

        pending_rows = []
        pending_keys = set()

        transaction_failed = False

        for extracted in extracted_picks:
            try:
                key = canonical_pick_key(
                    extracted
                )

                if key in pending_keys:
                    raise ValueError(
                        "Duplicate canonical wager "
                        "inside transaction"
                    )

                pending_keys.add(key)

                if key in seen_wagers:
                    duplicate_count += 1

                    print(
                        "EXISTING WAGER RECOGNIZED:",
                        extracted.get(
                            "picker"
                        ),
                        "| Week",
                        extracted.get(
                            "week"
                        ),
                        "|",
                        extracted.get(
                            "selection"
                        ),
                        "| matchup:",
                        extracted.get(
                            "matchup"
                        ),
                    )

                    continue

                stored = stored_pick_from_ai(
                    extracted,
                    post=post,
                    post_url=post_url,
                    reply_hint=reply_hint,
                    added_hint=added_hint,
                )

                # Final identity sanity check on the exact row
                # that would be stored.
                stored_key = (
                    canonical_pick_key(
                        stored
                    )
                )

                if stored_key != key:
                    raise ValueError(
                        "Stored-row canonical identity "
                        "changed after normalization"
                    )

                pending_rows.append(
                    (
                        stored_key,
                        stored,
                    )
                )

            except Exception as exc:
                transaction_failed = True

                print(
                    "TRANSACTION BUILD FAILED:",
                    post_id,
                    type(exc).__name__,
                    exc,
                )

                break

        if transaction_failed:
            failed_ids.add(
                post_id
            )

            processed_ids.discard(
                post_id
            )

            continue

        # ----------------------------------------------------
        # PHASE 4: COMMIT
        #
        # At this point:
        # - AI completed extraction
        # - every image was accounted for
        # - image wager count reconciled
        # - every wager normalized
        # - every wager structurally validated
        # - internal duplicates rejected
        # - canonical identities built
        #
        # ONLY NOW may the post become processed.
        # ----------------------------------------------------

        for key, stored in pending_rows:
            existing.append(
                stored
            )

            seen_wagers.add(
                key
            )

            new_pick_count += 1

            print(
                "ADDED VALIDATED PICK:",
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
                "| matchup:",
                stored.get(
                    "matchup"
                ),
            )

        processed_ids.add(
            post_id
        )

        failed_ids.discard(
            post_id
        )

        validated_post_count += 1

        print(
            "POST TRANSACTION COMMITTED:",
            post_id,
            "| extracted:",
            len(extracted_picks),
            "| new:",
            len(pending_rows),
            "| existing duplicates:",
            (
                len(extracted_picks)
                - len(pending_rows)
            ),
        )

    state[
        "processed_post_ids"
    ] = sorted(
        processed_ids,
        key=post_numeric_sort,
    )[
        -MAX_PROCESSED_POST_IDS:
    ]

    state[
        "failed_post_ids"
    ] = sorted(
        failed_ids,
        key=post_numeric_sort,
    )[
        -MAX_FAILED_POST_IDS:
    ]

    return (
        existing,
        {
            "candidate_count":
                candidate_count,

            "new_pick_count":
                new_pick_count,

            "validated_post_count":
                validated_post_count,

            "non_pick_candidate_count":
                non_pick_candidate_count,

            "duplicate_count":
                duplicate_count,
        },
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
    """
    Extract an official PAT HILL result-card set with a two-pass vision
    workflow.

    Dense weekly cards can contain dozens of small wager rows. Sending all
    pages to one vision request can cause a model to correctly notice that
    something is unreadable/missing and return complete=false. We therefore
    read every image independently first, then give the model those page
    transcripts plus every original image for a final reconciliation pass.

    Safety is intentionally unchanged: this function does not decide whether
    a week is official. validate_official_results() still requires all three
    pickers and exact agreement with the printed standings before any atomic
    week replacement can occur.
    """
    from openai import OpenAI

    client = OpenAI()

    image_urls = []

    for post in thread_posts:
        for image_url in image_urls_for_post(
            post,
            media_map,
        ):
            if image_url not in image_urls:
                image_urls.append(image_url)

    if not image_urls:
        raise ValueError(
            "PAT HILL thread has no images"
        )

    thread_text = "\n\n".join(
        str(post.get("text") or "")
        for post in thread_posts
    )

    # --------------------------------------------------------
    # PASS 1 — READ EACH IMAGE INDEPENDENTLY
    # --------------------------------------------------------
    # This is deliberately page-local. It prevents one dense image from
    # being overlooked when six or more result-card pages are supplied in
    # the same request. The page transcript is evidence for pass 2 only;
    # it is never written directly to picks.json.
    # --------------------------------------------------------

    page_reads = []

    for image_index, image_url in enumerate(
        image_urls,
        start=1,
    ):
        page_prompt = f"""
You are reading ONE image from the verified official
@barstoolpickem PAT HILL STANDINGS result-card material.

TARGET WEEK:
{target_week}

IMAGE NUMBER:
{image_index} of {len(image_urls)}

Read this single image at maximum care. It may be a full card, a continuation
page, a standings graphic, or another image attached to the official result
material.

TASK
====

Transcribe every visible wager row and every visible result marker from THIS
IMAGE ONLY.

A GREEN CHECK means WIN.
A RED X means LOSS.
A push/tie symbol means PUSH.

Preserve:
- picker name when visible;
- exact concise wager/selection;
- matchup/team/opponent when visible;
- spread/total/moneyline value;
- 1Q, 1H and team-total distinctions;
- whether a wager appears in an Adds section;
- printed record when visible.

Do not infer rows from another page.
Do not invent cropped or unreadable text.
If a row is partly unreadable, include it with readable=false and preserve the
visible fragments in raw_text.

OUTPUT JSON ONLY
================

{{
  "image_index": {image_index},
  "read_successfully": true,
  "image_role": "RESULT_CARD|STANDINGS|OTHER",
  "picker": "Rico Bosco|Big Cat|Stool Presidente|null",
  "printed_record": {{
    "wins": 0,
    "losses": 0,
    "pushes": 0
  }},
  "rows": [
    {{
      "raw_text": "visible wager text",
      "selection": "exact concise wager or null",
      "matchup": "matchup or null",
      "team": "team or null",
      "opponent": "opponent or null",
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
      "side": "OVER|UNDER|team name|null",
      "line": 0.0,
      "result": "WIN|LOSS|PUSH|null",
      "added_pick": false,
      "readable": true
    }}
  ]
}}

Use null for a printed_record if it is not visible on this image.
No markdown. No commentary. JSON only.
"""

        page_response = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": page_prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": image_url,
                        },
                    ],
                }
            ],
        )

        page_payload = parse_json_response(
            page_response.output_text
        )

        try:
            returned_index = int(
                page_payload.get("image_index")
                or 0
            )
        except Exception:
            returned_index = -1

        if returned_index != image_index:
            raise ValueError(
                "Official result page extraction returned "
                "wrong image index: "
                f"{returned_index}/{image_index}"
            )

        if not safe_bool(
            page_payload.get("read_successfully")
        ):
            raise ValueError(
                "Official result page extraction could not "
                f"read image {image_index}"
            )

        page_reads.append(page_payload)

        print(
            "PAT HILL image read:",
            f"{image_index}/{len(image_urls)}",
            "| role:",
            page_payload.get("image_role"),
            "| picker:",
            page_payload.get("picker"),
            "| rows:",
            len(page_payload.get("rows") or []),
        )

    if len(page_reads) != len(image_urls):
        raise ValueError(
            "Official result page extraction did not "
            "read every image"
        )

    # --------------------------------------------------------
    # DETERMINISTIC ORPHAN-PAGE OWNERSHIP
    # --------------------------------------------------------
    # Some continuation/result pages do not visibly repeat the picker's
    # name. Do not ask vision to guess ownership in that case. The PAT HILL
    # standings text gives the exact official row total for each picker.
    # We may assign an unnamed page only when the row-count arithmetic makes
    # exactly one ownership possible: every already-named picker must already
    # equal its official total, exactly one picker must be short, and the
    # unnamed rows must equal that one deficit. Otherwise leave ownership
    # unresolved and let the existing fail-closed validation reject the run.
    # --------------------------------------------------------

    expected_records = parse_printed_standings(
        str(root_post.get("text") or ""),
        target_week,
    )

    expected_row_totals = {
        picker: (
            int(record.get("wins") or 0)
            + int(record.get("losses") or 0)
            + int(record.get("pushes") or 0)
        )
        for picker, record in expected_records.items()
        if picker in TRACKED_PICKERS
    }

    named_row_totals = {
        picker: 0
        for picker in TRACKED_PICKERS
    }
    orphan_pages = []

    for page_payload in page_reads:
        rows = page_payload.get("rows") or []
        picker = normalize_picker(
            page_payload.get("picker")
        )

        if picker in TRACKED_PICKERS:
            named_row_totals[picker] += len(rows)
        elif (
            str(page_payload.get("image_role") or "").upper()
            == "RESULT_CARD"
            and rows
        ):
            orphan_pages.append(page_payload)

    if orphan_pages and len(expected_row_totals) == len(TRACKED_PICKERS):
        orphan_row_total = sum(
            len(page.get("rows") or [])
            for page in orphan_pages
        )

        deficits = {
            picker: expected_row_totals[picker] - named_row_totals[picker]
            for picker in TRACKED_PICKERS
        }

        candidates = [
            picker
            for picker, deficit in deficits.items()
            if deficit == orphan_row_total and deficit > 0
        ]

        other_pickers_exact = (
            len(candidates) == 1
            and all(
                named_row_totals[picker] == expected_row_totals[picker]
                for picker in TRACKED_PICKERS
                if picker != candidates[0]
            )
        )

        if other_pickers_exact:
            inferred_picker = candidates[0]
            for page_payload in orphan_pages:
                page_payload["picker"] = inferred_picker
                page_payload["picker_assignment"] = (
                    "DETERMINISTIC_OFFICIAL_ROW_TOTAL"
                )

            print(
                "PAT HILL deterministic orphan assignment:",
                inferred_picker,
                "| orphan pages:",
                len(orphan_pages),
                "| orphan rows:",
                orphan_row_total,
                "| expected totals:",
                expected_row_totals,
            )
        else:
            print(
                "PAT HILL orphan pages unresolved:",
                len(orphan_pages),
                "| orphan rows:",
                orphan_row_total,
                "| named totals:",
                named_row_totals,
                "| expected totals:",
                expected_row_totals,
                "| deficits:",
                deficits,
            )

    page_transcripts = json.dumps(
        page_reads,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # --------------------------------------------------------
    # PASS 2 — RECONCILE ALL PAGE READS AGAINST ALL IMAGES
    # --------------------------------------------------------
    # The final model is not allowed to trust the transcripts blindly. It
    # receives every original image again and must verify/deduplicate the
    # page-local reads. If anything is genuinely missing or ambiguous it
    # must still return complete=false, preserving the existing fail-closed
    # behavior.
    # --------------------------------------------------------

    prompt = f"""
You are reconciling the OFFICIAL weekly results for the
Barstool Pick Em college football show.

These images come only from verified official @barstoolpickem PAT HILL
STANDINGS result-card material.

TARGET WEEK:
{target_week}

THREAD TEXT:
{thread_text}

NUMBER OF IMAGES:
{len(image_urls)}

FIRST-PASS PAGE TRANSCRIPTS:
{page_transcripts}

TASK
====

Re-read ALL original images and use the page transcripts as a second source of
visual bookkeeping. Produce the complete final result cards for:

- Rico Bosco
- Big Cat
- Stool Presidente / Dave Portnoy / El Pres

A GREEN CHECK means WIN.
A RED X means LOSS.
A push/tie symbol means PUSH.

CRITICAL RULES
==============

1. Extract EVERY individual wager shown on each picker's final card.

2. Cards may span multiple images. Combine continuation pages belonging to the
   same picker.

3. Do not duplicate a wager simply because the same row appears in overlapping
   screenshots or because it appears in both a page transcript and an image.

4. Preserve the actual wager line.

5. Preserve 1Q, 1H and team-total distinctions.

6. The section labeled "Adds:" contains legitimate additional picks. Return
   those with added_pick=true.

7. Do not invent any wager not visible on the cards.

8. Do not use historical tracker data.

9. Read the printed record on each complete picker card when shown.

10. Your extracted individual WIN/LOSS/PUSH results for each picker MUST exactly
    add up to that picker's printed record.

11. The page transcripts are aids, not authority. Resolve any disagreement by
    re-reading the original images. If a transcript contains
    picker_assignment=DETERMINISTIC_OFFICIAL_ROW_TOTAL, the program assigned
    that otherwise-unnamed RESULT_CARD page only because the official printed
    standings row totals made exactly one picker ownership mathematically
    possible. Treat that ownership as established bookkeeping, while still
    verifying every wager and result from the original image.

12. If any required picker card is genuinely missing, a required wager row is
    unreadable after re-reading the image, or result totals cannot be
    reconciled, set complete=false. Never guess merely to make totals work.

13. Inspect all {len(image_urls)} supplied original images. Return
    images_read={len(image_urls)} only if every image was actually inspected in
    this final pass.

OUTPUT JSON ONLY
================

{{
  "complete": true,
  "week": {target_week},
  "images_read": {len(image_urls)},
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
No markdown. No commentary. JSON only.
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
            }
        )

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )

    payload = parse_json_response(
        response.output_text
    )

    try:
        images_read = int(
            payload.get("images_read")
            or 0
        )
    except Exception:
        images_read = -1

    if images_read != len(image_urls):
        raise ValueError(
            "Official result extraction did not "
            "confirm every image was read: "
            f"{images_read}/{len(image_urls)}"
        )

    # --------------------------------------------------------
    # STRICT DETERMINISTIC FALLBACK FROM PAGE-LOCAL READS
    # --------------------------------------------------------
    # The final reconciliation model can be overly conservative and return
    # complete=false even when every page-local read is complete and the
    # evidence reconciles exactly to the official printed standings. In that
    # narrow case, construct the payload directly from the independently read
    # pages instead of asking the model to make a second completeness judgment.
    #
    # This is NOT a relaxed validation path. It is available only when:
    #   * every RESULT_CARD page has a uniquely established picker;
    #   * every row is readable and has a WIN/LOSS/PUSH result;
    #   * every row has a non-empty selection;
    #   * each picker's row count exactly equals the official W+L+P total; and
    #   * the row-level result counts exactly equal the official printed record.
    # validate_official_results() still runs afterward and independently checks
    # the same official records before any week can be replaced.
    # --------------------------------------------------------

    if not safe_bool(payload.get("complete")):
        fallback_picks = {
            picker: []
            for picker in TRACKED_PICKERS
        }
        fallback_safe = (
            len(expected_row_totals) == len(TRACKED_PICKERS)
        )
        fallback_reason = None

        for page_payload in page_reads:
            role = str(
                page_payload.get("image_role") or ""
            ).upper()
            rows = page_payload.get("rows") or []

            if role != "RESULT_CARD":
                continue

            picker = normalize_picker(
                page_payload.get("picker")
            )

            if picker not in TRACKED_PICKERS:
                fallback_safe = False
                fallback_reason = (
                    "unassigned RESULT_CARD page"
                )
                break

            for row in rows:
                if not safe_bool(row.get("readable")):
                    fallback_safe = False
                    fallback_reason = (
                        f"unreadable row for {picker}"
                    )
                    break

                selection = str(
                    row.get("selection") or ""
                ).strip()
                result = str(
                    row.get("result") or ""
                ).upper().strip()

                if not selection:
                    fallback_safe = False
                    fallback_reason = (
                        f"missing selection for {picker}"
                    )
                    break

                if result not in {
                    "WIN",
                    "LOSS",
                    "PUSH",
                }:
                    fallback_safe = False
                    fallback_reason = (
                        f"missing result for {picker}"
                    )
                    break

                fallback_picks[picker].append(
                    {
                        "selection": selection,
                        "matchup": row.get("matchup"),
                        "team": row.get("team"),
                        "opponent": row.get("opponent"),
                        "bet_type": row.get("bet_type") or "OTHER",
                        "side": row.get("side"),
                        "line": row.get("line"),
                        "result": result,
                        "added_pick": safe_bool(
                            row.get("added_pick")
                        ),
                    }
                )

            if not fallback_safe:
                break

        if fallback_safe:
            for picker in TRACKED_PICKERS:
                expected = expected_records.get(picker)
                picks = fallback_picks[picker]

                if expected is None:
                    fallback_safe = False
                    fallback_reason = (
                        f"missing printed record for {picker}"
                    )
                    break

                expected_total = (
                    int(expected.get("wins") or 0)
                    + int(expected.get("losses") or 0)
                    + int(expected.get("pushes") or 0)
                )

                actual_record = {
                    "wins": sum(
                        1 for pick in picks
                        if pick.get("result") == "WIN"
                    ),
                    "losses": sum(
                        1 for pick in picks
                        if pick.get("result") == "LOSS"
                    ),
                    "pushes": sum(
                        1 for pick in picks
                        if pick.get("result") == "PUSH"
                    ),
                }

                expected_record = {
                    "wins": int(expected.get("wins") or 0),
                    "losses": int(expected.get("losses") or 0),
                    "pushes": int(expected.get("pushes") or 0),
                }

                if len(picks) != expected_total:
                    fallback_safe = False
                    fallback_reason = (
                        f"row total mismatch for {picker}: "
                        f"{len(picks)}/{expected_total}"
                    )
                    break

                if actual_record != expected_record:
                    fallback_safe = False
                    fallback_reason = (
                        f"result total mismatch for {picker}: "
                        f"{actual_record}/{expected_record}"
                    )
                    break

        if fallback_safe:
            payload = {
                "complete": True,
                "week": int(target_week),
                "images_read": len(image_urls),
                "pickers": [
                    {
                        "picker": picker,
                        "printed_record": {
                            "wins": int(
                                expected_records[picker].get("wins")
                                or 0
                            ),
                            "losses": int(
                                expected_records[picker].get("losses")
                                or 0
                            ),
                            "pushes": int(
                                expected_records[picker].get("pushes")
                                or 0
                            ),
                        },
                        "picks": fallback_picks[picker],
                    }
                    for picker in TRACKED_PICKERS
                ],
            }
            print(
                "PAT HILL strict deterministic page fallback accepted:",
                " | ".join(
                    f"{picker}={len(fallback_picks[picker])}"
                    for picker in TRACKED_PICKERS
                ),
            )
        else:
            print(
                "PAT HILL deterministic page fallback rejected:",
                fallback_reason or "strict conditions not satisfied",
            )

    return payload


# ============================================================
# VALIDATE OFFICIAL RESULT CARD EXTRACTION
# ============================================================

def validate_official_results(
    payload,
    *,
    printed_standings,
    target_week,
):
    if not safe_bool(
        payload.get("complete")
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
        payload.get("pickers")
        or []
    )

    if not isinstance(
        picker_rows,
        list,
    ):
        raise ValueError(
            "Official pickers payload is not a list"
        )

    by_picker = {}

    for row in picker_rows:
        if not isinstance(
            row,
            dict,
        ):
            continue

        picker = normalize_picker_safe(
            row.get("picker")
        )

        if not picker:
            continue

        if picker in by_picker:
            raise ValueError(
                f"Duplicate official card for {picker}"
            )

        by_picker[picker] = row

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
        row = by_picker[picker]

        record = (
            row.get(
                "printed_record"
            )
            or {}
        )

        wins = int(
            record.get("wins")
            or 0
        )

        losses = int(
            record.get("losses")
            or 0
        )

        pushes = int(
            record.get("pushes")
            or 0
        )

        picks = (
            row.get("picks")
            or []
        )

        if not isinstance(
            picks,
            list,
        ):
            raise ValueError(
                f"{picker} official picks is not a list"
            )

        result_counts = {
            "WIN": 0,
            "LOSS": 0,
            "PUSH": 0,
        }

        normalized_picks = []

        for source_pick in picks:
            if not isinstance(
                source_pick,
                dict,
            ):
                raise ValueError(
                    f"{picker} official pick is not an object"
                )

            result = str(
                source_pick.get(
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
                source_pick.get(
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
                    dict(source_pick)
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
        # ROOT PRINTED STANDINGS VALIDATION
        # ----------------------------------------------------

        if picker in printed_standings:
            tweet_record = (
                printed_standings[
                    picker
                ]
            )

            expected_tuple = (
                tweet_record["wins"],
                tweet_record["losses"],
                tweet_record["pushes"],
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

        validated[picker] = {
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
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
        root_post.get("id")
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
            picker_data["picks"]
        ):
            result = str(
                source_pick["result"]
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
                    int(target_week),

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
                    (
                        root_post.get(
                            "conversation_id"
                        )
                        or root_id
                    ),

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
                        row["selection"],
                        result,
                    ]
                )
            )

            rows.append(row)

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
            pick_week(pick)
            == int(target_week)
            and normalize_picker_safe(
                pick.get("picker")
            )
            in TRACKED_PICKERS
        ):
            removed_count += 1
            continue

        kept.append(pick)

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
        len(official_rows),
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
                pick_week(pick)
                == int(target_week)
                and normalize_picker_safe(
                    pick.get("picker")
                )
                == picker
            )
        ]

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
                len(non_official),
            )

            healthy = False

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
                len(wrong_source),
            )

            healthy = False

        invalid_rows = [
            pick
            for pick in rows
            if (
                str(
                    pick.get("result")
                    or ""
                ).upper()
                not in {
                    "WIN",
                    "LOSS",
                    "PUSH",
                }
                or str(
                    pick.get("status")
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
                len(invalid_rows),
            )

            healthy = False

        wins = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
                or ""
            ).upper()
            == "WIN"
        )

        losses = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
                or ""
            ).upper()
            == "LOSS"
        )

        pushes = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
                or ""
            ).upper()
            == "PUSH"
        )

        expected_wins = int(
            expected.get("wins")
            or 0
        )

        expected_losses = int(
            expected.get("losses")
            or 0
        )

        expected_pushes = int(
            expected.get("pushes")
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
            len(rows),
            "/",
            expected_total,
        )

        if (
            wins != expected_wins
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

        if len(rows) != expected_total:
            print(
                "OFFICIAL VALIDATION FAILED:",
                picker,
                "| expected",
                expected_total,
                "official rows but found",
                len(rows),
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
            start_time=start_time,
            max_pages=
                STANDINGS_MAX_PAGES,
        )
    )

    root_candidates = [
        post
        for post in posts
        if is_pat_hill_text(
            post.get("text")
        )
    ]

    if not root_candidates:
        print(
            "No PAT HILL STANDINGS "
            "root post found yet."
        )

        return existing

    root_candidates = sorted(
        root_candidates,
        key=lambda post:
            post_numeric_sort(
                post.get("id")
            ),
        reverse=False,
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

    for root_post in root_candidates:
        root_id = str(
            root_post.get("id")
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
        # Exhaustive official PAT HILL source collection.
        #
        # Do not trust only the broad account-timeline page set.
        # Collect the conversation again through targeted recent
        # search plus an independent deeper timeline pass, merge
        # all official posts/media, and let the existing strict
        # result-card validation decide whether Week N is safe.
        # ----------------------------------------------------

        thread_posts, thread_media_map = (
            collect_pat_hill_thread(
                root_post=root_post,
                discovery_posts=posts,
                discovery_media_map=media_map,
                official_user_id=official_user_id,
                standings_start_time=start_time,
            )
        )

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

        printed_standings = (
            parse_printed_standings(
                root_post.get("text"),
                target_week=target_week,
            )
        )

        if len(
            printed_standings
        ) != 3:
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
            len(thread_posts),
        )

        print(
            "Reconciling Week:",
            target_week,
        )

        print(
            "Printed standings:",
            printed_standings,
        )

        # ----------------------------------------------------
        # Self-heal already-processed official threads.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Extract + validate all official result cards.
        # ----------------------------------------------------

        try:
            payload = (
                parse_official_result_cards(
                    root_post=root_post,
                    thread_posts=
                        thread_posts,
                    media_map=thread_media_map,
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
                "the next eligible pipeline run."
            )

            continue

        print()
        print(
            "OFFICIAL CARD VALIDATION PASSED"
        )

        for picker in [
            "Rico Bosco",
            "Big Cat",
            "Stool Presidente",
        ]:
            data = validated[picker]

            print(
                picker,
                "| Official:",
                f"{data['wins']}-"
                f"{data['losses']}-"
                f"{data['pushes']}",
                "| picks:",
                len(data["picks"]),
            )

        official_rows = (
            build_official_week_rows(
                validated=validated,
                target_week=target_week,
                root_post=root_post,
            )
        )

        expected_total = sum(
            (
                data["wins"]
                + data["losses"]
                + data["pushes"]
            )
            for data in (
                validated.values()
            )
        )

        if len(
            official_rows
        ) != expected_total:
            print(
                "OFFICIAL RECONCILIATION ABORTED:"
            )

            print(
                "Built",
                len(official_rows),
                "rows but expected",
                expected_total,
            )

            continue

        existing = (
            replace_week_with_official(
                existing,
                official_rows=
                    official_rows,
                target_week=
                    target_week,
            )
        )

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

        processed.add(
            root_id
        )

        state[
            OFFICIAL_RESULT_STATE_KEY
        ] = sorted(
            processed,
            key=post_numeric_sort,
        )[-100:]

        try:
            prior_official_week = int(
                state.get(
                    "last_official_reconciled_week"
                )
                or 0
            )
        except Exception:
            prior_official_week = 0

        state[
            "last_official_reconciled_week"
        ] = max(
            prior_official_week,
            int(target_week),
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
                normalize_picker_safe(
                    pick.get("picker")
                )
                == picker
                and pick_week(pick)
                == int(week)
            )
        ]

        wins = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
                or ""
            ).upper()
            == "WIN"
        )

        losses = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
                or ""
            ).upper()
            == "LOSS"
        )

        pushes = sum(
            1
            for pick in rows
            if str(
                pick.get("result")
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

    # --------------------------------------------------------
    # 1. Seed processed IDs without losing old state.
    # --------------------------------------------------------

    initialize_processed_ids(
        existing,
        state,
    )

    # --------------------------------------------------------
    # 2. Resolve official account.
    # --------------------------------------------------------

    official_user_id = (
        resolve_user_id()
    )

    print(
        "Official account:",
        USERNAME,
        "| user ID:",
        official_user_id,
    )

    # --------------------------------------------------------
    # 3. Retry previously failed posts.
    # --------------------------------------------------------

    retry_posts, retry_media = (
        fetch_retry_posts(
            state,
            official_user_id,
        )
    )

    # --------------------------------------------------------
    # 4. Fetch only new timeline posts.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 5. Permanent recent-source safety backfill.
    #
    # The since_id cursor is an optimization, never the sole source of
    # truth. Every run also scans a bounded recent official-account
    # window. Already validated posts are skipped by processed_post_ids,
    # so this is idempotent. If a prior code/state bug advanced the cursor
    # too far, the source post is still rediscovered here.
    #
    # This also supplies the original post + ALL media expansions when a
    # validation-generation upgrade needs to rebuild an existing source
    # post. No manual post IDs, picker-specific repairs, or week-specific
    # exceptions are required.
    # --------------------------------------------------------

    safety_posts, safety_media = (
        fetch_user_posts(
            official_user_id,
            start_time=(
                datetime.now(timezone.utc)
                - timedelta(days=RECENT_SAFETY_BACKFILL_DAYS)
            ),
            max_pages=RECENT_SAFETY_BACKFILL_PAGES,
        )
    )

    print(
        "Recent safety-backfill posts:",
        len(safety_posts),
    )

    # --------------------------------------------------------
    # 6. Combine retries + cursor posts + safety backfill.
    # --------------------------------------------------------

    post_map = {}

    for post in (
        retry_posts
        + timeline_posts
        + safety_posts
    ):
        post_id = str(
            post.get("id")
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

    combined_media.update(
        safety_media
    )

    # --------------------------------------------------------
    # 7. Transactional normal-pick ingestion.
    # --------------------------------------------------------

    (
        existing,
        ingest_stats,
    ) = process_normal_posts(
        existing,
        combined_posts,
        combined_media,
        official_user_id,
        state,
    )

    # --------------------------------------------------------
    # 8. Advance timeline cursor.
    #
    # This is safe even when a candidate failed validation,
    # because failed_post_ids is an independent durable retry
    # queue and fetch_retry_posts retrieves those exact posts.
    # --------------------------------------------------------

    timeline_ids = [
        int(
            post.get("id")
        )
        for post in timeline_posts
        if (
            post.get("id")
            and str(
                post.get("id")
            ).isdigit()
        )
    ]

    if timeline_ids:
        newest_id = str(
            max(timeline_ids)
        )

        old_id = state.get(
            "last_x_post_id"
        )

        if (
            not old_id
            or int(newest_id)
            > int(old_id)
        ):
            state[
                "last_x_post_id"
            ] = newest_id

    # --------------------------------------------------------
    # 9. Official result reconciliation + missed-week self-healing.
    # --------------------------------------------------------

    if should_scan_standings(
        state
    ):
        existing = (
            reconcile_pat_hill_standings(
                existing,
                state,
                official_user_id,
            )
        )

    else:
        print(
            "PAT HILL reconciliation is current — "
            "no standings scan needed on this run."
        )

    # --------------------------------------------------------
    # 10. Permanent canonical dedupe.
    # --------------------------------------------------------

    existing = dedupe_picks(
        existing
    )

    # --------------------------------------------------------
    # 11. Permanent deterministic wager-ID integrity.
    #
    # Existing IDs are preserved. Any valid historical/recovered
    # wager that lacks an ID receives the same deterministic ID it
    # would receive if it were ingested today.
    #
    # ID collisions across different canonical wagers fail closed
    # before data is saved.
    # --------------------------------------------------------

    existing = ensure_pick_ids(
        existing
    )

    # --------------------------------------------------------
    # 12. Save atomically at end of successful ingest run.
    # --------------------------------------------------------

    state[
        "updated_at"
    ] = now_iso()

    state[
        "ingest_validation_version"
    ] = CURRENT_INGEST_VALIDATION_VERSION

    save_json(
        PICKS_FILE,
        existing,
    )

    save_json(
        STATE_FILE,
        state,
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print(
        "========== INGEST SUMMARY =========="
    )

    print(
        "New official timeline posts:",
        len(timeline_posts),
    )

    print(
        "Retry posts:",
        len(retry_posts),
    )

    print(
        "Candidate new-pick posts:",
        ingest_stats[
            "candidate_count"
        ],
    )

    print(
        "Validated candidate posts:",
        ingest_stats[
            "validated_post_count"
        ],
    )

    print(
        "Validated non-pick candidates:",
        ingest_stats[
            "non_pick_candidate_count"
        ],
    )

    print(
        "Existing wagers recognized:",
        ingest_stats[
            "duplicate_count"
        ],
    )

    print(
        "New wagers added:",
        ingest_stats[
            "new_pick_count"
        ],
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
        len(existing),
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
        "Ingest validation version:",
        state.get(
            "ingest_validation_version"
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
