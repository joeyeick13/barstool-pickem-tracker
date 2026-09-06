from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

from common import (
    load_json,
    save_json,
    PICKS_FILE,
    american_profit,
    now_iso,
)

ESPN = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football/scoreboard"
)

LOOKBACK_DAYS = 12


def scoreboard(date_yyyymmdd):
    r = requests.get(
        ESPN,
        params={
            "dates": date_yyyymmdd,
            "limit": 1000,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("events", [])


def norm(value):
    value = str(value or "").lower()

    value = (
        value.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("&", " and ")
    )

    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


TEAM_ALIASES = {
    "uconn": [
        "connecticut",
        "uconn",
        "connecticut huskies",
    ],
    "usc": [
        "southern california",
        "usc",
        "usc trojans",
    ],
    "miami oh": [
        "miami ohio",
        "miami oh",
        "miami redhawks",
    ],
    "miami ohio": [
        "miami ohio",
        "miami oh",
        "miami redhawks",
    ],
    "ole miss": [
        "mississippi",
        "ole miss",
        "mississippi rebels",
    ],
    "lsu": [
        "louisiana state",
        "lsu",
        "lsu tigers",
    ],
    "cal": [
        "california",
        "cal",
        "cal golden bears",
    ],
    "pitt": [
        "pittsburgh",
        "pitt",
        "pittsburgh panthers",
    ],
    "smu": [
        "southern methodist",
        "smu",
        "smu mustangs",
    ],
    "tcu": [
        "texas christian",
        "tcu",
        "tcu horned frogs",
    ],
    "byu": [
        "brigham young",
        "byu",
        "byu cougars",
    ],
    "ucf": [
        "central florida",
        "ucf",
        "ucf knights",
    ],
    "utsa": [
        "texas san antonio",
        "utsa",
        "utsa roadrunners",
    ],
    "utep": [
        "texas el paso",
        "utep",
        "utep miners",
    ],
    "fiu": [
        "florida international",
        "fiu",
        "fiu panthers",
    ],
    "usf": [
        "south florida",
        "usf",
        "south florida bulls",
    ],
    "ecu": [
        "east carolina",
        "ecu",
        "east carolina pirates",
    ],
}


def espn_aliases(comp):
    team = comp.get("team", {})

    values = [
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
    ]

    out = {norm(x) for x in values if x}

    for item in list(out):
        for canonical, extras in TEAM_ALIASES.items():
            normalized_extras = {norm(x) for x in extras}

            if item == norm(canonical) or item in normalized_extras:
                out.add(norm(canonical))
                out.update(normalized_extras)

    return list(out)


def expanded_hint_aliases(value):
    value = norm(value)

    if not value:
        return []

    out = {value}

    for canonical, extras in TEAM_ALIASES.items():
        normalized_extras = {norm(x) for x in extras}

        if value == norm(canonical) or value in normalized_extras:
            out.add(norm(canonical))
            out.update(normalized_extras)

    return list(out)


def similarity(a, b):
    a = norm(a)
    b = norm(b)

    if not a or not b:
        return 0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.96

    return SequenceMatcher(None, a, b).ratio()


def split_matchup(matchup):
    if not matchup:
        return []

    text = str(matchup)

    parts = re.split(
        r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
        text,
        flags=re.I,
    )

    return [p.strip() for p in parts if p.strip()]


def pick_team_hints(pick):
    hints = []

    for field in ["team", "opponent", "side"]:
        if pick.get(field):
            hints.append(str(pick[field]))

    hints.extend(split_matchup(pick.get("matchup")))

    selection = str(pick.get("selection") or "")

    cleaned_selection = re.sub(
        r"\b(over|under|ml|moneyline)\b",
        "",
        selection,
        flags=re.I,
    )

    cleaned_selection = re.sub(
        r"[+-]\s*\d+(?:\.\d+)?",
        "",
        cleaned_selection,
    )

    cleaned_selection = re.sub(
        r"\d+(?:\.\d+)?",
        "",
        cleaned_selection,
    )

    cleaned_selection = cleaned_selection.strip(" -/")

    if cleaned_selection:
        hints.append(cleaned_selection)

    return [h for h in hints if h]


def score_hint_against_comp(hint, comp):
    hint_aliases = expanded_hint_aliases(hint)
    team_aliases = espn_aliases(comp)

    best = 0

    for h in hint_aliases:
        for team_name in team_aliases:
            best = max(best, similarity(h, team_name))

    return best


def completed(event):
    return bool(
        event.get("status", {})
        .get("type", {})
        .get("completed")
    )


def event_date(event):
    raw = event.get("date")

    if not raw:
        return None

    try:
        return datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
    except Exception:
        return None


def find_event(pick, events):
    """
    Match a pick to the most likely ESPN event.

    Improvements over the original:
    - team aliases
    - selection text can identify the team
    - stronger preference for completed games
    - uses post date proximity
    - allows a single strong team match when opponent is missing
    """

    hints = pick_team_hints(pick)

    if not hints:
        return None

    posted_at = None

    if pick.get("posted_at"):
        try:
            posted_at = datetime.fromisoformat(
                pick["posted_at"].replace("Z", "+00:00")
            )
        except Exception:
            posted_at = None

    candidates = []

    for event in events:
        comps = (
            event.get("competitions", [{}])[0]
            .get("competitors", [])
        )

        if len(comps) != 2:
            continue

        hint_scores = []

        for hint in hints:
            best_for_hint = max(
                [
                    score_hint_against_comp(hint, c)
                    for c in comps
                ]
                or [0]
            )

            hint_scores.append(best_for_hint)

        hint_scores.sort(reverse=True)

        best_team = hint_scores[0] if hint_scores else 0
        second_team = (
            hint_scores[1]
            if len(hint_scores) > 1
            else 0
        )

        score = best_team

        if second_team >= 0.70:
            score += second_team * 0.75

        if completed(event):
            score += 0.05

        e_date = event_date(event)

        if posted_at and e_date:
            days_apart = abs(
                (e_date.date() - posted_at.date()).days
            )

            if days_apart <= 1:
                score += 0.12
            elif days_apart <= 3:
                score += 0.08
            elif days_apart <= 7:
                score += 0.03
            elif days_apart > 10:
                score -= 0.15

        candidates.append(
            {
                "event": event,
                "score": score,
                "best_team": best_team,
                "second_team": second_team,
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    best = candidates[0]

    # If both teams are represented, this is a very safe match.
    if (
        best["best_team"] >= 0.78
        and best["second_team"] >= 0.68
    ):
        return best["event"]

    # If we only know one team, require a very strong match.
    if best["best_team"] >= 0.90:
        if len(candidates) == 1:
            return best["event"]

        runner_up = candidates[1]

        # Accept when the best candidate is clearly better than the next one.
        if best["score"] - runner_up["score"] >= 0.10:
            return best["event"]

    return None


def team_from_selection(pick, competitors):
    hints = []

    for field in ["team", "side"]:
        if pick.get(field):
            hints.append(str(pick[field]))

    selection = str(pick.get("selection") or "")

    selection = re.sub(
        r"\b(over|under|ml|moneyline)\b",
        "",
        selection,
        flags=re.I,
    )

    selection = re.sub(
        r"[+-]\s*\d+(?:\.\d+)?",
        "",
        selection,
    )

    selection = selection.strip()

    if selection:
        hints.append(selection)

    best_score = 0
    best_comp = None

    for comp in competitors:
        for hint in hints:
            s = score_hint_against_comp(
                hint,
                comp,
            )

            if s > best_score:
                best_score = s
                best_comp = comp

    return best_comp if best_score >= 0.72 else None


def final_score_text(competitors, scores):
    parts = []

    for c in competitors:
        team = c.get("team", {})

        name = (
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("displayName")
            or "Team"
        )

        score = int(scores[c["id"]])

        parts.append(f"{name} {score}")

    return " - ".join(parts)


def grade_pick(pick, event):
    comp = event.get("competitions", [{}])[0]

    if not completed(event):
        return None

    competitors = comp.get("competitors", [])

    if len(competitors) != 2:
        return None

    scores = {
        c["id"]: float(c.get("score") or 0)
        for c in competitors
    }

    total = sum(scores.values())

    btype = str(
        pick.get("bet_type") or ""
    ).upper()

    line = pick.get("line")

    # Only full-game bets are auto-graded here.
    if btype == "TOTAL":
        if line is None:
            return None

        side = str(
            pick.get("side")
            or pick.get("selection")
            or ""
        ).upper()

        if "OVER" in side:
            if total > float(line):
                result = "WIN"
            elif total < float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        elif "UNDER" in side:
            if total < float(line):
                result = "WIN"
            elif total > float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        else:
            return None

    elif btype in {"SPREAD", "MONEYLINE"}:
        tc = team_from_selection(
            pick,
            competitors,
        )

        if not tc:
            return None

        other = next(
            c
            for c in competitors
            if c["id"] != tc["id"]
        )

        margin = (
            scores[tc["id"]]
            - scores[other["id"]]
        )

        if btype == "MONEYLINE":
            if margin > 0:
                result = "WIN"
            elif margin < 0:
                result = "LOSS"
            else:
                result = "PUSH"

        else:
            if line is None:
                return None

            adjusted_margin = (
                margin + float(line)
            )

            if adjusted_margin > 0:
                result = "WIN"
            elif adjusted_margin < 0:
                result = "LOSS"
            else:
                result = "PUSH"

    else:
        # TEAM_TOTAL, FIRST_HALF_TOTAL, props, etc.
        # remain pending/review until we add appropriate scoring data.
        return None

    pick["result"] = result
    pick["status"] = "FINAL"

    pick["profit_units"] = american_profit(
        pick.get("odds"),
        float(pick.get("units") or 1),
        result,
    )

    pick["graded_at"] = now_iso()
    pick["event_id"] = event.get("id")

    pick["final_score"] = final_score_text(
        competitors,
        scores,
    )

    return pick


def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    all_events = []

    now = datetime.now(timezone.utc)

    for i in range(LOOKBACK_DAYS):
        day = (
            now - timedelta(days=i)
        ).strftime("%Y%m%d")

        try:
            events = scoreboard(day)

            all_events.extend(events)

        except Exception as exc:
            print(
                "Score fetch failed",
                day,
                exc,
            )

    # ESPN may return the same event more than once
    # when queried across adjacent date windows.
    unique_events = {}

    for event in all_events:
        if event.get("id"):
            unique_events[event["id"]] = event

    all_events = list(
        unique_events.values()
    )

    changed = 0
    unmatched = 0

    for pick in picks:
        if (
            pick.get("status") != "OPEN"
            or pick.get("sport")
            not in {"CFB", "NCAAF"}
        ):
            continue

        if str(
            pick.get("bet_type") or ""
        ).upper() not in {
            "SPREAD",
            "TOTAL",
            "MONEYLINE",
        }:
            continue

        event = find_event(
            pick,
            all_events,
        )

        if not event:
            unmatched += 1

            print(
                "No ESPN match:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
            )

            continue

        if grade_pick(
            pick,
            event,
        ):
            changed += 1

            print(
                "Graded:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
                "|",
                pick.get("result"),
                "|",
                pick.get("final_score"),
            )

    save_json(
        PICKS_FILE,
        picks,
    )

    print(
        f"Graded {changed} picks"
    )

    print(
        f"Unmatched eligible picks: {unmatched}"
    )


if __name__ == "__main__":
    grade_open()
