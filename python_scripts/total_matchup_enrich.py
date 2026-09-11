from __future__ import annotations

import json
import os
import re
from collections import defaultdict

import requests

from common import PICKS_FILE, load_json, now_iso, save_json


X_API = "https://api.x.com/2"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

TOTAL_MARKETS = {
    "TOTAL",
    "FIRST_QUARTER_TOTAL",
    "FIRST_HALF_TOTAL",
}


# Exact matchup manually verified from the official Barstool card.
VERIFIED_MATCHUP_OVERRIDES = {
    (
        "2098104159572541853",
        "rico bosco",
        "over 48.5",
    ): "UofA @ BYU",
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
    raw = str(value or "").upper().strip()
    aliases = {
        "1Q_TOTAL": "FIRST_QUARTER_TOTAL",
        "1H_TOTAL": "FIRST_HALF_TOTAL",
    }
    return aliases.get(raw, raw)


def normalize_selection(value):
    value = clean_text(value).lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def side_and_line(selection, pick):
    side = str(pick.get("side") or "").upper().strip()
    line = pick.get("line")

    text = clean_text(selection)

    if side not in {"OVER", "UNDER"}:
        match = re.search(r"\b(over|under|o|u)\b", text, flags=re.I)
        if match:
            token = match.group(1).lower()
            side = "OVER" if token in {"over", "o"} else "UNDER"

    if line is None:
        match = re.search(r"(?:over|under|\bo\b|\bu\b)\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.I)
        if match:
            try:
                line = float(match.group(1))
            except Exception:
                line = None

    try:
        line = float(line) if line is not None else None
    except Exception:
        line = None

    return side, line


def x_get_post(post_id):
    token = os.environ["X_BEARER_TOKEN"]

    response = requests.get(
        f"{X_API}/tweets/{post_id}",
        params={
            "tweet.fields": "author_id,created_at,attachments,text,conversation_id",
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def image_urls_from_payload(payload):
    post = payload.get("data") or {}
    keys = (
        post.get("attachments", {})
        .get("media_keys", [])
    )

    media_by_key = {
        media.get("media_key"): media
        for media in (
            payload.get("includes", {})
            .get("media", [])
        )
        if media.get("media_key")
    }

    urls = []

    for key in keys:
        media = media_by_key.get(key) or {}
        if media.get("type") == "photo":
            url = media.get("url")
        else:
            url = media.get("preview_image_url")

        if url and url not in urls:
            urls.append(url)

    return post, urls


def extract_output_text(payload):
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    pieces = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                pieces.append(text.strip())

    return "\n".join(pieces).strip()


def parse_json_text(text):
    text = str(text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    first = text.find("{")
    last = text.rfind("}")

    if first >= 0 and last > first:
        text = text[first:last + 1]

    return json.loads(text)


def ask_model_for_matchup(picker, selection, existing_matchup, source_text, image_urls):
    side, line = side_and_line(selection, {})

    prompt = f"""
You are reading an official Barstool Pick Em pick-card image.

Your job is extremely narrow: identify the exact GAME MATCHUP attached to one TOTAL wager.

Picker: {picker}
Wager selection stored in our tracker: {selection}
Currently stored matchup (may be wrong): {existing_matchup or 'NONE'}
X post text: {source_text or 'NONE'}

Rules:
1. Look only at the official pick card images in this request.
2. Find the line for this exact wager selection.
3. Read the matchup heading immediately associated with that wager on the card.
4. Do NOT infer the matchup from memory, rankings, team schedules, or the currently stored matchup.
5. If the exact wager appears more than once on this picker's card and the matchup cannot be uniquely identified, return found=false.
6. Preserve the matchup wording from the graphic as closely as possible, for example "USF @ BYU" or "OSU @ TEX".
7. A total such as "Over 48.5" must be paired with the game directly above it on the graphic.
8. Return JSON only, no markdown.

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
            "type": "input_text",
            "text": prompt,
        }
    ]

    for url in image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": url,
            }
        )

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        },
        timeout=90,
    )
    response.raise_for_status()

    payload = response.json()
    text = extract_output_text(payload)

    if not text:
        raise ValueError("OpenAI returned no text")

    return parse_json_text(text)


def matchup_has_two_teams(value):
    value = clean_text(value)
    if not value:
        return False

    parts = re.split(
        r"\s*@\s*|\s+vs\.?\s+|\s+at\s+",
        value,
        flags=re.I,
    )

    parts = [part.strip() for part in parts if part.strip()]
    return len(parts) == 2


def selection_matches(requested, returned):
    req = normalize_selection(requested)
    got = normalize_selection(returned)

    if req == got:
        return True

    req_side, req_line = side_and_line(requested, {})
    got_side, got_line = side_and_line(returned, {})

    return (
        req_side in {"OVER", "UNDER"}
        and req_side == got_side
        and req_line is not None
        and got_line is not None
        and abs(req_line - got_line) < 0.001
    )


def verified_override_for_pick(pick):
    key = (
        str(pick.get("source_post_id") or "").strip(),
        normalize_selection(pick.get("picker")),
        normalize_selection(pick.get("selection")),
    )
    return VERIFIED_MATCHUP_OVERRIDES.get(key)


def clear_stale_schedule_fields(pick):
    for key in (
        "event_id",
        "game_matchup",
        "game_time",
        "game_match_status",
        "game_match_source",
        "game_match_confidence",
        "game_match_review_reason",
        "schedule_checked_at",
    ):
        pick.pop(key, None)


def needs_total_context(pick):
    if pick.get("official_reconciled"):
        return False

    if str(pick.get("sport") or "CFB").upper() not in {"CFB", "NCAAF"}:
        return False

    if normalize_market(pick.get("bet_type")) not in TOTAL_MARKETS:
        return False

    # Exact manually verified rows are allowed to repair stale metadata.
    if verified_override_for_pick(pick):
        return True

    # Once ESPN has locked the event, do not spend money re-reading the image.
    if pick.get("event_id"):
        return False

    return bool(pick.get("source_post_id"))


def enrich_total_matchups():
    print("\n========== TOTAL MATCHUP CONTEXT ==========")

    picks = load_json(PICKS_FILE, [])

    candidates = [pick for pick in picks if needs_total_context(pick)]

    if not candidates:
        print("No unmatched total picks need source-card verification.")
        print("=============================================\n")
        return

    print("Totals needing source-card verification:", len(candidates))

    post_cache = {}
    corrected = 0
    verified_unchanged = 0
    unresolved = 0

    for pick in candidates:
        post_id = str(pick.get("source_post_id") or "")
        picker = clean_text(pick.get("picker"))
        selection = clean_text(pick.get("selection"))
        existing_matchup = clean_text(pick.get("matchup"))

        override = verified_override_for_pick(pick)
        if override:
            if normalize_selection(existing_matchup) != normalize_selection(override):
                print(
                    "TOTAL MATCHUP VERIFIED OVERRIDE:",
                    picker,
                    "|",
                    selection,
                    "| old:",
                    existing_matchup or "NONE",
                    "| new:",
                    override,
                )
                pick["matchup"] = override
                clear_stale_schedule_fields(pick)
                corrected += 1
            else:
                print(
                    "TOTAL MATCHUP OVERRIDE ALREADY CORRECT:",
                    picker,
                    "|",
                    selection,
                    "|",
                    override,
                )
                verified_unchanged += 1

            pick["matchup_source"] = "VERIFIED_BARSTOOL_CARD_OVERRIDE"
            pick["matchup_source_verified_at"] = now_iso()
            continue

        try:
            if post_id not in post_cache:
                payload = x_get_post(post_id)
                post, image_urls = image_urls_from_payload(payload)
                post_cache[post_id] = (post, image_urls)

            post, image_urls = post_cache[post_id]

            if not image_urls:
                print(
                    "TOTAL CONTEXT SKIPPED - NO IMAGE:",
                    picker,
                    "|",
                    selection,
                    "| post:",
                    post_id,
                )
                unresolved += 1
                continue

            result = ask_model_for_matchup(
                picker=picker,
                selection=selection,
                existing_matchup=existing_matchup,
                source_text=post.get("text"),
                image_urls=image_urls,
            )

            found = bool(result.get("found"))
            returned_selection = clean_text(result.get("selection"))
            returned_matchup = clean_text(result.get("matchup"))

            if not found:
                print(
                    "TOTAL CONTEXT UNRESOLVED:",
                    picker,
                    "|",
                    selection,
                )
                unresolved += 1
                continue

            if not selection_matches(selection, returned_selection):
                print(
                    "TOTAL CONTEXT REJECTED - SELECTION MISMATCH:",
                    picker,
                    "| requested:",
                    selection,
                    "| returned:",
                    returned_selection,
                )
                unresolved += 1
                continue

            if not matchup_has_two_teams(returned_matchup):
                print(
                    "TOTAL CONTEXT REJECTED - BAD MATCHUP:",
                    picker,
                    "|",
                    selection,
                    "| returned:",
                    returned_matchup,
                )
                unresolved += 1
                continue

            if normalize_selection(existing_matchup) == normalize_selection(returned_matchup):
                verified_unchanged += 1
                pick["matchup_source_verified_at"] = now_iso()
                pick["matchup_source"] = "BARSTOOL_CARD_IMAGE"
                print(
                    "TOTAL MATCHUP VERIFIED:",
                    picker,
                    "|",
                    selection,
                    "|",
                    returned_matchup,
                )
                continue

            print(
                "TOTAL MATCHUP CORRECTED:",
                picker,
                "|",
                selection,
                "| old:",
                existing_matchup or "NONE",
                "| new:",
                returned_matchup,
            )

            pick["matchup"] = returned_matchup
            pick["matchup_source"] = "BARSTOOL_CARD_IMAGE"
            pick["matchup_source_verified_at"] = now_iso()

            # Clear stale schedule-review metadata so schedule_enrich.py gets
            # a clean chance to lock the corrected matchup to ESPN.
            pick.pop("game_matchup", None)
            pick.pop("game_time", None)
            pick.pop("game_match_status", None)
            pick.pop("game_match_source", None)
            pick.pop("game_match_confidence", None)
            pick.pop("game_match_review_reason", None)

            corrected += 1

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

    save_json(PICKS_FILE, picks)

    print("Total matchups corrected:", corrected)
    print("Total matchups verified unchanged:", verified_unchanged)
    print("Total matchups unresolved:", unresolved)
    print("=============================================\n")


if __name__ == "__main__":
    enrich_total_matchups()
