from __future__ import annotations
import json, os, re, hashlib
import requests
from common import load_json, save_json, PICKERS, PICKS_FILE, STATE_FILE, now_iso, normalize_picker

X_API = "https://api.x.com/2"
USERNAME = os.getenv("X_USERNAME", "barstoolpickem")


def x_get(path, params=None):
    token = os.environ["X_BEARER_TOKEN"]
    r = requests.get(X_API + path, params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def resolve_user_id():
    return x_get(f"/users/by/username/{USERNAME}")["data"]["id"]


def fetch_new_posts(user_id, since_id=None):
    params = {
        "max_results": 100,
        "exclude": "retweets",
        "tweet.fields": "created_at,attachments,text",
        "expansions": "attachments.media_keys",
        "media.fields": "media_key,type,url,preview_image_url",
    }
    if since_id:
        params["since_id"] = since_id
    return x_get(f"/users/{user_id}/tweets", params)


def media_map(payload):
    return {m["media_key"]: m for m in payload.get("includes", {}).get("media", [])}


def parse_post_with_ai(text: str, image_urls: list[str], post_url: str):
    from openai import OpenAI
    client = OpenAI()
    prompt = f"""
You extract ONLY explicit gambling picks attributed to these Barstool Pick Em personalities:
{', '.join(PICKERS)}.

Source post: {post_url}
Post text: {text}

Return strict JSON with this shape:
{{"picks":[{{
  "picker":"Big Cat|Stool Presidente|Rico Bosco",
  "sport":"CFB",
  "matchup":"free text matchup or null",
  "team":"team being bet or null",
  "opponent":"opponent if visible or null",
  "bet_type":"SPREAD|TOTAL|MONEYLINE|TEAM_TOTAL|FIRST_HALF_SPREAD|FIRST_HALF_TOTAL|OTHER",
  "selection":"exact concise selection, e.g. Texas -3.5 or Over 52.5",
  "side":"OVER|UNDER|team name|null",
  "line": number|null,
  "odds": integer|null,
  "units": number,
  "mortal_lock": boolean,
  "week": integer|null,
  "added_pick": boolean,
  "confidence": number
}}]}}

Rules:
- Do not infer a pick that is not explicit.
- Preserve the line exactly as posted.
- Default units to 1 if unstated.
- Default odds to -110 only if a spread/total has no odds shown; otherwise null is allowed.
- If a graphic contains separate columns for the three pickers, attribute each pick to the correct column.
- Ignore records, commentary, promos, jokes, and recaps unless they clearly announce a NEW/ADDED pick.
- Set week to the college-football week number when the post/card explicitly identifies it; otherwise use the current Pick Em week only when unambiguous from the post.
- Set added_pick true only when the post explicitly announces an addition/subsequent pick rather than the main card.
- If uncertain about attribution, week, or wager, confidence must be below 0.90.
- JSON only, no markdown.
"""
    content = [{"type": "input_text", "text": prompt}]
    for u in image_urls:
        content.append({"type": "input_image", "image_url": u, "detail": "high"})
    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        input=[{"role": "user", "content": content}],
    )
    raw = resp.output_text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S)
    return json.loads(raw).get("picks", [])


def stable_id(post_id, picker, selection):
    s = f"{post_id}|{picker}|{selection}".encode()
    return hashlib.sha1(s).hexdigest()[:16]


def ingest():
    state = load_json(STATE_FILE, {"last_x_post_id": None})
    existing = load_json(PICKS_FILE, [])
    existing_ids = {p["id"] for p in existing}
    uid = resolve_user_id()
    payload = fetch_new_posts(uid, state.get("last_x_post_id"))
    posts = payload.get("data", [])
    mmap = media_map(payload)

    for post in sorted(posts, key=lambda p: int(p["id"])):
        keys = post.get("attachments", {}).get("media_keys", [])
        images = []
        for k in keys:
            m = mmap.get(k, {})
            if m.get("type") == "photo" and m.get("url"):
                images.append(m["url"])
        post_url = f"https://x.com/{USERNAME}/status/{post['id']}"
        try:
            extracted = parse_post_with_ai(post.get("text", ""), images, post_url)
        except Exception as e:
            print(f"Parse failed for {post['id']}: {e}")
            extracted = []

        for p in extracted:
            picker = normalize_picker(p.get("picker"))
            if not picker:
                continue
            selection = str(p.get("selection") or "").strip()
            if not selection:
                continue
            pid = stable_id(post["id"], picker, selection)
            if pid in existing_ids:
                continue
            confidence = float(p.get("confidence") or 0)
            existing.append({
                "id": pid,
                "picker": picker,
                "sport": p.get("sport") or "CFB",
                "matchup": p.get("matchup"),
                "team": p.get("team"),
                "opponent": p.get("opponent"),
                "bet_type": p.get("bet_type") or "OTHER",
                "selection": selection,
                "side": p.get("side"),
                "line": p.get("line"),
                "odds": p.get("odds"),
                "units": float(p.get("units") or 1),
                "mortal_lock": bool(p.get("mortal_lock")),
                "week": p.get("week"),
                "added_pick": bool(p.get("added_pick")),
                "confidence": confidence,
                "status": "OPEN" if confidence >= 0.90 else "REVIEW",
                "result": None,
                "profit_units": 0,
                "source_post_id": post["id"],
                "source_url": post_url,
                "source_text": post.get("text", ""),
                "posted_at": post.get("created_at"),
                "graded_at": None,
                "final_score": None,
                "event_id": None,
            })
            existing_ids.add(pid)

    if posts:
        state["last_x_post_id"] = max((p["id"] for p in posts), key=int)
    state["updated_at"] = now_iso()
    save_json(PICKS_FILE, existing)
    save_json(STATE_FILE, state)
    print(f"Ingested. Total picks: {len(existing)}")

if __name__ == "__main__":
    ingest()
