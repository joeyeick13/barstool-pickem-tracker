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

ALIASES = {
    "uconn": {
        "uconn",
        "connecticut",
        "connecticut huskies",
    },
    "cal": {
        "cal",
        "california",
        "california golden bears",
    },
    "nd": {
        "nd",
        "notre dame",
        "notre dame fighting irish",
    },
    "wazzu": {
        "wazzu",
        "wsu",
        "washington state",
        "washington state cougars",
    },
    "ole miss": {
        "ole miss",
        "mississippi",
        "mississippi rebels",
    },
    "lsu": {
        "lsu",
        "louisiana state",
        "lsu tigers",
    },
    "miami oh": {
        "miami oh",
        "miami ohio",
        "miami redhawks",
    },
    "fiu": {
        "fiu",
        "florida international",
        "fiu panthers",
    },
    "usf": {
        "usf",
        "south florida",
        "south florida bulls",
    },
    "texas am": {
        "texas am",
        "texas a m",
        "texas a and m",
        "texas a&m",
        "texas a m aggies",
    },
}

SPECIAL_MARKERS = (
    "1q",
    "1st q",
    "first quarter",
    "1h",
    "1st h",
    "first half",
    "team total",
    " tt ",
    " tt",
    "1st half tt",
    "first half tt",
)


def norm(value):
    text = str(value or "").lower()

    text = (
        text.replace("&", " and ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("'", "")
    )

    text = re.sub(
        r"[^a-z0-9 ]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def alias_group(value):
    normalized = norm(value)

    if not normalized:
        return set()

    result = {normalized}

    for canonical, names in ALIASES.items():
        group = {
            norm(canonical),
            *[norm(x) for x in names],
        }

        if normalized in group:
            result |= group

    return result


def is_specialty(pick):
    bet_type = str(
        pick.get("bet_type") or ""
    ).upper()

    if bet_type not in {
        "SPREAD",
        "TOTAL",
        "MONEYLINE",
    }:
        return True

    text = (
        " "
        + norm(
            pick.get("selection")
        )
        + " "
    )

    return any(
        marker in text
        for marker in SPECIAL_MARKERS
    )


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

    return response.json().get(
        "events",
        [],
    )


def competitors(event):
    competitions = event.get(
        "competitions",
        [],
    )

    if not competitions:
        return []

    return competitions[0].get(
        "competitors",
        [],
    )


def completed(event):
    return bool(
        event.get("status", {})
        .get("type", {})
        .get("completed")
    )


def espn_names(comp):
    team = comp.get(
        "team",
        {},
    )

    names = set()

    for value in (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
        team.get("location"),
    ):
        names |= alias_group(value)

    return names


def matches_name(hint, comp):
    return bool(
        alias_group(hint)
        & espn_names(comp)
    )


def split_matchup(value):
    if not value:
        return []

    return [
        part.strip()
        for part in re.split(
            r"\s+(?:vs\.?|v\.?|at|@)\s+|/",
            str(value),
            flags=re.I,
        )
        if part.strip()
    ]


def clean_team(selection):
    text = str(
        selection or ""
    )

    text = re.sub(
        r"\b(over|under|moneyline|ml)\b",
        " ",
        text,
        flags=re.I,
    )

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

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip(" /-")


def primary_hints(pick):
    hints = []

    for field in (
        "team",
        "side",
    ):
        value = pick.get(field)

        if (
            value
            and norm(value)
            not in {
                "over",
                "under",
            }
        ):
            hints.append(
                str(value)
            )

    bet_type = str(
        pick.get("bet_type") or ""
    ).upper()

    if bet_type in {
        "SPREAD",
        "MONEYLINE",
    }:
        cleaned = clean_team(
            pick.get("selection")
        )

        if cleaned:
            hints.append(cleaned)

    output = []

    for hint in hints:
        normalized = norm(hint)

        if (
            normalized
            and normalized
            not in output
        ):
            output.append(
                normalized
            )

    return output


def opponent_hints(pick):
    hints = []

    if pick.get("opponent"):
        hints.append(
            norm(
                pick["opponent"]
            )
        )

    primary = primary_hints(
        pick
    )

    for part in split_matchup(
        pick.get("matchup")
    ):
        if not any(
            alias_group(part)
            & alias_group(primary_hint)
            for primary_hint in primary
        ):
            hints.append(
                norm(part)
            )

    output = []

    for hint in hints:
        if (
            hint
            and hint
            not in output
        ):
            output.append(hint)

    return output


def selected_comp(
    pick,
    comps,
):
    matches = []

    for comp in comps:
        if any(
            matches_name(
                hint,
                comp,
            )
            for hint in primary_hints(
                pick
            )
        ):
            matches.append(comp)

    if len(matches) == 1:
        return matches[0]

    return None


def event_matches(
    pick,
    event,
):
    comps = competitors(event)

    if len(comps) != 2:
        return False

    selected = selected_comp(
        pick,
        comps,
    )

    if selected is None:
        return False

    opponents = opponent_hints(
        pick
    )

    if opponents:
        other = next(
            c
            for c in comps
            if c["id"]
            != selected["id"]
        )

        if not any(
            matches_name(
                hint,
                other,
            )
            for hint in opponents
        ):
            return False

    return True


def parse_datetime(value):
    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except Exception:
        return None


def find_event(
    pick,
    events,
):
    bet_type = str(
        pick.get("bet_type") or ""
    ).upper()

    # Game totals MUST have a resolvable
    # two-team matchup.
    if bet_type == "TOTAL":
        parts = split_matchup(
            pick.get("matchup")
        )

        if len(parts) < 2:
            return None

        valid = []

        for event in events:
            comps = competitors(
                event
            )

            if len(comps) != 2:
                continue

            first_found = any(
                matches_name(
                    parts[0],
                    c,
                )
                for c in comps
            )

            second_found = any(
                matches_name(
                    parts[1],
                    c,
                )
                for c in comps
            )

            if (
                first_found
                and second_found
            ):
                valid.append(
                    event
                )

    else:
        valid = [
            event
            for event in events
            if event_matches(
                pick,
                event,
            )
        ]

    posted = parse_datetime(
        pick.get("posted_at")
    )

    if posted:
        sensible = []

        for event in valid:
            event_time = parse_datetime(
                event.get("date")
            )

            if not event_time:
                continue

            day_diff = (
                event_time.date()
                - posted.date()
            ).days

            if -1 <= day_diff <= 9:
                sensible.append(
                    event
                )

        if sensible:
            valid = sensible

    finals = [
        event
        for event in valid
        if completed(event)
    ]

    if finals:
        valid = finals

    # Bulletproof behavior:
    # only grade when exactly ONE
    # event satisfies the rules.
    if len(valid) == 1:
        return valid[0]

    return None


def final_score_text(
    comps,
    scores,
):
    output = []

    for comp in comps:
        team = comp.get(
            "team",
            {},
        )

        name = (
            team.get(
                "abbreviation"
            )
            or team.get(
                "shortDisplayName"
            )
            or team.get(
                "displayName"
            )
            or "Team"
        )

        score = int(
            scores[
                comp["id"]
            ]
        )

        output.append(
            f"{name} {score}"
        )

    return " - ".join(
        output
    )


def grade_pick(
    pick,
    event,
):
    if not completed(event):
        return False

    comps = competitors(
        event
    )

    if len(comps) != 2:
        return False

    scores = {
        c["id"]: float(
            c.get("score") or 0
        )
        for c in comps
    }

    bet_type = str(
        pick.get("bet_type") or ""
    ).upper()

    line = pick.get("line")

    if bet_type == "TOTAL":
        if line is None:
            return False

        side = norm(
            pick.get("side")
            or pick.get(
                "selection"
            )
        )

        total = sum(
            scores.values()
        )

        if "over" in side:
            if total > float(line):
                result = "WIN"
            elif total < float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        elif "under" in side:
            if total < float(line):
                result = "WIN"
            elif total > float(line):
                result = "LOSS"
            else:
                result = "PUSH"

        else:
            return False

    elif bet_type in {
        "SPREAD",
        "MONEYLINE",
    }:
        selected = selected_comp(
            pick,
            comps,
        )

        if selected is None:
            return False

        other = next(
            c
            for c in comps
            if c["id"]
            != selected["id"]
        )

        margin = (
            scores[
                selected["id"]
            ]
            - scores[
                other["id"]
            ]
        )

        if (
            bet_type
            == "MONEYLINE"
        ):
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
                margin
                + float(line)
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
    pick["event_id"] = event.get(
        "id"
    )
    pick["graded_at"] = now_iso()

    pick["final_score"] = (
        final_score_text(
            comps,
            scores,
        )
    )

    pick["profit_units"] = (
        american_profit(
            pick.get("odds"),
            float(
                pick.get("units")
                or 1
            ),
            result,
        )
    )

    return True


def clear_grade(
    pick,
    status="OPEN",
):
    pick["result"] = None
    pick["status"] = status
    pick["event_id"] = None
    pick["graded_at"] = None
    pick["final_score"] = None
    pick["profit_units"] = 0


def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    events_by_id = {}

    now = datetime.now(
        timezone.utc
    )

    for i in range(
        LOOKBACK_DAYS
    ):
        date = (
            now
            - timedelta(days=i)
        ).strftime(
            "%Y%m%d"
        )

        try:
            for event in scoreboard(
                date
            ):
                event_id = event.get(
                    "id"
                )

                if event_id:
                    events_by_id[
                        event_id
                    ] = event

        except Exception as exc:
            print(
                "ESPN fetch failed:",
                date,
                exc,
            )

    events = list(
        events_by_id.values()
    )

    print(
        f"Loaded {len(events)} "
        f"unique ESPN events"
    )

    graded = 0
    unmatched = 0
    specialty = 0

    for pick in picks:
        if pick.get("sport") not in {
            "CFB",
            "NCAAF",
        }:
            continue

        bet_type = str(
            pick.get("bet_type") or ""
        ).upper()

        # Anything specialty is explicitly
        # removed from full-game grading.
        if is_specialty(pick):
            clear_grade(
                pick,
                "REVIEW",
            )

            specialty += 1

            print(
                "QUARANTINED:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
            )

            continue

        if bet_type not in {
            "SPREAD",
            "TOTAL",
            "MONEYLINE",
        }:
            continue

        # Recheck every supported pick,
        # including previously FINAL picks.
        # This repairs results from older
        # unsafe grader versions.
        clear_grade(pick)

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
                pick.get(
                    "selection"
                ),
            )

            continue

        if not completed(event):
            print(
                "NOT FINAL:",
                pick.get("picker"),
                "|",
                pick.get(
                    "selection"
                ),
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
                pick.get(
                    "selection"
                ),
                "|",
                pick.get(
                    "result"
                ),
                "|",
                pick.get(
                    "final_score"
                ),
            )

    save_json(
        PICKS_FILE,
        picks,
    )

    print(
        f"Safely graded: "
        f"{graded}"
    )

    print(
        f"Unmatched supported picks: "
        f"{unmatched}"
    )

    print(
        f"Specialty picks quarantined: "
        f"{specialty}"
    )


if __name__ == "__main__":
    grade_open()
