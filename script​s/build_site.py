from __future__ import annotations
from collections import defaultdict
from common import load_json, save_json, PICKERS, PICKS_FILE, ROOT, now_iso


def finalized(p):
    return p.get("status") == "FINAL" and p.get("result") in {"WIN", "LOSS", "PUSH"}


def wl(picks):
    w = sum(p.get("result") == "WIN" for p in picks)
    l = sum(p.get("result") == "LOSS" for p in picks)
    push = sum(p.get("result") == "PUSH" for p in picks)
    pct = round(w / (w + l) * 100, 1) if w + l else 0.0
    return w, l, push, pct


def picker_summary(picks, picker):
    pp = [p for p in picks if p.get("picker") == picker]
    finals = [p for p in pp if finalized(p)]
    w, l, push, pct = wl(finals)
    locks = [p for p in finals if p.get("mortal_lock")]
    lw, ll, lp, _ = wl(locks)
    return {
        "picker": picker,
        "wins": w,
        "losses": l,
        "pushes": push,
        "win_pct": pct,
        "mortal_wins": lw,
        "mortal_losses": ll,
        "mortal_pushes": lp,
    }


def week_num(p):
    try:
        return int(p.get("week") or 0)
    except (TypeError, ValueError):
        return 0


def cumulative_series(picks, picker):
    pp = [p for p in picks if p.get("picker") == picker and finalized(p) and week_num(p) > 0]
    max_week = max([week_num(p) for p in picks] or [0])
    out = []
    running = []
    for week in range(1, max_week + 1):
        running.extend([p for p in pp if week_num(p) == week])
        w, l, _, pct = wl(running)
        if w + l:
            out.append({"week": week, "win_pct": pct})
    return out


def build():
    picks = load_json(PICKS_FILE, [])
    weeks = sorted({week_num(p) for p in picks if week_num(p) > 0})
    payload = {
        "updated_at": now_iso(),
        "cappers": [picker_summary(picks, p) for p in PICKERS],
        "win_pct_history": {p: cumulative_series(picks, p) for p in PICKERS},
        "weeks": weeks,
        "picks": sorted(picks, key=lambda p: (week_num(p), p.get("posted_at") or "", p.get("picker") or ""), reverse=True),
    }
    save_json(ROOT / "data" / "dashboard.json", payload)
    print("dashboard.json rebuilt")

if __name__ == "__main__":
    build()
