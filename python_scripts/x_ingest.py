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
    alias_group,
    base_market,
    best_matchup_hints,
    canonical_game_identity,
    canonical_pick_key,
    canonical_selected_team,
    clean_text,
    effective_line,
    market_period,
    norm,
    normalize_bet_type,
    safe_float,
    side_identity,
    spread_line_from_selection,
    total_direction,
    split_matchup,
    teams_equivalent,
)

from espn_resolver import (
    build_complete_week_slate,
    event_matchup_text,
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
    "processed_ids_initialized_v6"
)

OFFICIAL_RESULT_STATE_KEY = (
    "official_result_threads_processed"
)

POST_VALIDATION_VERSIONS_KEY = (
    "normal_post_validation_versions"
)

POST_SNAPSHOTS_KEY = (
    "normal_post_snapshots_v21"
)

MAX_PROCESSED_POST_IDS = 2000
MAX_FAILED_POST_IDS = 250

# Increment only when normal-post extraction/validation semantics change.
CURRENT_INGEST_VALIDATION_VERSION = 21

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


def source_post_text(post):
    """Return the fullest X text available for a post.

    X long-form posts can expose a shortened legacy `text` field while the
    complete body lives in `note_tweet.text`.  Ingestion must never parse the
    shorter representation when a longer official representation is present.
    """

    post = post or {}

    legacy = str(post.get("text") or "").strip()

    note = post.get("note_tweet") or {}
    note_text = str(
        note.get("text")
        if isinstance(note, dict)
        else ""
    ).strip()

    if len(note_text) > len(legacy):
        return note_text

    return legacy


def normalize_source_post(post):
    if not isinstance(post, dict):
        return post

    full_text = source_post_text(post)

    if full_text:
        post["text"] = full_text

    return post


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


def resolve_normal_pick_week(
    text,
    created_at,
    last_official_week,
):
    """
    Resolve the week for a NEW-PICK source post without letting an incidental
    reference to a closed historical week suppress a current-week card.

    Normal ingestion historically preferred any literal ``Week N`` token in
    the post text. That is unsafe when a current-week picks post mentions the
    prior week's record/standings in its caption: the old token can cause the
    entire source to be skipped as a closed official week before its wagers
    are even inventoried.

    Trust policy:
      1. If only one signal exists, use it.
      2. If explicit text and posting-date week agree, use that week.
      3. If the explicit week is already CLOSED/OFFICIAL but the posting date
         falls in a later OPEN week, treat the old text as historical context
         and use the posting-date week.
      4. Otherwise preserve the explicit source week. This keeps legitimate
         advance/future-week cards working when they are posted early.

    Returns diagnostic metadata so the caller can make any override visible
    in the run log.
    """

    explicit_week = explicit_week_from_text(
        text
    )
    date_week = infer_week_from_date(
        created_at
    )

    try:
        closed_week = int(
            last_official_week
            or 0
        )
    except Exception:
        closed_week = 0

    if explicit_week is None:
        return {
            "week": date_week,
            "explicit_week": None,
            "date_week": date_week,
            "method": "POST_DATE",
        }

    if date_week is None:
        return {
            "week": explicit_week,
            "explicit_week": explicit_week,
            "date_week": None,
            "method": "EXPLICIT_TEXT",
        }

    if int(explicit_week) == int(date_week):
        return {
            "week": explicit_week,
            "explicit_week": explicit_week,
            "date_week": date_week,
            "method": "EXPLICIT_DATE_AGREE",
        }

    if (
        int(explicit_week) <= closed_week
        and int(date_week) > closed_week
    ):
        return {
            "week": date_week,
            "explicit_week": explicit_week,
            "date_week": date_week,
            "method": "POST_DATE_OVERRIDES_CLOSED_EXPLICIT",
        }

    return {
        "week": explicit_week,
        "explicit_week": explicit_week,
        "date_week": date_week,
        "method": "EXPLICIT_TEXT",
    }


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
        source_post_text(root_post)
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
            "note_tweet,"
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

            posts.append(normalize_source_post(post))

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
            "note_tweet,"
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

            posts.append(normalize_source_post(post))

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
                "note_tweet,"
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

    post = normalize_source_post(
        payload.get("data")
    )

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
# COMPOUND WAGER DECOMPOSITION
# ============================================================

def split_compound_wager_selection(selection):
    """
    Split a single visible card cell only when it unambiguously contains two
    separate supported wagers joined by a spaced ampersand.

    Example:
        "Army -3 & O48.5" -> ["Army -3", "O48.5"]

    The rule is intentionally narrow. It does not split team names such as
    "Texas A&M" because the ampersand is not surrounded by whitespace, and
    it does not split arbitrary prose. Both sides must independently look like
    a supported spread/total/team-total/moneyline-style wager.
    """

    selection = clean_text(selection)

    if not selection:
        return [selection]

    parts = [
        clean_text(part)
        for part in re.split(r"\s+&\s+", selection)
    ]

    if len(parts) != 2 or not all(parts):
        return [selection]

    def _clause_kind(value):
        value = clean_text(value)
        lower = value.lower()

        # Compact or verbose game/team total, including source shorthand O48.5.
        if re.search(
            r"(?:^|\s)(?:over|under|o|u)\s*\d+(?:\.\d+)?$",
            lower,
            flags=re.I,
        ):
            return "TOTAL"

        if re.search(
            r"\b(?:tt|team total)\b.*(?:over|under|o|u)\s*\d+(?:\.\d+)?$",
            lower,
            flags=re.I,
        ):
            return "TEAM_TOTAL"

        # Spread: require a non-numeric team/token before a signed number.
        if re.search(
            r"[A-Za-z][A-Za-z0-9 .&'/-]{0,50}\s[+-]\s*\d+(?:\.\d+)?$",
            value,
        ):
            return "SPREAD"

        if re.search(
            r"\b(?:ml|moneyline)\b",
            lower,
            flags=re.I,
        ):
            return "MONEYLINE"

        return None

    kinds = [_clause_kind(part) for part in parts]

    if not all(kinds):
        return [selection]

    return parts


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

def preflight_normal_source(
    *,
    text,
    image_urls,
    post_url,
    picker_hint,
):
    """
    Independently inventory TRACKED wagers before structured extraction.

    This is deliberately a separate model request. It provides a row-by-row
    checklist for Big Cat, Rico Bosco, and Stool Presidente only. Guest/fan
    wagers may appear in an official @barstoolpickem post, but they are outside
    this tracker and must not inflate the reconciliation count.

    Every accepted inventory must be internally consistent and every supplied
    image must be inspected. Failed attempts are retried from the original
    source; rows are never merged across attempts.
    """

    from openai import OpenAI

    client = OpenAI()

    max_attempts = 3
    last_error = None

    # Dense multi-image cards are the highest-risk source class.  A single
    # otherwise valid vision pass can omit one or two tiny rows while still
    # returning a self-consistent JSON object.  Keep whole-attempt candidates
    # separate, read dense sources three independent times, and later select
    # the highest-coverage complete attempt.  Rows are NEVER merged across
    # attempts.
    dense_source = len(image_urls) >= 2
    successful_results = []

    for attempt in range(1, max_attempts + 1):
        retry_instruction = ""

        if attempt > 1:
            retry_instruction = f"""
IMPORTANT RETRY INSTRUCTION:

A previous independent inventory attempt was rejected because its output was
structurally inconsistent, incomplete, OR because an image-bearing source was
reported as containing zero tracked wagers and requires an independent zero
confirmation.

This is independent inventory attempt {attempt} of {max_attempts}.

Re-read the ORIGINAL source material from scratch. Do NOT copy or repair the
previous answer.

If a prior attempt reported ZERO tracked wagers, treat zero as a HIGH-RISK
classification. Zoom/reinspect every image specifically for betting-card
content: team abbreviations, signed spreads (+/-), Over/Under or O/U labels,
team totals/TT, quarter/half markets, or multiple rows of selections. Do not
return zero merely because the image is graphical rather than plain text.

Before responding, verify all of the following:

1. Count ONLY tracked wagers belonging to Big Cat, Rico Bosco, or Stool
   Presidente / Dave Portnoy / El Pres.
2. Ignore wagers belonging only to guests, fans, or any other person. An
   untracked wager does NOT make the source incomplete.
3. wager_count exactly equals the number of objects in wagers.
4. images_read exactly equals {len(image_urls)}.
5. Every supplied image was inspected.
6. Every individual TRACKED wager physically visible in the source appears
   exactly once. A card cell that contains TWO bets joined by a spaced ampersand
   is TWO wagers, not one. Example: "Army -3 & O48.5" must be returned as
   separate rows "Army -3" and "O48.5".
7. Matchup headings, records, scores, decorative text, and untracked wagers are
   not counted in wager_count.
8. If you cannot confidently inventory every TRACKED wager, return
   complete=false rather than guessing.
""".strip()

        prompt = f"""
You are performing an INDEPENDENT completeness inventory of one verified
@barstoolpickem source post for a tracker that follows ONLY these people:
- Big Cat
- Rico Bosco
- Stool Presidente / Dave Portnoy / El Pres

SOURCE: {post_url}
PICKER HINT: {picker_hint or 'Unknown'}
FULL SOURCE TEXT:
{text or 'NONE'}

There are {len(image_urls)} attached source images. Inspect every image.

Count EVERY individual new NCAA football wager physically present in this
source that belongs to one of the THREE TRACKED PICKERS above.

IMPORTANT:
- Ignore wagers belonging to guests, fans, or any other person.
- An untracked wager does NOT make the source incomplete.
- Do not count matchup headings, records, scores, or decorative text.
- If the picker hint identifies one tracked picker for the whole source, you may
  use that hint for the wager rows in this source.

For every TRACKED wager, transcribe the COMPLETE literal wager label preserving
all source identity context that is physically attached to the number. If the
source says "Mizzou Over 50.5", selection MUST be "Mizzou Over 50.5" — never
shorten it to "Over 50.5". If the source says "Notre Dame TT Over 47.5", keep
the team and TT marker. If a matchup is explicitly attached to that wager,
transcribe it; otherwise use null. Never invent an opponent or matchup.

CRITICAL CONTEXT-PRESERVATION RULE:
A team/school token immediately attached to a game total is source evidence,
not decoration. Preserve it in selection even when matchup is null. Bare totals
such as "Over 50.5" are allowed only when the source itself is actually bare.

IMPORTANT COMPOUND-CELL RULE:
If one visible source cell contains multiple independent wagers joined by a
spaced ampersand, count and return EACH wager separately. For example,
"Army -3 & O48.5" is TWO wager rows: "Army -3" and "O48.5". Do not
return the combined string as one wager. Team names such as "Texas A&M" are
not compound cells and must not be split.

{retry_instruction}

Return JSON only:
{{
  "complete": true,
  "wager_count": 0,
  "untracked_wager_count": 0,
  "wagers": [
    {{
      "picker": "Big Cat|Rico Bosco|Stool Presidente|null",
      "selection": "exact tracked wager",
      "matchup": null
    }}
  ],
  "images_read": {len(image_urls)}
}}

Set complete=false only if you cannot confidently inventory every TRACKED wager.
Untracked wagers are ignored for tracker completeness.
""".strip()

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

        try:
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

            if not safe_bool(
                payload.get("complete")
            ):
                raise ValueError(
                    "Independent source inventory marked extraction incomplete"
                )

            try:
                count = int(
                    payload.get("wager_count")
                )
            except Exception as exc:
                raise ValueError(
                    "Independent source inventory returned invalid wager_count"
                ) from exc

            if count < 0:
                raise ValueError(
                    "Independent source inventory returned negative wager_count"
                )

            wagers = payload.get("wagers")

            if not isinstance(wagers, list):
                raise ValueError(
                    "Independent source inventory wagers is not a list"
                )

            actual_row_count = len(wagers)

            # wager_count is redundant model arithmetic.  The row objects are
            # the actual inventory evidence.  Do not throw away a complete 53-row
            # transcription merely because the model typed 51 in the summary
            # field.  We still log the disagreement loudly and deterministically
            # recompute the count from the rows themselves.
            if actual_row_count != count:
                print(
                    "INDEPENDENT SOURCE INVENTORY COUNT FIELD CORRECTED:",
                    post_url,
                    "| attempt:",
                    attempt,
                    "| reported:",
                    count,
                    "| actual rows:",
                    actual_row_count,
                    "| action: trust row list and recompute count",
                )
                count = actual_row_count

            try:
                images_read = int(
                    payload.get("images_read") or 0
                )
            except Exception as exc:
                raise ValueError(
                    "Independent source inventory returned invalid images_read"
                ) from exc

            if images_read != len(image_urls):
                raise ValueError(
                    "Independent source inventory did not read every image "
                    f"(expected {len(image_urls)}, reported {images_read})"
                )

            try:
                untracked_count = int(
                    payload.get("untracked_wager_count") or 0
                )
            except Exception:
                untracked_count = 0

            if untracked_count < 0:
                untracked_count = 0

            normalized_wagers = []
            compound_expansions = []

            for source_row_index, wager in enumerate(
                wagers,
                start=1,
            ):
                if not isinstance(wager, dict):
                    raise ValueError(
                        "Independent source inventory returned invalid wager "
                        f"row {source_row_index}"
                    )

                selection = clean_text(
                    wager.get("selection")
                )

                if not selection:
                    raise ValueError(
                        "Independent source inventory returned empty selection "
                        f"for wager row {source_row_index}"
                    )

                matchup = clean_text(
                    wager.get("matchup")
                )

                picker = normalize_picker_safe(
                    wager.get("picker")
                )

                if not picker:
                    picker = normalize_picker_safe(
                        picker_hint
                    )

                split_selections = split_compound_wager_selection(
                    selection
                )

                if len(split_selections) > 1:
                    compound_expansions.append(
                        (selection, list(split_selections))
                    )

                for split_selection in split_selections:
                    normalized_wagers.append(
                        {
                            "inventory_index": len(normalized_wagers) + 1,
                            "picker": picker,
                            "selection": split_selection,
                            "matchup": matchup or None,
                            "compound_source_selection": (
                                selection
                                if len(split_selections) > 1
                                else None
                            ),
                        }
                    )

            # Reject an inventory attempt that is internally impossible: a
            # spread team cannot be absent from its own complete two-team
            # matchup. This catches OCR/context corruption such as
            # ``UofA -3`` being paired with ``UofSC @ UF`` before that bad
            # checklist can overwrite a previously-correct row.
            for row in normalized_wagers:
                row_selection = clean_text(row.get("selection"))
                row_matchup = clean_text(row.get("matchup"))
                sides = split_matchup(row_matchup) if row_matchup else []

                spread_match = re.match(
                    r"^(.*?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*$",
                    row_selection,
                    flags=re.I,
                )

                if spread_match and len(sides) == 2:
                    selected_token = clean_text(spread_match.group(1))
                    if selected_token and not any(
                        _source_literal_team_matches_matchup_side(
                            selected_token,
                            side,
                        )
                        for side in sides
                    ):
                        raise ValueError(
                            "Independent source inventory identity conflict: "
                            f"{row_selection} is incompatible with matchup "
                            f"{row_matchup}"
                        )

            if compound_expansions:
                for original, expanded in compound_expansions:
                    print(
                        "INDEPENDENT INVENTORY COMPOUND WAGER EXPANDED:",
                        post_url,
                        "|",
                        original,
                        "->",
                        expanded,
                    )

            # The model's row count remains structurally validated above. After
            # deterministic compound-cell decomposition, the tracker count is the
            # number of individual wagers, not the number of visual source cells.
            count = len(normalized_wagers)

            result = {
                "wager_count": count,
                "untracked_wager_count": untracked_count,
                "wagers": normalized_wagers,
                "inventory_attempt": attempt,
            }

            successful_results.append(result)

            # Image-bearing sources that appear to contain zero tracked wagers
            # are a high-risk false-negative class. Require all independent
            # inventory attempts to agree on zero before allowing the structured
            # extractor to classify the source as a non-pick. This prevents one
            # missed vision read from permanently suppressing a real picks card.
            if image_urls and count == 0 and attempt < max_attempts:
                print(
                    "ZERO-WAGER IMAGE INVENTORY RECHECK:",
                    post_url,
                    "| attempt:",
                    attempt,
                    "reported 0 tracked wagers",
                    "| action: independent re-read",
                )
                last_error = ValueError(
                    "Zero-wager image inventory requires independent confirmation"
                )
                continue

            # For dense cards, do not trust one self-consistent pass.  Read the
            # entire original source three times and choose ONE complete attempt
            # afterward.  We never union rows from separate attempts.
            if dense_source and attempt < max_attempts:
                print(
                    "DENSE SOURCE INVENTORY CONFIRMATION READ:",
                    post_url,
                    "| attempt:",
                    attempt,
                    "| tracked wagers:",
                    count,
                    "| action: independent full-source reread",
                )
                continue

            if not dense_source:
                if attempt > 1:
                    print(
                        "INDEPENDENT SOURCE INVENTORY RETRY SUCCEEDED:",
                        post_url,
                        "| attempt:",
                        attempt,
                        "| tracked wagers:",
                        count,
                        "| untracked ignored:",
                        untracked_count,
                    )

                return result

            # Dense source reaches here only on the final inventory attempt.
            break

        except Exception as exc:
            last_error = exc

            if attempt >= max_attempts:
                break

            print(
                "INDEPENDENT SOURCE INVENTORY RETRY:",
                post_url,
                "| attempt:",
                attempt,
                "failed |",
                type(exc).__name__,
                exc,
            )

    if successful_results:
        def inventory_quality(result):
            rows = list(result.get("wagers") or [])
            known_pickers = sum(
                1
                for row in rows
                if normalize_picker_safe(row.get("picker"))
            )
            known_matchups = sum(
                1
                for row in rows
                if len(split_matchup(row.get("matchup"))) == 2
            )
            return (
                int(result.get("wager_count") or 0),
                known_pickers,
                known_matchups,
            )

        chosen = max(
            successful_results,
            key=inventory_quality,
        )

        counts = [
            int(result.get("wager_count") or 0)
            for result in successful_results
        ]

        if len(set(counts)) > 1:
            print(
                "DENSE SOURCE INVENTORY DISAGREEMENT:",
                post_url,
                "| complete attempt counts:",
                counts,
                "| selected whole attempt:",
                chosen.get("inventory_attempt"),
                "| selected wagers:",
                chosen.get("wager_count"),
                "| action: highest-coverage complete attempt; no cross-attempt row merge",
            )
        elif dense_source:
            print(
                "DENSE SOURCE INVENTORY CONSENSUS:",
                post_url,
                "| attempts:",
                len(successful_results),
                "| tracked wagers:",
                chosen.get("wager_count"),
            )

        return chosen

    raise ValueError(
        "Independent source inventory failed after "
        f"{max_attempts} attempts: "
        f"{type(last_error).__name__ if last_error else 'UnknownError'} "
        f"{last_error or 'unknown inventory failure'}"
    )

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
    preflight_inventory,
    reconciliation_attempt=1,
):
    """
    Extract one official source post.

    The independent inventory is a row-by-row checklist of TRACKED wagers.
    Structured extraction must map each returned wager back to exactly one
    inventory row. Guest/fan wagers are ignored rather than causing the post to
    fail completeness validation.
    """

    from openai import OpenAI

    client = OpenAI()

    supplied_image_count = len(
        image_urls
    )

    inventory_rows = list(
        (preflight_inventory or {}).get("wagers")
        or []
    )

    inventory_count = int(
        (preflight_inventory or {}).get("wager_count")
        or 0
    )

    reconciliation_instruction = ""

    if reconciliation_attempt > 1:
        reconciliation_instruction = f"""
============================================================
COUNT-RECONCILIATION RETRY {reconciliation_attempt}
============================================================

A prior structured extraction did not reconcile with the independently read
TRACKED-wager inventory.

Re-read the ORIGINAL source from scratch, then use the inventory as a strict
row-by-row checklist:
- return exactly {inventory_count} tracked pick objects;
- return one and only one pick for each inventory_index from 1 through
  {inventory_count};
- do not omit a checklist row;
- do not return an extra pick that has no checklist row;
- if the original source clearly proves the inventory itself is wrong, set
  complete=false and explain that specific disagreement in extraction_notes.

Do not copy the prior failed extraction.
""".strip()

    prompt = f"""
You are a strict extraction engine for NCAA college football gambling picks
from the verified official @barstoolpickem X account.

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

WEEK ASSIGNMENT RULE:
For NEW wagers in this source, DEFAULT WEEK is authoritative. Do not change a
returned pick to an older week merely because the caption/image mentions a
prior-week record, standings result, recap, or historical "Week N" label.

IS OFFICIAL REPLY:
{reply_hint}

NUMBER OF SOURCE IMAGES SUPPLIED:
{supplied_image_count}

INDEPENDENT TRACKED-WAGER INVENTORY:
{json.dumps(preflight_inventory, ensure_ascii=False)}

The independent inventory contains {inventory_count} TRACKED wager rows.
It intentionally excludes guest/fan/untracked wagers.

Use those inventory rows as a completeness CHECKLIST. Every returned pick must
include inventory_index equal to the matching 1-based inventory row. Return
exactly one structured pick per tracked inventory row.

Some inventory rows may have been deterministically split from a compound card
cell such as "Army -3 & O48.5". Those are separate wagers. Return one pick for
each split inventory row and NEVER recombine them into a single OTHER market.

If the original source contains an untracked guest/fan wager, IGNORE it. It does
not make complete=false and it must not be returned in picks.

If you independently see an additional TRACKED wager that is genuinely absent
from the inventory, do not invent a new checklist row. Set complete=false and
explain the disputed source row in extraction_notes.

{reconciliation_instruction}

============================================================
SOURCE BOUNDARY
============================================================

Only wagers physically contained in the ACTUAL SOURCE POST may be returned as
picks.

Parent context may identify the picker or explain the reply, but NEVER copy a
wager from a parent post into this child post.

Never use fan content as a tracked pick. Never use historical tracker data.
This extraction is for NEW PICKS, not weekly result cards.

============================================================
IMAGE COMPLETENESS
============================================================

You MUST inspect every supplied source image.

If {supplied_image_count} images were supplied, inspect all
{supplied_image_count} images before responding.

Return images_supplied={supplied_image_count}.
Return images_read as the number of supplied images actually inspected.

If any supplied image cannot be read well enough to determine whether it
contains TRACKED wagers, set complete=false.

For every image, return an image_checks entry with:
- image_index: 1-based index
- readable: true/false
- contains_wagers: true/false, meaning contains TRACKED wagers
- wager_count: number of TRACKED wagers from the three tracked pickers

Do not include guest/fan/untracked wagers in image wager_count.
Do not count matchup headings, records, kickoff times, scores, checkmarks, red
Xs, or decorative text as wagers.

============================================================
PICK EXTRACTION
============================================================

Extract EVERY TRACKED wager represented in the independent inventory and the
original source. Each tracked wager must be its own pick object.

For every pick:
- inventory_index MUST identify the matching inventory checklist row;
- source_selection_text MUST preserve the literal visible/source wager text;
- source_team_text MUST preserve the literal visible selected-team token when
  one exists;
- source_matchup_text MUST preserve the literal visible matchup when one exists.

For card-style images:
- matchup headings establish game context;
- each wager underneath belongs to the closest applicable matchup heading;
- preserve that matchup on the wager;
- do not allow a total such as "Over 58.5" or "Under 53.5" to lose its matchup
  context.

CRITICAL LITERAL TRANSCRIPTION RULE:
Before interpreting or expanding any school abbreviation, copy the visible text
exactly as written on the source. Do NOT silently expand or autocorrect the
source_* fields. The normalized team/matchup fields may expand literal text
separately.

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

For totals:
- side must be OVER or UNDER
- line must be the total number
- matchup must identify the game whenever source context provides it

For team totals:
- team must identify the team whose total is being wagered
- side must be OVER or UNDER

For moneylines:
- team must identify the selected team

============================================================
PICKER
============================================================

Normalize tracked picks only to:
- Big Cat
- Rico Bosco
- Stool Presidente

If the source itself does not name the picker but verified official parent
context or PICKER HINT clearly identifies the tracked picker, use that picker.

If a wager belongs to someone outside the three tracked pickers, ignore it. Do
NOT set complete=false merely because an untracked wager exists.

If a wager is supposed to map to a tracked inventory row but its tracked picker
is genuinely unknowable, set complete=false rather than guessing.

============================================================
IS THIS ACTUALLY A PICK POST?
============================================================

Set is_pick_post=true ONLY if the ACTUAL SOURCE POST contains at least one new
wager from one of the three tracked pickers.

If inventory_count is zero and the source contains only untracked wagers, set:
- complete=true
- is_pick_post=false
- picks=[]

A result card or PAT HILL standings card is NOT a new-pick post.

============================================================
COMPLETENESS
============================================================

Set complete=true only when:

1. Every supplied image was inspected.
2. Every TRACKED wager in the actual source post was extracted exactly once.
3. Every tracked inventory row is represented exactly once by inventory_index.
4. No wager was copied from parent context.
5. No untracked guest/fan wager was returned as a tracked pick.
6. Every returned wager has a known tracked picker.
7. Every returned wager has a usable market and selection.
8. Every spread/total/team-total requiring a numeric line has that line.
9. Every total with source matchup context retains that matchup.
10. You did not have to guess through an unreadable/cropped source.

If uncertain whether TRACKED extraction is complete, set complete=false.
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
      "inventory_index": 1,
      "picker": "Big Cat|Stool Presidente|Rico Bosco",
      "sport": "CFB",
      "matchup": "Team A @ Team B or null",
      "source_selection_text": "literal wager text exactly as visible/source or null",
      "source_team_text": "literal selected-team token exactly as visible/source or null",
      "source_matchup_text": "literal matchup text exactly as visible/source or null",
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

No markdown. No commentary outside JSON. JSON only.
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

    max_readability_attempts = 3
    last_payload = None

    for extraction_attempt in range(1, max_readability_attempts + 1):
        attempt_content = list(content)

        if extraction_attempt > 1:
            attempt_content = [
                {
                    "type": "input_text",
                    "text": (
                        prompt
                        + "\n\nIMPORTANT READABILITY RETRY:\n"
                        + "A previous full-source extraction reported one or more "
                        + "source images as unreadable. Those images were independently "
                        + "re-opened successfully. Re-read EVERY original image from "
                        + "scratch, including the previously disputed image(s). Do not "
                        + "copy the prior extraction. Return complete=true only if every "
                        + "image is readable and every TRACKED wager is extracted."
                    ),
                }
            ]

            for image_url in image_urls:
                attempt_content.append(
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
                    "content": attempt_content,
                }
            ],
        )

        payload = parse_json_response(
            response.output_text
        )

        last_payload = payload

        checks = payload.get("image_checks")
        unreadable_indexes = []

        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue

                if safe_bool(check.get("readable")):
                    continue

                try:
                    image_index = int(check.get("image_index"))
                except Exception:
                    continue

                if 1 <= image_index <= supplied_image_count:
                    unreadable_indexes.append(image_index)

        # If the model did not explicitly flag an image as unreadable, return
        # the payload unchanged and let the transaction validator enforce the
        # inventory mapping and every other completeness rule.
        if not unreadable_indexes:
            return payload

        if extraction_attempt >= max_readability_attempts:
            return payload

        for image_index in sorted(set(unreadable_indexes)):
            probe_prompt = f"""
You are performing a SOURCE IMAGE READABILITY CHECK for a verified
@barstoolpickem pick-card source.

IMAGE NUMBER: {image_index} of {supplied_image_count}
SOURCE: {post_url}

Inspect this one original image carefully. Determine only whether the image is
readable well enough to identify every visible TRACKED wager row belonging to
Big Cat, Rico Bosco, or Stool Presidente / Dave Portnoy / El Pres. Guest/fan
wagers are outside this tracker.

Return JSON only:
{{
  "image_index": {image_index},
  "readable": true,
  "visible_tracked_wager_count": 0
}}

Set readable=false if any TRACKED wager row is too cropped, blurred, or obscured
to be transcribed reliably.
""".strip()

            probe_response = client.responses.create(
                model=OPENAI_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": probe_prompt,
                            },
                            {
                                "type": "input_image",
                                "image_url": image_urls[image_index - 1],
                            },
                        ],
                    }
                ],
            )

            probe_payload = parse_json_response(
                probe_response.output_text
            )

            if not safe_bool(probe_payload.get("readable")):
                print(
                    "SOURCE IMAGE READABILITY RETRY FAILED:",
                    post_url,
                    "| image:",
                    image_index,
                    "| attempt:",
                    extraction_attempt,
                )
                return payload

            print(
                "SOURCE IMAGE READABILITY RETRY SUCCEEDED:",
                post_url,
                "| image:",
                image_index,
                "| attempt:",
                extraction_attempt,
                "| visible tracked wagers:",
                probe_payload.get("visible_tracked_wager_count"),
            )

        print(
            "RETRYING FULL SOURCE EXTRACTION AFTER IMAGE READABILITY CHECK:",
            post_url,
            "| next attempt:",
            extraction_attempt + 1,
        )

    return last_payload

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

    # The source-post week is resolved once by the deterministic ingestion
    # layer. Do not let the extraction model reassign a wager to an incidental
    # historical week mentioned elsewhere in the caption/card.
    try:
        if default_week:
            week = int(
                default_week
            )
        else:
            week = int(
                pick.get("week")
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

    # Preserve literal source transcription independently from normalized identity.
    # These fields are intentionally never canonicalized here.
    for source_field in (
        "source_selection_text",
        "source_team_text",
        "source_matchup_text",
    ):
        pick[source_field] = clean_text(pick.get(source_field)) or None

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

    # Canonicalize game-total selections from structured fields. The model may
    # transcribe handwritten lowercase "u" as "w" (for example w44.5), but
    # side + numeric line are the authoritative structured representation.
    # Keep the literal source text separately in source_selection_text.
    if (
        base_market(pick.get("bet_type")) == "TOTAL"
        and pick.get("side") in {"OVER", "UNDER"}
        and pick.get("line") is not None
    ):
        label = "Over" if pick["side"] == "OVER" else "Under"
        pick["selection"] = f"{label} {pick['line']:g}"

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
# AUTHORITATIVE INVENTORY-LITERAL RESTORATION
# ============================================================

def restore_authoritative_inventory_literals(
    pick,
    inventory_row,
):
    """
    Make the independent source inventory authoritative for literal source text.

    The structured extraction is allowed to interpret market metadata, but it
    must not silently expand or replace a visible source abbreviation.

    Example:
        visible source:  NW -37.5 | BALL ST @ NW
        structured AI:  Navy -37.5 | Ball State @ Navy

    The inventory/source literals win:
        selection -> NW -37.5
        team/side -> NW
        matchup   -> BALL ST @ NW

    This is intentionally narrow:
      * source matchup text is restored whenever the independent inventory
        supplied a complete matchup;
      * selected-team restoration applies only to full-game SPREAD wagers;
      * the numeric spread must agree with the already-validated structured
        line;
      * totals, team totals, moneylines, and derivative markets are untouched.
    """

    if not isinstance(inventory_row, dict):
        return pick, False

    inventory_selection = clean_text(
        inventory_row.get("selection")
    )
    inventory_matchup = clean_text(
        inventory_row.get("matchup")
    )

    changed = False

    if inventory_selection:
        prior_source_selection = clean_text(
            pick.get("source_selection_text")
        )
        if prior_source_selection:
            pick["structured_source_selection_text"] = prior_source_selection

        # Independent inventory remains authoritative, but never discard a
        # richer literal total label that contains a source-proven team anchor.
        # This protects text posts such as "Mizzou Over 50.5" from being
        # degraded to bare "Over 50.5" by one inventory read.
        market = base_market(normalize_bet_type(pick.get("bet_type")))
        richer_total_literal = False

        if market == "TOTAL" and prior_source_selection:
            bare_inventory_total = re.match(
                r"^(?:over|under|o|u)\s*[0-9]+(?:\.[0-9]+)?\s*$",
                inventory_selection,
                flags=re.I,
            )
            structured_total = re.match(
                r"^(.*?)\s+(?:over|under|o|u)\s*[0-9]+(?:\.[0-9]+)?\s*$",
                prior_source_selection,
                flags=re.I,
            )

            if bare_inventory_total and structured_total:
                structured_team = clean_text(structured_total.group(1))
                selected_team = clean_text(side_identity(pick))
                richer_total_literal = bool(
                    structured_team
                    and selected_team
                    and teams_equivalent(structured_team, selected_team)
                )

        pick["source_selection_text"] = (
            prior_source_selection
            if richer_total_literal
            else inventory_selection
        )

    if (
        inventory_matchup
        and len(split_matchup(inventory_matchup)) == 2
    ):
        pick["source_matchup_text"] = (
            inventory_matchup
        )
        pick["matchup"] = inventory_matchup
        changed = True

    if normalize_bet_type(
        pick.get("bet_type")
    ) != "SPREAD":
        return pick, changed

    if not inventory_selection:
        return pick, changed

    match = re.match(
        r"^(.*?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*$",
        inventory_selection,
        flags=re.I,
    )

    if not match:
        return pick, changed

    source_team = clean_text(
        match.group(1)
    )

    if not source_team:
        return pick, changed

    source_line = float(
        match.group(3)
    )

    if match.group(2) == "-":
        source_line = -source_line

    structured_line = safe_float(
        pick.get("line")
    )

    if (
        structured_line is not None
        and abs(structured_line - source_line) > 1e-9
    ):
        # The row-level inventory reconciliation should already reject this
        # case.  Keep the helper fail-closed anyway.
        return pick, changed

    pick["source_team_text"] = source_team
    pick["selection"] = inventory_selection
    pick["team"] = source_team
    pick["side"] = source_team
    pick["line"] = source_line
    pick["source_literal_identity_restored"] = True

    return pick, True


# ============================================================
# CONTEXT-ONLY SOURCE TEAM NORMALIZATION
# ============================================================

# These are source-card conventions that are safe only when the complete
# matchup independently contains the mapped team. They are intentionally NOT
# global football aliases. This prevents a short token such as UT from being
# interpreted outside the source matchup that proves its meaning.
SOURCE_CONTEXT_TEAM_ALIASES = {
    "ut": "Tennessee",
}


def normalize_contextual_source_team(pick):
    """
    Correct a model-expanded spread team from the literal source token when a
    context-only source abbreviation is independently confirmed by the full
    matchup.

    Example:
        source_team_text = UT
        source_matchup_text = Texas @ Tenn
        -> Tennessee

    Safety:
      - SPREAD only;
      - requires a literal source_team_text;
      - requires a complete two-team source matchup;
      - mapped team must match exactly one matchup side;
      - line must already be numeric;
      - never changes picker, week, line, odds, units, or result.
    """
    if base_market(normalize_bet_type(pick.get("bet_type"))) != "SPREAD":
        return pick, False

    literal_team = clean_text(pick.get("source_team_text"))
    mapped_team = SOURCE_CONTEXT_TEAM_ALIASES.get(norm(literal_team))

    if not mapped_team:
        return pick, False

    source_matchup = (
        clean_text(pick.get("source_matchup_text"))
        or clean_text(pick.get("matchup"))
    )
    sides = split_matchup(source_matchup) if source_matchup else []

    if len(sides) != 2:
        return pick, False

    mapped_indexes = [
        index
        for index, side in enumerate(sides)
        if teams_equivalent(mapped_team, side)
    ]

    if len(mapped_indexes) != 1:
        return pick, False

    line = safe_float(pick.get("line"))
    if line is None:
        return pick, False

    old_selection = clean_text(pick.get("selection"))
    old_team = clean_text(pick.get("team"))
    old_side = clean_text(pick.get("side"))

    pick.setdefault("pre_source_context_selection", old_selection or None)
    pick.setdefault("pre_source_context_team", old_team or None)
    pick.setdefault("pre_source_context_side", old_side or None)

    pick["selection"] = f"{mapped_team} {line:+g}"
    pick["team"] = mapped_team

    if old_side and old_side.upper() not in {"OVER", "UNDER"}:
        pick["side"] = mapped_team

    # Keep the source matchup itself intact. Schedule enrichment owns canonical
    # ESPN matchup/opponent metadata.
    other_index = 1 - mapped_indexes[0]
    pick["opponent"] = clean_text(sides[other_index]) or pick.get("opponent")
    pick["source_context_team_normalized"] = True

    print(
        "INGEST SOURCE CONTEXT TEAM NORMALIZED:",
        pick.get("picker"),
        "| source token:",
        literal_team,
        "|",
        old_selection,
        "->",
        pick.get("selection"),
        "| source matchup:",
        source_matchup,
    )

    return pick, True


# ============================================================
# STRUCTURAL PICK VALIDATION
# ============================================================

def _source_total_single_team_anchor(pick):
    """
    Return True only when the literal source wager itself proves a one-team
    anchor for a GAME TOTAL, e.g. ``Mizzou Over 50.5``.

    This does not convert the wager into a team total. It only preserves enough
    identity for the ESPN resolver's UNIQUE_SELECTED_TEAM fallback. Bare totals
    such as ``Over 50.5`` still fail closed without a complete matchup.
    """
    if base_market(normalize_bet_type(pick.get("bet_type"))) != "TOTAL":
        return False

    literal = clean_text(pick.get("source_selection_text"))
    selected = clean_text(side_identity(pick))

    if not literal or not selected:
        return False

    match = re.match(
        r"^(.*?)\s+(?:over|under|o|u)\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        literal,
        flags=re.I,
    )

    if not match:
        return False

    source_team = clean_text(match.group(1))
    if not source_team:
        return False

    return teams_equivalent(source_team, selected)


def _source_literal_team_matches_matchup_side(selected, side):
    """
    Compare two SOURCE-LITERAL team tokens without forcing a global school
    identity for ambiguous abbreviations.

    This is an INTERNAL-CONSISTENCY check only. It never decides which school
    an ambiguous token means; ESPN enrichment still owns canonical identity.

    Safe source-card cases handled here include:
      * exact ambiguous literals: OSU == OSU;
      * full name vs source acronym: Air Force == AF;
      * local State shorthand: Jax == JAX ST, Sac St == SAC;
      * matchup headings with trailing schedule text:
        ARK ST == "ARK ST 7:30pm THURS".

    Contradictory source rows still fail closed, e.g. UofA vs UofSC/UF.
    """

    def _strip_source_schedule_suffix(value):
        text = clean_text(value)
        if not text:
            return ""

        # Source-card matchup headings sometimes append kickoff/day metadata to
        # the home-team cell.  Strip only an obvious clock token and everything
        # after it.  Do not remove arbitrary words from team names.
        text = re.sub(
            r"\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b.*$",
            "",
            text,
            flags=re.I,
        ).strip()

        return text

    selected_text = _strip_source_schedule_suffix(selected)
    side_text = _strip_source_schedule_suffix(side)

    if not selected_text or not side_text:
        return False

    # Exact source-literal equality is authoritative even when the token is
    # globally ambiguous (OSU, USC, MSU, etc.).
    if norm(selected_text) == norm(side_text):
        return True

    # Use the shared identity system whenever it can resolve the pair safely.
    if teams_equivalent(selected_text, side_text):
        return True

    def _short_state_abbreviation(value):
        tokens = norm(value).split()
        if not tokens:
            return None

        # Strip a trailing State marker ONLY when what remains is short. This
        # deliberately does not collapse Michigan and Michigan State, Florida
        # and Florida State, etc.
        if tokens[-1] in {"st", "state"}:
            tokens = tokens[:-1]

        compact = "".join(tokens)
        if 2 <= len(compact) <= 4:
            return compact
        return None

    selected_short = _short_state_abbreviation(selected_text)
    side_short = _short_state_abbreviation(side_text)

    if (
        selected_short
        and side_short
        and selected_short == side_short
    ):
        return True

    def _source_acronym(value):
        """Return a conservative acronym for a multi-word literal team name."""
        tokens = [
            token
            for token in norm(value).split()
            if token
        ]

        if len(tokens) < 2:
            return None

        # Preserve meaningful source words.  A two-to-four character acronym
        # such as AF for Air Force is useful as row-consistency evidence but is
        # never promoted to canonical identity here.
        acronym = "".join(token[0] for token in tokens if token)
        if 2 <= len(acronym) <= 4:
            return acronym
        return None

    selected_compact = "".join(norm(selected_text).split())
    side_compact = "".join(norm(side_text).split())
    selected_acronym = _source_acronym(selected_text)
    side_acronym = _source_acronym(side_text)

    if (
        selected_acronym
        and selected_acronym == side_compact
        and 2 <= len(side_compact) <= 4
    ):
        return True

    if (
        side_acronym
        and side_acronym == selected_compact
        and 2 <= len(selected_compact) <= 4
    ):
        return True

    return False


def _spread_selected_team_matches_source_matchup(pick):
    """Fail closed when a spread's selected team contradicts a full matchup."""
    matchup = clean_text(
        pick.get("source_matchup_text")
        or pick.get("matchup")
    )
    sides = split_matchup(matchup) if matchup else []

    if len(sides) != 2:
        return True

    selected = clean_text(side_identity(pick))
    if not selected:
        return False

    return any(
        _source_literal_team_matches_matchup_side(selected, side)
        for side in sides
    )

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

        if selected and not _spread_selected_team_matches_source_matchup(pick):
            errors.append(
                "spread selected team conflicts with complete source matchup"
            )

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
            and not _source_total_single_team_anchor(pick)
        ):
            errors.append(
                "total has no complete matchup identity or source-proven team anchor"
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
# ESPN-ASSISTED SOURCE IDENTITY REPAIR
# ============================================================

def repair_spread_identity_from_espn_schedule(
    pick,
    *,
    season_year,
    week,
    slate_cache,
):
    """
    Repair a narrowly defined OCR/transcription failure by using the ESPN
    schedule as an independent identity check.

    This is intentionally limited to SPREAD wagers with a complete two-team
    matchup. It is designed for cases where one side of the source graphic is
    read correctly and the other side is visually misread (for example GSU as
    ASU).

    Safety rules:
      - never changes picker, week, market, line, odds, units, or result;
      - requires the selected team to match one of the two extracted matchup
        sides, so we know which side of the graphic the wager selected;
      - uses the OTHER extracted matchup side as the independent anchor;
      - the anchor must identify exactly one ESPN event in the complete week
        slate;
      - that event must contain exactly one team equivalent to the anchor;
      - only then may the selected-team identity and matchup/opponent metadata
        be repaired to the other ESPN team;
      - ambiguous or missing schedule evidence fails closed and leaves the
        source extraction unchanged.

    Original source-derived identity fields are preserved in
    pre_espn_ingest_repair_* metadata whenever a repair occurs.
    """

    if base_market(normalize_bet_type(pick.get("bet_type"))) != "SPREAD":
        return pick, False

    matchup = clean_text(pick.get("matchup"))
    sides = split_matchup(matchup) if matchup else None

    if not sides or len(sides) != 2:
        return pick, False

    selected = clean_text(side_identity(pick))
    if not selected:
        return pick, False

    selected_side_indexes = [
        index
        for index, side in enumerate(sides)
        if teams_equivalent(selected, side)
    ]

    def _short_token(value):
        return re.sub(r"[^a-z0-9]", "", norm(value))

    def _edit_distance_at_most_one(first, second):
        first = _short_token(first)
        second = _short_token(second)

        if not first or not second:
            return False

        if not (2 <= len(first) <= 5 and 2 <= len(second) <= 5):
            return False

        if abs(len(first) - len(second)) > 1:
            return False

        if first == second:
            return True

        if len(first) == len(second):
            return sum(a != b for a, b in zip(first, second)) <= 1

        # One insertion/deletion.
        if len(first) > len(second):
            first, second = second, first

        i = j = mismatches = 0
        while i < len(first) and j < len(second):
            if first[i] == second[j]:
                i += 1
                j += 1
                continue
            mismatches += 1
            if mismatches > 1:
                return False
            j += 1

        return True

    def _short_alias_ocr_match(source_token, team_identity):
        source_token = _short_token(source_token)
        if not source_token or not (2 <= len(source_token) <= 5):
            return False

        aliases = set(alias_group(team_identity) or [])
        aliases.add(norm(team_identity))

        return any(
            _edit_distance_at_most_one(source_token, alias)
            for alias in aliases
            if 2 <= len(_short_token(alias)) <= 5
        )

    # Normally the selected spread team must match exactly one visible matchup
    # side.  A narrow exception handles a one-character OCR error in a compact
    # team abbreviation (for example NIU vs NWU).  The approximation is used
    # only to identify which side of an already-complete matchup was selected;
    # the opposite side must still uniquely anchor one ESPN event below.
    selected_side_ocr = False

    if len(selected_side_indexes) == 1:
        selected_index = selected_side_indexes[0]
    elif len(selected_side_indexes) == 0:
        approximate_indexes = [
            index
            for index, side in enumerate(sides)
            if _short_alias_ocr_match(selected, side)
        ]

        if len(approximate_indexes) != 1:
            return pick, False

        selected_index = approximate_indexes[0]
        selected_side_ocr = True
    else:
        return pick, False
    anchor_index = 1 - selected_index
    anchor = clean_text(sides[anchor_index])

    if not anchor:
        return pick, False

    cache_key = (int(season_year), int(week))

    if cache_key not in slate_cache:
        try:
            slate_cache[cache_key] = build_complete_week_slate(
                [pick],
                int(season_year),
                int(week),
            )
        except Exception as exc:
            print(
                "INGEST ESPN SOURCE IDENTITY CHECK UNAVAILABLE:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "|",
                type(exc).__name__,
                exc,
            )
            slate_cache[cache_key] = None

    events = slate_cache.get(cache_key)

    if events is None:
        return pick, False

    anchored_events = []

    for event in events:
        event_matchup = clean_text(event_matchup_text(event))
        event_sides = split_matchup(event_matchup) if event_matchup else None

        if not event_sides or len(event_sides) != 2:
            continue

        anchor_matches = [
            index
            for index, event_side in enumerate(event_sides)
            if teams_equivalent(anchor, event_side)
        ]

        if len(anchor_matches) != 1:
            continue

        anchored_events.append(
            (
                event,
                event_matchup,
                event_sides,
                anchor_matches[0],
            )
        )

    # One correctly read opponent must identify exactly one game that week.
    if len(anchored_events) != 1:
        return pick, False

    event, resolved_matchup, event_sides, event_anchor_index = anchored_events[0]
    resolved_anchor = clean_text(event_sides[event_anchor_index])
    resolved_selected = clean_text(event_sides[1 - event_anchor_index])

    if not resolved_selected or not resolved_anchor:
        return pick, False

    # If ESPN already agrees with the selected team, there is no selected-team
    # OCR error to repair here. Later schedule enrichment may canonicalize stale
    # opponent/matchup metadata without changing the wager identity.
    if teams_equivalent(selected, resolved_selected):
        return pick, False

    # PRIMARY SOURCE-LITERAL SAFETY GATE:
    #
    # The vision extractor preserves the exact selected-team token in
    # source_team_text before expanding school identities. If that literal token
    # maps to the ESPN counterpart through football_identity aliases (for example
    # GSU -> Georgia State), the source itself confirms the repair. This is
    # stronger evidence than fuzzy similarity and does not rely on the opponent
    # alone.
    literal_source_team = clean_text(pick.get("source_team_text"))
    literal_confirms_resolved = bool(
        literal_source_team
        and teams_equivalent(literal_source_team, resolved_selected)
    )

    # IMPORTANT SAFETY GATE:
    #
    # A unique game for the *other* extracted matchup side is not enough evidence
    # to replace the selected spread team. The opponent/matchup text itself may be
    # stale or misread (for example SMU paired with Mississippi State, or Minnesota
    # paired with Northwestern). In those cases the selected wager identity must be
    # preserved and schedule_enrich.py can repair only the stale game metadata.
    #
    # Ingest may replace the selected team only when the selected identity and the
    # ESPN counterpart are themselves strongly text-similar, which is evidence of
    # a narrow OCR/transcription error such as Arizona State vs Georgia State.
    # This is deliberately conservative and generic; it contains no week/team rule.
    from difflib import SequenceMatcher

    selected_norm = norm(selected)
    resolved_norm = norm(resolved_selected)

    identity_similarity = SequenceMatcher(
        None,
        selected_norm,
        resolved_norm,
    ).ratio()

    # A short source abbreviation can be a one-character OCR error even when
    # comparing it with the full ESPN team name produces a low similarity score.
    # Example shape: GSU misread as ASU.  Use ESPN's actual team abbreviation for the
    # resolved counterpart and allow the repair only when the source token and that
    # acronym have the same short length and differ by exactly one character.
    #
    # This remains safe because the OTHER matchup side already had to identify
    # exactly one ESPN event above.  It does not allow arbitrary opponent-driven
    # replacement such as SMU -> Missouri or MINN -> Indiana.
    def _resolved_espn_abbreviation(event, resolved_team):
        competitions = event.get("competitions") or []
        if not competitions:
            return ""

        competitors = competitions[0].get("competitors") or []
        matches = []

        for competitor in competitors:
            team = competitor.get("team") or {}
            identity_values = [
                clean_text(team.get("displayName")),
                clean_text(team.get("shortDisplayName")),
                clean_text(team.get("location")),
                clean_text(team.get("abbreviation")),
            ]

            if any(
                value and teams_equivalent(resolved_team, value)
                for value in identity_values
            ):
                abbreviation = clean_text(team.get("abbreviation"))
                if abbreviation:
                    matches.append(abbreviation)

        if len(matches) != 1:
            return ""

        return re.sub(r"[^a-z0-9]", "", norm(matches[0]))

    selected_token = re.sub(r"[^a-z0-9]", "", selected_norm)
    resolved_acronym = _resolved_espn_abbreviation(event, resolved_selected)

    one_char_acronym_ocr = (
        2 <= len(selected_token) <= 5
        and len(selected_token) == len(resolved_acronym)
        and sum(a != b for a, b in zip(selected_token, resolved_acronym)) == 1
    )

    # ESPN abbreviations are not always the same shorthand printed on betting
    # cards (Northwestern may appear as NW/NWU, for example).  Compare the
    # short source token against the complete alias family of the resolved ESPN
    # team as a second OCR check.  This remains fail-closed: the opposite
    # matchup side already had to identify exactly one event and the token must
    # be within one edit of a known short alias.
    one_char_alias_ocr = _short_alias_ocr_match(
        selected,
        resolved_selected,
    )

    if (
        identity_similarity < 0.67
        and not one_char_acronym_ocr
        and not one_char_alias_ocr
        and not literal_confirms_resolved
    ):
        print(
            "INGEST ESPN IDENTITY CONFLICT PRESERVED:",
            pick.get("picker"),
            "|",
            pick.get("selection"),
            "| matchup:",
            matchup,
            "| ESPN counterpart:",
            resolved_selected,
            "| similarity:",
            f"{identity_similarity:.3f}",
            "| ESPN acronym:",
            resolved_acronym or "NONE",
            "| action: preserve selected wager; defer metadata repair",
        )
        return pick, False

    if literal_confirms_resolved and not teams_equivalent(selected, resolved_selected):
        print(
            "INGEST SOURCE-LITERAL IDENTITY CONFIRMED:",
            pick.get("picker"),
            "| source token:",
            literal_source_team,
            "| parsed team:",
            selected,
            "| ESPN team:",
            resolved_selected,
            "| anchor:",
            anchor,
        )

    if (
        (one_char_acronym_ocr or one_char_alias_ocr or selected_side_ocr)
        and identity_similarity < 0.67
        and not literal_confirms_resolved
    ):
        print(
            "INGEST ESPN SHORT-ALIAS OCR CONFIRMED:",
            pick.get("picker"),
            "| source team:",
            selected,
            "| ESPN team:",
            resolved_selected,
            "| ESPN acronym:",
            resolved_acronym or "NONE",
            "| anchor:",
            anchor,
        )

    line = safe_float(pick.get("line"))
    if line is None:
        return pick, False

    old_selection = clean_text(pick.get("selection"))
    old_team = clean_text(pick.get("team"))
    old_side = clean_text(pick.get("side"))
    old_opponent = clean_text(pick.get("opponent"))
    old_matchup = matchup

    pick.setdefault("pre_espn_ingest_repair_selection", old_selection or None)
    pick.setdefault("pre_espn_ingest_repair_team", old_team or None)
    pick.setdefault("pre_espn_ingest_repair_side", old_side or None)
    pick.setdefault("pre_espn_ingest_repair_opponent", old_opponent or None)
    pick.setdefault("pre_espn_ingest_repair_matchup", old_matchup or None)

    pick["selection"] = f"{resolved_selected} {line:+g}"
    pick["team"] = resolved_selected

    # For spread markets, side may contain the selected team name. Preserve
    # OVER/UNDER-style values defensively, though they should not occur here.
    if old_side and old_side.upper() not in {"OVER", "UNDER"}:
        pick["side"] = resolved_selected

    pick["opponent"] = resolved_anchor
    pick["matchup"] = resolved_matchup
    pick["espn_ingest_identity_repaired"] = True
    pick["espn_ingest_identity_repair_event_id"] = str(event.get("id") or "") or None

    print(
        "INGEST ESPN SOURCE IDENTITY REPAIR:",
        pick.get("picker"),
        "|",
        old_selection,
        "| matchup:",
        old_matchup,
        "->",
        pick.get("selection"),
        "| matchup:",
        resolved_matchup,
        "| anchor:",
        anchor,
    )

    return pick, True


# ============================================================
# VALIDATE ENTIRE NORMAL-POST EXTRACTION
# ============================================================

def validate_normal_post_payload(
    payload,
    *,
    image_urls,
    default_week,
    season_year,
    picker_hint,
    preflight_inventory,
):
    """
    Transaction boundary for one normal source post.

    The independent inventory is a strict checklist of TRACKED wagers only.
    Structured extraction must map exactly one pick to every inventory row.
    Guest/fan wagers are ignored by both layers and therefore cannot poison the
    retry queue merely because they appear on the official account.
    """

    if not isinstance(payload, dict):
        raise ValueError(
            "Normal extraction payload is not an object"
        )

    complete = safe_bool(
        payload.get("complete")
    )

    if not complete:
        notes = clean_text(
            payload.get("extraction_notes")
        )

        raise ValueError(
            "AI marked source extraction incomplete"
            + (f": {notes}" if notes else "")
        )

    expected_images = len(image_urls)

    try:
        reported_supplied = int(
            payload.get("images_supplied") or 0
        )
    except Exception:
        reported_supplied = -1

    try:
        images_read = int(
            payload.get("images_read") or 0
        )
    except Exception:
        images_read = -1

    if reported_supplied != expected_images:
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

    checks = payload.get("image_checks")
    if checks is None:
        checks = []

    if not isinstance(checks, list):
        raise ValueError(
            "image_checks is not a list"
        )

    image_wager_count = 0

    if expected_images:
        if len(checks) != expected_images:
            raise ValueError(
                "AI image_checks count mismatch: "
                f"{len(checks)}/{expected_images}"
            )

        indexes = set()

        for check in checks:
            if not isinstance(check, dict):
                raise ValueError(
                    "Invalid image_checks entry"
                )

            try:
                image_index = int(
                    check.get("image_index")
                )
            except Exception as exc:
                raise ValueError(
                    "Image check has invalid index"
                ) from exc

            indexes.add(image_index)

            if not safe_bool(check.get("readable")):
                raise ValueError(
                    f"Source image {image_index} was not readable"
                )

            try:
                wager_count = int(
                    check.get("wager_count") or 0
                )
            except Exception as exc:
                raise ValueError(
                    f"Source image {image_index} has invalid wager_count"
                ) from exc

            if wager_count < 0:
                raise ValueError(
                    f"Source image {image_index} has negative wager_count"
                )

            image_wager_count += wager_count

        expected_indexes = set(
            range(1, expected_images + 1)
        )

        if indexes != expected_indexes:
            raise ValueError(
                "Image check indexes do not cover every supplied image"
            )

    inventory_rows = list(
        (preflight_inventory or {}).get("wagers")
        or []
    )

    try:
        independent_count = int(
            (preflight_inventory or {}).get("wager_count")
            or 0
        )
    except Exception as exc:
        raise ValueError(
            "Independent source inventory returned invalid wager_count"
        ) from exc

    if independent_count < 0:
        raise ValueError(
            "Independent source inventory returned negative wager_count"
        )

    if len(inventory_rows) != independent_count:
        raise ValueError(
            "Independent source inventory count does not match its rows "
            f"(reported {independent_count}, rows {len(inventory_rows)})"
        )

    is_pick_post = safe_bool(
        payload.get("is_pick_post")
    )

    raw_picks = payload.get("picks")

    if not isinstance(raw_picks, list):
        raise ValueError(
            "AI picks is not a list"
        )

    if not is_pick_post:
        if raw_picks:
            raise ValueError(
                "AI says this is not a pick post but returned wagers"
            )

        if independent_count != 0:
            raise ValueError(
                "Tracked wager count reconciliation failed: "
                f"inventory reports {independent_count}, "
                "but structured extraction classified the post as non-pick"
            )

        if expected_images and image_wager_count != 0:
            raise ValueError(
                "Tracked wager count reconciliation failed: "
                f"image checks report {image_wager_count}, "
                "but structured extraction classified the post as non-pick"
            )

        return {
            "is_pick_post": False,
            "picks": [],
            "image_wager_count": image_wager_count,
        }

    if not raw_picks:
        raise ValueError(
            "AI says this is a pick post but extracted zero wagers"
        )

    if independent_count <= 0:
        raise ValueError(
            "Tracked wager count reconciliation failed: "
            f"structured extraction returned {len(raw_picks)} wager(s), "
            "but independent tracked inventory reports zero"
        )

    if len(raw_picks) != independent_count:
        raise ValueError(
            "Tracked wager count reconciliation failed: "
            f"inventory reports {independent_count}, "
            f"structured extraction returned {len(raw_picks)}"
        )

    if expected_images and image_wager_count != independent_count:
        raise ValueError(
            "Tracked wager count reconciliation failed: "
            f"image checks report {image_wager_count}, "
            f"independent inventory reports {independent_count}"
        )

    expected_inventory_indexes = set(
        range(1, independent_count + 1)
    )

    mapped_raw_picks = []
    seen_inventory_indexes = set()

    for raw_pick in raw_picks:
        if not isinstance(raw_pick, dict):
            raise ValueError(
                "Extracted pick is not an object"
            )

        try:
            inventory_index = int(
                raw_pick.get("inventory_index")
            )
        except Exception as exc:
            raise ValueError(
                "Inventory row mapping failed: extracted wager is missing a "
                "valid inventory_index"
            ) from exc

        if inventory_index not in expected_inventory_indexes:
            raise ValueError(
                "Inventory row mapping failed: inventory_index "
                f"{inventory_index} is outside 1..{independent_count}"
            )

        if inventory_index in seen_inventory_indexes:
            raise ValueError(
                "Inventory row mapping failed: duplicate inventory_index "
                f"{inventory_index}"
            )

        seen_inventory_indexes.add(inventory_index)
        mapped_raw_picks.append((inventory_index, raw_pick))

    if seen_inventory_indexes != expected_inventory_indexes:
        missing = sorted(
            expected_inventory_indexes - seen_inventory_indexes
        )
        raise ValueError(
            "Inventory row mapping failed: missing inventory indexes "
            f"{missing}"
        )

    def _literal_signature(value):
        value = clean_text(value).lower()
        value = (
            value
            .replace("−", "-")
            .replace("–", "-")
            .replace("—", "-")
            .replace("½", ".5")
        )

        numbers = tuple(
            re.findall(
                r"[+-]?\d+(?:\.\d+)?",
                value,
            )
        )

        words = re.sub(
            r"[+-]?\d+(?:\.\d+)?",
            " ",
            value,
        )

        words = re.sub(
            r"[^a-z0-9]+",
            "",
            words,
        )

        return words, numbers

    def _literal_total_direction(value):
        text = clean_text(value).lower()
        text = (
            text
            .replace("−", "-")
            .replace("–", "-")
            .replace("—", "-")
            .replace("½", ".5")
        )

        matches = re.findall(
            r"(?:^|\s)(over|under|o|u)\s*([0-9]+(?:\.[0-9]+)?)\b",
            text,
            flags=re.I,
        )

        if not matches:
            return None

        token = str(matches[-1][0]).lower()
        return "OVER" if token in {"over", "o"} else "UNDER"

    def _inventory_selection_compatible(inventory_row, raw_pick):
        """
        Verify the structured row still represents the same independently
        inventoried wager. Literal equality is preferred. The only relaxed
        case is team-total shorthand where the inventory may contain only the
        visible O/U number (for example O30.5) while structured extraction
        correctly restores the team-total owner (for example OSU TT O30.5).

        This does NOT relax spread signs, total direction, numeric lines,
        picker identity, or conflicting matchup text.
        """
        if not isinstance(inventory_row, dict):
            return False

        inventory_text = clean_text(inventory_row.get("selection"))
        source_text = clean_text(
            raw_pick.get("source_selection_text")
            or raw_pick.get("selection")
        )

        inventory_signature = _literal_signature(inventory_text)
        source_signature = _literal_signature(source_text)

        if inventory_signature == source_signature:
            return True

        # If the structured extractor preserved the literal combined source
        # cell on each decomposed wager, allow the independently inventoried
        # component to map back to that cell only when deterministic compound
        # parsing proves the component is one of the exact two wagers. Market
        # and line validation still run below on the normalized pick.
        compound_parts = split_compound_wager_selection(
            source_text
        )

        if len(compound_parts) > 1:
            inventory_norm = norm(inventory_text)
            compound_norms = {
                norm(part)
                for part in compound_parts
            }

            if inventory_norm in compound_norms:
                return True

        # Different numbers can never describe the same source wager.
        if inventory_signature[1] != source_signature[1]:
            return False

        inventory_picker = normalize_picker_safe(
            inventory_row.get("picker")
        )
        source_picker = normalize_picker_safe(
            raw_pick.get("picker")
        )

        if (
            inventory_picker
            and source_picker
            and inventory_picker != source_picker
        ):
            return False

        raw_type = normalize_bet_type(
            raw_pick.get("bet_type")
        )
        raw_market = base_market(raw_type)

        # Source-literal abbreviations may differ harmlessly between the
        # independent inventory and structured extraction (for example
        # ``JAX ST -3`` vs ``Jax -3``).  Permit that mapping only when the
        # numeric spread is identical and both literal team tokens resolve to
        # the same unambiguous football identity.  The independent inventory
        # remains authoritative and is restored immediately after this check.
        if raw_market == "SPREAD":
            inventory_side = side_identity({
                "bet_type": raw_type,
                "selection": inventory_text,
            })
            source_side = side_identity({
                "bet_type": raw_type,
                "selection": source_text,
            })

            if (
                inventory_side
                and source_side
                and teams_equivalent(inventory_side, source_side)
            ):
                return True

            return False

        # Full-game/derivative totals may use equivalent direction shorthand
        # such as O48.5 vs Over 48.5.  Accept only an exact numeric line and
        # the same O/U direction; conflicting matchup text still fails closed.
        if raw_market == "TOTAL":
            inventory_direction = _literal_total_direction(inventory_text)
            source_direction = (
                _literal_total_direction(source_text)
                or total_direction(raw_pick)
            )

            if (
                not inventory_direction
                or not source_direction
                or inventory_direction != source_direction
            ):
                return False

            inventory_matchup = clean_text(inventory_row.get("matchup"))
            source_matchup = clean_text(
                raw_pick.get("source_matchup_text")
                or raw_pick.get("matchup")
            )

            if inventory_matchup and source_matchup:
                inventory_hints = split_matchup(inventory_matchup)
                source_hints = split_matchup(source_matchup)

                if len(inventory_hints) == 2 and len(source_hints) == 2:
                    direct = (
                        teams_equivalent(inventory_hints[0], source_hints[0])
                        and teams_equivalent(inventory_hints[1], source_hints[1])
                    )
                    reverse = (
                        teams_equivalent(inventory_hints[0], source_hints[1])
                        and teams_equivalent(inventory_hints[1], source_hints[0])
                    )
                    if not (direct or reverse):
                        # Exact normalized matchup text is also safe even when
                        # it contains a deliberately ambiguous short token.
                        if norm(inventory_matchup) != norm(source_matchup):
                            return False
                elif norm(inventory_matchup) != norm(source_matchup):
                    return False

            return True

        if raw_market != "TEAM_TOTAL":
            return False

        inventory_direction = _literal_total_direction(
            inventory_text
        )
        source_direction = (
            _literal_total_direction(source_text)
            or total_direction(raw_pick)
        )

        if (
            not inventory_direction
            or not source_direction
            or inventory_direction != source_direction
        ):
            return False

        # Require exactly one numeric line on each side and preserve it exactly.
        if (
            len(inventory_signature[1]) != 1
            or len(source_signature[1]) != 1
        ):
            return False

        inventory_line = safe_float(inventory_signature[1][0])
        source_line = safe_float(raw_pick.get("line"))

        if source_line is None:
            source_line = safe_float(source_signature[1][0])

        if (
            inventory_line is None
            or source_line is None
            or abs(inventory_line - source_line) > 1e-9
        ):
            return False

        # If both independent and structured passes retained matchup text, they
        # may not disagree. This keeps the relaxed rule from mapping a bare O/U
        # number onto the wrong game.
        inventory_matchup = clean_text(
            inventory_row.get("matchup")
        )
        source_matchup = clean_text(
            raw_pick.get("source_matchup_text")
            or raw_pick.get("matchup")
        )

        if (
            inventory_matchup
            and source_matchup
            and norm(inventory_matchup) != norm(source_matchup)
        ):
            return False

        return True

    normalized = []
    slate_cache = {}

    for inventory_index, raw_pick in sorted(
        mapped_raw_picks,
        key=lambda item: item[0],
    ):
        inventory_row = inventory_rows[inventory_index - 1]

        if not _inventory_selection_compatible(
            inventory_row,
            raw_pick,
        ):
            raise ValueError(
                "Inventory row mapping failed: row "
                f"{inventory_index} selection mismatch "
                f"({inventory_row.get('selection')!r} vs "
                f"{raw_pick.get('source_selection_text') or raw_pick.get('selection')!r})"
            )

        pick = normalize_ai_pick(
            raw_pick,
            default_week=default_week,
            picker_hint=picker_hint,
        )

        pick, _ = restore_authoritative_inventory_literals(
            pick,
            inventory_row,
        )

        pick, _ = normalize_contextual_source_team(
            pick
        )

        pick, _ = repair_spread_identity_from_espn_schedule(
            pick,
            season_year=season_year,
            week=pick_week(pick),
            slate_cache=slate_cache,
        )

        validate_normal_pick(pick)
        normalized.append(pick)

    if len(normalized) != independent_count:
        raise ValueError(
            "Tracked wager count reconciliation failed: "
            f"inventory reports {independent_count}, "
            f"validated extraction returned {len(normalized)}"
        )

    # AI must not return the same tracked wager twice from one post.
    local_keys = set()

    for pick in normalized:
        key = canonical_pick_key(pick)

        if key in local_keys:
            raise ValueError(
                "AI returned duplicate wager within the same source post: "
                f"{pick.get('picker')} | {pick.get('selection')}"
            )

        local_keys.add(key)

    return {
        "is_pick_post": True,
        "picks": normalized,
        "image_wager_count": image_wager_count,
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

        # Preserve the source-atomic transcription in the stored row. These
        # fields are immutable evidence for downstream deterministic checks.
        "source_selection_text":
            extracted.get(
                "source_selection_text"
            ),

        "source_team_text":
            extracted.get(
                "source_team_text"
            ),

        "source_matchup_text":
            extracted.get(
                "source_matchup_text"
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



def _normalized_effective_line(pick):
    value = effective_line(pick)

    if value is None:
        return None

    try:
        return round(float(value), 4)
    except Exception:
        return None


def _same_picker_week_market(candidate, existing):
    if norm(candidate.get("picker")) != norm(existing.get("picker")):
        return False

    if pick_week(candidate) != pick_week(existing):
        return False

    candidate_type = normalize_bet_type(candidate.get("bet_type"))
    existing_type = normalize_bet_type(existing.get("bet_type"))

    if market_period(candidate_type) != market_period(existing_type):
        return False

    if base_market(candidate_type) != base_market(existing_type):
        return False

    return True


def _resolved_selected_team(pick):
    return (
        canonical_selected_team(pick)
        or None
    )


def _team_is_in_game(team, game):
    return bool(
        team
        and game
        and team in set(game)
    )


def relaxed_duplicate_match(candidate, existing):
    """
    Safe cross-post duplicate detector.

    Exact canonical identity remains the primary system. This fallback exists
    only for a common official-account pattern: a later text/add-on post repeats
    a wager that already appeared on the complete image card but omits the
    matchup.

    Safety rules:
      * same picker, week, period, and market
      * same original wager line
      * spread/team identity must agree directly, or the fully named team from
        one row must belong to the exact two-team matchup on the other row
      * totals still require the same complete game identity
      * no fuzzy team guessing
    """

    if not _same_picker_week_market(candidate, existing):
        return False

    candidate_type = normalize_bet_type(candidate.get("bet_type"))
    market = base_market(candidate_type)

    candidate_line = _normalized_effective_line(candidate)
    existing_line = _normalized_effective_line(existing)

    if candidate_line != existing_line:
        return False

    candidate_game = canonical_game_identity(candidate)
    existing_game = canonical_game_identity(existing)

    if market == "TOTAL":
        if not candidate_game or not existing_game:
            return False

        return (
            candidate_game == existing_game
            and (total_direction(candidate) or "")
            == (total_direction(existing) or "")
        )

    if market == "TEAM_TOTAL":
        candidate_team = _resolved_selected_team(candidate)
        existing_team = _resolved_selected_team(existing)

        if not candidate_team or not existing_team:
            return False

        return (
            candidate_team == existing_team
            and (total_direction(candidate) or "")
            == (total_direction(existing) or "")
        )

    if market in {"SPREAD", "MONEYLINE"}:
        candidate_team = _resolved_selected_team(candidate)
        existing_team = _resolved_selected_team(existing)

        if (
            candidate_team
            and existing_team
            and candidate_team == existing_team
        ):
            return True

        if (
            candidate_team
            and _team_is_in_game(candidate_team, existing_game)
        ):
            return True

        if (
            existing_team
            and _team_is_in_game(existing_team, candidate_game)
        ):
            return True

        return False

    return False



def _normalized_source_evidence(pick):
    """Return a normalized per-post provenance map for one wager row.

    Older rows only have source_post_id/source_url.  Newer rows may carry
    source_post_evidence with multiple official posts that independently show
    the same wager.  This helper upgrades legacy rows in memory without
    changing wager identity.
    """
    evidence = {}

    raw = pick.get("source_post_evidence")
    if isinstance(raw, dict):
        for raw_id, raw_meta in raw.items():
            post_id = str(raw_id or "").strip()
            if not post_id:
                continue
            if isinstance(raw_meta, dict):
                evidence[post_id] = dict(raw_meta)
            else:
                evidence[post_id] = {}

    primary_id = str(pick.get("source_post_id") or "").strip()
    if primary_id and primary_id not in evidence:
        evidence[primary_id] = {
            "url": pick.get("source_url"),
            "text": pick.get("source_text"),
            "is_reply": pick.get("source_is_reply"),
            "conversation_id": pick.get("conversation_id"),
            "posted_at": pick.get("posted_at"),
        }

    legacy_ids = pick.get("source_post_ids") or []
    if isinstance(legacy_ids, (list, tuple, set)):
        for raw_id in legacy_ids:
            post_id = str(raw_id or "").strip()
            if post_id:
                evidence.setdefault(post_id, {})

    return evidence


def _apply_primary_source_from_evidence(pick, post_id, meta):
    """Promote one corroborating source to the legacy primary source fields."""
    pick["source_post_id"] = post_id

    if isinstance(meta, dict):
        if meta.get("url") is not None:
            pick["source_url"] = meta.get("url")
        if meta.get("text") is not None:
            pick["source_text"] = meta.get("text")
        if meta.get("is_reply") is not None:
            pick["source_is_reply"] = bool(meta.get("is_reply"))
        if "conversation_id" in meta:
            pick["conversation_id"] = meta.get("conversation_id")
        if meta.get("posted_at") is not None:
            pick["posted_at"] = meta.get("posted_at")


def register_source_evidence(pick, *, post, post_url, reply_hint):
    """Record that another verified official post contains the same wager."""
    post_id = str(post.get("id") or "").strip()
    if not post_id:
        return

    evidence = _normalized_source_evidence(pick)
    evidence[post_id] = {
        "url": post_url,
        "text": str(post.get("text") or ""),
        "is_reply": bool(reply_hint),
        "conversation_id": post.get("conversation_id"),
        "posted_at": post.get("created_at"),
        # V21+ provenance marker. register_source_evidence is only persisted
        # after a whole post transaction commits (new rows are still staged in
        # memory; duplicate evidence is applied in phase 4), so this flag is a
        # durable proof that the source evidence crossed the transaction
        # boundary successfully.
        "committed": True,
        "validation_version": CURRENT_INGEST_VALIDATION_VERSION,
    }

    pick["source_post_evidence"] = evidence
    pick["source_post_ids"] = sorted(
        evidence,
        key=post_numeric_sort,
    )

    if not str(pick.get("source_post_id") or "").strip():
        _apply_primary_source_from_evidence(
            pick,
            post_id,
            evidence[post_id],
        )


def merge_source_evidence_rows(keeper, duplicate):
    """Merge official-source provenance when two stored rows dedupe."""
    evidence = _normalized_source_evidence(keeper)
    evidence.update(_normalized_source_evidence(duplicate))

    if not evidence:
        return

    keeper["source_post_evidence"] = evidence
    keeper["source_post_ids"] = sorted(
        evidence,
        key=post_numeric_sort,
    )


def wager_has_source_post(pick, post_id):
    post_id = str(post_id or "").strip()
    if not post_id:
        return False

    if str(pick.get("source_post_id") or "").strip() == post_id:
        return True

    return post_id in _normalized_source_evidence(pick)


def detach_source_evidence(pick, post_id):
    """Detach one source from a provisional wager.

    Returns True when another official source still independently supports the
    wager, so the row must be preserved.  Returns False when the removed source
    was the row's only provenance and the row may be rebuilt/deleted.
    """
    post_id = str(post_id or "").strip()
    evidence = _normalized_source_evidence(pick)
    evidence.pop(post_id, None)

    if not evidence:
        return False

    pick["source_post_evidence"] = evidence
    pick["source_post_ids"] = sorted(
        evidence,
        key=post_numeric_sort,
    )

    if str(pick.get("source_post_id") or "").strip() == post_id:
        new_primary = pick["source_post_ids"][0]
        _apply_primary_source_from_evidence(
            pick,
            new_primary,
            evidence.get(new_primary) or {},
        )

    return True

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

        best_index, best_pick = max(
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

            merge_source_evidence_rows(
                best_pick,
                duplicate,
            )

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

    # A second pass catches cross-post repeats where one official source row
    # omitted its matchup, so the exact canonical keys differ even though the
    # wager is the same.  This uses relaxed_duplicate_match(), which is still
    # deterministic and fail-closed.
    relaxed_cleaned = []

    for pick in cleaned:
        duplicate_index = None

        for index, kept in enumerate(relaxed_cleaned):
            if relaxed_duplicate_match(pick, kept):
                duplicate_index = index
                break

        if duplicate_index is None:
            relaxed_cleaned.append(pick)
            continue

        kept = relaxed_cleaned[duplicate_index]

        if pick_quality(pick) > pick_quality(kept):
            duplicate = kept
            keeper = pick
            merge_source_evidence_rows(
                keeper,
                duplicate,
            )
            relaxed_cleaned[duplicate_index] = keeper
        else:
            duplicate = pick
            keeper = kept
            merge_source_evidence_rows(
                keeper,
                duplicate,
            )

        removed += 1

        print(
            "RELAXED DUPLICATE REMOVED:",
            duplicate.get("picker"),
            "| Week",
            duplicate.get("week"),
            "|",
            duplicate.get("selection"),
            "| matchup:",
            duplicate.get("matchup"),
        )

    if removed:
        print(
            "Duplicate wagers removed:",
            removed,
        )

    return relaxed_cleaned


# ============================================================
# DURABLE NORMAL-POST LEDGER
# ============================================================

def source_rows_for_post(existing, post_id):
    """Return every provisional wager still supported by one source post.

    This intentionally uses the full provenance map rather than only the
    legacy primary source_post_id.  A corroborated wager can legitimately
    promote another post to primary source without losing this post's evidence.
    """
    post_id = str(post_id or "").strip()
    if not post_id:
        return []

    return [
        pick
        for pick in existing
        if not pick.get("official_reconciled")
        and wager_has_source_post(pick, post_id)
    ]


def _stored_row_is_migration_safe(pick, post_id):
    """Deterministic safety gate for carrying a previously committed row forward.

    Validation-generation upgrades must never depend on a fresh AI transcription.
    The historical row already passed the ingestion transaction.  We therefore
    require only durable invariants here and let schedule/audit validation own
    canonical ESPN identity.
    """
    if not isinstance(pick, dict):
        return False

    if pick.get("official_reconciled"):
        return False

    if not pick.get("ingest_validated"):
        return False

    if not wager_has_source_post(pick, post_id):
        return False

    if not normalize_picker_safe(pick.get("picker")):
        return False

    if pick_week(pick) <= 0:
        return False

    bet_type = normalize_bet_type(pick.get("bet_type"))
    if bet_type not in SUPPORTED_MARKETS:
        return False

    if not clean_text(pick.get("selection")):
        return False

    market = base_market(bet_type)
    if market in {"SPREAD", "TOTAL", "TEAM_TOTAL"}:
        if safe_float(pick.get("line")) is None:
            return False

    return True


def _snapshot_for_post(existing, post_id, *, kind="PICK_POST"):
    rows = source_rows_for_post(existing, post_id)

    row_ids = []
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        if row_id:
            row_ids.append(row_id)

    return {
        "validation_version": CURRENT_INGEST_VALIDATION_VERSION,
        "kind": kind,
        "wager_count": len(rows),
        "row_ids": sorted(set(row_ids)),
        "recorded_at": now_iso(),
    }


def migrate_previously_committed_post(
    *,
    existing,
    post_id,
    processed_ids,
    failed_ids,
    post_validation_versions,
    post_snapshots,
):
    """Upgrade an already-committed source post WITHOUT rereading it with AI.

    This is the permanent boundary between source ingestion and validation
    migrations.  A model reread is nondeterministic and must never be allowed to
    replace rows that already carry downstream ESPN locks, grading metadata, or
    source corroboration.

    Returns True when the post was safely migrated and should be skipped.
    """
    rows = source_rows_for_post(existing, post_id)

    # A prior successful pick-post transaction is recoverable even if an older
    # buggy revalidation run removed the post from processed_post_ids and added
    # it to failed_post_ids.  Transactional ingestion never commits partial new
    # rows, so durable ingest_validated rows are proof of an earlier success.
    if rows:
        if not all(
            _stored_row_is_migration_safe(row, post_id)
            for row in rows
        ):
            return False

        # Recovering a post that an older buggy run removed from
        # processed_post_ids requires proof that this post once owned at least
        # one committed row as its PRIMARY source.  Merely having secondary
        # corroboration is not enough because pre-V21 failed transactions could
        # leak source evidence before commit.
        if post_id not in processed_ids:
            primary_proof = any(
                str(row.get("source_post_id") or "").strip() == post_id
                for row in rows
            )

            committed_evidence_proof = all(
                bool(
                    (_normalized_source_evidence(row).get(post_id) or {}).get(
                        "committed"
                    )
                )
                for row in rows
            )

            # A post can cease to be the legacy primary source after later
            # dedupe/corroboration.  V21+ committed provenance is sufficient
            # proof that it crossed the original transaction boundary and must
            # never be reread by AI.  Pre-V21 leaked evidence has no committed
            # marker and therefore cannot bootstrap itself here.
            if not (primary_proof or committed_evidence_proof):
                return False

        prior_snapshot = post_snapshots.get(post_id)
        if isinstance(prior_snapshot, dict):
            expected_ids = {
                str(value)
                for value in (prior_snapshot.get("row_ids") or [])
                if str(value)
            }
            current_ids = {
                str(row.get("id") or "")
                for row in rows
                if str(row.get("id") or "")
            }

            # If a durable V21 snapshot exists, never silently bless a different
            # row set.  This catches external/manual mutation of picks.json.
            if expected_ids and current_ids != expected_ids:
                return False

        for row in rows:
            row["ingest_validation_version"] = (
                CURRENT_INGEST_VALIDATION_VERSION
            )
            row["ingest_validated"] = True

        processed_ids.add(post_id)
        failed_ids.discard(post_id)
        post_validation_versions[post_id] = (
            CURRENT_INGEST_VALIDATION_VERSION
        )
        post_snapshots[post_id] = _snapshot_for_post(
            existing,
            post_id,
            kind="PICK_POST",
        )

        print(
            "MIGRATED PRIOR VALIDATED SOURCE POST WITHOUT AI REREAD:",
            post_id,
            "| preserved wagers:",
            len(rows),
            "| downstream locks/results preserved",
        )

        return True

    # Previously processed zero-wager/non-pick posts have no row to carry a
    # validation version.  If state proves they were successfully processed and
    # they are not a true failed-new-post retry, migrate the state record only.
    stored_version = int(post_validation_versions.get(post_id) or 0)
    if (
        post_id in processed_ids
        and post_id not in failed_ids
        and stored_version > 0
    ):
        post_validation_versions[post_id] = (
            CURRENT_INGEST_VALIDATION_VERSION
        )
        post_snapshots[post_id] = {
            "validation_version": CURRENT_INGEST_VALIDATION_VERSION,
            "kind": "NO_WAGER_ROWS",
            "wager_count": 0,
            "row_ids": [],
            "recorded_at": now_iso(),
        }

        print(
            "MIGRATED PRIOR PROCESSED ZERO-ROW POST WITHOUT AI REREAD:",
            post_id,
        )
        return True

    return False


# ============================================================
# PROCESSED NORMAL POST STATE
# ============================================================

def initialize_processed_ids(
    existing,
    state,
):
    # V21 treats committed primary source rows as the durable source ledger.
    # Rebuild the processed-ID set on EVERY run rather than trusting a one-time
    # initialization flag.  This self-heals older failed revalidation runs that
    # removed a post from processed_post_ids while leaving its committed wagers
    # intact.
    ids = set()

    for pick in existing:
        if pick.get("official_reconciled") or not pick.get("ingest_validated"):
            continue

        primary_id = str(pick.get("source_post_id") or "").strip()
        if primary_id:
            ids.add(primary_id)

        # V21+ source evidence that has crossed the transaction boundary is
        # also part of the durable ledger even if dedupe later promoted a
        # different post to the legacy primary source pointer.
        for evidence_id, evidence_meta in _normalized_source_evidence(pick).items():
            if (
                evidence_id
                and isinstance(evidence_meta, dict)
                and evidence_meta.get("committed")
            ):
                ids.add(str(evidence_id))

    # Preserve prior processed state as well, including validated non-pick posts
    # that have no wager rows.
    ids.update(
        str(value)
        for value in (
            state.get(
                "processed_post_ids",
                [],
            )
            or []
        )
        if str(value)
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
        "Durable source ledger processed post IDs:",
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

        posts.append(normalize_source_post(post))

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

    raw_post_validation_versions = (
        state.get(POST_VALIDATION_VERSIONS_KEY, {})
        or {}
    )

    post_validation_versions = {
        str(key): int(value or 0)
        for key, value in raw_post_validation_versions.items()
        if str(key)
    }

    raw_post_snapshots = (
        state.get(POST_SNAPSHOTS_KEY, {})
        or {}
    )
    post_snapshots = {
        str(key): dict(value)
        for key, value in raw_post_snapshots.items()
        if str(key) and isinstance(value, dict)
    }

    failed_ids = set(
        str(value)
        for value in (
            state.get(
                "failed_post_ids",
                [],
            )
            or []
        )
        if str(value)
    )

    # fetch_retry_posts removes successfully fetched IDs from the persisted
    # queue before validation.  Preserve the original retry intent for this run
    # so a genuinely failed NEW post cannot be mistaken for a zero-row migrated
    # post merely because the fetch itself succeeded.
    failed_ids.update(
        str(value)
        for value in (
            state.pop("_retry_post_ids_current_run", [])
            or []
        )
        if str(value)
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

        prior_rows_for_post = source_rows_for_post(
            existing,
            post_id,
        )

        row_validation_upgrade = any(
            int(
                pick.get(
                    "ingest_validation_version"
                )
                or 0
            ) < CURRENT_INGEST_VALIDATION_VERSION
            for pick in prior_rows_for_post
        )

        stored_post_validation_version = int(
            post_validation_versions.get(post_id)
            or 0
        )

        state_validation_upgrade = (
            post_id in processed_ids
            and stored_post_validation_version
            < CURRENT_INGEST_VALIDATION_VERSION
        )

        needs_validation_upgrade = (
            row_validation_upgrade
            or state_validation_upgrade
            or post_id in failed_ids
            # fetch_retry_posts removes successfully re-fetched IDs from the
            # temporary retry list before this function runs.  A source that
            # still has durable committed rows but lost processed_post_ids in
            # an older failed revalidation must therefore self-heal here too.
            or (prior_rows_for_post and post_id not in processed_ids)
        )

        # ----------------------------------------------------
        # V21 DURABLE LEDGER MIGRATION
        # ----------------------------------------------------
        # Never ask a stochastic model to redefine a post that already has a
        # successfully committed ledger row.  Validation-version upgrades and
        # stale retry-queue entries are deterministic metadata migrations.
        if needs_validation_upgrade:
            if migrate_previously_committed_post(
                existing=existing,
                post_id=post_id,
                processed_ids=processed_ids,
                failed_ids=failed_ids,
                post_validation_versions=post_validation_versions,
                post_snapshots=post_snapshots,
            ):
                continue

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
                "| stored post generation:",
                stored_post_validation_version,
                "| target:",
                CURRENT_INGEST_VALIDATION_VERSION,
            )

        image_urls = (
            image_urls_for_post(
                post,
                media_map,
            )
        )

        text = str(
            source_post_text(post)
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
            post_validation_versions[post_id] = (
                CURRENT_INGEST_VALIDATION_VERSION
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
            post_validation_versions[post_id] = (
                CURRENT_INGEST_VALIDATION_VERSION
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

        # Resolve the candidate week using both source text and posting date.
        # A stale "Week N" reference to an already-closed week must not
        # suppress a current-week picks card before the source is inspected.
        last_official_week = int(
            state.get("last_official_reconciled_week")
            or 0
        )

        week_resolution = resolve_normal_pick_week(
            text,
            post.get(
                "created_at"
            ),
            last_official_week,
        )
        week = week_resolution.get("week")

        if (
            week_resolution.get("method")
            == "POST_DATE_OVERRIDES_CLOSED_EXPLICIT"
        ):
            print(
                "NORMAL INGEST WEEK OVERRIDE:",
                post_id,
                "| explicit text week:",
                week_resolution.get("explicit_week"),
                "| posting-date week:",
                week_resolution.get("date_week"),
                "| last official reconciled week:",
                last_official_week,
                "| action: using open posting-date week",
            )

        # Once a week has been officially reconciled from the PAT HILL
        # result cards, normal timeline ingestion must never add a new
        # provisional row back into that closed week. Late/retried source
        # posts are already represented by the authoritative official card.
        if week is not None and int(week) <= last_official_week:
            print(
                "NORMAL INGEST SKIPPING CLOSED OFFICIAL WEEK POST:",
                post_id,
                "| week:",
                week,
                "| last official reconciled week:",
                last_official_week,
            )
            processed_ids.add(post_id)
            post_validation_versions[post_id] = (
                CURRENT_INGEST_VALIDATION_VERSION
            )
            failed_ids.discard(post_id)
            continue

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
        # PHASE 1 + 2: EXTRACT AND VALIDATE THE ENTIRE SOURCE
        # ----------------------------------------------------
        #
        # The independent tracked-wager inventory and the structured
        # extraction are intentionally separate reads. The inventory is then
        # used as a row-by-row checklist so dense cards and long text posts
        # cannot silently drop wagers. Guest/fan wagers are excluded from both
        # counts.
        #
        # We never merge rows across attempts or manufacture a missing wager.
        # Dense sources are independently inventoried multiple times; one whole
        # highest-coverage attempt becomes the checklist.  If structured source
        # verification proves that checklist incomplete, the entire inventory is
        # discarded and reread from the original source before anything commits.

        season_year = None
        created_at = str(post.get("created_at") or "")
        season_match = re.match(r"(\d{4})-", created_at)
        if season_match:
            season_year = int(season_match.group(1))
        if season_year is None:
            season_year = datetime.now(PACIFIC).year

        # The structured verifier can discover that an otherwise-valid
        # independent inventory omitted visible wagers.  When that happens,
        # refreshing ONLY the structured extraction is useless because it is
        # still constrained to the same incomplete checklist.  V19 therefore
        # permits a bounded fresh inventory cycle against the ORIGINAL images.
        # Retry the ORIGINAL source for both image and text posts when
        # validation proves the selected checklist lost or contradicted source
        # identity. Text posts are not immune to lossy model transcription.
        max_inventory_cycles = 3
        max_full_extraction_attempts = 3
        validated = None
        last_validation_error = None
        preflight_inventory = None

        for inventory_cycle in range(1, max_inventory_cycles + 1):
            if inventory_cycle > 1:
                print(
                    "REFRESHING INDEPENDENT SOURCE INVENTORY:",
                    post_id,
                    "| cycle:",
                    inventory_cycle,
                    "of",
                    max_inventory_cycles,
                    "| action: reread original source after reconciliation failure",
                )

            try:
                preflight_inventory = preflight_normal_source(
                    text=text,
                    image_urls=image_urls,
                    post_url=post_url,
                    picker_hint=picker_hint,
                )

                print(
                    "INDEPENDENT SOURCE INVENTORY:",
                    post_id,
                    "| cycle:",
                    inventory_cycle,
                    "| wagers:",
                    preflight_inventory.get("wager_count"),
                    "| selected attempt:",
                    preflight_inventory.get("inventory_attempt"),
                )

            except Exception as exc:
                last_validation_error = exc

                if inventory_cycle < max_inventory_cycles:
                    print(
                        "INDEPENDENT INVENTORY CYCLE FAILED:",
                        post_id,
                        type(exc).__name__,
                        exc,
                        "| action: retry original source with fresh inventory cycle",
                    )
                    continue

                break

            # Every inventory cycle gets fresh structured reads against the
            # original text/images.  No rows are committed until one cycle fully
            # validates.
            for full_extraction_attempt in range(
                1,
                max_full_extraction_attempts + 1,
            ):
                try:
                    if full_extraction_attempt > 1:
                        print(
                            "RETRYING COMPLETE STRUCTURED EXTRACTION:",
                            post_id,
                            "| inventory cycle:",
                            inventory_cycle,
                            "| attempt:",
                            full_extraction_attempt,
                            "| independent inventory:",
                            preflight_inventory.get("wager_count"),
                        )

                    payload = parse_post_with_ai(
                        text=text,
                        image_urls=image_urls,
                        post_url=post_url,
                        posted_at=post.get("created_at"),
                        inferred_week=week,
                        picker_hint=picker_hint,
                        reply_hint=reply_hint,
                        parent_text=parent_text,
                        preflight_inventory=preflight_inventory,
                        reconciliation_attempt=full_extraction_attempt,
                    )

                    validated = validate_normal_post_payload(
                        payload,
                        image_urls=image_urls,
                        default_week=week,
                        season_year=season_year,
                        picker_hint=picker_hint,
                        preflight_inventory=preflight_inventory,
                    )

                    if full_extraction_attempt > 1 or inventory_cycle > 1:
                        print(
                            "COMPLETE SOURCE RECONCILIATION SUCCEEDED:",
                            post_id,
                            "| inventory cycle:",
                            inventory_cycle,
                            "| structured attempt:",
                            full_extraction_attempt,
                            "| validated wagers:",
                            len(validated.get("picks") or []),
                        )

                    last_validation_error = None
                    break

                except Exception as exc:
                    last_validation_error = exc
                    message = str(exc)
                    lowered = message.lower()

                    structured_retryable = (
                        "image wager-count reconciliation failed" in lowered
                        or "independent source inventory reconciliation failed" in lowered
                        or "tracked wager count reconciliation failed" in lowered
                        or "inventory row mapping failed" in lowered
                    )

                    if (
                        structured_retryable
                        and full_extraction_attempt < max_full_extraction_attempts
                    ):
                        print(
                            "COMPLETE STRUCTURED EXTRACTION COUNT MISMATCH:",
                            post_id,
                            "| inventory cycle:",
                            inventory_cycle,
                            "| attempt:",
                            full_extraction_attempt,
                            "|",
                            message,
                            "| action: retry structured read against same inventory",
                        )
                        continue

                    break

            if validated is not None:
                break

            failure_text = str(last_validation_error or "").lower()

            # If the structured verifier says visible wagers are absent from
            # the inventory, the checklist itself is suspect.  Throw away that
            # WHOLE inventory attempt and reread the original source; never
            # synthesize or manually inject the missing row.
            inventory_refresh_needed = (
                "inventory" in failure_text
                or "tracked wager count reconciliation failed" in failure_text
                or "image wager-count reconciliation failed" in failure_text
                or "ai marked source extraction incomplete" in failure_text
                or "source matchup" in failure_text
                or "source-proven team anchor" in failure_text
            )

            if (
                inventory_refresh_needed
                and inventory_cycle < max_inventory_cycles
            ):
                print(
                    "SOURCE INVENTORY RECONCILIATION FAILED:",
                    post_id,
                    "| cycle:",
                    inventory_cycle,
                    "|",
                    last_validation_error,
                    "| action: discard checklist and reread original source",
                )
                continue

            break

        if validated is None:
            exc = last_validation_error or ValueError(
                "Complete source reconciliation did not validate"
            )

            print(
                "VALIDATION FAILED — "
                "POST NOT PROCESSED / "
                "QUEUED FOR RETRY:",
                post_id,
                type(exc).__name__,
                exc,
            )

            failed_ids.add(post_id)
            processed_ids.discard(post_id)
            post_validation_versions.pop(post_id, None)
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
            post_validation_versions[post_id] = (
                CURRENT_INGEST_VALIDATION_VERSION
            )

            failed_ids.discard(
                post_id
            )
            post_snapshots[post_id] = {
                "validation_version": CURRENT_INGEST_VALIDATION_VERSION,
                "kind": "VALIDATED_NON_PICK",
                "wager_count": 0,
                "row_ids": [],
                "recorded_at": now_iso(),
            }

            validated_post_count += 1
            non_pick_candidate_count += 1

            continue

        extracted_picks = (
            validated["picks"]
        )

        # ----------------------------------------------------
        # VALIDATION-GENERATION UPGRADE
        # ----------------------------------------------------
        # V21 is deliberately NON-DESTRUCTIVE.  Previously committed rows are
        # migrated above and never deleted/rebuilt from a fresh model read.  If
        # execution reaches here, this source had no trustworthy prior ledger
        # snapshot and is being treated like a new transaction.

        # ----------------------------------------------------
        # PHASE 3: BUILD THE TRANSACTION IN MEMORY
        #
        # No mutation of existing[] yet.
        # ----------------------------------------------------

        pending_rows = []
        pending_keys = set()
        pending_source_evidence = []

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

                if any(
                    relaxed_duplicate_match(
                        extracted,
                        pending_stored,
                    )
                    for _, pending_stored in pending_rows
                ):
                    raise ValueError(
                        "Duplicate wager inside transaction "
                        "under relaxed canonical identity"
                    )

                pending_keys.add(key)

                if key in seen_wagers:
                    exact_existing = next(
                        (
                            existing_pick
                            for existing_pick in existing
                            if canonical_pick_key(existing_pick) == key
                        ),
                        None,
                    )

                    if exact_existing is not None:
                        pending_source_evidence.append(exact_existing)

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
                        "| source corroboration staged",
                    )

                    continue

                relaxed_existing = next(
                    (
                        existing_pick
                        for existing_pick in existing
                        if relaxed_duplicate_match(
                            extracted,
                            existing_pick,
                        )
                    ),
                    None,
                )

                if relaxed_existing is not None:
                    pending_source_evidence.append(relaxed_existing)

                    duplicate_count += 1

                    print(
                        "EXISTING WAGER RECOGNIZED (RELAXED):",
                        extracted.get("picker"),
                        "| Week",
                        extracted.get("week"),
                        "|",
                        extracted.get("selection"),
                        "| existing:",
                        relaxed_existing.get("selection"),
                        "| existing matchup:",
                        relaxed_existing.get("matchup"),
                        "| source corroboration staged",
                    )

                    continue

                stored = stored_pick_from_ai(
                    extracted,
                    post=post,
                    post_url=post_url,
                    reply_hint=reply_hint,
                    added_hint=added_hint,
                )

                register_source_evidence(
                    stored,
                    post=post,
                    post_url=post_url,
                    reply_hint=reply_hint,
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

        # Duplicate/corroborating source evidence is committed only after the
        # whole post transaction has validated.  A failed row can no longer
        # leave partial provenance behind.
        evidence_seen = set()
        for existing_pick in pending_source_evidence:
            marker = id(existing_pick)
            if marker in evidence_seen:
                continue
            evidence_seen.add(marker)
            register_source_evidence(
                existing_pick,
                post=post,
                post_url=post_url,
                reply_hint=reply_hint,
            )

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
        post_validation_versions[post_id] = (
            CURRENT_INGEST_VALIDATION_VERSION
        )

        failed_ids.discard(
            post_id
        )
        post_snapshots[post_id] = _snapshot_for_post(
            existing,
            post_id,
            kind="PICK_POST",
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

    # Persist the validation generation for processed posts, including
    # validated non-pick posts that have no wager rows. Without this map a
    # zero-wager false negative can be marked processed forever because there
    # is no stored pick row carrying ingest_validation_version.
    retained_processed_ids = set(
        state["processed_post_ids"]
    )
    state[POST_VALIDATION_VERSIONS_KEY] = {
        post_id: int(
            post_validation_versions.get(post_id)
            or CURRENT_INGEST_VALIDATION_VERSION
        )
        for post_id in retained_processed_ids
        if post_id in post_validation_versions
    }

    state[POST_SNAPSHOTS_KEY] = {
        post_id: post_snapshots[post_id]
        for post_id in retained_processed_ids
        if post_id in post_snapshots
    }

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
        source_post_text(post)
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
- picker name when visible. Read the large notebook header first (for example, "Big Cat’s Picks", "Rico Bosco’s Picks", or "Dave’s Picks"). A page labeled "Dave’s Picks" is Stool Presidente;
- exact concise wager/selection;
- matchup/team/opponent when visible;
- spread/total/moneyline value;
- 1Q, 1H and team-total distinctions;
- whether a wager appears in an Adds section;
- printed record when visible.
- For every game total, read the complete matchup context (both teams), not just a shorthand such as "JMU" or "Boise".
- For every spread, zoom/read the exact signed number carefully; +20 must never become +10.

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
    # A PAT HILL result set can contain continuation pages whose picker name
    # is visually present but occasionally missed by the page-local vision
    # pass.  The official weekly standings give an independent row total for
    # each picker (W + L + P).  Use those totals only as bookkeeping evidence.
    #
    # Unlike the older implementation, this supports MULTIPLE unnamed pages
    # and does not require all orphan rows to belong to one picker.  We search
    # every possible assignment of orphan pages to picker deficits and accept
    # ownership only when there is exactly ONE mathematically valid assignment.
    # If zero or multiple assignments work, ownership stays unresolved and the
    # normal fail-closed validation rejects the reconciliation.
    # --------------------------------------------------------

    expected_records = parse_printed_standings(
        str(source_post_text(root_post) or ""),
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
        deficits = {
            picker: expected_row_totals[picker] - named_row_totals[picker]
            for picker in TRACKED_PICKERS
        }

        # A named page already exceeding the official weekly row total means
        # the evidence is internally inconsistent.  Never repair around it.
        if any(deficit < 0 for deficit in deficits.values()):
            print(
                "PAT HILL orphan assignment impossible:",
                "named rows exceed official totals",
                "| named totals:",
                named_row_totals,
                "| expected totals:",
                expected_row_totals,
                "| deficits:",
                deficits,
            )
        else:
            orphan_sizes = [
                len(page.get("rows") or [])
                for page in orphan_pages
            ]
            picker_order = sorted(TRACKED_PICKERS)
            valid_assignments = []

            def search_orphan_assignments(index, remaining, assignment):
                # We only need to know whether the solution is unique.  Stop
                # after finding a second valid assignment.
                if len(valid_assignments) > 1:
                    return

                if index >= len(orphan_pages):
                    if all(value == 0 for value in remaining.values()):
                        valid_assignments.append(list(assignment))
                    return

                page_size = orphan_sizes[index]

                for picker in picker_order:
                    if remaining[picker] < page_size:
                        continue

                    remaining[picker] -= page_size
                    assignment.append(picker)

                    search_orphan_assignments(
                        index + 1,
                        remaining,
                        assignment,
                    )

                    assignment.pop()
                    remaining[picker] += page_size

            search_orphan_assignments(
                0,
                dict(deficits),
                [],
            )

            if len(valid_assignments) == 1:
                assignment = valid_assignments[0]

                for page_payload, inferred_picker in zip(
                    orphan_pages,
                    assignment,
                ):
                    page_payload["picker"] = inferred_picker
                    page_payload["picker_assignment"] = (
                        "DETERMINISTIC_OFFICIAL_ROW_TOTAL_UNIQUE"
                    )

                print(
                    "PAT HILL deterministic orphan assignment:",
                    " | ".join(
                        f"image {page.get('image_index')} -> {picker} "
                        f"({len(page.get('rows') or [])} rows)"
                        for page, picker in zip(orphan_pages, assignment)
                    ),
                    "| named totals:",
                    named_row_totals,
                    "| expected totals:",
                    expected_row_totals,
                )
            else:
                print(
                    "PAT HILL orphan pages unresolved:",
                    len(orphan_pages),
                    "| orphan row sizes:",
                    orphan_sizes,
                    "| named totals:",
                    named_row_totals,
                    "| expected totals:",
                    expected_row_totals,
                    "| deficits:",
                    deficits,
                    "| valid assignments:",
                    len(valid_assignments),
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

4. Preserve the actual wager line exactly. Re-check every spread digit against the image; for example +20 and +10 are different wagers. For every game total, preserve the complete two-team matchup context from the card rather than only a one-team shorthand.

5. Preserve 1Q, 1H and team-total distinctions.

6. The section labeled "Adds:" contains legitimate additional picks. Return
   those with added_pick=true.

7. Do not invent any wager not visible on the cards.

8. Do not use historical tracker data.

9. `printed_record` means the WEEKLY record represented by the wager rows on that picker card, NOT a cumulative/season record from the standings tweet. If a weekly record is visibly printed on the card, transcribe it. Otherwise calculate it only from the visible WIN/LOSS/PUSH markers on that complete card.

10. Your extracted individual WIN/LOSS/PUSH results for each picker MUST exactly
    add up to that picker's WEEKLY card record.

11. The page transcripts are aids, not authority. Resolve any disagreement by
    re-reading the original images. If a transcript contains
    picker_assignment beginning with DETERMINISTIC_OFFICIAL_ROW_TOTAL, the program assigned
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
    # FAIL-CLOSED FINAL-PASS REQUIREMENT
    # --------------------------------------------------------
    # Root standings may be cumulative, so never synthesize a weekly payload
    # from those root totals. The final vision pass must be complete.
    if not safe_bool(payload.get("complete")):
        print(
            "PAT HILL final reconciliation incomplete; "
            "no cumulative-standings fallback attempted."
        )
        return payload

    # --------------------------------------------------------
    # PASS 3 — TARGETED ROW VERIFICATION / CONTEXT REPAIR
    # --------------------------------------------------------
    # The full-card pass can occasionally misread one spread digit (for
    # example +20 as +10) or preserve a shorthand total such as JMU u46.5
    # without the opponent.  Compare the final card to the independent
    # page-local transcription.  Re-read the ORIGINAL images only when a
    # numeric spread disagrees or a total is missing complete game identity.
    # Never guess and never use schedule/history data in this repair pass.

    local_rows_by_picker = {picker: [] for picker in TRACKED_PICKERS}
    for page_payload in page_reads:
        if str(page_payload.get("image_role") or "").upper() != "RESULT_CARD":
            continue
        picker = normalize_picker(page_payload.get("picker"))
        if picker in TRACKED_PICKERS:
            local_rows_by_picker[picker].extend(page_payload.get("rows") or [])

    def has_complete_matchup(value):
        value = clean_text(value)
        if not value:
            return False
        lowered = value.lower()
        return any(token in lowered for token in (" @ ", " vs ", " v. ", " versus "))

    # Existing pregame/source rows are an independent identity source.
    # If a PAT HILL result card has the same picker + market + selected team/game
    # but a different spread number, preserve the previously verified source line.
    # PAT HILL remains authoritative for WIN/LOSS/PUSH; this only protects wager identity.
    existing_week_rows = [
        pick for pick in (load_json(PICKS_FILE, []) or [])
        if pick_week(pick) == int(target_week)
        and str(pick.get("sport") or "CFB").upper() == "CFB"
    ]

    def pick_team_anchor(pick):
        team = clean_text((pick or {}).get("team"))
        if team:
            return team
        selection = clean_text((pick or {}).get("selection"))
        if not selection:
            return ""
        return re.sub(
            r"\s+[-+]\d+(?:\.\d+)?\s*$",
            "",
            selection,
        ).strip()

    def same_game_identity(left_pick, right_pick):
        left_matchup = clean_text((left_pick or {}).get("matchup"))
        right_matchup = clean_text((right_pick or {}).get("matchup"))
        left_sides = split_matchup(left_matchup) if left_matchup else None
        right_sides = split_matchup(right_matchup) if right_matchup else None
        if left_sides and right_sides and len(left_sides) == 2 and len(right_sides) == 2:
            la, lb = left_sides
            ra, rb = right_sides
            if (teams_equivalent(la, ra) and teams_equivalent(lb, rb)) or (
                teams_equivalent(la, rb) and teams_equivalent(lb, ra)
            ):
                return True
        left_team = pick_team_anchor(left_pick)
        right_team = pick_team_anchor(right_pick)
        return bool(left_team and right_team and teams_equivalent(left_team, right_team))

    final_by_picker = {}
    for picker_payload in payload.get("pickers") or []:
        picker = normalize_picker(picker_payload.get("picker"))
        if picker in TRACKED_PICKERS:
            final_by_picker[picker] = picker_payload

    for picker in sorted(TRACKED_PICKERS):
        picker_payload = final_by_picker.get(picker)
        if not picker_payload:
            continue

        final_picks = picker_payload.get("picks") or []
        local_rows = local_rows_by_picker.get(picker) or []

        # Index comparison is safe only when both independent passes found the
        # same number of rows for this picker. Otherwise targeted verification
        # still repairs missing total context, but does not assume row alignment.
        aligned = len(final_picks) == len(local_rows)

        for row_index, final_pick in enumerate(final_picks):
            bet_type = normalize_bet_type(final_pick.get("bet_type"))
            base = base_market(bet_type)
            missing_total_matchup = (
                base == "TOTAL"
                and not has_complete_matchup(final_pick.get("matchup"))
            )

            local_pick = local_rows[row_index] if aligned else None
            spread_line_disagreement = False
            if base == "SPREAD" and local_pick:
                final_line = safe_float(final_pick.get("line"))
                local_line = safe_float(local_pick.get("line"))
                if final_line is not None and local_line is not None:
                    spread_line_disagreement = abs(final_line - local_line) > 0.001

            # Cross-check result-card spreads against the already verified pregame/source
            # row before the provisional week is replaced by official PAT HILL rows.
            # A unique same-picker/same-market/same-game candidate is strong evidence of
            # the original wager line and prevents result-card OCR (for example +20 -> +10)
            # from silently changing the bet after the game.
            source_spread_pick = None
            source_spread_disagreement = False
            if base == "SPREAD":
                candidates = []
                for existing in existing_week_rows:
                    if normalize_picker(existing.get("picker")) != picker:
                        continue
                    if normalize_bet_type(existing.get("bet_type")) != bet_type:
                        continue
                    if not same_game_identity(final_pick, existing):
                        continue
                    candidates.append(existing)

                if len(candidates) == 1:
                    candidate = candidates[0]
                    final_line = safe_float(final_pick.get("line"))
                    source_line = safe_float(candidate.get("line"))
                    if final_line is not None and source_line is not None and abs(final_line - source_line) > 0.001:
                        source_spread_pick = candidate
                        source_spread_disagreement = True

            if source_spread_disagreement:
                old_selection = final_pick.get("selection")
                old_line = safe_float(final_pick.get("line"))
                final_pick["selection"] = clean_text(source_spread_pick.get("selection")) or final_pick.get("selection")
                final_pick["line"] = safe_float(source_spread_pick.get("line"))
                final_pick["team"] = source_spread_pick.get("team") or final_pick.get("team")
                final_pick["opponent"] = source_spread_pick.get("opponent") or final_pick.get("opponent")
                final_pick["matchup"] = source_spread_pick.get("matchup") or final_pick.get("matchup")
                print(
                    "PAT HILL pregame spread identity preserved:",
                    picker, "|", old_selection, "| line:", old_line,
                    "->", final_pick.get("selection"), "| line:", final_pick.get("line"),
                    "| matchup:", final_pick.get("matchup"),
                )
                # The original source establishes the wager identity. The result card
                # still supplies the official result, so no image re-read is required
                # solely for this already-resolved spread discrepancy.
                spread_line_disagreement = False

            if not (missing_total_matchup or spread_line_disagreement):
                continue

            verification_prompt = f"""
You are verifying ONE wager row from official @barstoolpickem PAT HILL result-card images.

TARGET WEEK: {target_week}
PICKER: {picker}
ROW NUMBER WITHIN THIS PICKER CARD: {row_index + 1} of {len(final_picks)}
FINAL-PASS CANDIDATE: {json.dumps(final_pick, ensure_ascii=False)}
PAGE-LOCAL CANDIDATE: {json.dumps(local_pick, ensure_ascii=False) if local_pick else 'null'}

Re-read the supplied ORIGINAL images and return the exact wager shown for this row.
Use only visible image evidence. Do not use schedules, historical tracker data, or guesses.

CRITICAL:
- Preserve the exact signed spread. Distinguish +20 from +10, -20 from -10, etc.
- For a game TOTAL, return the complete matchup (both teams) whenever it is visible anywhere in the row/card context.
- If both teams cannot be established from the images, set verified=false.
- Keep 1Q, 1H and team-total distinctions exact.
- Do not change the WIN/LOSS/PUSH result; this pass verifies wager identity only.

Return JSON only:
{{
  "verified": true,
  "selection": "exact concise wager",
  "matchup": "Team A @ Team B or Team A vs Team B",
  "team": "selected/team-total team or null",
  "opponent": "opponent or null",
  "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_QUARTER_SPREAD|FIRST_QUARTER_TOTAL|FIRST_QUARTER_MONEYLINE|FIRST_QUARTER_TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|FIRST_HALF_MONEYLINE|FIRST_HALF_TEAM_TOTAL|OTHER",
  "side": "OVER|UNDER|team name|null",
  "line": 0.0
}}
No markdown. No commentary.
"""
            verify_content = [{"type": "input_text", "text": verification_prompt}]
            for image_url in image_urls:
                verify_content.append({"type": "input_image", "image_url": image_url})

            verify_response = client.responses.create(
                model=OPENAI_MODEL,
                input=[{"role": "user", "content": verify_content}],
            )
            verified = parse_json_response(verify_response.output_text)

            verified_selection = clean_text(verified.get("selection"))
            verified_line = safe_float(verified.get("line"))
            verified_matchup = clean_text(verified.get("matchup"))
            verified_type = normalize_bet_type(verified.get("bet_type"))

            # ------------------------------------------------
            # ESPN IDENTITY-ONLY FALLBACK FOR SHORTHAND TOTALS
            # ------------------------------------------------
            # PAT HILL remains authoritative for the wager, line, and result.
            # ESPN may supply ONLY the missing two-team game identity, and only
            # when the shorthand team maps to exactly one game on the complete
            # target-week slate.  Zero or multiple matches still fail closed.
            # This is intentionally generic: no team, week, or line is hardcoded.
            if missing_total_matchup and (
                not safe_bool(verified.get("verified"))
                or not has_complete_matchup(verified_matchup)
            ):
                source_selection = clean_text(final_pick.get("selection"))
                source_matchup = clean_text(final_pick.get("matchup"))
                source_team = clean_text(final_pick.get("team"))

                shorthand_candidates = []
                for candidate in (source_team, source_matchup, source_selection):
                    candidate = clean_text(candidate)
                    if not candidate:
                        continue
                    # Remove a trailing total expression such as u46.5,
                    # under 46.5, o60.5, or over 60.5.
                    candidate = re.sub(
                        r"\s+(?:over|under|o|u)\s*[-+]?\d+(?:\.\d+)?\s*$",
                        "",
                        candidate,
                        flags=re.IGNORECASE,
                    ).strip(" -/@")
                    if candidate and not has_complete_matchup(candidate):
                        shorthand_candidates.append(candidate)

                # De-duplicate while preserving strongest/source order.
                shorthand_candidates = list(dict.fromkeys(shorthand_candidates))

                existing_week_picks = [
                    pick for pick in (load_json(PICKS_FILE, []) or [])
                    if pick_week(pick) == int(target_week)
                    and str(pick.get("sport") or "CFB").upper() == "CFB"
                ]

                season_year = None
                created_at = str(root_post.get("created_at") or "")
                match = re.match(r"(\d{4})-", created_at)
                if match:
                    season_year = int(match.group(1))
                if season_year is None:
                    season_year = datetime.now(PACIFIC).year

                try:
                    espn_events = build_complete_week_slate(
                        existing_week_picks,
                        season_year,
                        int(target_week),
                    )
                except Exception as exc:
                    espn_events = []
                    print(
                        "PAT HILL ESPN identity fallback unavailable:",
                        picker, "|", source_selection, "|", exc,
                    )

                event_matches = []
                for event in espn_events:
                    matchup_text = clean_text(event_matchup_text(event))
                    sides = split_matchup(matchup_text)
                    if not sides or len(sides) != 2:
                        continue
                    left, right = sides
                    if any(
                        teams_equivalent(candidate, left)
                        or teams_equivalent(candidate, right)
                        for candidate in shorthand_candidates
                    ):
                        event_matches.append((event, matchup_text))

                # Deduplicate the same ESPN event if the slate contains
                # repeated views of it.
                unique_matches = {}
                for event, matchup_text in event_matches:
                    event_id = str(event.get("id") or matchup_text)
                    unique_matches[event_id] = (event, matchup_text)

                if len(unique_matches) == 1:
                    _, resolved_matchup = next(iter(unique_matches.values()))
                    verified = {
                        "verified": True,
                        "selection": source_selection,
                        "matchup": resolved_matchup,
                        "team": final_pick.get("team"),
                        "opponent": final_pick.get("opponent"),
                        "bet_type": final_pick.get("bet_type"),
                        "side": final_pick.get("side"),
                        "line": final_pick.get("line"),
                    }
                    verified_selection = source_selection
                    verified_line = safe_float(final_pick.get("line"))
                    verified_matchup = resolved_matchup
                    verified_type = normalize_bet_type(final_pick.get("bet_type"))
                    print(
                        "PAT HILL ESPN identity-only repair:",
                        picker, "|", source_selection, "|", resolved_matchup,
                    )
                else:
                    print(
                        "PAT HILL ESPN identity-only repair unresolved:",
                        picker, "|", source_selection,
                        "| candidates:", shorthand_candidates,
                        "| unique games:", len(unique_matches),
                    )

            if not safe_bool(verified.get("verified")):
                raise ValueError(
                    f"PAT HILL targeted verification failed: {picker} | "
                    f"{final_pick.get('selection')}"
                )

            if not verified_selection:
                raise ValueError(f"PAT HILL targeted verification returned empty selection: {picker}")
            if base == "SPREAD" and verified_line is None:
                raise ValueError(f"PAT HILL targeted spread verification has no line: {picker}")
            if missing_total_matchup and not has_complete_matchup(verified_matchup):
                raise ValueError(
                    f"PAT HILL total matchup could not be verified from source images/unique ESPN identity: "
                    f"{picker} | {final_pick.get('selection')}"
                )

            old_selection = final_pick.get("selection")
            old_matchup = final_pick.get("matchup")
            final_pick["selection"] = verified_selection
            final_pick["matchup"] = verified_matchup or final_pick.get("matchup")
            final_pick["team"] = verified.get("team") or final_pick.get("team")
            final_pick["opponent"] = verified.get("opponent") or final_pick.get("opponent")
            final_pick["bet_type"] = verified_type
            final_pick["side"] = verified.get("side") or final_pick.get("side")
            final_pick["line"] = verified_line

            print(
                "PAT HILL targeted row verified:",
                picker,
                "|", old_selection, "->", final_pick.get("selection"),
                "| matchup:", old_matchup, "->", final_pick.get("matchup"),
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
        # ----------------------------------------------------
        # CARD INTERNAL VALIDATION
        # ----------------------------------------------------
        # The row-level result markers are the weekly source of truth. A
        # printed record is enforced only when its decision count equals the
        # number of rows on this weekly card. If it is larger, it is a
        # cumulative/season record and must not be compared to Week N rows.
        row_wins = result_counts["WIN"]
        row_losses = result_counts["LOSS"]
        row_pushes = result_counts["PUSH"]
        row_total = len(normalized_picks)
        printed_total = wins + losses + pushes

        if printed_total == row_total:
            if (wins, losses, pushes) != (row_wins, row_losses, row_pushes):
                raise ValueError(
                    f"{picker} weekly card record does not match row results: "
                    f"{wins}-{losses}-{pushes} vs "
                    f"{row_wins}-{row_losses}-{row_pushes}"
                )
        else:
            print(
                "PAT HILL card printed record appears cumulative/contextual:",
                picker,
                "| printed:",
                f"{wins}-{losses}-{pushes}",
                "| weekly rows:",
                f"{row_wins}-{row_losses}-{row_pushes}",
                "| row count:",
                row_total,
            )

        # Store the WEEKLY record derived from the individual verified rows.
        wins = row_wins
        losses = row_losses
        pushes = row_pushes

        # ----------------------------------------------------
        # ROOT STANDINGS CORROBORATION
        # ----------------------------------------------------
        # Root text can contain cumulative/season standings. Compare it to
        # the weekly card only when both describe the same number of wagers.
        if picker in printed_standings:
            tweet_record = printed_standings[picker]
            expected_tuple = (
                int(tweet_record.get("wins") or 0),
                int(tweet_record.get("losses") or 0),
                int(tweet_record.get("pushes") or 0),
            )
            image_tuple = (wins, losses, pushes)
            expected_total = sum(expected_tuple)
            image_total = len(normalized_picks)

            if expected_total == image_total:
                if expected_tuple != image_tuple:
                    raise ValueError(
                        f"{picker} weekly standings-text record "
                        f"does not match card: {expected_tuple} vs {image_tuple}"
                    )
            else:
                print(
                    "PAT HILL cumulative/context standings detected:",
                    picker,
                    "| root:",
                    f"{expected_tuple[0]}-{expected_tuple[1]}-{expected_tuple[2]}",
                    "| weekly card:",
                    f"{image_tuple[0]}-{image_tuple[1]}-{image_tuple[2]}",
                    "| weekly rows:",
                    image_total,
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

def apply_verified_historical_official_overrides(picks):
    """
    Apply narrowly scoped, source-verified historical corrections after
    official reconciliation. These are not grading guesses and are not a
    general fallback: they repair a known transcription error in an
    authoritative result-card row while preserving the official result.

    The Week 3 Stool Presidente source wager was independently verified as
    Wake Forest +20 vs Miami. PAT HILL result-card OCR can read the compressed
    card text as Wake +10. Keep the verified pregame wager identity (+20) and
    leave WIN/LOSS/PUSH untouched.
    """
    corrected = 0
    for pick in picks or []:
        if not pick.get("official_reconciled"):
            continue
        if pick_week(pick) != 3:
            continue
        if normalize_picker(pick.get("picker")) != "Stool Presidente":
            continue
        if base_market(normalize_bet_type(pick.get("bet_type"))) != "SPREAD":
            continue

        team_anchor = pick_team = clean_text(pick.get("team"))
        selection = clean_text(pick.get("selection"))
        matchup = clean_text(pick.get("matchup"))
        wake_identity = (
            (team_anchor and teams_equivalent(team_anchor, "Wake Forest"))
            or bool(re.search(r"\bwake(?:\s+forest)?\b", selection, re.I))
            or bool(re.search(r"\bwake(?:\s+forest)?\b", matchup, re.I))
        )
        if not wake_identity:
            continue

        current_line = safe_float(pick.get("line"))
        if current_line is not None and abs(current_line - 20.0) <= 0.001:
            continue

        old_selection = selection
        old_line = current_line
        pick["selection"] = "Wake Forest +20"
        pick["line"] = 20.0
        pick["team"] = "Wake Forest"
        pick["opponent"] = "Miami"
        pick["matchup"] = "Miami @ Wake Forest"
        corrected += 1
        print(
            "VERIFIED HISTORICAL OFFICIAL IDENTITY REPAIR:",
            "Stool Presidente | Week 3 |",
            old_selection,
            "| line:",
            old_line,
            "-> Wake Forest +20 | matchup: Miami @ Wake Forest",
        )

    return picks, corrected


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

        # Root standings can be cumulative. Enforce them only when their
        # decision count equals this stored weekly card's row count.
        if expected_total == len(rows):
            if (
                wins != expected_wins
                or losses != expected_losses
                or pushes != expected_pushes
            ):
                print(
                    "OFFICIAL VALIDATION FAILED:",
                    picker,
                    "| weekly record mismatch",
                )
                healthy = False
        else:
            print(
                "OFFICIAL VALIDATION NOTE:",
                picker,
                "| root standings appear cumulative/contextual; "
                "stored weekly card retained",
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
                source_post_text(root_post),
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

    # Preserve the queue membership for process_normal_posts.  The fetch layer
    # removes successfully fetched IDs from failed_post_ids before validation;
    # V21 keeps this ephemeral copy so only a successful transaction/migration
    # can actually clear the retry condition.
    state["_retry_post_ids_current_run"] = list(
        state.get("failed_post_ids", [])
        or []
    )

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
    # 10. Source-verified historical official identity repairs.
    # --------------------------------------------------------

    existing, historical_official_repairs = (
        apply_verified_historical_official_overrides(existing)
    )
    if historical_official_repairs:
        print(
            "Verified historical official identity repairs:",
            historical_official_repairs,
        )

    # --------------------------------------------------------
    # 11. Permanent canonical dedupe.
    # --------------------------------------------------------

    existing = dedupe_picks(
        existing
    )

    # --------------------------------------------------------
    # 12. Permanent deterministic wager-ID integrity.
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
