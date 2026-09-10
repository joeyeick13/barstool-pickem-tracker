from __future__ import annotations

import json
import os
import re

import requests

from common import PICKS_FILE, load_json, now_iso, save_json


X_API = "https://api.x.com/2"

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.6-luna",
)

OPENAI_RESPONSES_URL = (
    "https://api.openai.com/v1/responses"
)


TOTAL_MARKETS = {
    "TOTAL",
    "FIRST_QUARTER_TOTAL",
    "FIRST_HALF_TOTAL",
}


# ============================================================
# VERIFIED SOURCE-CARD OVERRIDES
#
# These are used only when we have personally verified the
# official Barstool graphic and the image model has shown that
# it can misread similar abbreviations.
#
# Key:
#   (season, week, picker, normalized selection)
#
# This does NOT globally teach the system that USF means
# anything else. It corrects only this exact verified wager.
# ============================================================

VERIFIED_MATCHUP_OVERRIDES = {
    (
        2026,
        2,
        "Rico Bosco",
        "over 48.5",
    ):
        "USF @ BYU",
}


def clean_text(value):
    return (
        str(value or "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
        .strip()
    )


def normalize_market(value):
    raw = str(
        value or ""
    ).upper().strip()

    aliases = {
        "1Q_TOTAL":
            "FIRST_QUARTER_TOTAL",

        "1H_TOTAL":
            "FIRST_HALF_TOTAL",
    }

    return aliases.get(
        raw,
        raw,
    )


def normalize_selection(value):
    value = clean_text(
        value
    ).lower()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def safe_int(value):
    try:
        return int(value)

    except Exception:
        return None


def verified_override_for_pick(
    pick
):
    season = safe_int(
        pick.get(
            "season"
        )
    )

    week = safe_int(
        pick.get(
            "week"
        )
    )

    picker = clean_text(
        pick.get(
            "picker"
        )
    )

    selection = normalize_selection(
        pick.get(
            "selection"
        )
    )

    return VERIFIED_MATCHUP_OVERRIDES.get(
        (
            season,
            week,
            picker,
            selection,
        )
    )


def side_and_line(
    selection,
    pick,
):
    side = str(
        pick.get("side")
        or ""
    ).upper().strip()

    line = pick.get(
        "line"
    )

    text = clean_text(
        selection
    )

    if side not in {
        "OVER",
        "UNDER",
    }:

        match = re.search(
            r"\b(over|under|o|u)\b",
            text,
            flags=re.I,
        )

        if match:

            token = (
                match.group(1)
                .lower()
            )

            side = (
                "OVER"
                if token
                in {
                    "over",
                    "o",
                }
                else "UNDER"
            )

    if line is None:

        match = re.search(
            r"(?:"
            r"over|under|"
            r"\bo\b|\bu\b"
            r")\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if match:

            try:
                line = float(
                    match.group(1)
                )

            except Exception:
                line = None

    try:
        line = (
            float(line)
            if line is not None
            else None
        )

    except Exception:
        line = None

    return (
        side,
        line,
    )


def x_get_post(
    post_id
):
    token = os.environ[
        "X_BEARER_TOKEN"
    ]

    response = requests.get(
        f"{X_API}/tweets/{post_id}",
        params={
            "tweet.fields": (
                "author_id,"
                "created_at,"
                "attachments,"
                "text,"
                "conversation_id"
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
        headers={
            "Authorization":
                f"Bearer {token}"
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def image_urls_from_payload(
    payload
):
    post = (
        payload.get("data")
        or {}
    )

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

    media_by_key = {
        media.get(
            "media_key"
        ): media
        for media
        in (
            payload
            .get(
                "includes",
                {},
            )
            .get(
                "media",
                [],
            )
        )
        if media.get(
            "media_key"
        )
    }

    urls = []

    for key in keys:

        media = (
            media_by_key.get(
                key
            )
            or {}
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

    return (
        post,
        urls,
    )


def extract_output_text(
    payload
):
    direct = payload.get(
        "output_text"
    )

    if (
        isinstance(
            direct,
            str,
        )
        and direct.strip()
    ):

        return direct.strip()

    pieces = []

    for item in (
        payload.get("output")
        or []
    ):

        for content in (
            item.get("content")
            or []
        ):

            text = content.get(
                "text"
            )

            if (
                isinstance(
                    text,
                    str,
                )
                and text.strip()
            ):

                pieces.append(
                    text.strip()
                )

    return "\n".join(
        pieces
    ).strip()


def parse_json_text(
    text
):
    text = str(
        text or ""
    ).strip()

    if text.startswith(
        "```"
    ):

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

    first = text.find(
        "{"
    )

    last = text.rfind(
        "}"
    )

    if (
        first >= 0
        and last > first
    ):

        text = text[
            first:
            last + 1
        ]

    return json.loads(
        text
    )


def ask_model_for_matchup(
    picker,
    selection,
    existing_matchup,
    source_text,
    image_urls,
):

    prompt = f"""
You are reading an official Barstool Pick Em pick-card image.

Your ONLY job is to identify the exact game heading attached to one
specific TOTAL wager.

Picker: {picker}
Wager: {selection}
Stored matchup, which may be wrong: {existing_matchup or 'NONE'}
Post text: {source_text or 'NONE'}

IMPORTANT READING RULES:

1. Use ONLY the words visible on the official card images.

2. Find the exact wager:
   {selection}

3. The matchup is the game heading DIRECTLY ABOVE that wager.

4. Do not use a different game that happens to contain the same line.

5. Do not infer or autocorrect school abbreviations.

6. Copy the abbreviations exactly as they appear on the card.

7. Carefully distinguish abbreviations such as:
   USF
   UofA
   UGA
   Utah
   Utah ST
   ASU

8. If you cannot clearly read BOTH teams attached to this exact wager,
   return found=false.

9. Never use the currently stored matchup as evidence.

10. Return JSON only.

Return exactly:

{{
  "found": true or false,
  "picker": "{picker}",
  "selection": "{selection}",
  "matchup": "TEAM A @ TEAM B" or null
}}
""".strip()

    content = [
        {
            "type":
                "input_text",

            "text":
                prompt,
        }
    ]

    for url in image_urls:

        content.append(
            {
                "type":
                    "input_image",

                "image_url":
                    url,
            }
        )

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization":
                (
                    "Bearer "
                    f"{os.environ['OPENAI_API_KEY']}"
                ),

            "Content-Type":
                "application/json",
        },
        json={
            "model":
                OPENAI_MODEL,

            "input": [
                {
                    "role":
                        "user",

                    "content":
                        content,
                }
            ],
        },
        timeout=90,
    )

    response.raise_for_status()

    payload = response.json()

    text = extract_output_text(
        payload
    )

    if not text:

        raise ValueError(
            "OpenAI returned no text"
        )

    return parse_json_text(
        text
    )


def matchup_has_two_teams(
    value
):
    value = clean_text(
        value
    )

    if not value:
        return False

    parts = re.split(
        r"\s*@\s*"
        r"|\s+vs\.?\s+"
        r"|\s+at\s+",
        value,
        flags=re.I,
    )

    parts = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    return len(
        parts
    ) == 2


def selection_matches(
    requested,
    returned,
):
    req = normalize_selection(
        requested
    )

    got = normalize_selection(
        returned
    )

    if req == got:
        return True

    req_side, req_line = (
        side_and_line(
            requested,
            {},
        )
    )

    got_side, got_line = (
        side_and_line(
            returned,
            {},
        )
    )

    return (
        req_side
        in {
            "OVER",
            "UNDER",
        }
        and req_side
        == got_side
        and req_line
        is not None
        and got_line
        is not None
        and abs(
            req_line
            - got_line
        )
        < 0.001
    )


def needs_total_context(
    pick
):
    if pick.get(
        "official_reconciled"
    ):
        return False

    sport = str(
        pick.get("sport")
        or "CFB"
    ).upper()

    if sport not in {
        "CFB",
        "NCAAF",
    }:
        return False

    if (
        normalize_market(
            pick.get(
                "bet_type"
            )
        )
        not in TOTAL_MARKETS
    ):
        return False

    # Verified overrides are allowed to repair
    # an existing incorrect matchup even if it
    # has already been source-verified.
    if verified_override_for_pick(
        pick
    ):
        return True

    # Once ESPN has locked the event, never
    # spend money reparsing the card.
    if pick.get(
        "event_id"
    ):
        return False

    return bool(
        pick.get(
            "source_post_id"
        )
    )


def clear_stale_schedule_fields(
    pick
):
    pick.pop(
        "event_id",
        None,
    )

    pick.pop(
        "game_matchup",
        None,
    )

    pick.pop(
        "game_time",
        None,
    )

    pick.pop(
        "game_match_status",
        None,
    )

    pick.pop(
        "game_match_source",
        None,
    )

    pick.pop(
        "game_match_confidence",
        None,
    )

    pick.pop(
        "game_match_review_reason",
        None,
    )

    pick.pop(
        "schedule_checked_at",
        None,
    )


def apply_matchup(
    pick,
    matchup,
    source,
):
    old = clean_text(
        pick.get(
            "matchup"
        )
    )

    matchup = clean_text(
        matchup
    )

    changed = (
        normalize_selection(
            old
        )
        !=
        normalize_selection(
            matchup
        )
    )

    pick[
        "matchup"
    ] = matchup

    pick[
        "matchup_source"
    ] = source

    pick[
        "matchup_source_verified_at"
    ] = now_iso()

    if changed:
        clear_stale_schedule_fields(
            pick
        )

    return (
        changed,
        old,
    )


def enrich_total_matchups():

    print(
        "\n========== "
        "TOTAL MATCHUP CONTEXT "
        "=========="
    )

    picks = load_json(
        PICKS_FILE,
        [],
    )

    candidates = [
        pick
        for pick in picks
        if needs_total_context(
            pick
        )
    ]

    if not candidates:

        print(
            "No unmatched total picks "
            "need source-card verification."
        )

        print(
            "================================"
            "=============\n"
        )

        return

    print(
        "Totals needing "
        "source-card verification:",
        len(candidates),
    )

    post_cache = {}

    corrected = 0
    verified_unchanged = 0
    unresolved = 0
    deterministic = 0

    for pick in candidates:

        picker = clean_text(
            pick.get(
                "picker"
            )
        )

        selection = clean_text(
            pick.get(
                "selection"
            )
        )

        existing_matchup = (
            clean_text(
                pick.get(
                    "matchup"
                )
            )
        )

        # ----------------------------------------------------
        # FIRST PRIORITY:
        # exact source-card values we have manually verified.
        # ----------------------------------------------------

        override = (
            verified_override_for_pick(
                pick
            )
        )

        if override:

            changed, old = (
                apply_matchup(
                    pick,
                    override,
                    (
                        "VERIFIED_BARSTOOL_"
                        "CARD_OVERRIDE"
                    ),
                )
            )

            deterministic += 1

            if changed:

                corrected += 1

                print(
                    "TOTAL MATCHUP "
                    "VERIFIED OVERRIDE:",
                    picker,
                    "|",
                    selection,
                    "| old:",
                    old
                    or "NONE",
                    "| new:",
                    override,
                )

            else:

                verified_unchanged += 1

                print(
                    "TOTAL MATCHUP "
                    "OVERRIDE ALREADY CORRECT:",
                    picker,
                    "|",
                    selection,
                    "|",
                    override,
                )

            continue

        post_id = str(
            pick.get(
                "source_post_id"
            )
            or ""
        )

        try:

            if (
                post_id
                not in post_cache
            ):

                payload = x_get_post(
                    post_id
                )

                post, image_urls = (
                    image_urls_from_payload(
                        payload
                    )
                )

                post_cache[
                    post_id
                ] = (
                    post,
                    image_urls,
                )

            post, image_urls = (
                post_cache[
                    post_id
                ]
            )

            if not image_urls:

                print(
                    "TOTAL CONTEXT SKIPPED "
                    "- NO IMAGE:",
                    picker,
                    "|",
                    selection,
                    "| post:",
                    post_id,
                )

                unresolved += 1

                continue

            result = (
                ask_model_for_matchup(
                    picker=
                        picker,

                    selection=
                        selection,

                    existing_matchup=
                        existing_matchup,

                    source_text=
                        post.get(
                            "text"
                        ),

                    image_urls=
                        image_urls,
                )
            )

            found = bool(
                result.get(
                    "found"
                )
            )

            returned_selection = (
                clean_text(
                    result.get(
                        "selection"
                    )
                )
            )

            returned_matchup = (
                clean_text(
                    result.get(
                        "matchup"
                    )
                )
            )

            if not found:

                print(
                    "TOTAL CONTEXT "
                    "UNRESOLVED:",
                    picker,
                    "|",
                    selection,
                )

                unresolved += 1

                continue

            if not selection_matches(
                selection,
                returned_selection,
            ):

                print(
                    "TOTAL CONTEXT REJECTED "
                    "- SELECTION MISMATCH:",
                    picker,
                    "| requested:",
                    selection,
                    "| returned:",
                    returned_selection,
                )

                unresolved += 1

                continue

            if not matchup_has_two_teams(
                returned_matchup
            ):

                print(
                    "TOTAL CONTEXT REJECTED "
                    "- BAD MATCHUP:",
                    picker,
                    "|",
                    selection,
                    "| returned:",
                    returned_matchup,
                )

                unresolved += 1

                continue

            changed, old = (
                apply_matchup(
                    pick,
                    returned_matchup,
                    "BARSTOOL_CARD_IMAGE",
                )
            )

            if changed:

                corrected += 1

                print(
                    "TOTAL MATCHUP CORRECTED:",
                    picker,
                    "|",
                    selection,
                    "| old:",
                    old
                    or "NONE",
                    "| new:",
                    returned_matchup,
                )

            else:

                verified_unchanged += 1

                print(
                    "TOTAL MATCHUP VERIFIED:",
                    picker,
                    "|",
                    selection,
                    "|",
                    returned_matchup,
                )

        except Exception as exc:

            unresolved += 1

            print(
                "TOTAL CONTEXT ERROR:",
                picker,
                "|",
                selection,
                "|",
                exc,
            )

    save_json(
        PICKS_FILE,
        picks,
    )

    print(
        "Verified deterministic overrides:",
        deterministic,
    )

    print(
        "Total matchups corrected:",
        corrected,
    )

    print(
        "Total matchups verified "
        "unchanged:",
        verified_unchanged,
    )

    print(
        "Total matchups unresolved:",
        unresolved,
    )

    print(
        "================================"
        "=============\n"
    )


if __name__ == "__main__":

    enrich_total_matchups()
