from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from common import (
    load_json,
    save_json,
    PICKERS,
    PICKS_FILE,
    STATE_FILE,
    now_iso,
    normalize_picker,
)

X_API = "https://api.x.com/2"
USERNAME = os.getenv("X_USERNAME", "barstoolpickem")

BACKFILL_DAYS = 8
MAX_BACKFILL_PAGES = 3


def x_get(path, params=None):
    token = os.environ["X_BEARER_TOKEN"]

    response = requests.get(
        X_API + path,
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def resolve_user_id():
    payload = x_get(f"/users/by/username/{USERNAME}")
    return payload["data"]["id"]


def iso_x_time(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_posts(user_id, since_id=None, backfill=False):
    """
    Normal runs:
        Fetch only posts newer than the last processed X post.

    Backfill:
        Fetch up to the last 8 days and paginate through several pages.
        This is used when picks.json is still empty so Week 1 can be recovered
        even if an earlier unsuccessful run already advanced last_x_post_id.
    """

    params = {
        "max_results": 100,
        "exclude": "retweets",
        "tweet.fields": "created_at,attachments,text,referenced_tweets",
        "expansions": "attachments.media_keys",
        "media.fields": "media_key,type,url,preview_image_url",
    }

    if backfill:
        params["start_time"] = iso_x_time(
            datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
        )
    elif since_id:
        params["since_id"] = since_id

    all_posts = []
    all_media = {}

    pages = 0

    while True:
        payload = x_get(f"/users/{user_id}/tweets", params)

        all_posts.extend(payload.get("data", []))

        for media in payload.get("includes", {}).get("media", []):
            if media.get("media_key"):
                all_media[media["media_key"]] = media

        pages += 1

        next_token = payload.get("meta", {}).get("next_token")

        if not next_token:
            break

        if not backfill:
            break

        if pages >= MAX_BACKFILL_PAGES:
            break

        params["pagination_token"] = next_token

    return all_posts, all_media


def infer_week_from_date(created_at):
    """
    2026 Week 1 begins around the week containing September 1.
    This also gives us a predictable fallback for later additions that do not
    explicitly say 'Week 1', 'Week 2', etc.

    Example:
        Aug 31 - Sep 6, 2026 => Week 1
        Sep 7 - Sep 13, 2026 => Week 2
    """

    if not created_at:
        return None

    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return None

    year = dt.year

    september_first = datetime(year, 9, 1, tzinfo=timezone.utc)

    week_one_monday = september_first - timedelta(
        days=september_first.weekday()
    )

    delta_days = (dt - week_one_monday).days

    if delta_days < -7:
        return None

    if delta_days < 0:
        return 1

    return (delta_days // 7) + 1


def normalize_text(value):
    value = str(value or "").lower()

    value = (
        value.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9+.\- ]", "", value)

    return value.strip()


def looks_like_pick_post(text, image_urls):
    """
    Avoid sending completely unrelated Barstool Pick Em posts to OpenAI.
    Image posts are always inspected because the weekly cards are images.
    """

    if image_urls:
        return True

    t = str(text or "").lower()

    keywords = [
        "adds for",
        "add for",
        "mortal lock",
        "over ",
        "under ",
        "moneyline",
        " ml",
        "team total",
        "first half",
        "1h",
    ]

    if any(keyword in t for keyword in keywords):
        return True

    # Examples:
    # Cal +2.5
    # UNLV -3
    # Central Michigan +11.5
    if re.search(r"\b[a-z][a-z .&'-]{1,35}\s[+-]\d+(?:\.\d+)?\b", t):
        return True

    return False


def picker_hint_from_text(text):
    t = str(text or "").lower()

    if "barstoolbigcat" in t or "big cat" in t or "bigcat" in t:
        return "Big Cat"

    if (
        "stoolpresidente" in t
        or "stool presidente" in t
        or "dave portnoy" in t
        or "portnoy" in t
        or "el pres" in t
    ):
        return "Stool Presidente"

    if "ricobosco" in t or "rico bosco" in t or "rico" in t:
        return "Rico Bosco"

    return None


def is_added_post(text):
    t = str(text or "").lower()

    added_terms = [
        "adds for",
        "add for",
        "added pick",
        "adding",
        "addition",
        "another one",
    ]

    return any(term in t for term in added_terms)


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
You are extracting gambling picks from the official Barstool Pick Em X account.

Only track picks belonging to these three people:

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
      "bet_type": "SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|OTHER",
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

1. Extract only actual explicit wagers. Never invent a pick.

2. A weekly card may contain MANY wagers. Extract every wager visible in every image.

3. Multiple images may belong to different Pick Em personalities.
   Read the card heading and correctly attribute each wager.

4. Common picker labels:
   - "Big Cat's Picks" = Big Cat
   - Dave / Pres / El Pres / Portnoy = Stool Presidente
   - Rico = Rico Bosco

5. Ignore:
   - record lines such as "Record 0-0-0"
   - kickoff times
   - promotional text
   - DraftKings branding
   - commentary
   - jokes
   - historical records
   - final scores
   - recaps that do not announce a new wager

6. Later X posts frequently look like:
   "Adds for @BarstoolBigCat"
   followed by one or more wagers.

   Every wager in such a post should:
   - be assigned to that picker
   - have added_pick = true

7. Sometimes an additional wager is posted by itself, for example:
   "central michigan +11.5"

   If the surrounding post context clearly identifies the picker, extract it.

8. Use the supplied likely week ({inferred_week}) when the post itself does not
   explicitly state a week and the date makes the active Pick Em week clear.

9. For spreads:
   Example "Cal +2.5"
   bet_type = SPREAD
   team = "Cal"
   side = "Cal"
   line = 2.5
   selection = "Cal +2.5"

10. For game totals:
    Example "FIU/USF Over 53.5"
    bet_type = TOTAL
    matchup = "FIU/USF"
    side = "OVER"
    line = 53.5
    selection = "FIU/USF Over 53.5"

11. A shorthand total such as:
    "Texas A&M Over 53.5"
    may identify the game using one team name.
    Do not automatically call it a TEAM_TOTAL unless the post explicitly says
    team total or TT.

12. For team totals:
    Example "Indiana first half TT over 27.5"
    bet_type = FIRST_HALF_TOTAL
    team = "Indiana"
    side = "OVER"
    line = 27.5

13. FIRST HALF wagers must remain clearly identified as first-half wagers.
    Do not convert them into full-game wagers.

14. Mortal Lock:
    mortal_lock = true ONLY when the card/post explicitly identifies that wager
    as the Mortal Lock.
    Do not guess based on placement.

15. Preserve posted lines exactly.
    Convert unicode minus signs to normal numeric negatives.

16. Default:
    units = 1
    odds = null unless explicitly shown

17. Confidence:
    0.95-1.00 = clearly readable and confidently attributed
    below 0.90 = anything uncertain about picker, wager, line, or matchup

18. If there are no actual new picks in the post:
    return {{"picks":[]}}

JSON only. No markdown.
"""

    content = [{"type": "input_text", "text": prompt}]

    for image_url in image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": image_url,
                "detail": "high",
            }
        )

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )

    raw = response.output_text.strip()

    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw,
        flags=re.S,
    )

    parsed = json.loads(raw)

    return parsed.get("picks", [])


def canonical_pick_key(pick):
    """
    Cross-post duplicate protection.

    This intentionally does NOT include the X post ID, so if Barstool repeats
    the same wager in another post it will not become a second counted pick.
    """

    picker = normalize_picker(pick.get("picker")) or ""
    week = pick.get("week") or ""
    bet_type = normalize_text(pick.get("bet_type"))
    matchup = normalize_text(pick.get("matchup"))
    team = normalize_text(pick.get("team"))
    side = normalize_text(pick.get("side"))

    line = pick.get("line")

    try:
        if line is not None:
            line = float(line)

            if line.is_integer():
                line = int(line)
    except Exception:
        pass

    selection = normalize_text(pick.get("selection"))

    # Prefer structured fields when available.
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

    # If AI could not structure enough of the wager, selection gives us a
    # fallback identity.
    if not matchup and not team:
        structured += "|" + selection

    return structured


def stable_id(canonical_key):
    return hashlib.sha1(canonical_key.encode()).hexdigest()[:16]


def existing_keys(existing):
    keys = set()

    for pick in existing:
        copy = dict(pick)

        if not copy.get("week"):
            copy["week"] = infer_week_from_date(copy.get("posted_at"))

        keys.add(canonical_pick_key(copy))

    return keys


def ingest():
    state = load_json(
        STATE_FILE,
        {
            "last_x_post_id": None,
            "backfill_complete": False,
        },
    )

    existing = load_json(PICKS_FILE, [])

    # Important:
    # The earlier run may have advanced last_x_post_id even though it extracted
    # zero picks. If picks.json is empty, force the Week 1 backfill.
    should_backfill = (
        not existing
        or not state.get("backfill_complete")
    )

    uid = resolve_user_id()

    posts, mmap = fetch_posts(
        uid,
        since_id=None if should_backfill else state.get("last_x_post_id"),
        backfill=should_backfill,
    )

    print(
        f"Fetched {len(posts)} X posts "
        f"({'backfill' if should_backfill else 'incremental'} mode)"
    )

    seen_keys = existing_keys(existing)

    added_count = 0
    candidate_count = 0

    for post in sorted(posts, key=lambda item: int(item["id"])):
        media_keys = post.get("attachments", {}).get("media_keys", [])

        image_urls = []

        for media_key in media_keys:
            media = mmap.get(media_key, {})

            if media.get("type") == "photo" and media.get("url"):
                image_urls.append(media["url"])

        text = post.get("text", "")

        if not looks_like_pick_post(text, image_urls):
            continue

        candidate_count += 1

        post_url = (
            f"https://x.com/{USERNAME}/status/{post['id']}"
        )

        inferred_week = infer_week_from_date(
            post.get("created_at")
        )

        picker_hint = picker_hint_from_text(text)

        added_hint = is_added_post(text)

        print(
            f"Parsing candidate post {post['id']} "
            f"images={len(image_urls)} "
            f"picker_hint={picker_hint} "
            f"week={inferred_week}"
        )

        try:
            extracted = parse_post_with_ai(
                text=text,
                image_urls=image_urls,
                post_url=post_url,
                posted_at=post.get("created_at"),
                inferred_week=inferred_week,
                picker_hint=picker_hint,
                added_hint=added_hint,
            )

        except Exception as exc:
            print(
                f"Parse failed for post {post['id']}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        print(
            f"AI extracted {len(extracted)} picks "
            f"from post {post['id']}"
        )

        for extracted_pick in extracted:
            picker = normalize_picker(
                extracted_pick.get("picker")
            )

            if not picker:
                picker = picker_hint

            if not picker:
                print(
                    f"Skipping pick with unknown picker "
                    f"from post {post['id']}"
                )
                continue

            selection = str(
                extracted_pick.get("selection") or ""
            ).strip()

            if not selection:
                continue

            week = extracted_pick.get("week")

            if not week:
                week = inferred_week

            try:
                if week is not None:
                    week = int(week)
            except Exception:
                week = inferred_week

            confidence = float(
                extracted_pick.get("confidence") or 0
            )

            added_pick = bool(
                extracted_pick.get("added_pick")
            )

            if added_hint:
                added_pick = True

            pick_for_key = dict(extracted_pick)

            pick_for_key["picker"] = picker
            pick_for_key["week"] = week
            pick_for_key["selection"] = selection

            canonical = canonical_pick_key(
                pick_for_key
            )

            if canonical in seen_keys:
                print(
                    f"Duplicate skipped: "
                    f"{picker} | Week {week} | {selection}"
                )
                continue

            pid = stable_id(canonical)

            bet_type = (
                extracted_pick.get("bet_type")
                or "OTHER"
            ).upper()

            # Do not automatically grade unsupported bet types.
            supported_full_game = {
                "SPREAD",
                "TOTAL",
                "MONEYLINE",
            }

            if confidence < 0.90:
                status = "REVIEW"
            elif bet_type in supported_full_game:
                status = "OPEN"
            else:
                # Still display the wager, but do not let the ESPN full-game
                # grader accidentally grade a first-half/team-total/prop bet.
                status = "REVIEW"

            existing.append(
                {
                    "id": pid,
                    "picker": picker,
                    "sport": extracted_pick.get("sport")
                    or "CFB",
                    "matchup": extracted_pick.get(
                        "matchup"
                    ),
                    "team": extracted_pick.get(
                        "team"
                    ),
                    "opponent": extracted_pick.get(
                        "opponent"
                    ),
                    "bet_type": bet_type,
                    "selection": selection,
                    "side": extracted_pick.get("side"),
                    "line": extracted_pick.get("line"),
                    "odds": extracted_pick.get("odds"),
                    "units": float(
                        extracted_pick.get("units")
                        or 1
                    ),
                    "mortal_lock": bool(
                        extracted_pick.get(
                            "mortal_lock"
                        )
                    ),
                    "week": week,
                    "added_pick": added_pick,
                    "confidence": confidence,
                    "status": status,
                    "result": None,
                    "profit_units": 0,
                    "source_post_id": post["id"],
                    "source_url": post_url,
                    "source_text": text,
                    "posted_at": post.get(
                        "created_at"
                    ),
                    "graded_at": None,
                    "final_score": None,
                    "event_id": None,
                }
            )

            seen_keys.add(canonical)
            added_count += 1

            print(
                f"Added: {picker} | "
                f"Week {week} | "
                f"{selection} | "
                f"{'ADDED PICK' if added_pick else 'MAIN CARD'}"
            )

    if posts:
        state["last_x_post_id"] = max(
            (post["id"] for post in posts),
            key=int,
        )

    if should_backfill:
        state["backfill_complete"] = True

    state["updated_at"] = now_iso()

    save_json(PICKS_FILE, existing)
    save_json(STATE_FILE, state)

    print(f"Candidate posts inspected: {candidate_count}")
    print(f"New picks added: {added_count}")
    print(f"Total stored picks: {len(existing)}")


if __name__ == "__main__":
    ingest()
