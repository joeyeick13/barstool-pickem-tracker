from __future__ import annotations
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
import requests
from common import load_json, save_json, PICKS_FILE, american_profit, now_iso

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"


def scoreboard(date_yyyymmdd):
    r = requests.get(ESPN, params={"dates": date_yyyymmdd, "limit": 1000}, timeout=30)
    r.raise_for_status()
    return r.json().get("events", [])


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def aliases(comp):
    team = comp.get("team", {})
    return list({norm(x) for x in [team.get("displayName"), team.get("shortDisplayName"), team.get("name"), team.get("abbreviation")] if x})


def similarity(a, b):
    a, b = norm(a), norm(b)
    if not a or not b: return 0
    if a in b or b in a: return 0.95
    return SequenceMatcher(None, a, b).ratio()


def find_event(pick, events):
    hints = [pick.get("team"), pick.get("opponent")]
    if pick.get("matchup"):
        hints += re.split(r"\s+(?:vs\.?|v\.?|@)\s+", pick["matchup"], flags=re.I)
    hints = [h for h in hints if h]
    best = (0, None)
    for e in events:
        comps = e.get("competitions", [{}])[0].get("competitors", [])
        names = [a for c in comps for a in aliases(c)]
        scores = []
        for h in hints:
            scores.append(max([similarity(h, n) for n in names] or [0]))
        score = sum(sorted(scores, reverse=True)[:2])
        if score > best[0]: best = (score, e)
    return best[1] if best[0] >= 0.90 else None


def team_from_selection(pick, competitors):
    hints = [pick.get("team"), pick.get("side")]
    # strip line/ML from selection and use that too
    hints.append(re.sub(r"[+-]?\d+(?:\.\d+)?|\bML\b", "", pick.get("selection", ""), flags=re.I))
    best = (0, None)
    for c in competitors:
        for h in hints:
            if not h: continue
            s = max([similarity(h, a) for a in aliases(c)] or [0])
            if s > best[0]: best = (s, c)
    return best[1] if best[0] >= 0.72 else None


def grade_pick(pick, event):
    comp = event.get("competitions", [{}])[0]
    if not event.get("status", {}).get("type", {}).get("completed"):
        return None
    competitors = comp.get("competitors", [])
    if len(competitors) != 2:
        return None
    scores = {c["id"]: float(c.get("score") or 0) for c in competitors}
    total = sum(scores.values())
    btype = pick.get("bet_type")
    line = pick.get("line")

    if btype in {"TOTAL", "FIRST_HALF_TOTAL"}:
        # Current starter grades full-game totals only. 1H remains review-safe.
        if btype == "FIRST_HALF_TOTAL": return None
        if line is None: return None
        side = str(pick.get("side") or pick.get("selection") or "").upper()
        if "OVER" in side:
            result = "WIN" if total > float(line) else "LOSS" if total < float(line) else "PUSH"
        elif "UNDER" in side:
            result = "WIN" if total < float(line) else "LOSS" if total > float(line) else "PUSH"
        else: return None
    elif btype in {"SPREAD", "MONEYLINE"}:
        tc = team_from_selection(pick, competitors)
        if not tc: return None
        other = next(c for c in competitors if c["id"] != tc["id"])
        margin = scores[tc["id"]] - scores[other["id"]]
        if btype == "MONEYLINE":
            result = "WIN" if margin > 0 else "LOSS" if margin < 0 else "PUSH"
        else:
            if line is None: return None
            adj = margin + float(line)
            result = "WIN" if adj > 0 else "LOSS" if adj < 0 else "PUSH"
    else:
        return None

    pick["result"] = result
    pick["status"] = "FINAL"
    pick["profit_units"] = american_profit(pick.get("odds"), float(pick.get("units") or 1), result)
    pick["graded_at"] = now_iso()
    pick["event_id"] = event.get("id")
    pick["final_score"] = " - ".join(f"{c.get('team',{}).get('abbreviation', c.get('team',{}).get('shortDisplayName'))} {int(scores[c['id']])}" for c in competitors)
    return pick


def grade_open():
    picks = load_json(PICKS_FILE, [])
    # Look back 9 days to cover late grading and CFB week windows.
    all_events = []
    now = datetime.now(timezone.utc)
    for i in range(0, 9):
        d = (now - timedelta(days=i)).strftime("%Y%m%d")
        try: all_events.extend(scoreboard(d))
        except Exception as e: print("Score fetch failed", d, e)

    changed = 0
    for p in picks:
        if p.get("status") != "OPEN" or p.get("sport") not in {"CFB", "NCAAF"}:
            continue
        event = find_event(p, all_events)
        if event and grade_pick(p, event):
            changed += 1
    save_json(PICKS_FILE, picks)
    print(f"Graded {changed} picks")

if __name__ == "__main__":
    grade_open()
