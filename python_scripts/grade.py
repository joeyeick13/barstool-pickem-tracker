from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

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

LOOKBACK_DAYS = 14

# Canonical names for common abbreviations / alternate names.
ALIASES = {
    "uconn": {"uconn", "connecticut", "connecticut huskies"},
    "usc": {"usc", "southern california", "usc trojans"},
    "cal": {"cal", "california", "california golden bears"},
    "ole miss": {"ole miss", "mississippi", "mississippi rebels"},
    "lsu": {"lsu", "louisiana state", "lsu tigers"},
    "pitt": {"pitt", "pittsburgh", "pittsburgh panthers"},
    "smu": {"smu", "southern methodist", "smu mustangs"},
    "tcu": {"tcu", "texas christian", "tcu horned frogs"},
    "byu": {"byu", "brigham young", "byu cougars"},
    "ucf": {"ucf", "central florida", "ucf knights"},
    "utsa": {"utsa", "texas san antonio", "utsa roadrunners"},
    "utep": {"utep", "texas el paso", "utep miners"},
    "fiu": {"fiu", "florida international", "fiu panthers"},
    "usf": {"usf", "south florida", "south florida bulls"},
    "ecu": {"ecu", "east carolina", "east carolina pirates"},
    "wazzu": {
        "wazzu",
        "washington state",
        "washington state cougars",
        "wsu",
    },
    "nd": {
        "nd",
        "notre dame",
        "notre dame fighting irish",
    },
    "miami oh": {
        "miami oh",
        "miami ohio",
        "miami redhawks",
    },
    "central michigan": {
        "central michigan",
        "central michigan chippewas",
        "cmu",
    },
    "texas am": {
        "texas am",
        "texas a m",
        "texas a&m",
        "texas a and m",
        "texas a m aggies",
        "aggies",
    },
}


def norm(value):
    value = str(value or "").lower()

    value = (
        value.replace("&", " and ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("'", "")
    )

    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def alias_group(value):
    n = norm(value)

    if not n:
        return set()

    result = {n}

    for canonical, names in ALIASES.items():
        normalized = {norm(x) for x in names}
        normalized.add(norm(canonical))

        if n in normalized:
            result.update(normalized)

    return result


def scoreboard(date_yyyymmdd):
    response = requests.get(
        ESPN,
        params={
            "dates": date_yyyymmdd,
            "limit": 1000,
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json().get("events", [])


def completed(event):
    return bool(
        event.get("status", {})
        .get("type", {})
        .get("completed")
    )


def competitors(event):
    competitions = event.get("competitions", [])

    if not competitions:
        return []

    return competitions[0].get("competitors", [])


def espn_names(comp):
    team = comp.get("team", {})

    values = [
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
    ]

    names = set()

    for value in values:
        if value:
            names.update(alias_group(value))

    return names


def exact_team_match(hint, comp):
    hints = alias_group(hint)

    if not hints:
        return False

    return bool(hints & espn_names(comp))


def split_matchup(value):
    if not value:
        return []

    text = str(value)

    return [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            text,
            flags=re.I,
        )
        if part.strip()
    ]


def clean_selection_team(selection):
    text = str(selection or "")

    # Remove common wager terminology.
    text = re.sub(
        r"\b(over|under|moneyline|ml)\b",
        " ",
        text,
        flags=re.I,
    )

    # Remove spread / total numbers.
    text = re.sub(
        r"[+-]\s*\d+(?:\.\d+)?",
        " ",
        text,
    )

    text = re.sub(
        r"\b\d+(?:\.\d+)?\b",
        " ",
        text,
    )

    return re.sub(r"\s+", " ", text).strip(" /-")


def primary_team_hints(pick):
    hints = []

    # These fields are most trustworthy.
    for field in ("team", "side"):
        value = pick.get(field)

        if value and norm(value) not in {"over", "under"}:
            hints.append(str(value))

    # For a spread/moneyline, the selection itself normally begins
    # with the selected team.
    btype = str(pick.get("bet_type") or "").upper()

    if btype in {"SPREAD", "MONEYLINE"}:
        cleaned = clean_selection_team(
            pick.get("selection")
        )

        if cleaned:
            hints.append(cleaned)

    # Preserve order while removing duplicates.
    output = []

    for hint in hints:
        if norm(hint) and norm(hint) not in {
            norm(x) for x in output
        }:
            output.append(hint)

    return output


def opponent_hints(pick):
    hints = []

    opponent = pick.get("opponent")

    if opponent:
        hints.append(str(opponent))

    matchup_parts = split_matchup(
        pick.get("matchup")
    )

    primary = primary_team_hints(pick)

    for part in matchup_parts:
        if not any(
            alias_group(part) & alias_group(p)
            for p in primary
        ):
            hints.append(part)

    return hints


def find_selected_comp(pick, comps):
    matches = []

    for comp in comps:
        if any(
            exact_team_match(hint, comp)
            for hint in primary_team_hints(pick)
        ):
            matches.append(comp)

    if len(matches) == 1:
        return matches[0]

    return None


def event_matches_pick(pick, event):
    comps = competitors(event)

    if len(comps) != 2:
        return False

    selected = find_selected_comp(pick, comps)

    if selected is None:
        return False

    opp_hints = opponent_hints(pick)

    if opp_hints:
        other = next(
            c for c in comps
            if c["id"] != selected["id"]
        )

        # If we have an explicit opponent/matchup, require it to match.
        if not any(
            exact_team_match(hint, other)
            for hint in opp_hints
        ):
            return False

    return True


def pick_post_date(pick):
    raw = pick.get("posted_at")

    if not raw:
        return None

    try:
        return datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
    except Exception:
        return None


def event_datetime(event):
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
    STRICT matching:
    1. Selected team must exactly match an ESPN name/alias.
    2. If opponent is known, opponent must also exactly match.
    3. Prefer completed games after the pick was posted.
    4. Never choose an event merely because its name is vaguely similar.
    """

    matches = [
        event
        for event in events
        if event_matches_pick(pick, event)
    ]

    if not matches:
        return None

    posted = pick_post_date(pick)

    if posted:
        sensible = []

        for event in matches:
            dt = event_datetime(event)

            if not dt:
                continue

            # A wager should normally be posted before kickoff.
            # Allow a small date tolerance for timezone differences.
            days = (
                dt.date() - posted.date()
            ).days

            if -1 <= days <= 9:
                sensible.append(event)

        if sensible:
            matches = sensible

    completed_matches = [
        event
        for event in matches
        if completed(event)
    ]

    if completed_matches:
        matches = completed_matches

    # One exact candidate = safe.
    if len(matches) == 1:
        return matches[0]

    # If multiple games for the same team are in our window,
    # choose the closest game occurring on/after the post.
    if posted:
        dated = []

        for event in matches:
            dt = event_datetime(event)

            if dt:
                delta = (
                    dt - posted
                ).total_seconds()

                # Strongly prefer games after the pick.
                penalty = (
                    abs(delta)
                    if delta >= -43200
                    else abs(delta) + 999999999
                )

                dated.append(
                    (penalty, event)
                )

        dated.sort(key=lambda x: x[0])

        if dated:
            # Only accept if the best candidate is clearly the closest.
            if len(dated) == 1:
                return dated[0][1]

            if (
                dated[1][0] - dated[0][0]
                > 24 * 60 * 60
            ):
                return dated[0][1]

    # Ambiguous = do not grade.
    return None


def final_score_text(comps, scores):
    output = []

    for comp in comps:
        team = comp.get("team", {})

        name = (
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("displayName")
            or "Team"
        )

        output.append(
            f"{name} {int(scores[comp['id']])}"
        )

    return " - ".join(output)


def grade_pick(pick, event):
    if not completed(event):
        return False

    comps = competitors(event)

    if len(comps) != 2:
        return False

    scores = {
        c["id"]: float(c.get("score") or 0)
        for c in comps
    }

    btype = str(
        pick.get("bet_type") or ""
    ).upper()

    line = pick.get("line")

    if btype == "TOTAL":
        if line is None:
            return False

        side = norm(
            pick.get("side")
            or pick.get("selection")
        )

        game_total = sum(scores.values())

        if "over" in side:
            if game_total > float(line):
                result = "WIN"
            elif game_total < float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        elif "under" in side:
            if game_total < float(line):
                result = "WIN"
            elif game_total > float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        else:
            return False

    elif btype in {"SPREAD", "MONEYLINE"}:
        selected = find_selected_comp(
            pick,
            comps,
        )

        if selected is None:
            return False

        other = next(
            c for c in comps
            if c["id"] != selected["id"]
        )

        margin = (
            scores[selected["id"]]
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
                return False

            adjusted = (
                margin + float(line)
            )

            if adjusted > 0:
                result = "WIN"
            elif adjusted < 0:
                result = "LOSS"
            else:
                result = "PUSH"

    else:
        return False

    pick["result"] = result
    pick["status"] = "FINAL"
    pick["event_id"] = event.get("id")
    pick["graded_at"] = now_iso()

    pick["final_score"] = final_score_text(
        comps,
        scores,
    )

    pick["profit_units"] = american_profit(
        pick.get("odds"),
        float(pick.get("units") or 1),
        result,
    )

    return True


def clear_old_grade(pick):
    """
    Critical for this repair:
    remove results produced by the earlier permissive matcher before
    attempting to grade the pick again.
    """

    pick["result"] = None
    pick["status"] = "OPEN"
    pick["event_id"] = None
    pick["graded_at"] = None
    pick["final_score"] = None
    pick["profit_units"] = 0


def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    now = datetime.now(timezone.utc)

    events_by_id = {}

    for i in range(LOOKBACK_DAYS):
        date = (
            now - timedelta(days=i)
        ).strftime("%Y%m%d")

        try:
            for event in scoreboard(date):
                event_id = event.get("id")

                if event_id:
                    events_by_id[event_id] = event

        except Exception as exc:
            print(
                "ESPN fetch failed:",
                date,
                exc,
            )

    events = list(events_by_id.values())

    print(
        f"Loaded {len(events)} unique ESPN events"
    )

    graded = 0
    unmatched = 0
    unsupported = 0

    for pick in picks:
        if pick.get("sport") not in {
            "CFB",
            "NCAAF",
        }:
            continue

        btype = str(
            pick.get("bet_type") or ""
        ).upper()

        if btype not in {
            "SPREAD",
            "TOTAL",
            "MONEYLINE",
        }:
            unsupported += 1
            continue

        # Recheck ALL supported wagers, including previously FINAL ones.
        # This repairs bad matches created by the old grader.
        clear_old_grade(pick)

        event = find_event(
            pick,
            events,
        )

        if event is None:
            unmatched += 1

            print(
                "UNMATCHED:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
            )

            continue

        if not completed(event):
            print(
                "NOT FINAL:",
                pick.get("picker"),
                "|",
                pick.get("selection"),
            )
            continue

        if grade_pick(
            pick,
            event,
        ):
            graded += 1

            print(
                "GRADED:",
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
        f"Safely graded: {graded}"
    )

    print(
        f"Unmatched supported picks: {unmatched}"
    )

    print(
        f"Unsupported/specialty picks left alone: {unsupported}"
    )


if __name__ == "__main__":
    grade_open()
